from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DATA_DIRECTORY = "Zhaozuo"
PORTABLE_MARKER = "portable.mode"


def data_root(
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> Path:
    r"""Return the writable root for recordings and future user state.

    Source checkouts keep data under the repository. Frozen builds use
    ``%LOCALAPPDATA%\Zhaozuo`` unless a ``portable.mode`` marker sits beside
    the executable. ``ZHAOZUO_DATA_DIR`` is an explicit override for tests,
    managed deployments, and recovery.
    """

    environment = os.environ if env is None else env
    override = str(environment.get("ZHAOZUO_DATA_DIR", "")).strip()
    if override:
        return Path(override).expanduser().resolve()

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return Path(project_root or PROJECT_ROOT).resolve()

    executable_path = Path(executable or sys.executable).resolve()
    executable_dir = executable_path.parent
    if (executable_dir / PORTABLE_MARKER).is_file():
        return executable_dir / "data"

    local_app_data = str(environment.get("LOCALAPPDATA", "")).strip()
    if local_app_data:
        return Path(local_app_data).expanduser().resolve() / APP_DATA_DIRECTORY

    return Path.home() / "AppData" / "Local" / APP_DATA_DIRECTORY
