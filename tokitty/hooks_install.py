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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, List, Optional, Tuple

from tokitty.accounts import (
    DEFAULT_PROVIDER,
    Account,
    canonicalize_locator,
    load_accounts_result,
    save_accounts,
)
from tokitty.frozen import MOVE_TO_APPLICATIONS, AppTranslocatedError
from tokitty.paths import get_state_dir, state_dir_path

MARKER = "tokitty"

HOOK_RUNNER_NAME = "tokitty-hook"

# Spec Q2a addendum: shown when the stable <state dir>/current link can't be
# made (a real directory occupies it, a repoint failed, or the cross-process
# lock timed out) and tokitty has fallen back to registering a path inside
# this release folder instead. {reason} is filled with a short description
# of what went wrong.
LINK_FALLBACK_WARNING = (
    "Tokitty could not set up its stable hook path ({reason}), so its hooks "
    "point into this release folder. Every update will need the hooks "
    "approved again in Codex, and open Claude Code sessions restarted."
)

_WSL_UNC_PREFIXES = ("\\\\wsl.localhost\\", "\\\\wsl$\\")

HOOK_EVENTS = [
    ("UserPromptSubmit", ""),
    ("PreToolUse", ""),
    ("PostToolUse", ""),
    ("Notification", "permission_prompt"),
    ("Stop", ""),
    ("SubagentStop", ""),
    ("SessionEnd", ""),
]

# Codex has no matcher on any of these: its MatcherGroup.matcher is optional
# and is part of the hashed config, so the group carries no "matcher" key.
CODEX_EVENTS: Tuple[Tuple[str, Optional[str]], ...] = (
    ("UserPromptSubmit", None),
    ("PreToolUse", None),
    ("PostToolUse", None),
    ("PermissionRequest", None),
    ("Stop", None),
    ("SubagentStop", None),
    ("Interrupt", None),
    ("SessionEnd", None),
)

CODEX_PROVIDER = "codex"

# Shown when a Codex handler was rewritten or moved: Codex hashes a handler's
# config and looks the hash up by position, so either change is a new review.
CODEX_REAPPROVE_WARNING = "Codex will ask you to approve Tokitty's hooks again."


@dataclass(frozen=True)
class HookTarget:
    """Where a provider's hooks live and which events tokitty owns there.

    A matcher of None means the group is written with no "matcher" key.
    timeouts is (event, seconds) pairs: those handlers carry a "timeout"
    field and no other does. exec_form is whether the provider can run a
    command plus args; one that cannot always gets a single command string.
    """
    settings_file: str
    local_settings_file: Optional[str]  # read-only; None if the harness has none
    events: Tuple[Tuple[str, Optional[str]], ...]
    timeouts: Tuple[Tuple[str, int], ...] = ()
    exec_form: bool = True


