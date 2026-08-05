from __future__ import annotations

import unittest

from shared.gestures import (
    VK_CTRL,
    VK_PROCESSKEY,
    VK_SHIFT,
    GestureAssembler,
    assemble,
)


def down(x: int, y: int, t: int, button: str = "left", **extra) -> dict:
    return {"kind": "mouse.down", "button": button, "x": x, "y": y, "t": t, **extra}


def up(x: int, y: int, t: int, button: str = "left", **extra) -> dict:
    return {"kind": "mouse.up", "button": button, "x": x, "y": y, "t": t, **extra}


def wheel(delta: int, t: int, x: int = 50, y: int = 50, **extra) -> dict:
    return {"kind": "mouse.wheel", "delta": delta, "x": x, "y": y, "t": t, **extra}


def kdown(vk: int, t: int, **extra) -> dict:
    return {"kind": "key.down", "vk": vk, "t": t, "repeat": False, **extra}


def kup(vk: int, t: int) -> dict:
    return {"kind": "key.up", "vk": vk, "t": t}


class PointerGestureTests(unittest.TestCase):
    def test_drag_is_not_a_click(self) -> None:
        """拖拽被记成点击，语义就是错的：选中一段文字变成把光标点到某处。"""

        gestures = assemble([down(100, 100, 0), up(400, 100, 250)])
        self.assertEqual(len(gestures), 1)
        drag = gestures[0]
        self.assertEqual(drag["kind"], "pointer.drag")
        self.assertEqual(drag["start"], [100, 100])
        self.assertEqual(drag["end"], [400, 100])
        self.assertEqual(drag["duration_ms"], 250)

    def test_tiny_wobble_is_still_a_click(self) -> None:
        gestures = assemble([down(100, 100, 0), up(102, 101, 60)])
        self.assertEqual([item["kind"] for item in gestures], ["pointer.click"])

    def test_double_click_is_one_gesture_not_two(self) -> None:
        gestures = assemble(
            [down(10, 10, 0), up(10, 10, 40), down(11, 10, 180), up(11, 10, 210)]
        )
        self.assertEqual([item["kind"] for item in gestures], ["pointer.double_click"])
        self.assertEqual(gestures[0]["offset_ms"], 0)

    def test_two_slow_clicks_stay_two_clicks(self) -> None:
        gestures = assemble(
            [down(10, 10, 0), up(10, 10, 40), down(10, 10, 900), up(10, 10, 940)]
        )
        self.assertEqual(
            [item["kind"] for item in gestures], ["pointer.click", "pointer.click"]
        )

    def test_two_fast_clicks_far_apart_stay_two_clicks(self) -> None:
        gestures = assemble(
            [down(10, 10, 0), up(10, 10, 30), down(600, 400, 120), up(600, 400, 150)]
        )
        self.assertEqual(
            [item["kind"] for item in gestures], ["pointer.click", "pointer.click"]
        )

    def test_right_and_middle_buttons_are_distinguished(self) -> None:
        gestures = assemble(
            [
                down(5, 5, 0, button="right"), up(5, 5, 30, button="right"),
                down(5, 5, 900, button="middle"), up(5, 5, 930, button="middle"),
            ]
        )
        self.assertEqual([item["button"] for item in gestures], ["right", "middle"])

    def test_wheel_run_becomes_one_gesture_with_summed_delta(self) -> None:
        """滚动列表过去完全录不出来；现在一串滚动是一个手势，不是二十个。"""

        gestures = assemble([wheel(-120, t) for t in (0, 60, 120, 180)])
        self.assertEqual(len(gestures), 1)
        self.assertEqual(gestures[0]["kind"], "pointer.wheel")
        self.assertEqual(gestures[0]["delta"], -480)

    def test_wheel_direction_change_splits_the_run(self) -> None:
        gestures = assemble([wheel(-120, 0), wheel(-120, 60), wheel(120, 120)])
        self.assertEqual([item["delta"] for item in gestures], [-240, 120])

    def test_injected_input_is_never_recorded(self) -> None:
        """回放自己发出的合成输入不能被录进来，否则一边放一边录会自我叠加。"""

        gestures = assemble(
            [
                down(10, 10, 0, injected=True), up(10, 10, 30, injected=True),
                kdown(0x41, 100, injected=True),
            ]
        )
        self.assertEqual(gestures, [])


