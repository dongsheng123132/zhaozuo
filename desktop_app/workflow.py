from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _action_slug(goal: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", goal.lower())
    return "_".join(words[:5]) if words else "recorded_task"


def event_summary(event: dict[str, Any]) -> str:
    window = str(event.get("window", {}).get("title", "")).strip() or "未知窗口"
    short_window = window if len(window) <= 34 else window[:31] + "…"
    kind = event.get("kind")
    if kind == "pointer.click":
        return f"点击 · {short_window}"
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
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    steps: list[dict[str, Any]] = []

    for index, event in enumerate(events, 1):
        kind = event.get("kind")
        window = event.get("window", {})
        window_locator = {
            "title": window.get("title", ""),
            "class_name": window.get("class_name", ""),
        }
        base: dict[str, Any] = {
            "id": f"step_{index:03d}",
            "description": event_summary(event),
            "captured_offset_ms": event.get("offset_ms", 0),
        }
        if kind == "pointer.click":
            base.update(
                {
                    "kind": "pointer.click",
                    "locator": {
                        "window": window_locator,
                        "relative": event.get("relative"),
                        "fallback_absolute": event.get("absolute"),
                    },
                    "args": {"button": event.get("button", "left")},
                }
            )
        elif kind == "keyboard.shortcut":
            base.update(
                {
                    "kind": "keyboard.shortcut",
                    "locator": {"window": window_locator},
                    "args": {"keys": event.get("keys", [])},
                }
            )
        elif kind == "keyboard.press":
            base.update(
                {
                    "kind": "keyboard.press",
                    "locator": {"window": window_locator},
                    "args": {"key": event.get("key")},
                }
            )
        elif kind == "text.input":
            name = str(event["placeholder"])
            properties[name] = {
                "type": "string",
                "description": "回放时提供的文字；演示原文未被保存",
            }
            required.append(name)
            base.update(
                {
                    "kind": "keyboard.type",
                    "locator": {"window": window_locator},
                    "args": {"text": f"${{{name}}}"},
                    "log_policy": "redact",
                }
            )
        else:
            continue
        steps.append(base)

    evidence: list[dict[str, Any]] = []
    if success_title.strip():
        evidence.append(
            {
                "kind": "window.title_contains",
                "expected": success_title.strip(),
                "timeout_ms": 5000,
            }
        )

    return {
        "profile_version": "0.1",
        "profile_id": f"windows.zhaozuo.{_action_slug(goal)}",
        "kind": "external_ui_adapter",
        "status": "draft",
        "application": {
            "id": "windows.desktop.workflow",
            "name": "Windows 桌面工作流",
            "platform": "windows",
        },
        "actions": {
            f"workflow.{_action_slug(goal)}": {
                "title": goal.strip(),
                "description": "由照做录制器从一次人工演示生成的候选动作，尚需变化回放验证。",
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                "risk": "medium",
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
    profile_path = session_dir / "draft.shadow.json"
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
