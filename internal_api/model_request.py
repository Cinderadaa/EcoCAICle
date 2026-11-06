"""Model request construction for LiteLLM and native Ollama chat APIs."""

from __future__ import annotations

from typing import Any


CLASSIFICATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object_name_th": {"type": "string"},
        "waste_category": {"type": "string"},
        "material_subtype": {"type": "string"},
        "visible_object_detected": {"type": "boolean"},
        "classification_reason": {"type": "string"},
        "resin_identification_code": {"type": "string"},
        "visible_condition": {"type": "string"},
        "confidence": {"type": "number"},
        "top_candidates": {"type": "array"},
        "has_food_scraps": {"type": "boolean"},
        "has_liquid": {"type": "boolean"},
    },
    "required": [
        "object_name_th", "waste_category", "material_subtype",
        "visible_object_detected", "classification_reason",
        "resin_identification_code", "visible_condition", "confidence",
        "top_candidates", "has_food_scraps", "has_liquid",
    ],
    "additionalProperties": False,
}


def build_model_request_payload(
    *,
    model: str,
    prompt: str,
    image_data_url: str,
    provider: str = "litellm",
    completion_token_limit: object = None,
) -> dict[str, Any]:
    """Build the provider-specific payload without an oversized token cap."""
    if provider == "ollama_native":
        header, separator, encoded_image = image_data_url.partition(",")
        if not separator:
            raise ValueError("Expected a data URL for the image")
        return {
            "model": model,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [encoded_image],
            }],
            "think": False,
            "stream": False,
            "format": CLASSIFICATION_JSON_SCHEMA,
            "options": {"num_predict": 300, "temperature": 0},
        }

    return {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }],
        "stream": False,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
