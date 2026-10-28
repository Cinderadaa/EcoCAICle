from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from internal_api.inference_storage import (
    InferenceStorage,
    StorageConfig,
    StorageFailure,
    validate_image_bytes,
)
from internal_api.mapping_config import load_location_config, validate_location_config
from internal_api.mapping_service import MappingService
from internal_api.review_service import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewService,
    ReviewUnavailableError,
    ReviewValidationError,
)


VALID_JPEG = b"\xff\xd8\xff\xe0phase5-review-image\xff\xd9"


class ReviewServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_location_config(
            location_id="ICT-001",
            config_dir=Path(__file__).parents[1] / "config" / "locations",
        )

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="ecocaicle-review-")
        self.storage = InferenceStorage(StorageConfig(root=Path(self.directory.name)))
        self.storage.initialize()
        self.service = ReviewService(self.storage, self.config)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def add_inference(
        self,
        request_id: str,
        category: str,
        *,
        bin_id: str | None = None,
        status: str = "success",
        confidence: float | None = 0.9,
        image: bool = True,
        latency: float = 100.0,
    ) -> None:
        stored = None
        if image:
            metadata = validate_image_bytes(
                VALID_JPEG,
                declared_content_type="image/jpeg",
                filename="ignored.jpg",
                max_image_bytes=1024,
            )
            stored = self.storage.save_image(request_id, VALID_JPEG, metadata)
        self.storage.append_inference_record(
            {
                "request_id": request_id,
                "timestamp": "2026-10-21T10:00:00Z",
                "location_id": "ICT-001",
                "image_id": stored.image_id if stored else None,
                "image_path": stored.image_path if stored else None,
                "image_save_status": "saved" if stored else "failed",
                "image_mime_type": "image/jpeg" if stored else None,
                "object_name_th": "วัตถุทดสอบ",
                "waste_category": category,
                "confidence": confidence,
                "has_food_scraps": False,
                "bin_id": bin_id if bin_id is not None else "ICT-001-B05",
                "bin_number": 5 if bin_id is not None else 5,
                "bin_name": "ขวดแก้ว",
                "mapping_version": "ICT-10-bin-v1",
                "classification_status": "confirmed" if status == "success" else status,
                "mapping_fallback": False,
                "status": status,
                "model_identifier": "test-model",
                "prompt_version": None,
                "inference_latency_ms": latency,
                "total_latency_ms": latency + 20,
                "error_type": "TimeoutError" if status == "api_error" else None,
                "storage_error_type": None,
            }
        )

    def label(self, request_id: str, category: str, *, revision: int = 0, note: str | None = None):
        return self.service.submit(
            request_id,
            {
                "reviewer_id": "qa.reviewer",
                "review_action": "label",
                "category": category,
                "note": note,
                "expected_revision": revision,
            },
        )

    def test_submission_is_separate_from_immutable_inference_record(self) -> None:
        self.add_inference("review-1", "glass", bin_id="ICT-001-B05")
        before = self.storage.inference_log_path.read_text(encoding="utf-8")

        result = self.label("review-1", "glass", note="Confirmed from image")

        self.assertEqual(result["annotation"]["review_action"], "confirm")
        self.assertEqual(result["review"]["status"], "reviewed")
        self.assertEqual(result["review"]["ground_truth_waste_category"], "glass")
        self.assertEqual(self.storage.inference_log_path.read_text(encoding="utf-8"), before)
        annotations = self.storage.ground_truth_annotations_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(annotations), 1)

    def test_revision_is_auditable_and_duplicate_final_label_is_rejected(self) -> None:
        self.add_inference("review-2", "glass", bin_id="ICT-001-B05")
        self.label("review-2", "glass")

        with self.assertRaises(ReviewConflictError) as duplicate:
            self.label("review-2", "glass", revision=1)
        self.assertEqual(duplicate.exception.extra["conflict"], "duplicate_final_review")

        revised = self.label("review-2", "paper", revision=1, note="Corrected after review")
        self.assertEqual(revised["annotation"]["revision"], 2)
        self.assertEqual(revised["annotation"]["review_action"], "correct")
        rows = [
            json.loads(line)
            for line in self.storage.ground_truth_annotations_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([row["revision"] for row in rows], [1, 2])
        self.assertEqual(self.service.queue(review_status="reviewed")["items"][0]["review"]["ground_truth_waste_category"], "paper")

    def test_concurrent_reviews_allow_one_revision_and_reject_stale_writers(self) -> None:
        self.add_inference("review-concurrent", "glass", bin_id="ICT-001-B05")

        def submit() -> str:
            try:
                self.label("review-concurrent", "glass")
                return "accepted"
            except ReviewConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: submit(), range(8)))
        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(results.count("conflict"), 7)

    def test_cardboard_normalizes_to_paper_and_uses_paper_destination(self) -> None:
        self.add_inference("review-cardboard", "cardboard", bin_id="ICT-001-B04")
        result = self.label("review-cardboard", "cardboard")
        self.assertEqual(result["annotation"]["ground_truth_waste_category"], "paper")
        item = self.service.queue(review_status="reviewed")["items"][0]
        self.assertEqual(item["expected_bin"], {
            "bin_id": "ICT-001-B04",
            "bin_number": 4,
            "bin_name": "กระดาษ",
        })

    def test_invalid_category_is_rejected(self) -> None:
        self.add_inference("review-invalid", "glass", bin_id="ICT-001-B05")
        with self.assertRaises(ReviewValidationError):
            self.label("review-invalid", "banana")

    def test_missing_image_is_gracefully_unreviewable_but_not_labeled(self) -> None:
        self.add_inference("review-missing-image", "glass", image=False)
        with self.assertRaises(ReviewConflictError) as missing:
            self.label("review-missing-image", "glass")
        self.assertEqual(missing.exception.extra["conflict"], "missing_image")

        result = self.service.submit(
            "review-missing-image",
            {"reviewer_id": "qa.reviewer", "review_action": "unreviewable"},
        )
        self.assertEqual(result["review"]["status"], "unreviewable")

    def test_empty_dataset_returns_explicit_empty_metrics(self) -> None:
        metrics = self.service.metrics()
        self.assertEqual(metrics["total_scans"], 0)
        self.assertIsNone(metrics["classification_accuracy"]["value"])
        self.assertIsNone(metrics["bin_recommendation_accuracy"]["value"])
        self.assertEqual(self.service.queue()["items"], [])
        self.assertEqual(self.service.errors()["items"], [])

    def test_incomplete_and_malformed_inference_rows_are_data_quality_warnings(self) -> None:
        self.storage.inference_log_path.write_text(
            "{not-json}\n" + json.dumps({"request_id": "incomplete", "status": "success"}) + "\n",
            encoding="utf-8",
        )
        queue = self.service.queue()
        self.assertEqual(queue["total"], 1)
        self.assertFalse(queue["items"][0]["image_available"])
        self.assertEqual(queue["data_quality"]["malformed_inference_records"], 1)

    def test_metrics_use_human_labels_and_mapping_config_not_model_bin(self) -> None:
        self.add_inference("metric-correct", "glass", bin_id="ICT-001-B05", latency=10)
        self.add_inference("metric-classification-error", "plastic", bin_id="ICT-001-B03", latency=20)
        self.add_inference("metric-mapping-error", "paper", bin_id="ICT-001-B05", latency=30)
        self.add_inference("metric-api-error", "unknown", status="api_error", latency=40)
        self.add_inference("metric-pending-unknown", "unknown", status="unknown", image=False, latency=50)
        self.label("metric-correct", "glass")
        self.label("metric-classification-error", "glass")
        self.label("metric-mapping-error", "paper")
        self.label("metric-api-error", "glass")

        metrics = self.service.metrics()
        self.assertEqual(metrics["total_scans"], 5)
        self.assertEqual(metrics["reviewed_samples"], 4)
        self.assertEqual(metrics["pending_reviews"], 1)
        self.assertEqual(metrics["classification_accuracy"]["eligible_samples"], 3)
        self.assertAlmostEqual(metrics["classification_accuracy"]["value"], 2 / 3, places=5)
        self.assertEqual(metrics["bin_recommendation_accuracy"]["eligible_samples"], 3)
        self.assertAlmostEqual(metrics["bin_recommendation_accuracy"]["value"], 1 / 3, places=5)
        self.assertEqual(metrics["error_counts"]["api_errors"], 1)
        self.assertEqual(metrics["error_counts"]["recommendation_errors"], 2)
        self.assertEqual(metrics["latency_ms"]["total"]["p50"], 50.0)
        self.assertEqual(metrics["confusion_matrix"]["matrix"]["glass"]["glass"], 1)
        self.assertEqual(metrics["per_category"]["glass"]["support"], 2)
        self.assertEqual(metrics["error_counts"]["mapping_errors"], 1)

    def test_error_explorer_distinguishes_classification_mapping_and_api_errors(self) -> None:
        self.add_inference("error-classification", "plastic", bin_id="ICT-001-B03")
        self.add_inference("error-mapping", "paper", bin_id="ICT-001-B05")
        self.add_inference("error-api", "unknown", status="api_error")
        self.label("error-classification", "glass")
        self.label("error-mapping", "paper")
        self.label("error-api", "glass")

        errors = self.service.errors()["items"]
        kinds = {item["request_id"]: item["error"]["error_type"] for item in errors}
        self.assertEqual(kinds["error-classification"], "classification_error")
        self.assertEqual(kinds["error-mapping"], "mapping_error")
        self.assertEqual(kinds["error-api"], "api_error")
        self.assertEqual(self.service.errors("api_error")["total"], 1)

    def test_many_to_one_mapping_is_supported_for_test_location(self) -> None:
        raw = {
            "schema_version": "1.0",
            "location_id": "TEST-MANY",
            "location_name": "Test Many To One",
            "mapping_version": "test-many-v1",
            "enabled": True,
            "classification_policy": {
                "confidence_threshold": 0.65,
                "unknown_fallback": {"enabled": True, "applies_to": ["unknown"], "bin_id": "TEST-MANY-B01"},
            },
            "bins": [
                {"bin_id": "TEST-MANY-B01", "bin_number": 1, "bin_name": "Combined", "enabled": True},
                {"bin_id": "TEST-MANY-B02", "bin_number": 2, "bin_name": "Other", "enabled": True},
            ],
            "category_mapping": {"plastic": "TEST-MANY-B01", "paper": "TEST-MANY-B01", "glass": "TEST-MANY-B02"},
        }
        config = validate_location_config(raw, expected_location_id="TEST-MANY")
        self.assertEqual(config.category_mapping["plastic"], config.category_mapping["paper"])
        self.add_inference("many-to-one", "plastic", bin_id="TEST-MANY-B01")
        # The helper writes ICT identifiers; replace the test service's immutable
        # config only for this assertion and append a location-specific record.
        self.service = ReviewService(self.storage, config)
        self.label("many-to-one", "paper")
        metrics = self.service.metrics()
        self.assertEqual(metrics["bin_recommendation_accuracy"]["value"], 1.0)

    def test_annotation_write_failure_is_a_safe_unavailable_error(self) -> None:
        self.add_inference("review-write-failure", "glass", bin_id="ICT-001-B05")
        with patch.object(
            self.storage,
            "append_ground_truth_annotation",
            side_effect=StorageFailure("read-only"),
        ):
            with self.assertRaises(ReviewUnavailableError):
                self.label("review-write-failure", "glass")

    def test_image_access_rejects_traversal_and_missing_files(self) -> None:
        with self.assertRaises(ReviewNotFoundError):
            self.service.image_path("../etc/passwd")
        with self.assertRaises(ReviewNotFoundError):
            self.service.image_path("images/does-not-exist.jpg")


if __name__ == "__main__":
    unittest.main()
