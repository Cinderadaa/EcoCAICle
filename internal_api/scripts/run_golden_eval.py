"""Compare configured providers on human-reviewed golden scan images."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load_backend_environment() -> None:
    """Load only missing backend variables without printing credential values."""
    env_path = ROOT / "internal_api" / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


load_backend_environment()

from internal_api import app as classifier
from internal_api.mapping_config import load_location_config, normalize_material_subtype, normalize_waste_category
from internal_api.mapping_service import MappingService

# Keep provider comparisons on the requested 60-second per-request window,
# even when a developer's local backend .env has a longer POC timeout.
classifier.QWEN_REQUEST_TIMEOUT_SECONDS = 60

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden"
LABELS_PATH = GOLDEN_DIR / "labels.json"
RESULTS_PATH = GOLDEN_DIR / "results.json"


def macro_f1(expected: list[str], predicted: list[str | None]) -> float | None:
    labels = sorted(set(expected) | {value for value in predicted if value is not None})
    if not labels:
        return None
    scores = []
    for label in labels:
        tp = sum(y == label and p == label for y, p in zip(expected, predicted))
        fp = sum(y != label and p == label for y, p in zip(expected, predicted))
        fn = sum(y == label and p != label for y, p in zip(expected, predicted))
        denominator = 2 * tp + fp + fn
        scores.append((2 * tp / denominator) if denominator else 0.0)
    return round(sum(scores) / len(scores), 4)


def run_provider(provider: str, labels: dict, mapper: MappingService, location_id: str) -> dict:
    rows = []
    for filename, label in sorted(labels.items()):
        path = GOLDEN_DIR / filename
        started = time.perf_counter()
        try:
            result = classifier.classify_image_bytes(path.read_bytes(), "image/jpeg", provider=provider)
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            parsed = result["parsed_model_response"]
            if parsed is None:
                rows.append({
                    "image": filename, "http_success": True, "valid_json": False,
                    "empty_content": result["content_length"] == 0, "needs_review": True,
                    "latency_ms": latency_ms, "predicted_category": None,
                    "predicted_subtype": None, "predicted_bin": None,
                })
                continue

            category, object_name, subtype, _resin, condition, scraps, liquid, confidence, _raw, _candidates = parsed
            decision = mapper.resolve(
                location_id=location_id,
                waste_category=category,
                confidence=confidence,
                object_name_th=object_name,
                material_subtype=subtype,
                visible_condition=condition,
                has_food_scraps=scraps,
                has_liquid=liquid,
            )
            rows.append({
                "image": filename, "http_success": True, "valid_json": True,
                "empty_content": result["content_length"] == 0,
                "needs_review": decision.classification_status in {"unknown", "low_confidence", "unavailable"},
                "latency_ms": latency_ms,
                "predicted_category": normalize_waste_category(category),
                "predicted_subtype": normalize_material_subtype(subtype),
                "predicted_bin": decision.bin_number,
                "confidence": confidence,
            })
        except Exception as exc:  # Keep diagnostics content-free.
            rows.append({
                "image": filename, "http_success": False, "valid_json": False,
                "empty_content": False, "needs_review": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "predicted_category": None, "predicted_subtype": None,
                "predicted_bin": None, "error_type": type(exc).__name__,
            })
        latest = rows[-1]
        print(json.dumps({
            "provider": provider, "image": filename,
            "response_success": latest["http_success"],
            "valid_json": latest["valid_json"],
            "empty_content": latest["empty_content"],
            "needs_review": latest["needs_review"],
            "latency_ms": latest["latency_ms"],
            "error_type": latest.get("error_type"),
        }, ensure_ascii=False), flush=True)

    ordered_labels = [labels[filename] for filename in sorted(labels)]
    expected_categories = [label["expected_category"] for label in ordered_labels]
    predicted_categories = [row["predicted_category"] for row in rows]
    total = len(rows)
    summary = {
        "sample_count": total,
        "response_success_rate": round(sum(row["http_success"] for row in rows) / total, 4) if total else None,
        "valid_json_rate": round(sum(row["valid_json"] for row in rows) / total, 4) if total else None,
        "category_accuracy": round(sum(y == p for y, p in zip(expected_categories, predicted_categories)) / total, 4) if total else None,
        "subtype_accuracy": round(sum(label["expected_subtype"] == row["predicted_subtype"] for label, row in zip(ordered_labels, rows)) / total, 4) if total else None,
        "bin_accuracy": round(sum(label["expected_bin"] == row["predicted_bin"] for label, row in zip(ordered_labels, rows)) / total, 4) if total else None,
        "category_macro_f1": macro_f1(expected_categories, predicted_categories),
        "needs_review_count": sum(row["needs_review"] for row in rows),
        "empty_content_count": sum(row["empty_content"] for row in rows),
        "mean_latency_ms": round(statistics.mean(row["latency_ms"] for row in rows), 3) if rows else None,
        "median_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 3) if rows else None,
    }
    return {"summary": summary, "results": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers", nargs="+", choices=("litellm", "ollama_native"), default=["litellm", "ollama_native"])
    args = parser.parse_args()
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    location_id = "ICT-001"
    mapper = MappingService(load_location_config(location_id))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": classifier.MODEL,
        "golden_source": "internal_api/evaluations/qwen3_8_27b_manifest.json human_review_basis",
        "sample_count": len(labels),
        "providers": {},
    }
    for provider in args.providers:
        report["providers"][provider] = run_provider(provider, labels, mapper, location_id)
        print(json.dumps({"provider": provider, **report["providers"][provider]["summary"]}, ensure_ascii=False))
    RESULTS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"results_json": str(RESULTS_PATH), "providers": args.providers}, ensure_ascii=False))


if __name__ == "__main__":
    main()
