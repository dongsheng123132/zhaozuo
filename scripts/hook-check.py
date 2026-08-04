"""在真机上验低级输入钩子这一层，不需要人来操作。

录制器换成 SetWindowsHookExW 之后，"钩子装得上、逐事件送得到、装配得成手势"
这条链在开发机之外从没被验证过——而它恰恰是最依赖具体机器的一层（会话类型、
UIPI、杀软注入拦截都可能让 SetWindowsHookExW 静默失败）。

这里用 SendInput 合成 F13 按键。选 F13 是因为几乎没有程序响应它：不会点到
任何东西，不会输入任何字符，不会移动鼠标。合成事件带 LLKHF_INJECTED 标记，
录制器会跳过窗口/UIA 富化但仍然入队，正好把钩子层单独验出来。

    python scripts/hook-check.py
"""

from __future__ import annotations

import ctypes
import json
import queue
import sys
import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_app import dpi, hooks  # noqa: E402
from shared.gestures import GestureAssembler  # noqa: E402

VK_F13 = 0x7C
PRESSES = 5

user32 = ctypes.WinDLL("user32", use_last_error=True)


KEYEVENTF_KEYUP = 0x0002


def tap(vk: int) -> None:
    """按项目自己发按键的那条路径（keybd_event），不另造一份 SendInput。"""

    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)


def main() -> int:
    dpi.ensure_per_monitor_awareness()

    records: queue.Queue = queue.Queue(maxsize=4096)
    listener = hooks.InputHookListener(records)
    report: dict = {"presses_sent": PRESSES}

    try:
        listener.start()
    except Exception as exc:
        print(json.dumps({"ok": False, "stage": "start", "error": str(exc)}, ensure_ascii=False))
        return 1

    report["hook_installed"] = listener.running
    time.sleep(0.3)
    for _ in range(PRESSES):
        tap(VK_F13)
        time.sleep(0.05)
    time.sleep(0.3)
    listener.stop()

    drained = []
    while True:
        try:
            drained.append(records.get_nowait())
        except queue.Empty:
            break

    assembler = GestureAssembler()
    gestures = []
    for record in drained:
        gestures.extend(assembler.feed(record))
    gestures.extend(assembler.flush())

    seen = [r for r in drained if r.get("kind") in ("key.down", "key.up") and r.get("vk") == VK_F13]
    report.update(
        {
            "raw_records": len(drained),
            "f13_events_captured": len(seen),
            "all_marked_injected": all(r.get("injected") for r in seen) if seen else False,
            "dropped": listener.dropped,
            "gestures": len(gestures),
            "gesture_kinds": sorted({str(g.get("kind")) for g in gestures}),
        }
    )
    # 每次按键各产生 down+up 两条；少一条就是钩子漏事件，多一条就是重复投递。
    report["ok"] = (
        report["hook_installed"]
        and report["f13_events_captured"] == PRESSES * 2
        and report["dropped"] == 0
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
