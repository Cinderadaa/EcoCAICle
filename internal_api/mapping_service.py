"""Independent waste-category to physical-bin mapping service."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

try:  # Supports both ``python -m internal_api...`` and the Docker working dir.
    from .mapping_config import (
        LocationMappingConfig,
        normalize_material_subtype,
        normalize_object_name_th,
        normalize_visible_condition,
        normalize_waste_category,
    )
except ImportError:  # pragma: no cover - exercised when running from internal_api/
    from mapping_config import (
        LocationMappingConfig,
        normalize_material_subtype,
        normalize_object_name_th,
        normalize_visible_condition,
        normalize_waste_category,
    )


class LocationMismatchError(ValueError):
    """Raised when a request attempts to use another location's configuration."""


@dataclass(frozen=True)
class MappingDecision:
    bin_id: Optional[str]
    bin_number: Optional[int]
    bin_name: Optional[str]
    mapping_version: str
    classification_status: str
    mapping_fallback: bool
    mapping_reason: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "bin_id": self.bin_id,
            "bin_number": self.bin_number,
            "bin_name": self.bin_name,
            "mapping_version": self.mapping_version,
            "classification_status": self.classification_status,
            "mapping_fallback": self.mapping_fallback,
            "mapping_reason": self.mapping_reason,
        }


@dataclass(frozen=True)
class PreparationDecision:
    """Backend-authoritative food/liquid preparation instructions."""

    required: bool
    reason: str
    version: Optional[str]
    signal: str
    signal_value: bool
    guidance_mode: str
    shared_disposal_destination: Optional[dict[str, object]]
    container_disposal: Optional[dict[str, object]]
    steps: list[dict[str, object]]
    limitation_th: Optional[str]
    has_food_scraps: bool = False
    has_liquid: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "required": self.required,
            "reason": self.reason,
            "version": self.version,
            "signal": self.signal,
            "signal_value": self.signal_value,
            "guidance_mode": self.guidance_mode,
            "has_food_scraps": self.has_food_scraps,
            "hasFoodScraps": self.has_food_scraps,
            "has_liquid": self.has_liquid,
            "hasLiquid": self.has_liquid,
            "shared_disposal_destination": self.shared_disposal_destination,
            "container_disposal": self.container_disposal,
            "steps": self.steps,
            "limitation_th": self.limitation_th,
        }


