"""Live implementation of EvidenceProbe.

证据引擎里没有一行 Windows API —— 全部经过这里。分层的意义在于：
业务判断对不对用假 probe 毫秒级断言，真机只用来验这一层薄壳本身。
"""

from __future__ import annotations

from typing import Any

from desktop_app import uia, windows


class LiveProbe:
    """Reads the real machine. 每个方法失败都返回"空/否"，不抛给证据引擎。"""

    def window_titles(self) -> list[str]:
        return windows.visible_window_titles()

    def window_matches(self, locator: dict[str, Any]) -> int:
        return windows.count_windows(locator)

    def process_names(self) -> set[str]:
        return windows.process_names()

    def clipboard_text(self) -> str:
        return windows.clipboard_text()

    def _hwnd_for(self, locator: dict[str, Any]) -> int | None:
        """Scope a UIA lookup to one window when the evidence names one."""

        window = locator.get("window")
        if not isinstance(window, dict) or not window:
            return None
        match = windows.resolve_window(window)
        # 窗口不唯一时不猜：返回 None 让 UIA 从桌面根找，命不中就记未通过。
        return None if match.get("ambiguous") else match.get("hwnd")

    @staticmethod
    def _control(locator: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in locator.items() if key != "window"}

    def uia_exists(self, locator: dict[str, Any]) -> bool:
        return bool(uia.find_element(self._control(locator), self._hwnd_for(locator)))

    def uia_value(self, locator: dict[str, Any]) -> str | None:
        return uia.element_value(self._control(locator), self._hwnd_for(locator))

    def uia_toggle_state(self, locator: dict[str, Any]) -> str | None:
        return uia.toggle_state(self._control(locator), self._hwnd_for(locator))
