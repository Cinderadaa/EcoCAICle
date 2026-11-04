import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "internal_api"))
import app  # noqa: E402


class NoWasteCaptureRegressionTest(unittest.TestCase):
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
        self.assertEqual(result["retry_message"], "ไม่พบขยะในภาพ กรุณาวางขยะในกรอบแล้วสแกนใหม่")
        self.assertIsNone(result["bin_id"])
        self.assertIsNone(result["bin_number"])
        self.assertIsNone(result["bin_name"])
        self.assertIsNone(result["mappedBin"])
        self.assertTrue(all(candidate["waste_category"] == "unknown" for candidate in result["top_candidates"]))


if __name__ == "__main__":
    unittest.main()
