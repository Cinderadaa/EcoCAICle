"""Local Phase 4 image storage and append-only inference records.

This module deliberately has no HTTP routes and no database dependency.  It
stores only the original accepted upload bytes and JSONL records keyed by the
backend-generated request ID.  Storage errors are raised to the caller so the
inference layer can report them through a safe diagnostic channel without
changing the classification result.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

try:  # Linux containers use this for cross-process append serialization.
    import fcntl
except ImportError:  # pragma: no cover - retained for non-POSIX local tooling
    fcntl = None


DEFAULT_STORAGE_ROOT = "/tmp/ecoaicle-phase4-data"
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ALLOWED_IMAGE_BYTES = 100 * 1024 * 1024
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,80}$")


class StorageConfigurationError(ValueError):
    """Raised when Phase 4 storage configuration is unsafe or invalid."""


class StorageFailure(RuntimeError):
    """Raised when an image or JSONL record cannot be persisted."""


class ImageValidationError(ValueError):
    """Raised when an upload is empty, too large, or not a supported image."""

    def __init__(self, message: str, *, status_code: int = 400, error_type: str = "InvalidImage"):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class ImageMetadata:
    mime_type: str
    extension: str


@dataclass(frozen=True)
class StoredImage:
    image_id: str
    image_path: str
    mime_type: str
    extension: str


@dataclass(frozen=True)
class StorageConfig:
    root: Path
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    retention_days: Optional[int] = None

    @classmethod
    def from_environment(cls) -> "StorageConfig":
        raw_root = os.environ.get("ECOAICLE_STORAGE_ROOT", DEFAULT_STORAGE_ROOT).strip()
        if not raw_root:
            raise StorageConfigurationError("ECOAICLE_STORAGE_ROOT must not be empty")

        root = Path(raw_root).expanduser().resolve()
        source_root = Path(__file__).resolve().parents[1]
        try:
            root.relative_to(source_root)
        except ValueError:
            pass
        else:
            raise StorageConfigurationError(
                "ECOAICLE_STORAGE_ROOT must be outside the source-code repository"
            )

        raw_max = os.environ.get("ECOAICLE_MAX_IMAGE_BYTES", "").strip()
        max_image_bytes = DEFAULT_MAX_IMAGE_BYTES if not raw_max else _positive_int(
            raw_max,
            "ECOAICLE_MAX_IMAGE_BYTES",
        )
        if max_image_bytes > MAX_ALLOWED_IMAGE_BYTES:
            raise StorageConfigurationError(
                f"ECOAICLE_MAX_IMAGE_BYTES must not exceed {MAX_ALLOWED_IMAGE_BYTES}"
            )

        raw_retention = os.environ.get("ECOAICLE_RETENTION_DAYS", "").strip()
        retention_days = None if not raw_retention else _non_negative_int(
            raw_retention,
            "ECOAICLE_RETENTION_DAYS",
        )

        return cls(
            root=root,
            max_image_bytes=max_image_bytes,
            retention_days=retention_days,
        )


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise StorageConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise StorageConfigurationError(f"{name} must be greater than zero")
    return parsed


def _non_negative_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise StorageConfigurationError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise StorageConfigurationError(f"{name} must not be negative")
    return parsed


_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"BM", "image/bmp", "bmp"),
    (b"II*\x00", "image/tiff", "tiff"),
    (b"MM\x00*", "image/tiff", "tiff"),
)


def detect_image_type(image_bytes: bytes) -> Optional[ImageMetadata]:
    """Return a supported type using file signatures, not client filenames."""

    for signature, mime_type, extension in _IMAGE_SIGNATURES:
        if image_bytes.startswith(signature):
            return ImageMetadata(mime_type=mime_type, extension=extension)
    if len(image_bytes) >= 12 and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return ImageMetadata(mime_type="image/webp", extension="webp")
    return None


def validate_image_bytes(
    image_bytes: bytes,
    *,
    declared_content_type: Optional[str],
    filename: Optional[str],
    max_image_bytes: int,
) -> ImageMetadata:
    """Validate size and basic image type before persistence or inference."""

    if not image_bytes:
        raise ImageValidationError("Uploaded image is empty")
    if len(image_bytes) > max_image_bytes:
        raise ImageValidationError(
            f"Uploaded image exceeds the {max_image_bytes}-byte limit",
            status_code=413,
            error_type="ImageTooLarge",
        )

    declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared and not declared.startswith("image/") and declared != "application/octet-stream":
        raise ImageValidationError("Uploaded file must be an image")

    detected = detect_image_type(image_bytes)
    if detected is None:
        extension = Path(filename or "").suffix.lower()
        raise ImageValidationError(
            f"Unsupported or invalid image type: {extension or 'unknown'}"
        )
    return detected


class InferenceStorage:
    """Filesystem-backed image, inference-log, and review-queue storage."""

    _append_lock = threading.Lock()

    def __init__(self, config: StorageConfig):
        self.config = config
        self._initialized = False

    @property
    def inference_log_path(self) -> Path:
        return self.config.root / "logs" / "inference.jsonl"

    @property
    def ground_truth_path(self) -> Path:
        return self.config.root / "review" / "ground_truth.jsonl"

    @property
    def ground_truth_annotations_path(self) -> Path:
        return self.config.root / "review" / "ground_truth_annotations.jsonl"

    def initialize(self) -> None:
        try:
            for directory in (
                self.config.root,
                self.config.root / "images",
                self.config.root / "logs",
                self.config.root / "review",
            ):
                directory.mkdir(parents=True, exist_ok=True)
                os.chmod(directory, 0o700)
        except OSError as exc:
            raise StorageFailure(f"Could not initialize storage directories: {type(exc).__name__}") from exc
        self._initialized = True

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    def save_image(
        self,
        request_id: str,
        image_bytes: bytes,
        metadata: ImageMetadata,
    ) -> StoredImage:
        self._ensure_initialized()
        if not REQUEST_ID_PATTERN.fullmatch(request_id):
            raise StorageFailure("request_id is not safe for a generated filename")

        now = datetime.now(timezone.utc)
        relative_directory = Path("images") / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
        directory = self.config.root / relative_directory
        target = directory / f"{request_id}.{metadata.extension}"
        temporary = directory / f".{request_id}.{metadata.extension}.tmp"

        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            with open(temporary, "xb") as output:
                os.chmod(temporary, 0o600)
                output.write(image_bytes)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        except FileExistsError as exc:
            self._cleanup_temporary(temporary)
            raise StorageFailure("Generated image filename already exists") from exc
        except OSError as exc:
            self._cleanup_temporary(temporary)
            raise StorageFailure(f"Could not save image: {type(exc).__name__}") from exc

        image_id = (relative_directory / target.name).as_posix()
        return StoredImage(
            image_id=image_id,
            image_path=image_id,
            mime_type=metadata.mime_type,
            extension=metadata.extension,
        )

    @staticmethod
    def _cleanup_temporary(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def append_inference_record(self, record: Mapping[str, Any]) -> None:
        self._append_jsonl(self.inference_log_path, record)

    def append_ground_truth_seed(self, record: Mapping[str, Any]) -> None:
        """Create an independent, explicitly unreviewed ground-truth row."""

        self._append_jsonl(self.ground_truth_path, record)

    def append_ground_truth_annotation(self, record: Mapping[str, Any]) -> None:
        """Append a reviewer action without modifying inference or seed rows."""

        self._append_jsonl(self.ground_truth_annotations_path, record)

    def _append_jsonl(self, path: Path, record: Mapping[str, Any]) -> None:
        self._ensure_initialized()
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(path.parent, 0o700)
            with self._append_lock:
                descriptor = os.open(
                    str(path),
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    if fcntl is not None:
                        fcntl.flock(descriptor, fcntl.LOCK_EX)
                    with os.fdopen(descriptor, "a", encoding="utf-8") as output:
                        descriptor = -1
                        output.write(line)
                        output.flush()
                        os.fsync(output.fileno())
                        if fcntl is not None:
                            fcntl.flock(output.fileno(), fcntl.LOCK_UN)
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
        except (OSError, TypeError, ValueError) as exc:
            raise StorageFailure(f"Could not append JSONL record: {type(exc).__name__}") from exc
