from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.action_id import (
    derive_action_id,
    derive_profile_id,
    demo_suffix,
    sanitize_action_id,
)
from shared.effects import assess, classify_effect, infer_effect
from shared.profile import CURRENT_VERSION


__all__ = [
    "assess",
    "build_profile",
    "classify_effect",
    "event_summary",
    "infer_effect",
    "merge_recording_segments",
    "replayable_events",
    "save_recording",
]

IGNORED_WINDOW_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Progman",
    "WorkerW",
}


def replayable_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop activation noise that cannot safely be replayed later."""

    result: list[dict[str, Any]] = []
    for event in events:
        window = event.get("window", {})
        if not int(window.get("hwnd") or 0):
            continue
        if str(window.get("class_name", "")) in IGNORED_WINDOW_CLASSES:
            continue
        if str(window.get("title", "")).startswith("照做"):
            continue
        result.append(event)
    return result


def merge_recording_segments(
    existing: list[dict[str, Any]],
    appended: list[dict[str, Any]],
    gap_ms: int = 800,
) -> list[dict[str, Any]]:
    """Append a recording segment while keeping offsets monotonic.

    Every EventRecorder starts its own clock at zero. Profiles need one shared
    timeline, so a resumed segment is shifted to begin shortly after the last
    event of the previous segment.
    """

    merged: list[dict[str, Any]] = []
    for event in existing:
        copied = dict(event)
        copied["segment_index"] = int(copied.get("segment_index") or 1)
        merged.append(copied)
    if not appended:
        return merged
    if not merged:
        return [
            {
                **event,
                "offset_ms": int(event.get("offset_ms") or 0),
                "segment_index": int(event.get("segment_index") or 1),
            }
            for event in appended
        ]

    next_segment = max(
        (int(event.get("segment_index") or 1) for event in merged),
        default=0,
    ) + 1
    first_offset = min(int(event.get("offset_ms") or 0) for event in appended)
    last_offset = max(
        (int(event.get("offset_ms") or 0) for event in merged),
        default=first_offset - max(gap_ms, 0),
    )
    shift = last_offset + max(gap_ms, 0) - first_offset
    for event in appended:
        copied = dict(event)
        copied["offset_ms"] = int(copied.get("offset_ms") or 0) + shift
        copied["segment_index"] = next_segment
        merged.append(copied)
    return merged


def event_summary(event: dict[str, Any]) -> str:
    window = str(event.get("window", {}).get("title", "")).strip() or "未知窗口"
    short_window = window if len(window) <= 34 else window[:31] + "…"
    kind = event.get("kind")
    button = {"left": "", "right": "右键", "middle": "中键"}.get(
        str(event.get("button", "left")), ""
    )
    if kind == "pointer.click":
        return f"{button}点击 · {short_window}"
    if kind == "pointer.double_click":
        return f"{button}双击 · {short_window}"
    if kind == "pointer.drag":
        return f"{button}拖拽 · {short_window}"
    if kind == "pointer.wheel":
        delta = int(event.get("delta", 0))
        direction = "横向滚动" if event.get("horizontal") else ("向上滚动" if delta > 0 else "向下滚动")
        return f"{direction} {abs(delta) // 120} 格 · {short_window}"
    if kind == "keyboard.shortcut":
        return f"快捷键 {'+'.join(event.get('keys', []))} · {short_window}"
    if kind == "keyboard.press":
        return f"按键 {event.get('key')} · {short_window}"
    if kind == "text.input":
        return (
            f"输入变量 ${{{event.get('placeholder')}}} "
            f"（原文已遮蔽，约 {event.get('key_count')} 键）"
        )
    return str(kind)


def build_profile(
    session_id: str,
    goal: str,
    events: list[dict[str, Any]],
    success_title: str,
    final_effect: bool | None = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    events = replayable_events(events)
    properties: dict[str, Any] = {}
    required: list[str] = []
    steps: list[dict[str, Any]] = []

    inferred_effect = infer_effect(goal, events)
    should_mark_effect = bool(inferred_effect) if final_effect is None else final_effect
    effect = inferred_effect or {
        "type": "representational_communication",
        "kind": "external_effect",
        "label": "最终对外动作",
        "confirmation": "always",
        "action_id": derive_action_id(goal),
        "confidence": "user_declared" if final_effect else "none",
        "reasons": ["user_marked_final_effect"] if final_effect else [],
    }
    text_event_indexes = [
        index for index, event in enumerate(events) if event.get("kind") == "text.input"
    ]
    semantic_input_names: dict[int, str] = {}
    if effect.get("kind") == "send_message" and len(text_event_indexes) >= 2:
        # 会话目标和正文是两个语义完全不同的输入：一个决定发给谁（发错人不可撤销），
        # 一个决定发什么。合并成 text_1/text_2 会让调用方分不清哪个是收件人。
        semantic_input_names[text_event_indexes[0]] = "conversation"
        semantic_input_names[text_event_indexes[-1]] = "reply_text"

    for event_index, event in enumerate(events):
        index = event_index + 1
        kind = event.get("kind")
        window = event.get("window", {})
        window_locator = {
            "title": window.get("title", ""),
            "class_name": window.get("class_name", ""),
        }
        if window.get("process"):
            window_locator["process"] = window["process"]
        if window.get("dpi"):
            # 记下录制时的缩放。定位不靠它，但换台机器缩放不同时，
            # 这是唯一能解释"坐标为什么对不上"的线索。
            window_locator["dpi"] = int(window["dpi"])
        base: dict[str, Any] = {
            "id": f"step_{index:03d}",
            "description": event_summary(event),
            "captured_offset_ms": event.get("offset_ms", 0),
        }
        if kind in ("pointer.click", "pointer.double_click", "pointer.drag", "pointer.wheel"):
            locator = {
                "window": window_locator,
                "relative": event.get("relative"),
                "fallback_absolute": event.get("absolute"),
            }
            if event.get("uia"):
                locator["uia"] = event["uia"]
            if kind == "pointer.wheel":
                args: dict[str, Any] = {
                    "delta": int(event.get("delta", 0)),
                    "horizontal": bool(event.get("horizontal")),
                }
            else:
                args = {"button": event.get("button", "left")}
            if kind == "pointer.drag":
                # 终点和起点一样重要：拖到哪里决定了选中了什么、放到了哪。
                args["end_relative"] = event.get("end_relative")
                args["end_absolute"] = event.get("end_absolute")
            base.update({"kind": kind, "locator": locator, "args": args})
        elif kind == "keyboard.shortcut":
            locator = {"window": window_locator}
            if event.get("uia"):
                locator["uia"] = event["uia"]
            base.update(
                {
                    "kind": "keyboard.shortcut",
                    "locator": locator,
                    "args": {"keys": event.get("keys", [])},
                }
            )
        elif kind == "keyboard.press":
            locator = {"window": window_locator}
            if event.get("uia"):
                locator["uia"] = event["uia"]
            base.update(
                {
                    "kind": "keyboard.press",
                    "locator": locator,
                    "args": {"key": event.get("key")},
                }
            )
        elif kind == "text.input":
            name = semantic_input_names.get(event_index, str(event["placeholder"]))
            base["description"] = (
                f"输入变量 ${{{name}}} "
                f"（原文已遮蔽，约 {event.get('key_count')} 键）"
            )
            properties[name] = {
                "type": "string",
                "description": (
                    "目标微信会话；执行日志应最小化记录"
                    if name == "conversation"
                    else "微信回复正文；日志必须遮蔽"
                    if name == "reply_text"
                    else "回放时提供的文字；演示原文未被保存"
                ),
            }
            required.append(name)
            locator = {"window": window_locator}
            if event.get("uia"):
                locator["uia"] = event["uia"]
            base.update(
                {
                    "kind": "keyboard.type",
                    "locator": locator,
                    "args": {"text": f"${{{name}}}"},
                    "log_policy": "redact",
                }
            )
        else:
            continue
        steps.append(base)

    if should_mark_effect and steps:
        # `action_id` 在效果块里指的是**效果分类**（这是"哪一类对外动作"），
        # 和档案的动作键（这是"哪一个具体动作"）不是一回事。早期实现把两者混用，
        # 导致所有微信回复共用一个 ID。这里改名 effect_id，语义不再重叠。
        steps[-1]["effect"] = {
            ("effect_id" if key == "action_id" else key): value
            for key, value in effect.items()
        }
        steps[-1]["effect"].setdefault("reversible", False)
        # 显式写出目标断言，即使全是默认值 —— 档案要能自证"发给谁被断言过"。
        steps[-1]["target_assertion"] = {
            "min_confidence": "strong",
            "require_unique": True,
            "reconfirm_if_changed": True,
        }

    evidence: list[dict[str, Any]] = []
    if success_title.strip():
        evidence.append(
            {
                "kind": "window.title_contains",
                "expected": success_title.strip(),
                "timeout_ms": 5000,
            }
        )

    # 录制器发的是**实例 ID**：效果分类 + 目标指纹，保证两次不同的演示不撞车。
    # 规范 ID（如 wechat.reply_message）是被承诺的接口，只能由人在提升到
    # validated 时通过 action_id 参数授予，录制器不自封。
    default_action_id = (
        f"{effect['action_id']}.{demo_suffix(goal)}"
        if should_mark_effect
        else derive_action_id(goal)
    )
    resolved_action_id = sanitize_action_id(action_id, default_action_id)
    return {
        "profile_version": CURRENT_VERSION,
        "profile_id": derive_profile_id(goal),
        "kind": "external_ui_adapter",
        "status": "draft",
        "application": {
            "id": "windows.desktop.workflow",
            "name": "Windows 桌面工作流",
            "platform": "windows",
            "discovery": {"window": {"strategy": "captured_windows"}},
        },
        "actions": {
            resolved_action_id: {
                "title": goal.strip(),
                "description": "由照做录制器从一次人工演示生成的候选动作，尚需变化回放验证。",
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                "risk": "high" if should_mark_effect else "medium",
                "confirmation": "before_effect",
                "steps": steps,
                "success_evidence": evidence,
                "learned_from": [session_id],
            }
        },
    }


def save_recording(
    session_dir: Path,
    session_id: str,
    goal: str,
    events: list[dict[str, Any]],
    profile: dict[str, Any],
) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"
    events_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    profile_path = session_dir / "draft.action-profile.json"
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary_path = session_dir / "session.json"
    summary_path.write_text(
        json.dumps(
            {
                "ok": True,
                "session_id": session_id,
                "goal": goal,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "event_count": len(events),
                "segment_count": max(
                    (int(event.get("segment_index") or 1) for event in events),
                    default=0,
                ),
                "typed_text_policy": "redacted",
                "profile": profile_path.name,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "events": events_path,
        "profile": profile_path,
        "summary": summary_path,
    }
