"""Local, file-backed ground-truth review and evaluation service.

The service reads immutable Phase 4 inference JSONL records and appends human
actions to a separate annotation JSONL file.  It deliberately has no database,
public static-file route, or mutation path for inference records.
"""

from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - retained for non-POSIX local tooling
    fcntl = None

try:  # Package import for local development and test discovery.
    from .inference_storage import InferenceStorage, StorageFailure
    from .mapping_config import (
        SUPPORTED_WASTE_CATEGORIES,
        LocationMappingConfig,
        normalize_material_subtype,
        normalize_waste_category,
    )
except ImportError:  # Docker's ``uvicorn app:app`` working-directory import.
    from inference_storage import InferenceStorage, StorageFailure
    from mapping_config import (
        SUPPORTED_WASTE_CATEGORIES,
        LocationMappingConfig,
        normalize_material_subtype,
        normalize_waste_category,
    )


REVIEW_CATEGORY_ALIASES = {"cardboard": "paper"}
REVIEW_ACTIONS = frozenset({"label", "unreviewable", "skip"})
FINAL_REVIEW_ACTIONS = frozenset({"confirm", "correct", "unreviewable"})
REVIEWER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ERROR_TYPES = frozenset(
    {
        "all",
        "classification_error",
        "mapping_error",
        "classification_and_mapping_error",
        "api_error",
        "pending",
        "unreviewable",
    }
)


class ReviewServiceError(Exception):
    status_code = 500
    code = "review_service_error"

    def __init__(self, message: str, **extra: Any):
        super().__init__(message)
        self.message = message
        self.extra = extra

    def to_detail(self) -> Dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


class ReviewValidationError(ReviewServiceError):
    status_code = 400
    code = "invalid_review_request"


class ReviewNotFoundError(ReviewServiceError):
    status_code = 404
    code = "review_item_not_found"


class ReviewConflictError(ReviewServiceError):
    status_code = 409
    code = "review_revision_conflict"


class ReviewUnavailableError(ReviewServiceError):
    status_code = 503
    code = "review_storage_unavailable"


@dataclass(frozen=True)
class ReviewState:
    latest_revision: int
    latest_action: Optional[str]
    final: Optional[Dict[str, Any]]


