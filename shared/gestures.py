"""Raw input records → semantic gestures.

录制层过去用 20ms 轮询 GetAsyncKeyState 采样，代价是：20ms 内按下又抬起的键
完全不存在，快速连按同一个键被并成一次，长按自动重复算一次，而且只认左右键单击 ——
滚轮、拖拽、双击一概录不出来，拖拽还会被记成一次点击（语义完全错的）。

这个模块只做**装配**：把 down/up/wheel 这些原始记录拼成有业务含义的手势。
它不碰任何 Windows API，因此可以毫秒级全覆盖测试；采集那层薄壳（hooks.py）
只负责把原始记录送进来。这样"拖拽有没有被认出来"是纯逻辑断言，不需要真机。
"""

from __future__ import annotations

from typing import Any, Iterable


#: 移动超过这个像素数才算拖拽，否则是手抖的点击。Windows 的 SM_CXDRAG 通常是 4。
DEFAULT_DRAG_THRESHOLD_PX = 5

#: 两次点击算双击的时间与距离上限。Windows 默认双击时间 500ms。
DEFAULT_DOUBLE_CLICK_MS = 500
DEFAULT_DOUBLE_CLICK_SLOP_PX = 4

#: 连续输入合并成一个文本段的最大间隔。
DEFAULT_TEXT_GAP_MS = 1200

#: 连续滚动合并成一个滚轮手势的最大间隔。
DEFAULT_WHEEL_GAP_MS = 300

VK_BACKSPACE = 0x08
VK_SHIFT, VK_CTRL, VK_ALT = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
#: IME 组字期间，键被输入法吃掉，系统送上来的是 VK_PROCESSKEY。
VK_PROCESSKEY = 0xE5

MODIFIER_VKS = {
    VK_SHIFT: "SHIFT",
    VK_CTRL: "CTRL",
    VK_ALT: "ALT",
    VK_LWIN: "WIN",
    VK_RWIN: "WIN",
    0xA0: "SHIFT", 0xA1: "SHIFT",
    0xA2: "CTRL", 0xA3: "CTRL",
    0xA4: "ALT", 0xA5: "ALT",
}

PRINTABLE_VKS = frozenset(
    list(range(0x30, 0x5B))  # 0-9 A-Z
    + list(range(0x60, 0x70))  # 小键盘
    + list(range(0xBA, 0xC1))  # ;=,-./`
    + list(range(0xDB, 0xE0))  # []\'
    + [0x20]  # 空格
)

VK_NAMES: dict[int, str] = {
    0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER", 0x1B: "ESCAPE", 0x20: "SPACE",
    0x21: "PAGEUP", 0x22: "PAGEDOWN", 0x23: "END", 0x24: "HOME",
    0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN",
    0x2D: "INSERT", 0x2E: "DELETE",
    **{code: "SHIFT" for code in (0x10, 0xA0, 0xA1)},
    **{code: "CTRL" for code in (0x11, 0xA2, 0xA3)},
    **{code: "ALT" for code in (0x12, 0xA4, 0xA5)},
    **{code: "WIN" for code in (0x5B, 0x5C)},
    **{code: chr(code) for code in range(0x30, 0x3A)},
    **{code: chr(code) for code in range(0x41, 0x5B)},
    **{0x6F + index: f"F{index}" for index in range(1, 13)},
}


def key_name(vk: int) -> str:
    return VK_NAMES.get(vk, f"VK_{vk}")


