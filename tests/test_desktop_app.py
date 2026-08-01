from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_app.replay import ReplayEngine
from desktop_app.workflow import build_profile, classify_effect, save_recording
from shared.profile import resolve_action, validate_profile


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            {
                "kind": "pointer.click",
                "button": "left",
                "absolute": [420, 220],
                "relative": {"x": 0.5, "y": 0.25},
                "uia": {
                    "control_type": "Button",
                    "control_type_id": 50000,
                    "automation_id": "search",
                    "name": "搜索",
                },
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

    def test_social_goal_marks_only_final_step_as_effect(self) -> None:
        profile = build_profile(
            "session-2",
            "自动回复微信消息",
            self.events,
            "微信",
        )
        action_id = next(iter(profile["actions"]))
        action = profile["actions"][action_id]
        self.assertEqual(action_id, "wechat.reply_message")
        self.assertEqual(action["risk"], "high")
        self.assertNotIn("effect", action["steps"][0])
        self.assertEqual(action["steps"][-1]["effect"]["confirmation"], "always")
        self.assertEqual(
            classify_effect("给这条抖音点赞")["action_id"], "douyin.like_video"
        )
        self.assertEqual(
            classify_effect("发布一篇公众号文章")["action_id"],
            "wechat_official.publish_article",
        )

    def test_replay_stops_before_final_effect_then_resumes_only_that_step(self) -> None:
        profile = build_profile(
            "session-3",
            "给抖音视频点赞",
            [self.events[0]],
            "计算器",
        )
        engine = ReplayEngine()
        with patch("desktop_app.replay.windows.find_window", return_value=123), patch(
            "desktop_app.replay.windows.activate_window", return_value=True
        ), patch("desktop_app.replay.windows.window_context", return_value={"rect": [0, 0, 800, 600]}), patch(
            "desktop_app.replay.uia.find_bounds", return_value=[10, 10, 30, 30]
        ), patch("desktop_app.replay.windows.click") as click, patch.object(
            engine, "_wait", return_value=None
        ), patch(
            "desktop_app.replay.windows.visible_window_titles", return_value=["计算器"]
        ):
            pending = engine.run(profile, {}, execute=True)
            self.assertEqual(pending["mode"], "awaiting_confirmation")
            click.assert_not_called()

            step_id = pending["pending_effect"]["step_id"]
            complete = engine.run(
                profile,
                {},
                execute=True,
                confirmed_effect_step_id=step_id,
                start_step_id=step_id,
            )
            self.assertTrue(complete["ok"])
            click.assert_called_once_with(20, 20, "left")
            self.assertEqual(complete["locator_results"][0]["used"], "uia")

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
