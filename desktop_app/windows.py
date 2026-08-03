from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any


if not hasattr(ctypes, "windll"):
    raise RuntimeError("照做桌面演示器目前只支持 Windows")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 64 位下句柄是指针，ctypes 默认 restype=c_int 会截断。显式声明才安全。
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.OpenClipboard.argtypes = [wintypes.HWND]

SW_RESTORE = 9
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    # INPUT is a tagged union. Even though the replay currently emits only
    # keyboard input, Windows validates cbSize against the full native union.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


VK_NAMES: dict[int, str] = {
    0x08: "BACKSPACE",
    0x09: "TAB",
    0x0D: "ENTER",
    0x1B: "ESCAPE",
    0x20: "SPACE",
    0x21: "PAGEUP",
    0x22: "PAGEDOWN",
    0x23: "END",
    0x24: "HOME",
    0x25: "LEFT",
    0x26: "UP",
    0x27: "RIGHT",
    0x28: "DOWN",
    0x2D: "INSERT",
    0x2E: "DELETE",
    0x5B: "WIN",
    0x5C: "WIN",
    0x10: "SHIFT",
    0x11: "CTRL",
    0x12: "ALT",
}
for code in range(0x30, 0x3A):
    VK_NAMES[code] = chr(code)
for code in range(0x41, 0x5B):
    VK_NAMES[code] = chr(code)
for index in range(1, 13):
    VK_NAMES[0x6F + index] = f"F{index}"

NAME_TO_VK = {name: code for code, name in VK_NAMES.items()}


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_position() -> tuple[int, int]:
    point = POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


_PROCESS_CACHE: dict[int, str] = {}
_PROCESS_CACHE_LIMIT = 512


def process_name(hwnd: int) -> str:
    """Owning executable name of a window, lowercased, e.g. ``wechat.exe``.

    进程名是窗口身份里最可靠的一条证据：标题会随文件名和会话变，类名会随
    框架版本变，进程名基本不变。拿不到时返回空字符串（权限不足、进程已退出、
    或目标是更高完整性级别的进程），调用方必须把空值当成"不知道"，不是"不匹配"。
    """

    handle_key = int(hwnd or 0)
    if not handle_key:
        return ""
    cached = _PROCESS_CACHE.get(handle_key)
    if cached is not None:
        return cached

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle_key, ctypes.byref(pid))
    name = ""
    if pid.value:
        process = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if process:
            try:
                size = wintypes.DWORD(260)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(
                    process, 0, buffer, ctypes.byref(size)
                ):
                    name = buffer.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            finally:
                kernel32.CloseHandle(process)

    if len(_PROCESS_CACHE) >= _PROCESS_CACHE_LIMIT:
        _PROCESS_CACHE.clear()
    _PROCESS_CACHE[handle_key] = name
    return name


def window_context(hwnd: int | None = None) -> dict[str, Any]:
    handle = int(hwnd or user32.GetForegroundWindow())
    if not handle:
        return {"hwnd": 0, "title": "", "class_name": "", "process": "", "rect": None}

    length = user32.GetWindowTextLengthW(handle)
    title_buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, title_buffer, length + 1)
    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, class_buffer, 256)
    rect = RECT()
    has_rect = bool(user32.GetWindowRect(handle, ctypes.byref(rect)))
    return {
        "hwnd": handle,
        "title": title_buffer.value,
        "class_name": class_buffer.value,
        "process": process_name(handle),
        "rect": [rect.left, rect.top, rect.right, rect.bottom] if has_rect else None,
    }


def relative_point(x: int, y: int, rect: list[int] | None) -> dict[str, float] | None:
    if not rect:
        return None
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    return {
        "x": round((x - left) / width, 6),
        "y": round((y - top) / height, 6),
    }


def _enumerate_windows() -> list[int]:
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return found


#: 分数阈值。process 是最强证据，title 子串是最弱证据。
_SCORE_PROCESS = 4
_SCORE_CLASS = 3
_SCORE_TITLE_EXACT = 3
_SCORE_TITLE_PARTIAL = 1
_CONFIDENCE_EXACT = 7
_CONFIDENCE_STRONG = 4


def score_window_candidate(
    recorded: dict[str, Any], candidate: dict[str, Any]
) -> tuple[int, list[str]] | None:
    """Score one live window against a recorded window locator.

    返回 None 表示被否决 —— 进程或类名明确对不上，不是"弱匹配"而是"另一个软件"。
    否决比打低分重要：旧实现用标题子串找"微信"，会命中浏览器里打开的微信网页、
    文件传输助手、任何标题含"微信"的窗口，而最后一步是回车发送。
    """

    def norm(value: Any) -> str:
        return str(value or "").strip().casefold()

    recorded_process, candidate_process = norm(recorded.get("process")), norm(candidate.get("process"))
    recorded_class, candidate_class = norm(recorded.get("class_name")), norm(candidate.get("class_name"))
    recorded_title, candidate_title = norm(recorded.get("title")), norm(candidate.get("title"))

    if recorded_process and candidate_process and recorded_process != candidate_process:
        return None
    if recorded_class and candidate_class and recorded_class != candidate_class:
        return None

    score = 0
    reasons: list[str] = []
    if recorded_process and recorded_process == candidate_process:
        score += _SCORE_PROCESS
        reasons.append(f"process={candidate_process}")
    if recorded_class and recorded_class == candidate_class:
        score += _SCORE_CLASS
        reasons.append(f"class={candidate_class}")
    if recorded_title and recorded_title == candidate_title:
        score += _SCORE_TITLE_EXACT
        reasons.append("title=exact")
    elif recorded_title and candidate_title and (
        recorded_title in candidate_title or candidate_title in recorded_title
    ):
        score += _SCORE_TITLE_PARTIAL
        reasons.append("title=partial")

    return (score, reasons) if score else None


