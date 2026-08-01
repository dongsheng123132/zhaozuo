from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shared.profile import ProfileError, load_profile, resolve_action, validate_profile


def _emit(result: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("ok"):
        for key, value in result.items():
            if key != "ok":
                print(f"{key}\t{json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}")
    else:
        print(f"error\t{result.get('error', 'unknown error')}")


def _parse_inputs(items: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ProfileError(f"input must use name=value: {item}")
        name, value = item.split("=", 1)
        if not name:
            raise ProfileError(f"input name cannot be empty: {item}")
        parsed[name] = value
    return parsed


def _validate(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    errors = validate_profile(profile)
    result = {
        "ok": not errors,
        "profile": str(Path(args.profile).resolve()),
        "profile_id": profile.get("profile_id"),
        "errors": errors,
    }
    _emit(result, args.json)
    return 0 if not errors else 1


def _plan(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    errors = validate_profile(profile)
    if errors:
        raise ProfileError("profile validation failed: " + "; ".join(errors))
    inputs = _parse_inputs(args.input)
    action = resolve_action(profile, args.action, inputs)
    result = {
        "ok": True,
        "mode": "plan_only",
        "profile_id": profile["profile_id"],
        "application": profile["application"],
        "action_id": args.action,
        "risk": action.get("risk"),
        "confirmation": action.get("confirmation"),
        "steps": action["steps"],
        "success_evidence": action.get("success_evidence", []),
        "executed": False,
    }
    _emit(result, args.json)
    print("plan only: no keyboard, mouse, application, or file action was executed", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shadowcore-execute",
        description="Validate Shadow Profiles and build safe execution plans.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("profile")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=_validate)

    plan = subparsers.add_parser("plan")
    plan.add_argument("profile")
    plan.add_argument("action")
    plan.add_argument("--input", action="append", default=[], metavar="NAME=VALUE")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(handler=_plan)
    return parser


def main() -> int:
    parser = build_parser()
    try:
        args = parser.parse_args()
        return args.handler(args)
    except ProfileError as exc:
        _emit({"ok": False, "error": str(exc)}, "--json" in sys.argv)
        return 1
    except KeyboardInterrupt:
        _emit({"ok": False, "error": "interrupted"}, "--json" in sys.argv)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
