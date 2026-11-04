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
import unicodedata
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
SUPPORTED_MATERIAL_SUBTYPES = frozenset({
    "pet_bottle",
    "stretch_plastic",
    "rigid_plastic",
    "plastic_straw",
    "plastic_spoon",
    "multilayer_packaging",
    "snack_sachet",
    "coffee_sachet",
    "refill_pouch",
    "non_recyclable_foam",
    "plastic_coated_paper",
    "non_recyclable_dry_plastic",
    "paper",
    "cardboard",
    "glass",
    "metal",
    "e_waste",
    "hazardous",
    "infectious",
    "general",
    "unknown",
})
SUPPORTED_VISIBLE_CONDITIONS = frozenset({
    "clean_dry",
    "food_or_liquid_contaminated",
    "unknown",
})
MAPPABLE_WASTE_CATEGORIES = SUPPORTED_WASTE_CATEGORIES - {"unknown"}
PREPARATION_WASTE_CATEGORIES = frozenset(
    {"pet_bottle", "plastic", "paper", "glass", "metal"}
)
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
    mapping_rulebook: Mapping[str, Any]
    unknown_fallback_enabled: bool
    unknown_fallback_bin_id: Optional[str]
    unknown_fallback_statuses: frozenset[str]
    infectious_low_confidence_override: Mapping[str, Any]
    food_contamination_flow: Mapping[str, Any]
    preparation: Mapping[str, Any]


def normalize_waste_category(value: object) -> str:
    """Return a canonical waste type; unsupported labels stay unknown."""

    normalized = " ".join(str(value or "").strip().lower().split())
    if normalized == "e_waste":
        normalized = "ewaste"
    return normalized if normalized in SUPPORTED_WASTE_CATEGORIES else "unknown"


def normalize_material_subtype(value: object) -> str:
    normalized = " ".join(str(value or "").strip().lower().split())
    if normalized == "ewaste":
        normalized = "e_waste"
    return normalized if normalized in SUPPORTED_MATERIAL_SUBTYPES else "unknown"


def normalize_visible_condition(value: object) -> str:
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized if normalized in SUPPORTED_VISIBLE_CONDITIONS else "unknown"


def normalize_object_name_th(value: object) -> str:
    """Normalize Thai object-name casing and whitespace for exact allowlist checks."""

    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", " ".join(value.casefold().split()))


def normalize_prediction_waste_type(category: object, item_name: object = None) -> str:
    """Normalize the model-selected category without deriving it from an object name."""

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


def _validate_infectious_low_confidence_override(
    raw_rule: object,
    *,
    confidence_threshold: float,
) -> Mapping[str, Any]:
    if raw_rule is None:
        return MappingProxyType({"enabled": False})
    if not isinstance(raw_rule, Mapping):
        raise MappingConfigError(
            "classification_policy.infectious_low_confidence_override must be an object"
        )

    field_prefix = "classification_policy.infectious_low_confidence_override"
    enabled = _boolean(raw_rule.get("enabled"), f"{field_prefix}.enabled")
    waste_category = _non_empty_string(raw_rule.get("waste_category"), f"{field_prefix}.waste_category")
    if waste_category != "infectious":
        raise MappingConfigError(f"{field_prefix}.waste_category must be 'infectious'")
    override_threshold = _finite_number(
        raw_rule.get("confidence_threshold"),
        f"{field_prefix}.confidence_threshold",
    )
    if not 0 <= override_threshold <= 1:
        raise MappingConfigError(f"{field_prefix}.confidence_threshold must be between 0 and 1")
    if override_threshold >= confidence_threshold:
        raise MappingConfigError(
            f"{field_prefix}.confidence_threshold must be below the global confidence threshold"
        )

    raw_names = raw_rule.get("object_name_th_allowlist")
    if not isinstance(raw_names, list) or not raw_names:
        raise MappingConfigError(f"{field_prefix}.object_name_th_allowlist must be a non-empty array")
    allowlist: list[str] = []
    for index, raw_name in enumerate(raw_names):
        name = _non_empty_string(raw_name, f"{field_prefix}.object_name_th_allowlist[{index}]")
        normalized_name = normalize_object_name_th(name)
        if normalized_name in allowlist:
            raise MappingConfigError(f"{field_prefix}.object_name_th_allowlist contains a duplicate name")
        allowlist.append(normalized_name)

    mapping_reason = _non_empty_string(raw_rule.get("mapping_reason"), f"{field_prefix}.mapping_reason")

    return MappingProxyType({
        "enabled": enabled,
        "waste_category": waste_category,
        "confidence_threshold": override_threshold,
        "object_name_th_allowlist": tuple(allowlist),
        "mapping_reason": mapping_reason,
    })


