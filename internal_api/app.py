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
        load_location_config,
        normalize_material_subtype,
        normalize_object_name_th,
        normalize_visible_condition,
        normalize_waste_category,
    )
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
    from .scan_history import ScanHistoryService, ScanNotFoundError, ScanHistoryUnavailableError
except ImportError:  # pragma: no cover - exercised when running app.py directly
    from mapping_config import (
        MappingConfigError,
        load_location_config,
        normalize_material_subtype,
        normalize_object_name_th,
        normalize_visible_condition,
        normalize_waste_category,
    )
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
    from scan_history import ScanHistoryService, ScanNotFoundError, ScanHistoryUnavailableError

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - uvicorn[standard] normally provides this
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


API_URL = os.environ.get("QWEN_API_URL", "https://genai-local.boonrawd.co.th/litellm/v1")
MODEL = os.environ.get("QWEN_MODEL", "qwen3-vl:4b")
QWEN_REQUEST_TIMEOUT_SECONDS = 40
GLASS_PET_REVIEW_CONFIDENCE_GAP = 0.15
PROMPT_VERSION = os.environ.get("ECOCAICLE_PROMPT_VERSION") or "qwen-waste-classification-v5"
REVIEWER_TOKEN = os.environ.get("ECOCAICLE_REVIEWER_TOKEN")
API_KEY = os.environ.get("QWEN_API_KEY")
CA_BUNDLE = os.environ.get("QWEN_CA_BUNDLE")
SSL_CONTEXT = ssl.create_default_context(cafile=CA_BUNDLE) if CA_BUNDLE else ssl.create_default_context()
LOGGER = logging.getLogger(__name__)


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
PROMPT = """Identify and classify only the primary waste item visible in the image. Return classification data only. Never return a bin number, bin name, bin ID, destination, disposal instruction, or mapping decision; the backend handles mapping and any preparation guidance.

Identify the main item itself. Ignore hands, people, background, caps, labels, and minor accessories when identifying its primary material. A cap or label may be read only as evidence about the body when it visibly carries a resin/material mark; it does not define the body's material. Describe the item briefly in Thai, using only visible evidence; do not invent a brand, contents, or material.

Choose waste_category from: general, pet_bottle, plastic, paper, glass, metal, ewaste, fuel, hazardous, infectious, unknown.
Choose material_subtype from: pet_bottle, stretch_plastic, rigid_plastic, plastic_straw, plastic_spoon, multilayer_packaging, snack_sachet, coffee_sachet, refill_pouch, non_recyclable_foam, plastic_coated_paper, non_recyclable_dry_plastic, paper, cardboard, glass, metal, e_waste, hazardous, infectious, general, unknown.

Visual decision rules:
- A beverage bottle with a clearly visible resin code 1, PET, or PETE on the bottle body is waste_category pet_bottle and material_subtype pet_bottle. Return resin_identification_code "1" for this evidence.
- Do not infer PET solely from bottle shape. A beverage bottle may be classified pet_bottle when its transparent plastic body is visibly supported as plastic even without a legible resin mark; when neither resin/PET marking nor transparent plastic evidence is visible, use unknown if material evidence is insufficient. Transparency alone does not prove that a non-bottle item is PET.
- Use resin_identification_code "1" through "7" only when that code is legible; otherwise use the string "unknown". PET/PETE text indicates code "1".
- Never emit material_subtype rigid_plastic for an item classified as pet_bottle. Use rigid_plastic only for supported non-PET rigid plastic items.
- A glass classification requires visible evidence of glass in the primary item's body, such as characteristic refraction, thick rigid glass walls or lip, or a substantial glass base. Do not classify an opaque plastic bottle as glass without such evidence. Bottle shape, reflections, contents, and a cap alone do not prove glass.
- Distinguish paper from cardboard by form: flat sheets or thin paper are paper; formed boxes and corrugated board are waste_category paper with material_subtype cardboard. Do not call cardboard a different waste_category.
- A clearly identifiable thin, hollow disposable drinking straw with visibly plastic material is waste_category plastic and material_subtype plastic_straw. A straw may be white or colored; use the visible hollow drinking-straw form and plastic evidence, not color alone.
- Do not confuse a drinking straw with a cable/wire, pen, product tube, or a line/object in the background. If it is not possible to identify the item as a disposable drinking straw, or its plastic material is not visible, use waste_category unknown and material_subtype unknown rather than plastic_straw.
- Classify plastic_straw as plastic only when the visible object is identifiable as a disposable drinking straw and the body is visibly plastic. Set visible_condition to clean_dry only when the straw is visibly clean and dry. Contamination does not qualify it for the clean/dry RDF route.
- A clearly visible disposable spoon with a visibly plastic body is waste_category plastic and material_subtype plastic_spoon. Do not use plastic_spoon for a metal/reusable spoon, fork, spatula, or an unclear utensil; when disposable-spoon identity or plastic material is uncertain, use unknown. Ignore food scraps on the spoon when identifying its body material, but set food_or_liquid_contaminated when visible residue/liquid is present so it cannot use the clean/dry RDF route.
- Distinguish metal from plastic-coated material by the primary item's visible structure and surface. Metal requires visible metal evidence such as a formed metal body, metallic edge/seam, or exposed metal surface; a shiny coating, foil-like print, or plastic laminate alone is not metal. Use plastic_coated_paper only when both paper and its plastic coating are visible; otherwise use unknown when evidence is insufficient.
- If the item can be named but its material cannot be supported visually, retain the best concise Thai object name and use waste_category unknown, material_subtype unknown, and low confidence. Do not guess a material from item shape alone.
- Ignore minor accessories and classify the main item's body. For plastic, distinguish stretch film, rigid plastic, and multilayer packaging only when visible evidence supports the distinction.
- Cardboard uses waste_category paper/material_subtype cardboard; metal cans use metal/metal; electronic devices use ewaste/e_waste. A clearly identified disposable medical face mask (หน้ากากอนามัย), including used, is infectious.
- Set visible_condition to clean_dry only when visibly clean and dry; food_or_liquid_contaminated only when visible residue/liquid contaminates the item; otherwise unknown. Set has_food_scraps and has_liquid true only when clearly visible.
- confidence is a number from 0.0 to 1.0 for the combined object and material classification. Keep it low when evidence is unclear. top_candidates must contain exactly 3 ranked plausible classifications, most likely first. Every candidate must include object_name_th, waste_category, material_subtype, and confidence (0.0 to 1.0). Candidate 1 must exactly match the primary object_name_th, waste_category, material_subtype, and confidence. Candidates 2 and 3 must be plausible alternatives supported by visible evidence, ranked in non-increasing confidence order after candidate 1. Always give alternatives, even when highly certain; a generic material alternative and/or unknown is allowed. Never invent visual evidence to fill an alternative. Include close alternatives such as glass and pet_bottle only when visible evidence supports both.

Return one valid JSON object only, with all required fields shown below and no markdown or extra text. resin_identification_code must be one of the strings "1" through "7" or "unknown". Do not include any bin or disposal field.
{"object_name_th":"ขวดเครื่องดื่มแก้ว","waste_category":"glass","material_subtype":"glass","resin_identification_code":"unknown","visible_condition":"clean_dry","confidence":0.8,"top_candidates":[{"object_name_th":"ขวดเครื่องดื่มแก้ว","waste_category":"glass","material_subtype":"glass","confidence":0.8},{"object_name_th":"ขวดเครื่องดื่ม","waste_category":"unknown","material_subtype":"unknown","confidence":0.5},{"object_name_th":"ภาชนะ","waste_category":"unknown","material_subtype":"unknown","confidence":0.2}],"has_food_scraps":false,"has_liquid":false}
"""


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


