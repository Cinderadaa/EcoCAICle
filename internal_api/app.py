from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
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
    from .mapping_config import (
        MappingConfigError,
        SUPPORTED_MATERIAL_SUBTYPES,
        configured_object_name_override,
        load_location_config,
        normalize_material_subtype,
        normalize_object_name_th,
        normalize_visible_condition,
        normalize_waste_category,
    )
    from .mapping_service import LocationMismatchError, MappingService
    from .model_request import build_model_request_payload
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
    from .scan_history import ScanHistoryService, ScanNotFoundError, ScanHistoryUnavailableError
    from .historical_poc import GROUND_TRUTH_SOURCES, build_poc_dataset, public_row
    from .inference_storage import REQUEST_ID_PATTERN
except ImportError:  # pragma: no cover - exercised when running app.py directly
    from mapping_config import (
        MappingConfigError,
        SUPPORTED_MATERIAL_SUBTYPES,
        configured_object_name_override,
        load_location_config,
        normalize_material_subtype,
        normalize_object_name_th,
        normalize_visible_condition,
        normalize_waste_category,
    )
    from mapping_service import LocationMismatchError, MappingService
    from model_request import build_model_request_payload
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
    from scan_history import ScanHistoryService, ScanNotFoundError, ScanHistoryUnavailableError
    from historical_poc import GROUND_TRUTH_SOURCES, build_poc_dataset, public_row
    from inference_storage import REQUEST_ID_PATTERN

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - uvicorn[standard] normally provides this
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


API_URL = os.environ.get("QWEN_API_URL", "https://genai-local.boonrawd.co.th/litellm/v1")
QWEN_PROVIDER = os.environ.get("QWEN_PROVIDER", "litellm").strip().lower()
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def _model_from_environment(value: object = None) -> str:
    configured = os.environ.get("QWEN_MODEL", "qwen3.8:27b") if value is None else value
    return str(configured).strip() or "qwen3.8:27b"


MODEL = _model_from_environment()


def _timeout_seconds_from_environment(value: object = None) -> int:
    configured = os.environ.get("QWEN_TIMEOUT_SECONDS", "60") if value is None else value
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return 60


QWEN_REQUEST_TIMEOUT_SECONDS = _timeout_seconds_from_environment()
GLASS_PET_REVIEW_CONFIDENCE_GAP = 0.15
PROMPT_VERSION = os.environ.get("ECOCAICLE_PROMPT_VERSION") or "qwen-waste-classification-v12"
REVIEWER_TOKEN = os.environ.get("ECOCAICLE_REVIEWER_TOKEN")
API_KEY = os.environ.get("QWEN_API_KEY")
CA_BUNDLE = os.environ.get("QWEN_CA_BUNDLE")
SSL_CONTEXT = ssl.create_default_context(cafile=CA_BUNDLE) if CA_BUNDLE else ssl.create_default_context()
LOGGER = logging.getLogger(__name__)
FALLBACK_METRICS = {"low_confidence": 0, "response_invalid": 0}


