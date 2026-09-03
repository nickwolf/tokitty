"""Cross-platform "launch tokitty at login" seam: no installer, no
elevation, stdlib only. See docs/superpowers/specs/
2026-09-01-autostart-per-os-design.md.
"""
from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath
from typing import List, Optional

LAUNCHER_FILENAME = "autostart_launcher.pyw"


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_launch_command(
    state_dir: Path,
    *,
    repo_root: Optional[Path] = None,
    frozen: Optional[bool] = None,
    executable: Optional[str] = None,
    platform: Optional[str] = None,
) -> List[str]:
    """The argv the OS should run to relaunch tokitty at login. Pure:
    every platform-dependent input is an optional override, so this is
    fully testable on any CI OS, never just the one it describes.

    A frozen build (#48) is simplest: the executable alone, no launcher
    needed. A repo clone cannot register "-m tokitty" directly -- it
    isn't pip-installed, so it only imports because the process starts
    with the repo root as cwd, and a Run-key/.desktop/.plist entry has no
    working-directory field (verified on real Windows Python: `cd
    C:\\Users && python.exe -c "import tokitty"` fails,
    `cd C:\\Tools\\tokitty && python.exe -c "import tokitty"` works). So a
    repo clone instead points at the generated launcher file in
    state_dir (see write_launcher_file, Task 2), which pins the repo
    root itself.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    platform = sys.platform if platform is None else platform

    if frozen:
        return [executable]

    launcher = str(Path(state_dir) / LAUNCHER_FILENAME)
    if platform == "win32":
        return [_windows_pythonw_path(executable), launcher]
    return [executable, launcher]


def _windows_pythonw_path(python_executable: str) -> str:
    """Swap .../python.exe for .../pythonw.exe (no console window at
    login); pass through unchanged if the name doesn't match that exact
    shape (e.g. a frozen tokitty.exe, or an already-pythonw.exe path).

    Uses PureWindowsPath rather than pathlib.Path deliberately: a bare
    Path on POSIX has no concept of backslash separators or drive
    letters and would silently treat 'C:\\Program Files\\...\\python.exe'
    as one opaque filename -- exactly the class of bug this repo's own
    accounts-setup-ui branch shipped four of, by validating only under
    WSL/Linux. PureWindowsPath parses Windows paths correctly regardless
    of host OS, so this is testable on every CI platform, not just
    win32."""
    path = PureWindowsPath(python_executable)
    if path.name.lower() == "pythonw.exe":
        return python_executable
    if path.name.lower() == "python.exe":
        return str(path.with_name("pythonw.exe"))
    return python_executable
