"""Retrospective POC subset built from historical scan records.

Everything here is read-only: it reads inference JSONL, reviewer annotation
JSONL and captured image files from the storage roots it is given, never writes
to them, never calls a model, and never rewrites mapping or model configuration.

Ground-truth rules
------------------
* ``human_verified``: the record has a final reviewer annotation (``confirm`` or
  ``correct``, latest revision wins, pilot-scoped rows ignored). The reviewer's
  category is the label. The expected bin is derived from that category through
  the current location mapping, using the same rule as ``review_service``
  (plastic has no authoritative expected bin, so bin accuracy is not evaluable).
* ``historical_prediction_only``: an eligible success record with no reviewer
  annotation at all. The historical prediction is shown for demonstration only
  and is never used as ground truth or in any metric.

Records that are unreviewable, skipped, or still unconfirmed are excluded from
the subset and counted under their exclusion reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from .inference_storage import REQUEST_ID_PATTERN
    from .mapping_config import SUPPORTED_WASTE_CATEGORIES, normalize_waste_category
except ImportError:  # pragma: no cover - direct script execution
    from inference_storage import REQUEST_ID_PATTERN
    from mapping_config import SUPPORTED_WASTE_CATEGORIES, normalize_waste_category


GT_HUMAN = "human_verified"
GT_HISTORICAL = "historical_prediction_only"
GROUND_TRUTH_SOURCES = frozenset({GT_HUMAN, GT_HISTORICAL})

# Same alias and verdict vocabulary as review_service.
REVIEW_CATEGORY_ALIASES = {"cardboard": "paper"}
LABEL_ACTIONS = frozenset({"confirm", "correct"})
UNREVIEWABLE_ACTION = "unreviewable"
FINAL_ACTIONS = LABEL_ACTIONS | {UNREVIEWABLE_ACTION}

OUTCOME_SUCCESS = "success"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_ERROR = "error"
OUTCOME_OTHER = "other"

EXCLUSION_REASONS = (
    "not_success",
    "timeout_or_api_error",
    "image_missing",
    "prediction_incomplete",
    "duplicate_request_id",
    "reviewer_unreviewable",
    "annotation_not_confirmed",
)

DISCLAIMER_TH = "ใช้สาธิตเท่านั้น ไม่ใช้คำนวณ accuracy"


def _read_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Read JSON objects line by line. Returns (records, malformed_line_count)."""

    if not path.is_file():
        return [], 0
    records: List[Dict[str, Any]] = []
    malformed = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(candidate, dict):
                records.append(candidate)
            else:
                malformed += 1
    return records, malformed


