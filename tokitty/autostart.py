"""Cross-platform "launch tokitty at login" seam: no installer, no
elevation, stdlib only. See docs/superpowers/specs/
2026-09-01-autostart-per-os-design.md.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
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


def launcher_content(repo_root) -> str:
    """Source of the generated launcher: pins repo_root onto sys.path and
    imports tokitty.__main__.main directly, rather than running
    tokitty/__main__.py as a script -- running the file directly would
    put <repo>/tokitty on sys.path instead of <repo>, so "import tokitty"
    would still fail. repr() (not manual string-building) embeds
    repo_root safely: it produces a valid, correctly escaped Python
    string literal for any path on any OS, including one with backslashes
    or spaces, without ever needing a backslash inside an f-string
    expression -- a SyntaxError before Python 3.12, and this repo's
    floor is 3.10."""
    repo_root_literal = repr(str(repo_root))
    return (
        "import sys\n"
        f"sys.path.insert(0, {repo_root_literal})\n"
        "from tokitty.__main__ import main\n"
        "main()\n"
    )


def write_launcher_file(state_dir: Path, repo_root: Optional[Path] = None) -> Path:
    """Write autostart_launcher.pyw into state_dir atomically (tmp
    sibling + os.replace, matching settings.py/hooks_install.py) and
    return its path. repo_root defaults to the real repo root, mirroring
    resolve_launch_command's default."""
    repo_root = _default_repo_root() if repo_root is None else repo_root
    path = Path(state_dir) / LAUNCHER_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(launcher_content(repo_root), encoding="utf-8")
    os.replace(tmp_path, path)
    return path


WINDOWS_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_VALUE_NAME = "Tokitty"


class _RealWindowsRegistry:
    """Talks to the real HKCU Run key. winreg is imported lazily inside
    each method, never at module scope -- it doesn't exist off win32, and
    this module must stay importable (and its tests runnable) on every
    CI OS."""

    def read_value(self) -> Optional[str]:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_KEY_PATH) as key:
                value, _ = winreg.QueryValueEx(key, WINDOWS_VALUE_NAME)
                return value
        except FileNotFoundError:
            return None

    def write_value(self, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_KEY_PATH) as key:
            winreg.SetValueEx(key, WINDOWS_VALUE_NAME, 0, winreg.REG_SZ, value)

    def delete_value(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WINDOWS_VALUE_NAME)
        except FileNotFoundError:
            pass


class WindowsRegistryBackend:
    """HKCU\\...\\Run entry, value name "Tokitty". register() stores the
    command as one string via subprocess.list2cmdline -- the same
    quoting Windows' own CreateProcess-family APIs expect, so it quotes
    only the arguments that need it (embedded spaces or special chars),
    doubling backslashes before an embedded quote per the documented
    algorithm. is_current() never parses that string back into a list
    (a genuinely hard problem for Windows command-line quoting); it
    re-serializes the candidate command the same way and compares the
    two strings."""

    def __init__(self, registry=None):
        self._registry = registry if registry is not None else _RealWindowsRegistry()

    def register(self, command: List[str]) -> None:
        self._registry.write_value(subprocess.list2cmdline(command))

    def deregister(self) -> None:
        self._registry.delete_value()

    def is_registered(self) -> bool:
        return self._registry.read_value() is not None

    def is_current(self, command: List[str]) -> bool:
        return self._registry.read_value() == subprocess.list2cmdline(command)


MAC_LAUNCH_AGENT_LABEL = "com.nickwolf.tokitty"


class MacLaunchAgentBackend:
    """~/Library/LaunchAgents/com.nickwolf.tokitty.plist, RunAtLoad true.
    Writing the plist takes effect at the NEXT login; this does not shell
    out to `launchctl load`/`bootstrap` for immediate activation in the
    current session -- out of scope, the design doesn't specify
    activation timing."""

    def __init__(self, launch_agents_dir: Optional[Path] = None):
        self._dir = launch_agents_dir if launch_agents_dir is not None else (Path.home() / "Library" / "LaunchAgents")

    def _path(self) -> Path:
        return self._dir / f"{MAC_LAUNCH_AGENT_LABEL}.plist"

    def register(self, command: List[str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"Label": MAC_LAUNCH_AGENT_LABEL, "ProgramArguments": list(command), "RunAtLoad": True}
        path = self._path()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            plistlib.dump(payload, f)
        os.replace(tmp_path, path)

    def deregister(self) -> None:
        try:
            self._path().unlink()
        except FileNotFoundError:
            pass

    def is_registered(self) -> bool:
        return self._path().is_file()

    def is_current(self, command: List[str]) -> bool:
        path = self._path()
        if not path.is_file():
            return False
        try:
            data = plistlib.loads(path.read_bytes())
        except Exception:
            return False
        return data.get("ProgramArguments") == list(command)


LINUX_DESKTOP_FILENAME = "tokitty.desktop"
_DESKTOP_RESERVED_CHARS = set(" \t\n\"'\\><~|&;$*?#()`")


def _quote_desktop_arg(arg: str) -> str:
    """Desktop Entry Spec Exec-key quoting: double quotes, not the
    single-quote style subprocess/shlex.quote would use, with
    backslash-escaping of the few characters still special inside double
    quotes. Only engaged when needed -- a Linux interpreter path with
    none of these characters (the overwhelming common case) passes
    through unquoted."""
    if not any(ch in _DESKTOP_RESERVED_CHARS for ch in arg):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _render_exec_line(command: List[str]) -> str:
    return " ".join(_quote_desktop_arg(a) for a in command)


class LinuxDesktopEntryBackend:
    """~/.config/autostart/tokitty.desktop, plain text, no serialization
    library needed. is_current() re-renders the candidate command with
    the same quoting function used at registration and compares the
    resulting Exec= line string -- no unquoting/parsing."""

    def __init__(self, autostart_dir: Optional[Path] = None):
        if autostart_dir is not None:
            self._dir = autostart_dir
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
            self._dir = Path(base) / "autostart"

    def _path(self) -> Path:
        return self._dir / LINUX_DESKTOP_FILENAME

    def _content(self, command: List[str]) -> str:
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Tokitty\n"
            f"Exec={_render_exec_line(command)}\n"
            "X-GNOME-Autostart-enabled=true\n"
        )

    def register(self, command: List[str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(self._content(command), encoding="utf-8")
        os.replace(tmp_path, path)

    def deregister(self) -> None:
        try:
            self._path().unlink()
        except FileNotFoundError:
            pass

    def is_registered(self) -> bool:
        return self._path().is_file()

    def is_current(self, command: List[str]) -> bool:
        path = self._path()
        if not path.is_file():
            return False
        target = f"Exec={_render_exec_line(command)}"
        return any(line == target for line in path.read_text(encoding="utf-8").splitlines())


def get_backend(platform: Optional[str] = None):
    """Pick the backend for the given (or current) platform. None for an
    unrecognized platform string -- callers (menu wiring, Task 5) treat
    that as "no autostart support here" rather than raising."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return WindowsRegistryBackend()
    if platform == "darwin":
        return MacLaunchAgentBackend()
    if platform.startswith("linux"):
        return LinuxDesktopEntryBackend()
    return None
