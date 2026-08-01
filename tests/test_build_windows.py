from __future__ import annotations

import unittest
from pathlib import Path

from scripts.build_windows import ROOT, _inside_workspace, version_tuple


class WindowsBuildTests(unittest.TestCase):
    def test_version_tuple_pads_semver(self) -> None:
        self.assertEqual(version_tuple("0.2.0"), (0, 2, 0, 0))

    def test_build_outputs_must_stay_inside_workspace(self) -> None:
        self.assertEqual(_inside_workspace(ROOT / "build"), (ROOT / "build").resolve())
        with self.assertRaises(RuntimeError):
            _inside_workspace(Path(ROOT.anchor) / "outside-zhaozuo")


if __name__ == "__main__":
    unittest.main()
