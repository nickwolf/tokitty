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
import os
import re
import shlex
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tokitty.accounts import (
    DEFAULT_PROVIDER,
    Account,
    canonicalize_locator,
    load_accounts_result,
    save_accounts,
)
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


@dataclass(frozen=True)
class HookTarget:
    """Where a provider's hooks live and which events tokitty owns there."""
    settings_file: str
    local_settings_file: Optional[str]  # read-only; None if the harness has none
    events: Tuple[Tuple[str, str], ...]


_HOOK_TARGETS: Dict[str, HookTarget] = {
    "claude": HookTarget("settings.json", "settings.local.json", tuple(HOOK_EVENTS)),
}


def _hook_target(provider: Optional[str]) -> HookTarget:
    """The settings files and events tokitty owns for a provider.

    Raises ValueError for a provider with no entry here. That can only
    happen if a provider declares activity=True without a target, which
    is a programming error, not something a caller needs to recover from.
    """
    key = provider or DEFAULT_PROVIDER
    try:
        return _HOOK_TARGETS[key]
    except KeyError:
        raise ValueError(f"no hook target registered for provider {key!r}") from None


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


def provider_has_hooks(kind: Optional[str]) -> bool:
    """Whether an account of this provider kind gets tokitty's hooks.

    Driven by the provider's declared activity capability, since the hooks
    exist only to feed live activity. A kind this build does not know gets
    no hooks: writing Claude Code settings into a directory that belongs to
    some other harness is the one outcome worse than a missing pose.
    """
    # Imported here: the registry pulls in the poller and the API client,
    # which the CLI's --install-hooks path has no other need for.
    from tokitty.providers import UnknownProviderError, get_provider

    try:
        return get_provider(kind or DEFAULT_PROVIDER).capabilities.activity
    except UnknownProviderError:
        return False


def get_config_dirs(state_dir: Optional[Path] = None) -> List[Tuple[str, str]]:
    """Return the (config_dir, provider) pairs to install/uninstall hooks in.

    Default is the single dir ~/.claude (or, on Windows with WSL, the
    \\\\wsl.localhost dir where Claude Code actually lives -- see
    _default_config_dir), paired with DEFAULT_PROVIDER. If
    <state-dir>/accounts.json exists and contains a list of config-dir
    paths under key "accounts" (each item an object with a "config_dir"
    key), those are used instead, minus any account whose provider has no
    hooks. An accounts.json holding only such accounts yields an empty
    list rather than the default dir, which is not an account the user
    asked for. state_dir defaults to get_state_dir() when not given.
    """
    state_dir = state_dir if state_dir is not None else get_state_dir()
    accounts_file = Path(state_dir) / "accounts.json"
    if accounts_file.exists():
        try:
            with open(accounts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts = data.get("accounts")
            if isinstance(accounts, list) and accounts:
                entries = [a for a in accounts if isinstance(a, dict) and "config_dir" in a]
                if entries:
                    return [
                        (a["config_dir"], a.get("provider") or DEFAULT_PROVIDER)
                        for a in entries
                        if provider_has_hooks(a.get("provider"))
                    ]
        except Exception:
            pass
    return [(_default_config_dir(), DEFAULT_PROVIDER)]


def _build_command(config_dir: str) -> dict:
    native = _wsl_native_path(config_dir)
    native = native.rstrip("/\\") or native
    interpreter = "python" if _is_windows_local_path(config_dir) else "python3"
    script = f'"{native}/tokitty/hook_writer.py"'
    sessions_dir = f'"{native}/tokitty/sessions"'
    command = f"{interpreter} {script} --sessions-dir {sessions_dir}"
    return {"type": "command", "command": command}


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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _normalize_home_path(path: str) -> str:
    """Normalise a home (or a path derived from one) for comparison.

    Backslashes become forward slashes, repeated slashes collapse to one,
    a trailing slash is stripped, and the result is case-folded when it
    starts with a drive letter -- those are case-insensitive, unlike a
    POSIX path, which must not be folded.
    """
    normalized = re.sub(r"/+", "/", path.replace("\\", "/"))
    if len(normalized) > 1:
        normalized = normalized.rstrip("/") or normalized
    if _is_windows_local_path(normalized):
        normalized = normalized.casefold()
    return normalized


def _is_owned_hook(hook, config_dir: str, provider: str = DEFAULT_PROVIDER) -> bool:
    """Whether hook is the exact command tokitty writes for config_dir.

    Ownership is yes-or-no only: a hook is owned if it is a "command"
    hook whose command parses (shlex, posix mode -- handles both the
    quoted and the historical unquoted form) into exactly an interpreter,
    the hook_writer.py path, "--sessions-dir", and the sessions path, and
    both paths normalise to this home's tokitty/hook_writer.py and
    tokitty/sessions. An equivalent spelling (quoting, a doubled slash, a
    differently-cased drive letter) is still owned; a hook aimed at
    another home, or one that merely mentions tokitty, is not.

    provider is accepted for forward compatibility with non-Claude
    shapes; only Claude's shape is recognised today.
    """
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return False
    if len(parts) != 4:
        return False
    interpreter, script, flag, sessions_arg = parts
    if interpreter not in ("python", "python3") or flag != "--sessions-dir":
        return False
    home = _normalize_home_path(_wsl_native_path(config_dir))
    expected_script = f"{home}/tokitty/hook_writer.py"
    expected_sessions = f"{home}/tokitty/sessions"
    return (
        _normalize_home_path(script) == expected_script
        and _normalize_home_path(sessions_arg) == expected_sessions
    )


def _is_tokitty_entry(entry, config_dir: str, provider: str = DEFAULT_PROVIDER) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []):
        if _is_owned_hook(hook, config_dir, provider):
            return True
    return False


