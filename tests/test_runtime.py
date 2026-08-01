from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shared.runtime import data_root


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
                Path(directory) / "data",
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


if __name__ == "__main__":
    unittest.main()
