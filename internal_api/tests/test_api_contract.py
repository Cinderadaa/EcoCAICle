from __future__ import annotations

import asyncio
import json
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

from internal_api import app as backend
from internal_api.mapping_config import validate_location_config
from internal_api.mapping_service import MappingService
from internal_api.inference_storage import InferenceStorage, StorageConfig


VALID_JPEG = b"\xff\xd8\xff\xe0phase4-contract-test-image\xff\xd9"


class FakeUploadFile:
    content_type = "image/jpeg"
    filename = "contract-test.jpg"

    async def read(self, size: int = -1) -> bytes:
        return VALID_JPEG if size < 0 else VALID_JPEG[:size]


def qwen_result(
    category: str,
    *,
    confidence: float = 1.0,
    object_name: str = "ทดสอบวัตถุ",
    has_food_scraps: bool = False,
) -> dict[str, object]:
    content = json.dumps(
        {
            "object_name_th": object_name,
            "waste_category": category,
            "has_food_scraps": has_food_scraps,
            "confidence": confidence,
        },
        ensure_ascii=False,
    )
    return {"choices": [{"message": {"content": content}}]}


async def inline_to_thread(function, *args, **kwargs):
    """Avoid the host Python 3.9 executor hang while testing the route contract."""

    return function(*args, **kwargs)


class PredictContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        asyncio.run(backend.load_mapping_foundation())
        cls.ict_config = backend.MAPPING_CONFIG
        cls.ict_service = backend.MAPPING_SERVICE
        cls.storage_directory = tempfile.TemporaryDirectory(prefix="ecoaicle-contract-")
        cls.storage = InferenceStorage(
            StorageConfig(root=Path(cls.storage_directory.name))
        )
        cls.storage.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.storage_directory.cleanup()

    def setUp(self) -> None:
        backend.MAPPING_LOCATION_ID = "ICT-001"
        backend.MAPPING_CONFIG = self.ict_config
        backend.MAPPING_SERVICE = self.ict_service
        backend.PHASE4_STORAGE = self.storage

    def predict_with(self, qwen_payload: object):
        with patch.object(backend, "_post_json", return_value=qwen_payload), patch.object(
            backend.asyncio,
            "to_thread",
            new=inline_to_thread,
        ):
            return asyncio.run(backend.predict(FakeUploadFile()))

    def test_all_ten_bins_return_extended_fields_and_legacy_fields(self) -> None:
        expected = {
            "general": ("ICT-001-B01", 1),
            "pet_bottle": ("ICT-001-B02", 2),
            "plastic": ("ICT-001-B03", 3),
            "paper": ("ICT-001-B04", 4),
            "glass": ("ICT-001-B05", 5),
            "metal": ("ICT-001-B06", 6),
            "ewaste": ("ICT-001-B07", 7),
            "fuel": ("ICT-001-B08", 8),
            "hazardous": ("ICT-001-B09", 9),
            "infectious": ("ICT-001-B10", 10),
        }

        for category, (bin_id, bin_number) in expected.items():
            response = self.predict_with(qwen_result(category))
            self.assertIsInstance(response, dict)
            self.assertEqual(response["status"], "success")
            self.assertEqual(response["location_id"], "ICT-001")
            self.assertEqual(response["bin_id"], bin_id)
            self.assertEqual(response["bin_number"], bin_number)
            self.assertEqual(response["classification_status"], "confirmed")
            self.assertFalse(response["mapping_fallback"])
            self.assertTrue(response["request_id"])
            self.assertEqual(response["prediction"]["waste_category"], category)
            self.assertTrue(response["valid_prediction"])
            self.assertIn("disposal", response)
            self.assertIn("model", response)

    def test_qwen_request_format_and_prompt_are_unchanged(self) -> None:
        captured_payload = {}

        def capture_post(payload):
            captured_payload.update(payload)
            return qwen_result("paper")

        with patch.object(backend, "_post_json", new=capture_post), patch.object(
            backend.asyncio,
            "to_thread",
            new=inline_to_thread,
        ):
            response = asyncio.run(backend.predict(FakeUploadFile()))

        self.assertEqual(response["status"], "success")
        self.assertEqual(captured_payload["model"], backend.MODEL)
        self.assertEqual(captured_payload["stream"], False)
        self.assertEqual(captured_payload["temperature"], 0)
        content = captured_payload["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": backend.PROMPT})
        self.assertEqual(content[1]["type"], "image_url")

    def test_food_contamination_metadata_is_preserved_without_activation(self) -> None:
        response = self.predict_with(
            qwen_result("plastic", has_food_scraps=True)
        )
        self.assertEqual(response["status"], "success")
        self.assertTrue(response["prediction"]["has_food_scraps"])
        self.assertTrue(response["prediction"]["hasFoodScraps"])
        self.assertTrue(response["disposal"]["hasFoodScraps"])
        self.assertTrue(response["preparation"]["required"])
        self.assertEqual(
            response["preparation"]["shared_disposal_destination"]["type"],
            "designated_disposal_point",
        )
        self.assertIsNone(
            response["preparation"]["shared_disposal_destination"]["bin_id"]
        )
        self.assertIsNone(
            response["preparation"]["shared_disposal_destination"]["bin_number"]
        )
        self.assertEqual(
            response["preparation"]["container_disposal"]["bin_number"],
            3,
        )
        self.assertNotIn("food_waste_bin_id", response)
        self.assertNotIn("food_waste_bin_number", response)

    def test_remaining_liquid_signal_uses_the_same_destination_and_glass_bin(self) -> None:
        response = self.predict_with(
            qwen_result("glass", has_food_scraps=True)
        )
        self.assertEqual(response["bin_number"], 5)
        self.assertTrue(response["preparation"]["required"])
        self.assertEqual(
            response["preparation"]["steps"][0]["destination"]["label_th"],
            "จุดแยกเศษอาหาร/ของเหลว",
        )
        self.assertIsNone(
            response["preparation"]["steps"][0]["destination"]["bin_number"]
        )
        self.assertEqual(
            response["preparation"]["steps"][1]["destination"]["bin_number"],
            5,
        )

    def test_unknown_and_low_confidence_never_emit_preparation(self) -> None:
        unknown = self.predict_with(qwen_result("unknown", confidence=0.2, has_food_scraps=True))
        low_confidence = self.predict_with(qwen_result("glass", confidence=0.2, has_food_scraps=True))
        self.assertFalse(unknown["preparation"]["required"])
        self.assertFalse(low_confidence["preparation"]["required"])

    def test_missing_mapping_never_emits_preparation(self) -> None:
        base_config = json.loads(json.dumps({
            "schema_version": "1.0",
            "location_id": "TEST-MISSING-PREP",
            "location_name": self.ict_config.location_name,
            "mapping_version": "TEST-MISSING-PREP-v1",
            "enabled": True,
            "classification_policy": {
                "confidence_threshold": self.ict_config.confidence_threshold,
                "unknown_fallback": {
                    "enabled": True,
                    "applies_to": ["unknown", "low_confidence"],
                    "bin_id": "ICT-001-B01",
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
        }))
        del base_config["category_mapping"]["glass"]
        config = validate_location_config(base_config, expected_location_id="TEST-MISSING-PREP")
        backend.MAPPING_LOCATION_ID = "TEST-MISSING-PREP"
        backend.MAPPING_CONFIG = config
        backend.MAPPING_SERVICE = MappingService(config)
        response = self.predict_with(qwen_result("glass", has_food_scraps=True))
        self.assertEqual(response["status"], "missing_mapping")
        self.assertFalse(response["preparation"]["required"])

    def test_cardboard_remains_compatible_with_paper_mapping(self) -> None:
        response = self.predict_with(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "object_name_th": "กระดาษแข็ง",
                                    "waste_category": "cardboard",
                                    "has_food_scraps": False,
                                    "confidence": 1.0,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["prediction"]["waste_category"], "paper")
        self.assertEqual(response["bin_id"], "ICT-001-B04")

    def test_unknown_classification_uses_bin_one_fallback(self) -> None:
        response = self.predict_with(qwen_result("unknown", confidence=0.2))
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(response["classification_status"], "unknown")
        self.assertEqual(response["bin_id"], "ICT-001-B01")
        self.assertEqual(response["bin_number"], 1)
        self.assertTrue(response["mapping_fallback"])

    def test_low_confidence_uses_bin_one_fallback(self) -> None:
        response = self.predict_with(qwen_result("glass", confidence=0.599999))
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(response["classification_status"], "low_confidence")
        self.assertEqual(response["bin_number"], 1)
        self.assertTrue(response["mapping_fallback"])

    def test_glass_at_zero_point_six_uses_bin_five(self) -> None:
        response = self.predict_with(qwen_result("glass", confidence=0.60))
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["classification_status"], "confirmed")
        self.assertEqual(response["bin_id"], "ICT-001-B05")
        self.assertEqual(response["bin_number"], 5)
        self.assertFalse(response["mapping_fallback"])

    def test_missing_mapping_is_distinct_from_unknown(self) -> None:
        base_config = json.loads(
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
                            "bin_id": bin_definition.bin_id,
                            "bin_number": bin_definition.bin_number,
                            "bin_name": bin_definition.bin_name,
                            "icon": bin_definition.icon,
                            "enabled": bin_definition.enabled,
                        }
                        for bin_definition in self.ict_config.bins.values()
                    ],
                    "category_mapping": dict(self.ict_config.category_mapping),
                }
            )
        )
        del base_config["category_mapping"]["ewaste"]
        missing_config = validate_location_config(
            base_config,
            expected_location_id="TEST-MISSING",
        )
        backend.MAPPING_LOCATION_ID = "TEST-MISSING"
        backend.MAPPING_CONFIG = missing_config
        backend.MAPPING_SERVICE = MappingService(missing_config)

        response = self.predict_with(qwen_result("ewaste"))
        self.assertEqual(response["status"], "missing_mapping")
        self.assertEqual(response["classification_status"], "unavailable")
        self.assertIsNone(response["bin_id"])
        self.assertFalse(response["mapping_fallback"])

    def test_malformed_model_content_is_unknown_not_api_failure(self) -> None:
        response = self.predict_with(
            {"choices": [{"message": {"content": "not valid model JSON"}}]}
        )
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(response["classification_status"], "unknown")
        self.assertNotEqual(response["status"], "api_error")
        self.assertEqual(response["bin_number"], 1)

    def test_model_api_failure_is_not_unknown(self) -> None:
        with patch.object(
            backend,
            "_post_json",
            side_effect=urllib.error.URLError("model offline"),
        ), patch.object(backend.asyncio, "to_thread", new=inline_to_thread):
            response = asyncio.run(backend.predict(FakeUploadFile()))

        self.assertEqual(response.status_code, 502)
        body = json.loads(response.body)
        self.assertEqual(body["status"], "api_error")
        self.assertEqual(body["classification_status"], "api_error")
        self.assertIsNone(body["waste_category"])
        self.assertNotEqual(body["status"], "unknown")
        self.assertTrue(body["request_id"])
        self.assertFalse(body["preparation"]["required"])

    def test_client_cannot_select_another_location(self) -> None:
        response = self.predict_with(qwen_result("paper"))
        self.assertEqual(response["location_id"], "ICT-001")
