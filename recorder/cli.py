from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _emit(result: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("ok"):
        print(f"session_id\t{result['session_id']}")
        print(f"session_dir\t{result['session_dir']}")
    else:
        print(f"error\t{result.get('error', 'unknown error')}")


def _new_session(args: argparse.Namespace) -> int:
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    session_dir = Path(args.output) / session_id
    session_dir.mkdir(parents=True, exist_ok=False)

    event = {
        "event_version": "0.1",
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "session.started",
        "application": args.app,
        "task": args.task,
        "capture_policy": {
            "typed_text": "redacted",
            "screenshots": "disabled",
            "uia_snapshots": "disabled",
        },
        "note": "Session envelope only; Windows hooks are not connected yet.",
    }
    events_path = session_dir / "events.jsonl"
    events_path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

    result = {
        "ok": True,
        "session_id": session_id,
        "session_dir": str(session_dir.resolve()),
        "events_path": str(events_path.resolve()),
        "capture_active": False,
    }
    _emit(result, args.json)
    print(
        "recording hooks are not connected; created a privacy-safe session envelope",
        file=sys.stderr,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zhaozuo-record",
        description="Create privacy-safe Zhaozuo recording sessions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_session = subparsers.add_parser("new-session")
    new_session.add_argument("--app", required=True)
    new_session.add_argument("--task", required=True)
    new_session.add_argument("--output", default="recordings")
    new_session.add_argument("--json", action="store_true")
    new_session.set_defaults(handler=_new_session)
    return parser


def main() -> int:
    parser = build_parser()
    try:
        args = parser.parse_args()
        return args.handler(args)
    except FileExistsError as exc:
        as_json = "--json" in sys.argv
        _emit({"ok": False, "error": str(exc)}, as_json)
        return 1
    except KeyboardInterrupt:
        _emit({"ok": False, "error": "interrupted"}, "--json" in sys.argv)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