_HOOK_TARGETS: Dict[str, HookTarget] = {
    "claude": HookTarget("settings.json", "settings.local.json", tuple(HOOK_EVENTS)),
    CODEX_PROVIDER: HookTarget(
        "hooks.json",
        None,
        CODEX_EVENTS,
        timeouts=(("Interrupt", 3), ("SessionEnd", 3)),
        exec_form=False,
    ),
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
    for prefix in _WSL_UNC_PREFIXES:
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


def codex_paths(config_dir: str) -> Tuple[str, str]:
    """(hooks.json as this process opens it, hooks.json as Codex sees it).

    The second is what appears in Codex's trust keys: /home/u/.codex/hooks.json
    for a WSL home, a drive-letter path for a native Windows one.
    """
    filesystem = str(Path(_local_config_path(config_dir)) / "hooks.json")
    native = _wsl_native_path(config_dir).rstrip("/\\") or _wsl_native_path(config_dir)
    return filesystem, f"{native}/hooks.json"


def _is_windows_local_path(config_dir: str) -> bool:
    return len(config_dir) >= 2 and config_dir[1] == ":" and config_dir[0].isalpha()


def _is_wsl_unc(config_dir: str) -> bool:
    normalized = config_dir.replace("/", "\\").lower()
    return normalized.startswith(_WSL_UNC_PREFIXES)


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


def _config_dirs_from_accounts_file(state_dir: Path) -> Optional[List[Tuple[str, str]]]:
    """(config_dir, provider) pairs explicitly listed in
    <state_dir>/accounts.json, minus any account whose provider has no
    hooks -- or None if the file is absent, malformed, or lists no
    config_dir entries at all.

    None is the signal a caller uses to decide what "no explicit
    accounts" means: get_config_dirs falls back to a single default dir
    (a real end user's normal single-account case); ensure_current, the
    once-per-launch startup refresh, does not. Falling back there would
    mean resolving _default_config_dir() on every launch with no
    accounts.json, which on Windows shells into every WSL distro looking
    for credentials nobody asked it to refresh.
    """
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
    return None


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
    explicit = _config_dirs_from_accounts_file(state_dir)
    if explicit is not None:
        return explicit
    return [(_default_config_dir(), DEFAULT_PROVIDER)]


def hook_runner_path(executable: str, platform: str) -> str:
    """The bundled tokitty-hook beside a frozen build's own executable."""
    if platform == "win32":
        return str(PureWindowsPath(executable).with_name(HOOK_RUNNER_NAME + ".exe"))
    return str(PurePosixPath(executable).with_name(HOOK_RUNNER_NAME))


def stable_runner_path(state_dir, platform: str) -> str:
    """Where hooks run tokitty-hook from: a link in the state dir that each
    launch repoints at the running release, so the registered command never
    changes (spec Q2a)."""
    if platform == "win32":
        return str(PureWindowsPath(str(state_dir)) / "current" / (HOOK_RUNNER_NAME + ".exe"))
    return str(Path(state_dir) / "current" / HOOK_RUNNER_NAME)


def _build_command(
    config_dir: str, *, frozen=None, platform=None, runner=None, provider: str = DEFAULT_PROVIDER
) -> dict:
    """The hook to register, chosen by where Claude Code runs (spec Q2).

    A frozen build registers tokitty-hook in exec form (Claude Code
    2.1.139+), except for a WSL home seen from Windows, which keeps python3:
    WSL ships it and it is twice as fast as the exe through interop.

    Codex has no exec form, so its frozen entry is one command string, the
    runner quoted as a single token followed by --sessions-dir. Every other
    Codex case gets the same Python string Claude does.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    native = _wsl_native_path(config_dir).rstrip("/\\") or _wsl_native_path(config_dir)
    sessions_dir = f"{native}/tokitty/sessions"
    if frozen and not (platform == "win32" and _is_wsl_unc(config_dir)):
        runner_path = runner if runner is not None else stable_runner_path(state_dir_path(), platform)
        if provider == CODEX_PROVIDER:
            return {"type": "command", "command": f'"{runner_path}" --sessions-dir "{sessions_dir}"'}
        return {
            "type": "command",
            "command": runner_path,
            "args": ["--sessions-dir", sessions_dir],
        }
    interpreter = "python" if _is_windows_local_path(config_dir) else "python3"
    return {
        "type": "command",
        "command": f'{interpreter} "{native}/tokitty/hook_writer.py" --sessions-dir "{sessions_dir}"',
    }


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


def _normalize_token_path(path: str) -> str:
    """Normalise a path token pulled out of a hook command for comparison.

    Unlike _normalize_home_path, a trailing separator is never stripped:
    the home is ours to normalise (it comes from accounts.json or the
    UI), but a script or sessions path out of an arbitrary hook command
    is exactly what tokitty wrote or it is not owned, and
    ".../hook_writer.py/" is not the file tokitty writes. Backslashes
    still become forward slashes, repeated slashes still collapse, and a
    drive-letter path is still case-folded -- those are equivalent
    spellings regardless of where the string came from.
    """
    normalized = re.sub(r"/+", "/", path.replace("\\", "/"))
    if _is_windows_local_path(normalized):
        normalized = normalized.casefold()
    return normalized


def _command_owned_from_parts(parts, expected_script: str, expected_sessions: str) -> bool:
    if len(parts) != 4:
        return False
    interpreter, script, flag, sessions_arg = parts
    if interpreter not in ("python", "python3") or flag != "--sessions-dir":
        return False
    return (
        _normalize_token_path(script) == expected_script
        and _normalize_token_path(sessions_arg) == expected_sessions
    )


def _is_owned_exec_hook(command: str, args, expected_sessions: str) -> bool:
    """Whether an exec-form hook ({"type": "command", "command": ...,
    "args": [...]}, spec Q2a) is tokitty's own frozen-build registration.

    Owned when command's basename (backslashes normalised to forward
    slashes first) case-folds to "tokitty-hook" or "tokitty-hook.exe" --
    the containing directory is deliberately never checked, since a
    legitimately-owned exec hook may point at the stable <state
    dir>/current link, at a fallback release-specific path, or at
    wherever an older build put it, and all of those are still tokitty's
    -- and args is exactly ["--sessions-dir", s] with s normalising to
    this home's tokitty/sessions.
    """
    basename = PurePosixPath(command.replace("\\", "/")).name
    owned_names = (HOOK_RUNNER_NAME.casefold(), f"{HOOK_RUNNER_NAME}.exe".casefold())
    if basename.casefold() not in owned_names:
        return False
    if not isinstance(args, list) or len(args) != 2:
        return False
    flag, sessions_arg = args
    if flag != "--sessions-dir" or not isinstance(sessions_arg, str):
        return False
    return _normalize_token_path(sessions_arg) == expected_sessions


def _codex_command_candidates(command: str) -> List[Tuple[str, str, str]]:
    """The (kind, runner, sessions) readings of a Codex command string.

    kind is "python" (interpreter, hook_writer.py, flag, sessions; runner is
    the script) or "runner" (a runner path, flag, sessions). Paths come back
    under _normalize_token_path. shlex's reading comes first, then a plain
    whitespace split when the command has no quote characters, for the
    historical unquoted shape on a drive-letter home that shlex mangles. A
    command that does not parse (an unbalanced quote) has no reading.
    """
    try:
        split_lists = [shlex.split(command, posix=True)]
    except ValueError:
        return []
    if '"' not in command and "'" not in command:
        split_lists.append(command.split())
    candidates = []
    for parts in split_lists:
        if len(parts) == 4 and parts[0] in ("python", "python3") and parts[2] == "--sessions-dir":
            candidates.append(("python", _normalize_token_path(parts[1]), _normalize_token_path(parts[3])))
        elif len(parts) == 3 and parts[1] == "--sessions-dir":
            candidates.append(("runner", _normalize_token_path(parts[0]), _normalize_token_path(parts[2])))
    return candidates


def _codex_owned_parts(command: str, config_dir: str) -> Optional[Tuple[str, str, str]]:
    """The (kind, runner, sessions) reading of command that is tokitty's
    own Codex registration for config_dir, or None if it is not ours."""
    home = _normalize_home_path(_wsl_native_path(config_dir))
    expected_script = f"{home}/tokitty/hook_writer.py"
    expected_sessions = f"{home}/tokitty/sessions"
    owned_names = (HOOK_RUNNER_NAME.casefold(), f"{HOOK_RUNNER_NAME}.exe".casefold())
    for kind, runner, sessions in _codex_command_candidates(command):
        if sessions != expected_sessions:
            continue
        if kind == "python":
            if runner == expected_script:
                return kind, runner, sessions
        elif PurePosixPath(runner).name.casefold() in owned_names:
            return kind, runner, sessions
    return None


def _is_owned_hook(hook, config_dir: str, provider: str = DEFAULT_PROVIDER) -> bool:
    """Whether hook is the exact command tokitty writes for config_dir.

    Ownership is yes-or-no only. Two shapes are recognised: the
    interpreter-string form ("command" splits into exactly an
    interpreter, the hook_writer.py path, "--sessions-dir", and the
    sessions path, both normalising to this home's tokitty/hook_writer.py
    and tokitty/sessions) and the exec form a frozen build registers
    ("command" is a tokitty-hook[.exe] path and "args" is exactly
    ["--sessions-dir", s] normalising to this home's tokitty/sessions --
    see _is_owned_exec_hook). A hook with an "args" key is only ever
    checked against the exec shape, and one without is only ever checked
    against the interpreter-string shape -- the two never cross-match. An
    equivalent spelling (quoting, a doubled slash, a differently-cased
    drive letter) is still owned; a hook aimed at another home, one that
    merely mentions tokitty, one with an unbalanced quote, or one whose
    script/sessions token carries a trailing separator tokitty never
    wrote, is not.

    The interpreter-string split is tried two ways. shlex (posix mode)
    handles the quoted form and the historical unquoted POSIX form
    (9bab1b3). It does not handle the historical unquoted form on a
    drive-letter home: shlex reads the backslashes in "C:\\Users\\..." as
    escape characters and mangles the path. A raised ValueError (an
    unbalanced quote) means the command does not parse as anything
    tokitty wrote, full stop -- no fallback. When shlex's split parses
    but is not an owned match, a plain whitespace split is tried too, but
    only when the command has no quote characters at all: the whitespace
    fallback exists solely for the historical unquoted shape, which
    never had quotes, so any quote in the command means that shape is
    not what this is.

    Codex never gets the exec form, so for provider "codex" a hook with an
    "args" key is not owned, and the owned shapes are two single strings:
    the Python string above, and a tokitty-hook[.exe] path (only its
    basename is checked, as for the exec form) followed by "--sessions-dir"
    and this home's sessions path. See _codex_owned_parts.
    """
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    if provider == CODEX_PROVIDER:
        return "args" not in hook and _codex_owned_parts(command, config_dir) is not None
    home = _normalize_home_path(_wsl_native_path(config_dir))
    expected_script = f"{home}/tokitty/hook_writer.py"
    expected_sessions = f"{home}/tokitty/sessions"

    if "args" in hook:
        return _is_owned_exec_hook(command, hook.get("args"), expected_sessions)

    try:
        shlex_parts = shlex.split(command, posix=True)
    except ValueError:
        return False
    if _command_owned_from_parts(shlex_parts, expected_script, expected_sessions):
        return True

    if '"' in command or "'" in command:
        return False

    whitespace_parts = command.split()
    return _command_owned_from_parts(whitespace_parts, expected_script, expected_sessions)


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
    def __init__(
        self,
        config_dir: str,
        ok: bool,
        message: str,
        installed_events: Optional[List[str]] = None,
        warning: Optional[str] = None,
        refreshed_events: Optional[List[str]] = None,
        note: Optional[str] = None,
    ):
        self.config_dir = config_dir
        self.ok = ok
        self.message = message
        self.installed_events = installed_events or []
        self.warning = warning
        self.refreshed_events = refreshed_events or []
        # A soft, informational note distinct from `warning` (reserved for
        # the link-fallback case, spec Q2a) -- e.g. a settings.local.json
        # entry that owns an event but differs from what tokitty would
        # write today. Never blocks anything; the CLI prints it plainly.
        self.note = note


def _collect_owned_positions(entries, config_dir: str, provider: str) -> List[Tuple[int, int]]:
    """[(entry_index, hook_index), ...] for every owned hook in entries, in
    document order. entries may be missing or malformed; both yield []."""
    positions: List[Tuple[int, int]] = []
    if not isinstance(entries, list):
        return positions
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        hooks_list = entry.get("hooks")
        if not isinstance(hooks_list, list):
            continue
        for hook_index, hook in enumerate(hooks_list):
            if _is_owned_hook(hook, config_dir, provider):
                positions.append((entry_index, hook_index))
    return positions


def _rebuild_entries(entries, replace_pos=None, replacement=None, remove_positions=()):
    """A new entries list built from entries: the hook at replace_pos (if
    given) becomes replacement, every hook at a position in
    remove_positions is dropped, and an entry left with no hooks at all is
    dropped entirely. Everything else -- an entry's other keys, sibling
    handlers, document order -- is unchanged, and an entry untouched by
    either operation keeps its original object identity."""
    remove_set = set(remove_positions)
    new_entries = []
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            new_entries.append(entry)
            continue
        hooks_list = entry.get("hooks")
        if not isinstance(hooks_list, list):
            new_entries.append(entry)
            continue
        new_hooks = []
        touched = False
        for hook_index, hook in enumerate(hooks_list):
            pos = (entry_index, hook_index)
            if pos in remove_set:
                touched = True
                continue
            if pos == replace_pos:
                new_hooks.append(replacement)
                touched = True
            else:
                new_hooks.append(hook)
        if not touched:
            new_entries.append(entry)
        elif new_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = new_hooks
            new_entries.append(new_entry)
        # else: every hook in this entry was removed, so the entry is dropped.
    return new_entries


def _normalized_args(args):
    return [_normalize_token_path(v) if isinstance(v, str) else v for v in args]


def _codex_effective_desired(
    old_handler: dict, desired_handler: dict, *, refresh: bool, config_dir: Optional[str] = None
) -> dict:
    """What a Codex refresh should actually aim old_handler at.

    A refresh never demotes the runner string to the Python string (a
    source launch must not flip a frozen install's command), but it still
    repairs a wrong or missing timeout. So when old is runner kind and
    desired is Python kind, the target is the old command with desired's
    timeout. Anything else aims at desired as given.
    """
    if not refresh:
        return desired_handler
    old = _codex_parts_for_compare(old_handler, config_dir)
    new = _codex_parts_for_compare(desired_handler, config_dir)
    if old is None or new is None or old[0] != "runner" or new[0] != "python":
        return desired_handler
    effective = {k: v for k, v in desired_handler.items() if k != "timeout"}
    effective["command"] = old_handler["command"]
    if "timeout" in desired_handler:
        effective["timeout"] = desired_handler["timeout"]
    return effective


def _codex_parts_for_compare(handler: dict, config_dir: Optional[str]) -> Optional[Tuple[str, str, str]]:
    command = handler.get("command")
    if not isinstance(command, str) or "args" in handler:
        return None
    if config_dir is not None:
        return _codex_owned_parts(command, config_dir)
    candidates = _codex_command_candidates(command)
    return candidates[0] if candidates else None


def _codex_handler_needs_rewrite(
    old_handler: dict, desired_handler: dict, *, refresh: bool = False, config_dir: Optional[str] = None
) -> bool:
    """Codex's rewrite rule. Both shapes are strings, so each command is
    parsed into (kind, runner, sessions) with the interpreter name ignored
    and paths normalised; a difference in any of the three, or in
    "timeout", is a rewrite, because Codex hashes both. A handler that
    does not parse is rewritten.
    """
    desired_handler = _codex_effective_desired(
        old_handler, desired_handler, refresh=refresh, config_dir=config_dir
    )
    old = _codex_parts_for_compare(old_handler, config_dir)
    new = _codex_parts_for_compare(desired_handler, config_dir)
    if old is None or new is None:
        return True
    return old != new or old_handler.get("timeout") != desired_handler.get("timeout")


def _handler_needs_rewrite(
    old_handler: dict,
    desired_handler: dict,
    *,
    refresh: bool = False,
    provider: str = DEFAULT_PROVIDER,
    config_dir: Optional[str] = None,
) -> bool:
    """Whether an already-owned handler must be rewritten to match desired.

    Codex is dispatched to _codex_handler_needs_rewrite: the rules below are
    Claude's.

    refresh (the startup path, add_missing=False) never demotes an owned
    exec-form handler back to the interpreter-string shape: a source
    (non-frozen) launch's own startup refresh must leave a frozen
    install's hooks alone, or a machine that launches both forms
    flip-flops the registered command on every launch. An explicit
    install (add_missing=True) still performs that demotion, same as
    before -- the one-way python -> exec promotion a frozen refresh makes
    is untouched by this guard, since that direction isn't a demotion.

    Both lacking "args" (the interpreter-string shape) is never a
    rewrite: old_handler is already confirmed owned, and _is_owned_hook's
    string match already accepts an equivalent spelling (quoting,
    ``python`` vs ``python3``), so rewriting here would only change the
    string Codex hashes for no semantic difference. Otherwise a match
    needs the same "command" after _normalize_token_path (an
    equivalently-spelled path -- a doubled slash, a differently-cased
    drive letter -- is not a rewrite either) and, for "args", each
    element equal after the same normalisation.
    """
    if provider == CODEX_PROVIDER:
        return _codex_handler_needs_rewrite(
            old_handler, desired_handler, refresh=refresh, config_dir=config_dir
        )
    if refresh and "args" in old_handler and "args" not in desired_handler:
        return False
    if "args" not in old_handler and "args" not in desired_handler:
        return False
    old_command = old_handler.get("command")
    desired_command = desired_handler.get("command")
    if isinstance(old_command, str) and isinstance(desired_command, str):
        commands_match = _normalize_token_path(old_command) == _normalize_token_path(desired_command)
    else:
        commands_match = old_command == desired_command
    if not commands_match:
        return True
    old_args = old_handler.get("args")
    desired_args = desired_handler.get("args")
    if (old_args is None) != (desired_args is None):
        return True
    if old_args is None:
        return False
    if not isinstance(old_args, list) or not isinstance(desired_args, list):
        return old_args != desired_args
    if len(old_args) != len(desired_args):
        return True
    return _normalized_args(old_args) != _normalized_args(desired_args)


def _merge_handler(old_handler: dict, desired_handler: dict) -> dict:
    """Rewrite old_handler toward desired_handler in place: type/command/
    args are overwritten, while any other key old_handler carried (e.g. a
    "timeout") survives."""
    merged = dict(old_handler)
    merged.pop("args", None)
    merged.update(desired_handler)
    return merged


def _record_codex_trust(config_dir: str, changes: List[Tuple[str, int, int]]) -> None:
    """Hook point for the Codex trust record. changes lists (event,
    group_index, handler_index) for each owned Codex handler a reconcile is
    about to add, rewrite or move, at its final position. Called before
    hooks.json is written. Does nothing yet."""


def _load_reconcile_state(base: Path, target: HookTarget):
    """Load and validate config_dir's settings for reconcile.

    Returns (data, local_hooks, problem). problem is None on success, or
    a (kind, detail) pair: kind is "parse" for a JSON parse failure in
    either file, or "shape" for valid JSON that isn't the shape reconcile
    needs (settings.json's root isn't an object, its "hooks" key is
    present but isn't an object -- this also catches "hooks": null,
    which used to reach a bare AttributeError further down -- or a
    per-event value isn't a list). The caller decides what a problem
    means: an explicit install always aborts on either kind; a refresh
    aborts only on "shape" and quietly skips on "parse".
    settings.local.json's shape is deliberately not
    validated this strictly: it's read-only, and a malformed local file
    should never block reconciling the file tokitty actually writes.
    """
    settings_path = base / target.settings_file
    data, error = _load_settings(settings_path)
    if error:
        return None, None, ("parse", f"could not parse {target.settings_file}: {error}")
    if not isinstance(data, dict):
        return None, None, ("shape", f"{target.settings_file} is not an object: {data!r}")

    local_data: dict = {}
    if target.local_settings_file is not None:
        local_data, local_error = _load_settings(base / target.local_settings_file)
        if local_error:
            return None, None, ("parse", f"could not parse {target.local_settings_file}: {local_error}")

    existing_hooks = data.get("hooks")
    if "hooks" in data and not isinstance(existing_hooks, dict):
        return None, None, ("shape", f"{target.settings_file} 'hooks' key is not an object: {existing_hooks!r}")
    for event, _matcher in target.events:
        entries = existing_hooks.get(event) if existing_hooks else None
        if entries is not None and not isinstance(entries, list):
            return None, None, ("shape", f"{target.settings_file} 'hooks.{event}' is not a list: {entries!r}")

    local_hooks = local_data.get("hooks") if isinstance(local_data, dict) else None
    if not isinstance(local_hooks, dict):
        local_hooks = {}

    return data, local_hooks, None


def _reconcile_problem_result(config_dir: str, problem, add_missing: bool) -> ConfigDirResult:
    kind, detail = problem
    if kind == "parse" and not add_missing:
        # Noise, not harm: nothing is written either way, and this is
        # reached on every launch for a home Tokitty has never touched
        # whose settings.json (or settings.local.json) just happens not
        # to parse. An explicit install still fails loudly, below.
        return ConfigDirResult(config_dir, True, f"skipped, {detail}")
    return ConfigDirResult(config_dir, False, f"aborted, {detail}")


def _runner_token(handler: dict, provider: str, config_dir: str) -> Optional[str]:
    """The runner path a handler launches, or None if it launches the
    Python interpreter. Claude's exec form keeps it in "command"; Codex's
    runner string is parsed (so the token comes back normalised)."""
    command = handler.get("command")
    if provider == CODEX_PROVIDER:
        parts = _codex_owned_parts(command, config_dir) if isinstance(command, str) else None
        return parts[1] if parts is not None and parts[0] == "runner" else None
    return command


def _find_handler(entries, handler) -> Tuple[int, int]:
    """(entry_index, hook_index) of the handler object itself in entries."""
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            continue
        for hook_index, candidate in enumerate(entry["hooks"]):
            if candidate is handler:
                return entry_index, hook_index
    raise LookupError("handler not found")


def _reconcile_hooks(config_dir: str, provider: str, add_missing: bool) -> ConfigDirResult:
    """Bring config_dir's hooks in line with what tokitty would write
    today, adding a missing handler only when add_missing is true. Driven
    by the provider's HookTarget, so it serves Claude (settings.json) and
    Codex (hooks.json) alike. Shared by install_hooks_for_dir
    (add_missing=True) and refresh_hooks_for_dir (add_missing=False, the
    startup refresh)."""
    target = _hook_target(provider)
    base = Path(_local_config_path(config_dir))
    settings_path = base / target.settings_file

    data, local_hooks, problem = _load_reconcile_state(base, target)
    if problem is not None:
        return _reconcile_problem_result(config_dir, problem, add_missing)

    any_owned = any(
        _collect_owned_positions(local_hooks.get(event), config_dir, provider)
        or _collect_owned_positions((data.get("hooks") or {}).get(event), config_dir, provider)
        for event, _matcher in target.events
    )
    if not add_missing and not any_owned:
        # A refresh on a home tokitty has never touched: no copy, no
        # mkdir, no link -- there is nothing here to bring current.
        return ConfigDirResult(config_dir, True, "nothing to refresh", installed_events=[], refreshed_events=[])

    skeleton = _build_command(config_dir, provider=provider)
    # The link work below applies wherever the skeleton launches the
    # runner: Claude's exec form or Codex's frozen runner string.
    if provider == CODEX_PROVIDER:
        is_exec = _runner_token(skeleton, provider, config_dir) is not None
    else:
        is_exec = "args" in skeleton
    warning = None
    healthy_runner = None
    stable = None
    fallback_bundled = None

    if is_exec:
        # Exec form (a frozen build, not the WSL-from-Windows row): the
        # link has to be made, or the fallback below resolved, before
        # anything is written. Lazy import: hooks_install and runner_link
        # import each other (runner_link needs hook_runner_path/
        # stable_runner_path back).
        from tokitty.runner_link import ensure_runner_link

        try:
            outcome = ensure_runner_link(state_dir_path())
        except AppTranslocatedError:
            return ConfigDirResult(config_dir, False, MOVE_TO_APPLICATIONS)
        except FileNotFoundError:
            # This release has no bundled tokitty-hook at all -- a broken
            # build, not a transient lock/repoint problem. Falling back
            # would register a command pointing at a file that doesn't
            # exist, so this aborts instead of warning.
            return ConfigDirResult(config_dir, False, "this copy of Tokitty has no tokitty-hook next to it")
        except OSError as exc:
            outcome = None
            reason = str(exc)
        else:
            reason = outcome.note

        platform = sys.platform
        stable = stable_runner_path(state_dir_path(), platform)
        if outcome is not None and not reason:
            healthy_runner = stable
        else:
            # Real directory at "current", a failed repoint, or a lock
            # timeout: never silent (spec Q2a addendum). Decided per
            # event below, not once for the whole home -- an event
            # that's never had a stable-path registration must not be
            # pointed at a link that isn't working right now just
            # because some *other* event already was.
            fallback_bundled = hook_runner_path(os.path.realpath(sys.executable), platform)
            warning = LINK_FALLBACK_WARNING.format(reason=reason)

        # ensure_runner_link above can block up to 5s on current.lock. Re-read
        # settings fresh right before deciding what to write, so a concurrent
        # uninstall (or account removal) landing during that wait is never
        # overwritten by a snapshot taken before it happened. A refresh
        # naturally does nothing for an event the fresh read no longer shows
        # as owned (the "no owned handler, not add_missing" branch below), so
        # no separate re-check of any_owned is needed here.
        data, local_hooks, problem = _load_reconcile_state(base, target)
        if problem is not None:
            return _reconcile_problem_result(config_dir, problem, add_missing)

    refresh = not add_missing

    timeouts = dict(target.timeouts)

    def with_timeout(handler, event):
        handler = dict(handler)
        if event in timeouts:
            handler["timeout"] = timeouts[event]
        return handler

    def desired_for(event, existing_command):
        if not is_exec:
            return with_timeout(skeleton, event)
        if healthy_runner is not None:
            runner = healthy_runner
        elif (
            isinstance(existing_command, str)
            and _normalize_token_path(existing_command) == _normalize_token_path(stable)
        ):
            runner = stable
        else:
            runner = fallback_bundled
        return with_timeout(_build_command(config_dir, runner=runner, provider=provider), event)

    def needs_rewrite(handler, desired):
        return _handler_needs_rewrite(
            handler, desired, refresh=refresh, provider=provider, config_dir=config_dir
        )

    def choose_primary(event, main_entries, main_positions):
        # When an event has more than one owned handler, a stable-path
        # handler is checked FIRST -- it must never be dropped in favour
        # of a release-path handler just because that release-path
        # handler happens to already match what fallback mode would
        # write for it today. Only once no stable-path handler is
        # present do we fall back to whichever already matches desired,
        # then the first found.
        if len(main_positions) == 1:
            return main_positions[0]
        for pos in main_positions:
            handler = main_entries[pos[0]]["hooks"][pos[1]]
            command = _runner_token(handler, provider, config_dir)
            if (
                stable is not None
                and isinstance(command, str)
                and _normalize_token_path(command) == _normalize_token_path(stable)
            ):
                return pos
        for pos in main_positions:
            handler = main_entries[pos[0]]["hooks"][pos[1]]
            if not needs_rewrite(handler, desired_for(event, _runner_token(handler, provider, config_dir))):
                return pos
        return main_positions[0]

    hooks_dest = base / "tokitty" / "hook_writer.py"
    hooks_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_HOOK_WRITER_SOURCE, hooks_dest)

    hooks_dict = data.setdefault("hooks", {})

    installed_events: List[str] = []
    refreshed_events: List[str] = []
    stale_local_events: List[str] = []
    changed = False
    # Codex only: (event, group, handler) at the final position of every
    # owned handler added, rewritten or moved, and whether any was
    # rewritten or moved rather than just added.
    trust_changes: List[Tuple[str, int, int]] = []
    reapproval = False

    for event, matcher in target.events:
        local_entries = local_hooks.get(event)
        local_positions = _collect_owned_positions(local_entries, config_dir, provider)

        main_entries = hooks_dict.get(event)
        if not isinstance(main_entries, list):
            main_entries = []
        main_positions = _collect_owned_positions(main_entries, config_dir, provider)

        if local_positions:
            # settings.local.json already owns this event: it counts as
            # installed, the local file is never written, and any owned
            # copy left in settings.json is removed so the event can't
            # fire twice.
            local_handler = local_entries[local_positions[0][0]]["hooks"][local_positions[0][1]]
            if needs_rewrite(local_handler, desired_for(event, _runner_token(local_handler, provider, config_dir))):
                stale_local_events.append(event)
            if main_positions:
                new_entries = _rebuild_entries(main_entries, remove_positions=main_positions)
                if new_entries:
                    hooks_dict[event] = new_entries
                else:
                    hooks_dict.pop(event, None)
                changed = True
                refreshed_events.append(event)
            continue

        if not main_positions:
            if add_missing:
                desired = desired_for(event, None)
                group = {"hooks": [dict(desired)]}
                if matcher is not None:
                    group = {"matcher": matcher, **group}
                trust_changes.append((event, len(main_entries), 0))
                hooks_dict[event] = main_entries + [group]
                installed_events.append(event)
                changed = True
            # else: a refresh never adds a handler for an event nothing owns.
            continue

        primary_pos = choose_primary(event, main_entries, main_positions)
        extra_positions = [pos for pos in main_positions if pos != primary_pos]
        primary_handler = main_entries[primary_pos[0]]["hooks"][primary_pos[1]]
        desired = desired_for(event, _runner_token(primary_handler, provider, config_dir))
        rewrite = needs_rewrite(primary_handler, desired)

        if rewrite:
            if provider == CODEX_PROVIDER:
                desired = _codex_effective_desired(
                    primary_handler, desired, refresh=refresh, config_dir=config_dir
                )
            replacement = _merge_handler(primary_handler, desired)
            if provider == CODEX_PROVIDER and "timeout" not in desired:
                replacement.pop("timeout", None)
            kept = replacement
        elif extra_positions:
            # Already matches; only duplicate owned handlers to collapse.
            replacement = None
            kept = primary_handler
        else:
            continue

        new_entries = _rebuild_entries(
            main_entries,
            replace_pos=primary_pos if rewrite else None,
            replacement=replacement,
            remove_positions=extra_positions,
        )
        hooks_dict[event] = new_entries
        changed = True
        refreshed_events.append(event)
        if provider == CODEX_PROVIDER:
            final_pos = _find_handler(new_entries, kept)
            if rewrite or final_pos != primary_pos:
                # A handler that only moved is reviewed again too: Codex
                # looks its old hash up at the new position.
                trust_changes.append((event, final_pos[0], final_pos[1]))
                reapproval = True

    if changed:
        if provider == CODEX_PROVIDER:
            _record_codex_trust(config_dir, trust_changes)
        _backup(settings_path)
        _write_settings(settings_path, data)

    if installed_events or refreshed_events:
        parts = []
        if installed_events:
            parts.append("installed")
        if refreshed_events:
            parts.append("refreshed")
        msg = " and ".join(parts)
    else:
        msg = "already installed, nothing to do"

    note = None
    if stale_local_events:
        note = (
            "a locally-owned hook differs from what Tokitty would write for: "
            + ", ".join(stale_local_events)
        )

    if reapproval:
        warning = f"{warning} {CODEX_REAPPROVE_WARNING}" if warning else CODEX_REAPPROVE_WARNING

    return ConfigDirResult(
        config_dir,
        True,
        msg,
        installed_events=installed_events,
        refreshed_events=refreshed_events,
        warning=warning,
        note=note,
    )


_RECONCILE_TABLE = {"claude": _reconcile_hooks, CODEX_PROVIDER: _reconcile_hooks}


def _reconcile(config_dir: str, provider: str, add_missing: bool) -> ConfigDirResult:
    key = provider or DEFAULT_PROVIDER
    fn = _RECONCILE_TABLE.get(key)
    if fn is None:
        raise ValueError(f"no hook reconciler registered for provider {key!r}")
    return fn(config_dir, provider, add_missing)


def install_hooks_for_dir(config_dir: str, provider: str = DEFAULT_PROVIDER) -> ConfigDirResult:
    return _reconcile(config_dir, provider, add_missing=True)


def refresh_hooks_for_dir(config_dir: str, provider: str = DEFAULT_PROVIDER) -> ConfigDirResult:
    """Bring config_dir's hooks current without ever adding a missing one --
    the startup refresh (ensure_current) and every other path that must
    never turn a partial or uninstalled home into an installed one."""
    return _reconcile(config_dir, provider, add_missing=False)


def ensure_current(state_dir: Optional[Path] = None, refresh_fn=None) -> List[ConfigDirResult]:
    """Refresh every explicitly-configured hook-enabled account's
    registration in place, called from run_discovery on every launch so a
    stale owned handler (an old release path, a spelling a past version
    wrote) gets corrected without ever installing hooks into a home that
    never had them.

    Deliberately does not call get_config_dirs: its fallback to
    _default_config_dir() when accounts.json is absent is right for an
    explicit --install-hooks/Accounts-dialog call, but wrong for a call
    that fires on every single launch -- on Windows that fallback shells
    into every WSL distro looking for credentials, a regression of issue
    #52's WslCredentialsCache. With no accounts.json, or nothing usable
    in it, there is by definition no account the user has explicitly
    asked tokitty to watch, so this does nothing at all: no default-dir
    resolution, no WSL probe.

    refresh_fn defaults to refresh_hooks_for_dir, looked up fresh on each
    call rather than bound as a default argument, so a test can
    monkeypatch the module attribute instead of passing it explicitly. A
    pair whose provider has no reconcile branch is skipped -- today
    every hook-enabled provider is in _RECONCILE_TABLE, but this keeps a
    future mismatch (a provider gaining hooks before its own reconcile
    branch lands) from raising instead of just doing nothing for that
    pair. Any exception a reconcile call raises (not just OSError)
    becomes a failed result for that account instead of aborting every
    account after it.
    """
    resolved_state_dir = state_dir if state_dir is not None else get_state_dir()
    pairs = _config_dirs_from_accounts_file(resolved_state_dir)
    if pairs is None:
        return []
    results = []
    for config_dir, provider in pairs:
        if (provider or DEFAULT_PROVIDER) not in _RECONCILE_TABLE:
            continue
        fn = refresh_fn if refresh_fn is not None else refresh_hooks_for_dir
        try:
            results.append(fn(config_dir, provider))
        except Exception as exc:
            results.append(ConfigDirResult(config_dir, False, str(exc)))
    return results


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
        new_entries = []
        event_changed = False
        for entry in entries:
            if not isinstance(entry, dict):
                new_entries.append(entry)
                continue
            entry_hooks = entry.get("hooks")
            if not isinstance(entry_hooks, list):
                new_entries.append(entry)
                continue
            kept_hooks = [h for h in entry_hooks if not _is_owned_hook(h, config_dir, provider)]
            if len(kept_hooks) == len(entry_hooks):
                new_entries.append(entry)
                continue
            event_changed = True
            if kept_hooks:
                kept_entry = dict(entry)
                kept_entry["hooks"] = kept_hooks
                new_entries.append(kept_entry)
            # else: every handler in this entry was tokitty's, drop the entry.
        if event_changed:
            removed.append(event)
            if new_entries:
                hooks[event] = new_entries
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


def _pending_dir_has_hooks(state_dir: Path, config_dir: str, op: str) -> bool:
    """Whether a legacy pending op's dir is one that gets hooks. Records
    written by this build carry their provider and never get here.

    The account's own provider decides when it is still in accounts.json.
    A removed account is gone from there, so its dir is judged by what is
    in it; this runs off the Tk thread, like the hook op it gates.

    An uninstall and an install are judged differently once the account
    is gone. Replaying an uninstall just removes tokitty's own hook
    entries, so it fails open the way it always has: not clearly a Codex
    home is enough. Replaying an install writes a Claude settings.json,
    so it fails closed instead -- only affirmative Claude evidence
    (settings.json, projects/, or .credentials.json) earns the replay.
    Otherwise the dir could be a former Codex home whose rollout dirs
    were deleted, and looks_like_codex_home alone can't tell that from a
    directory with no markers at all.
    """
    from tokitty.manual_path import looks_like_claude_home, looks_like_codex_home

    matched = _pending_dir_matched_provider(state_dir, config_dir)
    if matched is not None:
        return provider_has_hooks(matched)
    local_path = _local_config_path(config_dir)
    if op == "install":
        try:
            return looks_like_claude_home(Path(local_path))
        except OSError:
            return False
    return not looks_like_codex_home(local_path)


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
        has_hooks = _pending_dir_has_hooks(state_dir, pending["config_dir"], pending["op"])
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
        if result.refreshed_events:
            print(f"{config_dir}: refreshed hooks for {', '.join(result.refreshed_events)}")
        if not result.installed_events and not result.refreshed_events:
            print(f"{config_dir}: {result.message}")
        if result.note:
            print(f"{config_dir}: {result.note}")
        if result.warning:
            print(f"{config_dir}: warning: {result.warning}", file=sys.stderr)
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
        if result.warning:
            print(f"{config_dir}: warning: {result.warning}", file=sys.stderr)
    print("The copied hook_writer.py and sessions state files were left in place; "
          "delete <config-dir>/tokitty/ manually if you want them gone.")
    return 1 if any_failed else 0
