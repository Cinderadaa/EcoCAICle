from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import mimetypes
import os
from pathlib import Path
import socket
import ssl
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse, urlsplit, urlunsplit
from typing import Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

try:  # Supports both Docker's working directory and package-style imports.
    from .mapping_config import MappingConfigError, load_location_config
    from .mapping_service import LocationMismatchError, MappingService
    from .inference_storage import (
        DEFAULT_STORAGE_ROOT,
        ImageValidationError,
        InferenceStorage,
        StorageConfig,
        StorageConfigurationError,
        StorageFailure,
        StoredImage,
        validate_image_bytes,
    )
    from .review_service import (
        ReviewService,
        ReviewServiceError,
    )
except ImportError:  # pragma: no cover - exercised when running app.py directly
    from mapping_config import MappingConfigError, load_location_config
    from mapping_service import LocationMismatchError, MappingService
    from inference_storage import (
        DEFAULT_STORAGE_ROOT,
        ImageValidationError,
        InferenceStorage,
        StorageConfig,
        StorageConfigurationError,
        StorageFailure,
        StoredImage,
        validate_image_bytes,
    )
    from review_service import (
        ReviewService,
        ReviewServiceError,
    )

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - uvicorn[standard] normally provides this
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


API_URL = os.environ.get("QWEN_API_URL", "https://genai-local.boonrawd.co.th/litellm/v1")
MODEL = os.environ.get("QWEN_MODEL", "qwen3.8:27b")
PROMPT_VERSION = os.environ.get("ECOAICLE_PROMPT_VERSION") or None
REVIEWER_TOKEN = os.environ.get("ECOAICLE_REVIEWER_TOKEN")
API_KEY = os.environ.get("QWEN_API_KEY")
CA_BUNDLE = os.environ.get("QWEN_CA_BUNDLE")
SSL_CONTEXT = ssl.create_default_context(cafile=CA_BUNDLE) if CA_BUNDLE else ssl.create_default_context()
LOGGER = logging.getLogger(__name__)
WASTE_TYPES = {
    "general",
    "pet_bottle",
    "plastic",
    "paper",
    "glass",
    "metal",
    "ewaste",
    "fuel",
    "hazardous",
    "infectious",
    "unknown",
}
# Compatibility name for callers that imported the former label set.
LABELS = WASTE_TYPES
UNKNOWN_WASTE_TYPE = "unknown"
LEGACY_CLASS_TO_WASTE_TYPE = {
    "cardboard": "paper",
    "glass": "glass",
    "metal": "metal",
    "paper": "paper",
    "pet": "pet_bottle",
    "other": "plastic",
    "trash": "general",
}
LEGACY_CLASS_ITEM_NAMES = {
    "cardboard": "กระดาษแข็ง",
    "glass": "ขวดแก้ว",
    "metal": "กระป๋องโลหะ",
    "paper": "กระดาษ",
    "pet": "ขวดน้ำพลาสติก PET",
    "other": "พลาสติก",
    "trash": "ขยะทั่วไป",
}
DEFAULT_ITEM_NAMES = {
    "general": "ขยะทั่วไป",
    "pet_bottle": "ขวดพลาสติก PET",
    "plastic": "พลาสติก",
    "paper": "กระดาษ",
    "glass": "ขวดแก้ว",
    "metal": "โลหะ",
    "ewaste": "ขยะอิเล็กทรอนิกส์",
    "fuel": "ขยะเชื้อเพลิง",
    "hazardous": "ขยะอันตราย",
    "infectious": "ขยะติดเชื้อ",
    "unknown": "ไม่สามารถระบุได้",
}
PROMPT = """Identify and classify the primary waste object visible in this image for one of the real bins.
The object description and the waste category are separate decisions.

Object identification rules:
- Identify the main physical waste object held or presented by the user, not the background, hand, camera, or a secondary object.
- Give the most specific short Thai object description supported by visible evidence. Include the object's form and material only when the material is visually identifiable (for example: พลาสติกหลอดครีม, ขวดน้ำพลาสติก, กระปุกยาดม, ขวดเครื่องดื่มแก้ว, กระป๋องเครื่องดื่มอะลูมิเนียม).
- Prefer a specific object such as a bottle, cream tube, inhaler container, glass beverage bottle, or aluminum can over a broad label such as ขยะพลาสติก when the image supports it.
- Never invent a product name, brand, contents, object details, or material that cannot be identified from the image.
- If the exact object is uncertain, use a general description. If identification is unreliable, return ไม่สามารถระบุได้.

Waste classification rules:
- Return exactly one waste_category from this list:
  general, pet_bottle, plastic, paper, glass, metal, ewaste, fuel, hazardous, infectious, unknown.
- Choose waste_category from visible material and classification evidence, independently from object_name_th. Do not derive the category solely from the generated object name or assume a material from an object type.
- pet_bottle means a beverage bottle made of PET plastic, not merely any bottle or any transparent container.
- plastic means identifiable plastic that is not a PET beverage bottle. A transparent plastic cup or other plastic container is not pet_bottle unless PET evidence is visible.
- Distinguish PET plastic from glass using material evidence, not bottle silhouette, neck shape, cap, label, or transparency alone. Both PET and glass can be clear and bottle-shaped.
- Evidence that can support PET includes a visibly thin or flexible/crinkled wall, lightweight molded construction, plastic molding details, a PET/recycling mark, or a characteristic plastic base. Evidence that can support glass includes a visibly rigid/thick wall, substantial heavy-looking base, glass-like hard edges/lip, or characteristic glass refraction. These cues are not individually conclusive; use only what is actually visible.
- Inspect the wall, base, rim, seams, and readable markings before deciding between pet_bottle and glass. Do not treat a plastic cap, beverage contents, reflections, or background as proof of the container material.
- If the object is bottle-shaped but the material evidence is insufficient or conflicting, choose unknown with a low confidence instead of guessing PET or glass.
- Choose hazardous, infectious, or ewaste only when the evidence is clear; otherwise choose unknown.
- If the material or waste category cannot be identified clearly, choose unknown.
- Set has_food_scraps to true only when food residue, leftover food, liquid, grease, or contamination is clearly visible on or in the item. Do not infer food scraps merely because the item is food packaging.
- Set confidence to a number from 0.0 to 1.0 representing confidence in the combined object identification and waste classification. Use a low value when the image is unclear.

Keep the two decisions separate:
- object_name_th describes the visible physical object and must not be used to force a material category.
- waste_category describes the supported material/disposal class. For example, use object_name_th "ขวดน้ำพลาสติก PET" with waste_category "pet_bottle" only when both the beverage-bottle object and PET material are supported; use "ขวดเครื่องดื่มแก้ว" with "glass" only when glass evidence is supported.

Output only valid JSON with no markdown or explanation, in exactly this shape:
{"object_name_th":"ชื่อวัตถุขยะภาษาไทยแบบสั้น","waste_category":"general|pet_bottle|plastic|paper|glass|metal|ewaste|fuel|hazardous|infectious|unknown","has_food_scraps":false,"confidence":0.0}
"""