def _chat_completions_url(api_url: str = API_URL) -> str:
    """Accept either the LiteLLM v1 base URL or its full chat-completions URL."""
    parts = urlsplit(api_url)
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


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
    override = MAPPING_CONFIG.infectious_low_confidence_override if MAPPING_CONFIG else {}
    if normalize_object_name_th(item_name) in override.get("object_name_th_allowlist", ()):
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
    override = MAPPING_CONFIG.infectious_low_confidence_override if MAPPING_CONFIG else {}
    if (
        override.get("enabled") is True
        and normalize_object_name_th(object_name_th) in override.get("object_name_th_allowlist", ())
    ):
        return "infectious"
    return normalized_category


def _is_configured_infectious_name(object_name_th: object) -> bool:
    override = MAPPING_CONFIG.infectious_low_confidence_override if MAPPING_CONFIG else {}
    return (
        override.get("enabled") is True
        and normalize_object_name_th(object_name_th) in override.get("object_name_th_allowlist", ())
    )


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
    if isinstance(raw_candidates, list):
        for candidate in raw_candidates[:3]:
            if not isinstance(candidate, dict):
                continue
            raw_name = candidate.get("object_name_th") or candidate.get("itemNameTh")
            raw_category = candidate.get("waste_category")
            category = _normalize_configured_model_category(raw_category, raw_name)
            subtype = normalize_material_subtype(candidate.get("material_subtype"))
            if category == "pet_bottle" and subtype == "rigid_plastic":
                category = "plastic"
            if category == "infectious" and _is_configured_infectious_name(raw_name):
                subtype = "infectious"
            score = _candidate_confidence(candidate.get("confidence"))
            if score is None:
                continue
            candidates.append({
                "object_name_th": _safe_model_item_name(raw_name, category),
                "waste_category": category,
                "material_subtype": subtype,
                "confidence": score,
            })
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
    alternatives = sorted(candidates[1:], key=lambda item: float(item["confidence"]), reverse=True)
    ordered = [primary.copy()]
    for alternative in alternatives:
        if float(alternative["confidence"]) <= float(primary_confidence):
            ordered.append(alternative)
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
    resin_identification_code = _normalize_resin_identification_code(
        response.get("resin_identification_code")
    )
    if resin_identification_code == "1" and _is_beverage_bottle_name(raw_item_name):
        waste_type = "pet_bottle"
        material_subtype = "pet_bottle"
    elif waste_type == "pet_bottle" and material_subtype == "rigid_plastic":
        # Keep an internally contradictory model label from presenting generic
        # rigid plastic as a PET bottle or selecting PET-specific mapping rules.
        waste_type = "plastic"
    if waste_type == "infectious" and _is_configured_infectious_name(item_name):
        material_subtype = "infectious"
    visible_condition = normalize_visible_condition(response.get("visible_condition"))

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
    if resin_identification_code == "1" and _is_beverage_bottle_name(raw_item_name):
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
    with urllib.request.urlopen(
        request,
        timeout=QWEN_REQUEST_TIMEOUT_SECONDS,
        context=SSL_CONTEXT,
    ) as response:
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
    mapping_reason: Optional[str] = None,
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
        "mapping_fallback": mapping_fallback,
        "mapping_reason": mapping_reason or ("mapping_not_resolved" if status == "api_error" else None),
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


