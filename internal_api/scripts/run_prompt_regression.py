#!/usr/bin/env python3
"""Run a small fixed prompt check on existing folder-labeled images.

This is a prompt regression aid, not an accuracy benchmark. It sends only the
listed existing images to the configured Qwen-compatible endpoint and writes
the raw model output alongside normalized predictions and mapping results.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from internal_api import app as api  # noqa: E402
from internal_api.mapping_config import load_location_config  # noqa: E402
from internal_api.mapping_service import MappingService  # noqa: E402


DATASET = ROOT / "dataset" / "Garbage classification"
CASES = (
    ("pet_bottle", "pet", "plastic1.jpg", "pet_bottle"),
    ("glass", "glass", "glass1.jpg", "glass"),
    ("paper", "paper", "paper1.jpg", "paper"),
    ("cardboard", "cardboard", "cardboard1.jpg", "paper"),
    ("metal", "metal", "metal1.jpg", "metal"),
    # Existing source folders explicitly denote mixed/unsupported materials.
    ("insufficient_other", "other", "plastic102.jpg", "unknown"),
    ("insufficient_trash", "trash", "trash1.jpg", "unknown"),
)


def _request(path: Path) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data_url = f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    result = api._post_json({
        "model": api.MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": api.PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "stream": False,
        "temperature": 0,
    })
    return {"raw": api._extract_chat_content(result)}


def run(output: Optional[Path] = None) -> dict[str, Any]:
    location = load_location_config(location_id=api.MAPPING_LOCATION_ID, config_dir=api.MAPPING_CONFIG_DIRECTORY)
    mapper = MappingService(location)
    rows: list[dict[str, Any]] = []
    for case_name, source_label, filename, expected in CASES:
        image = DATASET / source_label / filename
        if not image.is_file():
            raise FileNotFoundError(f"Regression image is missing: {image}")
        raw = _request(image)["raw"]
        category, name, subtype, resin, condition, scraps, liquid, confidence, raw_category, candidates = api._parse_model_response(raw)
        decision = mapper.resolve(
            location_id=location.location_id,
            waste_category=category,
            confidence=confidence,
            object_name_th=name,
            material_subtype=subtype,
            visible_condition=condition,
            has_food_scraps=scraps,
            has_liquid=liquid,
        )
        rows.append({
            "case": case_name,
            "image": image.relative_to(ROOT).as_posix(),
            "source_label": source_label,
            "expected_category": expected,
            "raw_model_output": raw,
            "normalized_category": category,
            "normalized_subtype": subtype,
            "resin_identification_code": resin,
            "confidence": confidence,
            "mapping_result": decision.to_dict(),
            "pass": category == expected,
        })
    report = {
        "suite": "prompt-regression",
        "prompt_version": api.PROMPT_VERSION,
        "model": api.MODEL,
        "mapping_version": location.mapping_version,
        "scope_note": "Small deterministic prompt regression set; not an accuracy estimate.",
        "cases": rows,
        "passed": sum(row["pass"] for row in rows),
        "failed": sum(not row["pass"] for row in rows),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Also save the JSON report to this path")
    args = parser.parse_args()
    report = run(args.output)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