def resolve_window(recorded: dict[str, Any]) -> dict[str, Any]:
    """Resolve a recorded window locator to exactly one live window.

    与旧的 find_window 的关键差别：**歧义会被报出来，而不是默默取第一个**。
    高风险步骤宁可拒绝执行，也不能对着不确定的窗口按回车。
    """

    scored: list[tuple[int, int, dict[str, Any], list[str]]] = []
    for hwnd in _enumerate_windows():
        context = window_context(hwnd)
        result = score_window_candidate(recorded, context)
        if result:
            scored.append((result[0], hwnd, context, result[1]))

    if not scored:
        return {
            "hwnd": None, "confidence": "none", "ambiguous": False,
            "candidates": 0, "reasons": ["no_window_matched"], "context": None,
        }

    top_score = max(item[0] for item in scored)
    winners = [item for item in scored if item[0] == top_score]
    score, hwnd, context, reasons = winners[0]
    confidence = (
        "exact" if score >= _CONFIDENCE_EXACT
        else "strong" if score >= _CONFIDENCE_STRONG
        else "weak"
    )
    return {
        "hwnd": hwnd,
        "confidence": confidence,
        "ambiguous": len(winners) > 1,
        "candidates": len(winners),
        "reasons": reasons + ([f"tied_candidates={len(winners)}"] if len(winners) > 1 else []),
        "context": context,
    }


def target_fingerprint(context: dict[str, Any] | None) -> str:
    """Stable identity string shown to the human and re-checked before the effect."""

    if not context:
        return ""
    return "|".join(
        str(context.get(key) or "").strip().casefold()
        for key in ("process", "class_name", "title")
    )


def find_window(title: str, class_name: str = "") -> int | None:
    """Backwards-compatible lookup. 新代码请用 resolve_window。"""

    return resolve_window({"title": title, "class_name": class_name})["hwnd"]


def activate_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


def point_for_window(
    hwnd: int | None,
    relative: dict[str, float] | None,
    absolute: list[int],
) -> tuple[int, int, bool]:
    if hwnd and relative:
        context = window_context(hwnd)
        rect = context.get("rect")
        if rect:
            left, top, right, bottom = rect
            return (
                round(left + (right - left) * float(relative["x"])),
                round(top + (bottom - top) * float(relative["y"])),
                False,
            )
    return int(absolute[0]), int(absolute[1]), True


def click(x: int, y: int, button: str = "left") -> None:
    user32.SetCursorPos(int(x), int(y))
    if button == "right":
        down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    else:
        down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    user32.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.04)
    user32.mouse_event(up, 0, 0, 0, 0)


def press_key(name: str) -> None:
    vk = NAME_TO_VK.get(name.upper())
    if vk is None:
        raise ValueError(f"不支持的按键: {name}")
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def press_shortcut(keys: list[str]) -> None:
    virtual_keys = [NAME_TO_VK.get(key.upper()) for key in keys]
    if any(vk is None for vk in virtual_keys):
        raise ValueError(f"不支持的快捷键: {'+'.join(keys)}")
    for vk in virtual_keys:
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.01)
    for vk in reversed(virtual_keys):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)


def type_unicode(text: str) -> None:
    units = text.encode("utf-16-le")
    for index in range(0, len(units), 2):
        code_unit = int.from_bytes(units[index : index + 2], "little")
        down = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE, 0, 0)
            ),
        )
        up = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(
                    0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0
                )
            ),
        )
        batch = (INPUT * 2)(down, up)
        sent = user32.SendInput(2, batch, ctypes.sizeof(INPUT))
        if sent != 2:
            raise OSError("SendInput 未能发送完整文本")


TH32CS_SNAPPROCESS = 0x00000002
CF_UNICODETEXT = 13
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


def count_windows(locator: dict[str, Any]) -> int:
    """How many live windows match a locator. 用于 window.exists 证据。"""

    return sum(
        1
        for hwnd in _enumerate_windows()
        if score_window_candidate(locator, window_context(hwnd))
    )


def process_names() -> set[str]:
    """All running process executable names, lowercased.

    走 Toolhelp 快照而不是"有可见窗口的进程"：无窗口的后台服务同样算在运行。
    """

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return set()
    names: set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                names.add(str(entry.szExeFile).lower())
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def clipboard_text() -> str:
    """Current clipboard text, or empty. 打不开剪贴板时返回空而不是抛错。"""

    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    except OSError:
        return ""
    finally:
        user32.CloseClipboard()


def visible_window_titles() -> list[str]:
    titles: list[str] = []
    for hwnd in _enumerate_windows():
        title = str(window_context(hwnd)["title"]).strip()
        if title:
            titles.append(title)
    return titles
