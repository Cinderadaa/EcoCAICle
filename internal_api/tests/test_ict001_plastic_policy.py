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
        for name in ("carrier bag", "cling film", "bubble wrap", "zip-lock bag"):
            with self.subTest(name=name):
                decision = self.mapper.resolve(
                    location_id="ICT-001", waste_category="plastic", confidence=0.9,
                    object_name_th=name, material_subtype="stretch_plastic",
                    visible_condition="clean_dry",
                )
                self.assertEqual(decision.bin_number, 3)

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

    def test_policy_versions_are_bumped(self):
        self.assertEqual(self.config.mapping_version, "ICT-10-bin-v4")
        self.assertEqual(self.config.mapping_rulebook["version"], "ICT-physical-bin-rules-v2")


if __name__ == "__main__":
    unittest.main()
