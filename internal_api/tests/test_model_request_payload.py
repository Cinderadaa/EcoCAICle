import base64
import unittest

from internal_api.model_request import CLASSIFICATION_JSON_SCHEMA, build_model_request_payload


class ModelRequestPayloadTest(unittest.TestCase):
    def test_litellm_payload_keeps_existing_format_without_token_cap(self):
        payload = build_model_request_payload(
            model="qwen3-vl:4b",
            prompt="Return JSON only.",
            image_data_url="data:image/jpeg;base64,AA==",
            provider="litellm",
        )

        self.assertEqual(payload["model"], "qwen3-vl:4b")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["temperature"], 0)
        self.assertNotIn("max_tokens", payload)

    def test_native_ollama_payload_disables_thinking_and_has_bounded_options(self):
        payload = build_model_request_payload(
            model="qwen3-vl:4b",
            prompt="Return JSON only.",
            image_data_url="data:image/jpeg;base64,AA==",
            provider="ollama_native",
        )

        self.assertEqual(payload["model"], "qwen3-vl:4b")
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"], CLASSIFICATION_JSON_SCHEMA)
        self.assertEqual(payload["options"], {"num_predict": 300, "temperature": 0})
        self.assertEqual(payload["messages"][0]["images"], [base64.b64encode(b"\x00").decode()])
        self.assertIn("waste_category", payload["format"]["required"])


if __name__ == "__main__":
    unittest.main()
