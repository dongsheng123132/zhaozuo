from __future__ import annotations

import contextlib
import unittest
from unittest.mock import patch

from desktop_app.replay import ReplayEngine
from desktop_app.workflow import build_profile
from shared.profile import validate_profile
from tests.test_evidence import FakeProbe


WINDOW = {
    "hwnd": 7,
    "title": "资源管理器",
    "class_name": "CabinetWClass",
    "process": "explorer.exe",
    "rect": [0, 0, 1000, 800],
}


def _event(kind: str, **fields) -> dict:
    base = {
        "kind": kind,
        "window": dict(WINDOW),
        "offset_ms": 100,
        "absolute": [400, 300],
        "relative": {"x": 0.4, "y": 0.375},
    }
    base.update(fields)
    return base


@contextlib.contextmanager
def _executed(engine: ReplayEngine):
    """Stub the world; hand back the mouse primitives so tests can assert on them."""

    match = {
        "hwnd": 7,
        "confidence": "exact",
        "ambiguous": False,
        "candidates": 1,
        "reasons": [],
        "context": dict(WINDOW),
    }
    with contextlib.ExitStack() as stack:
        for target, value in (
            ("desktop_app.replay.windows.resolve_window", match),
            ("desktop_app.replay.windows.activate_window", True),
            ("desktop_app.replay.windows.window_context", {"rect": [0, 0, 1000, 800]}),
            ("desktop_app.replay.uia.find_bounds", None),
        ):
            stack.enter_context(patch(target, return_value=value))
        stack.enter_context(patch.object(engine, "_wait", return_value=None))
        yield {
            name: stack.enter_context(patch(f"desktop_app.replay.windows.{name}"))
            for name in ("click", "double_click", "drag", "scroll")
        }


class PointerGestureRoundTripTests(unittest.TestCase):
    """录得出来还得放得回去 —— 否则拖拽只是被认出来，然后消失在档案里。"""

    def _run(self, event: dict):
        profile = build_profile("s", "整理文件夹", [event], "")
        self.assertEqual(validate_profile(profile), [])
        engine = ReplayEngine(
            probe=FakeProbe(windows=1, everything_exists=True, processes={"explorer.exe"})
        )
        with _executed(engine) as mouse:
            report = engine.run(profile, {}, execute=True)
        return profile, report, mouse

    def test_double_click_replays_as_a_double_click(self) -> None:
        _, report, mouse = self._run(_event("pointer.double_click"))
        self.assertEqual(report["mode"], "executed")
        mouse["double_click"].assert_called_once_with(400, 300, "left")
        mouse["click"].assert_not_called()

    def test_drag_replays_start_and_end(self) -> None:
        _, report, mouse = self._run(
            _event(
                "pointer.drag",
                absolute=[100, 300],
                relative={"x": 0.1, "y": 0.375},
                end_absolute=[700, 300],
                end_relative={"x": 0.7, "y": 0.375},
            )
        )
        self.assertEqual(report["mode"], "executed")
        mouse["drag"].assert_called_once_with(100, 300, 700, 300, "left")
        # 拖拽绝不能退化成一次点击 —— 那会把"选中一段"变成"把光标点到某处"。
        mouse["click"].assert_not_called()

    def test_wheel_replays_the_summed_delta(self) -> None:
        _, report, mouse = self._run(
            _event("pointer.wheel", delta=-480, horizontal=False)
        )
        self.assertEqual(report["mode"], "executed")
        mouse["scroll"].assert_called_once_with(400, 300, -480, False)

    def test_right_button_survives_the_round_trip(self) -> None:
        _, _, mouse = self._run(_event("pointer.click", button="right"))
        mouse["click"].assert_called_once_with(400, 300, "right")

    def test_new_kinds_are_not_silently_dropped_from_the_profile(self) -> None:
        """旧的 build_profile 遇到不认识的 kind 直接 continue —— 手势会人间蒸发。"""

        events = [
            _event("pointer.double_click", offset_ms=100),
            _event("pointer.wheel", delta=-240, offset_ms=200),
            _event("pointer.drag", end_absolute=[700, 300],
                   end_relative={"x": 0.7, "y": 0.375}, offset_ms=300),
            _event("pointer.click", button="middle", offset_ms=400),
        ]
        profile = build_profile("s", "整理文件夹", events, "")
        action = next(iter(profile["actions"].values()))
        self.assertEqual(
            [step["kind"] for step in action["steps"]],
            ["pointer.double_click", "pointer.wheel", "pointer.drag", "pointer.click"],
        )
        self.assertEqual(action["steps"][2]["args"]["end_relative"], {"x": 0.7, "y": 0.375})
        self.assertEqual(action["steps"][3]["args"]["button"], "middle")


if __name__ == "__main__":
    unittest.main()
