"""Global low-level input hooks.

旧录制器每 20ms 轮询一次 GetAsyncKeyState。代价不只是丢事件，还有一条更难查的：
轮询循环里同步调 UIA，跨进程 COM 常见 50-500ms，这段时间采样完全停摆 ——
**软件越复杂 UIA 越慢，丢的步骤越多**，正好在最需要录准的地方最不准。

低级钩子按事件送达，不存在采样窗口。但它有一条硬约束：**回调必须极快**。
超过 LowLevelHooksTimeout（默认 300ms）系统会直接把钩子摘掉，且不通知你。
所以这里的回调只做一件事 —— 把原始记录塞进队列，立刻返回。窗口查询、UIA、
手势装配全部在别的线程做。
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from typing import Any


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_QUIT = 0x0012
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C

LLKHF_INJECTED = 0x00000010
LLMHF_INJECTED = 0x00000001

_BUTTON_MESSAGES: dict[int, tuple[str, str]] = {
    WM_LBUTTONDOWN: ("mouse.down", "left"),
    WM_LBUTTONUP: ("mouse.up", "left"),
    WM_RBUTTONDOWN: ("mouse.down", "right"),
    WM_RBUTTONUP: ("mouse.up", "right"),
    WM_MBUTTONDOWN: ("mouse.down", "middle"),
    WM_MBUTTONUP: ("mouse.up", "middle"),
    WM_XBUTTONDOWN: ("mouse.down", "x"),
    WM_XBUTTONUP: ("mouse.up", "x"),
}

_KEY_MESSAGES: dict[int, str] = {
    WM_KEYDOWN: "key.down",
    WM_SYSKEYDOWN: "key.down",
    WM_KEYUP: "key.up",
    WM_SYSKEYUP: "key.up",
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def _high_word_signed(value: int) -> int:
    """mouseData 的高 16 位是有符号滚轮增量。"""

    delta = (int(value) >> 16) & 0xFFFF
    return delta - 0x10000 if delta & 0x8000 else delta


class InputHookListener:
    """Install global keyboard+mouse hooks and push raw records onto a queue.

    队列而不是回调：钩子回调跑在系统的输入线程上，任何在那里做的事都直接
    拖慢整台机器的输入响应。入队是唯一足够便宜的动作。
    """

    def __init__(self, records: "queue.Queue[dict[str, Any]]") -> None:
        self.records = records
        self.dropped = 0
        self.started_at = 0.0
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: BaseException | None = None
        # 回调对象必须被强引用住：被 GC 掉之后系统仍会调用它，直接崩溃。
        self._keyboard_proc = HOOKPROC(self._on_keyboard)
        self._mouse_proc = HOOKPROC(self._on_mouse)
        self._hooks: list[int] = []

    # ------------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, timeout: float = 3.0) -> None:
        if self.running:
            return
        self.started_at = time.monotonic()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("输入钩子安装超时")
        if self._error:
            raise self._error

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None
        self._thread_id = 0

    def _pump(self) -> None:
        """Hooks must be installed and pumped on the same thread."""

        self._thread_id = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        try:
            for hook_id, proc in (
                (WH_KEYBOARD_LL, self._keyboard_proc),
                (WH_MOUSE_LL, self._mouse_proc),
            ):
                handle = user32.SetWindowsHookExW(hook_id, proc, module, 0)
                if not handle:
                    raise OSError(
                        ctypes.get_last_error(),
                        f"SetWindowsHookExW({hook_id}) 失败",
                    )
                self._hooks.append(handle)
        except BaseException as exc:  # 安装失败要让 start() 看得见
            self._error = exc
            self._ready.set()
            self._release()
            return

        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        self._release()

    def _release(self) -> None:
        for handle in self._hooks:
            user32.UnhookWindowsHookEx(handle)
        self._hooks.clear()

    # -------------------------------------------------------------- callbacks

    def _offset_ms(self) -> int:
        return round((time.monotonic() - self.started_at) * 1000)

    def _emit(self, record: dict[str, Any]) -> None:
        try:
            self.records.put_nowait(record)
        except queue.Full:
            # 宁可丢一条并把它数出来，也不能在钩子里阻塞 —— 那会卡住整机输入。
            self.dropped += 1

    def _on_keyboard(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0:
            data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            kind = _KEY_MESSAGES.get(int(wparam))
            if kind:
                self._emit(
                    {
                        "kind": kind,
                        "vk": int(data.vkCode),
                        "scan": int(data.scanCode),
                        "injected": bool(data.flags & LLKHF_INJECTED),
                        "t": self._offset_ms(),
                    }
                )
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0:
            message = int(wparam)
            if message != WM_MOUSEMOVE:  # 鼠标移动不构成业务动作，且量极大
                data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                record = self._mouse_record(message, data)
                if record:
                    self._emit(record)
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _mouse_record(self, message: int, data: Any) -> dict[str, Any] | None:
        common = {
            "x": int(data.pt.x),
            "y": int(data.pt.y),
            "injected": bool(data.flags & LLMHF_INJECTED),
            "t": self._offset_ms(),
        }
        if message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            return {
                "kind": "mouse.wheel",
                "delta": _high_word_signed(data.mouseData),
                "horizontal": message == WM_MOUSEHWHEEL,
                **common,
            }
        button_message = _BUTTON_MESSAGES.get(message)
        if button_message:
            kind, button = button_message
            return {"kind": kind, "button": button, **common}
        return None
