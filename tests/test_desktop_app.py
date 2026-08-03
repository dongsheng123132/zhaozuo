from __future__ import annotations

import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_app.replay import ReplayEngine
from desktop_app.capture import EventRecorder
from desktop_app import windows
from desktop_app.workflow import (
    build_profile,
    classify_effect,
    merge_recording_segments,
    save_recording,
)
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
        # 录制器发实例 ID：效果分类 + 目标指纹。规范 ID 由人在提升时授予。
        self.assertTrue(action_id.startswith("wechat.reply_message."))
        self.assertEqual(action["risk"], "high")
        self.assertNotIn("effect", action["steps"][0])
        self.assertEqual(action["steps"][-1]["effect"]["confirmation"], "always")
        self.assertEqual(
            action["steps"][-1]["effect"]["effect_id"], "wechat.reply_message"
        )
        self.assertEqual(
            classify_effect("给这条抖音点赞")["action_id"], "douyin.like_video"
        )
        self.assertEqual(
            classify_effect("发布一篇公众号文章")["action_id"],
            "wechat_official.publish_article",
        )

    def test_distinct_goals_never_share_an_action_id(self) -> None:
        """中文目标曾经全部塌陷成 workflow.recorded_task —— ID 撞车即协议失效。"""

        goals = [
            "在Excel里做月度报表",
            "在Excel里做年度报表",
            "给张总回复微信",
            "回复老板消息",
            "导出发票PDF",
        ]
        ids = [
            next(iter(build_profile("s", goal, self.events, "").get("actions")))
            for goal in goals
        ]
        self.assertEqual(len(set(ids)), len(goals))
        self.assertNotIn("recorded_task", " ".join(ids))
        # 同一个目标必须跨进程稳定，否则档案库按 ID 索引不到自己。
        self.assertEqual(
            next(iter(build_profile("s2", goals[0], self.events, "")["actions"])),
            ids[0],
        )

    def test_guard_does_not_depend_on_user_wording(self) -> None:
        """守卫靠证据，不靠用户在目标里说对词。"""

        def ends_in(title: str, class_name: str, process: str) -> list[dict]:
            window = {
                "hwnd": 11,
                "title": title,
                "class_name": class_name,
                "process": process,
                "rect": [0, 0, 800, 600],
            }
            return [{"kind": "keyboard.press", "key": "ENTER", "window": window,
                     "offset_ms": 10}]

        # 目标描述完全没有"发送"的意思，仍然必须守卫。
        for title, class_name, process in (
            ("钉钉", "StandardFrame_DingTalk", "dingtalk.exe"),
            ("飞书", "Chrome_WidgetWin_1", "feishu.exe"),
            ("企业微信", "WeWorkWindow", "wxwork.exe"),
            ("收件箱 - Outlook", "rctrl_renwnd32", "outlook.exe"),
        ):
            with self.subTest(app=process):
                profile = build_profile("s", "随便点点", ends_in(title, class_name, process), "")
                action = next(iter(profile["actions"].values()))
                self.assertIn("effect", action["steps"][-1], f"{process} 未被守卫")

        # 换成英文、换成"发给"这类同义表达，同样必须守卫。
        for goal in ("reply to boss on WeChat", "把这条消息发给客户群", "pay the invoice"):
            with self.subTest(goal=goal):
                self.assertIsNotNone(classify_effect(goal), f"{goal!r} 未被守卫")

        # 但不能过度守卫：本地计算没有对外效果。
        calc = build_profile("s", "算一下 7+8",
                             ends_in("计算器", "ApplicationFrameWindow", "calc.exe"), "")
        self.assertNotIn("effect", next(iter(calc["actions"].values()))["steps"][-1])

    def test_wechat_recording_infers_effect_and_semantic_inputs(self) -> None:
        events = [dict(event) for event in self.events]
        for event in events:
            event["window"] = {
                "hwnd": 10,
                "title": "微信",
                "class_name": "Qt51514QWindowIcon",
                "rect": [0, 0, 840, 880],
            }
        events[0]["kind"] = "text.input"
        events[0]["placeholder"] = "text_1"
        events[0]["key_count"] = 8
        events[1]["placeholder"] = "text_2"
        events[2]["kind"] = "keyboard.press"
        events[2]["key"] = "ENTER"

        profile = build_profile(
            "session-wechat",
            "在目标软件中完成一个可验证任务",
            events,
            "微信",
        )
        self.assertEqual(validate_profile(profile), [])
        action = next(iter(profile["actions"].values()))
        self.assertEqual(
            set(action["input_schema"]["properties"]),
            {"conversation", "reply_text"},
        )
        self.assertEqual(action["steps"][0]["args"]["text"], "${conversation}")
        self.assertEqual(action["steps"][1]["args"]["text"], "${reply_text}")
        self.assertEqual(action["steps"][-1]["effect"]["confirmation"], "always")

    def test_replay_preserves_recorded_wechat_settle_time(self) -> None:
        self.assertAlmostEqual(ReplayEngine.replay_delay(6469, 17297), 10.828)
        self.assertEqual(ReplayEngine.replay_delay(0, 30000), 12.0)

    def test_recorder_ignores_taskbar_and_windowless_clicks(self) -> None:
        self.assertTrue(
            EventRecorder._is_ignored_context(
                {"hwnd": 0, "title": "", "class_name": ""}
            )
        )
        self.assertTrue(
            EventRecorder._is_ignored_context(
                {"hwnd": 1, "title": "", "class_name": "Shell_TrayWnd"}
            )
        )
        self.assertFalse(
            EventRecorder._is_ignored_context(
                {"hwnd": 2, "title": "微信", "class_name": "Qt51514QWindowIcon"}
            )
        )

    def test_profile_builder_drops_recorded_taskbar_activation(self) -> None:
        events = [
            {
                "kind": "pointer.click",
                "button": "left",
                "absolute": [100, 1000],
                "relative": None,
                "window": {"hwnd": 0, "title": "", "class_name": "", "rect": None},
                "offset_ms": 10,
            },
            self.events[0],
        ]
        profile = build_profile("session-clean", "点击测试", events, "示例窗口")
        action = next(iter(profile["actions"].values()))
        self.assertEqual(len(action["steps"]), 1)
        self.assertEqual(action["steps"][0]["id"], "step_001")

    def test_windows_input_structure_matches_native_size(self) -> None:
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(windows.INPUT), expected)

    def test_recording_segments_append_on_one_monotonic_timeline(self) -> None:
        first = merge_recording_segments([], self.events[:2])
        resumed = [dict(self.events[2], offset_ms=250)]
        merged = merge_recording_segments(first, resumed, gap_ms=800)

        self.assertEqual([event["segment_index"] for event in merged], [1, 1, 2])
        self.assertEqual(merged[-1]["offset_ms"], first[-1]["offset_ms"] + 800)

    def test_appended_wechat_send_moves_effect_to_new_final_step(self) -> None:
        before_send = [dict(event) for event in self.events[:2]]
        for event in before_send:
            event["window"] = {
                "hwnd": 10,
                "title": "微信",
                "class_name": "Qt51514QWindowIcon",
                "rect": [0, 0, 840, 880],
            }
        send = dict(
            self.events[2],
            window=before_send[-1]["window"],
            kind="keyboard.press",
            key="ENTER",
            offset_ms=200,
        )
        merged = merge_recording_segments(
            merge_recording_segments([], before_send), [send]
        )
        profile = build_profile(
            "session-append",
            "自动回复微信消息",
            merged,
            "微信",
        )
        action = next(iter(profile["actions"].values()))
        self.assertNotIn("effect", action["steps"][-2])
        self.assertEqual(action["steps"][-1]["effect"]["kind"], "send_message")

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
            self.assertEqual(paths["profile"].name, "draft.action-profile.json")
            summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
            self.assertEqual(summary["segment_count"], 1)


if __name__ == "__main__":
    unittest.main()
