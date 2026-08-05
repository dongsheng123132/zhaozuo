from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shared.action_id import ACTION_ID_RE, PROFILE_ID_RE
from shared.evidence import UNSUPPORTED_KINDS


PLACEHOLDER_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

#: 已冻结的格式版本。v1 规范见 docs/ACTION-PROFILE-V1.md。
SUPPORTED_VERSIONS = ("0.1", "1.0")
CURRENT_VERSION = "1.0"

STATUSES = ("draft", "learned", "validated")

#: 窗口标题是最弱的成功证据：消息发成功和发失败，标题往往一模一样。
WEAK_EVIDENCE_KINDS = {"window.title_contains", "window.exists"}


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

    if profile.get("profile_version") == "1.0":
        errors.extend(_validate_v1(profile))
    return errors


def _has_stable_locator(step: dict[str, Any]) -> bool:
    """Whether a step can be located by anything other than absolute coordinates."""

    locator = step.get("locator")
    if not isinstance(locator, dict):
        return False
    if isinstance(locator.get("uia"), dict) and locator["uia"]:
        return True
    window = locator.get("window")
    if isinstance(window, dict) and any(
        str(window.get(key) or "").strip()
        for key in ("process", "class_name", "title", "title_regex")
    ):
        return True
    return bool(locator.get("relative"))


def _validate_v1(profile: dict[str, Any]) -> list[str]:
    """Rules that only apply to the frozen v1 format.

    校验器必须真的执行规范，否则规范只是一篇作文。这里落地的是
    docs/ACTION-PROFILE-V1.md 里 §1 §2 §3 §4 §5 的强制条款。
    """

    errors: list[str] = []

    status = profile.get("status")
    if status not in STATUSES:
        errors.append(f"status must be one of {STATUSES}, got: {status!r}")
    profile_id = profile.get("profile_id")
    if isinstance(profile_id, str) and not PROFILE_ID_RE.fullmatch(profile_id):
        errors.append(f"profile_id is not a valid namespaced id: {profile_id!r}")

    if status in ("learned", "validated"):
        demonstrations = 0
        evidence_block = profile.get("evidence")
        if isinstance(evidence_block, dict):
            try:
                demonstrations = int(evidence_block.get("demonstrations") or 0)
            except (TypeError, ValueError):
                demonstrations = 0
        if demonstrations < 2:
            errors.append(
                f"{status} profile must record at least 2 varied demonstrations "
                "(窗口位置、输入内容、初始状态至少各变过一次)，"
                f"got evidence.demonstrations={demonstrations}"
            )

    if status == "validated":
        compatibility = profile.get("compatibility")
        if not isinstance(compatibility, dict) or not compatibility.get("app_versions"):
            errors.append(
                "validated profile must declare compatibility.app_versions "
                "(范围之外失效是兼容性问题，但范围必须先被声明)"
            )
        evidence = profile.get("evidence")
        if not isinstance(evidence, dict):
            errors.append("validated profile must carry an evidence object")
        else:
            runs = evidence.get("regression_runs")
            passed = [
                run for run in runs
                if isinstance(run, dict) and run.get("result") == "pass"
            ] if isinstance(runs, list) else []
            if not passed:
                errors.append(
                    "validated profile must record at least one passing regression run"
                )
            if not str(evidence.get("promoted_by") or "").strip():
                errors.append(
                    "validated profile must record evidence.promoted_by "
                    "(录制器不得自封 validated)"
                )

    for action_id, action in profile.get("actions", {}).items():
        prefix = f"actions.{action_id}"
        if not ACTION_ID_RE.fullmatch(str(action_id)):
            errors.append(
                f"{prefix}: action id must match {ACTION_ID_RE.pattern} "
                "(稳定唯一的 Action ID 是被承诺的调用接口)"
            )
        if not isinstance(action, dict):
            continue

        steps = action.get("steps") if isinstance(action.get("steps"), list) else []
        evidence_kinds = {
            str(item.get("kind"))
            for item in action.get("success_evidence", [])
            if isinstance(item, dict)
        }

        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_prefix = f"{prefix}.steps[{index}]"
            effect = step.get("effect")
            if isinstance(effect, dict):
                if effect.get("confirmation") != "always":
                    errors.append(
                        f"{step_prefix}.effect.confirmation must be 'always'"
                    )
                effect_id = effect.get("effect_id")
                if effect_id is not None and not ACTION_ID_RE.fullmatch(str(effect_id)):
                    errors.append(
                        f"{step_prefix}.effect.effect_id is not a valid namespaced id: {effect_id!r}"
                    )
                if status == "validated" and not isinstance(
                    step.get("target_assertion"), dict
                ):
                    errors.append(
                        f"{step_prefix}: outward effect in a validated profile "
                        "must declare target_assertion (发给谁必须被断言)"
                    )
            if status == "validated" and not _has_stable_locator(step):
                errors.append(
                    f"{step_prefix}: validated profile must not rely on absolute "
                    "coordinates alone (换机器、换 DPI、换分辨率后必然失效)"
                )

        if status in ("learned", "validated") and not evidence_kinds:
            errors.append(
                f"{prefix}: {status} profile must declare at least one success evidence"
            )
        unsupported = evidence_kinds & set(UNSUPPORTED_KINDS)
        if status == "validated" and unsupported and not (
            evidence_kinds - WEAK_EVIDENCE_KINDS - set(UNSUPPORTED_KINDS)
        ):
            # 只声明了执行器验不了的证据 == 没有可自动回归的证据。
            errors.append(
                f"{prefix}: validated profile relies only on evidence this executor "
                f"cannot check ({sorted(unsupported)})"
            )
        if status == "validated" and not (evidence_kinds - WEAK_EVIDENCE_KINDS):
            errors.append(
                f"{prefix}: validated profile needs evidence beyond window title "
                "(发送成功和发送失败的窗口标题往往一模一样)"
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