class ModelResponseError(ValueError):
    """Raised when the upstream response does not follow LiteLLM chat completions."""


def _chat_completions_url(api_url: str = API_URL) -> str:
    """Accept either the LiteLLM v1 base URL or its full chat-completions URL."""
    parts = urlsplit(api_url)
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


app = FastAPI(title="EcoAICle API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# Load and validate the location mapping at startup, then use the immutable
# configuration for /predict resolution. A process restart is intentionally
# required after a JSON configuration change; there is no watcher or hot-reload
# path.
MAPPING_LOCATION_ID = os.environ.get("ECOAICLE_LOCATION_ID", "ICT-001")
MAPPING_CONFIG_DIRECTORY = os.environ.get("ECOAICLE_MAPPING_CONFIG_DIR")
MAPPING_CONFIG = None
MAPPING_SERVICE = None
REVIEW_SERVICE = None


def _default_storage() -> InferenceStorage:
    return InferenceStorage(StorageConfig(root=Path(DEFAULT_STORAGE_ROOT)))


try:
    PHASE4_STORAGE = InferenceStorage(StorageConfig.from_environment())
except StorageConfigurationError as exc:
    # An unsafe storage setting must not make inference unavailable.  Keep the
    # safe development default and emit only a type-level diagnostic.
    LOGGER.warning(
        "Phase 4 storage configuration unavailable error_type=%s; using the safe development default",
        type(exc).__name__,
    )
    PHASE4_STORAGE = _default_storage()


@app.on_event("startup")
async def load_mapping_foundation() -> None:
    global MAPPING_CONFIG, MAPPING_SERVICE, PHASE4_STORAGE, REVIEW_SERVICE
    try:
        MAPPING_CONFIG = load_location_config(
            location_id=MAPPING_LOCATION_ID,
            config_dir=MAPPING_CONFIG_DIRECTORY,
        )
        MAPPING_SERVICE = MappingService(MAPPING_CONFIG)
    except MappingConfigError:
        LOGGER.exception(
            "Mapping configuration failed validation for location %s",
            MAPPING_LOCATION_ID,
        )
        raise
    LOGGER.info(
        "Loaded mapping configuration location=%s version=%s",
        MAPPING_CONFIG.location_id,
        MAPPING_CONFIG.mapping_version,
    )
    try:
        # Storage is initialized at startup, but a filesystem problem remains
        # non-fatal to inference.  Each request still attempts persistence so
        # a transient failure can recover without a process restart.
        PHASE4_STORAGE = InferenceStorage(StorageConfig.from_environment())
        PHASE4_STORAGE.initialize()
        LOGGER.info(
            "Phase 4 storage initialized root=%s retention_days=%s",
            PHASE4_STORAGE.config.root,
            PHASE4_STORAGE.config.retention_days,
        )
    except (StorageConfigurationError, StorageFailure) as exc:
        LOGGER.warning(
            "Phase 4 storage unavailable error_type=%s; inference remains enabled",
            type(exc).__name__,
        )
    REVIEW_SERVICE = ReviewService(PHASE4_STORAGE, MAPPING_CONFIG)


def _one_line(value: object) -> str:
    return " ".join(str(value).split()).lower()


def _parse_model_response(raw: object) -> tuple[str, str, bool, float]:
    """Parse the new contract while accepting the former class response temporarily."""
    text = str(raw).strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        response = json.loads(text)
    except json.JSONDecodeError:
        legacy_label = _one_line(text)
        waste_type = LEGACY_CLASS_TO_WASTE_TYPE.get(legacy_label, UNKNOWN_WASTE_TYPE)
        item_name = LEGACY_CLASS_ITEM_NAMES.get(legacy_label, "")
        return waste_type, item_name or DEFAULT_ITEM_NAMES[waste_type], False, 0.0

    if not isinstance(response, dict):
        return UNKNOWN_WASTE_TYPE, DEFAULT_ITEM_NAMES[UNKNOWN_WASTE_TYPE], False, 0.0

    raw_waste_type = _one_line(
        response.get("waste_category") or response.get("wasteType") or ""
    )
    if not raw_waste_type:
        # Transitional support for the previous seven-class model response.
        legacy_label = _one_line(response.get("class", ""))
        waste_type = LEGACY_CLASS_TO_WASTE_TYPE.get(legacy_label, UNKNOWN_WASTE_TYPE)
    else:
        # Accept legacy labels wherever a category field is supplied, then
        # expose one canonical category to the mapping layer and clients.
        normalized_waste_type = LEGACY_CLASS_TO_WASTE_TYPE.get(
            raw_waste_type,
            raw_waste_type,
        )
        waste_type = (
            normalized_waste_type
            if normalized_waste_type in WASTE_TYPES
            else UNKNOWN_WASTE_TYPE
        )

    item_name = (
        response.get("object_name_th")
        or response.get("objectNameTh")
        or response.get("itemNameTh")
    )
    if not isinstance(item_name, str) or not item_name.strip():
        item_name = LEGACY_CLASS_ITEM_NAMES.get(_one_line(response.get("class", "")), "")
    if not item_name:
        item_name = DEFAULT_ITEM_NAMES[waste_type]
    item_name = " ".join(item_name.split())[:120]

    confidence = response.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
    elif not 0 <= confidence <= 1:
        confidence = 0.0

    has_food_scraps = response.get("has_food_scraps") is True or response.get("hasFoodScraps") is True
    return waste_type, item_name, has_food_scraps, float(confidence)


def _post_json(payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    request = urllib.request.Request(
        _chat_completions_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300, context=SSL_CONTEXT) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ModelResponseError("Expected a JSON object from LiteLLM")
    return result


def _extract_chat_content(result: dict) -> str:
    """Read OpenAI-compatible LiteLLM ``choices[0].message.content``."""
    try:
        choices = result["choices"]
        first_choice = choices[0]
        message = first_choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelResponseError("Missing choices[0].message.content") from exc

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
        if text_parts:
            return "\n".join(text_parts)
    raise ModelResponseError("Expected text content in choices[0].message.content")


def _model_api_error(exc: Exception) -> dict[str, object]:
    """Build a safe, useful error without returning the request or image data."""
    host = urlparse(API_URL).hostname or "configured model host"

    if isinstance(exc, urllib.error.HTTPError):
        try:
            upstream_detail = " ".join(exc.read(1024).decode("utf-8", errors="replace").split())
        except OSError:
            upstream_detail = ""
        error: dict[str, object] = {
            "error": "Model API rejected the request",
            "upstream_status": exc.code,
        }
        if upstream_detail:
            error["upstream_detail"] = upstream_detail[:500]
        return error

    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            message = f"Could not resolve model host {host}; check DNS/VPN and QWEN_API_URL."
        elif isinstance(reason, ssl.SSLCertVerificationError):
            message = f"TLS certificate verification failed for {host}; configure QWEN_CA_BUNDLE if required."
        else:
            message = str(reason)
        return {"error": "Model API is unreachable", "type": type(reason).__name__, "message": message}

    if isinstance(exc, json.JSONDecodeError):
        return {"error": "Model API returned invalid JSON", "type": type(exc).__name__}

    if isinstance(exc, ModelResponseError):
        return {"error": "Model API returned an unexpected response schema", "type": type(exc).__name__}

    return {"error": "Model API request failed", "type": type(exc).__name__, "message": str(exc)}


def _mapping_api_status(classification_status: str) -> str:
    if classification_status == "confirmed":
        return "success"
    if classification_status == "unavailable":
        return "missing_mapping"
    return "unknown"


def _no_preparation(reason: str) -> dict[str, object]:
    """Return a safe contract object with no disposal recommendation."""

    version = None
    if MAPPING_CONFIG is not None:
        configured_version = MAPPING_CONFIG.preparation.get("version")
        if isinstance(configured_version, str):
            version = configured_version
    return {
        "required": False,
        "reason": reason,
        "version": version,
        "signal": "has_food_scraps",
        "signal_value": None,
        "guidance_mode": "combined_food_liquid_contamination",
        "shared_disposal_destination": None,
        "container_disposal": None,
        "steps": [],
        "limitation_th": None,
    }


def _api_error_response(
    request_id: str,
    detail: object,
    *,
    status_code: int,
) -> JSONResponse:
    """Return a structured error without turning transport failures into unknown waste."""

    content = {
        "request_id": request_id,
        "location_id": MAPPING_LOCATION_ID,
        "object_name_th": None,
        "waste_category": None,
        "confidence": None,
        "bin_id": None,
        "bin_number": None,
        "bin_name": None,
        "mapping_version": MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
        "classification_status": "api_error",
        "mapping_fallback": False,
        "status": "api_error",
        "preparation": _no_preparation("api_error"),
        # Keep FastAPI's existing error envelope key for current proxy/client users.
        "detail": detail,
    }
    return JSONResponse(status_code=status_code, content=content)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _elapsed_ms(started_at: Optional[float], finished_at: Optional[float] = None) -> Optional[float]:
    if started_at is None:
        return None
    finished = time.perf_counter() if finished_at is None else finished_at
    return round(max(0.0, (finished - started_at) * 1000), 3)


def _storage_diagnostic(request_id: str, operation: str, exc: Exception) -> None:
    """Log only safe storage diagnostics; never include headers or image data."""

    LOGGER.warning(
        "Phase 4 storage operation failed request_id=%s operation=%s error_type=%s",
        request_id,
        operation,
        type(exc).__name__,
    )


def _safe_ground_truth_seed(
    request_id: str,
    timestamp: str,
    stored_image: Optional[StoredImage],
) -> None:
    if stored_image is None:
        return
    review_record = {
        "request_id": request_id,
        "image_id": stored_image.image_id,
        "image_path": stored_image.image_path,
        "created_at": timestamp,
        "review_status": "unreviewed",
        "ground_truth_object_name_th": None,
        "ground_truth_waste_category": None,
        "ground_truth_has_food_scraps": None,
        "reviewer_id": None,
        "reviewed_at": None,
        "notes": None,
    }
    try:
        PHASE4_STORAGE.append_ground_truth_seed(review_record)
    except Exception as exc:  # Storage must never change inference behavior.
        _storage_diagnostic(request_id, "ground_truth_seed", exc)


def _safe_inference_record(
    *,
    request_id: str,
    timestamp: str,
    request_started_at: float,
    inference_started_at: Optional[float],
    stored_image: Optional[StoredImage],
    image_save_status: str,
    storage_error_type: Optional[str],
    object_name_th: Optional[str],
    waste_category: Optional[str],
    confidence: Optional[float],
    has_food_scraps: Optional[bool],
    bin_id: Optional[str],
    bin_number: Optional[int],
    bin_name: Optional[str],
    mapping_version: Optional[str],
    classification_status: Optional[str],
    mapping_fallback: Optional[bool],
    status: str,
    error_type: Optional[str],
) -> None:
    record = {
        "request_id": request_id,
        "timestamp": timestamp,
        "location_id": MAPPING_LOCATION_ID,
        "image_id": stored_image.image_id if stored_image else None,
        "image_path": stored_image.image_path if stored_image else None,
        "image_save_status": image_save_status,
        "image_mime_type": stored_image.mime_type if stored_image else None,
        "object_name_th": object_name_th,
        "waste_category": waste_category,
        "confidence": confidence,
        "has_food_scraps": has_food_scraps,
        "bin_id": bin_id,
        "bin_number": bin_number,
        "bin_name": bin_name,
        "mapping_version": mapping_version,
        "classification_status": classification_status,
        "mapping_fallback": mapping_fallback,
        "status": status,
        "model_identifier": MODEL,
        "prompt_version": PROMPT_VERSION,
        "inference_latency_ms": _elapsed_ms(inference_started_at),
        "total_latency_ms": _elapsed_ms(request_started_at),
        "error_type": error_type,
        "storage_error_type": storage_error_type,
    }
    try:
        PHASE4_STORAGE.append_inference_record(record)
    except Exception as exc:  # Logging failures use the separate diagnostics channel.
        _storage_diagnostic(request_id, "inference_log", exc)


def _require_reviewer(request: Request) -> None:
    """Require a configured local-only reviewer token for every review route."""

    client_host = request.client.host if request.client else None
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail={"error": "local_only"})
    if not REVIEWER_TOKEN:
        raise HTTPException(
            status_code=503,
            detail={"error": "reviewer_not_configured"},
        )
    provided_token = request.headers.get("X-Reviewer-Token", "")
    if not hmac.compare_digest(provided_token, REVIEWER_TOKEN):
        raise HTTPException(status_code=401, detail={"error": "reviewer_unauthorized"})


def _review_service_or_error() -> ReviewService:
    if REVIEW_SERVICE is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "reviewer_service_unavailable"},
        )
    return REVIEW_SERVICE