def _coerce_confidence(value: object) -> Optional[float]:
    """Match the current parser's safe behavior for invalid confidence values."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        return None
    if not 0 <= numeric_value <= 1:
        return 0.0
    return numeric_value


class MappingService:
    """Resolve a classification against one validated, immutable location config."""

    def __init__(self, config: LocationMappingConfig):
        self.config = config

    def resolve(
        self,
        *,
        location_id: str,
        waste_category: object,
        confidence: object,
        object_name_th: object = None,
        material_subtype: object = None,
        visible_condition: object = None,
        has_food_scraps: bool = False,
        has_liquid: bool = False,
    ) -> MappingDecision:
        if location_id != self.config.location_id:
            raise LocationMismatchError(
                f"requested location {location_id!r} does not match configured "
                f"location {self.config.location_id!r}"
            )

        normalized_category = normalize_waste_category(waste_category)
        normalized_subtype = normalize_material_subtype(material_subtype)
        normalized_condition = normalize_visible_condition(visible_condition)
        if has_food_scraps is True or has_liquid is True:
            normalized_condition = "food_or_liquid_contaminated"
        normalized_confidence = _coerce_confidence(confidence)
        rulebook_rules = self.config.mapping_rulebook.get("ordered_rules", ())

        override = self.config.infectious_low_confidence_override
        normalized_object_name = normalize_object_name_th(object_name_th)
        if (
            normalized_category == override.get("waste_category")
            and normalized_confidence is not None
            and self.config.confidence_threshold > normalized_confidence
            and normalized_confidence >= override.get("confidence_threshold", 1.0)
            and override.get("enabled") is True
            and normalized_object_name in override.get("object_name_th_allowlist", ())
        ):
            infectious_rule = next(
                (rule for rule in rulebook_rules if rule.get("id") == "infectious"),
                None,
            )
            bin_id = (
                infectious_rule.get("bin_id") if infectious_rule
                else self.config.category_mapping.get(normalized_category)
            )
            if bin_id is not None:
                return self._infectious_safety_override(
                    bin_id,
                    mapping_reason=override["mapping_reason"],
                )

        if normalized_category == "unknown":
            classification_status = "unknown"
        elif (
            normalized_confidence is None
            or normalized_confidence < self.config.confidence_threshold
        ):
            classification_status = "low_confidence"
        else:
            classification_status = "confirmed"

        if classification_status != "confirmed":
            reason = self._fallback_reason(
                rulebook_rules[-1] if rulebook_rules else {},
                classification_status=classification_status,
                visible_condition=normalized_condition,
            )
            return self._bin_one_fallback(classification_status, mapping_reason=reason)

        if not rulebook_rules:
            # The legacy plastic category mapping is retained in configuration
            # for compatibility, but category alone cannot identify stretch
            # plastic and must never route generic plastic to Bin 3.
            if normalized_category == "plastic":
                return self._bin_one_fallback(
                    classification_status,
                    mapping_reason="unsupported_material_fallback",
                )
            bin_id = self.config.category_mapping.get(normalized_category)
            if bin_id is None:
                return self._unavailable()
            return self._confirmed(bin_id, mapping_reason="legacy_category_mapping")

        normalized_name = normalize_object_name_th(object_name_th)
        for rule in rulebook_rules:
            rule_id = rule.get("id")
            if rule_id == "fallback":
                reason = self._fallback_reason(
                    rule,
                    classification_status=classification_status,
                    visible_condition=normalized_condition,
                )
                return self._fallback(rule["bin_id"], classification_status, mapping_reason=reason)

            categories = rule.get("waste_categories")
            if categories and normalized_category not in categories:
                continue
            subtypes = rule.get("material_subtypes")
            if subtypes and normalized_subtype not in subtypes:
                continue

            if rule_id == "clean_recyclable_material":
                if normalized_condition not in rule.get("visible_conditions", ()):
                    continue
                subtype_bins = rule.get("material_subtype_bins", {})
                if normalized_subtype not in subtype_bins:
                    continue
                subtype_categories = rule.get("material_subtype_categories", {}).get(normalized_subtype, ())
                if normalized_category not in subtype_categories:
                    continue
                needs_preparation = normalized_condition == "food_or_liquid_contaminated"
                if needs_preparation and (
                    rule.get("requires_preparation_path_when_contaminated") is not True
                    or not self._has_preparation_path(
                        normalized_category,
                        has_food_scraps=has_food_scraps,
                        has_liquid=has_liquid,
                        visible_condition=normalized_condition,
                    )
                ):
                    continue
                reason = (
                    rule.get("preparation_mapping_reason") if needs_preparation
                    else rule["mapping_reason"]
                )
                return self._confirmed(subtype_bins[normalized_subtype], mapping_reason=reason)

            if rule_id == "rdf_fuel":
                if normalized_condition not in rule.get("visible_conditions", ()):
                    continue
                eligible_subtypes = rule.get("eligible_material_subtypes", ())
                eligible_names = rule.get("eligible_object_names_th", ())
                if normalized_subtype not in eligible_subtypes and normalized_name not in eligible_names:
                    continue
                needs_preparation = normalized_condition == "food_or_liquid_contaminated"
                if needs_preparation and (
                    rule.get("requires_preparation_path_when_contaminated") is not True
                    or not self._has_preparation_path(
                        normalized_category,
                        has_food_scraps=has_food_scraps,
                        has_liquid=has_liquid,
                        visible_condition=normalized_condition,
                    )
                ):
                    continue
                reason = (
                    rule.get("preparation_mapping_reason") if needs_preparation
                    else rule.get("mapping_reason_by_material_subtype", {}).get(normalized_subtype, rule["mapping_reason"])
                )
                return self._confirmed(rule["bin_id"], mapping_reason=reason)

            else:
                return self._confirmed(rule["bin_id"], mapping_reason=rule["mapping_reason"])

        return self._bin_one_fallback(
            classification_status,
            mapping_reason=self._fallback_reason(
                rulebook_rules[-1],
                classification_status=classification_status,
                visible_condition=normalized_condition,
            ),
        )

    def _has_preparation_path(
        self,
        waste_category: str,
        *,
        has_food_scraps: bool,
        has_liquid: bool,
        visible_condition: str,
    ) -> bool:
        preparation = self.config.preparation
        trigger = preparation.get("trigger", {})
        return (
            preparation.get("enabled") is True
            and waste_category in trigger.get("waste_categories", ())
            and (
                has_food_scraps is True
                or has_liquid is True
                or visible_condition == "food_or_liquid_contaminated"
            )
        )

    @staticmethod
    def _fallback_reason(
        rule: object,
        *,
        classification_status: str,
        visible_condition: str,
    ) -> str:
        reasons = rule.get("mapping_reasons", {}) if hasattr(rule, "get") else {}
        if classification_status == "unknown":
            return reasons.get("unknown", "unknown_category_fallback")
        if classification_status == "low_confidence":
            return reasons.get("low_confidence", "low_confidence_fallback")
        if visible_condition == "food_or_liquid_contaminated":
            return reasons.get("contaminated", "contaminated_without_preparation_fallback")
        if visible_condition == "unknown":
            return reasons.get("unknown_condition", "unknown_cleanliness_fallback")
        return reasons.get("default", rule.get("mapping_reason", "unsupported_material_fallback"))

    def resolve_preparation(
        self,
        *,
        location_id: str,
        waste_category: object,
        has_food_scraps: bool,
        has_liquid: bool = False,
        visible_condition: object = None,
        mapping_decision: MappingDecision,
    ) -> PreparationDecision:
        """Resolve the configured two-step preparation flow.

        The validated food and liquid booleans are treated as one combined
        preparation signal. A
        preparation flow is emitted only when the location has enabled the
        rule, the category is explicitly configured, and the container has a
        confirmed backend mapping.  This prevents an unknown/fallback or
        missing mapping from being presented as a confirmed destination.
        """

        if location_id != self.config.location_id:
            raise LocationMismatchError(
                f"requested location {location_id!r} does not match configured "
                f"location {self.config.location_id!r}"
            )

        preparation = self.config.preparation
        version = preparation.get("version")
        signal = preparation.get("signal", "has_food_scraps")
        visible_condition = normalize_visible_condition(visible_condition)
        preparation_required = (
            has_food_scraps or has_liquid or
            visible_condition == "food_or_liquid_contaminated"
        )
        base_kwargs = {
            "version": version if isinstance(version, str) else None,
            "signal": signal if isinstance(signal, str) else "has_food_scraps",
            "signal_value": preparation_required,
            "guidance_mode": "combined_food_liquid_contamination",
            "has_food_scraps": has_food_scraps,
            "has_liquid": has_liquid,
        }

        if preparation.get("enabled") is not True:
            return PreparationDecision(
                required=False,
                reason="disabled",
                shared_disposal_destination=None,
                container_disposal=None,
                steps=[],
                limitation_th=None,
                **base_kwargs,
            )

        if not preparation_required:
            return PreparationDecision(
                required=False,
                reason="signal_not_present",
                shared_disposal_destination=None,
                container_disposal=None,
                steps=[],
                limitation_th=None,
                **base_kwargs,
            )

        trigger = preparation.get("trigger", {})
        categories = trigger.get("waste_categories", ())
        normalized_category = normalize_waste_category(waste_category)
        if normalized_category not in categories:
            return PreparationDecision(
                required=False,
                reason="category_not_configured",
                shared_disposal_destination=None,
                container_disposal=None,
                steps=[],
                limitation_th=None,
                **base_kwargs,
            )

        if mapping_decision.classification_status != "confirmed" or mapping_decision.mapping_fallback:
            return PreparationDecision(
                required=False,
                reason="mapping_not_confirmed",
                shared_disposal_destination=None,
                container_disposal=None,
                steps=[],
                limitation_th=None,
                **base_kwargs,
            )

        destination = preparation.get("shared_disposal_destination")
        if not isinstance(destination, dict) and not hasattr(destination, "get"):
            return PreparationDecision(
                required=False,
                reason="destination_unavailable",
                shared_disposal_destination=None,
                container_disposal=None,
                steps=[],
                limitation_th=None,
                **base_kwargs,
            )

        shared_destination = {
            "destination_id": destination.get("destination_id"),
            "type": "designated_disposal_point",
            "label_th": destination.get("label_th"),
            # Null is explicit in the API contract: this point is not a bin.
            "bin_id": None,
            "bin_number": None,
            "bin_name": None,
        }
        container_disposal = {
            "type": "mapped_bin",
            "bin_id": mapping_decision.bin_id,
            "bin_number": mapping_decision.bin_number,
            "bin_name": mapping_decision.bin_name,
            "mapping_version": mapping_decision.mapping_version,
        }

        steps: list[dict[str, object]] = []
        for raw_step in preparation.get("steps", ()):
            step = {
                "step": raw_step.get("step"),
                "action_th": raw_step.get("action_th"),
                "instruction_th": raw_step.get("instruction_th"),
                "icon": raw_step.get("icon"),
            }
            if raw_step.get("destination_ref") == "shared_disposal_destination":
                step["destination"] = dict(shared_destination)
            else:
                step["destination"] = dict(container_disposal)
            steps.append(step)

        return PreparationDecision(
            required=True,
            reason="combined_food_liquid_signal",
            shared_disposal_destination=shared_destination,
            container_disposal=container_disposal,
            steps=steps,
            limitation_th=(
                "สัญญาณนี้รวมเศษอาหาร ของเหลว ไขมัน หรือการปนเปื้อน "
                "ระบบจึงยังแยกชนิดของสิ่งที่ต้องเททิ้งไม่ได้"
            ),
            **base_kwargs,
        )

    def _confirmed(self, bin_id: str, *, mapping_reason: str) -> MappingDecision:
        bin_definition = self.config.bins[bin_id]
        return MappingDecision(
            bin_id=bin_definition.bin_id,
            bin_number=bin_definition.bin_number,
            bin_name=bin_definition.bin_name,
            mapping_version=self.config.mapping_version,
            classification_status="confirmed",
            mapping_fallback=False,
            mapping_reason=mapping_reason,
        )

    def _fallback(
        self,
        bin_id: str,
        classification_status: str,
        *,
        mapping_reason: str,
    ) -> MappingDecision:
        bin_definition = self.config.bins[bin_id]
        return MappingDecision(
            bin_id=bin_definition.bin_id,
            bin_number=bin_definition.bin_number,
            bin_name=bin_definition.bin_name,
            mapping_version=self.config.mapping_version,
            classification_status=classification_status,
            mapping_fallback=True,
            mapping_reason=mapping_reason,
        )

    def _infectious_safety_override(self, bin_id: str, *, mapping_reason: str) -> MappingDecision:
        bin_definition = self.config.bins[bin_id]
        return MappingDecision(
            bin_id=bin_definition.bin_id,
            bin_number=bin_definition.bin_number,
            bin_name=bin_definition.bin_name,
            mapping_version=self.config.mapping_version,
            classification_status="low_confidence",
            mapping_fallback=False,
            mapping_reason=mapping_reason,
        )

    def _unavailable(self) -> MappingDecision:
        return self._bin_one_fallback(
            "unavailable",
            mapping_reason="no_configured_category_mapping",
        )

    def _bin_one_fallback(
        self,
        classification_status: str,
        *,
        mapping_reason: str,
    ) -> MappingDecision:
        bin_one_id = next(
            (item.bin_id for item in self.config.bins.values() if item.bin_number == 1 and item.enabled),
            None,
        )
        if bin_one_id is not None:
            return self._fallback(bin_one_id, classification_status, mapping_reason=mapping_reason)
        return MappingDecision(
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=self.config.mapping_version,
            classification_status=classification_status,
            mapping_fallback=False,
            mapping_reason=mapping_reason,
        )
