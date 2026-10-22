from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from internal_api.mapping_config import (
    MappingConfigError,
    load_location_config,
    validate_location_config,
)
from internal_api.mapping_service import LocationMismatchError, MappingService


PROJECT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "locations" / "ICT-001.json"


class MappingFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_location_config("ICT-001")
        cls.service = MappingService(cls.config)
        cls.raw_ict_config = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))

    def test_all_ten_ict_mappings(self) -> None:
        expected = {
            "general": ("ICT-001-B01", 1),
            "pet_bottle": ("ICT-001-B02", 2),
            "plastic": ("ICT-001-B03", 3),
            "paper": ("ICT-001-B04", 4),
            "glass": ("ICT-001-B05", 5),
            "metal": ("ICT-001-B06", 6),
            "ewaste": ("ICT-001-B07", 7),
            "fuel": ("ICT-001-B08", 8),
            "hazardous": ("ICT-001-B09", 9),
            "infectious": ("ICT-001-B10", 10),
        }

        self.assertEqual(set(self.config.category_mapping), set(expected))
        for category, (bin_id, bin_number) in expected.items():
            decision = self.service.resolve(
                location_id="ICT-001",
                waste_category=category,
                confidence=1.0,
            )
            self.assertEqual(decision.classification_status, "confirmed")
            self.assertFalse(decision.mapping_fallback)
            self.assertEqual(decision.bin_id, bin_id)
            self.assertEqual(decision.bin_number, bin_number)

    def test_cardboard_and_paper_share_operational_paper_mapping(self) -> None:
        cardboard = self.service.resolve(
            location_id="ICT-001",
            waste_category="cardboard",
            confidence=1.0,
        )
        paper = self.service.resolve(
            location_id="ICT-001",
            waste_category="paper",
            confidence=1.0,
        )
        self.assertEqual(cardboard.to_dict(), paper.to_dict())
        self.assertEqual(cardboard.bin_id, "ICT-001-B04")

    def test_threshold_is_inclusive_at_zero_point_six(self) -> None:
        below = self.service.resolve(
            location_id="ICT-001",
            waste_category="glass",
            confidence=0.599999,
        )
        at_threshold = self.service.resolve(
            location_id="ICT-001",
            waste_category="glass",
            confidence=0.60,
        )

        self.assertEqual(below.classification_status, "low_confidence")
        self.assertTrue(below.mapping_fallback)
        self.assertEqual(below.bin_number, 1)
        self.assertEqual(at_threshold.classification_status, "confirmed")
        self.assertFalse(at_threshold.mapping_fallback)
        self.assertEqual(at_threshold.bin_number, 5)

    def test_unknown_uses_existing_ict_bin_one_fallback(self) -> None:
        decision = self.service.resolve(
            location_id="ICT-001",
            waste_category="unknown",
            confidence=0.2,
        )
        self.assertEqual(decision.classification_status, "unknown")
        self.assertTrue(decision.mapping_fallback)
        self.assertEqual(decision.bin_id, "ICT-001-B01")
        self.assertEqual(decision.bin_number, 1)

    def test_missing_mapping_is_unavailable_and_does_not_guess(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_ict_config))
        del raw_config["category_mapping"]["ewaste"]
        raw_config["location_id"] = "TEST-MISSING"
        raw_config["mapping_version"] = "TEST-MISSING-v1"
        config = validate_location_config(raw_config, expected_location_id="TEST-MISSING")
        decision = MappingService(config).resolve(
            location_id="TEST-MISSING",
            waste_category="ewaste",
            confidence=0.99,
        )
        self.assertEqual(decision.classification_status, "unavailable")
        self.assertFalse(decision.mapping_fallback)
        self.assertIsNone(decision.bin_id)
        self.assertIsNone(decision.bin_number)

    def test_many_to_one_mapping_is_supported_for_test_location(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_ict_config))
        raw_config["location_id"] = "TEST-MANY"
        raw_config["mapping_version"] = "TEST-MANY-v1"
        raw_config["category_mapping"]["pet_bottle"] = "ICT-001-B03"
        config = validate_location_config(raw_config, expected_location_id="TEST-MANY")
        service = MappingService(config)

        plastic = service.resolve(
            location_id="TEST-MANY",
            waste_category="plastic",
            confidence=1.0,
        )
        pet = service.resolve(
            location_id="TEST-MANY",
            waste_category="pet_bottle",
            confidence=1.0,
        )
        self.assertEqual(plastic.bin_id, pet.bin_id)
        self.assertEqual(plastic.bin_number, pet.bin_number)

    def test_location_mismatch_is_rejected(self) -> None:
        with self.assertRaises(LocationMismatchError):
            self.service.resolve(
                location_id="OTHER-LOCATION",
                waste_category="paper",
                confidence=1.0,
            )

    def test_food_contamination_flow_remains_unresolved_and_inactive(self) -> None:
        flow = self.config.food_contamination_flow
        self.assertFalse(flow["enabled"])
        self.assertEqual(flow["status"], "unresolved")
        self.assertEqual(flow["container_bin_id"], "ICT-001-B03")
        self.assertIsNone(flow["food_scrap_destination"]["bin_id"])

    def test_preparation_uses_shared_unnumbered_destination_and_confirmed_bin(self) -> None:
        mapping = self.service.resolve(
            location_id="ICT-001",
            waste_category="glass",
            confidence=1.0,
        )
        preparation = self.service.resolve_preparation(
            location_id="ICT-001",
            waste_category="glass",
            has_food_scraps=True,
            mapping_decision=mapping,
        )

        self.assertTrue(preparation.required)
        self.assertEqual(preparation.reason, "combined_food_liquid_signal")
        self.assertEqual(
            preparation.shared_disposal_destination["type"],
            "designated_disposal_point",
        )
        self.assertIsNone(preparation.shared_disposal_destination["bin_id"])
        self.assertIsNone(preparation.shared_disposal_destination["bin_number"])
        self.assertEqual(preparation.container_disposal["bin_number"], 5)
        self.assertEqual(preparation.container_disposal["bin_name"], "ขวดแก้ว")
        self.assertEqual(preparation.steps[1]["destination"]["bin_number"], 5)

    def test_preparation_is_not_activated_for_fallback_mapping(self) -> None:
        mapping = self.service.resolve(
            location_id="ICT-001",
            waste_category="unknown",
            confidence=0.1,
        )
        preparation = self.service.resolve_preparation(
            location_id="ICT-001",
            waste_category="unknown",
            has_food_scraps=True,
            mapping_decision=mapping,
        )
        self.assertFalse(preparation.required)
        self.assertEqual(preparation.reason, "category_not_configured")


class ConfigurationValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).resolve().parents[1] / "config" / "locations" / "ICT-001.json"
        cls.raw_config = json.loads(path.read_text(encoding="utf-8"))

    def test_invalid_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "BROKEN.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(MappingConfigError):
                load_location_config("BROKEN", temp_dir)

    def test_unsupported_category_is_rejected(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        raw_config["category_mapping"]["organic"] = "ICT-001-B01"
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

    def test_unknown_category_mapping_is_rejected(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        raw_config["category_mapping"]["unknown"] = "ICT-001-B01"
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

    def test_duplicate_bin_numbers_are_rejected(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        raw_config["bins"][1]["bin_number"] = raw_config["bins"][0]["bin_number"]
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

    def test_duplicate_bin_ids_are_rejected(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        raw_config["bins"][1]["bin_id"] = raw_config["bins"][0]["bin_id"]
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

    def test_unknown_mapping_reference_is_rejected(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        raw_config["category_mapping"]["paper"] = "ICT-001-NOT-A-BIN"
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

    def test_preparation_requires_a_shared_destination_without_a_bin(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        raw_config["preparation"]["shared_disposal_destination"]["bin_number"] = 99
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

    def test_preparation_rejects_unsupported_trigger_category(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        raw_config["preparation"]["trigger"]["waste_categories"].append("organic")
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

    def test_preparation_requires_both_steps_and_destination(self) -> None:
        raw_config = json.loads(json.dumps(self.raw_config))
        del raw_config["preparation"]["shared_disposal_destination"]
        with self.assertRaises(MappingConfigError):
            validate_location_config(raw_config)