class ReviewService:
    """Review queue, append-only annotations, and mapping-aware evaluation."""

    _annotation_thread_lock = threading.Lock()

    def __init__(self, storage: InferenceStorage, mapping_config: LocationMappingConfig):
        self.storage = storage
        self.mapping_config = mapping_config
        self._annotation_lock_path = storage.config.root / "review" / "ground_truth_annotations.lock"

    @property
    def annotation_path(self) -> Path:
        return self.storage.ground_truth_annotations_path

    @property
    def categories(self) -> List[str]:
        return sorted(SUPPORTED_WASTE_CATEGORIES)

    def _read_jsonl(self, path: Path) -> Tuple[List[Dict[str, Any]], int]:
        if not path.exists():
            return [], 0
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ReviewUnavailableError(
                "Review data could not be read",
                error_type=type(exc).__name__,
            ) from exc

        records: List[Dict[str, Any]] = []
        malformed = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(candidate, dict):
                malformed += 1
                continue
            records.append(candidate)
        return records, malformed

    def _inference_records(self) -> Tuple[List[Dict[str, Any]], int]:
        records, malformed = self._read_jsonl(self.storage.inference_log_path)
        by_request_id: Dict[str, Dict[str, Any]] = {}
        for record in records:
            request_id = record.get("request_id")
            if not isinstance(request_id, str) or not request_id.strip():
                malformed += 1
                continue
            # A duplicate request_id is retained as the last observed record
            # for queue/evaluation purposes; the original JSONL remains intact.
            by_request_id[request_id] = record
        return list(by_request_id.values()), malformed

    def _annotation_records(self) -> Tuple[List[Dict[str, Any]], int]:
        return self._read_jsonl(self.annotation_path)

    @staticmethod
    def _review_states(records: Sequence[Mapping[str, Any]]) -> Dict[str, ReviewState]:
        states: Dict[str, ReviewState] = {}
        for record in records:
            if record.get("evaluation_scope") == "pilot":
                continue
            request_id = record.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                continue
            raw_revision = record.get("revision", 0)
            revision = raw_revision if isinstance(raw_revision, int) and not isinstance(raw_revision, bool) else 0
            current = states.get(request_id)
            latest_revision = max(revision, current.latest_revision if current else 0)
            latest_action = record.get("review_action") if isinstance(record.get("review_action"), str) else None
            final = current.final if current else None
            if latest_action in FINAL_REVIEW_ACTIONS:
                if final is None or revision >= int(final.get("revision", 0)):
                    final = dict(record)
            states[request_id] = ReviewState(
                latest_revision=latest_revision,
                latest_action=latest_action or (current.latest_action if current else None),
                final=final,
            )
        return states

    def _safe_image_path(self, image_id: object) -> Optional[Path]:
        if not isinstance(image_id, str) or not image_id.strip():
            return None
        relative = Path(image_id)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            return None
        root = self.storage.config.root.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not relative.parts or relative.parts[0] != "images":
            return None
        return candidate

    def image_path(self, image_id: str) -> Path:
        target = self._safe_image_path(image_id)
        if target is None or not target.is_file():
            raise ReviewNotFoundError("The requested review image is not available")
        return target

    def _expected_bin(self, category: object) -> Optional[Dict[str, Any]]:
        if not isinstance(category, str):
            return None
        normalized = normalize_review_category(category)
        if normalized == "unknown":
            return None
        # Category-only review labels cannot distinguish stretch plastic from
        # other plastic subtypes, so the compatibility shortcut is not an
        # authoritative expected-bin mapping here.
        if normalized == "plastic":
            return None
        bin_id = self.mapping_config.category_mapping.get(normalized)
        if not bin_id:
            return None
        definition = self.mapping_config.bins.get(bin_id)
        if definition is None:
            return None
        return {
            "bin_id": definition.bin_id,
            "bin_number": definition.bin_number,
            "bin_name": definition.bin_name,
        }

    def _item(
        self,
        inference: Mapping[str, Any],
        state: Optional[ReviewState],
    ) -> Dict[str, Any]:
        request_id = str(inference.get("request_id"))
        final = state.final if state else None
        review_status = final.get("review_status") if final else "pending"
        image_id = inference.get("image_id") or inference.get("image_path")
        image_target = self._safe_image_path(image_id)
        image_available = bool(image_target and image_target.is_file())
        review = {
            "status": review_status,
            "revision": state.latest_revision if state else 0,
            "last_action": state.latest_action if state else None,
            "reviewer_id": final.get("reviewer_id") if final else None,
            "reviewed_at": final.get("reviewed_at") if final else None,
            "review_action": final.get("review_action") if final else None,
            "ground_truth_waste_category": final.get("ground_truth_waste_category") if final else None,
            "note": final.get("note") if final else None,
        }
        expected_bin = self._expected_bin(review["ground_truth_waste_category"])
        return {
            "request_id": request_id,
            "timestamp": inference.get("timestamp"),
            "location_id": inference.get("location_id"),
            "image_id": image_id,
            "image_available": image_available,
            "object_name_th": inference.get("object_name_th"),
            "waste_category": inference.get("waste_category"),
            "resin_identification_code": inference.get("resin_identification_code", "unknown"),
            "confidence": inference.get("confidence"),
            "has_food_scraps": inference.get("has_food_scraps"),
            "bin_id": inference.get("bin_id"),
            "bin_number": inference.get("bin_number"),
            "bin_name": inference.get("bin_name"),
            "mapping_version": inference.get("mapping_version"),
            "classification_status": inference.get("classification_status"),
            "mapping_fallback": inference.get("mapping_fallback"),
            "status": inference.get("status"),
            "model_identifier": inference.get("model_identifier"),
            "prompt_version": inference.get("prompt_version"),
            "inference_latency_ms": inference.get("inference_latency_ms"),
            "total_latency_ms": inference.get("total_latency_ms"),
            "error_type": inference.get("error_type"),
            "storage_error_type": inference.get("storage_error_type"),
            "review": review,
            "expected_bin": expected_bin,
        }

    def queue(
        self,
        *,
        review_status: str = "all",
        predicted_category: Optional[str] = None,
        location_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        review_status = _validate_filter(review_status, {"all", "pending", "reviewed", "unreviewable"}, "review_status")
        predicted = normalize_review_category(predicted_category) if predicted_category else None
        _validate_date(date_from, "date_from")
        _validate_date(date_to, "date_to")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ReviewValidationError("limit must be between 1 and 100")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ReviewValidationError("offset must not be negative")

        inferences, malformed_inferences = self._inference_records()
        annotations, malformed_annotations = self._annotation_records()
        states = self._review_states(annotations)
        items: List[Dict[str, Any]] = []
        for inference in inferences:
            item = self._item(inference, states.get(str(inference.get("request_id"))))
            if review_status != "all" and item["review"]["status"] != review_status:
                continue
            if predicted and normalize_waste_category(item.get("waste_category")) != predicted:
                continue
            if location_id and item.get("location_id") != location_id:
                continue
            day = str(item.get("timestamp") or "")[:10]
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
            items.append(item)

        items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        total = len(items)
        return {
            "items": items[offset : offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
            "categories": self.categories,
            "location_id": self.mapping_config.location_id,
            "data_quality": {
                "malformed_inference_records": malformed_inferences,
                "malformed_annotation_records": malformed_annotations,
            },
        }

    def submit(self, request_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ReviewValidationError("request_id must be a non-empty string")
        action, category, reviewer_id, note, expected_revision = _validate_submission(payload)

        inference_records, _ = self._inference_records()
        inference = next((item for item in inference_records if item.get("request_id") == request_id), None)
        if inference is None:
            raise ReviewNotFoundError("No inference record exists for this request_id")

        image_id = inference.get("image_id") or inference.get("image_path")
        image_available = bool(self._safe_image_path(image_id) and self._safe_image_path(image_id).is_file())
        if action == "label" and not image_available:
            raise ReviewConflictError(
                "The image is unavailable; mark the item unreviewable instead",
                conflict="missing_image",
            )

        try:
            self.storage.initialize()
        except StorageFailure as exc:
            raise ReviewUnavailableError("Review annotations cannot be stored", error_type=type(exc).__name__) from exc

        with self._exclusive_annotation_lock():
            annotations, _ = self._annotation_records()
            states = self._review_states(annotations)
            state = states.get(request_id, ReviewState(0, None, None))
            if expected_revision != state.latest_revision:
                raise ReviewConflictError(
                    "The review changed; reload before submitting",
                    conflict="revision_conflict",
                    current_revision=state.latest_revision,
                )

            if action == "label":
                previous_category = state.final.get("ground_truth_waste_category") if state.final else None
                previous_action = state.final.get("review_action") if state.final else None
                if previous_action in {"confirm", "correct"} and previous_category == category:
                    raise ReviewConflictError(
                        "This final label already exists; submit a changed label to create a revision",
                        conflict="duplicate_final_review",
                        current_revision=state.latest_revision,
                    )
                predicted = normalize_waste_category(inference.get("waste_category"))
                stored_action = "confirm" if predicted == category else "correct"
                review_status = "reviewed"
                is_final = True
            elif action == "unreviewable":
                stored_action = "unreviewable"
                review_status = "unreviewable"
                category = None
                is_final = True
            else:
                stored_action = "skip"
                review_status = "pending"
                category = None
                is_final = False

            revision = state.latest_revision + 1
            timestamp = _utc_timestamp()
            record = {
                "request_id": request_id,
                "image_id": image_id,
                "image_path": image_id,
                "revision": revision,
                "reviewer_id": reviewer_id,
                "reviewed_at": timestamp if is_final else None,
                "recorded_at": timestamp,
                "review_action": stored_action,
                "review_status": review_status,
                "is_final": is_final,
                "ground_truth_waste_category": category,
                "note": note,
            }
            try:
                self.storage.append_ground_truth_annotation(record)
            except StorageFailure as exc:
                raise ReviewUnavailableError(
                    "Review annotation could not be stored",
                    error_type=type(exc).__name__,
                ) from exc

        return {
            "request_id": request_id,
            "annotation": record,
            "review": self._item(inference, self._review_states([record]).get(request_id))["review"],
        }

    @staticmethod
    def _latest_pilot_annotations(records: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for record in records:
            request_id = record.get("request_id")
            if record.get("evaluation_scope") != "pilot" or not isinstance(request_id, str):
                continue
            current = latest.get(request_id)
            raw_revision = record.get("revision", 0)
            revision = raw_revision if isinstance(raw_revision, int) and not isinstance(raw_revision, bool) else 0
            raw_current_revision = current.get("revision", 0) if current else -1
            current_revision = raw_current_revision if isinstance(raw_current_revision, int) and not isinstance(raw_current_revision, bool) else -1
            if current is None or (
                revision >= current_revision
            ):
                latest[request_id] = dict(record)
        return latest

    def pilot_evaluation(self, request_id: str) -> Dict[str, Any]:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ReviewValidationError("request_id must be a non-empty string")
        inferences, _ = self._inference_records()
        inference = next((row for row in inferences if row.get("request_id") == request_id), None)
        if inference is None:
            raise ReviewNotFoundError("No inference record exists for this request_id")
        annotations, _ = self._annotation_records()
        evaluation = self._latest_pilot_annotations(annotations).get(request_id)
        configured_bins = [
            {"number": definition.bin_number, "name": definition.bin_name}
            for definition in self.mapping_config.bins.values()
            if definition.enabled
        ]
        configured_bins.sort(key=lambda item: item["number"])
        return {"request_id": request_id, "evaluation": evaluation, "configured_bins": configured_bins}

    def submit_pilot_evaluation(self, request_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ReviewValidationError("request_id must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise ReviewValidationError("Request body must be an object")

        valid_capture = payload.get("valid_capture")
        if not isinstance(valid_capture, bool):
            raise ReviewValidationError("valid_capture must be a boolean")
        expected_revision = payload.get("expected_revision", 0)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ReviewValidationError("expected_revision must be a non-negative integer")
        note = payload.get("note")
        if note is not None and not isinstance(note, str):
            raise ReviewValidationError("note must be a string or null")
        note = note.strip() if isinstance(note, str) else None
        if note and len(note) > 2000:
            raise ReviewValidationError("note must not exceed 2000 characters")

        category: Optional[str] = None
        subtype: Optional[str] = None
        expected_bin_number: Optional[int] = None
        if valid_capture:
            category = normalize_review_category(payload.get("expected_waste_category"))
            raw_subtype = payload.get("expected_material_subtype")
            if raw_subtype not in (None, ""):
                if not isinstance(raw_subtype, str):
                    raise ReviewValidationError("expected_material_subtype must be a string or null")
                subtype = normalize_material_subtype(raw_subtype)
                if subtype == "unknown":
                    raise ReviewValidationError("Unsupported material subtype", material_subtype=raw_subtype)
            raw_bin = payload.get("expected_bin_number")
            if raw_bin not in (None, ""):
                if isinstance(raw_bin, bool):
                    raise ReviewValidationError("expected_bin_number must be a configured bin number")
                try:
                    expected_bin_number = int(raw_bin)
                except (TypeError, ValueError):
                    raise ReviewValidationError("expected_bin_number must be a configured bin number") from None
                configured_bins = {
                    definition.bin_number
                    for definition in self.mapping_config.bins.values()
                    if definition.enabled
                }
                if expected_bin_number not in configured_bins:
                    raise ReviewValidationError("expected_bin_number must be a configured bin number")
            if category != "unknown" and expected_bin_number is None:
                raise ReviewValidationError("expected_bin_number is required for a valid capture")
        elif any(payload.get(key) not in (None, "") for key in (
            "expected_waste_category", "expected_material_subtype", "expected_bin_number",
        )):
            raise ReviewValidationError("Ground-truth fields must be empty for an invalid capture")

        inferences, _ = self._inference_records()
        inference = next((row for row in inferences if row.get("request_id") == request_id), None)
        if inference is None:
            raise ReviewNotFoundError("No inference record exists for this request_id")
        if valid_capture:
            image_id = inference.get("image_id") or inference.get("image_path")
            image_path = self._safe_image_path(image_id)
            if image_path is None or not image_path.is_file():
                raise ReviewConflictError("A valid capture requires an available image", conflict="missing_image")

        try:
            self.storage.initialize()
        except StorageFailure as exc:
            raise ReviewUnavailableError("Pilot evaluations cannot be stored", error_type=type(exc).__name__) from exc

        with self._exclusive_annotation_lock():
            annotations, _ = self._annotation_records()
            latest = self._latest_pilot_annotations(annotations)
            previous = latest.get(request_id)
            revision = previous.get("revision", 0) if previous else 0
            if expected_revision != revision:
                raise ReviewConflictError(
                    "The pilot evaluation changed; reload before submitting",
                    conflict="revision_conflict",
                    current_revision=revision,
                )
            timestamp = _utc_timestamp()
            image_id = inference.get("image_id") or inference.get("image_path")
            record = {
                "request_id": request_id,
                "image_id": image_id,
                "image_path": image_id,
                "revision": revision + 1,
                "reviewer_id": "pilot.tester",
                "reviewed_at": timestamp,
                "recorded_at": timestamp,
                "review_action": "pilot_evaluation",
                "review_status": "pilot_evaluated",
                "is_final": True,
                "evaluation_scope": "pilot",
                "valid_capture": valid_capture,
                "ground_truth_waste_category": category,
                "expected_waste_category": category,
                "expected_material_subtype": subtype,
                "expected_bin_number": expected_bin_number,
                "note": note,
            }
            try:
                self.storage.append_ground_truth_annotation(record)
            except StorageFailure as exc:
                raise ReviewUnavailableError(
                    "Pilot evaluation could not be stored",
                    error_type=type(exc).__name__,
                ) from exc
        return {"request_id": request_id, "evaluation": record}

    def pilot_metrics(self) -> Dict[str, Any]:
        inferences, malformed_inferences = self._inference_records()
        annotations, malformed_annotations = self._annotation_records()
        latest = self._latest_pilot_annotations(annotations)
        by_request_id = {str(row.get("request_id")): row for row in inferences}
        samples = [
            (by_request_id[request_id], evaluation)
            for request_id, evaluation in latest.items()
            if request_id in by_request_id
        ]
        invalid_captures = sum(evaluation.get("valid_capture") is False for _, evaluation in samples)
        valid_samples = [(inference, evaluation) for inference, evaluation in samples if evaluation.get("valid_capture") is True]
        api_errors = [(inference, evaluation) for inference, evaluation in samples if inference.get("status") == "api_error"]
        timeouts = [
            (inference, evaluation) for inference, evaluation in api_errors
            if "timeout" in str(inference.get("error_type") or "").lower()
        ]
        completed = [
            (inference, evaluation) for inference, evaluation in valid_samples
            if inference.get("status") in {"success", "unknown", "missing_mapping"}
        ]
        class_scored = [
            (inference, evaluation) for inference, evaluation in completed
            if evaluation.get("expected_waste_category") is not None
        ]
        class_correct = sum(
            _canonical_prediction(inference.get("waste_category")) == evaluation.get("expected_waste_category")
            for inference, evaluation in class_scored
        )
        bin_scored = [
            (inference, evaluation) for inference, evaluation in completed
            if isinstance(evaluation.get("expected_bin_number"), int)
            and not isinstance(evaluation.get("expected_bin_number"), bool)
        ]
        bin_correct = sum(inference.get("bin_number") == evaluation.get("expected_bin_number") for inference, evaluation in bin_scored)
        subtype_scored = [
            (inference, evaluation) for inference, evaluation in completed
            if evaluation.get("expected_material_subtype") is not None
        ]
        subtype_correct = sum(inference.get("material_subtype") == evaluation.get("expected_material_subtype") for inference, evaluation in subtype_scored)
        threshold = self.mapping_config.confidence_threshold
        confidence_samples = [
            inference.get("confidence") for inference, _ in completed
            if isinstance(inference.get("confidence"), (int, float))
            and not isinstance(inference.get("confidence"), bool)
        ]
        latencies = _numeric_values(inference.get("inference_latency_ms") for inference, _ in completed)
        return {
            "evaluation_scope": "pilot",
            "threshold": threshold,
            "configured_bins": [
                {"number": definition.bin_number, "name": definition.bin_name}
                for definition in sorted(self.mapping_config.bins.values(), key=lambda item: item.bin_number)
                if definition.enabled
            ],
            "attempted_scans": len(samples),
            "invalid_captures": invalid_captures,
            "valid_captures": len(valid_samples),
            "completed_predictions": len(completed),
            "valid_capture_completion_rate": _ratio(len(completed), len(valid_samples)),
            "classification_accuracy": {
                "value": _ratio(class_correct, len(class_scored)),
                "correct": class_correct,
                "eligible_samples": len(class_scored),
            },
            "bin_accuracy": {
                "value": _ratio(bin_correct, len(bin_scored)),
                "correct": bin_correct,
                "eligible_samples": len(bin_scored),
            },
            "subtype_accuracy": {
                "value": _ratio(subtype_correct, len(subtype_scored)),
                "correct": subtype_correct,
                "eligible_samples": len(subtype_scored),
            },
            "timeout_count": len(timeouts),
            "api_error_count": len(api_errors),
            "classification_error_count": len(class_scored) - class_correct,
            "confidence_breakdown": {
                "at_or_above_threshold": sum(value >= threshold for value in confidence_samples),
                "below_threshold": sum(value < threshold for value in confidence_samples),
                "threshold": threshold,
            },
            "completed_inference_latency_ms": {
                "count": len(latencies),
                "median": _percentile(latencies, 0.5),
                "p95": _percentile(latencies, 0.95),
            },
            "data_quality": {
                "malformed_inference_records": malformed_inferences,
                "malformed_annotation_records": malformed_annotations,
            },
        }

    def metrics(self) -> Dict[str, Any]:
        inferences, malformed_inferences = self._inference_records()
        annotations, malformed_annotations = self._annotation_records()
        states = self._review_states(annotations)
        items = [self._item(record, states.get(str(record.get("request_id")))) for record in inferences]

        total_scans = len(items)
        reviewed = [item for item in items if item["review"]["status"] == "reviewed"]
        unreviewable = [item for item in items if item["review"]["status"] == "unreviewable"]
        pending = total_scans - len(reviewed) - len(unreviewable)
        image_saved = sum(1 for item in items if item.get("storage_error_type") is None and item.get("image_available"))
        api_errors = sum(1 for item in items if item.get("status") == "api_error")
        missing_mappings = sum(1 for item in items if item.get("status") == "missing_mapping")
        unknowns = sum(1 for item in items if item.get("status") == "unknown")

        eligible_classification: List[Tuple[str, str]] = []
        eligible_bin: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        confusion = {category: {predicted: 0 for predicted in self.categories} for category in self.categories}
        classification_errors: Dict[str, int] = {category: 0 for category in self.categories}
        class_correct = 0
        mapping_correct = 0
        mapping_total = 0
        recommendation_errors = 0
        mapping_errors = 0
        classification_and_mapping_errors = 0

        for item in reviewed:
            if item.get("status") == "api_error":
                continue
            actual = item["review"].get("ground_truth_waste_category")
            predicted = _canonical_prediction(item.get("waste_category"))
            if not actual or predicted is None:
                continue
            eligible_classification.append((actual, predicted))
            confusion.setdefault(actual, {category: 0 for category in self.categories})
            confusion[actual].setdefault(predicted, 0)
            confusion[actual][predicted] += 1
            if actual == predicted:
                class_correct += 1
            else:
                classification_errors[predicted] = classification_errors.get(predicted, 0) + 1

            expected = self._expected_bin(actual)
            if expected is None:
                continue
            mapping_total += 1
            eligible_bin.append((item, expected))
            if item.get("bin_id") == expected["bin_id"]:
                mapping_correct += 1
            else:
                recommendation_errors += 1
            predicted_expected = self._expected_bin(predicted)
            routing_error = bool(
                predicted_expected
                and item.get("bin_id") != predicted_expected.get("bin_id")
            )
            if actual == predicted and routing_error:
                mapping_errors += 1
            elif actual != predicted and routing_error:
                classification_and_mapping_errors += 1

        per_category: Dict[str, Dict[str, Any]] = {}
        for category in self.categories:
            tp = confusion.get(category, {}).get(category, 0)
            fp = sum(row.get(category, 0) for actual, row in confusion.items() if actual != category)
            fn = sum(value for predicted, value in confusion.get(category, {}).items() if predicted != category)
            precision = _ratio(tp, tp + fp)
            recall = _ratio(tp, tp + fn)
            per_category[category] = {
                "precision": precision,
                "recall": recall,
                "f1": _f1(precision, recall),
                "support": tp + fn,
            }

        inference_latencies = _numeric_values(item.get("inference_latency_ms") for item in items)
        total_latencies = _numeric_values(item.get("total_latency_ms") for item in items)
        return {
            "location_id": self.mapping_config.location_id,
            "mapping_version": self.mapping_config.mapping_version,
            "categories": self.categories,
            "total_scans": total_scans,
            "successfully_recorded_scans": total_scans,
            "images_saved": image_saved,
            "image_save_failures": total_scans - image_saved,
            "pending_reviews": max(0, pending),
            "reviewed_samples": len(reviewed),
            "unreviewable_samples": len(unreviewable),
            "sample_size_warning": len(reviewed) < 30,
            "classification_accuracy": {
                "value": _ratio(class_correct, len(eligible_classification)),
                "correct": class_correct,
                "eligible_samples": len(eligible_classification),
            },
            "bin_recommendation_accuracy": {
                "value": _ratio(mapping_correct, mapping_total),
                "correct": mapping_correct,
                "eligible_samples": mapping_total,
            },
            "per_category": per_category,
            "confusion_matrix": {
                "categories": self.categories,
                "matrix": confusion,
            },
            "error_distribution_by_predicted_category": classification_errors,
            "error_counts": {
                "classification_errors": sum(classification_errors.values()),
                "recommendation_errors": recommendation_errors,
                "mapping_errors": mapping_errors,
                "classification_and_mapping_errors": classification_and_mapping_errors,
                "api_errors": api_errors,
                "missing_mappings": missing_mappings,
                "unknown_predictions": unknowns,
            },
            "failure_rates": {
                "api_error_count": api_errors,
                "api_error_rate": _ratio(api_errors, total_scans),
                "image_save_failure_count": total_scans - image_saved,
                "image_save_failure_rate": _ratio(total_scans - image_saved, total_scans),
            },
            "latency_ms": {
                "inference": _latency_summary(inference_latencies),
                "total": _latency_summary(total_latencies),
            },
            "data_quality": {
                "malformed_inference_records": malformed_inferences,
                "malformed_annotation_records": malformed_annotations,
            },
        }

    def errors(self, error_type: str = "all") -> Dict[str, Any]:
        if error_type not in ERROR_TYPES:
            raise ReviewValidationError(
                "Unsupported error_type",
                allowed=sorted(ERROR_TYPES),
            )
        inferences, malformed_inferences = self._inference_records()
        annotations, malformed_annotations = self._annotation_records()
        states = self._review_states(annotations)
        items: List[Dict[str, Any]] = []
        for inference in inferences:
            item = self._item(inference, states.get(str(inference.get("request_id"))))
            detail = self._error_detail(item)
            if detail["error_type"] in {None, "pending", "unreviewable"} and error_type == "all":
                continue
            if error_type != "all" and detail["error_type"] != error_type:
                continue
            item["error"] = detail
            items.append(item)
        items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return {
            "items": items,
            "total": len(items),
            "error_type": error_type,
            "data_quality": {
                "malformed_inference_records": malformed_inferences,
                "malformed_annotation_records": malformed_annotations,
            },
        }

    def _error_detail(self, item: Mapping[str, Any]) -> Dict[str, Any]:
        review = item.get("review") or {}
        if review.get("status") == "pending":
            return {"error_type": "pending", "expected_bin": None}
        if review.get("status") == "unreviewable":
            return {"error_type": "unreviewable", "expected_bin": None}
        if item.get("status") == "api_error":
            return {"error_type": "api_error", "expected_bin": None}

        actual = review.get("ground_truth_waste_category")
        predicted = _canonical_prediction(item.get("waste_category"))
        expected = self._expected_bin(actual)
        classification_error = actual is not None and predicted is not None and actual != predicted
        predicted_expected = self._expected_bin(predicted)
        mapping_error = bool(
            predicted_expected
            and item.get("bin_id") != predicted_expected.get("bin_id")
        )
        recommendation_error = bool(expected and item.get("bin_id") != expected.get("bin_id"))
        if classification_error and mapping_error:
            kind = "classification_and_mapping_error"
        elif classification_error:
            kind = "classification_error"
        elif mapping_error or recommendation_error:
            kind = "mapping_error"
        else:
            kind = None
        return {
            "error_type": kind,
            "actual_category": actual,
            "predicted_category": predicted,
            "expected_bin": expected,
            "predicted_expected_bin": predicted_expected,
            "recommended_bin": {
                "bin_id": item.get("bin_id"),
                "bin_number": item.get("bin_number"),
                "bin_name": item.get("bin_name"),
            },
            "classification_error": classification_error,
            "mapping_error": mapping_error,
            "recommendation_error": recommendation_error,
        }

    @contextmanager
    def _exclusive_annotation_lock(self) -> Iterator[None]:
        self.storage.initialize()
        self._annotation_lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            str(self._annotation_lock_path),
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with self._annotation_thread_lock:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def normalize_review_category(value: object) -> str:
    if not isinstance(value, str):
        raise ReviewValidationError("category must be a string")
    normalized = " ".join(value.strip().lower().split())
    if normalized in REVIEW_CATEGORY_ALIASES:
        return REVIEW_CATEGORY_ALIASES[normalized]
    if normalized not in SUPPORTED_WASTE_CATEGORIES:
        raise ReviewValidationError(
            "Unsupported category label",
            category=value,
            allowed=sorted(SUPPORTED_WASTE_CATEGORIES | set(REVIEW_CATEGORY_ALIASES)),
        )
    return normalized


def _validate_submission(payload: Mapping[str, Any]) -> Tuple[str, Optional[str], str, Optional[str], int]:
    if not isinstance(payload, Mapping):
        raise ReviewValidationError("Request body must be an object")
    reviewer_id = payload.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not REVIEWER_ID_PATTERN.fullmatch(reviewer_id.strip()):
        raise ReviewValidationError("reviewer_id must be 1-64 safe characters")
    reviewer_id = reviewer_id.strip()

    action = payload.get("review_action")
    if action not in REVIEW_ACTIONS:
        raise ReviewValidationError("review_action must be label, unreviewable, or skip")

    category: Optional[str] = None
    if action == "label":
        category = normalize_review_category(payload.get("category"))
    elif payload.get("category") not in (None, ""):
        raise ReviewValidationError("category is only valid for a label action")

    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        raise ReviewValidationError("note must be a string or null")
    note = note.strip() if isinstance(note, str) else None
    if note and len(note) > 2000:
        raise ReviewValidationError("note must not exceed 2000 characters")

    expected_revision = payload.get("expected_revision", 0)
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ReviewValidationError("expected_revision must be a non-negative integer")
    return action, category, reviewer_id, note or None, expected_revision


def _validate_filter(value: str, allowed: set[str], name: str) -> str:
    if value not in allowed:
        raise ReviewValidationError(f"Unsupported {name}", allowed=sorted(allowed))
    return value


def _validate_date(value: Optional[str], name: str) -> None:
    if value is not None and not DATE_PATTERN.fullmatch(value):
        raise ReviewValidationError(f"{name} must use YYYY-MM-DD")


def _canonical_prediction(value: object) -> Optional[str]:
    if value is None:
        return None
    return normalize_waste_category(value)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _f1(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 6)


def _numeric_values(values: Sequence[object]) -> List[float]:
    return sorted(
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    value = values[lower] + (values[upper] - values[lower]) * fraction
    return round(value, 3)


def _latency_summary(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None}
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 3),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