def _trace_scan_event(event: str, cycle_id: str, **details: object) -> None:
    """Emit opt-in local diagnostics for tracing a scan through FastAPI."""
    if os.environ.get("ECOCAICLE_SCAN_TRACE") != "1":
        return
    entry = {
        "event": event,
        "cycleId": cycle_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    print(f"[ScanTrace] {json.dumps(entry, sort_keys=True)}", flush=True)


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
DEFAULT_ITEM_NAMES = {
    "general": "ขยะทั่วไป",
    "pet_bottle": "ขวดพลาสติก",
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
PROMPT = """Classify the main foreground item from visible evidence only; ignore hands, people, background, labels/caps as the main material, and minor accessories. Do not infer unseen details or any bin/disposal decision.

Set visible_object_detected to false only for a genuinely absent-object frame (empty, hand-only, or unusably occluded): classification_reason="no_object_visible", Thai object_name_th="ไม่พบวัตถุสำหรับคัดแยกในภาพ", category/subtype="unknown", confidence 0.0–0.2, unknown resin/condition, and 3 consistent unknown candidates. If an object is visible but unsupported or uncertain, set visible_object_detected=true and classification_reason "visible_object_unclassified"; name it briefly in Thai and use unknown category/subtype when evidence is insufficient.

Use waste_category from general, pet_bottle, plastic, paper, glass, metal, ewaste, fuel, hazardous, infectious, unknown. Use material_subtype from pet_bottle, stretch_plastic, rigid_plastic, plastic_straw, plastic_spoon, multilayer_packaging, snack_sachet, coffee_sachet, refill_pouch, non_recyclable_foam, plastic_coated_paper, non_recyclable_dry_plastic, paper, cardboard, glass, metal, e_waste, hazardous, infectious, general, unknown. Set visible_condition to clean_dry only when visibly clean and dry, food_or_liquid_contaminated only for visible residue/liquid, otherwise unknown. Set has_food_scraps/has_liquid true only when visible. Resin code is "1"–"7" only when legible; otherwise "unknown".

PET beverage bottle rule: when the image shows a standard disposable beverage bottle and its visible packaging/body supports a plastic beverage bottle (for example a molded bottle with a beverage product label), set waste_category="pet_bottle" and material_subtype="pet_bottle". Do not label that same beverage bottle as plastic/rigid_plastic. A readable resin mark is helpful but not required when the image otherwise supports a disposable plastic beverage bottle. Reserve plastic/rigid_plastic for rigid plastic containers that are not beverage bottles (such as tubs or other packaging); do not convert every rigid_plastic item to PET. Do not call a glass bottle, metal bottle, tumbler, or reusable/insulated container PET based on shape. If the image does not distinguish plastic beverage packaging from glass, metal, or a reusable container, use the evidence-supported material or unknown; do not infer PET from the object name alone. Never infer PETE/resin code from shape, color, opacity, or transparency. Keep stretch_plastic for visibly flexible bags/film only. For a clearly identifiable smartphone, name it exactly "โทรศัพท์มือถือ", set waste_category to "ewaste", and set material_subtype to "e_waste". A front-facing smartphone with its screen visible is phone-specific evidence. A rear-facing smartphone in a black case remains a smartphone when its camera module or overall phone form is identifiable. A generic black rectangle or plastic container with no phone-specific feature is not enough; keep category/subtype "unknown". Do not infer ewaste from rectangular shape, black color.

Return compact JSON only: one complete JSON object on one line, with no prose, markdown, explanations, analysis, or reasoning in content. Required top-level keys: object_name_th, waste_category, material_subtype, visible_object_detected (boolean), classification_reason, resin_identification_code, visible_condition, confidence (number 0–1), top_candidates, has_food_scraps (boolean), has_liquid (boolean). top_candidates must contain exactly 3 complete valid objects, each with non-empty string object_name_th, valid waste_category, valid material_subtype, and numeric confidence 0–1. Candidate 1 must match the primary classification; rank all 3 in non-increasing confidence order. Do not omit, truncate, or add text around the JSON."""


class ModelResponseError(ValueError):
    """Raised when the upstream response does not follow LiteLLM chat completions."""


RESIN_IDENTIFICATION_CODES = frozenset({"1", "2", "3", "4", "5", "6", "7", "unknown"})


def _normalize_resin_identification_code(value: object) -> str:
    """Accept only the documented string values; omitted/invalid values are unknown."""
    if isinstance(value, str) and value.strip() in RESIN_IDENTIFICATION_CODES:
        return value.strip()
    return "unknown"


def _is_beverage_bottle_name(value: object) -> bool:
    name = normalize_object_name_th(value)
    return any(token in name for token in (
        "ขวดน้ำ", "ขวดเครื่องดื่ม", "ขวดน้ำดื่ม", "ขวดน้ำอัดลม",
        "ขวดชา", "ขวดกาแฟ", "beverage bottle", "water bottle",
    ))


def _is_non_pet_bottle_name(value: object) -> bool:
    name = normalize_object_name_th(value)
    return any(token in name for token in (
        "ขวดแก้ว", "ขวดโลหะ", "ขวดสแตนเลส", "กระบอกน้ำ", "แก้ว",
        "แก้วเก็บอุณหภูมิ", "แก้วน้ำ", "glass bottle", "metal bottle",
        "stainless bottle", "reusable bottle", "insulated bottle", "tumbler", "thermos",
    ))


def _chat_completions_url(api_url: str = API_URL) -> str:
    """Accept either the LiteLLM v1 base URL or its full chat-completions URL."""
    parts = urlsplit(api_url)
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _ollama_chat_url(base_url: str = OLLAMA_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/api/chat"


app = FastAPI(title="EcoCAICle API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_prediction_requests(request: Request, call_next):
    if request.method != "POST" or request.url.path != "/predict":
        return await call_next(request)

    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    scan_cycle_id = request.headers.get("x-scan-cycle-id", "unknown")
    reset_cycle_id = request.headers.get("x-reset-cycle-id", scan_cycle_id)
    started_at = time.perf_counter()
    LOGGER.info(
        "POST /predict ingress request_id=%s reset_cycle_id=%s content_length=%s",
        request_id,
        reset_cycle_id,
        request.headers.get("content-length"),
    )
    _trace_scan_event(
        "FastAPI prediction request received",
        scan_cycle_id,
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        resetCycleId=reset_cycle_id,
        contentLength=request.headers.get("content-length"),
        qwenTimeoutSeconds=QWEN_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        response = await call_next(request)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ModelResponseError) as exc:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
        LOGGER.exception(
            "POST /predict failed request_id=%s http_status=500 elapsed_ms=%s",
            request_id,
            elapsed_ms,
        )
        _trace_scan_event(
            "FastAPI prediction request failed",
            scan_cycle_id,
            request_id=request_id,
            name=type(exc).__name__,
            message=str(exc),
            resetCycleId=reset_cycle_id,
            elapsedMs=elapsed_ms,
        )
        raise
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
    response.headers["X-Request-ID"] = request_id
    LOGGER.info(
        "POST /predict completed request_id=%s http_status=%s elapsed_ms=%s",
        request_id,
        response.status_code,
        elapsed_ms,
    )
    _trace_scan_event(
        "FastAPI prediction request completed",
        scan_cycle_id,
        request_id=request_id,
        status=response.status_code,
        resetCycleId=reset_cycle_id,
        elapsedMs=elapsed_ms,
    )
    return response


# Load and validate the location mapping at startup, then use the immutable
# configuration for /predict resolution. A process restart is intentionally
# required after a JSON configuration change; there is no watcher or hot-reload
# path.
MAPPING_LOCATION_ID = os.environ.get("ECOCAICLE_LOCATION_ID", "ICT-001")
MAPPING_CONFIG_DIRECTORY = os.environ.get("ECOCAICLE_MAPPING_CONFIG_DIR")
MAPPING_CONFIG = None
MAPPING_SERVICE = None
REVIEW_SERVICE = None
SCAN_HISTORY_SERVICE = None


def _default_storage() -> InferenceStorage:
    return InferenceStorage(StorageConfig(root=Path(DEFAULT_STORAGE_ROOT)))


try:
    PHASE4_STORAGE = InferenceStorage(StorageConfig.from_environment())
except StorageConfigurationError as exc:
    # Keep imports available for local tooling; startup revalidates the
    # configured root and fails before serving requests if it is unsafe.
    LOGGER.warning(
        "Phase 4 storage configuration unavailable error_type=%s; temporary import fallback selected",
        type(exc).__name__,
    )
    PHASE4_STORAGE = _default_storage()


@app.on_event("startup")
async def load_mapping_foundation() -> None:
    global MAPPING_CONFIG, MAPPING_SERVICE, PHASE4_STORAGE, REVIEW_SERVICE, SCAN_HISTORY_SERVICE
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
        # Do not serve prediction traffic unless its configured storage root
        # is available. Each scan requires a persisted image and inference row.
        PHASE4_STORAGE = InferenceStorage(StorageConfig.from_environment())
        PHASE4_STORAGE.initialize()
        LOGGER.info(
            "Phase 4 storage initialized root=%s retention_days=%s",
            PHASE4_STORAGE.config.root,
            PHASE4_STORAGE.config.retention_days,
        )
    except (StorageConfigurationError, StorageFailure):
        LOGGER.exception("Phase 4 storage is unavailable; prediction service cannot start")
        raise
    REVIEW_SERVICE = ReviewService(PHASE4_STORAGE, MAPPING_CONFIG)
    SCAN_HISTORY_SERVICE = ScanHistoryService(PHASE4_STORAGE)


def _one_line(value: object) -> str:
    return " ".join(str(value).split()).lower()


def _safe_model_item_name(value: object, waste_type: str) -> str:
    item_name = " ".join(value.split())[:120] if isinstance(value, str) else ""
    if _configured_object_name_override(item_name):
        return item_name
    if not item_name or not all(
        char.isspace() or "\u0e01" <= char <= "\u0e3a" or "\u0e40" <= char <= "\u0e4e"
        for char in item_name
    ) or not any("\u0e01" <= char <= "\u0e2e" for char in item_name):
        return DEFAULT_ITEM_NAMES[waste_type]
    return item_name


def _candidate_confidence(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= 1:
        return None
    return float(value)


def _normalize_configured_model_category(category: object, object_name_th: object) -> str:
    normalized_category = normalize_waste_category(category)
    override = _configured_object_name_override(object_name_th)
    return str(override["waste_category"]) if override else normalized_category


def _configured_object_name_override(object_name_th: object) -> Optional[object]:
    overrides = MAPPING_CONFIG.object_name_overrides if MAPPING_CONFIG else ()
    return configured_object_name_override(object_name_th, overrides)


def _normalize_top_candidates(
    raw_candidates: object,
    *,
    primary_name: str,
    primary_category: str,
    primary_subtype: str,
    primary_confidence: float,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    provided_count = len(raw_candidates) if isinstance(raw_candidates, list) else 0
    indexed_candidates: list[tuple[int, dict[str, object]]] = []
    if isinstance(raw_candidates, list):
        for index, candidate in enumerate(raw_candidates[:3], start=1):
            if not isinstance(candidate, dict):
                LOGGER.warning(
                    "top_candidates_candidate_dropped candidate_index=%d reason=not_object field=top_candidates",
                    index,
                )
                continue
            raw_name = candidate.get("object_name_th") or candidate.get("itemNameTh")
            raw_category = candidate.get("waste_category")
            category = _normalize_configured_model_category(raw_category, raw_name)
            subtype = normalize_material_subtype(candidate.get("material_subtype"))
            if override := _configured_object_name_override(raw_name):
                subtype = str(override["material_subtype"])
            if category == "pet_bottle" and subtype == "rigid_plastic":
                category = "plastic"
            score = _candidate_confidence(candidate.get("confidence"))
            if score is None:
                LOGGER.warning(
                    "top_candidates_candidate_dropped candidate_index=%d reason=invalid_value field=confidence",
                    index,
                )
                continue
            normalized = {
                "object_name_th": _safe_model_item_name(raw_name, category),
                "waste_category": category,
                "material_subtype": subtype,
                "confidence": score,
            }
            candidates.append(normalized)
            indexed_candidates.append((index, normalized))
    primary = {
        "object_name_th": primary_name,
        "waste_category": primary_category,
        "material_subtype": primary_subtype,
        "confidence": primary_confidence,
    }
    if not candidates:
        candidates = [primary.copy()]
    else:
        first = candidates[0]
        if any(first.get(key) != primary[key] for key in primary):
            LOGGER.warning(
                "top_candidates_primary_normalized diagnostic=top_candidates_primary_mismatch"
            )
        candidates[0] = primary.copy()

    if provided_count != 3:
        LOGGER.warning(
            "top_candidates_count_invalid diagnostic=top_candidates_count_invalid provided_count=%d",
            provided_count,
        )

    # Candidate 1 is fixed to the primary prediction. Keep only alternatives
    # that can follow it in non-increasing confidence order; do not fabricate
    # alternatives when the model omits them or supplies invalid scores.
    alternatives = sorted(indexed_candidates[1:], key=lambda entry: float(entry[1]["confidence"]), reverse=True)
    ordered = [primary.copy()]
    for candidate_index, alternative in alternatives:
        if float(alternative["confidence"]) <= float(primary_confidence):
            ordered.append(alternative)
        else:
            LOGGER.warning(
                "top_candidates_candidate_dropped candidate_index=%d reason=confidence_exceeds_primary field=confidence",
                candidate_index,
            )
    ordered = ordered[:3]
    if len(ordered) != 3:
        LOGGER.warning(
            "top_candidates_incomplete diagnostic=top_candidates_incomplete provided_count=%d normalized_count=%d",
            provided_count,
            len(ordered),
        )
    return ordered


def _glass_pet_review_flags(
    top_candidates: list[dict[str, object]],
    *,
    waste_type: str,
    item_name_th: str,
    confidence: float,
) -> dict[str, object]:
    scores: dict[str, float] = {}
    evidence = [*top_candidates, {
        "waste_category": waste_type,
        "object_name_th": item_name_th,
        "confidence": confidence,
    }]
    for candidate in evidence:
        category = candidate.get("waste_category")
        score = candidate.get("confidence")
        if category in {"glass", "pet_bottle"} and isinstance(score, (int, float)):
            scores[category] = max(scores.get(category, 0.0), float(score))

    both_candidates_present = "glass" in scores and "pet_bottle" in scores
    confidence_gap = abs(scores["glass"] - scores["pet_bottle"]) if both_candidates_present else None
    confidence_close = (
        confidence_gap is not None and confidence_gap <= GLASS_PET_REVIEW_CONFIDENCE_GAP
    )
    return {
        "glass_pet_review_required": both_candidates_present or confidence_close,
        "glass_pet_candidates_present": both_candidates_present,
        "glass_pet_confidence_gap": round(confidence_gap, 4) if confidence_gap is not None else None,
        "glass_pet_confidence_close": confidence_close,
        "glass_pet_review_reason": (
            "both_candidates_close" if confidence_close else
            "both_candidates" if both_candidates_present else None
        ),
    }


def _parse_model_response(
    raw: object,
) -> tuple[str, str, str, str, str, bool, bool, float, object, list[dict[str, object]]]:
    """Parse the new contract while accepting the former class response temporarily."""
    text = str(raw).strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        response = json.loads(text)
    except json.JSONDecodeError:
        return UNKNOWN_WASTE_TYPE, DEFAULT_ITEM_NAMES[UNKNOWN_WASTE_TYPE], "unknown", "unknown", "unknown", False, False, 0.0, text[:120], []

    if not isinstance(response, dict):
        return UNKNOWN_WASTE_TYPE, DEFAULT_ITEM_NAMES[UNKNOWN_WASTE_TYPE], "unknown", "unknown", "unknown", False, False, 0.0, None, []

    raw_category = response.get("waste_category")
    if raw_category is None:
        raw_category = response.get("wasteType")
    raw_item_name = (
        response.get("itemNameTh")
        or response.get("item_name_th")
        or response.get("object_name_th")
        or response.get("objectNameTh")
    )
    waste_type = _normalize_configured_model_category(raw_category, raw_item_name)
    item_name = _safe_model_item_name(raw_item_name, waste_type)
    material_subtype = normalize_material_subtype(response.get("material_subtype"))
    if override := _configured_object_name_override(raw_item_name):
        material_subtype = str(override["material_subtype"])
    resin_identification_code = _normalize_resin_identification_code(
        response.get("resin_identification_code")
    )
    non_pet_name = _is_non_pet_bottle_name(raw_item_name)
    if waste_type == "pet_bottle" and (
        material_subtype in {"glass", "metal"} or non_pet_name
    ):
        if material_subtype in {"glass", "metal"}:
            waste_type = material_subtype
        else:
            waste_type = "unknown"
            material_subtype = "unknown"
    elif (
        resin_identification_code == "1"
        and _is_beverage_bottle_name(raw_item_name)
        and not non_pet_name
        and waste_type not in {"glass", "metal"}
        and material_subtype not in {"glass", "metal"}
    ):
        waste_type = "pet_bottle"
        material_subtype = "pet_bottle"
    elif waste_type == "pet_bottle" and material_subtype == "rigid_plastic":
        # Keep an internally contradictory model label from presenting generic
        # rigid plastic as a PET bottle or selecting PET-specific mapping rules.
        waste_type = "plastic"
    if waste_type == "infectious" and _is_configured_infectious_name(item_name):
        material_subtype = "infectious"
    visible_condition = normalize_visible_condition(response.get("visible_condition"))
    if (
        response.get("visible_object_detected") is not False
        and normalize_object_name_th(item_name) in {
            normalize_object_name_th(NO_WASTE_OBJECT_NAME_TH),
            normalize_object_name_th(LEGACY_NO_WASTE_OBJECT_NAME_TH),
        }
    ):
        # A legacy no-waste sentinel without an explicit visibility signal is
        # not enough evidence to reject a frame as empty.
        item_name = VISIBLE_UNCLASSIFIED_OBJECT_NAME_TH

    confidence = response.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
    elif not 0 <= confidence <= 1:
        confidence = 0.0

    has_food_scraps = response.get("has_food_scraps") is True or response.get("hasFoodScraps") is True
    has_liquid = response.get("has_liquid") is True or response.get("hasLiquid") is True
    confidence = float(confidence)
    top_candidates = _normalize_top_candidates(
        response.get("top_candidates"),
        primary_name=item_name,
        primary_category=waste_type,
        primary_subtype=material_subtype,
        primary_confidence=confidence,
    )
    # Unknown is a fallback label, not a stronger material hypothesis than a
    # supported PET candidate for an explicitly named beverage bottle. Promote
    # only that candidate, retain its own confidence, and leave ambiguous or
    # explicitly glass/metal candidates untouched.
    if (
        waste_type == "unknown"
        and material_subtype == "unknown"
        and _is_beverage_bottle_name(item_name)
        and not _is_non_pet_bottle_name(item_name)
        and not any(candidate["waste_category"] in {"glass", "metal"} for candidate in top_candidates)
    ):
        pet_candidate = next((
            candidate for candidate in top_candidates
            if candidate["waste_category"] == "pet_bottle"
            and candidate["material_subtype"] == "pet_bottle"
        ), None)
        if pet_candidate is not None:
            waste_type = "pet_bottle"
            material_subtype = "pet_bottle"
            confidence = float(pet_candidate["confidence"])
            promoted_primary = {
                "object_name_th": item_name,
                "waste_category": waste_type,
                "material_subtype": material_subtype,
                "confidence": confidence,
            }
            promoted_alternatives = []
            for candidate_index, candidate in enumerate(top_candidates, start=1):
                if candidate is pet_candidate:
                    continue
                if float(candidate["confidence"]) <= confidence:
                    promoted_alternatives.append(candidate)
                else:
                    LOGGER.warning(
                        "top_candidates_candidate_dropped candidate_index=%d reason=confidence_exceeds_promoted_primary field=confidence",
                        candidate_index,
                    )
            top_candidates = [promoted_primary, *promoted_alternatives][:3]
            if len(top_candidates) != 3:
                LOGGER.warning(
                    "top_candidates_incomplete diagnostic=top_candidates_incomplete provided_count=%d normalized_count=%d",
                    len(response.get("top_candidates", ())) if isinstance(response.get("top_candidates"), list) else 0,
                    len(top_candidates),
                )
    if resin_identification_code == "1" and _is_beverage_bottle_name(raw_item_name) and not non_pet_name:
        for candidate in top_candidates:
            candidate_name = normalize_object_name_th(candidate.get("object_name_th"))
            if candidate_name == normalize_object_name_th(item_name):
                candidate["waste_category"] = "pet_bottle"
                candidate["material_subtype"] = "pet_bottle"
                break
    return (
        waste_type,
        item_name,
        material_subtype,
        resin_identification_code,
        visible_condition,
        has_food_scraps,
        has_liquid,
        confidence,
        raw_category,
        top_candidates,
    )


def _parse_validated_model_response(raw: str) -> tuple[str, str, str, str, str, bool, bool, float, object, list[dict[str, object]]]:
    parsed = _parse_model_response(raw)
    normalized_count = len(parsed[-1])
    if normalized_count != 3:
        raise ModelResponseError(
            f"Normalized top_candidates must contain exactly 3 items; got {normalized_count}"
        )
    return parsed


NO_WASTE_RETRY_MESSAGE_TH = "ไม่พบวัตถุสำหรับคัดแยกในภาพ"
NO_WASTE_OBJECT_NAME_TH = "ไม่พบวัตถุสำหรับคัดแยกในภาพ"
VISIBLE_UNCLASSIFIED_OBJECT_NAME_TH = "วัตถุที่ยังระบุประเภทขยะไม่ได้"
LEGACY_NO_WASTE_OBJECT_NAME_TH = "ไม่พบขยะในภาพ"


def _is_no_waste_prediction(raw: object) -> bool:
    """Recognize explicit no-waste model output before any bin mapping occurs."""
    text = str(raw).strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        response = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(response, dict):
        return False
    category = str(response.get("waste_category", response.get("wasteType", ""))).strip().lower()
    object_name = normalize_object_name_th(
        response.get("object_name_th") or response.get("objectNameTh") or
        response.get("item_name_th") or response.get("itemNameTh") or ""
    )
    absent_object_names = {
        normalize_object_name_th(NO_WASTE_OBJECT_NAME_TH),
        normalize_object_name_th(LEGACY_NO_WASTE_OBJECT_NAME_TH),
    }
    # Legacy no-waste markers alone cannot distinguish an absent object from a
    # visible but unsupported object. Require an explicit visibility decision.
    if response.get("visible_object_detected") is not False:
        return False
    if object_name and object_name not in absent_object_names:
        return False
    candidates = response.get("top_candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_name = normalize_object_name_th(candidate.get("object_name_th"))
            candidate_category = str(candidate.get("waste_category", "unknown")).strip().lower()
            if candidate_name and candidate_name not in absent_object_names:
                return False
            if candidate_category not in {"", "unknown"}:
                return False
    return category == "unknown"


def _no_waste_response(*, request_id: str, stored_image: Optional[StoredImage],
                       timestamp: str, request_started_at: float,
                       inference_started_at: float, image_save_status: str,
                       storage_error_type: Optional[str]) -> dict[str, object]:
    """Build a true absent-object result that cannot imply a bin."""
    top_candidates = [
        {"object_name_th": NO_WASTE_OBJECT_NAME_TH, "waste_category": "unknown",
         "material_subtype": "unknown", "confidence": score}
        for score in (0.1, 0.05, 0.0)
    ]
    _safe_inference_record(
        request_id=request_id, timestamp=timestamp, request_started_at=request_started_at,
        inference_started_at=inference_started_at, stored_image=stored_image,
        image_save_status=image_save_status, storage_error_type=storage_error_type,
        object_name_th=NO_WASTE_OBJECT_NAME_TH, waste_category="unknown",
        material_subtype="unknown", resin_identification_code="unknown",
        visible_condition="unknown", confidence=0.1, has_food_scraps=False,
        has_liquid=False, bin_id=None, bin_number=None, bin_name=None,
        mapping_version=MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
        classification_status="invalid_capture", mapping_fallback=False,
        status="no_waste_detected", error_type=None, top_candidates=top_candidates,
        visible_object_detected=False, classification_reason="no_object_visible",
        mapping_reason="no_object_visible",
    )
    return {
        "request_id": request_id, "prompt_version": PROMPT_VERSION,
        "location_id": MAPPING_LOCATION_ID,
        "prediction": {"object_name_th": NO_WASTE_OBJECT_NAME_TH,
            "waste_category": "unknown", "material_subtype": "unknown",
            "visible_object_detected": False,
            "classification_reason": "no_object_visible",
            "resin_identification_code": "unknown", "visible_condition": "unknown",
            "confidence": 0.1, "top_candidates": top_candidates,
            "has_food_scraps": False, "has_liquid": False,
            "wasteType": "unknown", "itemNameTh": NO_WASTE_OBJECT_NAME_TH,
            "prompt_version": PROMPT_VERSION},
        "object_name_th": NO_WASTE_OBJECT_NAME_TH, "waste_category": "unknown",
        "material_subtype": "unknown", "confidence": 0.1,
        "top_candidates": top_candidates, "bin_id": None, "bin_number": None,
        "bin_name": None, "mappedBin": None, "mapping_fallback": False,
        "mapping_reason": "no_waste_detected", "mapping_version": MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
        "classification_status": "invalid_capture", "status": "no_waste_detected",
        "no_waste_detected": True, "valid_prediction": False,
        "classification_reason": "no_object_visible",
        "visible_object_detected": False,
        "retry_message": NO_WASTE_RETRY_MESSAGE_TH,
        "preparation": _no_preparation("no_waste_detected"),
    }


def _post_json(payload: dict, provider: Optional[str] = None) -> dict:
    selected_provider = (provider or QWEN_PROVIDER).strip().lower()
    if selected_provider not in {"litellm", "ollama_native"}:
        raise ModelResponseError(f"Unsupported QWEN_PROVIDER={selected_provider}")
    request_payload = payload
    endpoint = _chat_completions_url() if selected_provider == "litellm" else _ollama_chat_url()
    headers = {"Content-Type": "application/json"}
    if selected_provider == "litellm" and API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(
        request,
        timeout=60 if selected_provider == "ollama_native" else QWEN_REQUEST_TIMEOUT_SECONDS,
        context=SSL_CONTEXT,
    ) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ModelResponseError("Expected a JSON object from model provider")
    return result


def _build_model_request_payload(
    image_data_url: str,
    provider: Optional[str] = None,
) -> dict[str, object]:
    return build_model_request_payload(
        model=MODEL,
        prompt=PROMPT,
        image_data_url=image_data_url,
        provider=provider or QWEN_PROVIDER,
    )


def _extract_chat_content(result: dict) -> str:
    """Read final content from either LiteLLM or native Ollama chat."""
    if isinstance(result.get("message"), dict):
        content = result["message"].get("content")
        if isinstance(content, str) and content.strip():
            return content
        raise ModelResponseError("Empty message.content")
    try:
        choices = result["choices"]
        first_choice = choices[0]
        message = first_choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelResponseError("Missing choices[0].message.content") from exc

    if isinstance(content, str):
        if content.strip():
            return content
        raise ModelResponseError("Empty choices[0].message.content")
    if isinstance(content, list):
        text_parts = [part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
        if text_parts:
            text = "\n".join(text_parts)
            if text.strip():
                return text
            raise ModelResponseError("Empty choices[0].message.content")
    raise ModelResponseError("Expected text content in choices[0].message.content")


def _validate_model_content(content: str) -> dict[str, object]:
    """Require a complete JSON classification and exactly three candidates."""
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ModelResponseError("Model content is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ModelResponseError("Model content must be a JSON object")
    required = {
        "object_name_th", "waste_category", "material_subtype",
        "visible_object_detected", "classification_reason",
        "resin_identification_code", "visible_condition", "confidence",
        "top_candidates", "has_food_scraps", "has_liquid",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ModelResponseError(f"Model JSON is missing required fields: {','.join(missing)}")
    candidates = payload.get("top_candidates")
    if not isinstance(candidates, list) or len(candidates) != 3:
        raise ModelResponseError("Model JSON must contain exactly 3 top_candidates")
    candidate_fields = {"object_name_th", "waste_category", "material_subtype", "confidence"}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or not candidate_fields.issubset(candidate):
            raise ModelResponseError(f"Model top_candidate_{index + 1} is incomplete")
        if not isinstance(candidate["object_name_th"], str) or not candidate["object_name_th"].strip():
            raise ModelResponseError(f"Model top_candidate_{index + 1} invalid field=object_name_th")
        if not isinstance(candidate["waste_category"], str) or candidate["waste_category"] not in WASTE_TYPES:
            raise ModelResponseError(f"Model top_candidate_{index + 1} invalid field=waste_category")
        if not isinstance(candidate["material_subtype"], str) or candidate["material_subtype"] not in SUPPORTED_MATERIAL_SUBTYPES:
            raise ModelResponseError(f"Model top_candidate_{index + 1} invalid field=material_subtype")
        candidate_confidence = candidate["confidence"]
        if isinstance(candidate_confidence, bool) or not isinstance(candidate_confidence, (int, float)) or not 0 <= candidate_confidence <= 1:
            raise ModelResponseError(f"Model top_candidate_{index + 1} invalid field=confidence")
    if not isinstance(payload["object_name_th"], str) or not payload["object_name_th"].strip():
        raise ModelResponseError("Model object_name_th is empty")
    if not isinstance(payload["waste_category"], str) or not isinstance(payload["material_subtype"], str):
        raise ModelResponseError("Model classification fields are invalid")
    if not all(isinstance(payload[field], str) for field in ("resin_identification_code", "visible_condition")):
        raise ModelResponseError("Model material evidence fields are invalid")
    if not isinstance(payload["classification_reason"], str) or not isinstance(payload["visible_object_detected"], bool):
        raise ModelResponseError("Model visibility fields are invalid")
    if not isinstance(payload["has_food_scraps"], bool) or not isinstance(payload["has_liquid"], bool):
        raise ModelResponseError("Model condition fields are invalid")
    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ModelResponseError("Model confidence is invalid")
    return payload


def _extract_complete_model_content(result: dict) -> str:
    if isinstance(result.get("message"), dict):
        if result.get("done_reason") in {"length", "max_tokens"}:
            raise ModelResponseError(f"Model response done_reason={result.get('done_reason')}")
        content = _extract_chat_content(result)
        _validate_model_content(content)
        return content
    choices = result.get("choices")
    first_choice = choices[0] if isinstance(choices, list) and choices else {}
    finish_reason = first_choice.get("finish_reason") if isinstance(first_choice, dict) else None
    if finish_reason == "length":
        raise ModelResponseError("Model response finish_reason=length")
    content = _extract_chat_content(result)
    _validate_model_content(content)
    return content


def classify_image_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    provider: Optional[str] = None,
) -> dict[str, object]:
    """Shared image classification path used by /predict and golden evaluation."""
    selected_provider = (provider or QWEN_PROVIDER).strip().lower()
    image_data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    payload = _build_model_request_payload(image_data_url, selected_provider)
    result = _post_json(payload, selected_provider)
    try:
        content = _extract_complete_model_content(result)
        parsed = _parse_validated_model_response(content)
        parse_error = None
    except ModelResponseError as exc:
        parsed = None
        parse_error = str(exc)
        if isinstance(result.get("message"), dict):
            content = result["message"].get("content", "")
        else:
            try:
                content = result["choices"][0]["message"].get("content", "")
            except (KeyError, IndexError, TypeError):
                content = ""
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str):
            content = ""
    message = result.get("message") if isinstance(result.get("message"), dict) else {}
    choices = result.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    completion_tokens = usage.get("completion_tokens")
    if completion_tokens is None:
        completion_tokens = result.get("eval_count")
    thinking = message.get("thinking", message.get("reasoning_content", ""))
    return {
        "raw_prediction": content,
        "parsed_model_response": parsed,
        "parse_error": parse_error,
        "result": result,
        "finish_reason": result.get("done_reason") or choice.get("finish_reason"),
        "completion_tokens": completion_tokens,
        "content_length": len(content),
        "reasoning_length": len(thinking) if isinstance(thinking, str) else 0,
        "category": parsed[0] if parsed else None,
        "confidence": parsed[7] if parsed else None,
    }


def _model_api_error(exc: Exception) -> dict[str, object]:
    """Build a safe, useful error without returning the request or image data."""
    active_url = OLLAMA_BASE_URL if QWEN_PROVIDER == "ollama_native" else API_URL
    host = urlparse(active_url).hostname or "configured model host"

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return {
            "error": "Model API request timed out",
            "type": "TimeoutError",
            "message": f"Qwen request exceeded the {QWEN_REQUEST_TIMEOUT_SECONDS}-second upstream timeout.",
        }

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
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            message = f"Qwen request exceeded the {QWEN_REQUEST_TIMEOUT_SECONDS}-second upstream timeout."
        else:
            message = str(reason)
        return {"error": "Model API is unreachable", "type": type(reason).__name__, "message": message}

    if isinstance(exc, json.JSONDecodeError):
        return {"error": "Model API returned invalid JSON", "type": type(exc).__name__}

    if isinstance(exc, ModelResponseError):
        return {"error": "Model API returned an unexpected response schema", "type": type(exc).__name__}

    return {"error": "Model API request failed", "type": type(exc).__name__, "message": str(exc)}


def _is_model_timeout(exc: Exception) -> bool:
    """Recognize direct and urllib-wrapped timeouts for status selection."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason,
        (TimeoutError, socket.timeout),
    )


def _mapping_api_status(classification_status: str) -> str:
    if classification_status == "confirmed":
        return "success"
    if classification_status == "unavailable":
        return "missing_mapping"
    return "unknown"


def _classification_reason(waste_category: str) -> Optional[str]:
    if waste_category == "unknown":
        return "visible_object_unclassified"
    return None


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
        "has_food_scraps": None,
        "hasFoodScraps": None,
        "has_liquid": None,
        "hasLiquid": None,
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
    resin_identification_code: object = "unknown",
) -> JSONResponse:
    """Return a structured error without turning transport failures into unknown waste."""

    content = {
        "request_id": request_id,
        "prompt_version": PROMPT_VERSION,
        "location_id": MAPPING_LOCATION_ID,
        "object_name_th": None,
        "waste_category": None,
        "material_subtype": None,
        "resin_identification_code": _normalize_resin_identification_code(resin_identification_code),
        "visible_condition": "unknown",
        "confidence": None,
        "bin_id": None,
        "bin_number": None,
        "bin_name": None,
        "mappedBin": None,
        "mapping_version": MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
        "classification_status": "api_error",
        "classificationStatus": "api_error",
        "mapping_fallback": False,
        "mapping_reason": "mapping_not_resolved",
        "top_candidates": [],
        "review_flags": {
            "glass_pet_review_required": False,
            "glass_pet_candidates_present": False,
            "glass_pet_confidence_gap": None,
            "glass_pet_confidence_close": False,
            "glass_pet_review_reason": None,
        },
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
    material_subtype: Optional[str] = None,
    resin_identification_code: Optional[str] = None,
    visible_condition: Optional[str] = None,
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
    top_candidates: Optional[list[dict[str, object]]] = None,
    review_flags: Optional[dict[str, object]] = None,
    has_liquid: Optional[bool] = None,
    visible_object_detected: Optional[bool] = None,
    classification_reason: Optional[str] = None,
    object_name_override_applied: bool = False,
    mapping_reason: Optional[str] = None,
    mapping_rule_name: Optional[str] = None,
    confidence_threshold_used: Optional[float] = None,
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
        "normalized_waste_category": waste_category,
        "material_subtype": material_subtype or "unknown",
        "resin_identification_code": _normalize_resin_identification_code(resin_identification_code),
        "visible_condition": visible_condition or "unknown",
        "confidence": confidence,
        "has_food_scraps": has_food_scraps,
        "has_liquid": has_liquid,
        "top_candidates": top_candidates or [],
        "review_flags": review_flags or {
            "glass_pet_review_required": False,
            "glass_pet_candidates_present": False,
            "glass_pet_confidence_gap": None,
            "glass_pet_confidence_close": False,
            "glass_pet_review_reason": None,
        },
        "bin_id": bin_id,
        "bin_number": bin_number,
        "bin_name": bin_name,
        "mapping_version": mapping_version,
        "classification_status": classification_status,
        "visible_object_detected": visible_object_detected,
        "classification_reason": classification_reason,
        "object_name_override_applied": object_name_override_applied,
        "mapping_fallback": mapping_fallback,
        "mapping_reason": mapping_reason or ("mapping_not_resolved" if status == "api_error" else None),
        "mapping_rule_name": mapping_rule_name,
        "confidence_threshold_used": confidence_threshold_used,
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
        raise HTTPException(
            status_code=503,
            detail={
                "status": "api_error",
                "error": "inference_record_storage_unavailable",
                "request_id": request_id,
            },
        ) from exc


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


def _require_local_scan_history(request: Request) -> None:
    if os.environ.get("ECOAICLE_SCAN_HISTORY_ENABLED") != "1":
        raise HTTPException(status_code=404, detail={"error": "scan_history_unavailable"})
    host = request.client.host if request.client else ""
    allowed_hosts = {
        value.strip()
        for value in os.environ.get("ECOAICLE_SCAN_HISTORY_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    }
    local = False
    try:
        address = ipaddress.ip_address(host)
        local = address.is_loopback or bool(address.ipv4_mapped and address.ipv4_mapped.is_loopback)
    except ValueError:
        local = host.lower() == "localhost"
    if not local and host not in allowed_hosts:
        raise HTTPException(status_code=403, detail={"error": "scan_history_local_only"})


def _scan_history_service_or_error() -> ScanHistoryService:
    if SCAN_HISTORY_SERVICE is None:
        raise HTTPException(status_code=503, detail={"error": "scan_history_unavailable"})
    return SCAN_HISTORY_SERVICE


@app.get("/scan-history")
async def scan_history(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
    waste_category: Optional[str] = None,
    bin_number: Optional[str] = None,
    needs_glass_pet_review: bool = False,
) -> dict[str, object]:
    _require_local_scan_history(request)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=400, detail={"error": "invalid_limit"})
    try:
        return _scan_history_service_or_error().list_scans(
            limit=limit,
            offset=offset,
            status=status,
            waste_category=waste_category,
            bin_number=bin_number,
            needs_glass_pet_review=needs_glass_pet_review,
        )
    except ScanHistoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail={"error": "scan_history_unavailable"}) from exc


@app.get("/scan-history/images/{request_id}")
async def scan_history_image(request: Request, request_id: str) -> Response:
    _require_local_scan_history(request)
    try:
        image_path = _scan_history_service_or_error().image_path(request_id)
        content = image_path.read_bytes()
    except ScanNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "scan_image_not_found"}) from exc
    except ScanHistoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail={"error": "scan_history_unavailable"}) from exc
    except OSError as exc:
        raise HTTPException(status_code=404, detail={"error": "scan_image_not_found"}) from exc
    media_type = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _poc_sources() -> list[tuple[str, Path]]:
    """Read-only storage roots for the retrospective POC subset."""

    configured = PHASE4_STORAGE.config.root
    sources = [("configured_storage", configured)]
    legacy = Path(DEFAULT_STORAGE_ROOT)
    if legacy.is_dir() and legacy.resolve() != configured.resolve():
        sources.append(("legacy_storage", legacy))
    return sources


def _poc_dataset() -> dict[str, object]:
    if MAPPING_CONFIG is None:
        raise HTTPException(status_code=503, detail={"error": "scan_history_unavailable"})
    try:
        return build_poc_dataset(_poc_sources(), MAPPING_CONFIG)
    except OSError as exc:
        raise HTTPException(status_code=503, detail={"error": "scan_history_unavailable"}) from exc


@app.get("/scan-history/poc")
async def scan_history_poc(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    ground_truth_source: Optional[str] = None,
    waste_category: Optional[str] = None,
    bin_number: Optional[str] = None,
) -> dict[str, object]:
    """Demo-only retrospective subset. Metrics cover human_verified rows only."""

    _require_local_scan_history(request)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=400, detail={"error": "invalid_limit"})
    if ground_truth_source is not None and ground_truth_source not in GROUND_TRUTH_SOURCES:
        raise HTTPException(status_code=400, detail={"error": "invalid_ground_truth_source"})
    dataset = _poc_dataset()
    rows = [
        row for row in dataset["rows"]
        if (ground_truth_source is None or row["ground_truth_source"] == ground_truth_source)
        and (waste_category is None or row["historical_prediction"]["waste_category"] == waste_category)
        and (bin_number is None or str(row["historical_mapping"]["bin_number"]) == bin_number)
    ]
    page = rows[offset : offset + limit]
    summary = {key: value for key, value in dataset["summary"].items() if key != "source_fingerprints"}
    return {
        "items": [public_row(row) for row in page],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < len(rows),
        "summary": summary,
    }


@app.get("/scan-history/poc/images/{request_id}")
async def scan_history_poc_image(request: Request, request_id: str) -> Response:
    _require_local_scan_history(request)
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise HTTPException(status_code=404, detail={"error": "scan_image_not_found"})
    dataset = _poc_dataset()
    row = next((item for item in dataset["rows"] if item["request_id"] == request_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "scan_image_not_found"})
    try:
        content = row["_image_path"].read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=404, detail={"error": "scan_image_not_found"}) from exc
    media_type = mimetypes.guess_type(str(row["_image_path"]))[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL}


@app.post("/predict")
async def predict(request: Request, file: UploadFile = File(...)) -> object:
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    request_headers = request.headers if request is not None else {}
    reset_cycle_id = request_headers.get(
        "x-reset-cycle-id",
        request_headers.get("x-scan-cycle-id", "unknown"),
    )
    request_started_at = time.perf_counter()
    timestamp = _utc_timestamp()
    stored_image: Optional[StoredImage] = None
    image_save_status = "failed"
    storage_error_type: Optional[str] = None

    LOGGER.info(
        "Image upload received request_id=%s filename=%s content_type=%s",
        request_id,
        file.filename,
        file.content_type,
    )
    image_bytes = await file.read(PHASE4_STORAGE.config.max_image_bytes + 1)
    try:
        image_metadata = validate_image_bytes(
            image_bytes,
            declared_content_type=file.content_type,
            filename=file.filename,
            max_image_bytes=PHASE4_STORAGE.config.max_image_bytes,
        )
    except ImageValidationError as exc:
        LOGGER.warning(
            "Image validation failed request_id=%s http_status=%s error_type=%s",
            request_id,
            exc.status_code,
            type(exc).__name__,
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    LOGGER.info(
        "Image validated request_id=%s mime_type=%s byte_count=%s",
        request_id,
        image_metadata.mime_type,
        len(image_bytes),
    )

    # Persist the original bytes before inference so a model timeout, unknown
    # result, low-confidence result, or mapping failure remains reviewable.
    try:
        stored_image = PHASE4_STORAGE.save_image(request_id, image_bytes, image_metadata)
        image_save_status = "saved"
        LOGGER.info("Image stored request_id=%s image_id=%s", request_id, stored_image.image_id)
        _safe_ground_truth_seed(request_id, timestamp, stored_image)
    except Exception as exc:
        storage_error_type = type(exc).__name__
        _storage_diagnostic(request_id, "image_save", exc)
        _safe_inference_record(
            request_id=request_id,
            timestamp=timestamp,
            request_started_at=request_started_at,
            inference_started_at=None,
            stored_image=None,
            image_save_status=image_save_status,
            storage_error_type=storage_error_type,
            object_name_th=None,
            waste_category=None,
            confidence=None,
            has_food_scraps=None,
            has_liquid=None,
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
            classification_status="api_error",
            mapping_fallback=False,
            status="api_error",
            error_type=type(exc).__name__,
        )
        return _api_error_response(
            request_id,
            {
                "error": "The uploaded image could not be persisted",
                "type": type(exc).__name__,
            },
            status_code=503,
        )

    if MAPPING_SERVICE is None:
        LOGGER.error("Mapping unavailable request_id=%s", request_id)
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

    inference_started_at = time.perf_counter()
    finish_reason: Optional[str] = None
    content_length = 0
    reasoning_length = 0
    completion_tokens: Optional[int] = None
    raw_prediction = ""
    parsed_model_response: Optional[tuple[str, str, str, str, str, bool, bool, float, object, list[dict[str, object]]]] = None
    try:
        classification = await asyncio.to_thread(
            classify_image_bytes, image_bytes, image_metadata.mime_type,
        )
        raw_prediction = str(classification["raw_prediction"])
        parsed_model_response = classification["parsed_model_response"]
        finish_reason = str(classification["finish_reason"] or "")[:80] or None
        content_length = int(classification["content_length"])
        reasoning_length = int(classification["reasoning_length"])
        completion_tokens = classification["completion_tokens"] if isinstance(classification["completion_tokens"], int) else None
    except Exception as exc:
        detail = _model_api_error(exc)
        response_status = 504 if _is_model_timeout(exc) else 502
        error_reason = str(exc)[:180] if isinstance(exc, ModelResponseError) else type(exc).__name__
        LOGGER.warning(
            "Qwen request failed request_id=%s provider=%s model=%s timeout_seconds=%s completion_tokens=%s elapsed_ms=%s finish_reason=%s content_length=%s reasoning_length=%s category=null confidence=null json_validation_failure_reason=%s error_reason=%s http_status=%s fallback_flag=true fallback_reason=model_request_error metric_response_invalid_total=%s metric_low_confidence_total=%s",
            request_id,
            QWEN_PROVIDER,
            MODEL,
            QWEN_REQUEST_TIMEOUT_SECONDS,
            completion_tokens,
            _elapsed_ms(inference_started_at),
            finish_reason,
            content_length,
            reasoning_length,
            error_reason if isinstance(exc, ModelResponseError) else None,
            error_reason,
            response_status,
            FALLBACK_METRICS["response_invalid"],
            FALLBACK_METRICS["low_confidence"],
        )
        response = _api_error_response(request_id, detail, status_code=response_status)
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

    if parsed_model_response is None:
        reason = str(classification["parse_error"] or "Model response content is empty or invalid JSON")
        FALLBACK_METRICS["response_invalid"] += 1
        LOGGER.warning(
            "Qwen classification fallback request_id=%s provider=%s model=%s latency_ms=%s finish_reason=%s completion_tokens=%s reasoning_length=%s content_length=%s category=null confidence=null fallback_flag=true fallback_reason=response_empty_or_invalid_json metric_response_invalid_total=%s metric_low_confidence_total=%s reason=%s",
            request_id, QWEN_PROVIDER, MODEL, _elapsed_ms(inference_started_at),
            finish_reason, completion_tokens, reasoning_length, content_length,
            FALLBACK_METRICS["response_invalid"], FALLBACK_METRICS["low_confidence"], reason[:160],
        )
        _safe_inference_record(
            request_id=request_id, timestamp=timestamp, request_started_at=request_started_at,
            inference_started_at=inference_started_at, stored_image=stored_image,
            image_save_status=image_save_status, storage_error_type=storage_error_type,
            object_name_th=None, waste_category=None, confidence=None, has_food_scraps=None,
            has_liquid=None, bin_id=None, bin_number=None, bin_name=None,
            mapping_version=MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
            classification_status="needs_review", mapping_fallback=False,
            status="needs_review", error_type="ModelResponseError",
        )
        return JSONResponse(status_code=200, content={
            "ok": False, "category": None, "needs_review": True,
            "reason": "response_empty_or_invalid_json",
        })

    category = parsed_model_response[0]
    confidence = parsed_model_response[7]
    low_confidence = confidence < (MAPPING_CONFIG.confidence_threshold if MAPPING_CONFIG else 0)
    if low_confidence:
        FALLBACK_METRICS["low_confidence"] += 1
    LOGGER.info(
        "Qwen request completed request_id=%s provider=%s model=%s latency_ms=%s finish_reason=%s completion_tokens=%s reasoning_length=%s content_length=%s category=%s confidence=%s fallback_flag=%s fallback_reason=%s metric_response_invalid_total=%s metric_low_confidence_total=%s",
        request_id, QWEN_PROVIDER, MODEL, _elapsed_ms(inference_started_at), finish_reason,
        completion_tokens, reasoning_length, content_length, category, confidence,
        low_confidence, "low_confidence" if low_confidence else "none",
        FALLBACK_METRICS["response_invalid"], FALLBACK_METRICS["low_confidence"],
    )

    if _is_no_waste_prediction(raw_prediction):
        LOGGER.info("No waste object detected request_id=%s", request_id)
        return _no_waste_response(
            request_id=request_id, stored_image=stored_image, timestamp=timestamp,
            request_started_at=request_started_at, inference_started_at=inference_started_at,
            image_save_status=image_save_status, storage_error_type=storage_error_type,
        )

    assert parsed_model_response is not None
    (
        waste_type,
        item_name_th,
        material_subtype,
        resin_identification_code,
        visible_condition,
        has_food_scraps,
        has_liquid,
        confidence,
        raw_category,
        top_candidates,
    ) = parsed_model_response
    review_flags = _glass_pet_review_flags(
        top_candidates,
        waste_type=waste_type,
        item_name_th=item_name_th,
        confidence=confidence,
    )
    try:
        mapping_decision = MAPPING_SERVICE.resolve(
            location_id=MAPPING_LOCATION_ID,
            waste_category=waste_type,
            confidence=confidence,
            object_name_th=item_name_th,
            material_subtype=material_subtype,
            visible_condition=visible_condition,
            has_food_scraps=has_food_scraps,
            has_liquid=has_liquid,
        )
        preparation_decision = MAPPING_SERVICE.resolve_preparation(
            location_id=MAPPING_LOCATION_ID,
            waste_category=waste_type,
            has_food_scraps=has_food_scraps,
            has_liquid=has_liquid,
            visible_condition=visible_condition,
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
            resin_identification_code=resin_identification_code,
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
            material_subtype=material_subtype,
            resin_identification_code=resin_identification_code,
            visible_condition=visible_condition,
            confidence=confidence,
            has_food_scraps=has_food_scraps,
            has_liquid=has_liquid,
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=MAPPING_CONFIG.mapping_version if MAPPING_CONFIG else None,
            classification_status="api_error",
            mapping_fallback=False,
            status="api_error",
            error_type=type(exc).__name__,
            top_candidates=top_candidates,
            review_flags=review_flags,
        )
        return response

    status = _mapping_api_status(mapping_decision.classification_status)
    classification_reason = _classification_reason(waste_type)
    preparation = preparation_decision.to_dict()
    fallback_reason = {
        "unknown": "unknown-classification",
        "low_confidence": "low-confidence",
        "unavailable": "no-verified-mapping",
    }.get(mapping_decision.classification_status)
    if mapping_decision.mapping_reason:
        fallback_reason = None
    mapping_key_used = "general" if mapping_decision.mapping_fallback else waste_type
    _trace_scan_event(
        "Waste-to-bin mapping resolved",
        reset_cycle_id,
        request_id=request_id,
        resetCycleId=reset_cycle_id,
        rawModelCategory=raw_category,
        normalizedWasteType=waste_type,
        materialSubtype=material_subtype,
        visibleCondition=visible_condition,
        confidence=confidence,
        fallbackReason=fallback_reason,
        mappingReason=mapping_decision.mapping_reason,
        mappingKeyUsed=mapping_key_used,
        matchedBinId=mapping_decision.bin_id,
        matchedBinNumber=mapping_decision.bin_number,
        matchedBinName=mapping_decision.bin_name,
        mappingFallback=mapping_decision.mapping_fallback,
        mappingRuleName=mapping_decision.mapping_rule_name,
        confidenceThresholdUsed=mapping_decision.confidence_threshold_used,
        locationId=MAPPING_LOCATION_ID,
        mappingVersion=mapping_decision.mapping_version,
    )
    _trace_scan_event(
        "Food/liquid preparation resolved",
        request_id,
        request_id=request_id,
        required=preparation_decision.required,
        signalValue=preparation_decision.signal_value,
        containerBinId=(preparation_decision.container_disposal or {}).get("bin_id"),
        containerBinNumber=(preparation_decision.container_disposal or {}).get("bin_number"),
        mappedBinId=mapping_decision.bin_id,
        mappedBinNumber=mapping_decision.bin_number,
        locationId=MAPPING_LOCATION_ID,
        mappingVersion=mapping_decision.mapping_version,
    )
    mapped_waste_type = mapping_key_used or "general"
    LOGGER.info(
        "Mapping resolved request_id=%s classification_status=%s waste_category=%s material_subtype=%s bin_number=%s mapping_reason=%s",
        request_id,
        mapping_decision.classification_status,
        waste_type,
        material_subtype,
        mapping_decision.bin_number,
        mapping_decision.mapping_reason,
    )
    prediction_payload: dict[str, object] = {
        # Canonical fields for new clients.
        "waste_category": waste_type,
        "object_name_th": item_name_th,
        "material_subtype": material_subtype,
        "visible_object_detected": True,
        "classification_reason": classification_reason,
        "object_name_override_applied": mapping_decision.object_name_override_applied,
        "resin_identification_code": resin_identification_code,
        "visible_condition": visible_condition,
        "has_food_scraps": has_food_scraps,
        "has_liquid": has_liquid,
        "top_candidates": top_candidates,
        "prompt_version": PROMPT_VERSION,
        # Transitional aliases for the existing frontend contract.
        "wasteType": waste_type,
        "itemNameTh": item_name_th,
        "hasFoodScraps": has_food_scraps,
        "hasLiquid": has_liquid,
        "confidence": confidence,
    }

    response = {
        "prediction": prediction_payload,
        "top_candidates": top_candidates,
        "review_flags": review_flags,
        "valid_prediction": True,
        "disposal": {
            "hasFoodScraps": has_food_scraps,
            "has_food_scraps": has_food_scraps,
            "hasLiquid": has_liquid,
            "has_liquid": has_liquid,
            "preparation": preparation,
        },
        "preparation": preparation,
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "request_id": request_id,
        "location_id": MAPPING_LOCATION_ID,
        "object_name_th": item_name_th,
        "waste_category": waste_type,
        "material_subtype": material_subtype,
        "resin_identification_code": resin_identification_code,
        "visible_condition": visible_condition,
        "confidence": confidence,
        "has_food_scraps": has_food_scraps,
        "has_liquid": has_liquid,
        "bin_id": mapping_decision.bin_id,
        "bin_number": mapping_decision.bin_number,
        "bin_name": mapping_decision.bin_name,
        "mappedBin": {
            "id": mapping_decision.bin_id,
            "number": mapping_decision.bin_number,
            "nameTh": mapping_decision.bin_name,
            "wasteType": mapped_waste_type,
            "mappingVersion": mapping_decision.mapping_version,
            "fallback": mapping_decision.mapping_fallback,
        } if mapping_decision.bin_id and mapping_decision.bin_number and mapping_decision.bin_name else None,
        "mappingDecision": {
            "rawCategory": raw_category,
            "normalizedWasteType": waste_type,
            "binNumber": mapping_decision.bin_number,
            "binName": mapping_decision.bin_name,
            "mappingFallback": mapping_decision.mapping_fallback,
            "classificationStatus": mapping_decision.classification_status,
            "mappingKeyUsed": mapping_key_used,
            "mappingReason": mapping_decision.mapping_reason,
            "mappingRuleName": mapping_decision.mapping_rule_name,
            "confidenceThresholdUsed": mapping_decision.confidence_threshold_used,
            "objectNameOverrideApplied": mapping_decision.object_name_override_applied,
        },
        "rawCategory": raw_category,
        "normalizedWasteType": waste_type,
        "binNumber": mapping_decision.bin_number,
        "binName": mapping_decision.bin_name,
        "mappingFallback": mapping_decision.mapping_fallback,
        "mapping_version": mapping_decision.mapping_version,
        "classification_status": mapping_decision.classification_status,
        "classificationStatus": mapping_decision.classification_status,
        "classification_reason": classification_reason,
        "visible_object_detected": True,
        "mapping_fallback": mapping_decision.mapping_fallback,
        "mapping_reason": mapping_decision.mapping_reason,
        "mappingReason": mapping_decision.mapping_reason,
        "mapping_rule_name": mapping_decision.mapping_rule_name,
        "confidence_threshold_used": mapping_decision.confidence_threshold_used,
        "object_name_override_applied": mapping_decision.object_name_override_applied,
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
        material_subtype=material_subtype,
        resin_identification_code=resin_identification_code,
        visible_condition=visible_condition,
        confidence=confidence,
        has_food_scraps=has_food_scraps,
        has_liquid=has_liquid,
        bin_id=mapping_decision.bin_id,
        bin_number=mapping_decision.bin_number,
        bin_name=mapping_decision.bin_name,
        mapping_version=mapping_decision.mapping_version,
        classification_status=mapping_decision.classification_status,
        mapping_fallback=mapping_decision.mapping_fallback,
        status=status,
        error_type=None,
        top_candidates=top_candidates,
        review_flags=review_flags,
        visible_object_detected=True,
        classification_reason=classification_reason,
        object_name_override_applied=mapping_decision.object_name_override_applied,
        mapping_reason=mapping_decision.mapping_reason,
        mapping_rule_name=mapping_decision.mapping_rule_name,
        confidence_threshold_used=mapping_decision.confidence_threshold_used,
    )
    return response
