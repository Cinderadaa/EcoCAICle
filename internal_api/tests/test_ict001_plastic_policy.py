import unittest
from pathlib import Path

from internal_api.mapping_config import load_location_config
from internal_api.mapping_service import MappingService


ROOT = Path(__file__).resolve().parents[2]


class ICT001PlasticPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_location_config(
            location_id="ICT-001",
            config_dir=ROOT / "internal_api" / "config" / "locations",
        )
        cls.mapper = MappingService(cls.config)

    def resolve(self, category, subtype, condition="clean_dry", **signals):
        return self.mapper.resolve(
            location_id="ICT-001", waste_category=category, confidence=0.9,
            object_name_th="fixture item", material_subtype=subtype,
            visible_condition=condition, **signals,
        )

    def test_pet_beverage_bottle_routes_to_bin_2(self):
        decision = self.resolve("pet_bottle", "pet_bottle")
        self.assertEqual(decision.bin_number, 2)
        self.assertEqual(decision.classification_status, "confirmed")

    def test_carrier_bag_and_film_route_to_bin_3(self):
        for name in (
            "carrier bag", "soup bag", "ice bag", "produce bag", "cling film",
            "shrink film", "stretch film", "bubble wrap", "zip-lock bag",
        ):
            with self.subTest(name=name):
                decision = self.mapper.resolve(
                    location_id="ICT-001", waste_category="plastic", confidence=0.9,
                    object_name_th=name, material_subtype="stretch_plastic",
                    visible_condition="clean_dry",
                )
                self.assertEqual(decision.bin_number, 3)

    def test_bin_3_requires_the_flexible_plastic_subtype(self):
        for subtype in (
            "rigid_plastic", "plastic_straw", "plastic_spoon", "pet_bottle",
            "unknown",
        ):
            with self.subTest(subtype=subtype):
                category = "pet_bottle" if subtype == "pet_bottle" else "plastic"
                decision = self.resolve(category, subtype)
                self.assertNotEqual(decision.bin_number, 3)

    def test_clean_rigid_plastic_routes_to_bin_8_with_specific_reason(self):
        decision = self.resolve("plastic", "rigid_plastic")
        self.assertEqual(decision.bin_number, 8)
        self.assertEqual(decision.mapping_reason, "clean_dry_rigid_plastic_rdf")

    def test_clean_straw_and_cutlery_remain_approved_rdf(self):
        for subtype, reason in (
            ("plastic_straw", "clean_dry_plastic_straw_rdf"),
            ("plastic_spoon", "clean_dry_plastic_spoon_rdf"),
        ):
            with self.subTest(subtype=subtype):
                decision = self.resolve("plastic", subtype)
                self.assertEqual(decision.bin_number, 8)
                self.assertEqual(decision.mapping_reason, reason)

    def test_contaminated_or_unknown_condition_rigid_plastic_falls_back_to_bin_1(self):
        for condition, signals in (
            ("food_or_liquid_contaminated", {}),
            ("unknown", {}),
            ("clean_dry", {"has_liquid": True}),
        ):
            with self.subTest(condition=condition, signals=signals):
                decision = self.resolve("plastic", "rigid_plastic", condition, **signals)
                self.assertEqual(decision.bin_number, 1)
                self.assertTrue(decision.mapping_fallback)

    def test_unknown_cleanliness_and_unmapped_material_fall_back_to_bin_1(self):
        for category, subtype, condition in (
            ("plastic", "rigid_plastic", "unknown"),
            ("plastic", "unknown", "clean_dry"),
            ("unknown", "unknown", "unknown"),
        ):
            with self.subTest(category=category, subtype=subtype, condition=condition):
                decision = self.resolve(category, subtype, condition)
                self.assertEqual(decision.bin_number, 1)
                self.assertTrue(decision.mapping_fallback)

    def test_policy_versions_are_bumped(self):
        self.assertEqual(self.config.mapping_version, "ICT-10-bin-v5")
        self.assertEqual(self.config.mapping_rulebook["version"], "ICT-physical-bin-rules-v3")
        self.assertEqual(self.config.confidence_threshold, 0.7)


