import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
from internal_api import app
from internal_api.mapping_config import load_location_config
from internal_api.mapping_service import MappingService


class NoWasteCaptureRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapper = MappingService(load_location_config(
            location_id="ICT-001",
            config_dir=ROOT / "internal_api" / "config" / "locations",
        ))

    def test_prompt_separates_absent_object_from_visible_unknown(self):
        self.assertIn("visible_object_detected to false only for a genuinely absent-object frame", app.PROMPT)
        self.assertIn('classification_reason "visible_object_unclassified"', app.PROMPT)

    def test_face_only_fixture_is_invalid_and_has_no_physical_bin(self):
        fixture = json.loads((ROOT / "internal_api/fixtures/face_only_no_waste.json").read_text())
        raw_model_response = json.dumps(fixture["model_response"], ensure_ascii=False)

        self.assertTrue(app._is_no_waste_prediction(raw_model_response))
        with patch.object(app, "_safe_inference_record"):
            result = app._no_waste_response(
                request_id=fixture["request_id"], stored_image=None,
                timestamp="2026-11-04T00:00:00Z", request_started_at=1.0,
                inference_started_at=1.1, image_save_status="saved",
                storage_error_type=None,
            )

        self.assertEqual(result["status"], "no_waste_detected")
        self.assertEqual(result["classification_status"], "invalid_capture")
        self.assertFalse(result["valid_prediction"])
        self.assertEqual(result["retry_message"], "ไม่พบวัตถุสำหรับคัดแยกในภาพ")
        self.assertEqual(result["classification_reason"], "no_object_visible")
        self.assertFalse(result["visible_object_detected"])
        self.assertIsNone(result["bin_id"])
        self.assertIsNone(result["bin_number"])
        self.assertIsNone(result["bin_name"])
        self.assertIsNone(result["mappedBin"])
        self.assertTrue(all(candidate["waste_category"] == "unknown" for candidate in result["top_candidates"]))

    def test_legacy_no_waste_marker_does_not_override_visible_object(self):
        raw = json.dumps({
            "object_name_th": "เคสสีแดงรูปมะเขือเทศ",
            "waste_category": "unknown",
            "material_subtype": "unknown",
            "visible_object_detected": True,
            "no_waste_detected": True,
            "valid_capture": False,
            "visible_condition": "unknown",
            "confidence": 0.3,
            "top_candidates": [
                {"object_name_th": "เคสสีแดงรูปมะเขือเทศ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.3},
                {"object_name_th": "ภาชนะ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.2},
                {"object_name_th": "วัตถุ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.1},
            ],
        }, ensure_ascii=False)
        self.assertFalse(app._is_no_waste_prediction(raw))
        parsed = app._parse_model_response(raw)
        self.assertEqual(parsed[0], "unknown")
        self.assertEqual(parsed[1], "เคสสีแดงรูปมะเขือเทศ")
        self.assertEqual(app._classification_reason(parsed[0]), "visible_object_unclassified")
        decision = self.mapper.resolve(
            location_id="ICT-001", waste_category=parsed[0], confidence=parsed[7],
            object_name_th=parsed[1], material_subtype=parsed[2],
            visible_condition=parsed[4],
        )
        self.assertEqual(decision.bin_number, 1)
        self.assertTrue(decision.mapping_fallback)

    def test_legacy_sentinel_without_visibility_flag_is_not_invalid_capture(self):
        raw = json.dumps({
            "object_name_th": "ไม่พบขยะในภาพ",
            "waste_category": "unknown",
            "material_subtype": "unknown",
            "no_waste_detected": True,
            "valid_capture": False,
            "top_candidates": [
                {"object_name_th": "ไม่พบขยะในภาพ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": score}
                for score in (0.1, 0.05, 0.0)
            ],
        }, ensure_ascii=False)
        self.assertFalse(app._is_no_waste_prediction(raw))
        parsed = app._parse_model_response(raw)
        self.assertEqual(parsed[0], "unknown")
        self.assertEqual(parsed[1], app.VISIBLE_UNCLASSIFIED_OBJECT_NAME_TH)
        self.assertEqual(app._classification_reason(parsed[0]), "visible_object_unclassified")

    def test_hand_only_frame_is_absent_object(self):
        raw = json.dumps({
            "object_name_th": "ไม่พบวัตถุสำหรับคัดแยกในภาพ",
            "waste_category": "unknown",
            "material_subtype": "unknown",
            "visible_object_detected": False,
            "classification_reason": "no_object_visible",
            "top_candidates": [
                {"object_name_th": "ไม่พบวัตถุสำหรับคัดแยกในภาพ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": score}
                for score in (0.1, 0.05, 0.0)
            ],
        }, ensure_ascii=False)
        self.assertTrue(app._is_no_waste_prediction(raw))

    def test_ambiguous_container_is_visible_unknown_not_invalid_capture(self):
        raw = json.dumps({
            "object_name_th": "ภาชนะทรงขวดไม่ทราบวัสดุ",
            "waste_category": "unknown",
            "material_subtype": "unknown",
            "visible_object_detected": True,
            "classification_reason": "visible_object_unclassified",
            "visible_condition": "unknown",
            "confidence": 0.25,
            "top_candidates": [
                {"object_name_th": "ภาชนะทรงขวดไม่ทราบวัสดุ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.25},
                {"object_name_th": "ขวดพลาสติก", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.2},
                {"object_name_th": "ขวดแก้ว", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.1},
            ],
        }, ensure_ascii=False)
        self.assertFalse(app._is_no_waste_prediction(raw))
        parsed = app._parse_model_response(raw)
        self.assertEqual(parsed[0], "unknown")
        self.assertEqual(app._classification_reason(parsed[0]), "visible_object_unclassified")

    def test_clear_supported_waste_remains_a_normal_mapped_classification(self):
        raw = json.dumps({
            "object_name_th": "ขวดน้ำดื่มพลาสติก",
            "waste_category": "pet_bottle",
            "material_subtype": "pet_bottle",
            "visible_object_detected": True,
            "visible_condition": "clean_dry",
            "confidence": 0.95,
            "top_candidates": [
                {"object_name_th": "ขวดน้ำดื่มพลาสติก", "waste_category": "pet_bottle", "material_subtype": "pet_bottle", "confidence": 0.95},
                {"object_name_th": "ขวดพลาสติก", "waste_category": "plastic", "material_subtype": "rigid_plastic", "confidence": 0.3},
                {"object_name_th": "ภาชนะ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.1},
            ],
        }, ensure_ascii=False)
        self.assertFalse(app._is_no_waste_prediction(raw))
        parsed = app._parse_model_response(raw)
        decision = self.mapper.resolve(
            location_id="ICT-001", waste_category=parsed[0], confidence=parsed[7],
            object_name_th=parsed[1], material_subtype=parsed[2],
            visible_condition=parsed[4],
        )
        self.assertEqual(decision.bin_number, 2)


if __name__ == "__main__":
    unittest.main()
