from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from fastapi import HTTPException

from internal_api import app as backend
from internal_api.inference_storage import (
    ImageValidationError,
    InferenceStorage,
    StorageConfig,
    StorageFailure,
    validate_image_bytes,
)
from internal_api.mapping_config import validate_location_config
from internal_api.mapping_service import MappingService


VALID_JPEG = b"\xff\xd8\xff\xe0phase4-test-image\xff\xd9"


def qwen_result(category: str, *, confidence: float = 1.0) -> dict[str, object]:
    content = json.dumps(
        {
            "object_name_th": "ทดสอบวัตถุ",
            "waste_category": category,
            "has_food_scraps": False,
            "confidence": confidence,
        },
        ensure_ascii=False,
    )
    return {"choices": [{"message": {"content": content}}]}


async def inline_to_thread(function, *args, **kwargs):
    return function(*args, **kwargs)


class FakeUploadFile:
    content_type = "image/jpeg"
    filename = "client-supplied-name.jpg"

    def __init__(self, content: bytes = VALID_JPEG, content_type: str = "image/jpeg"):
        self.content = content
        self.content_type = content_type

    async def read(self, size: int = -1) -> bytes:
        return self.content if size < 0 else self.content[:size]


class Phase4StorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        asyncio.run(backend.load_mapping_foundation())
        cls.ict_config = backend.MAPPING_CONFIG
        cls.ict_service = backend.MAPPING_SERVICE

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="ecoaicle-phase4-")
        self.storage = InferenceStorage(
            StorageConfig(root=Path(self.directory.name), max_image_bytes=1024)
        )
        self.storage.initialize()
        backend.MAPPING_LOCATION_ID = "ICT-001"
        backend.MAPPING_CONFIG = self.ict_config
        backend.MAPPING_SERVICE = self.ict_service
        backend.PHASE4_STORAGE = self.storage

    def tearDown(self) -> None:
        self.directory.cleanup()

    def predict_with(self, qwen_payload: object, upload: Optional[FakeUploadFile] = None):
        with patch.object(backend, "_post_json", return_value=qwen_payload), patch.object(
            backend.asyncio,
            "to_thread",
            new=inline_to_thread,
        ):
            return asyncio.run(backend.predict(request=None, file=upload or FakeUploadFile()))

    def read_jsonl(self, path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_success_image_log_and_ground_truth_rows_are_joinable(self) -> None:
        response = self.predict_with(qwen_result("glass"))

        self.assertEqual(response["status"], "success")
        records = self.read_jsonl(self.storage.inference_log_path)
        reviews = self.read_jsonl(self.storage.ground_truth_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(reviews), 1)
        record = records[0]
        review = reviews[0]
        self.assertEqual(record["request_id"], response["request_id"])
        self.assertEqual(record["image_save_status"], "saved")
        self.assertEqual(record["image_id"], record["image_path"])
        image_path = self.storage.config.root / record["image_path"]
        self.assertTrue(image_path.is_file())
        self.assertEqual(image_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.storage.inference_log_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(review["request_id"], record["request_id"])
        self.assertEqual(review["image_id"], record["image_id"])
        self.assertEqual(review["review_status"], "unreviewed")
        self.assertIsNone(review["ground_truth_waste_category"])
        self.assertEqual(record["waste_category"], "glass")
        self.assertEqual(record["bin_number"], 5)
        self.assertIsNotNone(record["inference_latency_ms"])
        self.assertIsNotNone(record["total_latency_ms"])

    def test_unknown_and_low_confidence_are_logged_with_bin_one_fallback(self) -> None:
        unknown = self.predict_with(qwen_result("unknown", confidence=0.1))
        low = self.predict_with(qwen_result("glass", confidence=0.599999))

        records = self.read_jsonl(self.storage.inference_log_path)
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(low["status"], "unknown")
        self.assertEqual(records[0]["classification_status"], "unknown")
        self.assertEqual(records[0]["bin_number"], 1)
        self.assertTrue(records[0]["mapping_fallback"])
        self.assertEqual(records[1]["classification_status"], "low_confidence")
        self.assertEqual(records[1]["bin_number"], 1)
        self.assertTrue(records[1]["mapping_fallback"])

    def test_missing_mapping_logs_the_explicit_bin_one_fallback(self) -> None:
        raw_config = json.loads(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "location_id": "TEST-MISSING",
                    "location_name": self.ict_config.location_name,
                    "mapping_version": "TEST-MISSING-v1",
                    "enabled": True,
                    "classification_policy": {
                        "confidence_threshold": self.ict_config.confidence_threshold,
                        "unknown_fallback": {
                            "enabled": True,
                            "applies_to": ["unknown", "low_confidence"],
                            "bin_id": "ICT-001-B01",
                            "reason": "unknown-classification",
                        },
                    },
                    "bins": [
                        {
                            "bin_id": item.bin_id,
                            "bin_number": item.bin_number,
                            "bin_name": item.bin_name,
                            "icon": item.icon,
                            "enabled": item.enabled,
                        }
                        for item in self.ict_config.bins.values()
                    ],
                    "category_mapping": dict(self.ict_config.category_mapping),
                }
            )
        )
        del raw_config["category_mapping"]["ewaste"]
        missing_config = validate_location_config(raw_config, expected_location_id="TEST-MISSING")
        backend.MAPPING_LOCATION_ID = "TEST-MISSING"
        backend.MAPPING_CONFIG = missing_config
        backend.MAPPING_SERVICE = MappingService(missing_config)

        response = self.predict_with(qwen_result("ewaste"))
        record = self.read_jsonl(self.storage.inference_log_path)[0]
        self.assertEqual(response["status"], "missing_mapping")
        self.assertEqual(record["status"], "missing_mapping")
        self.assertEqual(record["classification_status"], "unavailable")
        self.assertEqual(record["bin_id"], "ICT-001-B01")
        self.assertEqual(record["bin_number"], 1)
        self.assertTrue(record["mapping_fallback"])

    def test_qwen_api_error_still_saves_image_and_is_not_unknown(self) -> None:
        with patch.object(backend, "_post_json", side_effect=urllib.error.URLError("timeout")), patch.object(
            backend.asyncio,
            "to_thread",
            new=inline_to_thread,
        ):
            response = asyncio.run(backend.predict(request=None, file=FakeUploadFile()))

        body = json.loads(response.body)
        record = self.read_jsonl(self.storage.inference_log_path)[0]
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["status"], "api_error")
        self.assertEqual(record["status"], "api_error")
        self.assertEqual(record["error_type"], "URLError")
        self.assertEqual(record["image_save_status"], "saved")
        self.assertIsNone(record["waste_category"])
        self.assertTrue((self.storage.config.root / record["image_path"]).is_file())

    def test_image_write_failure_does_not_change_classification(self) -> None:
        with patch.object(self.storage, "save_image", side_effect=StorageFailure("disk full")):
            response = self.predict_with(qwen_result("paper"))

        record = self.read_jsonl(self.storage.inference_log_path)[0]
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["bin_number"], 4)
        self.assertEqual(record["image_save_status"], "failed")
        self.assertEqual(record["storage_error_type"], "StorageFailure")
        self.assertIsNone(record["image_id"])

    def test_log_write_failure_does_not_change_classification(self) -> None:
        with patch.object(self.storage, "append_inference_record", side_effect=StorageFailure("read-only")):
            with self.assertLogs("internal_api.app", level="WARNING") as diagnostics:
                response = self.predict_with(qwen_result("metal"))

        self.assertEqual(response["status"], "success")
        self.assertTrue(any("inference_log" in message for message in diagnostics.output))
        self.assertTrue(self.read_jsonl(self.storage.ground_truth_path))

    def test_concurrent_storage_writes_are_complete_and_joinable(self) -> None:
        def write_one(index: int) -> str:
            request_id = f"concurrent-{index}"
            metadata = validate_image_bytes(
                VALID_JPEG,
                declared_content_type="image/jpeg",
                filename="ignored.jpg",
                max_image_bytes=1024,
            )
            stored = self.storage.save_image(request_id, VALID_JPEG, metadata)
            self.storage.append_inference_record(
                {"request_id": request_id, "image_id": stored.image_id, "status": "success"}
            )
            return request_id

        with ThreadPoolExecutor(max_workers=8) as executor:
            request_ids = list(executor.map(write_one, range(24)))

        records = self.read_jsonl(self.storage.inference_log_path)
        self.assertEqual({record["request_id"] for record in records}, set(request_ids))
        self.assertEqual(len(records), 24)
        self.assertEqual(
            len(list((self.storage.config.root / "images").rglob("*.jpg"))),
            24,
        )

    def test_invalid_and_oversized_uploads_are_rejected_before_inference(self) -> None:
        with self.assertRaises(ImageValidationError):
            validate_image_bytes(
                b"not-an-image",
                declared_content_type="image/jpeg",
                filename="bad.jpg",
                max_image_bytes=1024,
            )
        with self.assertRaises(ImageValidationError) as context:
            validate_image_bytes(
                VALID_JPEG,
                declared_content_type="image/jpeg",
                filename="large.jpg",
                max_image_bytes=4,
            )
        self.assertEqual(context.exception.status_code, 413)

        with patch.object(backend, "_post_json") as model_call:
            with self.assertRaises(HTTPException) as request_error:
                asyncio.run(backend.predict(request=None, file=FakeUploadFile(b"not-an-image")))
        self.assertEqual(request_error.exception.status_code, 400)
        model_call.assert_not_called()
        self.assertEqual(list((self.storage.config.root / "images").rglob("*")), [])

    def test_restart_style_reopen_preserves_images_and_jsonl(self) -> None:
        metadata = validate_image_bytes(
            VALID_JPEG,
            declared_content_type="image/jpeg",
            filename="restart.jpg",
            max_image_bytes=1024,
        )
        first = self.storage.save_image("restart-request", VALID_JPEG, metadata)
        self.storage.append_inference_record(
            {"request_id": "restart-request", "image_id": first.image_id, "status": "success"}
        )

        reopened = InferenceStorage(self.storage.config)
        reopened.initialize()
        self.assertTrue((reopened.config.root / first.image_path).is_file())
        records = self.read_jsonl(reopened.inference_log_path)
        self.assertEqual(records[0]["request_id"], "restart-request")

    def test_storage_configuration_rejects_repository_path_and_bad_limits(self) -> None:
        import os

        with patch.dict(
            os.environ,
            {
                "ECOAICLE_STORAGE_ROOT": str(Path(backend.__file__).resolve().parents[1]),
            },
            clear=False,
        ):
            with self.assertRaises(ValueError):
                StorageConfig.from_environment()

        with patch.dict(
            os.environ,
            {"ECOAICLE_STORAGE_ROOT": self.directory.name, "ECOAICLE_MAX_IMAGE_BYTES": "0"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                StorageConfig.from_environment()


if __name__ == "__main__":
    unittest.main()
