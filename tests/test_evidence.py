from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shared.evidence import (
    UNSUPPORTED_KINDS,
    capture_baseline,
    check_all,
    check_one,
    summarize,
)


class FakeProbe:
    """A whole machine in a dict. 业务判断对不对，毫秒级断言，不开界面。"""

    def __init__(self, **state: object) -> None:
        self.titles: list[str] = list(state.get("titles", []))  # type: ignore[arg-type]
        self.windows: int = int(state.get("windows", 0))  # type: ignore[arg-type]
        self.processes: set[str] = set(state.get("processes", set()))  # type: ignore[arg-type]
        self.clipboard: str = str(state.get("clipboard", ""))
        self.values: dict[str, str] = dict(state.get("values", {}))  # type: ignore[arg-type]
        self.toggles: dict[str, str] = dict(state.get("toggles", {}))  # type: ignore[arg-type]
        self.everything_exists: bool = bool(state.get("everything_exists", False))

    @staticmethod
    def _key(locator: dict) -> str:
        return str(locator.get("automation_id") or locator.get("name") or "")

    def window_titles(self) -> list[str]:
        return self.titles

    def window_matches(self, locator: dict) -> int:
        return self.windows

    def process_names(self) -> set[str]:
        return self.processes

    def clipboard_text(self) -> str:
        return self.clipboard

    def uia_exists(self, locator: dict) -> bool:
        if self.everything_exists:
            return True
        return self._key(locator) in self.values or self._key(locator) in self.toggles

    def uia_value(self, locator: dict) -> str | None:
        return self.values.get(self._key(locator))

    def uia_toggle_state(self, locator: dict) -> str | None:
        return self.toggles.get(self._key(locator))


class EvidenceEngineTests(unittest.TestCase):
    def test_unsupported_evidence_never_counts_as_success(self) -> None:
        """验不了就是没验过。默默放行比没有证据更危险 —— 它看起来像验过了。"""

        for kind in UNSUPPORTED_KINDS:
            with self.subTest(kind=kind):
                result = check_one({"kind": kind}, FakeProbe())
                self.assertFalse(result["ok"])
                self.assertIn("未实现", result["reason"])

        unknown = check_one({"kind": "made.up_thing"}, FakeProbe())
        self.assertFalse(unknown["ok"])
        self.assertIn("未知的证据种类", unknown["reason"])

    def test_empty_evidence_is_not_success(self) -> None:
        verdict = summarize([])
        self.assertFalse(verdict["ok"])
        self.assertIn("没有声明任何成功证据", verdict["reason"])

    def test_checker_exception_fails_closed(self) -> None:
        class Exploding(FakeProbe):
            def window_titles(self):  # type: ignore[override]
                raise RuntimeError("UIA 挂了")

        result = check_one(
            {"kind": "window.title_contains", "expected": "x"}, Exploding()
        )
        self.assertFalse(result["ok"])
        self.assertIn("证据检查出错", result["reason"])

    def test_toggle_state_proves_a_like_actually_landed(self) -> None:
        """点赞的成功判据是控件被切到 on，不是"点了一下没报错"。"""

        spec = {
            "kind": "control.toggle_state",
            "locator": {"automation_id": "like"},
            "expected": "on",
        }
        self.assertTrue(check_one(spec, FakeProbe(toggles={"like": "on"}))["ok"])

        # 点过了但没生效 —— 旧的"跑完就算成功"会把这种情况报成成功。
        missed = check_one(spec, FakeProbe(toggles={"like": "off"}))
        self.assertFalse(missed["ok"])
        self.assertEqual(missed["observed"], "off")

        # 控件根本不支持 Toggle：没验到，不是通过。
        self.assertFalse(check_one(spec, FakeProbe())["ok"])

    def test_uia_value_distinguishes_missing_from_empty(self) -> None:
        spec = {
            "kind": "uia.value_equals",
            "locator": {"automation_id": "addr"},
            "expected": "",
        }
        # 控件不存在 -> 读不到 -> 未通过，而不是"空值等于空期望"。
        self.assertFalse(check_one(spec, FakeProbe())["ok"])
        self.assertTrue(check_one(spec, FakeProbe(values={"addr": ""}))["ok"])

    def test_file_change_evidence_requires_a_baseline(self) -> None:
        """没有执行前基线，就无法把"文件变了"归因到这次动作。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.xlsx"
            target.write_text("v1", encoding="utf-8")
            spec = {"kind": "file.hash_changed", "path": str(target)}

            # 无基线：拒绝判定为成功
            no_baseline = check_one(spec, FakeProbe())
            self.assertFalse(no_baseline["ok"])
            self.assertIn("基线", no_baseline["reason"])

            baseline = capture_baseline([spec])

            # 文件没被动过 -> 未通过
            self.assertFalse(check_one(spec, FakeProbe(), baseline)["ok"])

            # 动作真的改了文件 -> 通过
            target.write_text("v2 changed by the action", encoding="utf-8")
            self.assertTrue(check_one(spec, FakeProbe(), baseline)["ok"])

    def test_clipboard_evidence_does_not_echo_secrets(self) -> None:
        result = check_one(
            {"kind": "clipboard.contains", "expected": "订单号"},
            FakeProbe(clipboard="订单号 A-1 密码 hunter2"),
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("hunter2", str(result))

    def test_mixed_evidence_summary_reports_what_failed(self) -> None:
        evidence = [
            {"kind": "window.title_contains", "expected": "报表"},
            {"kind": "process.running", "expected": "excel.exe"},
            {"kind": "message.outgoing_visible"},
        ]
        probe = FakeProbe(titles=["月度报表 - Excel"], processes={"excel.exe"})
        results = check_all(evidence, probe)
        verdict = summarize(results)

        self.assertEqual(verdict["checked"], 3)
        self.assertEqual(verdict["passed"], 2)
        self.assertFalse(verdict["ok"])
        self.assertEqual(
            [item["kind"] for item in verdict["failed"]], ["message.outgoing_visible"]
        )

    def test_window_title_evidence_still_works_for_legacy_profiles(self) -> None:
        probe = FakeProbe(titles=["计算器", "照做 · ShadowCore"])
        self.assertTrue(
            check_one({"kind": "window.title_contains", "expected": "计算器"}, probe)["ok"]
        )
        self.assertFalse(
            check_one({"kind": "window.title_contains", "expected": "微信"}, probe)["ok"]
        )


if __name__ == "__main__":
    unittest.main()
