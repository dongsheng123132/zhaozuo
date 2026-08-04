from __future__ import annotations

import argparse
import ctypes
import json
import uuid
from pathlib import Path
from typing import Any

from shared.runtime import data_root


def _self_test() -> dict[str, Any]:
    from desktop_app import dpi, uia, windows
    from desktop_app.capture import ImageGrab

    checks: dict[str, bool] = {
        "windows_input_size": ctypes.sizeof(windows.INPUT) in {28, 40},
        "uia_available": uia.available(),
        "pillow_image_grab": ImageGrab is not None,
        # 不感知 DPI 的进程拿到的是被折算过的坐标，而 UIA 给的是物理像素 ——
        # 主屏带缩放的机器上，UIA 定位到的按钮会被点到别处。
        "dpi_aware": dpi.current_awareness() in ("system", "per_monitor"),
    }

    writable_root = data_root()
    marker = writable_root / f".self-test-{uuid.uuid4().hex}"
    try:
        writable_root.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok", encoding="utf-8")
        checks["data_directory_writable"] = marker.read_text(encoding="utf-8") == "ok"
    except OSError:
        checks["data_directory_writable"] = False
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        checks["tkinter_available"] = True
    except Exception:
        checks["tkinter_available"] = False

    return {
        "ok": all(checks.values()),
        "mode": "self_test",
        "data_root": str(writable_root),
        "dpi_awareness": dpi.current_awareness(),
        "system_dpi": dpi.system_dpi(),
        "checks": checks,
    }


def _emit(result: dict[str, Any], *, as_json: bool, output: str) -> None:
    rendered = (
        json.dumps(result, ensure_ascii=False, indent=2)
        if as_json
        else "\n".join(f"{name}\t{value}" for name, value in result.items())
    )
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zhaozuo")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the packaged Windows runtime without recording or replaying input",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default="", metavar="PATH")
    return parser


def main() -> int:
    # 必须在创建任何窗口之前声明，否则该进程这辈子都是被折算过的坐标。
    from desktop_app.dpi import ensure_per_monitor_awareness

    ensure_per_monitor_awareness()

    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        try:
            result = _self_test()
        except Exception as exc:  # frozen runtime boundary
            result = {"ok": False, "mode": "self_test", "error": str(exc)}
        _emit(result, as_json=args.json, output=args.output)
        return 0 if result.get("ok") else 1

    if args.json or args.output:
        parser.error("--json and --output require --self-test")

    from desktop_app.app import main as run_app

    return run_app()
