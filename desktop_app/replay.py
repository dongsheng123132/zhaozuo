from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

from desktop_app import uia, windows
from desktop_app.probe import LiveProbe
from shared.evidence import capture_baseline, check_all, summarize
from shared.profile import ProfileError, resolve_action, validate_profile


PLACEHOLDER_ONLY = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")


class ReplayEngine:
    MIN_INTER_STEP_WAIT_SECONDS = 0.08
    MAX_INTER_STEP_WAIT_SECONDS = 12.0

    def __init__(self, probe: Any | None = None) -> None:
        self.cancelled = threading.Event()
        # 证据检查的全部系统访问都经过 probe，测试可注入假实现无界面断言。
        self.probe = probe or LiveProbe()

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

    #: 等待就绪的轮询间隔与预算上下限。
    READINESS_POLL_SECONDS = 0.05
    MIN_READINESS_BUDGET_SECONDS = 1.5
    MAX_READINESS_BUDGET_SECONDS = 30.0

    @staticmethod
    def readiness_conditions(step: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        """What must be observably true before this step may run.

        录制时人等了多久，只说明当时那台机器有多快 —— 换台机器、网络慢一点，
        同一个 800ms 就不够了。能观察的锚点（窗口在不在、控件出来没有）才是
        真正的前置条件；照秒表睡是没有锚点时的兜底，而且必须被记为降级。
        """

        declared = step.get("wait_for")
        if step.get("kind") == "wait.for_evidence":
            declared = declared or (step.get("args") or {}).get("evidence")
        if isinstance(declared, dict):
            declared = [declared]
        if isinstance(declared, list) and declared:
            return [item for item in declared if isinstance(item, dict)], "declared"

        locator = step.get("locator") or {}
        window = locator.get("window") or {}
        uia = locator.get("uia")
        conditions: list[dict[str, Any]] = []
        if window:
            conditions.append({"kind": "window.exists", "window": window})
        if isinstance(uia, dict) and uia:
            conditions.append(
                {"kind": "uia.element_exists", "locator": {**uia, "window": window}}
            )
            return conditions, "anchored"
        return conditions, "unanchored"

    @classmethod
    def readiness_budget(cls, step: dict[str, Any], recorded_delay: float) -> float:
        """How long to keep waiting. 录制间隔只当提示，不当机制。"""

        declared = step.get("timeout_ms")
        if declared:
            return max(float(declared) / 1000, 0.0)
        return min(
            max(recorded_delay * 2, cls.MIN_READINESS_BUDGET_SECONDS),
            cls.MAX_READINESS_BUDGET_SECONDS,
        )

    def _await_readiness(
        self, step: dict[str, Any], recorded_delay: float, is_first: bool
    ) -> dict[str, Any]:
        conditions, source = self.readiness_conditions(step)
        started = time.monotonic()

        if source == "unanchored" or not conditions:
            # 没有可观察的锚点，只能回落到录制间隔。这是降级，必须能被数出来。
            if not is_first:
                self._wait(recorded_delay)
            return {
                "step_id": step["id"],
                "mode": "timed",
                "source": source,
                "waited_ms": round((time.monotonic() - started) * 1000),
            }

        budget = self.readiness_budget(step, recorded_delay)
        deadline = started + budget
        results = check_all(conditions, self.probe)
        while not all(item["ok"] for item in results) and time.monotonic() < deadline:
            self._wait(self.READINESS_POLL_SECONDS)
            results = check_all(conditions, self.probe)

        ready = all(item["ok"] for item in results)
        return {
            "step_id": step["id"],
            "mode": "observed" if ready else "timeout",
            "source": source,
            "waited_ms": round((time.monotonic() - started) * 1000),
            "budget_ms": round(budget * 1000),
            "unmet": [
                {"kind": item.get("kind"), "reason": item.get("reason")}
                for item in results
                if not item["ok"]
            ],
        }

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
        # 变化类证据（文件改没改、内容更新没有）必须先拍基线，否则无法归因到本次动作。
        baseline = capture_baseline(action.get("success_evidence", []))
        degraded: list[str] = []
        locator_results: list[dict[str, str]] = []
        readiness_results: list[dict[str, Any]] = []
        timed_steps: list[str] = []
        unready_steps: list[str] = []
        executed_step_count = 0
        previous_offset = 0
        for index, step in enumerate(steps, 1):
            if self.cancelled.is_set():
                raise InterruptedError("执行已停止")
            offset = int(step.get("captured_offset_ms", previous_offset))
            if progress:
                progress(f"{index}/{len(steps)}  {step.get('description', step['kind'])}")

            readiness = self._await_readiness(
                step, self.replay_delay(previous_offset, offset), is_first=index == 1
            )
            previous_offset = offset
            readiness_results.append(readiness)
            if readiness["mode"] == "timed":
                timed_steps.append(step["id"])
            elif readiness["mode"] == "timeout":
                unready_steps.append(step["id"])
                if readiness["source"] == "declared":
                    # 档案作者明确声明的前置条件没满足 —— 继续往下点就是往空处点。
                    return {
                        "ok": False,
                        "mode": "readiness_timeout",
                        "action_id": action_id,
                        "step_count": len(all_steps),
                        "executed_step_count": executed_step_count,
                        "executed": bool(executed_step_count),
                        "readiness_results": readiness_results,
                        "degraded_steps": degraded,
                        "locator_results": locator_results,
                        "error": (
                            f"步骤 {step['id']} 声明的前置条件在 "
                            f"{readiness['budget_ms']}ms 内没有满足，已停止"
                        ),
                    }

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
                        "readiness_results": readiness_results,
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
                        "readiness_results": readiness_results,
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
            elif kind == "wait.for_evidence":
                # 等待本身就是这一步的全部内容，readiness 已经完成，不碰键鼠。
                locator_results.append({"step_id": step["id"], "used": "evidence"})
            else:
                raise ProfileError(f"尚不支持真实回放步骤: {kind}")
            executed_step_count += 1

        evidence_results = check_all(
            action.get("success_evidence", []),
            self.probe,
            baseline,
            wait=self._wait,
        )
        verdict = summarize(evidence_results)
        return {
            "ok": verdict["ok"],
            "mode": "executed",
            "action_id": action_id,
            "step_count": len(all_steps),
            "executed_step_count": executed_step_count,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "degraded_steps": degraded,
            "timed_steps": timed_steps,
            "unready_steps": unready_steps,
            "readiness_results": readiness_results,
            "locator_results": locator_results,
            "evidence": evidence_results,
            "evidence_summary": verdict,
            "executed": True,
            "error": None if verdict["ok"] else f"动作已执行，但{verdict['reason']}",
        }
