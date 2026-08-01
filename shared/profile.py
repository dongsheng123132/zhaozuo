from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PLACEHOLDER_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class ProfileError(ValueError):
    """Raised when a compatibility action profile cannot be loaded or resolved."""


def load_profile(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path)
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileError(f"profile not found: {profile_path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ProfileError("profile root must be an object")
    return data


def validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for key in ("profile_version", "profile_id", "application", "actions"):
        if key not in profile:
            errors.append(f"missing required field: {key}")

    application = profile.get("application")
    if not isinstance(application, dict):
        errors.append("application must be an object")
    else:
        for key in ("id", "name", "platform"):
            if not isinstance(application.get(key), str) or not application[key].strip():
                errors.append(f"application.{key} must be a non-empty string")

    actions = profile.get("actions")
    if not isinstance(actions, dict) or not actions:
        errors.append("actions must be a non-empty object")
        return errors

    for action_id, action in actions.items():
        prefix = f"actions.{action_id}"
        if not isinstance(action, dict):
            errors.append(f"{prefix} must be an object")
            continue

        steps = action.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{prefix}.steps must be a non-empty array")
            continue

        seen_step_ids: set[str] = set()
        for index, step in enumerate(steps):
            step_prefix = f"{prefix}.steps[{index}]"
            if not isinstance(step, dict):
                errors.append(f"{step_prefix} must be an object")
                continue
            step_id = step.get("id")
            if not isinstance(step_id, str) or not step_id.strip():
                errors.append(f"{step_prefix}.id must be a non-empty string")
            elif step_id in seen_step_ids:
                errors.append(f"{step_prefix}.id is duplicated: {step_id}")
            else:
                seen_step_ids.add(step_id)
            if not isinstance(step.get("kind"), str) or not step["kind"].strip():
                errors.append(f"{step_prefix}.kind must be a non-empty string")

        declared_inputs = set(
            action.get("input_schema", {}).get("properties", {}).keys()
            if isinstance(action.get("input_schema"), dict)
            else []
        )
        used_inputs = collect_placeholders(action)
        unknown_inputs = sorted(used_inputs - declared_inputs)
        for input_name in unknown_inputs:
            errors.append(
                f"{prefix} uses undeclared input placeholder: {input_name}"
            )

    return errors


def collect_placeholders(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(PLACEHOLDER_RE.findall(value))
    elif isinstance(value, list):
        for item in value:
            found.update(collect_placeholders(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(collect_placeholders(item))
    return found


def resolve_action(
    profile: dict[str, Any], action_id: str, inputs: dict[str, str]
) -> dict[str, Any]:
    actions = profile.get("actions", {})
    if action_id not in actions:
        raise ProfileError(f"action not found: {action_id}")

    action = actions[action_id]
    input_schema = action.get("input_schema", {})
    required = input_schema.get("required", [])
    missing = [name for name in required if name not in inputs]
    if missing:
        raise ProfileError(f"missing required input(s): {', '.join(missing)}")

    declared = set(input_schema.get("properties", {}).keys())
    unknown = sorted(set(inputs) - declared)
    if unknown:
        raise ProfileError(f"unknown input(s): {', '.join(unknown)}")

    return _resolve_value(action, inputs)


def _resolve_value(value: Any, inputs: dict[str, str]) -> Any:
    if isinstance(value, str):
        full_match = PLACEHOLDER_RE.fullmatch(value)
        if full_match:
            name = full_match.group(1)
            if name not in inputs:
                raise ProfileError(f"missing input: {name}")
            return inputs[name]

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in inputs:
                raise ProfileError(f"missing input: {name}")
            return inputs[name]

        return PLACEHOLDER_RE.sub(replace, value)
    if isinstance(value, list):
        return [_resolve_value(item, inputs) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_value(item, inputs) for key, item in value.items()}
    return value
