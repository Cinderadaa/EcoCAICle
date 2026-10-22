"""Independent waste-category to physical-bin mapping service."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

try:  # Supports both ``python -m internal_api...`` and the Docker working dir.
    from .mapping_config import LocationMappingConfig, normalize_waste_category
except ImportError:  # pragma: no cover - exercised when running from internal_api/
    from mapping_config import LocationMappingConfig, normalize_waste_category


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

    def to_dict(self) -> dict[str, object]:
        return {
            "bin_id": self.bin_id,
            "bin_number": self.bin_number,
            "bin_name": self.bin_name,
            "mapping_version": self.mapping_version,
            "classification_status": self.classification_status,
            "mapping_fallback": self.mapping_fallback,
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
    ) -> MappingDecision:
        if location_id != self.config.location_id:
            raise LocationMismatchError(
                f"requested location {location_id!r} does not match configured "
                f"location {self.config.location_id!r}"
            )

        normalized_category = normalize_waste_category(waste_category)
        normalized_confidence = _coerce_confidence(confidence)

        if normalized_category == "unknown":
            classification_status = "unknown"
        elif (
            normalized_confidence is None
            or normalized_confidence < self.config.confidence_threshold
        ):
            classification_status = "low_confidence"
        else:
            classification_status = "confirmed"

        if classification_status == "confirmed":
            bin_id = self.config.category_mapping.get(normalized_category)
            if bin_id is None:
                return self._unavailable()
            return self._confirmed(bin_id)

        if (
            self.config.unknown_fallback_enabled
            and classification_status in self.config.unknown_fallback_statuses
            and self.config.unknown_fallback_bin_id is not None
        ):
            return self._fallback(
                self.config.unknown_fallback_bin_id,
                classification_status,
            )

        return MappingDecision(
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=self.config.mapping_version,
            classification_status=classification_status,
            mapping_fallback=False,
        )

    def resolve_preparation(
        self,
        *,
        location_id: str,
        waste_category: object,
        has_food_scraps: bool,
        mapping_decision: MappingDecision,
    ) -> PreparationDecision:
        """Resolve the configured two-step preparation flow.

        The model's boolean is intentionally treated as one combined signal:
        it may indicate food residue, liquid, grease, or contamination.  A
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
        preparation_required = has_food_scraps
        base_kwargs = {
            "version": version if isinstance(version, str) else None,
            "signal": signal if isinstance(signal, str) else "has_food_scraps",
            "signal_value": preparation_required,
            "guidance_mode": "combined_food_liquid_contamination",
            "has_food_scraps": has_food_scraps,
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

        if mapping_decision.classification_status != "confirmed":
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

    def _confirmed(self, bin_id: str) -> MappingDecision:
        bin_definition = self.config.bins[bin_id]
        return MappingDecision(
            bin_id=bin_definition.bin_id,
            bin_number=bin_definition.bin_number,
            bin_name=bin_definition.bin_name,
            mapping_version=self.config.mapping_version,
            classification_status="confirmed",
            mapping_fallback=False,
        )

    def _fallback(self, bin_id: str, classification_status: str) -> MappingDecision:
        bin_definition = self.config.bins[bin_id]
        return MappingDecision(
            bin_id=bin_definition.bin_id,
            bin_number=bin_definition.bin_number,
            bin_name=bin_definition.bin_name,
            mapping_version=self.config.mapping_version,
            classification_status=classification_status,
            mapping_fallback=True,
        )

    def _unavailable(self) -> MappingDecision:
        return MappingDecision(
            bin_id=None,
            bin_number=None,
            bin_name=None,
            mapping_version=self.config.mapping_version,
            classification_status="unavailable",
            mapping_fallback=False,
        )