class GestureAssembler:
    """Turn a stream of raw input records into replayable gestures.

    调用方每收到一条原始记录就 `feed()`，并定期 `tick(now_ms)` 让基于时间的
    缓冲（双击判定、文本段、滚轮段）到点吐出。`flush()` 在停止录制时收尾。
    """

    def __init__(
        self,
        *,
        drag_threshold_px: int = DEFAULT_DRAG_THRESHOLD_PX,
        double_click_ms: int = DEFAULT_DOUBLE_CLICK_MS,
        double_click_slop_px: int = DEFAULT_DOUBLE_CLICK_SLOP_PX,
        text_gap_ms: int = DEFAULT_TEXT_GAP_MS,
        wheel_gap_ms: int = DEFAULT_WHEEL_GAP_MS,
    ) -> None:
        self.drag_threshold_px = drag_threshold_px
        self.double_click_ms = double_click_ms
        self.double_click_slop_px = double_click_slop_px
        self.text_gap_ms = text_gap_ms
        self.wheel_gap_ms = wheel_gap_ms

        self._down: dict[str, dict[str, Any]] = {}
        self._pending_click: dict[str, Any] | None = None
        self._text: dict[str, Any] | None = None
        self._wheel: dict[str, Any] | None = None
        self._held: dict[int, bool] = {}
        self._text_counter = 0

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _distance(first: tuple[int, int], second: tuple[int, int]) -> float:
        return max(abs(first[0] - second[0]), abs(first[1] - second[1]))

    def _modifiers(self) -> list[str]:
        names: list[str] = []
        for vk, name in MODIFIER_VKS.items():
            if self._held.get(vk) and name not in names:
                names.append(name)
        return names

    def _context_of(self, record: dict[str, Any]) -> Any:
        return record.get("context_key")

    # ------------------------------------------------------------- buffering

    def _flush_text(self, out: list[dict[str, Any]]) -> None:
        segment = self._text
        self._text = None
        if segment and int(segment["key_count"]) > 0:
            out.append(segment)

    def _flush_wheel(self, out: list[dict[str, Any]]) -> None:
        wheel = self._wheel
        self._wheel = None
        if wheel and int(wheel["delta"]) != 0:
            out.append(wheel)

    def _flush_pending_click(self, out: list[dict[str, Any]]) -> None:
        click = self._pending_click
        self._pending_click = None
        if click:
            out.append(click)

    def _flush_all_but_text(self, out: list[dict[str, Any]]) -> None:
        self._flush_pending_click(out)
        self._flush_wheel(out)

    # ------------------------------------------------------------------ feed

    def feed(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Consume one raw record, return the gestures it completes."""

        if record.get("injected"):
            # 回放自己发出的合成输入不能被录进来，否则一边放一边录会自我叠加。
            return []

        out: list[dict[str, Any]] = []
        kind = record.get("kind")
        handler = {
            "mouse.down": self._on_mouse_down,
            "mouse.up": self._on_mouse_up,
            "mouse.wheel": self._on_wheel,
            "key.down": self._on_key_down,
            "key.up": self._on_key_up,
        }.get(str(kind))
        if handler:
            handler(record, out)
        return out

    def tick(self, now_ms: int) -> list[dict[str, Any]]:
        """Flush buffers whose time window has passed."""

        out: list[dict[str, Any]] = []
        if self._pending_click and now_ms - int(self._pending_click["_at_ms"]) >= self.double_click_ms:
            self._flush_pending_click(out)
        if self._text and now_ms - int(self._text["_last_ms"]) >= self.text_gap_ms:
            self._flush_text(out)
        if self._wheel and now_ms - int(self._wheel["_last_ms"]) >= self.wheel_gap_ms:
            self._flush_wheel(out)
        return [self._clean(item) for item in out]

    def flush(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        self._flush_pending_click(out)
        self._flush_wheel(out)
        self._flush_text(out)
        return [self._clean(item) for item in out]

    @staticmethod
    def _clean(gesture: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in gesture.items() if not key.startswith("_")}

    # --------------------------------------------------------------- handlers

    def _on_mouse_down(self, record: dict[str, Any], out: list[dict[str, Any]]) -> None:
        self._flush_text(out)
        self._flush_wheel(out)
        self._down[str(record.get("button", "left"))] = record

    def _on_mouse_up(self, record: dict[str, Any], out: list[dict[str, Any]]) -> None:
        button = str(record.get("button", "left"))
        down = self._down.pop(button, None)
        if down is None:
            # 只看到抬起（录制从按住状态开始），没有起点就无法判断是点击还是拖拽。
            return

        start = (int(down["x"]), int(down["y"]))
        end = (int(record["x"]), int(record["y"]))
        at_ms = int(down["t"])

        if self._distance(start, end) > self.drag_threshold_px:
            # 拖拽被记成点击，语义就是错的：选中一段文字变成把光标点到某处。
            self._flush_pending_click(out)
            out.append(
                {
                    "kind": "pointer.drag",
                    "button": button,
                    "start": list(start),
                    "end": list(end),
                    "offset_ms": at_ms,
                    "duration_ms": max(int(record["t"]) - at_ms, 0),
                    "context_key": self._context_of(down),
                }
            )
            return

        pending = self._pending_click
        if (
            pending
            and pending["button"] == button
            and at_ms - int(pending["_at_ms"]) <= self.double_click_ms
            and self._distance(tuple(pending["position"]), start) <= self.double_click_slop_px
        ):
            self._pending_click = None
            out.append(
                {
                    "kind": "pointer.double_click",
                    "button": button,
                    "position": list(start),
                    "offset_ms": int(pending["offset_ms"]),
                    "context_key": pending.get("context_key"),
                }
            )
            return

        # 还不能确定是不是双击的第一下，先扣住，等 tick 到点或下一个动作把它挤出去。
        self._flush_pending_click(out)
        self._pending_click = {
            "kind": "pointer.click",
            "button": button,
            "position": list(start),
            "offset_ms": at_ms,
            "context_key": self._context_of(down),
            "_at_ms": at_ms,
        }

    def _on_wheel(self, record: dict[str, Any], out: list[dict[str, Any]]) -> None:
        self._flush_text(out)
        self._flush_pending_click(out)
        now = int(record["t"])
        delta = int(record.get("delta", 0))
        wheel = self._wheel
        same_run = (
            wheel is not None
            and now - int(wheel["_last_ms"]) <= self.wheel_gap_ms
            and wheel["context_key"] == self._context_of(record)
            and (int(wheel["delta"]) >= 0) == (delta >= 0)
        )
        if same_run and wheel is not None:
            wheel["delta"] = int(wheel["delta"]) + delta
            wheel["_last_ms"] = now
            return
        self._flush_wheel(out)
        self._wheel = {
            "kind": "pointer.wheel",
            "delta": delta,
            "horizontal": bool(record.get("horizontal")),
            "position": [int(record["x"]), int(record["y"])],
            "offset_ms": now,
            "context_key": self._context_of(record),
            "_last_ms": now,
        }

    def _on_key_down(self, record: dict[str, Any], out: list[dict[str, Any]]) -> None:
        vk = int(record["vk"])
        was_held = self._held.get(vk, False)
        self._held[vk] = True
        if vk in MODIFIER_VKS:
            return
        if was_held and record.get("repeat", True):
            # 长按自动重复：算成一次按键，不是几十次。
            return

        now = int(record["t"])
        modifiers = self._modifiers()
        shortcut_modifiers = [name for name in modifiers if name != "SHIFT"]

        if shortcut_modifiers:
            self._flush_text(out)
            self._flush_all_but_text(out)
            out.append(
                {
                    "kind": "keyboard.shortcut",
                    "keys": modifiers + [key_name(vk)],
                    "offset_ms": now,
                    "context_key": self._context_of(record),
                }
            )
            return

        if vk == VK_PROCESSKEY or vk in PRINTABLE_VKS:
            self._accumulate_text(record, vk, now, out)
            return

        if vk == VK_BACKSPACE and self._text:
            self._text["key_count"] = max(0, int(self._text["key_count"]) - 1)
            self._text["_last_ms"] = now
            return

        self._flush_text(out)
        self._flush_all_but_text(out)
        out.append(
            {
                "kind": "keyboard.press",
                "key": key_name(vk),
                "offset_ms": now,
                "context_key": self._context_of(record),
            }
        )

    def _on_key_up(self, record: dict[str, Any], out: list[dict[str, Any]]) -> None:
        self._held[int(record["vk"])] = False

    def _accumulate_text(
        self, record: dict[str, Any], vk: int, now: int, out: list[dict[str, Any]]
    ) -> None:
        context = self._context_of(record)
        segment = self._text
        continuing = (
            segment is not None
            and now - int(segment["_last_ms"]) <= self.text_gap_ms
            and segment["context_key"] == context
        )
        if not continuing:
            self._flush_text(out)
            self._flush_all_but_text(out)
            self._text_counter += 1
            segment = {
                "kind": "text.input",
                "placeholder": f"text_{self._text_counter}",
                "key_count": 0,
                "privacy": "redacted",
                "input_method": "direct",
                "offset_ms": now,
                "context_key": context,
                "_last_ms": now,
            }
            self._text = segment
        assert segment is not None
        segment["key_count"] = int(segment["key_count"]) + 1
        segment["_last_ms"] = now
        if vk == VK_PROCESSKEY:
            # 中文经输入法组字：按键数不等于字符数，标注出来而不是假装它准。
            segment["input_method"] = "ime"
            segment["key_count_is_approximate"] = True


def assemble(records: Iterable[dict[str, Any]], **options: Any) -> list[dict[str, Any]]:
    """Convenience: run a whole recorded stream through a fresh assembler."""

    assembler = GestureAssembler(**options)
    gestures: list[dict[str, Any]] = []
    for record in records:
        gestures.extend(assembler._clean(item) for item in assembler.feed(record))
    gestures.extend(assembler.flush())
    return gestures
