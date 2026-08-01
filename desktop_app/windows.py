from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any


if not hasattr(ctypes, "windll"):
    raise RuntimeError("照做桌面演示器目前只支持 Windows")


user32 = ctypes.windll.user32

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


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


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


def window_context(hwnd: int | None = None) -> dict[str, Any]:
    handle = int(hwnd or user32.GetForegroundWindow())
    if not handle:
        return {"hwnd": 0, "title": "", "class_name": "", "rect": None}

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


def find_window(title: str, class_name: str = "") -> int | None:
    title_folded = title.casefold().strip()
    best_partial: int | None = None
    for hwnd in _enumerate_windows():
        context = window_context(hwnd)
        if class_name and context["class_name"] != class_name:
            continue
        candidate = str(context["title"])
        if candidate.casefold() == title_folded and title_folded:
            return hwnd
        if title_folded and title_folded in candidate.casefold():
            best_partial = best_partial or hwnd
    return best_partial


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


def visible_window_titles() -> list[str]:
    titles: list[str] = []
    for hwnd in _enumerate_windows():
        title = str(window_context(hwnd)["title"]).strip()
        if title:
            titles.append(title)
    return titles
