"""Privacy-first Windows input recording.

三层，各司其职：

    低级钩子（hooks.py）   逐事件送达，回调只入队，绝不做慢事
        ↓ 队列
    富化工作线程（本文件） 窗口身份 + UIA 控件，**跑在钩子之外**
        ↓
    手势装配（gestures.py）点击/双击/拖拽/滚轮/按键/文本段，纯逻辑

旧实现把这三件事挤在一个 20ms 轮询循环里，于是 UIA 一慢（跨进程 COM，
常见 50-500ms）采样就整体停摆 —— 软件越复杂丢得越多。现在钩子那一层
永远不等 UIA。

隐私不变：可打印文字永不落盘，只留占位符与键数。
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

from desktop_app import hooks, uia, windows
from shared.gestures import GestureAssembler


try:
    from PIL import ImageGrab
except ImportError:  # pragma: no cover - depends on local optional package
    ImageGrab = None


#: 队列上限。钩子回调宁可丢一条并记数，也不能阻塞 —— 那会卡住整机输入。
RAW_QUEUE_LIMIT = 8192

#: 焦点控件在连续输入期间基本不变，缓存一小段时间，别为每个键都跑一次 UIA。
FOCUS_CACHE_MS = 500

IGNORED_WINDOW_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Progman",
    "WorkerW",
}

#: 只有可能**开启**一个手势的记录才值得富化；抬起只负责收尾。
_ENRICHED_KINDS = {"mouse.down", "mouse.wheel", "key.down"}

_POINTER_KINDS = {"pointer.click", "pointer.double_click", "pointer.drag", "pointer.wheel"}


class EventRecorder:
    """Records semantically meaningful gestures, never raw text."""

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

        self._raw: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=RAW_QUEUE_LIMIT)
        self._hook = hooks.InputHookListener(self._raw)
        self._assembler = GestureAssembler()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._focus_cache: tuple[int, float, Any] | None = None

    # ------------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        return bool(self._worker and self._worker.is_alive())

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self.events)

    @property
    def dropped_records(self) -> int:
        """队列满时被钩子丢掉的原始输入条数。

        丢一条就等于档案里少一步，而少一步的录制看上去和完整录制一模一样。
        所以这个数必须一路走到会话摘要和界面上，不能只留在钩子里。
        """

        return self._hook.dropped

    def start(self) -> None:
        if self.running:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._hook.start()
        self._worker = threading.Thread(target=self._drain, daemon=True)
        self._worker.start()

    def stop(self) -> list[dict[str, Any]]:
        self._hook.stop()
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=3)
        self._drain_pending()
        self._record_gestures(self._assembler.flush())
        with self._lock:
            return list(self.events)

    # ---------------------------------------------------------------- worker

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                record = self._raw.get(timeout=0.05)
            except queue.Empty:
                self._record_gestures(self._assembler.tick(self._now_ms()))
                continue
            self._consume(record)

    def _drain_pending(self) -> None:
        while True:
            try:
                self._consume(self._raw.get_nowait())
            except queue.Empty:
                return

    def _now_ms(self) -> int:
        return round((time.monotonic() - self._hook.started_at) * 1000)

    def _consume(self, record: dict[str, Any]) -> None:
        if not record.get("injected") and record.get("kind") in _ENRICHED_KINDS:
            if not self._enrich(record):
                return  # 落在任务栏/桌面/照做自己身上，不构成可复用的应用动作
        self._record_gestures(self._assembler.feed(record))

    # -------------------------------------------------------------- enriching

    @staticmethod
    def _is_ignored_context(context: dict[str, Any]) -> bool:
        """Whether an event cannot describe a reusable application action."""

        if not int(context.get("hwnd") or 0):
            return True
        if str(context.get("title", "")).startswith("照做"):
            return True
        return str(context.get("class_name", "")) in IGNORED_WINDOW_CLASSES

    def _focused_uia(self, hwnd: int, rect: Any) -> Any:
        cached = self._focus_cache
        now = time.monotonic()
        if cached and cached[0] == hwnd and (now - cached[1]) * 1000 < FOCUS_CACHE_MS:
            return cached[2]
        snapshot = uia.snapshot_focused(rect)
        self._focus_cache = (hwnd, now, snapshot)
        return snapshot

    def _enrich(self, record: dict[str, Any]) -> bool:
        """Attach window identity and UIA control identity. False = drop it."""

        is_pointer = record["kind"] != "key.down"
        hwnd = (
            windows.window_from_point(record["x"], record["y"])
            if is_pointer
            else 0
        )
        context = windows.window_context(hwnd or None)
        if self._is_ignored_context(context):
            return False

        rect = context.get("rect")
        record["window"] = context
        record["context_key"] = context["hwnd"]
        if is_pointer:
            record["relative"] = windows.relative_point(record["x"], record["y"], rect)
            record["uia"] = uia.snapshot_at(record["x"], record["y"], rect)
            if record["kind"] == "mouse.down":
                screenshot = self._capture_screenshot()
                if screenshot:
                    record["screenshot"] = screenshot
        else:
            record["uia"] = self._focused_uia(context["hwnd"], rect)
        return True

    def _capture_screenshot(self) -> str | None:
        if not self.capture_screenshots or ImageGrab is None:
            return None
        name = f"step-{len(self.events) + 1:03d}.jpg"
        try:
            image = ImageGrab.grab(all_screens=True)
            image.thumbnail((1920, 1080))
            image.convert("RGB").save(self.screenshot_dir / name, "JPEG", quality=78)
        except Exception:
            return None
        return name

    # ---------------------------------------------------------------- output

    def _record_gestures(self, gestures: list[dict[str, Any]]) -> None:
        if not gestures:
            return
        with self._lock:
            for gesture in gestures:
                self.events.append(self._as_event(gesture))

    @staticmethod
    def _as_event(gesture: dict[str, Any]) -> dict[str, Any]:
        """Normalize a gesture into the recorded-event shape profiles consume."""

        event = {key: value for key, value in gesture.items() if key != "context_key"}
        kind = str(gesture.get("kind"))
        if kind not in _POINTER_KINDS:
            return event

        position = gesture.get("position") or gesture.get("start")
        if position:
            event["absolute"] = list(position)
        if kind == "pointer.drag":
            event["end_absolute"] = list(gesture["end"])
            rect = (gesture.get("window") or {}).get("rect")
            event["end_relative"] = windows.relative_point(
                gesture["end"][0], gesture["end"][1], rect
            )
        return event