def _require_pilot_evaluation(request: Request) -> None:
    if os.environ.get("ECOAICLE_PILOT_EVALUATION_ENABLED") != "1":
        raise HTTPException(status_code=404, detail={"error": "pilot_evaluation_unavailable"})
    _require_reviewer(request)


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


@app.get("/review/pilot/metrics")
async def pilot_evaluation_metrics(request: Request) -> dict[str, object]:
    _require_pilot_evaluation(request)
    try:
        return _review_service_or_error().pilot_metrics()
    except ReviewServiceError as exc:
        _raise_review_http_error(exc)


@app.get("/review/pilot/annotations/{request_id}")
async def get_pilot_evaluation(request: Request, request_id: str) -> dict[str, object]:
    _require_pilot_evaluation(request)
    try:
        return _review_service_or_error().pilot_evaluation(request_id)
    except ReviewServiceError as exc:
        _raise_review_http_error(exc)


@app.post("/review/pilot/annotations/{request_id}")
async def submit_pilot_evaluation(
    request: Request,
    request_id: str,
    payload: dict[str, object] = Body(...),
) -> dict[str, object]:
    _require_pilot_evaluation(request)
    try:
        return _review_service_or_error().submit_pilot_evaluation(request_id, payload)
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
    LOGGER.info("Qwen request started request_id=%s model=%s", request_id, MODEL)
    try:
        result = await asyncio.to_thread(_post_json, payload)
        raw_prediction = _extract_chat_content(result)
    except Exception as exc:
        detail = _model_api_error(exc)
        response_status = 504 if _is_model_timeout(exc) else 502
        LOGGER.warning(
            "Qwen request failed request_id=%s http_status=%s error=%s",
            request_id,
            response_status,
            detail,
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
    LOGGER.info(
        "Qwen response received request_id=%s elapsed_ms=%s",
        request_id,
        _elapsed_ms(inference_started_at),
    )

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
    ) = _parse_model_response(raw_prediction)
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
        },
        "rawCategory": raw_category,
        "normalizedWasteType": waste_type,
        "binNumber": mapping_decision.bin_number,
        "binName": mapping_decision.bin_name,
        "mappingFallback": mapping_decision.mapping_fallback,
        "mapping_version": mapping_decision.mapping_version,
        "classification_status": mapping_decision.classification_status,
        "classificationStatus": mapping_decision.classification_status,
        "mapping_fallback": mapping_decision.mapping_fallback,
        "mapping_reason": mapping_decision.mapping_reason,
        "mappingReason": mapping_decision.mapping_reason,
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
        mapping_reason=mapping_decision.mapping_reason,
    )
    return response
