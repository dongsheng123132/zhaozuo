from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop_app.replay import ReplayEngine
from desktop_app.workflow import build_profile, save_recording
from shared.profile import resolve_action, validate_profile


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            {
                "kind": "pointer.click",
                "button": "left",
                "absolute": [420, 220],
                "relative": {"x": 0.5, "y": 0.25},
                "window": {
                    "hwnd": 10,
                    "title": "示例窗口",
                    "class_name": "Example",
                    "rect": [0, 0, 840, 880],
                },
                "offset_ms": 100,
            },
            {
                "kind": "text.input",
                "placeholder": "text_1",
                "key_count": 6,
                "privacy": "redacted",
                "window": {
                    "hwnd": 10,
                    "title": "示例窗口",
                    "class_name": "Example",
                    "rect": [0, 0, 840, 880],
                },
                "offset_ms": 500,
            },
            {
                "kind": "keyboard.press",
                "key": "ENTER",
                "window": {
                    "hwnd": 10,
                    "title": "示例窗口",
                    "class_name": "Example",
                    "rect": [0, 0, 840, 880],
                },
                "offset_ms": 900,
            },
        ]

    def test_recording_builds_valid_profile_with_redacted_input(self) -> None:
        profile = build_profile(
            "session-1", "搜索客户", self.events, "搜索结果"
        )
        self.assertEqual(validate_profile(profile), [])
        action = next(iter(profile["actions"].values()))
        self.assertIn("text_1", action["input_schema"]["properties"])
        self.assertNotIn("secret", str(profile))
        resolved = resolve_action(
            profile, next(iter(profile["actions"])), {"text_1": "新客户"}
        )
        self.assertEqual(resolved["steps"][1]["args"]["text"], "新客户")

    def test_plan_mode_never_executes(self) -> None:
        profile = build_profile(
            "session-1", "搜索客户", self.events, "搜索结果"
        )
        report = ReplayEngine().run(
            profile, {"text_1": "新客户"}, execute=False
        )
        self.assertTrue(report["ok"])
        self.assertFalse(report["executed"])
        self.assertEqual(report["step_count"], 3)

    def test_session_files_are_written_together(self) -> None:
        profile = build_profile("session-1", "搜索客户", self.events, "")
        with tempfile.TemporaryDirectory() as directory:
            paths = save_recording(
                Path(directory), "session-1", "搜索客户", self.events, profile
            )
            self.assertTrue(paths["events"].exists())
            self.assertTrue(paths["profile"].exists())
            self.assertTrue(paths["summary"].exists())


if __name__ == "__main__":
    unittest.main()
