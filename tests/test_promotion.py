from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from executor.cli import build_parser
from shared.profile import ProfileError


def _draft() -> dict:
    return {
        "profile_version": "1.0",
        "profile_id": "windows.demo.export-report",
        "kind": "external_ui_adapter",
        "status": "draft",
        "application": {
            "id": "com.demo.excel",
            "name": "Excel",
            "platform": "windows",
            "discovery": {"process_names": ["excel.exe"]},
        },
        "actions": {
            "report.export_monthly": {
                "title": "导出月度报表",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "risk": "medium",
                "confirmation": "before_execute",
                "steps": [
                    {
                        "id": "step_001",
                        "kind": "keyboard.shortcut",
                        "locator": {
                            "window": {"process": "excel.exe", "class_name": "XLMAIN"}
                        },
                        "args": {"keys": ["CTRL", "S"]},
                    }
                ],
                "success_evidence": [
                    {"kind": "file.hash_changed", "path": "report.xlsx"},
                    {"kind": "process.running", "expected": "excel.exe"},
                ],
            }
        },
    }


class PromotionLadderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.profile_path = self.root / "demo.action-profile.json"
        self._write(_draft())
        self.parser = build_parser()

    def _write(self, profile: dict) -> None:
        self.profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load(self) -> dict:
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    def _report(self, name: str, *, ok: bool = True, executed: bool = True) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "ok": ok,
                    "executed": executed,
                    "mode": "executed" if executed else "plan_only",
                    "action_id": "report.export_monthly",
                    "evidence_summary": {"ok": ok, "checked": 2, "passed": 2 if ok else 1},
                    "degraded_steps": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _run(self, *argv: str) -> int:
        args = self.parser.parse_args([*argv, "--json"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return args.handler(args)

    def test_draft_cannot_be_promoted_without_evidence(self) -> None:
        code = self._run("promote", str(self.profile_path), "--to", "validated", "--by", "老何")
        self.assertEqual(code, 1)
        # 被拒绝时绝不能写文件 —— 否则档案会停在半提升的状态。
        self.assertEqual(self._load()["status"], "draft")

    def test_dry_run_report_cannot_be_laundered_into_evidence(self) -> None:
        report = self._report("plan.json", executed=False)
        with self.assertRaisesRegex(ProfileError, "不能作为回归证据"):
            self._run("record-run", str(self.profile_path), "--report", str(report))
        self.assertNotIn("evidence", self._load())

    def test_failing_run_is_recorded_as_fail_and_blocks_promotion(self) -> None:
        self._run(
            "record-run", str(self.profile_path),
            "--report", str(self._report("bad.json", ok=False)),
            "--app-version", "16.0.17", "--varied",
        )
        self._run(
            "record-run", str(self.profile_path),
            "--report", str(self._report("bad2.json", ok=False)),
            "--app-version", "16.0.17", "--varied",
        )
        profile = self._load()
        runs = profile["evidence"]["regression_runs"]
        self.assertEqual([run["result"] for run in runs], ["fail", "fail"])
        # 失败的回放不抬 demonstrations，也不把该版本写进对外兼容声明 ——
        # 否则工具等于自己伪造了一条"这版能跑"的证据。
        self.assertEqual(profile["evidence"].get("demonstrations", 0), 0)
        self.assertEqual(profile.get("compatibility", {}).get("app_versions", []), [])
        # 版本号仍留在 run 上，失败史查得到。
        self.assertEqual([run["app_version"] for run in runs], ["16.0.17", "16.0.17"])

        code = self._run("promote", str(self.profile_path), "--to", "validated", "--by", "老何")
        self.assertEqual(code, 1)
        self.assertEqual(self._load()["status"], "draft")

    def test_failures_cannot_be_laundered_into_a_compatibility_claim(self) -> None:
        """两次失败 + 一次别处的成功，凑不出 validated。

        校验器只数"有没有通过的回归"，数不出"通过的是不是声明的那个版本"。
        所以污染必须挡在 record-run，而不是指望 promote 再兜一次。
        """

        for index, version in enumerate(("16.0.17", "16.0.18"), 1):
            self._run(
                "record-run", str(self.profile_path),
                "--report", str(self._report(f"fail{index}.json", ok=False)),
                "--app-version", version, "--varied",
            )
        # 一次成功，但操作者没声明变化过、也没声明是哪个版本上跑的。
        self._run(
            "record-run", str(self.profile_path),
            "--report", str(self._report("pass.json")),
        )

        code = self._run("promote", str(self.profile_path), "--to", "validated", "--by", "老何")
        self.assertEqual(code, 1)
        profile = self._load()
        self.assertEqual(profile["status"], "draft")
        self.assertNotIn("16.0.17", profile.get("compatibility", {}).get("app_versions", []))
        self.assertEqual(profile["evidence"].get("demonstrations", 0), 0)

    def test_full_ladder_records_an_auditable_chain(self) -> None:
        for index in (1, 2):
            self._run(
                "record-run", str(self.profile_path),
                "--report", str(self._report(f"run{index}.json")),
                "--app-version", "16.0.17", "--os-build", "22631",
                "--dpi-scale", "1.5", "--locale", "zh-CN", "--varied",
            )
        code = self._run("promote", str(self.profile_path), "--to", "validated", "--by", "老何")
        self.assertEqual(code, 0)

        profile = self._load()
        evidence = profile["evidence"]
        self.assertEqual(profile["status"], "validated")
        self.assertEqual(evidence["promoted_by"], "老何")
        self.assertEqual(evidence["demonstrations"], 2)
        self.assertEqual([run["result"] for run in evidence["regression_runs"]], ["pass", "pass"])
        self.assertEqual(profile["compatibility"]["app_versions"], ["16.0.17"])
        # 每次写入都留底，可回滚。
        self.assertTrue(self.profile_path.with_suffix(".json.bak").is_file())

    def test_demonstrations_only_count_when_the_operator_asserts_variation(self) -> None:
        """工具无法自证一次演示"变化过"，所以必须由操作者明示。"""

        for index in (1, 2):
            self._run(
                "record-run", str(self.profile_path),
                "--report", str(self._report(f"same{index}.json")),
                "--app-version", "16.0.17",
            )
        evidence = self._load()["evidence"]
        self.assertEqual(len(evidence["regression_runs"]), 2)
        self.assertEqual(evidence.get("demonstrations", 0), 0)

        code = self._run("promote", str(self.profile_path), "--to", "validated", "--by", "老何")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
