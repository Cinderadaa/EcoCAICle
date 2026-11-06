import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = ROOT / "internal_api" / "app.py"
REAR_PHONE_IMAGE = Path(
    "/var/lib/ecoaicle-data/images/2026/11/05/949e0c04-2886-46a7-9904-d3858028e1af.jpg"
)


def prompt_constants():
    tree = ast.parse(APP_SOURCE.read_text())
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {"PROMPT", "PROMPT_VERSION"}:
                if target.id == "PROMPT_VERSION":
                    constants[target.id] = ast.get_source_segment(APP_SOURCE.read_text(), node.value)
                    continue
                try:
                    constants[target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    return constants


class SmartphonePromptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        constants = prompt_constants()
        cls.prompt = constants["PROMPT"]
        cls.version_expression = constants["PROMPT_VERSION"]

    def test_prompt_version_is_bumped(self):
        self.assertIn("qwen-waste-classification-v11", self.version_expression)

    @unittest.skipUnless(REAR_PHONE_IMAGE.is_file(), "stored request image is not available")
    def test_named_rear_facing_black_case_phone_image_is_available(self):
        self.assertTrue(REAR_PHONE_IMAGE.is_file())
        image_bytes = REAR_PHONE_IMAGE.read_bytes()
        self.assertGreater(len(image_bytes), 0)
        self.assertTrue(image_bytes.startswith(b"\xff\xd8"))
        self.assertIn("A rear-facing smartphone in a black case", self.prompt)
        self.assertIn('name it exactly "โทรศัพท์มือถือ"', self.prompt)
        self.assertIn('set waste_category to "ewaste"', self.prompt)
        self.assertIn('set material_subtype to "e_waste"', self.prompt)

    def test_front_facing_phone_screen_is_phone_specific_evidence(self):
        self.assertIn("A front-facing smartphone with its screen visible", self.prompt)

    def test_generic_rectangular_container_or_black_object_stays_unknown(self):
        self.assertIn("plastic container", self.prompt)
        self.assertIn("with no phone-specific feature is not enough", self.prompt)
        self.assertIn('keep category/subtype "unknown"', self.prompt)
        self.assertIn("Do not infer ewaste from rectangular shape, black color", self.prompt)


if __name__ == "__main__":
    unittest.main()
