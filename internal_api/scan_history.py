"""Read-only access to locally persisted scan records and captured images."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .inference_storage import InferenceStorage, REQUEST_ID_PATTERN

SCAN_METADATA_FIELDS = (
    "request_id",
    "timestamp",
    "location_id",
    "image_id",
    "image_save_status",
    "image_mime_type",
    "object_name_th",
    "waste_category",
    "normalized_waste_category",
    "material_subtype",
    "resin_identification_code",
    "visible_condition",
    "confidence",
    "has_food_scraps",
    "has_liquid",
    "top_candidates",
    "review_flags",
    "bin_id",
    "bin_number",
    "bin_name",
    "mapping_version",
    "classification_status",
    "mapping_fallback",
    "mapping_reason",
    "status",
    "model_identifier",
    "prompt_version",
    "inference_latency_ms",
    "total_latency_ms",
    "error_type",
    "storage_error_type",
)


class ScanNotFoundError(LookupError):
    """A scan or its safely stored image does not exist."""


class ScanHistoryUnavailableError(RuntimeError):
    """The persistent scan log could not be read."""


def _timestamp_key(value: object) -> Tuple[int, float, str]:
    raw = value if isinstance(value, str) else ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.timestamp(), raw)
    except (ValueError, OverflowError):
        return (0, 0.0, raw)


class ScanHistoryService:
    def __init__(self, storage: InferenceStorage):
        self.storage = storage

    def _records(self) -> List[Dict[str, Any]]:
        path = self.storage.inference_log_path
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ScanHistoryUnavailableError("Scan history could not be read") from exc

        by_request_id: Dict[str, Dict[str, Any]] = {}
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            request_id = record.get("request_id")
            if isinstance(request_id, str) and REQUEST_ID_PATTERN.fullmatch(request_id):
                by_request_id[request_id] = record
        return sorted(
            by_request_id.values(),
            key=lambda item: _timestamp_key(item.get("timestamp")),
            reverse=True,
        )

    @staticmethod
    def _needs_glass_pet_review(record: Dict[str, Any]) -> bool:
        flags = record.get("review_flags")
        return bool(isinstance(flags, dict) and flags.get("glass_pet_review_required"))

    def list_scans(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        waste_category: Optional[str] = None,
        bin_number: Optional[str] = None,
        needs_glass_pet_review: bool = False,
    ) -> Dict[str, Any]:
        records = self._records()
        if status:
            records = [record for record in records if str(record.get("status", "")) == status]
        if waste_category:
            records = [
                record for record in records
                if str(record.get("waste_category", record.get("normalized_waste_category", ""))) == waste_category
            ]
        if bin_number:
            records = [record for record in records if str(record.get("bin_number", "")) == bin_number]
        if needs_glass_pet_review:
            records = [record for record in records if self._needs_glass_pet_review(record)]

        total = len(records)
        bounded_limit = max(1, min(int(limit), 100))
        bounded_offset = max(0, int(offset))
        page = records[bounded_offset : bounded_offset + bounded_limit]
        items: List[Dict[str, Any]] = []
        for record in page:
            item = {field: record[field] for field in SCAN_METADATA_FIELDS if field in record}
            item.setdefault("resin_identification_code", "unknown")
            # Never expose an absolute filesystem path. Stored image IDs are
            # relative identifiers and the image route validates them again.
            image_path = self._safe_image_path(record)
            item["image_id"] = (
                image_path.relative_to(self.storage.config.root.resolve()).as_posix()
                if image_path is not None
                else None
            )
            item["image_available"] = bool(image_path and image_path.is_file())
            items.append(item)
        return {
            "items": items,
            "total": total,
            "limit": bounded_limit,
            "offset": bounded_offset,
            "has_more": bounded_offset + len(items) < total,
        }

    def _safe_image_path(self, record: Dict[str, Any]) -> Optional[Path]:
        image_id = record.get("image_id") or record.get("image_path")
        if not isinstance(image_id, str) or not image_id.strip():
            return None
        relative = Path(image_id)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "images":
            return None
        root = self.storage.config.root.resolve()
        image_root = (root / "images").resolve()
        try:
            image_root.relative_to(root)
        except ValueError:
            return None
        target = (root / relative).resolve()
        try:
            target.relative_to(image_root)
        except ValueError:
            return None
        return target

    def image_path(self, request_id: str) -> Path:
        if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
            raise ScanNotFoundError("The requested scan image is not available")
        for record in self._records():
            if record.get("request_id") != request_id:
                continue
            path = self._safe_image_path(record)
            if path is None or not path.is_file():
                break
            return path
        raise ScanNotFoundError("The requested scan image is not available")
