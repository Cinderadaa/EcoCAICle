import json
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

    def load_reviewed_beverage_bottle_fixture(self):
        fixture_path = ROOT / "internal_api" / "tests" / "fixtures" / (
            "09f5ffc8-9339-4d50-99d7-d7909ce1a0a2.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        image_path = ROOT / fixture["image_path"]
        self.assertTrue(fixture["reviewer_confirmed"])
        self.assertTrue(image_path.is_file())
        self.assertTrue(image_path.read_bytes().startswith(b"\xff\xd8"))
        return fixture

    def test_reviewed_request_fixture_classifies_pet_beverage_bottle_to_bin_2(self):
        fixture = self.load_reviewed_beverage_bottle_fixture()
        expected = fixture["expected"]
        self.assertIn(
            'set waste_category="pet_bottle" and material_subtype="pet_bottle"',
            api.PROMPT,
        )
        self.assertIn(
            "Reserve plastic/rigid_plastic for rigid plastic containers that are not beverage bottles",
            api.PROMPT,
        )

        parsed, decision = self.parse_and_map(classification_response(
            expected["object_name_th"],
            expected["waste_category"],
            expected["material_subtype"],
            condition=expected["visible_condition"],
            confidence=expected["confidence"],
        ))
        self.assertEqual(parsed[0], "pet_bottle")
        self.assertEqual(parsed[2], "pet_bottle")
        self.assertEqual(decision.bin_number, expected["bin_number"])
        self.assertFalse(decision.mapping_fallback)

    def test_historical_rigid_plastic_response_is_not_promoted_by_bottle_name(self):
        fixture = self.load_reviewed_beverage_bottle_fixture()
        old = fixture["prior_model_output"]
        parsed, decision = self.parse_and_map(classification_response(
            old["object_name_th"], old["waste_category"], old["material_subtype"],
            condition=old["visible_condition"], confidence=old["confidence"],
        ))
        self.assertEqual(parsed[0], "plastic")
        self.assertEqual(parsed[2], "rigid_plastic")
        self.assertEqual(decision.bin_number, old["bin_number"])

    def test_pet_beverage_bottle_contaminated_or_unknown_condition_stays_in_bin_1(self):
        for condition in ("food_or_liquid_contaminated", "unknown"):
            with self.subTest(condition=condition):
                parsed, decision = self.parse_and_map(classification_response(
                    "ขวดพลาสติกแข็งบรรจุเครื่องดื่ม", "pet_bottle", "pet_bottle",
                    condition=condition, confidence=0.7,
                ))
                self.assertEqual(parsed[0:3:2], ("pet_bottle", "pet_bottle"))
                self.assertEqual(decision.bin_number, 1)
                self.assertTrue(decision.mapping_fallback)

    def test_non_beverage_rigid_plastic_container_remains_bin_8(self):
        parsed, decision = self.parse_and_map(classification_response(
            "ภาชนะพลาสติกแข็ง", "plastic", "rigid_plastic",
            condition="clean_dry", confidence=0.7,
        ))
        self.assertEqual(parsed[0], "plastic")
        self.assertEqual(parsed[2], "rigid_plastic")
        self.assertEqual(decision.bin_number, 8)

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