def _revision(record: Mapping[str, Any]) -> int:
    value = record.get("revision", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _final_annotation(rows: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """Latest final verdict for one request, mirroring review_service."""

    final: Optional[Mapping[str, Any]] = None
    for row in rows:
        if row.get("review_action") not in FINAL_ACTIONS:
            continue
        if final is None or _revision(row) >= _revision(final):
            final = row
    return final


def _normalize_label(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.strip().lower().split())
    cleaned = REVIEW_CATEGORY_ALIASES.get(cleaned, cleaned)
    if cleaned in SUPPORTED_WASTE_CATEGORIES:
        return cleaned
    return None


def _expected_bin(mapping_config: Any, category: Optional[str]) -> Optional[Dict[str, Any]]:
    """Expected bin for a reviewer category, using the same rule as review_service."""

    if category is None or category in ("unknown", "plastic"):
        return None
    bin_id = mapping_config.category_mapping.get(category)
    if not bin_id:
        return None
    definition = mapping_config.bins.get(bin_id)
    if definition is None:
        return None
    return {"bin_id": definition.bin_id, "bin_number": definition.bin_number, "bin_name": definition.bin_name}


def _as_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_image_path(root: Path, image_id: object) -> Optional[Path]:
    """Resolve an image ID that must live under ``<root>/images``."""

    if not isinstance(image_id, str) or not image_id.strip():
        return None
    relative = Path(image_id)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "images":
        return None
    resolved_root = root.resolve()
    image_root = (resolved_root / "images").resolve()
    target = (resolved_root / relative).resolve()
    try:
        target.relative_to(image_root)
    except ValueError:
        return None
    return target


def _outcome(record: Mapping[str, Any]) -> str:
    error_type = record.get("error_type")
    if record.get("status") == "success" and error_type is None:
        return OUTCOME_SUCCESS
    if error_type == "timeout":
        return OUTCOME_TIMEOUT
    if record.get("status") == "api_error" or error_type is not None:
        return OUTCOME_ERROR
    return OUTCOME_OTHER


def _sha256_of(path: Path) -> Optional[str]:
    import hashlib

    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_poc_dataset(
    sources: Sequence[Tuple[str, Path]],
    mapping_config: Any,
) -> Dict[str, Any]:
    """Build the derived POC rows and summary from one or more storage roots.

    ``sources`` is an ordered list of ``(source_label, storage_root)``. The first
    occurrence of a request_id wins; later duplicates are excluded and counted.
    The returned rows carry internal ``_image_path`` keys; strip them before
    publishing (see :func:`public_row`).
    """

    rows: List[Dict[str, Any]] = []
    seen_request_ids: set[str] = set()
    excluded: Dict[str, int] = {reason: 0 for reason in EXCLUSION_REASONS}
    source_summaries: List[Dict[str, Any]] = []
    fingerprints: Dict[str, Any] = {}

    for source_label, root in sources:
        root = Path(root)
        log_path = root / "logs" / "inference.jsonl"
        annotation_path = root / "review" / "ground_truth_annotations.jsonl"
        inference_records, malformed_inference = _read_jsonl(log_path)
        annotation_records, malformed_annotations = _read_jsonl(annotation_path)

        # Last record per request_id inside one root, matching scan_history.
        latest: Dict[str, Dict[str, Any]] = {}
        for record in inference_records:
            request_id = record.get("request_id")
            if isinstance(request_id, str) and REQUEST_ID_PATTERN.fullmatch(request_id):
                latest[request_id] = record

        annotations_by_request: Dict[str, List[Mapping[str, Any]]] = {}
        for annotation in annotation_records:
            # Pilot-scoped rows are ignored entirely, exactly as review_service does.
            if annotation.get("evaluation_scope") == "pilot":
                continue
            request_id = annotation.get("request_id")
            if isinstance(request_id, str):
                annotations_by_request.setdefault(request_id, []).append(annotation)

        outcomes = {OUTCOME_SUCCESS: 0, OUTCOME_TIMEOUT: 0, OUTCOME_ERROR: 0, OUTCOME_OTHER: 0}
        for record in latest.values():
            outcomes[_outcome(record)] += 1
        source_summaries.append({
            "source_label": source_label,
            "records": len(latest),
            "malformed_inference_lines": malformed_inference,
            "malformed_annotation_lines": malformed_annotations,
            "outcomes": outcomes,
        })
        fingerprints[source_label] = {
            "inference_log_sha256": _sha256_of(log_path),
            "annotations_sha256": _sha256_of(annotation_path),
        }

        for request_id, record in latest.items():
            if request_id in seen_request_ids:
                excluded["duplicate_request_id"] += 1
                continue
            seen_request_ids.add(request_id)

            if record.get("status") != "success":
                excluded["not_success"] += 1
                continue
            if record.get("error_type") is not None or record.get("classification_status") == "api_error":
                excluded["timeout_or_api_error"] += 1
                continue

            image_path = _safe_image_path(root, record.get("image_id") or record.get("image_path"))
            if image_path is None or not image_path.is_file():
                excluded["image_missing"] += 1
                continue

            prediction = _normalize_label(record.get("waste_category"))
            bin_number = _as_int(record.get("bin_number"))
            if prediction is None or bin_number is None:
                excluded["prediction_incomplete"] += 1
                continue

            annotations = annotations_by_request.get(request_id, [])
            final = _final_annotation(annotations)
            if final is not None and final.get("review_action") == UNREVIEWABLE_ACTION:
                excluded["reviewer_unreviewable"] += 1
                continue
            if annotations and (final is None or _normalize_label(final.get("ground_truth_waste_category")) is None):
                # Annotation rows exist but none is a confirmed category (for example skip or pending).
                excluded["annotation_not_confirmed"] += 1
                continue

            rows.append(_row(source_label, root, image_path, record, prediction, bin_number, final, mapping_config))

    eligible = len(rows)
    human = sum(1 for row in rows if row["ground_truth_source"] == GT_HUMAN)
    summary = {
        "sources": source_summaries,
        "outcome_totals": _sum_outcomes(source_summaries),
        "pool": {
            "eligible": eligible,
            GT_HUMAN: human,
            GT_HISTORICAL: eligible - human,
        },
        "excluded": excluded,
        "review_needed": eligible - human,
        "metrics": compute_metrics(rows),
        "source_fingerprints": fingerprints,
        "disclaimer": DISCLAIMER_TH,
    }
    return {"rows": rows, "summary": summary}


def _row(
    source_label: str,
    root: Path,
    image_path: Path,
    record: Mapping[str, Any],
    prediction: str,
    bin_number: int,
    final: Optional[Mapping[str, Any]],
    mapping_config: Any,
) -> Dict[str, Any]:
    if final is not None:
        label = _normalize_label(final.get("ground_truth_waste_category"))
        expected = _expected_bin(mapping_config, label)
        expected_bin_number = expected["bin_number"] if expected else None
        classification_correct: Optional[bool] = prediction == label
        if expected is None:
            bin_status = "not_evaluable_plastic" if label == "plastic" else "not_evaluable"
            bin_correct: Optional[bool] = None
        else:
            bin_status = "derived_from_category"
            bin_correct = bin_number == expected_bin_number
        review: Optional[Dict[str, Any]] = {
            "reviewer_id": final.get("reviewer_id"),
            "reviewed_at": final.get("reviewed_at"),
            "review_action": final.get("review_action"),
            "revision": _revision(final),
        }
        ground_truth_source = GT_HUMAN
    else:
        label = None
        expected_bin_number = None
        classification_correct = None
        bin_correct = None
        bin_status = None
        review = None
        ground_truth_source = GT_HISTORICAL

    return {
        "request_id": record["request_id"],
        "source_label": source_label,
        "image_id": record.get("image_id") or record.get("image_path"),
        "timestamp": record.get("timestamp"),
        "location_id": record.get("location_id"),
        "historical_prediction": {
            "object_name_th": record.get("object_name_th"),
            "waste_category": prediction,
            "material_subtype": record.get("material_subtype"),
            "confidence": record.get("confidence"),
            "top_candidates": record.get("top_candidates") or [],
            "model_identifier": record.get("model_identifier"),
            "prompt_version": record.get("prompt_version"),
        },
        "historical_mapping": {
            "mapping_version": record.get("mapping_version"),
            "bin_id": record.get("bin_id"),
            "bin_number": bin_number,
            "bin_name": record.get("bin_name"),
            "mapping_reason": record.get("mapping_reason"),
            "mapping_fallback": record.get("mapping_fallback"),
        },
        "ground_truth_source": ground_truth_source,
        "ground_truth_waste_category": label,
        "expected_bin_number": expected_bin_number,
        "bin_ground_truth_status": bin_status,
        "classification_correct": classification_correct,
        "bin_correct": bin_correct,
        "demo_only": ground_truth_source != GT_HUMAN,
        "review": review,
        "_image_path": image_path,
        "_source_root": str(root),
    }


def public_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop internal keys so no absolute filesystem path leaves the service."""

    return {key: value for key, value in row.items() if not key.startswith("_")}


def compute_metrics(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Metrics over ``human_verified`` rows only. ``None`` means not computable yet."""

    verified = [row for row in rows if row["ground_truth_source"] == GT_HUMAN]
    n = len(verified)
    correct = sum(1 for row in verified if row["classification_correct"])
    accuracy = round(correct / n, 4) if n else None

    labels = sorted({row["ground_truth_waste_category"] for row in verified} | {row["historical_prediction"]["waste_category"] for row in verified})
    f1_values: List[float] = []
    per_label: Dict[str, Dict[str, Any]] = {}
    for label in labels:
        gt = [row for row in verified if row["ground_truth_waste_category"] == label]
        predicted = [row for row in verified if row["historical_prediction"]["waste_category"] == label]
        tp = sum(1 for row in predicted if row["ground_truth_waste_category"] == label)
        fp = len(predicted) - tp
        fn = len(gt) - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_label[label] = {"support": len(gt), "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
    macro_f1 = round(sum(f1_values) / len(f1_values), 4) if f1_values else None

    bin_rows = [row for row in verified if row["expected_bin_number"] is not None]
    bin_correct = sum(1 for row in bin_rows if row["bin_correct"])
    bin_accuracy = round(bin_correct / len(bin_rows), 4) if bin_rows else None

    return {
        "classification_sample_size": n,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "per_label": per_label,
        "bin_sample_size": len(bin_rows),
        "bin_accuracy": bin_accuracy,
        "bin_not_evaluable": n - len(bin_rows),
    }


def _sum_outcomes(source_summaries: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    totals = {OUTCOME_SUCCESS: 0, OUTCOME_TIMEOUT: 0, OUTCOME_ERROR: 0, OUTCOME_OTHER: 0}
    for source in source_summaries:
        for key, value in source["outcomes"].items():
            totals[key] += value
    return totals