class ICT001RdfLowConfidencePolicyTest(unittest.TestCase):
    """Narrow 0.60 RDF threshold for clean/dry straw and rigid plastic only."""

    @classmethod
    def setUpClass(cls):
        cls.config = load_location_config(
            location_id="ICT-001",
            config_dir=ROOT / "internal_api" / "config" / "locations",
        )
        cls.mapper = MappingService(cls.config)

    def resolve(self, category, subtype, confidence, condition="clean_dry"):
        return self.mapper.resolve(
            location_id="ICT-001", waste_category=category, confidence=confidence,
            object_name_th="fixture item", material_subtype=subtype,
            visible_condition=condition,
        )

    def test_clean_straw_and_rigid_plastic_at_0_60_and_0_65_route_to_bin_8(self):
        for subtype, reason in (
            ("plastic_straw", "clean_dry_plastic_straw_rdf"),
            ("rigid_plastic", "clean_dry_rigid_plastic_rdf"),
        ):
            for confidence in (0.60, 0.65):
                with self.subTest(subtype=subtype, confidence=confidence):
                    decision = self.resolve("plastic", subtype, confidence)
                    self.assertEqual(decision.bin_number, 8)
                    self.assertEqual(decision.classification_status, "confirmed")
                    self.assertFalse(decision.mapping_fallback)
                    self.assertEqual(decision.mapping_reason, reason)

    def test_clean_straw_and_rigid_plastic_below_0_60_fall_back_to_bin_1(self):
        for subtype in ("plastic_straw", "rigid_plastic"):
            with self.subTest(subtype=subtype):
                decision = self.resolve("plastic", subtype, 0.59)
                self.assertEqual(decision.bin_number, 1)
                self.assertEqual(decision.classification_status, "low_confidence")
                self.assertTrue(decision.mapping_fallback)
                self.assertEqual(decision.mapping_reason, "low_confidence_fallback")

    def test_contaminated_or_unknown_condition_does_not_use_lowered_threshold(self):
        for subtype in ("plastic_straw", "rigid_plastic"):
            for condition in ("food_or_liquid_contaminated", "unknown"):
                with self.subTest(subtype=subtype, condition=condition):
                    decision = self.resolve("plastic", subtype, 0.65, condition=condition)
                    self.assertEqual(decision.bin_number, 1)
                    self.assertTrue(decision.mapping_fallback)

    def test_clean_dry_pet_bottle_at_0_60_uses_configured_local_rule(self):
        decision = self.resolve("pet_bottle", "pet_bottle", 0.60)
        self.assertEqual(decision.bin_number, 2)
        self.assertEqual(decision.classification_status, "confirmed")
        self.assertFalse(decision.mapping_fallback)
        self.assertEqual(decision.mapping_reason, "pet_bottle_local_confidence_threshold")
        self.assertEqual(decision.mapping_rule_name, "pet_bottle_local_confidence_threshold")
        self.assertEqual(decision.confidence_threshold_used, 0.60)
        self.assertEqual(decision.to_dict()["confidence_threshold_used"], 0.60)

        from internal_api.scan_history import SCAN_METADATA_FIELDS
        self.assertIn("mapping_rule_name", SCAN_METADATA_FIELDS)
        self.assertIn("confidence_threshold_used", SCAN_METADATA_FIELDS)

    def test_clean_dry_pet_bottle_below_0_60_uses_global_fallback(self):
        decision = self.resolve("pet_bottle", "pet_bottle", 0.59)
        self.assertEqual(decision.bin_number, 1)
        self.assertEqual(decision.classification_status, "low_confidence")
        self.assertTrue(decision.mapping_fallback)
        self.assertIsNone(decision.mapping_rule_name)
        self.assertIsNone(decision.confidence_threshold_used)

    def test_contaminated_pet_bottle_at_0_90_falls_back(self):
        for condition, signals in (
            ("food_or_liquid_contaminated", {}),
            ("clean_dry", {"has_food_scraps": True}),
            ("clean_dry", {"has_liquid": True}),
            ("unknown", {}),
        ):
            with self.subTest(condition=condition, signals=signals):
                decision = self.mapper.resolve(
                    location_id="ICT-001", waste_category="pet_bottle", confidence=0.90,
                    object_name_th="fixture item", material_subtype="pet_bottle",
                    visible_condition=condition, **signals,
                )
                self.assertEqual(decision.bin_number, 1)
                self.assertTrue(decision.mapping_fallback)
                self.assertIsNone(decision.mapping_rule_name)

    def test_clean_pet_category_with_non_pet_subtype_does_not_use_pet_override(self):
        decision = self.resolve("pet_bottle", "rigid_plastic", 0.90)
        self.assertEqual(decision.bin_number, 1)
        self.assertTrue(decision.mapping_fallback)
        self.assertIsNone(decision.mapping_rule_name)

    def test_rigid_plastic_at_0_60_is_not_mapped_by_pet_override(self):
        decision = self.resolve("plastic", "rigid_plastic", 0.60)
        self.assertEqual(decision.bin_number, 8)
        self.assertEqual(decision.mapping_reason, "clean_dry_rigid_plastic_rdf")
        self.assertIsNone(decision.mapping_rule_name)
        self.assertIsNone(decision.confidence_threshold_used)

    def test_stretch_plastic_at_0_60_is_not_mapped_by_pet_override(self):
        decision = self.resolve("plastic", "stretch_plastic", 0.60)
        self.assertEqual(decision.bin_number, 1)
        self.assertTrue(decision.mapping_fallback)
        self.assertIsNone(decision.mapping_rule_name)
        self.assertIsNone(decision.confidence_threshold_used)

    def test_pet_bottle_at_global_threshold_still_maps_to_bin_2(self):
        confirmed = self.resolve("pet_bottle", "pet_bottle", 0.7)
        self.assertEqual(confirmed.bin_number, 2)

    def test_config_rejects_expanding_the_pet_confidence_rule_to_generic_plastic(self):
        import json
        import tempfile

        from internal_api.mapping_config import MappingConfigError

        config_path = ROOT / "internal_api" / "config" / "locations" / "ICT-001.json"
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        raw["mapping_rulebook"]["confidence_overrides"][0]["waste_category"] = "plastic"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ICT-001.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(MappingConfigError):
                load_location_config(location_id="ICT-001", config_dir=Path(tmp))

    def test_stretch_plastic_keeps_global_threshold(self):
        decision = self.resolve("plastic", "stretch_plastic", 0.65)
        self.assertEqual(decision.bin_number, 1)
        self.assertEqual(decision.classification_status, "low_confidence")

        confirmed = self.resolve("plastic", "stretch_plastic", 0.7)
        self.assertEqual(confirmed.bin_number, 3)

    def test_generic_low_confidence_plastic_keeps_global_threshold(self):
        decision = self.resolve("plastic", "unknown", 0.65)
        self.assertEqual(decision.bin_number, 1)
        self.assertEqual(decision.classification_status, "low_confidence")
        self.assertTrue(decision.mapping_fallback)

    def test_other_rdf_subtypes_keep_global_threshold(self):
        for subtype in ("plastic_spoon", "snack_sachet", "multilayer_packaging"):
            with self.subTest(subtype=subtype):
                decision = self.resolve("plastic", subtype, 0.65)
                self.assertEqual(decision.bin_number, 1)
                self.assertEqual(decision.classification_status, "low_confidence")

    def test_config_rejects_threshold_outside_approved_subtypes(self):
        import json
        import tempfile

        from internal_api.mapping_config import MappingConfigError

        raw = json.loads((ROOT / "internal_api" / "config" / "locations" / "ICT-001.json").read_text(encoding="utf-8"))
        rdf = next(rule for rule in raw["mapping_rulebook"]["ordered_rules"] if rule["id"] == "rdf_fuel")
        rdf["minimum_confidence_by_material_subtype"]["snack_sachet"] = 0.6
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ICT-001.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(MappingConfigError):
                load_location_config(location_id="ICT-001", config_dir=Path(tmp))


if __name__ == "__main__":
    unittest.main()