def _raise_review_http_error(exc: ReviewServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_detail()) from exc


@app.get("/review/queue")
async def review_queue(
    request: Request,
    review_status: str = "all",
    predicted_category: Optional[str] = None,
    location_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    _require_reviewer(request)
    try:
        return _review_service_or_error().queue(
            review_status=review_status,
            predicted_category=predicted_category,
            location_id=location_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
    except ReviewServiceError as exc:
        _raise_review_http_error(exc)


@app.get("/review/metrics")
async def review_metrics(request: Request) -> dict[str, object]:
    _require_reviewer(request)
    try:
        return _review_service_or_error().metrics()
    except ReviewServiceError as exc:
        _raise_review_http_error(exc)


@app.get("/review/errors")
async def review_errors(
    request: Request,
    error_type: str = "all",
) -> dict[str, object]:
    _require_reviewer(request)
    try:
        return _review_service_or_error().errors(error_type=error_type)
    except ReviewServiceError as exc:
        _raise_review_http_error(exc)


@app.post("/review/annotations/{request_id}")
async def submit_review_annotation(
    request: Request,
    request_id: str,
    payload: dict[str, object] = Body(...),
) -> dict[str, object]:
    _require_reviewer(request)
    try:
        return _review_service_or_error().submit(request_id, payload)
    except ReviewServiceError as exc:
        _raise_review_http_error(exc)


@app.get("/review/images/{image_id:path}")
async def review_image(request: Request, image_id: str) -> Response:
    _require_reviewer(request)
    try:
        image_path = _review_service_or_error().image_path(image_id)
        content = image_path.read_bytes()
    except ReviewServiceError as exc:
        _raise_review_http_error(exc)
    except OSError as exc:
        _raise_review_http_error(
            ReviewServiceError(
                "The review image could not be read",
                error_type=type(exc).__name__,
            )
        )
    media_type = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL}


@app.post("/predict")
async def predict(file: UploadFile = File(...)) -> object:
    request_id = str(uuid.uuid4())
    request_started_at = time.perf_counter()
    timestamp = _utc_timestamp()
    stored_image: Optional[StoredImage] = None
    image_save_status = "failed"
    storage_error_type: Optional[str] = None

    image_bytes = await file.read(PHASE4_STORAGE.config.max_image_bytes + 1)
    try:
        image_metadata = validate_image_bytes(
            image_bytes,
            declared_content_type=file.content_type,
            filename=file.filename,
            max_image_bytes=PHASE4_STORAGE.config.max_image_bytes,
        )
    except ImageValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # Persist the original bytes before inference so a model timeout, unknown
    # result, low-confidence result, or mapping failure remains reviewable.
    try:
        stored_image = PHASE4_STORAGE.save_image(request_id, image_bytes, image_metadata)
        image_save_status = "saved"
        _safe_ground_truth_seed(request_id, timestamp, stored_image)
    except Exception as exc:  # Image storage is explicitly non-fatal.
        storage_error_type = type(exc).__name__
        _storage_diagnostic(request_id, "image_save", exc)

    if MAPPING_SERVICE is None:
        response = _api_error_response(
            request_id,
            {
                "error": "Mapping configuration is not loaded",
                "type": "MappingConfigurationUnavailable",
            },
            status_code=503,
        )
        _safe_inference_record(
            request_id=request_id,
            timestamp=timestamp,
            request_started_at=request_started_at,
            inference_started_at=None,
            stored_image=stored_image,
            image_save_status=image_save_status,
            storage_error_type=storage_error_type,
            object_name_th=None,
            waste_category=None,
            confidence=None,
            has_food_scraps=None,
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
            classification_status="api_error",
            mapping_fallback=False,
            status="api_error",
            error_type="MappingConfigurationUnavailable",
        )
        return response

    image_data_url = (
        f"data:{image_metadata.mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    )
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        "stream": False,
        "temperature": 0,
    }

    inference_started_at = time.perf_counter()
    try:
        result = await asyncio.to_thread(_post_json, payload)
        raw_prediction = _extract_chat_content(result)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ModelResponseError) as exc:
        detail = _model_api_error(exc)
        LOGGER.warning("Qwen request failed: %s", detail)
        response = _api_error_response(request_id, detail, status_code=502)
        _safe_inference_record(
            request_id=request_id,
            timestamp=timestamp,
            request_started_at=request_started_at,
            inference_started_at=inference_started_at,
            stored_image=stored_image,
            image_save_status=image_save_status,
            storage_error_type=storage_error_type,
            object_name_th=None,
            waste_category=None,
            confidence=None,
            has_food_scraps=None,
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
            classification_status="api_error",
            mapping_fallback=False,
            status="api_error",
            error_type=type(exc).__name__,
        )
        return response

    waste_type, item_name_th, has_food_scraps, confidence = _parse_model_response(raw_prediction)
    try:
        mapping_decision = MAPPING_SERVICE.resolve(
            location_id=MAPPING_LOCATION_ID,
            waste_category=waste_type,
            confidence=confidence,
        )
        preparation_decision = MAPPING_SERVICE.resolve_preparation(
            location_id=MAPPING_LOCATION_ID,
            waste_category=waste_type,
            has_food_scraps=has_food_scraps,
            mapping_decision=mapping_decision,
        )
    except (LocationMismatchError, KeyError, MappingConfigError) as exc:
        LOGGER.exception("Mapping resolution failed for request_id=%s", request_id)
        response = _api_error_response(
            request_id,
            {
                "error": "Mapping configuration could not resolve the prediction",
                "type": type(exc).__name__,
            },
            status_code=500,
        )
        _safe_inference_record(
            request_id=request_id,
            timestamp=timestamp,
            request_started_at=request_started_at,
            inference_started_at=inference_started_at,
            stored_image=stored_image,
            image_save_status=image_save_status,
            storage_error_type=storage_error_type,
            object_name_th=item_name_th,
            waste_category=waste_type,
            confidence=confidence,
            has_food_scraps=has_food_scraps,
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
            classification_status="api_error",
            mapping_fallback=False,
            status="api_error",
            error_type=type(exc).__name__,
        )
        return response

    status = _mapping_api_status(mapping_decision.classification_status)
    preparation = preparation_decision.to_dict()
    prediction_payload: dict[str, object] = {
        # Canonical fields for new clients.
        "waste_category": waste_type,
        "object_name_th": item_name_th,
        "has_food_scraps": has_food_scraps,
        # Transitional aliases for the existing frontend contract.
        "wasteType": waste_type,
        "itemNameTh": item_name_th,
        "hasFoodScraps": has_food_scraps,
        "confidence": confidence,
    }

    response = {
        "prediction": prediction_payload,
        "valid_prediction": True,
        "disposal": {
            "hasFoodScraps": has_food_scraps,
            "has_food_scraps": has_food_scraps,
            "preparation": preparation,
        },
        "preparation": preparation,
        "model": MODEL,
        "request_id": request_id,
        "location_id": MAPPING_LOCATION_ID,
        "bin_id": mapping_decision.bin_id,
        "bin_number": mapping_decision.bin_number,
        "bin_name": mapping_decision.bin_name,
        "mapping_version": mapping_decision.mapping_version,
        "classification_status": mapping_decision.classification_status,
        "mapping_fallback": mapping_decision.mapping_fallback,
        "status": status,
    }
    _safe_inference_record(
        request_id=request_id,
        timestamp=timestamp,
        request_started_at=request_started_at,
        inference_started_at=inference_started_at,
        stored_image=stored_image,
        image_save_status=image_save_status,
        storage_error_type=storage_error_type,
        object_name_th=item_name_th,
        waste_category=waste_type,
        confidence=confidence,
        has_food_scraps=has_food_scraps,
        bin_id=mapping_decision.bin_id,
        bin_number=mapping_decision.bin_number,
        bin_name=mapping_decision.bin_name,
        mapping_version=mapping_decision.mapping_version,
        classification_status=mapping_decision.classification_status,
        mapping_fallback=mapping_decision.mapping_fallback,
        status=status,
        error_type=None,
    )
    return response