def _events_with_tokitty_entries(data, config_dir: str, provider: str = DEFAULT_PROVIDER) -> set:
    events = set()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return events
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        if any(_is_tokitty_entry(e, config_dir, provider) for e in entries):
            events.add(event)
    return events


class ConfigDirResult:
    def __init__(self, config_dir: str, ok: bool, message: str, installed_events: Optional[List[str]] = None):
        self.config_dir = config_dir
        self.ok = ok
        self.message = message
        self.installed_events = installed_events or []


def install_hooks_for_dir(config_dir: str, provider: str = DEFAULT_PROVIDER) -> ConfigDirResult:
    target = _hook_target(provider)
    base = Path(_local_config_path(config_dir))
    settings_path = base / target.settings_file

    data, error = _load_settings(settings_path)
    if error:
        return ConfigDirResult(config_dir, False, f"aborted, could not parse {target.settings_file}: {error}")

    local_data: dict = {}
    if target.local_settings_file is not None:
        local_data, local_error = _load_settings(base / target.local_settings_file)
        if local_error:
            return ConfigDirResult(
                config_dir, False, f"aborted, could not parse {target.local_settings_file}: {local_error}"
            )

    already_installed = _events_with_tokitty_entries(data, config_dir, provider) | _events_with_tokitty_entries(
        local_data, config_dir, provider
    )

    handler = _build_command(config_dir)

    hooks_dest = base / "tokitty" / "hook_writer.py"
    hooks_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_HOOK_WRITER_SOURCE, hooks_dest)

    events_to_add = [(event, matcher) for event, matcher in target.events if event not in already_installed]

    if not events_to_add:
        return ConfigDirResult(config_dir, True, "already installed, nothing to do", installed_events=[])

    existing_hooks = data.get("hooks")
    if existing_hooks is not None and not isinstance(existing_hooks, dict):
        return ConfigDirResult(
            config_dir, False, f"aborted, {target.settings_file} 'hooks' key is not an object: {existing_hooks!r}"
        )
    for event, _matcher in events_to_add:
        entries = existing_hooks.get(event) if existing_hooks else None
        if entries is not None and not isinstance(entries, list):
            return ConfigDirResult(
                config_dir,
                False,
                f"aborted, {target.settings_file} 'hooks.{event}' is not a list: {entries!r}",
            )

    _backup(settings_path)

    data.setdefault("hooks", {})
    installed = []
    for event, matcher in events_to_add:
        data["hooks"].setdefault(event, [])
        data["hooks"][event].append(
            {"matcher": matcher, "hooks": [dict(handler)]}
        )
        installed.append(event)

    _write_settings(settings_path, data)

    return ConfigDirResult(config_dir, True, "installed", installed_events=installed)


def uninstall_hooks_for_dir(config_dir: str, provider: str = DEFAULT_PROVIDER) -> ConfigDirResult:
    target = _hook_target(provider)
    base = Path(_local_config_path(config_dir))
    settings_path = base / target.settings_file

    data, error = _load_settings(settings_path)
    if error:
        return ConfigDirResult(config_dir, False, f"aborted, could not parse {target.settings_file}: {error}")

    warn_local = False
    if target.local_settings_file is not None:
        local_data, local_error = _load_settings(base / target.local_settings_file)
        if local_error is None and isinstance(local_data, dict):
            if _events_with_tokitty_entries(local_data, config_dir, provider):
                warn_local = True

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        msg = "no tokitty hooks found"
        if warn_local:
            msg += f" (note: tokitty-marked entries found in {target.local_settings_file}, left untouched)"
        return ConfigDirResult(config_dir, True, msg, installed_events=[])

    removed = []
    for event in list(hooks.keys()):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not _is_tokitty_entry(e, config_dir, provider)]
        if len(kept) != len(entries):
            removed.append(event)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]

    if not removed:
        msg = "no tokitty hooks found"
        if warn_local:
            msg += f" (note: tokitty-marked entries found in {target.local_settings_file}, left untouched)"
        return ConfigDirResult(config_dir, True, msg, installed_events=[])

    _backup(settings_path)
    _write_settings(settings_path, data)

    msg = "uninstalled"
    if warn_local:
        msg += f" (note: tokitty-marked entries found in {target.local_settings_file}, left untouched)"
    return ConfigDirResult(config_dir, True, msg, installed_events=removed)


PENDING_HOOK_OP_FILENAME = "pending_hook_op.json"