class KeyboardGestureTests(unittest.TestCase):
    def test_fast_typing_counts_every_key(self) -> None:
        """20ms 轮询会漏掉按下即抬起的键；钩子逐事件送达，一个都不该少。"""

        records = []
        for index in range(12):
            records += [kdown(0x41 + index, index * 3), kup(0x41 + index, index * 3 + 1)]
        gestures = assemble(records)
        self.assertEqual([item["kind"] for item in gestures], ["text.input"])
        self.assertEqual(gestures[0]["key_count"], 12)

    def test_repeated_same_key_is_not_collapsed(self) -> None:
        records = []
        for index in range(4):
            records += [kdown(0x41, index * 10), kup(0x41, index * 10 + 4)]
        self.assertEqual(assemble(records)[0]["key_count"], 4)

    def test_auto_repeat_counts_once(self) -> None:
        records = [kdown(0x41, 0)] + [
            {"kind": "key.down", "vk": 0x41, "t": t, "repeat": True}
            for t in (30, 60, 90, 120)
        ] + [kup(0x41, 150)]
        self.assertEqual(assemble(records)[0]["key_count"], 1)

    def test_shift_is_typing_but_ctrl_is_a_shortcut(self) -> None:
        shifted = assemble([kdown(VK_SHIFT, 0), kdown(0x41, 10), kup(0x41, 20)])
        self.assertEqual([item["kind"] for item in shifted], ["text.input"])

        control = assemble([kdown(VK_CTRL, 0), kdown(0x53, 10), kup(0x53, 20)])
        self.assertEqual([item["kind"] for item in control], ["keyboard.shortcut"])
        self.assertEqual(control[0]["keys"], ["CTRL", "S"])

    def test_backspace_reduces_the_pending_count(self) -> None:
        records = [kdown(0x41, 0), kdown(0x42, 10), kdown(0x08, 20)]
        self.assertEqual(assemble(records)[0]["key_count"], 1)

    def test_ime_input_is_flagged_as_approximate(self) -> None:
        """中文经输入法组字，按键数不等于字符数 —— 标注出来，不假装它准。"""

        gestures = assemble([kdown(VK_PROCESSKEY, t) for t in (0, 20, 40)])
        self.assertEqual(gestures[0]["input_method"], "ime")
        self.assertTrue(gestures[0]["key_count_is_approximate"])

        direct = assemble([kdown(0x41, 0)])
        self.assertEqual(direct[0]["input_method"], "direct")
        self.assertNotIn("key_count_is_approximate", direct[0])

    def test_text_segment_splits_on_window_change(self) -> None:
        """跨窗口的输入不是同一个变量：一个可能是收件人，一个是正文。"""

        gestures = assemble(
            [
                kdown(0x41, 0, context_key="excel"),
                kdown(0x42, 10, context_key="excel"),
                kdown(0x43, 20, context_key="wechat"),
            ]
        )
        self.assertEqual([item["key_count"] for item in gestures], [2, 1])
        self.assertEqual([item["placeholder"] for item in gestures], ["text_1", "text_2"])

    def test_enter_ends_the_text_segment_and_keeps_order(self) -> None:
        gestures = assemble([kdown(0x41, 0), kdown(0x42, 10), kdown(0x0D, 20)])
        self.assertEqual(
            [item["kind"] for item in gestures], ["text.input", "keyboard.press"]
        )
        self.assertEqual(gestures[-1]["key"], "ENTER")


class BufferingTests(unittest.TestCase):
    def test_pending_click_is_released_by_tick(self) -> None:
        assembler = GestureAssembler()
        self.assertEqual(assembler.feed(down(10, 10, 0)), [])
        self.assertEqual(assembler.feed(up(10, 10, 30)), [])  # 可能还有第二下
        self.assertEqual(assembler.tick(100), [])  # 双击窗口还没过
        released = assembler.tick(600)
        self.assertEqual([item["kind"] for item in released], ["pointer.click"])

    def test_tick_output_carries_no_internal_bookkeeping(self) -> None:
        assembler = GestureAssembler()
        assembler.feed(kdown(0x41, 0))
        flushed = assembler.tick(5000) or assembler.flush()
        self.assertTrue(flushed)
        for gesture in flushed:
            self.assertFalse([key for key in gesture if key.startswith("_")])

    def test_a_mixed_session_comes_out_in_order_with_nothing_lost(self) -> None:
        """一次真实演示的形状：点一下 → 滚一段 → 打字 → 回车。顺序和数量都不能错。"""

        assembler = GestureAssembler()
        gestures: list[dict] = []
        for record in (
            down(10, 10, 0), up(10, 10, 20),          # 点击（被扣住等双击判定）
            wheel(-120, 300), wheel(-120, 340),        # 滚动挤出上面的点击
            kdown(0x41, 800), kdown(0x42, 830),        # 打字挤出滚轮
            kdown(0x0D, 900),                          # 回车挤出文本段
        ):
            gestures.extend(assembler.feed(record))
        gestures.extend(assembler.flush())

        self.assertEqual(
            [item["kind"] for item in gestures],
            ["pointer.click", "pointer.wheel", "text.input", "keyboard.press"],
        )
        self.assertEqual([item["offset_ms"] for item in gestures], [0, 300, 800, 900])
        self.assertEqual(gestures[1]["delta"], -240)
        self.assertEqual(gestures[2]["key_count"], 2)

    def test_flush_emits_what_is_still_buffered_at_stop(self) -> None:
        assembler = GestureAssembler()
        assembler.feed(kdown(0x41, 0))
        self.assertEqual([item["kind"] for item in assembler.flush()], ["text.input"])


if __name__ == "__main__":
    unittest.main()
