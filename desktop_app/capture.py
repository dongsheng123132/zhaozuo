from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from desktop_app import uia, windows


try:
    from PIL import ImageGrab
except ImportError:  # pragma: no cover - depends on local optional package
    ImageGrab = None


PRINTABLE_VKS = list(range(0x30, 0x5B)) + list(range(0xBA, 0xDF))
SPECIAL_VKS = [
    0x08,
    0x09,
    0x0D,
    0x1B,
    0x20,
    0x21,
    0x22,
    0x23,
    0x24,
    0x25,
    0x26,
    0x27,
    0x28,
    0x2D,
    0x2E,
] + list(range(0x70, 0x7C))
MODIFIER_VKS = {0x10: "SHIFT", 0x11: "CTRL", 0x12: "ALT", 0x5B: "WIN"}
WATCHED_VKS = sorted(set(PRINTABLE_VKS + SPECIAL_VKS + list(MODIFIER_VKS)))


class EventRecorder:
    """Privacy-first Windows input sampler.

    Printable keys are never stored. A contiguous typing segment becomes a
    `${text_N}` placeholder with only its key count retained.
    """

    def __init__(
        self,
        session_id: str,
        screenshot_dir: Path,
        capture_screenshots: bool = False,
    ) -> None:
        self.session_id = session_id
        self.screenshot_dir = screenshot_dir
        self.capture_screenshots = capture_screenshots and ImageGrab is not None
        self.events: list[dict[str, Any]] = []
        self._started = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._key_states = {vk: False for vk in WATCHED_VKS}
        self._mouse_states = {0x01: False, 0x02: False}
        self._text_segment: dict[str, Any] | None = None
        self._text_counter = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self.events) + (1 if self._text_segment else 0)

    def start(self) -> None:
        if self.running:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._started = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> list[dict[str, Any]]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._flush_text()
        with self._lock:
            return list(self.events)

    def _offset_ms(self) -> int:
        return round((time.monotonic() - self._started) * 1000)

    @staticmethod
    def _is_ignored_context(context: dict[str, Any]) -> bool:
        """Return whether an event cannot describe a reusable app action.

        Taskbar/desktop activation clicks are useful to a human demonstrator but
        harmful in a replay: the recorded taskbar slot can point at another app
        later. The next real application event already gives the executor the
        window it should activate.
        """

        if not int(context.get("hwnd") or 0):
            return True
        if str(context.get("title", "")).startswith("照做"):
            return True
        return str(context.get("class_name", "")) in {
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "Progman",
            "WorkerW",
        }

    def _append(self, event: dict[str, Any]) -> None:
        event.setdefault("event_id", str(uuid.uuid4()))
        event.setdefault("offset_ms", self._offset_ms())
        with self._lock:
            self.events.append(event)

    def _flush_text(self) -> None:
        segment = self._text_segment
        if not segment:
            return
        self._text_segment = None
        if int(segment["key_count"]) <= 0:
            return
        self._append(segment)

    def _record_text_key(self, context: dict[str, Any]) -> None:
        now = self._offset_ms()
        if (
            self._text_segment
            and now - int(self._text_segment["last_key_ms"]) <= 1200
            and self._text_segment["window"]["hwnd"] == context["hwnd"]
        ):
            self._text_segment["key_count"] += 1
            self._text_segment["last_key_ms"] = now
            return
        self._flush_text()
        self._text_counter += 1
        self._text_segment = {
            "kind": "text.input",
            "placeholder": f"text_{self._text_counter}",
            "key_count": 1,
            "last_key_ms": now,
            "window": context,
            "uia": uia.snapshot_focused(context.get("rect")),
            "privacy": "redacted",
            "offset_ms": now,
        }

    def _capture_screenshot(self) -> str | None:
        if not self.capture_screenshots or ImageGrab is None:
            return None
        name = f"step-{len(self.events) + 1:03d}.jpg"
        path = self.screenshot_dir / name
        try:
            image = ImageGrab.grab(all_screens=True)
            image.thumbnail((1920, 1080))
            image.convert("RGB").save(path, "JPEG", quality=78)
        except Exception:
            return None
        return name

    def _record_click(self, button: str) -> None:
        context = windows.window_context()
        if self._is_ignored_context(context):
            return
        self._flush_text()
        x, y = windows.cursor_position()
        screenshot = self._capture_screenshot()
        event: dict[str, Any] = {
            "kind": "pointer.click",
            "button": button,
            "absolute": [x, y],
            "relative": windows.relative_point(x, y, context.get("rect")),
            "window": context,
            "uia": uia.snapshot_at(x, y, context.get("rect")),
        }
        if screenshot:
            event["screenshot"] = screenshot
        self._append(event)

    def _record_key(self, vk: int) -> None:
        context = windows.window_context()
        if self._is_ignored_context(context):
            return

        modifiers = [
            name for code, name in MODIFIER_VKS.items() if windows.key_down(code)
        ]
        shortcut_modifiers = [name for name in modifiers if name != "SHIFT"]
        if vk in PRINTABLE_VKS and shortcut_modifiers:
            self._flush_text()
            key_name = windows.VK_NAMES.get(vk, f"VK_{vk}")
            self._append(
                {
                    "kind": "keyboard.shortcut",
                    "keys": modifiers + [key_name],
                    "window": context,
                    "uia": uia.snapshot_focused(context.get("rect")),
                }
            )
            return
        if vk in PRINTABLE_VKS or vk == 0x20:
            self._record_text_key(context)
            return
        if vk == 0x08 and self._text_segment:
            self._text_segment["key_count"] = max(
                0, int(self._text_segment["key_count"]) - 1
            )
            self._text_segment["last_key_ms"] = self._offset_ms()
            return
        self._flush_text()
        self._append(
            {
                "kind": "keyboard.press",
                "key": windows.VK_NAMES.get(vk, f"VK_{vk}"),
                "window": context,
                "uia": uia.snapshot_focused(context.get("rect")),
            }
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            for vk, button in ((0x01, "left"), (0x02, "right")):
                current = windows.key_down(vk)
                if current and not self._mouse_states[vk]:
                    self._record_click(button)
                self._mouse_states[vk] = current

            for vk in WATCHED_VKS:
                current = windows.key_down(vk)
                if current and not self._key_states[vk] and vk not in MODIFIER_VKS:
                    self._record_key(vk)
                self._key_states[vk] = current

            if (
                self._text_segment
                and self._offset_ms() - int(self._text_segment["last_key_ms"]) > 1200
            ):
                self._flush_text()
            time.sleep(0.02)
