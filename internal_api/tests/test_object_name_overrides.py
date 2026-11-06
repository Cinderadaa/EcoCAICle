import unittest
from pathlib import Path

from internal_api.mapping_config import configured_object_name_override, load_location_config
from internal_api.mapping_service import MappingService


ROOT = Path(__file__).resolve().parents[2]


class ObjectNameOverridesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_location_config(
            location_id="ICT-001",
            config_dir=ROOT / "internal_api" / "config" / "locations",
        )
        cls.mapper = MappingService(cls.config)

    def resolve(self, name, category, subtype, confidence, condition="clean_dry"):
        return self.mapper.resolve(
            location_id="ICT-001",
            waste_category=category,
            confidence=confidence,
            object_name_th=name,
            material_subtype=subtype,
            visible_condition=condition,
        )

    def test_phone_aliases_override_unknown_category_and_bypass_generic_threshold(self):
        for name in (
            "โทรศัพท์มือถือ",
            "โทรศัพท์มือถือในเคสสีดำ",
            "มือถือจอแตก",
            "  โทรศัพท์มือถือ   ในเคสสีดำ  ",
        ):
            with self.subTest(name=name):
                decision = self.resolve(name, "unknown", "unknown", 0.2)
                self.assertEqual(decision.bin_number, 7)
                self.assertEqual(decision.mapping_reason, "local_object_name_override_mobile_phone")
                self.assertTrue(decision.object_name_override_applied)
                self.assertEqual(decision.classification_status, "low_confidence")
                self.assertFalse(decision.mapping_fallback)
                self.assertTrue(decision.to_dict()["object_name_override_applied"])

    def test_existing_infectious_aliases_still_route_to_bin_10(self):
        for name, confidence in (("หน้ากากอนามัยใช้แล้ว", 0.6), ("ชุดตรวจ ATK", 0.9)):
            with self.subTest(name=name):
                decision = self.resolve(name, "unknown", "unknown", confidence)
                self.assertEqual(decision.bin_number, 10)
                self.assertEqual(decision.mapping_reason, "local_object_name_override_infectious")
                self.assertTrue(decision.object_name_override_applied)
                self.assertFalse(decision.mapping_fallback)

    def test_computer_mouse_aliases_use_their_configured_half_confidence_floor(self):
        cases = (
            ("เมาส์คอมพิวเตอร์", 0.6),
            ("เมาส์", 0.5),
        )
        for name, confidence in cases:
            with self.subTest(name=name, confidence=confidence):
                decision = self.resolve(name, "ewaste", "e_waste", confidence)
                self.assertEqual(decision.bin_number, 7)
                self.assertFalse(decision.mapping_fallback)
                self.assertEqual(
                    decision.mapping_reason,
                    "local_object_name_override_computer_mouse",
                )
                self.assertTrue(decision.object_name_override_applied)
                self.assertEqual(decision.classification_status, "low_confidence")

    def test_generic_ewaste_and_generic_electronics_still_fall_back(self):
        cases = (
            ("อุปกรณ์อิเล็กทรอนิกส์", "ewaste", "e_waste", 0.6),
            ("อุปกรณ์อิเล็กทรอนิกส์", "unknown", "unknown", 0.6),
        )
        for name, category, subtype, confidence in cases:
            with self.subTest(name=name, category=category):
                decision = self.resolve(name, category, subtype, confidence)
                self.assertEqual(decision.bin_number, 1)
                self.assertTrue(decision.mapping_fallback)
                self.assertFalse(decision.object_name_override_applied)

    def test_computer_mouse_below_its_override_floor_falls_back(self):
        decision = self.resolve("เมาส์", "ewaste", "e_waste", 0.49)
        self.assertEqual(decision.bin_number, 1)
        self.assertTrue(decision.mapping_fallback)
        self.assertFalse(decision.object_name_override_applied)
        self.assertEqual(decision.mapping_reason, "low_confidence_fallback")

    def test_infectious_alias_below_its_existing_safety_floor_falls_back(self):
        decision = self.resolve("หน้ากากอนามัยใช้แล้ว", "unknown", "unknown", 0.4)
        self.assertEqual(decision.bin_number, 1)
        self.assertFalse(decision.object_name_override_applied)
        self.assertTrue(decision.mapping_fallback)

    def test_generic_electronics_and_ambiguous_unknown_names_stay_on_bin_1(self):
        for name, confidence in (("อุปกรณ์อิเล็กทรอนิกส์", 0.9), ("วัตถุไม่ทราบชนิด", 0.2)):
            with self.subTest(name=name):
                self.assertIsNone(configured_object_name_override(name, self.config.object_name_overrides))
                decision = self.resolve(name, "unknown", "unknown", confidence, condition="unknown")
                self.assertEqual(decision.bin_number, 1)
                self.assertFalse(decision.object_name_override_applied)
                self.assertTrue(decision.mapping_fallback)

    def test_pet_bottle_stretch_film_rdf_and_contaminated_plastic_rules_remain_unchanged(self):
        cases = (
            ("ขวดน้ำดื่มพลาสติก", "pet_bottle", "pet_bottle", "clean_dry", 2),
            ("ฟิล์มพลาสติกยืด", "plastic", "stretch_plastic", "clean_dry", 3),
            ("ภาชนะพลาสติกแข็ง", "plastic", "rigid_plastic", "clean_dry", 8),
            ("ภาชนะพลาสติกแข็ง", "plastic", "rigid_plastic", "food_or_liquid_contaminated", 1),
            ("ภาชนะพลาสติกแข็ง", "plastic", "rigid_plastic", "unknown", 1),
        )
        for name, category, subtype, condition, expected_bin in cases:
            with self.subTest(category=category, subtype=subtype, condition=condition):
                decision = self.resolve(name, category, subtype, 0.9, condition)
                self.assertEqual(decision.bin_number, expected_bin)
                self.assertFalse(decision.object_name_override_applied)


if __name__ == "__main__":
    unittest.main()
