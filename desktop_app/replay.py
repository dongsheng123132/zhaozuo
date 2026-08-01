from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

from desktop_app import windows
from shared.profile import ProfileError, resolve_action, validate_profile


PLACEHOLDER_ONLY = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")


class ReplayEngine:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def _wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.cancelled.is_set() or windows.key_down(0x1B):
                raise InterruptedError("执行已停止")
            time.sleep(min(0.03, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _action_id(profile: dict[str, Any]) -> str:
        actions = profile.get("actions", {})
        if len(actions) != 1:
            raise ProfileError("录制演示 Profile 必须恰好包含一个动作")
        return next(iter(actions))

    def run(
        self,
        profile: dict[str, Any],
        inputs: dict[str, str],
        execute: bool,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self.cancelled.clear()
        errors = validate_profile(profile)
        if errors:
            raise ProfileError("; ".join(errors))
        action_id = self._action_id(profile)
        action = resolve_action(profile, action_id, inputs)
        steps = action["steps"]
        if not execute:
            return {
                "ok": True,
                "mode": "plan_only",
                "action_id": action_id,
                "step_count": len(steps),
                "required_inputs": list(inputs),
                "executed": False,
            }

        started = time.monotonic()
        degraded: list[str] = []
        previous_offset = 0
        for index, step in enumerate(steps, 1):
            if self.cancelled.is_set():
                raise InterruptedError("执行已停止")
            offset = int(step.get("captured_offset_ms", previous_offset))
            if index > 1:
                self._wait(min(max((offset - previous_offset) / 1000, 0.08), 1.2))
            previous_offset = offset
            if progress:
                progress(f"{index}/{len(steps)}  {step.get('description', step['kind'])}")

            locator = step.get("locator", {})
            window_locator = locator.get("window", {})
            hwnd = windows.find_window(
                str(window_locator.get("title", "")),
                str(window_locator.get("class_name", "")),
            )
            if hwnd:
                windows.activate_window(hwnd)
                self._wait(0.08)

            kind = step["kind"]
            if kind == "pointer.click":
                x, y, used_fallback = windows.point_for_window(
                    hwnd,
                    locator.get("relative"),
                    locator.get("fallback_absolute", [0, 0]),
                )
                if used_fallback:
                    degraded.append(step["id"])
                windows.click(x, y, step.get("args", {}).get("button", "left"))
            elif kind == "keyboard.shortcut":
                windows.press_shortcut(list(step.get("args", {}).get("keys", [])))
            elif kind == "keyboard.press":
                windows.press_key(str(step.get("args", {}).get("key", "")))
            elif kind == "keyboard.type":
                windows.type_unicode(str(step.get("args", {}).get("text", "")))
            else:
                raise ProfileError(f"尚不支持真实回放步骤: {kind}")

        evidence_results: list[dict[str, Any]] = []
        for evidence in action.get("success_evidence", []):
            if evidence.get("kind") != "window.title_contains":
                continue
            expected = str(evidence.get("expected", "")).casefold()
            timeout = int(evidence.get("timeout_ms", 5000)) / 1000
            deadline = time.monotonic() + timeout
            matched = ""
            while time.monotonic() < deadline:
                matched = next(
                    (
                        title
                        for title in windows.visible_window_titles()
                        if expected in title.casefold()
                    ),
                    "",
                )
                if matched:
                    break
                self._wait(0.15)
            evidence_results.append(
                {
                    "kind": "window.title_contains",
                    "expected": evidence.get("expected"),
                    "ok": bool(matched),
                    "observed": matched,
                }
            )

        evidence_ok = bool(evidence_results) and all(
            item["ok"] for item in evidence_results
        )
        return {
            "ok": evidence_ok,
            "mode": "executed",
            "action_id": action_id,
            "step_count": len(steps),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "degraded_steps": degraded,
            "evidence": evidence_results,
            "executed": True,
            "error": None if evidence_ok else "动作已执行，但成功证据未通过",
        }
