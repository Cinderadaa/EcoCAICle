import unittest
from pathlib import Path

from internal_api import app as api
from internal_api.mapping_config import load_location_config
from internal_api.mapping_service import MappingService


ROOT = Path(__file__).resolve().parents[2]


def classification_response(
    name,
    category,
    subtype,
    *,
    condition="clean_dry",
    confidence=0.92,
    resin="unknown",
    alternatives=(),
):
    primary = {
        "object_name_th": name,
        "waste_category": category,
        "material_subtype": subtype,
        "confidence": confidence,
    }
    candidates = [primary, *alternatives]
    while len(candidates) < 3:
        candidates.append({
            "object_name_th": "ขยะที่ไม่สามารถระบุได้",
            "waste_category": "unknown",
            "material_subtype": "unknown",
            "confidence": 0.1,
        })
    import json

    return json.dumps({
        **primary,
        "resin_identification_code": resin,
        "visible_condition": condition,
        "top_candidates": candidates[:3],
        "has_food_scraps": False,
        "has_liquid": False,
    }, ensure_ascii=False)


class PetPromptClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_location_config(
            location_id="ICT-001",
            config_dir=ROOT / "internal_api" / "config" / "locations",
        )
        cls.mapper = MappingService(cls.config)

    def parse_and_map(self, raw):
        parsed = api._parse_model_response(raw)
        category, _, subtype, _, condition, has_food, has_liquid, confidence, _, _ = parsed
        decision = self.mapper.resolve(
            location_id="ICT-001",
            waste_category=category,
            confidence=confidence,
            material_subtype=subtype,
            visible_condition=condition,
            has_food_scraps=has_food,
            has_liquid=has_liquid,
        )
        return parsed, decision

    def test_clear_pet_beverage_bottle_maps_to_bin_2(self):
        parsed, decision = self.parse_and_map(classification_response(
            "ขวดน้ำดื่มพลาสติก", "pet_bottle", "pet_bottle",
        ))
        self.assertEqual(parsed[0:3:2], ("pet_bottle", "pet_bottle"))
        self.assertEqual(decision.bin_number, 2)
        self.assertEqual(decision.mapping_reason, "clean_dry_recyclable_material")

    def test_green_or_opaque_pet_bottle_needs_no_visible_resin_mark(self):
        for name in ("ขวดน้ำดื่มพลาสติกสีเขียว", "ขวดน้ำดื่มพลาสติกสีทึบติดฉลาก"):
            with self.subTest(name=name):
                parsed, decision = self.parse_and_map(classification_response(
                    name, "pet_bottle", "pet_bottle", resin="unknown",
                ))
                self.assertEqual(parsed[0], "pet_bottle")
                self.assertEqual(parsed[2], "pet_bottle")
                self.assertEqual(parsed[3], "unknown")
                self.assertEqual(decision.bin_number, 2)

    def test_supported_pet_candidate_replaces_unknown_primary_without_inflating_confidence(self):
        raw = classification_response(
            "ขวดเครื่องดื่ม", "unknown", "unknown", confidence=0.20,
            alternatives=(
                {
                    "object_name_th": "ขวดเครื่องดื่มพลาสติก",
                    "waste_category": "pet_bottle",
                    "material_subtype": "pet_bottle",
                    "confidence": 0.15,
                },
                {
                    "object_name_th": "ขยะที่ไม่สามารถระบุได้",
                    "waste_category": "unknown",
                    "material_subtype": "unknown",
                    "confidence": 0.10,
                },
            ),
        )
        parsed, decision = self.parse_and_map(raw)
        self.assertEqual(parsed[0], "pet_bottle")
        self.assertEqual(parsed[2], "pet_bottle")
        self.assertEqual(parsed[7], 0.15)
        self.assertEqual(parsed[9][0]["waste_category"], "pet_bottle")
        # Existing confidence policy remains authoritative; the parser does not
        # raise a weak alternative to the Bin 2 confidence requirement.
        self.assertEqual(decision.bin_number, 1)

    def test_glass_metal_tumbler_and_ambiguous_bottles_are_not_forced_to_pet(self):
        cases = (
            ("ขวดเครื่องดื่มแก้ว", "glass", "glass", "glass"),
            ("ขวดน้ำสแตนเลส", "metal", "metal", "metal"),
            ("แก้วเก็บอุณหภูมิ", "unknown", "unknown", "unknown"),
            ("ขวด", "unknown", "unknown", "unknown"),
        )
        for name, category, subtype, expected in cases:
            with self.subTest(name=name):
                alternatives = ({
                    "object_name_th": "ขวดน้ำดื่มพลาสติก",
                    "waste_category": "pet_bottle",
                    "material_subtype": "pet_bottle",
                    "confidence": 0.40,
                },) if category == "unknown" else ()
                parsed, _ = self.parse_and_map(classification_response(
                    name, category, subtype, resin="1", alternatives=alternatives,
                ))
                self.assertEqual(parsed[0], expected)
                self.assertEqual(parsed[3], "1")

    def test_unmarked_ambiguous_bottle_does_not_gain_a_resin_code_or_pet_route(self):
        parsed, decision = self.parse_and_map(classification_response(
            "ขวด", "unknown", "unknown", resin="unknown",
        ))
        self.assertEqual(parsed[0], "unknown")
        self.assertEqual(parsed[3], "unknown")
        self.assertEqual(decision.bin_number, 1)


if __name__ == "__main__":
    unittest.main()
