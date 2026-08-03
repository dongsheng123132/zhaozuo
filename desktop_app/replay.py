from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

from desktop_app import uia, windows
from shared.profile import ProfileError, resolve_action, validate_profile


PLACEHOLDER_ONLY = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")


class ReplayEngine:
    MIN_INTER_STEP_WAIT_SECONDS = 0.08
    MAX_INTER_STEP_WAIT_SECONDS = 12.0

    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def reset(self) -> None:
        self.cancelled.clear()

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

    #: 对外动作只接受这两档窗口身份。weak（仅标题子串命中）不够 —— 那正是
    #: "把消息发给同名的另一个窗口"的入口。
    TRUSTED_TARGET_CONFIDENCE = frozenset({"exact", "strong"})

    @staticmethod
    def _target_report(match: dict[str, Any]) -> dict[str, Any]:
        context = match.get("context") or {}
        return {
            "title": context.get("title", ""),
            "process": context.get("process", ""),
            "class_name": context.get("class_name", ""),
            "confidence": match.get("confidence"),
            "ambiguous": match.get("ambiguous", False),
            "candidates": match.get("candidates", 0),
            "reasons": match.get("reasons", []),
            "fingerprint": windows.target_fingerprint(match.get("context")),
        }

    @classmethod
    def _target_refusal(
        cls, match: dict[str, Any], confirmed_target: str | None
    ) -> str | None:
        """Why this outward action must not fire. None 表示目标可信。"""

        if not match.get("hwnd"):
            return "找不到目标窗口"
        if match.get("ambiguous"):
            return f"目标窗口不唯一，有 {match.get('candidates')} 个同分候选"
        if match.get("confidence") not in cls.TRUSTED_TARGET_CONFIDENCE:
            return f"窗口身份证据不足（{match.get('confidence')}），无法确定是同一个目标"
        if confirmed_target and windows.target_fingerprint(match.get("context")) != confirmed_target:
            # 人确认的是 A 窗口，真要动手时前台变成了 B —— 这一步必须失败，不能顺手发出去。
            return "确认之后目标窗口已经变了"
        return None

    @classmethod
    def replay_delay(cls, previous_offset_ms: int, offset_ms: int) -> float:
        """Preserve UI settle time while bounding accidental long pauses."""

        captured = max((offset_ms - previous_offset_ms) / 1000, 0.0)
        return min(
            max(captured, cls.MIN_INTER_STEP_WAIT_SECONDS),
            cls.MAX_INTER_STEP_WAIT_SECONDS,
        )

    def run(
        self,
        profile: dict[str, Any],
        inputs: dict[str, str],
        execute: bool,
        progress: Callable[[str], None] | None = None,
        confirmed_effect_step_id: str | None = None,
        start_step_id: str | None = None,
        confirmed_target: str | None = None,
    ) -> dict[str, Any]:
        errors = validate_profile(profile)
        if errors:
            raise ProfileError("; ".join(errors))
        action_id = self._action_id(profile)
        action = resolve_action(profile, action_id, inputs)
        all_steps = action["steps"]
        steps = all_steps
        if start_step_id:
            start_indexes = [
                index for index, step in enumerate(all_steps) if step["id"] == start_step_id
            ]
            if not start_indexes:
                raise ProfileError(f"找不到待确认步骤: {start_step_id}")
            steps = all_steps[start_indexes[0] :]
        if not execute:
            effects = [
                {"step_id": step["id"], **step["effect"]}
                for step in all_steps
                if isinstance(step.get("effect"), dict)
            ]
            return {
                "ok": True,
                "mode": "plan_only",
                "action_id": action_id,
                "step_count": len(all_steps),
                "required_inputs": list(inputs),
                "effects_requiring_confirmation": effects,
                "executed": False,
            }

        started = time.monotonic()
        degraded: list[str] = []
        locator_results: list[dict[str, str]] = []
        executed_step_count = 0
        previous_offset = 0
        for index, step in enumerate(steps, 1):
            if self.cancelled.is_set():
                raise InterruptedError("执行已停止")
            offset = int(step.get("captured_offset_ms", previous_offset))
            if index > 1:
                self._wait(self.replay_delay(previous_offset, offset))
            previous_offset = offset
            if progress:
                progress(f"{index}/{len(steps)}  {step.get('description', step['kind'])}")

            locator = step.get("locator", {})
            window_locator = locator.get("window", {})
            match = windows.resolve_window(window_locator)
            hwnd = match["hwnd"]

            effect = step.get("effect")
            if isinstance(effect, dict):
                if step["id"] != confirmed_effect_step_id:
                    # 人要确认的不是"要不要发"，是"发给谁"。把解析出的目标一并交出去。
                    return {
                        "ok": False,
                        "mode": "awaiting_confirmation",
                        "action_id": action_id,
                        "step_count": len(all_steps),
                        "executed_step_count": executed_step_count,
                        "executed": bool(executed_step_count),
                        "pending_effect": {"step_id": step["id"], **effect},
                        "target": self._target_report(match),
                        "degraded_steps": degraded,
                        "locator_results": locator_results,
                        "error": "已停在最终对外动作前，等待当下确认",
                    }
                refusal = self._target_refusal(match, confirmed_target)
                if refusal:
                    return {
                        "ok": False,
                        "mode": "target_unverified",
                        "action_id": action_id,
                        "step_count": len(all_steps),
                        "executed_step_count": executed_step_count,
                        "executed": bool(executed_step_count),
                        "pending_effect": {"step_id": step["id"], **effect},
                        "target": self._target_report(match),
                        "degraded_steps": degraded,
                        "locator_results": locator_results,
                        "error": f"拒绝执行对外动作：{refusal}",
                    }

            if hwnd:
                windows.activate_window(hwnd)
                self._wait(0.08)

            kind = step["kind"]
            if kind == "pointer.click":
                bounds = None
                if hwnd and isinstance(locator.get("uia"), dict):
                    bounds = uia.find_bounds(
                        hwnd,
                        locator["uia"],
                        windows.window_context(hwnd).get("rect"),
                    )
                if bounds:
                    x = round((bounds[0] + bounds[2]) / 2)
                    y = round((bounds[1] + bounds[3]) / 2)
                    locator_used = "uia"
                else:
                    x, y, used_fallback = windows.point_for_window(
                        hwnd,
                        locator.get("relative"),
                        locator.get("fallback_absolute", [0, 0]),
                    )
                    locator_used = "absolute" if used_fallback else "window_relative"
                    if used_fallback:
                        degraded.append(step["id"])
                locator_results.append({"step_id": step["id"], "used": locator_used})
                windows.click(x, y, step.get("args", {}).get("button", "left"))
            elif kind == "keyboard.shortcut":
                if hwnd and isinstance(locator.get("uia"), dict):
                    uia.focus(hwnd, locator["uia"])
                windows.press_shortcut(list(step.get("args", {}).get("keys", [])))
                locator_results.append({"step_id": step["id"], "used": "focused_window"})
            elif kind == "keyboard.press":
                if hwnd and isinstance(locator.get("uia"), dict):
                    uia.focus(hwnd, locator["uia"])
                windows.press_key(str(step.get("args", {}).get("key", "")))
                locator_results.append({"step_id": step["id"], "used": "focused_window"})
            elif kind == "keyboard.type":
                focused = bool(
                    hwnd
                    and isinstance(locator.get("uia"), dict)
                    and uia.focus(hwnd, locator["uia"])
                )
                windows.type_unicode(str(step.get("args", {}).get("text", "")))
                locator_results.append(
                    {"step_id": step["id"], "used": "uia_focus" if focused else "focused_window"}
                )
            else:
                raise ProfileError(f"尚不支持真实回放步骤: {kind}")
            executed_step_count += 1

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
            "step_count": len(all_steps),
            "executed_step_count": executed_step_count,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "degraded_steps": degraded,
            "locator_results": locator_results,
            "evidence": evidence_results,
            "executed": True,
            "error": None if evidence_ok else "动作已执行，但成功证据未通过",
        }
