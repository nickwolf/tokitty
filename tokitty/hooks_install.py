"""Installer for tokitty's Claude Code hooks (--install-hooks / --uninstall-hooks).

Copies tokitty/hook_writer.py into each configured Claude Code config dir
and registers it in that dir's settings.json for the hook events tokitty
needs to observe live session activity. See docs/hook-preflight-2026-07-16.md
for why the script is copied onto the config dir's own filesystem (ext4 vs
/mnt/c latency) instead of invoked in place, and why running sessions need a
restart to pick up hook edits.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

from tokitty.paths import get_state_dir

MARKER = "tokitty"

HOOK_EVENTS = [
    ("UserPromptSubmit", ""),
    ("PreToolUse", ""),
    ("PostToolUse", ""),
    ("Notification", "permission_prompt"),
    ("Stop", ""),
    ("SubagentStop", ""),
    ("SessionEnd", ""),
]

_HOOK_WRITER_SOURCE = Path(__file__).resolve().parent / "hook_writer.py"


def _wsl_native_path(config_dir: str) -> str:
    """Map a Windows-visible WSL path to its WSL-native POSIX form.

    Handles \\\\wsl.localhost\\<Distro>\\home\\<user>\\... and
    \\\\wsl$\\<Distro>\\home\\<user>\\... UNC forms, converting to
    /home/<user>/.... Any path not matching that UNC shape (a plain POSIX
    path, or a Windows-local path like C:\\Users\\...) passes through
    unchanged -- the caller is responsible for deciding whether a
    Windows-local path needs a different interpreter invocation.
    """
    normalized = config_dir.replace("/", "\\")
    for prefix in ("\\\\wsl.localhost\\", "\\\\wsl$\\"):
        if normalized.lower().startswith(prefix.lower()):
            rest = normalized[len(prefix):]
            parts = rest.split("\\")
            # parts[0] is the distro name; the remainder is the in-distro path.
            posix_rest = "/".join(p for p in parts[1:] if p != "")
            return "/" + posix_rest
    return config_dir


def _local_config_path(config_dir: str) -> str:
    """The path this process should use to reach config_dir's filesystem.

    On Windows a \\\\wsl.localhost UNC dir is directly reachable, so it
    passes through. On Linux/macOS that same accounts.json entry must be
    translated to its in-distro posix form -- feeding the UNC string to
    Path() there silently creates a literal './\\\\wsl.localhost\\...'
    directory and reports success while touching nothing real (bug #35).
    """
    if sys.platform == "win32":
        return config_dir
    return _wsl_native_path(config_dir)


def _is_windows_local_path(config_dir: str) -> bool:
    return len(config_dir) >= 2 and config_dir[1] == ":" and config_dir[0].isalpha()


def _default_config_dir() -> str:
    """Return the default single config dir, resolving WSL on Windows.

    On win32, Claude Code actually lives inside WSL (the watcher polls it
    via find_wsl_credentials too -- see __main__.resolve_activity_sessions),
    so the installer must target the same \\\\wsl.localhost UNC dir rather
    than a Windows-local ~/.claude that nothing ever reads. Falls back to
    the Windows-local path if WSL resolution fails for any reason (no WSL
    installed, no credentials found, ambiguous install, wsl.exe error).
    """
    if sys.platform == "win32":
        try:
            from tokitty.wsl_probe import find_wsl_credentials, wsl_config_dir_from_credentials

            distro, wsl_credentials_path = find_wsl_credentials()
            return wsl_config_dir_from_credentials(distro, wsl_credentials_path)
        except Exception:
            pass
    return str(Path.home() / ".claude")


def get_config_dirs() -> List[str]:
    """Return the list of Claude Code config dirs to install/uninstall hooks in.

    Default is the single dir ~/.claude (or, on Windows with WSL, the
    \\\\wsl.localhost dir where Claude Code actually lives -- see
    _default_config_dir). If <state-dir>/accounts.json exists and contains
    a list of config-dir paths under key "accounts" (each item an object
    with a "config_dir" key), those are used instead.
    """
    state_dir = get_state_dir()
    accounts_file = state_dir / "accounts.json"
    if accounts_file.exists():
        try:
            with open(accounts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts = data.get("accounts")
            if isinstance(accounts, list) and accounts:
                dirs = [a["config_dir"] for a in accounts if isinstance(a, dict) and "config_dir" in a]
                if dirs:
                    return dirs
        except Exception:
            pass
    return [_default_config_dir()]


def _build_command(config_dir: str) -> str:
    native = _wsl_native_path(config_dir)
    interpreter = "python" if _is_windows_local_path(config_dir) else "python3"
    script = f'"{native}/tokitty/hook_writer.py"'
    sessions_dir = f'"{native}/tokitty/sessions"'
    return f"{interpreter} {script} --sessions-dir {sessions_dir}"


def _backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(f"{path.name}.tokitty-backup-{stamp}")
    if backup_path.exists():
        suffix = 2
        while True:
            candidate = path.with_name(f"{path.name}.tokitty-backup-{stamp}-{suffix}")
            if not candidate.exists():
                backup_path = candidate
                break
            suffix += 1
    shutil.copy2(path, backup_path)


def _load_settings(path: Path):
    """Return (data, error). error is None on success. Missing file -> ({}, None)."""
    if not path.exists():
        return {}, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            return {}, None
        return json.loads(text), None
    except Exception as exc:
        return None, f"{path}: could not parse existing JSON ({exc})"


def _write_settings(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _is_tokitty_entry(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []):
        if isinstance(hook, dict) and MARKER in str(hook.get("command", "")):
            return True
    return False


def _events_with_tokitty_entries(data) -> set:
    events = set()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return events
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        if any(_is_tokitty_entry(e) for e in entries):
            events.add(event)
    return events


class ConfigDirResult:
    def __init__(self, config_dir: str, ok: bool, message: str, installed_events: Optional[List[str]] = None):
        self.config_dir = config_dir
        self.ok = ok
        self.message = message
        self.installed_events = installed_events or []


def install_hooks_for_dir(config_dir: str) -> ConfigDirResult:
    base = Path(_local_config_path(config_dir))
    settings_path = base / "settings.json"
    local_settings_path = base / "settings.local.json"

    data, error = _load_settings(settings_path)
    if error:
        return ConfigDirResult(config_dir, False, f"aborted, could not parse settings.json: {error}")

    local_data, local_error = _load_settings(local_settings_path)
    if local_error:
        return ConfigDirResult(config_dir, False, f"aborted, could not parse settings.local.json: {local_error}")

    already_installed = _events_with_tokitty_entries(data) | _events_with_tokitty_entries(local_data)

    command = _build_command(config_dir)

    hooks_dest = base / "tokitty" / "hook_writer.py"
    hooks_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_HOOK_WRITER_SOURCE, hooks_dest)

    events_to_add = [(event, matcher) for event, matcher in HOOK_EVENTS if event not in already_installed]

    if not events_to_add:
        return ConfigDirResult(config_dir, True, "already installed, nothing to do", installed_events=[])

    existing_hooks = data.get("hooks")
    if existing_hooks is not None and not isinstance(existing_hooks, dict):
        return ConfigDirResult(
            config_dir, False, f"aborted, settings.json 'hooks' key is not an object: {existing_hooks!r}"
        )
    for event, _matcher in events_to_add:
        entries = existing_hooks.get(event) if existing_hooks else None
        if entries is not None and not isinstance(entries, list):
            return ConfigDirResult(
                config_dir,
                False,
                f"aborted, settings.json 'hooks.{event}' is not a list: {entries!r}",
            )

    _backup(settings_path)

    data.setdefault("hooks", {})
    installed = []
    for event, matcher in events_to_add:
        data["hooks"].setdefault(event, [])
        data["hooks"][event].append(
            {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
        )
        installed.append(event)

    _write_settings(settings_path, data)

    return ConfigDirResult(config_dir, True, "installed", installed_events=installed)


def uninstall_hooks_for_dir(config_dir: str) -> ConfigDirResult:
    base = Path(_local_config_path(config_dir))
    settings_path = base / "settings.json"
    local_settings_path = base / "settings.local.json"

    data, error = _load_settings(settings_path)
    if error:
        return ConfigDirResult(config_dir, False, f"aborted, could not parse settings.json: {error}")

    local_data, local_error = _load_settings(local_settings_path)
    warn_local = False
    if local_error is None and isinstance(local_data, dict):
        if _events_with_tokitty_entries(local_data):
            warn_local = True

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        msg = "no tokitty hooks found"
        if warn_local:
            msg += " (note: tokitty-marked entries found in settings.local.json, left untouched)"
        return ConfigDirResult(config_dir, True, msg, installed_events=[])

    removed = []
    for event in list(hooks.keys()):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not _is_tokitty_entry(e)]
        if len(kept) != len(entries):
            removed.append(event)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]

    if not removed:
        msg = "no tokitty hooks found"
        if warn_local:
            msg += " (note: tokitty-marked entries found in settings.local.json, left untouched)"
        return ConfigDirResult(config_dir, True, msg, installed_events=[])

    _backup(settings_path)
    _write_settings(settings_path, data)

    msg = "uninstalled"
    if warn_local:
        msg += " (note: tokitty-marked entries found in settings.local.json, left untouched)"
    return ConfigDirResult(config_dir, True, msg, installed_events=removed)


def install_hooks() -> int:
    config_dirs = get_config_dirs()
    any_failed = False
    for config_dir in config_dirs:
        result = install_hooks_for_dir(config_dir)
        if not result.ok:
            any_failed = True
            print(f"{config_dir}: {result.message}", file=sys.stderr)
            continue
        if result.installed_events:
            print(f"{config_dir}: installed hooks for {', '.join(result.installed_events)}")
        else:
            print(f"{config_dir}: {result.message}")
    print("If the cat doesn't react, restart running Claude Code sessions "
          "(hook edits are not hot-reloaded).")
    return 1 if any_failed else 0


def uninstall_hooks() -> int:
    config_dirs = get_config_dirs()
    any_failed = False
    for config_dir in config_dirs:
        result = uninstall_hooks_for_dir(config_dir)
        if not result.ok:
            any_failed = True
            print(f"{config_dir}: {result.message}", file=sys.stderr)
            continue
        print(f"{config_dir}: {result.message}")
    print("The copied hook_writer.py and sessions state files were left in place; "
          "delete <config-dir>/tokitty/ manually if you want them gone.")
    return 1 if any_failed else 0
