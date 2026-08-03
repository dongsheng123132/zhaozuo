from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.evidence import capture_baseline, check_all, summarize
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


def _write_profile(path: Path, profile: dict) -> Path:
    """Atomically rewrite a profile, keeping a .bak of what was there before.

    档案是别人验证过的资产，不能被半截写坏，也不能静默覆盖。
    """

    backup = path.with_suffix(path.suffix + ".bak")
    if path.is_file():
        backup.write_bytes(path.read_bytes())
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return backup


def _record_run(args: argparse.Namespace) -> int:
    """Append a regression run derived from a real replay report.

    回归记录只能来自一次真实执行的报告，不能手写 —— 否则 validated 的证据链
    第一环就是空的。
    """

    path = Path(args.profile)
    profile = load_profile(path)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ProfileError("replay report must be a JSON object")
    if not report.get("executed"):
        raise ProfileError(
            "报告显示这次没有真正执行（dry-run / 停在确认前 / 目标未通过校验），不能作为回归证据"
        )

    run = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "result": "pass" if report.get("ok") else "fail",
        "action_id": report.get("action_id"),
        "evidence_summary": report.get("evidence_summary"),
        "degraded_steps": report.get("degraded_steps") or [],
    }
    for flag, key in (
        (args.app_version, "app_version"),
        (args.os_build, "os_build"),
        (args.locale, "locale"),
        (args.notes, "notes"),
    ):
        if flag:
            run[key] = flag
    if args.dpi_scale is not None:
        run["dpi_scale"] = args.dpi_scale

    evidence = profile.setdefault("evidence", {})
    evidence.setdefault("regression_runs", []).append(run)
    if args.varied:
        # 工具无法自证这次演示"变化过"，只能记录操作者的明示断言。
        evidence["demonstrations"] = int(evidence.get("demonstrations") or 0) + 1
    if args.app_version:
        versions = profile.setdefault("compatibility", {}).setdefault("app_versions", [])
        if args.app_version not in versions:
            versions.append(args.app_version)

    backup = _write_profile(path, profile)
    _emit(
        {
            "ok": True,
            "profile": str(path.resolve()),
            "recorded": run,
            "regression_runs": len(evidence["regression_runs"]),
            "demonstrations": evidence.get("demonstrations", 0),
            "backup": str(backup),
        },
        args.json,
    )
    return 0


def _promote(args: argparse.Namespace) -> int:
    """Move a profile up the ladder, but only if the evidence is actually there."""

    path = Path(args.profile)
    profile = load_profile(path)
    previous = profile.get("status")

    candidate = json.loads(json.dumps(profile, ensure_ascii=False))
    candidate["status"] = args.to
    candidate.setdefault("evidence", {})["promoted_by"] = args.by

    errors = validate_profile(candidate)
    if errors:
        _emit(
            {
                "ok": False,
                "profile": str(path.resolve()),
                "status": previous,
                "requested": args.to,
                "error": f"证据不足，拒绝提升到 {args.to}",
                "missing": errors,
            },
            args.json,
        )
        return 1

    backup = _write_profile(path, candidate)
    _emit(
        {
            "ok": True,
            "profile": str(path.resolve()),
            "status": args.to,
            "previous_status": previous,
            "promoted_by": args.by,
            "backup": str(backup),
        },
        args.json,
    )
    return 0


def _evidence(args: argparse.Namespace) -> int:
    """Run this action's success evidence against the live machine, executing nothing."""

    from desktop_app.probe import LiveProbe  # Windows-only, imported on demand

    profile = load_profile(args.profile)
    action = resolve_action(profile, args.action, _parse_inputs(args.input))
    evidence = action.get("success_evidence", [])
    results = check_all(evidence, LiveProbe(), capture_baseline(evidence))
    verdict = summarize(results)
    _emit(
        {
            "ok": verdict["ok"],
            "profile_id": profile.get("profile_id"),
            "action_id": args.action,
            "evidence": results,
            "summary": verdict,
            "executed": False,
        },
        args.json,
    )
    print("evidence only: nothing was executed", file=sys.stderr)
    return 0 if verdict["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zhaozuo-profile",
        description="Validate compatibility action profiles and build safe execution plans.",
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

    evidence = subparsers.add_parser(
        "evidence", help="对着真机跑一遍该动作的成功证据，不执行任何步骤"
    )
    evidence.add_argument("profile")
    evidence.add_argument("action")
    evidence.add_argument("--input", action="append", default=[], metavar="NAME=VALUE")
    evidence.add_argument("--json", action="store_true")
    evidence.set_defaults(handler=_evidence)

    record = subparsers.add_parser(
        "record-run", help="把一次真实回放报告记入档案的回归记录"
    )
    record.add_argument("profile")
    record.add_argument("--report", required=True, help="ReplayEngine 输出的报告 JSON")
    record.add_argument("--app-version")
    record.add_argument("--os-build")
    record.add_argument("--dpi-scale", type=float)
    record.add_argument("--locale")
    record.add_argument("--notes")
    record.add_argument(
        "--varied",
        action="store_true",
        help="声明本次演示改变了窗口位置/输入/初始状态（工具无法自证，需操作者明示）",
    )
    record.add_argument("--json", action="store_true")
    record.set_defaults(handler=_record_run)

    promote = subparsers.add_parser(
        "promote", help="提升档案状态；证据不足会被拒绝且不写文件"
    )
    promote.add_argument("profile")
    promote.add_argument("--to", required=True, choices=["draft", "learned", "validated"])
    promote.add_argument("--by", required=True, help="提升人，写入 evidence.promoted_by")
    promote.add_argument("--json", action="store_true")
    promote.set_defaults(handler=_promote)
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
