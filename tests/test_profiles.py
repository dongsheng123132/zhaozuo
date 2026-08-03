from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shared.profile import (
    CURRENT_VERSION,
    ProfileError,
    load_profile,
    resolve_action,
    validate_profile,
)


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
        for name in (
            "compatibility-action-profile.schema.json",
            "action-profile-v1.schema.json",
        ):
            with self.subTest(schema=name):
                schema = json.loads(
                    (ROOT / "profiles/schema" / name).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
                )

    def test_examples_are_on_the_frozen_v1_format(self) -> None:
        for profile_path in sorted((ROOT / "profiles").glob("*/*.action-profile.json")):
            with self.subTest(profile=profile_path.name):
                self.assertEqual(
                    load_profile(profile_path)["profile_version"], CURRENT_VERSION
                )

    def _validated(self, **overrides: object) -> dict:
        """A v1 profile that claims `validated` —— 校验器必须逐条要证据。"""

        profile = {
            "profile_version": "1.0",
            "profile_id": "windows.demo.thing",
            "kind": "external_ui_adapter",
            "status": "validated",
            "compatibility": {"app_versions": ["4.0.1"]},
            "evidence": {
                "demonstrations": 3,
                "regression_runs": [{"ran_at": "2026-08-04T00:00:00Z", "result": "pass"}],
                "promoted_by": "someone",
            },
            "application": {
                "id": "com.demo", "name": "Demo", "platform": "windows",
                "discovery": {"process_names": ["demo.exe"]},
            },
            "actions": {
                "demo.do_thing": {
                    "title": "做一件事",
                    "input_schema": {"type": "object", "properties": {}, "required": []},
                    "risk": "high",
                    "confirmation": "before_effect",
                    "steps": [{
                        "id": "step_001",
                        "kind": "pointer.click",
                        "locator": {"window": {"process": "demo.exe", "class_name": "D"}},
                        "effect": {
                            "effect_id": "message.send", "kind": "send_message",
                            "confirmation": "always",
                        },
                        "target_assertion": {"min_confidence": "strong"},
                    }],
                    "success_evidence": [{"kind": "message.outgoing_visible"}],
                }
            },
        }
        profile.update(overrides)  # type: ignore[arg-type]
        return profile

    def test_well_formed_validated_profile_passes(self) -> None:
        self.assertEqual(validate_profile(self._validated()), [])

    def test_validator_rejects_unearned_validated_claims(self) -> None:
        """『做完』必须拿得出证据。拿不出就不该被叫做 validated。"""

        import copy

        def first_error(profile: dict) -> str:
            errors = validate_profile(profile)
            self.assertTrue(errors, "本该被拒绝的档案通过了校验")
            return " ".join(errors)

        # 没声明兼容范围
        self.assertIn("compatibility.app_versions",
                      first_error(self._validated(compatibility={})))

        # 没有通过的回归记录
        no_pass = self._validated()
        no_pass["evidence"]["regression_runs"] = [
            {"ran_at": "2026-08-04T00:00:00Z", "result": "fail"}
        ]
        self.assertIn("passing regression run", first_error(no_pass))

        # 录制器自封（没有提升人）
        self_promoted = self._validated()
        del self_promoted["evidence"]["promoted_by"]
        self.assertIn("promoted_by", first_error(self_promoted))

        # 成功证据只有窗口标题 —— 发成功和发失败长得一样
        weak = self._validated()
        action = weak["actions"]["demo.do_thing"]
        action["success_evidence"] = [{"kind": "window.title_contains", "expected": "微信"}]
        self.assertIn("beyond window title", first_error(weak))

        # 对外动作没有目标断言
        untargeted = copy.deepcopy(self._validated())
        del untargeted["actions"]["demo.do_thing"]["steps"][0]["target_assertion"]
        self.assertIn("target_assertion", first_error(untargeted))

        # 只靠绝对坐标定位
        coords_only = copy.deepcopy(self._validated())
        coords_only["actions"]["demo.do_thing"]["steps"][0]["locator"] = {
            "fallback_absolute": [100, 200]
        }
        self.assertIn("absolute", first_error(coords_only))

        # Action ID 不合语法（中文目标曾经在这里塌陷）
        bad_id = copy.deepcopy(self._validated())
        bad_id["actions"] = {"做一件事": bad_id["actions"]["demo.do_thing"]}
        self.assertIn("action id must match", first_error(bad_id))


if __name__ == "__main__":
    unittest.main()