def save_pending_hook_op(
    state_dir: Path, op: str, config_dir: str, provider: Optional[str] = None
) -> None:
    path = Path(state_dir) / PENDING_HOOK_OP_FILENAME
    payload = {"op": op, "config_dir": config_dir}
    if provider is not None:
        payload["provider"] = provider
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def load_pending_hook_op(state_dir: Path) -> Optional[dict]:
    path = Path(state_dir) / PENDING_HOOK_OP_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("op") not in ("install", "remove") or not data.get("config_dir"):
        return None
    pending = {"op": data["op"], "config_dir": data["config_dir"]}
    if isinstance(data.get("provider"), str):
        pending["provider"] = data["provider"]
    return pending


def clear_pending_hook_op(state_dir: Path) -> None:
    path = Path(state_dir) / PENDING_HOOK_OP_FILENAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def apply_account_mutation(
    state_dir: Path,
    accounts: List[Account],
    op: str,
    config_dir: str,
    install_fn=install_hooks_for_dir,
    uninstall_fn=uninstall_hooks_for_dir,
    provider: str = DEFAULT_PROVIDER,
) -> ConfigDirResult:
    """accounts.json first (durable desired state), then a pending hook
    op record, then the hook side effect, clearing the record only on
    success. Call this off the Tk thread (see accounts_ui.py, Task 13) --
    a slow filesystem or a stuck wsl.exe call must not freeze the UI.
    result.ok is False and a raised exception are both treated as "did
    not complete": both leave the pending-op record in place for
    retry_pending_hook_op to pick up.

    A provider without hooks stops after the save: no pending op is
    recorded and nothing is written inside config_dir."""
    save_accounts(state_dir, accounts)
    if not provider_has_hooks(provider):
        return ConfigDirResult(config_dir, True, "saved, this harness has no hooks")
    save_pending_hook_op(state_dir, op, config_dir, provider)
    fn = install_fn if op == "install" else uninstall_fn
    result = fn(config_dir, provider)
    if result.ok:
        clear_pending_hook_op(state_dir)
    return result


def _pending_dir_matched_provider(state_dir: Path, config_dir: str) -> Optional[str]:
    """Provider of the accounts.json entry matching config_dir, or None if
    the account has been removed (or never existed) since a legacy pending
    record without its own provider was written."""
    try:
        locator = canonicalize_locator(config_dir)
    except ValueError:
        return None
    for account in load_accounts_result(state_dir).accounts:
        try:
            if canonicalize_locator(account.config_dir) == locator:
                return account.provider
        except ValueError:
            continue
    return None


def _pending_dir_has_hooks(state_dir: Path, config_dir: str) -> bool:
    """Whether a legacy pending op's dir is one that gets hooks. Records
    written by this build carry their provider and never get here.

    The account's own provider decides when it is still in accounts.json.
    A removed account is gone from there, so its dir is judged by what is
    in it; this runs off the Tk thread, like the hook op it gates.
    """
    from tokitty.manual_path import looks_like_codex_home

    matched = _pending_dir_matched_provider(state_dir, config_dir)
    if matched is not None:
        return provider_has_hooks(matched)
    return not looks_like_codex_home(_local_config_path(config_dir))


def retry_pending_hook_op(
    state_dir: Path, install_fn=install_hooks_for_dir, uninstall_fn=uninstall_hooks_for_dir
) -> Optional[ConfigDirResult]:
    """Called at next startup, or the next time the manager is opened.
    Returns None if there was nothing pending."""
    pending = load_pending_hook_op(state_dir)
    if pending is None:
        return None
    if "provider" in pending:
        provider = pending["provider"]
        has_hooks = provider_has_hooks(provider)
    else:
        has_hooks = _pending_dir_has_hooks(state_dir, pending["config_dir"])
        matched = _pending_dir_matched_provider(state_dir, pending["config_dir"])
        provider = matched if matched is not None else DEFAULT_PROVIDER
    if not has_hooks:
        # Left by a build that installed hooks into every account. Replaying
        # it would write Claude Code settings into another harness's home.
        clear_pending_hook_op(state_dir)
        return None
    fn = install_fn if pending["op"] == "install" else uninstall_fn
    result = fn(pending["config_dir"], provider)
    if result.ok:
        clear_pending_hook_op(state_dir)
    return result


def install_hooks() -> int:
    config_dirs = get_config_dirs()
    if not config_dirs:
        print("No accounts use a harness with hooks; nothing to install.")
        return 0
    any_failed = False
    for config_dir, provider in config_dirs:
        result = install_hooks_for_dir(config_dir, provider)
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
    if not config_dirs:
        print("No accounts use a harness with hooks; nothing to uninstall.")
        return 0
    any_failed = False
    for config_dir, provider in config_dirs:
        result = uninstall_hooks_for_dir(config_dir, provider)
        if not result.ok:
            any_failed = True
            print(f"{config_dir}: {result.message}", file=sys.stderr)
            continue
        print(f"{config_dir}: {result.message}")
    print("The copied hook_writer.py and sessions state files were left in place; "
          "delete <config-dir>/tokitty/ manually if you want them gone.")
    return 1 if any_failed else 0
