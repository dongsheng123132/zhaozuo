from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shared.profile import ProfileError, load_profile, resolve_action, validate_profile


ROOT = Path(__file__).resolve().parents[1]


class ProfileContractTests(unittest.TestCase):
    def test_all_example_profiles_are_valid(self) -> None:
        profile_paths = sorted((ROOT / "profiles").glob("*/*.action-profile.json"))
        self.assertGreaterEqual(len(profile_paths), 2)
        for profile_path in profile_paths:
            with self.subTest(profile=profile_path.name):
                errors = validate_profile(load_profile(profile_path))
                self.assertEqual(errors, [])

    def test_chrome_action_resolves_url(self) -> None:
        profile = load_profile(ROOT / "profiles/chrome/open-url.action-profile.json")
        action = resolve_action(
            profile, "browser.open_url", {"url": "https://example.com"}
        )
        typed = next(step for step in action["steps"] if step["id"] == "type_url")
        self.assertEqual(typed["args"]["text"], "https://example.com")

    def test_missing_required_input_is_rejected(self) -> None:
        profile = load_profile(ROOT / "profiles/chrome/open-url.action-profile.json")
        with self.assertRaises(ProfileError):
            resolve_action(profile, "browser.open_url", {})

    def test_invalid_json_reports_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid = Path(temp_dir) / "invalid.json"
            invalid.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ProfileError, "line 1"):
                load_profile(invalid)

    def test_schema_file_is_valid_json(self) -> None:
        schema_path = ROOT / "profiles/schema/compatibility-action-profile.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")


if __name__ == "__main__":
    unittest.main()