def _validate_mapping_rulebook(
    raw_rulebook: object,
    *,
    bins: Mapping[str, BinDefinition],
    location_id: str,
) -> Mapping[str, Any]:
    if raw_rulebook is None:
        # Compatibility for older non-ICT test/deployment configurations.
        return MappingProxyType({"version": "legacy-category-map", "ordered_rules": ()})
    if not isinstance(raw_rulebook, Mapping):
        raise MappingConfigError("mapping_rulebook must be an object")
    version = _non_empty_string(raw_rulebook.get("version"), "mapping_rulebook.version")
    raw_rules = raw_rulebook.get("ordered_rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise MappingConfigError("mapping_rulebook.ordered_rules must be a non-empty array")

    expected_ids = ["infectious", "hazardous", "e_waste", "clean_recyclable_material", "rdf_fuel", "fallback"]
    rules: list[dict[str, Any]] = []
    for index, raw_rule in enumerate(raw_rules):
        prefix = f"mapping_rulebook.ordered_rules[{index}]"
        if not isinstance(raw_rule, Mapping):
            raise MappingConfigError(f"{prefix} must be an object")
        rule = dict(raw_rule)
        rule_id = _non_empty_string(rule.get("id"), f"{prefix}.id")
        if index >= len(expected_ids) or rule_id != expected_ids[index]:
            raise MappingConfigError("mapping_rulebook rules are missing or out of priority order")
        rule["mapping_reason"] = _non_empty_string(rule.get("mapping_reason"), f"{prefix}.mapping_reason")
        if rule.get("bin_id") is not None:
            bin_id = _non_empty_string(rule.get("bin_id"), f"{prefix}.bin_id")
            if bin_id not in bins or not bins[bin_id].enabled:
                raise MappingConfigError(f"{prefix}.bin_id must reference an enabled configured bin")
            rule["bin_id"] = bin_id
        elif rule_id != "clean_recyclable_material":
            raise MappingConfigError(f"{prefix}.bin_id is required")

        if "waste_categories" in rule:
            categories = rule["waste_categories"]
            if not isinstance(categories, list) or not categories:
                raise MappingConfigError(f"{prefix}.waste_categories must be a non-empty array")
            normalized_categories = [normalize_waste_category(value) for value in categories]
            if "unknown" in normalized_categories or len(set(normalized_categories)) != len(normalized_categories):
                raise MappingConfigError(f"{prefix}.waste_categories contains an invalid or duplicate category")
            rule["waste_categories"] = normalized_categories

        if "visible_conditions" in rule:
            conditions = rule["visible_conditions"]
            if not isinstance(conditions, list) or not conditions:
                raise MappingConfigError(f"{prefix}.visible_conditions must be a non-empty array")
            normalized_conditions = [normalize_visible_condition(value) for value in conditions]
            if "unknown" in normalized_conditions or len(set(normalized_conditions)) != len(normalized_conditions):
                raise MappingConfigError(f"{prefix}.visible_conditions contains an invalid or duplicate condition")
            rule["visible_conditions"] = normalized_conditions

        if "material_subtypes" in rule:
            subtypes = rule["material_subtypes"]
            if not isinstance(subtypes, list) or not subtypes:
                raise MappingConfigError(f"{prefix}.material_subtypes must be a non-empty array")
            normalized_subtypes = [normalize_material_subtype(value) for value in subtypes]
            if "unknown" in normalized_subtypes or len(set(normalized_subtypes)) != len(normalized_subtypes):
                raise MappingConfigError(f"{prefix}.material_subtypes contains an invalid or duplicate subtype")
            rule["material_subtypes"] = normalized_subtypes

        if "material_subtype_bins" in rule:
            subtype_bins = rule["material_subtype_bins"]
            if not isinstance(subtype_bins, Mapping) or not subtype_bins:
                raise MappingConfigError(f"{prefix}.material_subtype_bins must be a non-empty object")
            normalized_bins: dict[str, str] = {}
            for raw_subtype, raw_bin_id in subtype_bins.items():
                subtype = normalize_material_subtype(raw_subtype)
                selected_bin = _non_empty_string(raw_bin_id, f"{prefix}.material_subtype_bins.{raw_subtype}")
                if subtype == "unknown" or selected_bin not in bins or not bins[selected_bin].enabled:
                    raise MappingConfigError(f"{prefix}.material_subtype_bins contains an invalid mapping")
                normalized_bins[subtype] = selected_bin
            rule["material_subtype_bins"] = normalized_bins

        if "material_subtype_categories" in rule:
            subtype_categories = rule["material_subtype_categories"]
            if not isinstance(subtype_categories, Mapping):
                raise MappingConfigError(f"{prefix}.material_subtype_categories must be an object")
            normalized_map: dict[str, list[str]] = {}
            for raw_subtype, categories in subtype_categories.items():
                subtype = normalize_material_subtype(raw_subtype)
                if subtype == "unknown" or not isinstance(categories, list) or not categories:
                    raise MappingConfigError(f"{prefix}.material_subtype_categories contains an invalid entry")
                normalized_categories = [normalize_waste_category(value) for value in categories]
                if "unknown" in normalized_categories:
                    raise MappingConfigError(f"{prefix}.material_subtype_categories contains an invalid category")
                normalized_map[subtype] = normalized_categories
            rule["material_subtype_categories"] = normalized_map

        for list_key in ("eligible_material_subtypes", "eligible_object_names_th"):
            if list_key in rule:
                values = rule[list_key]
                if not isinstance(values, list):
                    raise MappingConfigError(f"{prefix}.{list_key} must be an array")
                if list_key == "eligible_material_subtypes":
                    normalized_values = [normalize_material_subtype(value) for value in values]
                    if "unknown" in normalized_values or len(set(normalized_values)) != len(normalized_values):
                        raise MappingConfigError(f"{prefix}.{list_key} contains an invalid or duplicate subtype")
                    rule[list_key] = normalized_values
                else:
                    names = [normalize_object_name_th(_non_empty_string(value, f"{prefix}.{list_key}")) for value in values]
                    if len(set(names)) != len(names):
                        raise MappingConfigError(f"{prefix}.{list_key} contains duplicate names")
                    rule[list_key] = names
        if "requires_preparation_path_when_contaminated" in rule:
            rule["requires_preparation_path_when_contaminated"] = _boolean(
                rule["requires_preparation_path_when_contaminated"],
                f"{prefix}.requires_preparation_path_when_contaminated",
            )
        if "preparation_mapping_reason" in rule:
            rule["preparation_mapping_reason"] = _non_empty_string(
                rule["preparation_mapping_reason"],
                f"{prefix}.preparation_mapping_reason",
            )
        if "mapping_reasons" in rule:
            reasons = rule["mapping_reasons"]
            if not isinstance(reasons, Mapping):
                raise MappingConfigError(f"{prefix}.mapping_reasons must be an object")
            rule["mapping_reasons"] = {
                _non_empty_string(key, f"{prefix}.mapping_reasons key"):
                _non_empty_string(value, f"{prefix}.mapping_reasons.{key}")
                for key, value in reasons.items()
            }
        if "mapping_reason_by_material_subtype" in rule:
            reasons = rule["mapping_reason_by_material_subtype"]
            if not isinstance(reasons, Mapping):
                raise MappingConfigError(f"{prefix}.mapping_reason_by_material_subtype must be an object")
            normalized_reasons: dict[str, str] = {}
            for raw_subtype, raw_reason in reasons.items():
                subtype = normalize_material_subtype(raw_subtype)
                reason = _non_empty_string(raw_reason, f"{prefix}.mapping_reason_by_material_subtype.{raw_subtype}")
                if subtype == "unknown" or subtype in normalized_reasons:
                    raise MappingConfigError(f"{prefix}.mapping_reason_by_material_subtype contains an invalid or duplicate subtype")
                normalized_reasons[subtype] = reason
            eligible_subtypes = set(rule.get("eligible_material_subtypes", ()))
            if not set(normalized_reasons).issubset(eligible_subtypes):
                raise MappingConfigError(f"{prefix}.mapping_reason_by_material_subtype must target eligible material subtypes")
            rule["mapping_reason_by_material_subtype"] = normalized_reasons
        rules.append(rule)

    if len(rules) != len(expected_ids) or rules[-1].get("id") != "fallback":
        raise MappingConfigError("mapping_rulebook must define all required priority rules and a final fallback")
    rdf_rule = rules[4]
    forbidden_rdf_subtypes = {"pet_bottle", "stretch_plastic", "paper", "cardboard", "glass", "metal", "e_waste", "hazardous", "infectious"}
    if location_id != "ICT-001":
        forbidden_rdf_subtypes.add("rigid_plastic")
    if forbidden_rdf_subtypes.intersection(rdf_rule.get("eligible_material_subtypes", ())):
        raise MappingConfigError("mapping_rulebook RDF rule includes a subtype reserved for another bin or excluded material")
    rdf_categories = set(rdf_rule.get("waste_categories", ()))
    if rdf_categories.intersection({"glass", "metal", "ewaste", "hazardous", "infectious", "general"}):
        raise MappingConfigError("mapping_rulebook RDF rule includes a prohibited waste category")

    return _freeze_value({"version": version, "ordered_rules": rules})


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
        if category not in MAPPABLE_WASTE_CATEGORIES or category not in PREPARATION_WASTE_CATEGORIES:
            raise MappingConfigError(
                "unsupported or non-recyclable preparation trigger category at index "
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
    infectious_low_confidence_override = _validate_infectious_low_confidence_override(
        policy.get("infectious_low_confidence_override"),
        confidence_threshold=confidence_threshold,
    )

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

    if infectious_low_confidence_override.get("enabled") and "infectious" not in category_mapping:
        raise MappingConfigError(
            "classification_policy.infectious_low_confidence_override requires a configured infectious category mapping"
        )

    mapping_rulebook = _validate_mapping_rulebook(
        raw_config.get("mapping_rulebook"),
        bins=bins,
        location_id=location_id,
    )

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
        mapping_rulebook=mapping_rulebook,
        unknown_fallback_enabled=fallback_enabled,
        unknown_fallback_bin_id=fallback_bin_id,
        unknown_fallback_statuses=fallback_statuses,
        infectious_low_confidence_override=infectious_low_confidence_override,
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
