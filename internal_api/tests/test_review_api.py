from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from internal_api import app as backend
from internal_api.inference_storage import InferenceStorage, StorageConfig, validate_image_bytes
from internal_api.mapping_config import load_location_config
from internal_api.review_service import ReviewService


VALID_JPEG = b"\xff\xd8\xff\xe0review-api-image\xff\xd9"


class ReviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_location_config(
            location_id="ICT-001",
            config_dir=Path(__file__).parents[1] / "config" / "locations",
        )

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="ecocaicle-review-api-")
        self.storage = InferenceStorage(StorageConfig(root=Path(self.directory.name)))
        self.storage.initialize()
        metadata = validate_image_bytes(
            VALID_JPEG,
            declared_content_type="image/jpeg",
            filename="ignored.jpg",
            max_image_bytes=1024,
        )
        stored = self.storage.save_image("review-api-1", VALID_JPEG, metadata)
        self.image_id = stored.image_id
        self.storage.append_inference_record(
            {
                "request_id": "review-api-1",
                "timestamp": "2026-10-21T10:00:00Z",
                "location_id": "ICT-001",
                "image_id": stored.image_id,
                "image_path": stored.image_path,
                "image_save_status": "saved",
                "object_name_th": "วัตถุทดสอบ",
                "waste_category": "glass",
                "confidence": 0.99,
                "has_food_scraps": False,
                "bin_id": "ICT-001-B05",
                "bin_number": 5,
                "bin_name": "ขวดแก้ว",
                "mapping_version": "ICT-10-bin-v1",
                "classification_status": "confirmed",
                "mapping_fallback": False,
                "status": "success",
                "inference_latency_ms": 10,
                "total_latency_ms": 20,
            }
        )
        self.service = ReviewService(self.storage, self.config)
        self.old_token = backend.REVIEWER_TOKEN
        self.old_service = backend.REVIEW_SERVICE
        backend.REVIEWER_TOKEN = "phase5-test-token"
        backend.REVIEW_SERVICE = self.service

    def tearDown(self) -> None:
        backend.REVIEWER_TOKEN = self.old_token
        backend.REVIEW_SERVICE = self.old_service
        self.directory.cleanup()

    @staticmethod
    def request(host="127.0.0.1", token="phase5-test-token"):
        return SimpleNamespace(
            client=SimpleNamespace(host=host),
            headers={"X-Reviewer-Token": token} if token is not None else {},
        )

    def test_reviewer_routes_require_loopback_and_token(self) -> None:
        with self.assertRaises(HTTPException) as missing:
            backend._require_reviewer(self.request(token=None))
        self.assertEqual(missing.exception.status_code, 401)

        with self.assertRaises(HTTPException) as remote:
            backend._require_reviewer(self.request(host="10.0.0.5"))
        self.assertEqual(remote.exception.status_code, 403)

        with self.assertRaises(HTTPException) as wrong:
            backend._require_reviewer(self.request(token="wrong"))
        self.assertEqual(wrong.exception.status_code, 401)

    def test_queue_metrics_and_image_route_are_protected_and_local(self) -> None:
        queue = asyncio.run(backend.review_queue(self.request()))
        self.assertEqual(queue["total"], 1)
        metrics = asyncio.run(backend.review_metrics(self.request()))
        self.assertEqual(metrics["total_scans"], 1)

        image_response = asyncio.run(
            backend.review_image(self.request(), self.image_id)
        )
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.body, VALID_JPEG)
        self.assertEqual(image_response.headers["cache-control"], "no-store")

    def test_annotation_route_returns_revisioned_review(self) -> None:
        result = asyncio.run(
            backend.submit_review_annotation(
                self.request(),
                "review-api-1",
                {"reviewer_id": "qa.reviewer", "review_action": "label", "category": "glass"},
            )
        )
        self.assertEqual(result["annotation"]["revision"], 1)
        self.assertEqual(result["review"]["status"], "reviewed")


if __name__ == "__main__":
    unittest.main()
