import json
import unittest
from unittest.mock import patch

from internal_api.app import (
    ModelResponseError,
    _extract_chat_content,
    _extract_complete_model_content,
    _model_from_environment,
    _parse_validated_model_response,
    _timeout_seconds_from_environment,
    _validate_model_content,
)


def valid_model_json():
    primary = {
        "object_name_th": "ขวดน้ำ",
        "waste_category": "pet_bottle",
        "material_subtype": "pet_bottle",
        "confidence": 0.9,
    }
    payload = {
        **primary,
        "visible_object_detected": True,
        "classification_reason": "visible_pet_bottle",
        "resin_identification_code": "unknown",
        "visible_condition": "clean_dry",
        "has_food_scraps": False,
        "has_liquid": False,
        "top_candidates": [
            primary,
            {"object_name_th": "ขวดแก้ว", "waste_category": "glass", "material_subtype": "glass", "confidence": 0.5},
            {"object_name_th": "ภาชนะ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.2},
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


class QwenPocResponseTest(unittest.TestCase):
    def test_model_and_timeout_are_read_from_environment(self):
        with patch.dict("os.environ", {"QWEN_MODEL": "qwen3-vl:4b", "QWEN_TIMEOUT_SECONDS": "60"}):
            self.assertEqual(_model_from_environment(), "qwen3-vl:4b")
            self.assertEqual(_timeout_seconds_from_environment(), 60)
        self.assertEqual(_timeout_seconds_from_environment("75"), 75)

    def test_prompt_requires_json_only_and_three_valid_candidates(self):
        from internal_api.app import PROMPT

        self.assertIn("no prose, markdown, explanations, analysis, or reasoning in content", PROMPT)
        self.assertIn("one complete JSON object on one line", PROMPT)
        self.assertIn("exactly 3 complete valid objects", PROMPT)

    def test_empty_4b_content_is_rejected_without_using_reasoning_content(self):
        result = {
            "model": "qwen3-vl:4b",
            "choices": [{"finish_reason": "stop", "message": {
                "content": "", "reasoning_content": valid_model_json(),
            }}],
        }
        with self.assertRaisesRegex(ModelResponseError, "Empty"):
            _extract_chat_content(result)

    def test_complete_json_with_exactly_three_candidates_is_accepted(self):
        raw = valid_model_json()
        parsed = _validate_model_content(raw)
        normalized = _parse_validated_model_response(raw)
        self.assertEqual(len(parsed["top_candidates"]), 3)
        self.assertEqual(len(normalized[-1]), 3)
        response = {"usage": {"completion_tokens": 240}, "choices": [{
            "finish_reason": "stop", "message": {"content": raw},
        }]}
        self.assertEqual(_extract_complete_model_content(response), raw)

    def test_finish_reason_length_is_rejected_even_if_content_is_valid_json(self):
        response = {"choices": [{"finish_reason": "length", "message": {"content": valid_model_json()}}]}
        with self.assertRaisesRegex(ModelResponseError, "finish_reason=length"):
            _extract_complete_model_content(response)

    def test_invalid_json_is_rejected(self):
        response = {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}
        with self.assertRaisesRegex(ModelResponseError, "not valid JSON"):
            _extract_complete_model_content(response)

    def test_truncated_json_is_rejected(self):
        response = {"choices": [{"finish_reason": "stop", "message": {"content": valid_model_json()[:-20]}}]}
        with self.assertRaisesRegex(ModelResponseError, "not valid JSON"):
            _extract_complete_model_content(response)

    def test_response_with_only_two_candidates_is_rejected(self):
        payload = json.loads(valid_model_json())
        payload["top_candidates"] = payload["top_candidates"][:2]
        response = {"choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(payload, ensure_ascii=False)},
        }]}
        with self.assertRaisesRegex(ModelResponseError, "exactly 3"):
            _extract_complete_model_content(response)

    def test_three_raw_candidates_with_only_two_valid_after_normalization_are_rejected(self):
        payload = json.loads(valid_model_json())
        payload["top_candidates"][2]["confidence"] = 0.95
        with self.assertLogs("internal_api.app", level="WARNING") as captured:
            with self.assertRaisesRegex(ModelResponseError, "got 2"):
                _parse_validated_model_response(json.dumps(payload, ensure_ascii=False))
        self.assertTrue(any(
            "candidate_index=3" in message and "field=confidence" in message
            for message in captured.output
        ))


if __name__ == "__main__":
    unittest.main()
