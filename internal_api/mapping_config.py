"""Configuration loading and validation for location-aware bin mapping.

This module is deliberately independent from the image inference path.  A
configuration is read once when the backend starts and is represented as
immutable objects for the lifetime of that process.  Updating a JSON file
therefore has no effect until the backend is restarted.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional


SUPPORTED_WASTE_CATEGORIES = frozenset(
    {
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
)
MAPPABLE_WASTE_CATEGORIES = SUPPORTED_WASTE_CATEGORIES - {"unknown"}
DEFAULT_LOCATION_ID = "ICT-001"
DEFAULT_CONFIG_DIRECTORY = Path(__file__).resolve().parent / "config" / "locations"
LOCATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
SUPPORTED_FALLBACK_STATUSES = frozenset({"unknown", "low_confidence"})


class MappingConfigError(ValueError):
    """Raised when a location mapping configuration is invalid."""


@dataclass(frozen=True)
class BinDefinition:
    bin_id: str
    bin_number: int
    bin_name: str
    enabled: bool
    icon: Optional[str] = None


@dataclass(frozen=True)
class LocationMappingConfig:
    location_id: str
    location_name: str
    mapping_version: str
    confidence_threshold: float
    bins: Mapping[str, BinDefinition]
    category_mapping: Mapping[str, str]
    unknown_fallback_enabled: bool
    unknown_fallback_bin_id: Optional[str]
    unknown_fallback_statuses: frozenset[str]
    food_contamination_flow: Mapping[str, Any]
    preparation: Mapping[str, Any]


def normalize_waste_category(value: object) -> str:
    """Return a canonical waste type; unsupported labels stay unknown."""

    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized if normalized in SUPPORTED_WASTE_CATEGORIES else "unknown"


PHONE_OBJECT_NAME_ALIASES = (
    "โทรศัพท์มือถือ",
    "มือถือ",
    "สมาร์ตโฟน",
    "สมาร์ทโฟน",
    "โทรศัพท์",
    "mobile phone",
    "smartphone",
    "cell phone",
)


def normalize_prediction_waste_type(category: object, item_name: object = None) -> str:
    """Normalize a waste type, recognizing mobile phones as e-waste."""

    if isinstance(item_name, str):
        normalized_name = " ".join(item_name.casefold().split())
        if any(alias in normalized_name for alias in PHONE_OBJECT_NAME_ALIASES):
            return "ewaste"
    return normalize_waste_category(category)


def _non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingConfigError(f"{field_name} must be a non-empty string")
    return value.strip()


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise MappingConfigError(f"{field_name} must be a boolean")
    return value


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MappingConfigError(f"{field_name} must be a number")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise MappingConfigError(f"{field_name} must be finite")
    return numeric_value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MappingConfigError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle, object_pairs_hook=_reject_duplicate_json_keys)
    except FileNotFoundError as exc:
        raise MappingConfigError(f"mapping configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MappingConfigError(f"invalid JSON in mapping configuration {path}: {exc}") from exc
    except OSError as exc:
        raise MappingConfigError(f"could not read mapping configuration {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise MappingConfigError("mapping configuration root must be an object")
    return raw


def _validate_food_contamination_flow(
    raw_flow: object,
    bins: Mapping[str, BinDefinition],
) -> Mapping[str, Any]:
    if raw_flow is None:
        return MappingProxyType({})
    if not isinstance(raw_flow, dict):
        raise MappingConfigError("food_contamination_flow must be an object")

    flow = dict(raw_flow)
    if "enabled" in flow:
        _boolean(flow["enabled"], "food_contamination_flow.enabled")

    container_bin_id = flow.get("container_bin_id")
    if container_bin_id is not None:
        container_bin_id = _non_empty_string(
            container_bin_id,
            "food_contamination_flow.container_bin_id",
        )
        if container_bin_id not in bins:
            raise MappingConfigError(
                "food_contamination_flow.container_bin_id references an unknown bin"
            )

    destination = flow.get("food_scrap_destination")
    if destination is not None:
        if not isinstance(destination, dict):
            raise MappingConfigError(
                "food_contamination_flow.food_scrap_destination must be an object"
            )
        destination_bin_id = destination.get("bin_id")
        if destination_bin_id is not None:
            destination_bin_id = _non_empty_string(
                destination_bin_id,
                "food_contamination_flow.food_scrap_destination.bin_id",
            )
            if destination_bin_id not in bins:
                raise MappingConfigError(
                    "food_contamination_flow.food_scrap_destination.bin_id references an unknown bin"
                )

    steps = flow.get("steps")
    if steps is not None:
        if not isinstance(steps, list):
            raise MappingConfigError("food_contamination_flow.steps must be an array")
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise MappingConfigError(
                    f"food_contamination_flow.steps[{index}] must be an object"
                )
            if "step" in step:
                step_number = step["step"]
                if isinstance(step_number, bool) or not isinstance(step_number, int):
                    raise MappingConfigError(
                        f"food_contamination_flow.steps[{index}].step must be an integer"
                    )

    return _freeze_value(flow)


def _validate_preparation(
    raw_preparation: object,
) -> Mapping[str, Any]:
    """Validate the location-specific food/liquid preparation contract.

    The first destination is deliberately a designated point outside the
    numbered-bin mapping.  The second destination is resolved from the
    classification mapping at request time and therefore cannot carry a
    hardcoded bin reference in this configuration.
    """

    if raw_preparation is None:
        return MappingProxyType({"enabled": False})
    if not isinstance(raw_preparation, Mapping):
        raise MappingConfigError("preparation must be an object")

    preparation = dict(raw_preparation)
    enabled = _boolean(preparation.get("enabled"), "preparation.enabled")
    if not enabled:
        return _freeze_value(preparation)

    _non_empty_string(preparation.get("version"), "preparation.version")
    signal = _non_empty_string(preparation.get("signal"), "preparation.signal")
    if signal != "has_food_scraps":
        raise MappingConfigError(
            "preparation.signal must be has_food_scraps because the model contract "
            "does not distinguish food, liquid, grease, and contamination"
        )

    trigger = preparation.get("trigger")
    if not isinstance(trigger, Mapping):
        raise MappingConfigError("preparation.trigger must be an object")
    if trigger.get("has_food_scraps") is not True:
        raise MappingConfigError("preparation.trigger.has_food_scraps must be true")
    if trigger.get("requires_confirmed_mapping") is not True:
        raise MappingConfigError(
            "preparation.trigger.requires_confirmed_mapping must be true"
        )
    raw_categories = trigger.get("waste_categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise MappingConfigError(
            "preparation.trigger.waste_categories must be a non-empty array"
        )
    normalized_categories: list[str] = []
    for index, raw_category in enumerate(raw_categories):
        category = normalize_waste_category(raw_category)
        if category not in MAPPABLE_WASTE_CATEGORIES:
            raise MappingConfigError(
                "unsupported preparation trigger category at index "
                f"{index}: {raw_category}"
            )
        if category in normalized_categories:
            raise MappingConfigError(
                f"duplicate preparation trigger category: {category}"
            )
        normalized_categories.append(category)

    destination = preparation.get("shared_disposal_destination")
    if not isinstance(destination, Mapping):
        raise MappingConfigError(
            "preparation.shared_disposal_destination must be an object"
        )
    _non_empty_string(
        destination.get("destination_id"),
        "preparation.shared_disposal_destination.destination_id",
    )
    destination_type = _non_empty_string(
        destination.get("type"),
        "preparation.shared_disposal_destination.type",
    )
    if destination_type != "designated_disposal_point":
        raise MappingConfigError(
            "preparation.shared_disposal_destination.type must be "
            "designated_disposal_point"
        )
    _non_empty_string(
        destination.get("label_th"),
        "preparation.shared_disposal_destination.label_th",
    )
    for field_name in ("bin_id", "bin_number"):
        if field_name in destination and destination[field_name] is not None:
            raise MappingConfigError(
                "preparation.shared_disposal_destination must not define "
                f"{field_name}"
            )

    steps = preparation.get("steps")
    if not isinstance(steps, list) or len(steps) != 2:
        raise MappingConfigError("preparation.steps must contain exactly two steps")
    expected_refs = ("shared_disposal_destination", "mapped_container_bin")
    seen_steps: set[int] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise MappingConfigError(f"preparation.steps[{index}] must be an object")
        step_number = step.get("step")
        if isinstance(step_number, bool) or not isinstance(step_number, int):
            raise MappingConfigError(
                f"preparation.steps[{index}].step must be an integer"
            )
        if step_number not in (1, 2) or step_number in seen_steps:
            raise MappingConfigError(
                "preparation.steps must contain unique step numbers 1 and 2"
            )
        seen_steps.add(step_number)
        _non_empty_string(step.get("action_th"), f"preparation.steps[{index}].action_th")
        _non_empty_string(
            step.get("instruction_th"),
            f"preparation.steps[{index}].instruction_th",
        )
        if step.get("destination_ref") != expected_refs[index]:
            raise MappingConfigError(
                f"preparation.steps[{index}].destination_ref must be "
                f"{expected_refs[index]}"
            )
        icon = step.get("icon")
        if icon is not None:
            _non_empty_string(icon, f"preparation.steps[{index}].icon")

    if seen_steps != {1, 2}:
        raise MappingConfigError("preparation.steps must contain step 1 and step 2")

    normalized_trigger = dict(trigger)
    normalized_trigger["waste_categories"] = normalized_categories
    preparation["trigger"] = normalized_trigger
    return _freeze_value(preparation)


def validate_location_config(
    raw_config: Mapping[str, Any],
    *,
    expected_location_id: Optional[str] = None,
) -> LocationMappingConfig:
    """Validate raw JSON data and return an immutable location configuration."""

    if not isinstance(raw_config, Mapping):
        raise MappingConfigError("mapping configuration root must be an object")

    schema_version = _non_empty_string(raw_config.get("schema_version"), "schema_version")
    if schema_version != "1.0":
        raise MappingConfigError(f"unsupported mapping schema_version: {schema_version}")

    location_id = _non_empty_string(raw_config.get("location_id"), "location_id")
    if not LOCATION_ID_PATTERN.fullmatch(location_id):
        raise MappingConfigError("location_id contains unsupported characters")
    if expected_location_id is not None and location_id != expected_location_id:
        raise MappingConfigError(
            f"configuration location_id {location_id!r} does not match expected "
            f"location_id {expected_location_id!r}"
        )

    location_name = _non_empty_string(raw_config.get("location_name"), "location_name")
    mapping_version = _non_empty_string(raw_config.get("mapping_version"), "mapping_version")
    if "enabled" in raw_config and not _boolean(raw_config["enabled"], "enabled"):
        raise MappingConfigError(f"location {location_id} is disabled")

    policy = raw_config.get("classification_policy")
    if not isinstance(policy, Mapping):
        raise MappingConfigError("classification_policy must be an object")
    confidence_threshold = _finite_number(
        policy.get("confidence_threshold"),
        "classification_policy.confidence_threshold",
    )
    if not 0 <= confidence_threshold <= 1:
        raise MappingConfigError("classification_policy.confidence_threshold must be between 0 and 1")

    fallback = policy.get("unknown_fallback")
    if not isinstance(fallback, Mapping):
        raise MappingConfigError("classification_policy.unknown_fallback must be an object")
    fallback_enabled = _boolean(
        fallback.get("enabled"),
        "classification_policy.unknown_fallback.enabled",
    )
    fallback_bin_id: Optional[str] = None
    if fallback_enabled:
        fallback_bin_id = _non_empty_string(
            fallback.get("bin_id"),
            "classification_policy.unknown_fallback.bin_id",
        )

    raw_statuses = fallback.get("applies_to", ["unknown"])
    if not isinstance(raw_statuses, list) or not raw_statuses:
        raise MappingConfigError(
            "classification_policy.unknown_fallback.applies_to must be a non-empty array"
        )
    fallback_statuses = frozenset(
        _non_empty_string(status, "classification_policy.unknown_fallback.applies_to item")
        for status in raw_statuses
    )
    unsupported_statuses = fallback_statuses - SUPPORTED_FALLBACK_STATUSES
    if unsupported_statuses:
        raise MappingConfigError(
            "unsupported fallback statuses: " + ", ".join(sorted(unsupported_statuses))
        )

    raw_bins = raw_config.get("bins")
    if not isinstance(raw_bins, list) or not raw_bins:
        raise MappingConfigError("bins must be a non-empty array")

    bins: dict[str, BinDefinition] = {}
    numbers: dict[int, str] = {}
    for index, raw_bin in enumerate(raw_bins):
        if not isinstance(raw_bin, Mapping):
            raise MappingConfigError(f"bins[{index}] must be an object")
        bin_id = _non_empty_string(raw_bin.get("bin_id"), f"bins[{index}].bin_id")
        if bin_id in bins:
            raise MappingConfigError(f"duplicate bin_id: {bin_id}")

        raw_number = raw_bin.get("bin_number")
        if isinstance(raw_number, bool) or not isinstance(raw_number, int) or raw_number <= 0:
            raise MappingConfigError(f"bins[{index}].bin_number must be a positive integer")
        if raw_number in numbers:
            raise MappingConfigError(
                f"duplicate bin_number {raw_number}: {numbers[raw_number]} and {bin_id}"
            )

        bin_name = _non_empty_string(raw_bin.get("bin_name"), f"bins[{index}].bin_name")
        enabled = _boolean(raw_bin.get("enabled"), f"bins[{index}].enabled")
        icon = raw_bin.get("icon")
        if icon is not None:
            icon = _non_empty_string(icon, f"bins[{index}].icon")

        bins[bin_id] = BinDefinition(
            bin_id=bin_id,
            bin_number=raw_number,
            bin_name=bin_name,
            enabled=enabled,
            icon=icon,
        )
        numbers[raw_number] = bin_id

    if fallback_enabled:
        assert fallback_bin_id is not None
        if fallback_bin_id not in bins:
            raise MappingConfigError(
                "classification_policy.unknown_fallback.bin_id references an unknown bin"
            )
        if not bins[fallback_bin_id].enabled:
            raise MappingConfigError(
                "classification_policy.unknown_fallback.bin_id references a disabled bin"
            )

    raw_category_mapping = raw_config.get("category_mapping")
    if not isinstance(raw_category_mapping, Mapping) or not raw_category_mapping:
        raise MappingConfigError("category_mapping must be a non-empty object")

    category_mapping: dict[str, str] = {}
    for raw_category, raw_bin_id in raw_category_mapping.items():
        if not isinstance(raw_category, str):
            raise MappingConfigError("category_mapping keys must be strings")
        category = normalize_waste_category(raw_category)
        if category == "unknown" or category not in MAPPABLE_WASTE_CATEGORIES:
            raise MappingConfigError(
                f"unsupported category_mapping category: {raw_category}"
            )
        bin_id = _non_empty_string(raw_bin_id, f"category_mapping.{raw_category}")
        if bin_id not in bins:
            raise MappingConfigError(
                f"category_mapping.{raw_category} references an unknown bin: {bin_id}"
            )
        if not bins[bin_id].enabled:
            raise MappingConfigError(
                f"category_mapping.{raw_category} references a disabled bin: {bin_id}"
            )
        previous_bin_id = category_mapping.get(category)
        if previous_bin_id is not None and previous_bin_id != bin_id:
            raise MappingConfigError(
                f"legacy/canonical category collision for {category}: "
                f"{previous_bin_id} and {bin_id}"
            )
        category_mapping[category] = bin_id

    food_contamination_flow = _validate_food_contamination_flow(
        raw_config.get("food_contamination_flow"),
        bins,
    )
    preparation = _validate_preparation(raw_config.get("preparation"))

    return LocationMappingConfig(
        location_id=location_id,
        location_name=location_name,
        mapping_version=mapping_version,
        confidence_threshold=confidence_threshold,
        bins=MappingProxyType(dict(bins)),
        category_mapping=MappingProxyType(dict(category_mapping)),
        unknown_fallback_enabled=fallback_enabled,
        unknown_fallback_bin_id=fallback_bin_id,
        unknown_fallback_statuses=fallback_statuses,
        food_contamination_flow=food_contamination_flow,
        preparation=preparation,
    )


def load_location_config(
    location_id: Optional[str] = None,
    config_dir: Optional[os.PathLike[str] | str] = None,
) -> LocationMappingConfig:
    """Load and validate one location configuration from disk.

    The caller should invoke this during application startup.  There is no
    watcher or reload path by design; a process restart is required after a
    configuration change.
    """

    resolved_location_id = location_id or os.environ.get(
        "ECOCAICLE_LOCATION_ID",
        DEFAULT_LOCATION_ID,
    )
    if not LOCATION_ID_PATTERN.fullmatch(resolved_location_id):
        raise MappingConfigError("configured ECOCAICLE_LOCATION_ID contains unsupported characters")

    resolved_config_dir = Path(
        config_dir
        or os.environ.get("ECOCAICLE_MAPPING_CONFIG_DIR", str(DEFAULT_CONFIG_DIRECTORY))
    )
    config_path = resolved_config_dir / f"{resolved_location_id}.json"
    raw_config = _read_json(config_path)
    return validate_location_config(raw_config, expected_location_id=resolved_location_id)
