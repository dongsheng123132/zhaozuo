from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = ROOT / "build" / "windows"
DIST_WINDOWS = ROOT / "dist" / "windows"
RELEASE_DIR = ROOT / "dist" / "release"
ISS_SCRIPT = ROOT / "packaging" / "windows" / "Zhaozuo.iss"
PORTABLE_README = ROOT / "packaging" / "windows" / "README-PORTABLE.txt"


def _inside_workspace(path: Path) -> Path:
    resolved = path.resolve()
    if ROOT.resolve() not in resolved.parents:
        raise RuntimeError(f"refusing to modify path outside workspace: {resolved}")
    return resolved


def _reset_directory(path: Path) -> None:
    target = _inside_workspace(path)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def version_tuple(version: str) -> tuple[int, int, int, int]:
    numeric = version.split("+", 1)[0].split("-", 1)[0].split(".")
    parts = [int(part) for part in numeric]
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"unsupported version: {version}")
    return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def source_commit() -> str:
    configured = os.environ.get("GITHUB_SHA", "").strip()
    if configured:
        return configured
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def write_version_info(version: str) -> Path:
    major, minor, patch, build = version_tuple(version)
    output = BUILD_ROOT / "generated" / "version_info.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'Zhaozuo open-source contributors'),
        StringStruct('FileDescription', 'Zhaozuo Windows compatibility action learner'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'Zhaozuo'),
        StringStruct('LegalCopyright', 'Copyright 2026 hfshfg'),
        StringStruct('OriginalFilename', 'Zhaozuo.exe'),
        StringStruct('ProductName', 'Zhaozuo'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return output


def prepare_comtypes() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows distributions must be built on Windows")
    from comtypes.client import GetModule

    GetModule("UIAutomationCore.dll")
    __import__("comtypes.gen.UIAutomationClient")


def build_executable(version: str) -> Path:
    prepare_comtypes()
    version_info = write_version_info(version)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--noupx",
        "--name",
        "Zhaozuo",
        "--distpath",
        str(DIST_WINDOWS),
        "--workpath",
        str(BUILD_ROOT / "pyinstaller-work"),
        "--specpath",
        str(BUILD_ROOT / "spec"),
        "--version-file",
        str(version_info),
        "--hidden-import",
        "comtypes.gen.UIAutomationClient",
        str(ROOT / "desktop_app" / "__main__.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    executable = DIST_WINDOWS / "Zhaozuo.exe"
    if not executable.is_file():
        raise RuntimeError("PyInstaller did not produce Zhaozuo.exe")
    return executable


def write_build_info(version: str, commit: str) -> Path:
    output = DIST_WINDOWS / "build-info.json"
    output.write_text(
        json.dumps(
            {
                "version": version,
                "source_commit": commit,
                "platform": "windows-x64",
                "signed": False,
                "channel": "technical_preview",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def run_self_test(
    executable: Path,
    *,
    portable_dir: Path | None = None,
    local_app_data: Path | None = None,
) -> None:
    if portable_dir is not None and local_app_data is not None:
        raise ValueError("portable_dir and local_app_data are mutually exclusive")
    with tempfile.TemporaryDirectory(prefix="zhaozuo-self-test-") as directory:
        temp = Path(directory)
        output = temp / "report.json"
        environment = os.environ.copy()
        environment.pop("ZHAOZUO_DATA_DIR", None)
        if portable_dir is not None:
            expected = (portable_dir / "data").resolve()
        elif local_app_data is not None:
            environment["LOCALAPPDATA"] = str(local_app_data)
            expected = (local_app_data / "Zhaozuo").resolve()
        else:
            environment["ZHAOZUO_DATA_DIR"] = str(temp / "data")
            expected = (temp / "data").resolve()
        subprocess.run(
            [str(executable), "--self-test", "--json", "--output", str(output)],
            check=True,
            timeout=90,
            env=environment,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        if report.get("ok") is not True:
            raise RuntimeError(f"frozen self-test failed: {report}")
        observed = Path(report["data_root"]).resolve()
        if observed != expected:
            raise RuntimeError(
                f"runtime data root mismatch: expected {expected}, got {observed}"
            )


def build_portable(version: str, executable: Path, build_info: Path) -> Path:
    portable_name = f"Zhaozuo-portable-v{version}-windows-x64"
    portable_dir = BUILD_ROOT / "portable" / portable_name
    portable_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, portable_dir / executable.name)
    shutil.copy2(ROOT / "LICENSE", portable_dir / "LICENSE")
    shutil.copy2(PORTABLE_README, portable_dir / "README-PORTABLE.txt")
    shutil.copy2(build_info, portable_dir / build_info.name)
    (portable_dir / "portable.mode").write_text(
        "Presence of this file keeps Zhaozuo data beside the executable.\n",
        encoding="utf-8",
    )

    run_self_test(portable_dir / "Zhaozuo.exe", portable_dir=portable_dir)

    archive = RELEASE_DIR / f"{portable_name}.zip"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as handle:
        for path in sorted(portable_dir.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(portable_dir.parent))
    return archive


def find_iscc(configured: str) -> Path | None:
    candidates = [Path(configured)] if configured else []
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(
            Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Inno Setup 6" / "ISCC.exe"
        )
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "Inno Setup 6" / "ISCC.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def build_installer(version: str, configured_iscc: str) -> Path:
    compiler = find_iscc(configured_iscc)
    if compiler is None:
        raise RuntimeError(
            "Inno Setup 6 was not found; pass --iscc or set INNO_SETUP_COMPILER"
        )
    subprocess.run(
        [str(compiler), f"/DAppVersion={version}", str(ISS_SCRIPT)],
        cwd=ROOT,
        check=True,
    )
    installer = RELEASE_DIR / f"Zhaozuo-Setup-v{version}-windows-x64.exe"
    if not installer.is_file():
        raise RuntimeError("Inno Setup did not produce the expected installer")
    return installer


def smoke_installer(installer: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="zhaozuo-installer-smoke-") as directory:
        install_dir = Path(directory) / "installed"
        log_path = Path(directory) / "install.log"
        subprocess.run(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
                f"/DIR={install_dir}",
                f"/LOG={log_path}",
            ],
            check=True,
            timeout=120,
        )
        executable = install_dir / "Zhaozuo.exe"
        if not executable.is_file():
            raise RuntimeError("installer smoke test could not find installed executable")
        run_self_test(
            executable, local_app_data=Path(directory) / "local-app-data"
        )
        uninstaller = install_dir / "unins000.exe"
        subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            check=True,
            timeout=120,
        )
        if executable.exists():
            raise RuntimeError("installer smoke test did not remove the executable")


def write_checksums() -> Path:
    checksum_path = RELEASE_DIR / "SHA256SUMS.txt"
    lines = []
    for path in sorted(RELEASE_DIR.iterdir()):
        if path.is_file() and path != checksum_path:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.name}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Zhaozuo Windows distributions")
    parser.add_argument("--installer", action="store_true")
    parser.add_argument("--smoke-installer", action="store_true")
    parser.add_argument(
        "--iscc", default=os.environ.get("INNO_SETUP_COMPILER", ""), metavar="PATH"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.smoke_installer and not args.installer:
        raise SystemExit("--smoke-installer requires --installer")

    version = project_version()
    commit = source_commit()
    _reset_directory(BUILD_ROOT)
    _reset_directory(DIST_WINDOWS)
    _reset_directory(RELEASE_DIR)

    executable = build_executable(version)
    build_info = write_build_info(version, commit)
    run_self_test(executable)
    portable = build_portable(version, executable, build_info)
    outputs = [portable]
    if args.installer:
        installer = build_installer(version, args.iscc)
        if args.smoke_installer:
            smoke_installer(installer)
        outputs.append(installer)
    outputs.append(write_checksums())

    print(
        json.dumps(
            {
                "ok": True,
                "version": version,
                "signed": False,
                "outputs": [str(path.resolve()) for path in outputs],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
