"""在真机上验 desktop_app.dpi 的三级回退，只读，不创建窗口、不动键鼠。

用途：DPI 相关的失效模式在系统缩放为 100% 的开发机上复现不出来。这个脚本
让任意一台带缩放的机器都能自证一句话——「这台机器上，不声明感知的进程会把
坐标按多少折算」。

    python scripts/dpi-check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 裸机上这个包没被安装过——诊断脚本必须自己找得到仓库根，否则它只在
# 开发机上跑得起来，而开发机恰恰是最不需要诊断的那台。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_app import dpi  # noqa: E402


def main() -> int:
    before = {
        "awareness": dpi.current_awareness(),
        "system_dpi": dpi.system_dpi(),
        "screen": [
            dpi.user32.GetSystemMetrics(0),
            dpi.user32.GetSystemMetrics(1),
        ],
    }
    strategy = dpi.ensure_per_monitor_awareness()
    after = {
        "awareness": dpi.current_awareness(),
        "system_dpi": dpi.system_dpi(),
        "screen": [
            dpi.user32.GetSystemMetrics(0),
            dpi.user32.GetSystemMetrics(1),
        ],
    }
    report = {
        "ok": after["awareness"] != "unaware" and after["system_dpi"] >= before["system_dpi"],
        "strategy": strategy,
        "before": before,
        "after": after,
        "scale_before": dpi.scale_for(before["system_dpi"]),
        "scale_after": dpi.scale_for(after["system_dpi"]),
        "coordinate_drift": (
            None
            if not before["screen"][0]
            else round(after["screen"][0] / before["screen"][0], 4)
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
