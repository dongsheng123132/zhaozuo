"""Per-monitor DPI awareness.

不声明 DPI 感知的进程，拿到的坐标是被 Windows 虚拟化过的：GetWindowRect、
GetCursorPos、SetCursorPos 全部按**系统** DPI 折算。而 UIA 的 BoundingRectangle
是物理像素。两者在主屏带缩放的机器上会错开，UIA 定位到的按钮被点到别处。

麻烦的是这个失效模式**在系统 DPI 为 96 的开发机上复现不出来**（不缩放就没有
折算），只有主屏本身是 125% / 150% 的机器才看得到 —— 而那是大多数笔记本的
出厂设置。所以这里不靠本机验证，而是直接消除整类差异：进程声明 per-monitor
感知，坐标从此就是物理像素；同时把录制时的 DPI 记进档案，让不匹配变成可见的
信息，而不是一个静默的偏移。

必须在创建任何窗口之前调用。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


user32 = ctypes.windll.user32

#: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2，Windows 10 1703+。
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
PROCESS_PER_MONITOR_DPI_AWARE = 2

#: Windows 的 100% 缩放基准。
BASE_DPI = 96


def ensure_per_monitor_awareness() -> str:
    """Declare DPI awareness, best available API first. Returns what took effect.

    三级回退：1703+ 用 context API，8.1+ 用 shcore，更老只有全局 SetProcessDPIAware。
    已经声明过（例如清单里写了）会返回 already_set —— 那不是错误。
    """

    try:
        if user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return "per_monitor_v2"
    except (AttributeError, OSError):
        pass

    try:
        shcore = ctypes.windll.shcore
        result = shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        if result == 0:
            return "per_monitor"
        if result == -2147024891:  # E_ACCESSDENIED：已经设过了
            return "already_set"
    except (AttributeError, OSError):
        pass

    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "unaware"


def current_awareness() -> str:
    """What this process actually ended up with. 用于自检与报告。"""

    try:
        value = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
        return {0: "unaware", 1: "system", 2: "per_monitor"}.get(
            value.value, str(value.value)
        )
    except (AttributeError, OSError):
        return "unknown"


def window_dpi(hwnd: int) -> int:
    """Effective DPI of a window. 拿不到就退回系统 DPI，不猜。"""

    try:
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        dpi = int(user32.GetDpiForWindow(hwnd))
        if dpi:
            return dpi
    except (AttributeError, OSError, ValueError):
        pass
    return system_dpi()


def system_dpi() -> int:
    try:
        return int(user32.GetDpiForSystem()) or BASE_DPI
    except (AttributeError, OSError):
        return BASE_DPI


def scale_for(dpi: int | None) -> float:
    """DPI → 缩放倍数。120 -> 1.25。"""

    return (int(dpi) if dpi else BASE_DPI) / BASE_DPI
