#!/usr/bin/env python3
"""Write a derived, read-only retrospective POC subset from historical scans.

Reads inference logs, reviewer annotations and image files from the source
storage roots. It never writes to those roots, never calls a model, and never
touches mapping or model configuration. Output goes to a new directory under the
repository's ``derived/`` folder (or ``--out-dir``), which must sit outside every
source root.

Example (from the repository root)::

    python3 internal_api/scripts/build_poc_historical_subset.py

Outputs: manifest.jsonl, manifest.csv, summary.json, README.md
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from internal_api.historical_poc import build_poc_dataset, public_row  # noqa: E402
from internal_api.inference_storage import DEFAULT_STORAGE_ROOT  # noqa: E402
from internal_api.mapping_config import load_location_config  # noqa: E402

OUTPUT_FILES = ("manifest.jsonl", "manifest.csv", "summary.json", "README.md")
CSV_FIELDS = [
    "request_id", "source_label", "image_id", "timestamp",
    "predicted_category", "predicted_object_name_th", "predicted_confidence", "material_subtype",
    "historical_bin_number", "historical_bin_name", "mapping_version", "mapping_reason",
    "ground_truth_source", "ground_truth_waste_category", "expected_bin_number",
    "classification_correct", "bin_correct", "demo_only",
    "review_action", "reviewer_id", "reviewed_at",
]


def _configured_storage_root() -> str:
    """ECOAICLE_STORAGE_ROOT from the environment, else from internal_api/.env (as the app loads it)."""

    value = os.environ.get("ECOAICLE_STORAGE_ROOT", "").strip()
    env_file = REPO_ROOT / "internal_api" / ".env"
    if not value and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, raw = line.partition("=")
            if sep and key.strip() == "ECOAICLE_STORAGE_ROOT":
                value = raw.strip().strip('"').strip("'")
    return value


def _default_sources() -> List[Tuple[str, Path]]:
    configured = Path(_configured_storage_root() or DEFAULT_STORAGE_ROOT)
    sources = [("configured_storage", configured)]
    legacy = Path(DEFAULT_STORAGE_ROOT)
    if legacy.resolve() != configured.resolve():
        sources.append(("legacy_storage", legacy))
    return sources


def _parse_source(value: str) -> Tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--source must look like LABEL=/absolute/storage/root")
    label, raw_path = value.split("=", 1)
    label = label.strip()
    if not label or not raw_path.strip():
        raise argparse.ArgumentTypeError("--source needs both LABEL and PATH")
    return label, Path(raw_path.strip())


def _fingerprint(root: Path) -> dict:
    # Cheap change detector: size and mtime of the three source files read by the builder.
    result = {}
    for relative in ("logs/inference.jsonl", "review/ground_truth_annotations.jsonl"):
        path = root / relative
        if path.is_file():
            stat = path.stat()
            result[relative] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        else:
            result[relative] = None
    return result


def _csv_row(row: dict) -> dict:
    prediction = row["historical_prediction"]
    mapping = row["historical_mapping"]
    review = row.get("review") or {}
    return {
        "request_id": row["request_id"],
        "source_label": row["source_label"],
        "image_id": row["image_id"],
        "timestamp": row["timestamp"],
        "predicted_category": prediction["waste_category"],
        "predicted_object_name_th": prediction["object_name_th"],
        "predicted_confidence": prediction["confidence"],
        "material_subtype": prediction["material_subtype"],
        "historical_bin_number": mapping["bin_number"],
        "historical_bin_name": mapping["bin_name"],
        "mapping_version": mapping["mapping_version"],
        "mapping_reason": mapping["mapping_reason"],
        "ground_truth_source": row["ground_truth_source"],
        "ground_truth_waste_category": row["ground_truth_waste_category"],
        "expected_bin_number": row["expected_bin_number"],
        "classification_correct": row["classification_correct"],
        "bin_correct": row["bin_correct"],
        "demo_only": row["demo_only"],
        "review_action": review.get("review_action"),
        "reviewer_id": review.get("reviewer_id"),
        "reviewed_at": review.get("reviewed_at"),
    }


def _readme(summary: dict, generated_at: str) -> str:
    pool = summary["pool"]
    metrics = summary["metrics"]
    outcomes = summary["outcome_totals"]
    excluded = summary["excluded"]

    def shown(value):
        return "—" if value is None else str(value)

    lines = [
        "# Retrospective POC subset (derived, read-only)",
        "",
        f"Generated: {generated_at}. Source logs, images, mapping and model config were read only.",
        "",
        "## Counts",
        "",
        f"- Eligible scans: **{pool['eligible']}**",
        f"- human_verified: **{pool['human_verified']}**",
        f"- historical_prediction_only (demo only, not used for accuracy): **{pool['historical_prediction_only']}**",
        f"- Needs reviewer confirmation: **{summary['review_needed']}**",
        "",
        "## Source outcomes (all records, separate from accuracy)",
        "",
        f"- success: {outcomes['success']} · timeout: {outcomes['timeout']} · error: {outcomes['error']} · other: {outcomes['other']}",
        "",
        "## Metrics (human_verified only)",
        "",
        f"- classification n={metrics['classification_sample_size']}: accuracy {shown(metrics['accuracy'])}, macro F1 {shown(metrics['macro_f1'])}",
        f"- bin n={metrics['bin_sample_size']} (not evaluable: {metrics['bin_not_evaluable']}, plastic has no authoritative expected bin): bin accuracy {shown(metrics['bin_accuracy'])}",
        "",
        "## Exclusions",
        "",
    ]
    lines += [f"- {reason}: {count}" for reason, count in excluded.items()]
    lines += ["", f"> {summary['disclaimer']}", ""]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--source", type=_parse_source, action="append",
                        help="LABEL=/absolute/storage/root (repeatable). Defaults to the configured and legacy roots.")
    parser.add_argument("--location-id", default=os.environ.get("ECOCAICLE_LOCATION_ID", "ICT-001"))
    parser.add_argument("--config-dir", default=str(REPO_ROOT / "internal_api" / "config" / "locations"))
    parser.add_argument("--out-dir", default=None,
                        help="Output directory. Default: derived/poc_historical_<today>.")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace only the four generated output files in an existing --out-dir.")
    args = parser.parse_args(argv)

    sources = args.source or _default_sources()
    for label, root in sources:
        if not root.is_dir():
            parser.error(f"source root for {label} does not exist")

    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "derived" / f"poc_historical_{today}"
    out_dir = out_dir.resolve()
    for _, root in sources:
        resolved_root = root.resolve()
        if out_dir == resolved_root or resolved_root in out_dir.parents or out_dir in resolved_root.parents:
            parser.error("--out-dir must be outside every source storage root")

    if out_dir.exists() and any(out_dir.iterdir()):
        existing = {path.name for path in out_dir.iterdir()}
        if not args.overwrite:
            parser.error(f"{out_dir} already exists; pass --overwrite to replace the generated files")
        unexpected = existing - set(OUTPUT_FILES)
        if unexpected:
            parser.error(f"refusing to overwrite: unexpected files in {out_dir}: {sorted(unexpected)}")
    out_dir.mkdir(parents=True, exist_ok=True)

    before = {label: _fingerprint(root) for label, root in sources}
    config = load_location_config(location_id=args.location_id, config_dir=Path(args.config_dir))
    dataset = build_poc_dataset(sources, config)
    after = {label: _fingerprint(root) for label, root in sources}
    if before != after:
        parser.error("source files changed while the subset was being built; rerun the script")

    rows = [public_row(row) for row in dataset["rows"]]
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    summary = dict(dataset["summary"], generated_at=generated_at, location_id=args.location_id,
                   mapping_version=config.mapping_version)

    with (out_dir / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    with (out_dir / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_row(row))
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "README.md").write_text(_readme(summary, generated_at), encoding="utf-8")

    pool = summary["pool"]
    print(f"wrote {out_dir}")
    print(f"eligible={pool['eligible']} human_verified={pool['human_verified']} "
          f"historical_prediction_only={pool['historical_prediction_only']}")
    print(f"outcomes={summary['outcome_totals']}")
    print(f"excluded={summary['excluded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
