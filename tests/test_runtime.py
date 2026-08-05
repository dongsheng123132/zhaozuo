from __future__ import annotations

import py_compile
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from shared.runtime import data_root

#: 仓库根目录（tests/ 的上一级）。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRECTORIES = {".git", ".venv", "build", "dist", "node_modules", "__pycache__"}


class RuntimePathTests(unittest.TestCase):
    def test_source_checkout_uses_project_root(self) -> None:
        expected = Path("C:/example/source")
        self.assertEqual(
            data_root(frozen=False, env={}, project_root=expected),
            expected.resolve(),
        )

    def test_explicit_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                data_root(
                    frozen=True,
                    executable="C:/Program Files/Zhaozuo/Zhaozuo.exe",
                    env={"ZHAOZUO_DATA_DIR": directory},
                ),
                Path(directory).resolve(),
            )

    def test_portable_marker_keeps_data_beside_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "Zhaozuo.exe"
            (Path(directory) / "portable.mode").touch()
            self.assertEqual(
                data_root(frozen=True, executable=executable, env={}),
                (Path(directory) / "data").resolve(),
            )

    def test_installed_build_uses_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                data_root(
                    frozen=True,
                    executable="C:/Program Files/Zhaozuo/Zhaozuo.exe",
                    env={"LOCALAPPDATA": directory},
                ),
                Path(directory).resolve() / "Zhaozuo",
            )


class SourceCompilesCleanlyTests(unittest.TestCase):
    def test_no_invalid_escape_sequences(self) -> None:
        """非 raw 字符串里的 ``\\Z`` 这类写法，3.12 起告警，3.14 起直接是语法错误。

        开发机跑 3.11，那里它只是被默认隐藏的 DeprecationWarning —— 所以这类
        问题必须由编译期断言兜住，不能指望本机跑一遍就能看见。
        """

        failures: list[str] = []
        with tempfile.TemporaryDirectory() as cache:
            for source in sorted(PROJECT_ROOT.rglob("*.py")):
                if SKIP_DIRECTORIES.intersection(source.parts):
                    continue
                target = Path(cache) / (source.stem + ".pyc")
                with warnings.catch_warnings():
                    warnings.simplefilter("error", SyntaxWarning)
                    warnings.simplefilter("error", DeprecationWarning)
                    try:
                        py_compile.compile(str(source), cfile=str(target), doraise=True)
                    except (py_compile.PyCompileError, SyntaxWarning, DeprecationWarning) as exc:
                        failures.append(
                            f"{source.relative_to(PROJECT_ROOT)}: {type(exc).__name__}: {exc}"
                        )
        self.assertEqual(failures, [], "\n".join(failures))


class EntryPointTests(unittest.TestCase):
    """`-m` 跑得起来的模块，绝不能"什么都没做"却退出 0。"""

    def _exit_code(self, module: str) -> int:
        # `--json` 不带 `--self-test` 是既定的用法错误，argparse 应当以 2 退出。
        # 选它是因为它快、不建窗口、不碰键鼠，却能证明 main() 真的被调用了。
        return subprocess.run(
            [sys.executable, "-m", module, "--json"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=60,
        ).returncode

    def test_module_entrypoints_actually_run_main(self) -> None:
        for module in ("desktop_app", "desktop_app.entrypoint"):
            with self.subTest(module=module):
                self.assertEqual(
                    self._exit_code(module),
                    2,
                    f"python -m {module} 没有真正执行 main()——退出 0 会被读成自检通过",
                )


if __name__ == "__main__":
    unittest.main()
