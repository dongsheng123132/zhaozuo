from __future__ import annotations

import contextlib
import unittest
from unittest.mock import patch

from desktop_app.replay import ReplayEngine
from tests.test_evidence import FakeProbe


WINDOW = {"process": "demo.exe", "class_name": "DemoMain", "title": "演示"}
UIA = {"control_type": "Button", "control_type_id": 50000, "automation_id": "save"}


def _profile(*steps: dict) -> dict:
    return {
        "profile_version": "1.0",
        "profile_id": "windows.demo.readiness",
        "kind": "external_ui_adapter",
        "status": "draft",
        "application": {
            "id": "com.demo",
            "name": "Demo",
            "platform": "windows",
            "discovery": {"process_names": ["demo.exe"]},
        },
        "actions": {
            "demo.do_thing": {
                "title": "做一件事",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "risk": "low",
                "confirmation": "never",
                "steps": list(steps),
                "success_evidence": [
                    {"kind": "process.running", "expected": "demo.exe"}
                ],
            }
        },
    }


def _click(step_id: str, *, offset_ms: int = 0, anchored: bool = True, **extra) -> dict:
    locator: dict = {"window": dict(WINDOW), "fallback_absolute": [10, 10]}
    if anchored:
        locator["uia"] = dict(UIA)
    return {
        "id": step_id,
        "kind": "pointer.click",
        "captured_offset_ms": offset_ms,
        "locator": locator,
        "args": {"button": "left"},
        **extra,
    }


class LateProbe(FakeProbe):
    """The element only shows up after `appears_after` checks. 模拟界面还没加载完。"""

    def __init__(self, appears_after: int, **state: object) -> None:
        super().__init__(**state)
        self.appears_after = appears_after
        self.checks = 0

    def uia_exists(self, locator: dict) -> bool:
        self.checks += 1
        return self.checks > self.appears_after


@contextlib.contextmanager
def _harness(engine: ReplayEngine):
    match = {
        "hwnd": 42,
        "confidence": "exact",
        "ambiguous": False,
        "candidates": 1,
        "reasons": [],
        "context": {**WINDOW, "hwnd": 42, "rect": [0, 0, 800, 600]},
    }
    with contextlib.ExitStack() as stack:
        for target, value in (
            ("desktop_app.replay.windows.resolve_window", match),
            ("desktop_app.replay.windows.activate_window", True),
            ("desktop_app.replay.windows.window_context", {"rect": [0, 0, 800, 600]}),
            ("desktop_app.replay.uia.find_bounds", [10, 10, 30, 30]),
        ):
            stack.enter_context(patch(target, return_value=value))
        stack.enter_context(patch.object(engine, "_wait", return_value=None))
        yield stack.enter_context(patch("desktop_app.replay.windows.click"))


class ReadinessTests(unittest.TestCase):
    def test_anchored_step_waits_for_the_element_not_the_clock(self) -> None:
        """录制时手快的一步在慢机器上必须多等，不能照着 80ms 就往下点。"""

        probe = LateProbe(appears_after=4, windows=1, processes={"demo.exe"})
        engine = ReplayEngine(probe=probe)
        # 录制间隔只有 80ms，但控件要 5 次检查之后才出现。
        profile = _profile(_click("step_001"), _click("step_002", offset_ms=80))
        with _harness(engine) as click:
            report = engine.run(profile, {}, execute=True)

        self.assertEqual(click.call_count, 2)
        modes = [item["mode"] for item in report["readiness_results"]]
        self.assertEqual(modes, ["observed", "observed"])
        # 真的轮询等待过，而不是睡完录制间隔就走。
        self.assertGreater(probe.checks, 4)
        self.assertEqual(report["timed_steps"], [])

    def test_recorded_long_pause_is_skipped_when_the_anchor_is_already_there(self) -> None:
        """人当时等了 10 秒，不代表机器每次都要等 10 秒。"""

        engine = ReplayEngine(probe=FakeProbe(windows=1, everything_exists=True, processes={"demo.exe"}))
        profile = _profile(_click("step_001"), _click("step_002", offset_ms=10_000))
        with _harness(engine):
            report = engine.run(profile, {}, execute=True)

        second = report["readiness_results"][1]
        self.assertEqual(second["mode"], "observed")
        self.assertLess(second["waited_ms"], 500)

    def test_unanchored_step_falls_back_to_the_stopwatch_and_says_so(self) -> None:
        """没有可观察锚点时只能照秒表睡 —— 但这必须被数出来，不能假装是等到了。"""

        engine = ReplayEngine(probe=FakeProbe(windows=1, processes={"demo.exe"}))
        profile = _profile(
            _click("step_001", anchored=False),
            _click("step_002", anchored=False, offset_ms=200),
        )
        with _harness(engine):
            report = engine.run(profile, {}, execute=True)

        self.assertEqual(
            [item["mode"] for item in report["readiness_results"]], ["timed", "timed"]
        )
        self.assertEqual(report["timed_steps"], ["step_001", "step_002"])

    def test_declared_precondition_that_never_holds_stops_the_run(self) -> None:
        """档案作者声明的前置条件没满足，继续往下点就是往空处点。"""

        engine = ReplayEngine(probe=FakeProbe(windows=0, processes={"demo.exe"}))
        profile = _profile(
            _click(
                "step_001",
                wait_for=[{"kind": "window.exists", "window": dict(WINDOW)}],
                timeout_ms=10,
            ),
            _click("step_002"),
        )
        with _harness(engine) as click:
            report = engine.run(profile, {}, execute=True)

        self.assertEqual(report["mode"], "readiness_timeout")
        self.assertFalse(report["ok"])
        click.assert_not_called()
        self.assertIn("step_001", report["error"])

    def test_derived_precondition_timeout_proceeds_but_is_reported(self) -> None:
        """派生条件只是启发式，超时不该整条中断，但必须留下记录。"""

        engine = ReplayEngine(probe=FakeProbe(windows=0, processes={"demo.exe"}))  # 窗口证据永远不成立
        profile = _profile(_click("step_001", timeout_ms=10))
        with _harness(engine) as click:
            report = engine.run(profile, {}, execute=True)

        self.assertEqual(report["mode"], "executed")
        click.assert_called_once()
        self.assertEqual(report["unready_steps"], ["step_001"])
        self.assertEqual(report["readiness_results"][0]["mode"], "timeout")
        self.assertTrue(report["readiness_results"][0]["unmet"])

    def test_wait_for_evidence_step_touches_nothing(self) -> None:
        engine = ReplayEngine(
            probe=FakeProbe(windows=1, everything_exists=True, processes={"demo.exe"})
        )
        profile = _profile(
            {
                "id": "step_001",
                "kind": "wait.for_evidence",
                "timeout_ms": 500,
                "args": {"evidence": [{"kind": "process.running", "expected": "demo.exe"}]},
                "locator": {"window": dict(WINDOW)},
            },
            _click("step_002"),
        )
        with _harness(engine) as click:
            report = engine.run(profile, {}, execute=True)

        self.assertEqual(report["mode"], "executed")
        # 等待步骤自己不点不打字，只有后面那一步真的点了。
        click.assert_called_once()
        self.assertEqual(report["readiness_results"][0]["source"], "declared")
        self.assertEqual(report["locator_results"][0]["used"], "evidence")


if __name__ == "__main__":
    unittest.main()
