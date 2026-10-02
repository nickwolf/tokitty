"""Open a new Windows Terminal tab running `claude` for a preset.

Presets live in settings (see `Settings.streamdock_presets`). Account
checks happen here, at launch time, because the account list can change
after the settings file was written.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Callable, List, Optional

from tokitty.accounts import DEFAULT_PROVIDER, Account, parse_wsl_unc

_WSL_DEFAULT_DIR = re.compile(r"^(/home/[^/]+|/root)/\.claude/?$")
# Windows Terminal reads a bare semicolon as a subcommand separator, and
# escaping rules differ per layer, so these are refused outright.
_FORBIDDEN = (";", '"')


def find_preset(presets, name) -> Optional[dict]:
    for preset in presets or []:
        if isinstance(preset, dict) and preset.get("name") == name:
            return preset
    return None


def _check_text(label: str, value: str) -> None:
    if any(ch in value for ch in _FORBIDDEN):
        raise ValueError(f"{label} contains a semicolon or double quote")


def _normalise_windows(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").lower()


def build_command(preset: dict, account: Account, *, userprofile: Optional[str] = None) -> List[str]:
    """The argument list that opens the tab. Raises ValueError with a short
    reason when the preset cannot be launched for this account."""
    if account.provider != DEFAULT_PROVIDER:
        raise ValueError("account is not a Claude account")
    env = preset.get("env")
    cwd = preset.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("preset has no cwd")
    _check_text("cwd", cwd)
    unc = parse_wsl_unc(account.config_dir)
    if env == "wsl":
        distro = preset.get("distro")
        if not isinstance(distro, str) or not distro:
            raise ValueError("preset has no distro")
        _check_text("distro", distro)
        if unc is None:
            raise ValueError("account is not in WSL")
        if unc[0].lower() != distro.lower():
            raise ValueError("account is in a different WSL distro")
        posix = unc[1]
        _check_text("config path", posix)
        command = ["wt.exe", "-w", "0", "new-tab", "wsl.exe", "-d", distro, "--cd", cwd, "--"]
        if not _WSL_DEFAULT_DIR.match(posix):
            command += ["env", f"CLAUDE_CONFIG_DIR={posix}"]
        return command + ["bash", "-lic", "claude"]
    if env == "native":
        if unc is not None:
            raise ValueError("account is in WSL, preset is native")
        path = account.config_dir
        _check_text("config path", path)
        command = ["wt.exe", "-w", "0", "new-tab", "-d", cwd]
        if userprofile is None:
            userprofile = os.environ.get("USERPROFILE", "")
        default = _normalise_windows(userprofile + "\\.claude") if userprofile else None
        if _normalise_windows(path) == default:
            return command + ["claude"]
        # The tab inherits the running Windows Terminal's environment, not
        # ours, so the variable has to be set inside the tab itself.
        return command + ["cmd.exe", "/k", f'set "CLAUDE_CONFIG_DIR={path}" && claude']
    raise ValueError("preset env must be wsl or native")


def launch(preset: dict, account: Account, *, popen_fn: Callable = subprocess.Popen) -> None:
    """Start the tab and return without waiting on it."""
    command = build_command(preset, account)
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    popen_fn(command, **kwargs)
