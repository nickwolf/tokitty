"""Whether Codex has approved Tokitty's hooks, read from Codex's config.toml.

Codex skips a hook until the user approves it in its own review, and records
the approval as [hooks.state.'<hooks.json path>:<event>:<group>:<handler>']
with a trusted_hash in config.toml. Tokitty never writes that file and never
computes the hash. It only reads it, and keeps a small record of what the
hash under each of its keys was when Tokitty last wrote a handler there, so
a stale approval (left over from a command Tokitty has since rewritten) is
not mistaken for a current one. See the codex-activity-hooks design, section 4.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from tokitty import hooks_install as hi

RECORD_FILENAME = "codex_hook_trust.json"

NEEDS_APPROVAL = "needs_approval"
APPROVED = "approved"
UNREADABLE = "unreadable"

# Codex's own snake case for each event Tokitty installs, as it appears in a
# trust key.
EVENT_SNAKE = {
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "PermissionRequest": "permission_request",
    "Stop": "stop",
    "SubagentStop": "subagent_stop",
    "Interrupt": "interrupt",
    "SessionEnd": "session_end",
}

_SINGLE_HEADER = re.compile(r"""^\[hooks\.state\.'([^']*)'\]\s*(?:#.*)?$""")
_DOUBLE_HEADER = re.compile(r'''^\[hooks\.state\."((?:[^"\\]|\\.)*)"\]\s*(?:#.*)?$''')
_HASH_LINE = re.compile(r'''^trusted_hash\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)')\s*(?:#.*)?$''')


def _tomllib():
    """The tomllib module, or None on Python 3.10. A function so a test can
    force the fallback parser."""
    try:
        import tomllib
    except ImportError:
        return None
    return tomllib


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _parse_lines(text: str) -> Dict[str, str]:
    """Narrow fallback for Python 3.10: only [hooks.state.'...'] and
    [hooks.state."..."] headers and the trusted_hash line under each."""
    found: Dict[str, str] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            single = _SINGLE_HEADER.match(line)
            double = _DOUBLE_HEADER.match(line)
            if single:
                current = single.group(1)
            elif double:
                current = _unescape(double.group(1))
            else:
                current = None
            continue
        if current is not None:
            match = _HASH_LINE.match(line)
            if match:
                value = match.group(1)
                found[current] = _unescape(value) if value is not None else match.group(2)
    return found


def read_trusted_hashes(config_toml_path) -> Optional[Dict[str, str]]:
    """Every [hooks.state.'<key>'] table's trusted_hash, keyed by the key
    as written. A missing file gives {}; an unreadable or unparseable one
    gives None. Never raises."""
    try:
        raw = Path(config_toml_path).read_bytes()
    except FileNotFoundError:
        return {}
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    module = _tomllib()
    if module is None:
        return _parse_lines(text)
    try:
        data = module.loads(text)
    except Exception:
        return None
    states = data.get("hooks", {}).get("state", {}) if isinstance(data.get("hooks"), dict) else {}
    if not isinstance(states, dict):
        return {}
    found: Dict[str, str] = {}
    for key, table in states.items():
        if isinstance(table, dict) and isinstance(table.get("trusted_hash"), str):
            found[key] = table["trusted_hash"]
    return found


def home_key(config_dir: str) -> str:
    """The normalised Codex-visible hooks.json path: the record's key for a
    home, and the path part of every trust key Tokitty looks up."""
    return hi._normalize_home_path(hi.codex_paths(config_dir)[1])


def trust_key(config_dir: str, event: str, group_index: int, handler_index: int) -> str:
    return f"{home_key(config_dir)}:{EVENT_SNAKE[event]}:{group_index}:{handler_index}"


def config_toml_path(config_dir: str) -> Path:
    return Path(hi._local_config_path(config_dir)) / "config.toml"


def _normalise_key(key: str) -> str:
    """A config.toml trust key with its path part normalised the way
    home_key is. rsplit, because a drive-letter path has its own colon."""
    parts = key.rsplit(":", 3)
    if len(parts) != 4:
        return key
    path, event, group, handler = parts
    return f"{hi._normalize_home_path(path)}:{event}:{group}:{handler}"


def hashes_for_home(config_dir: str) -> Optional[Dict[str, str]]:
    """config.toml's trusted hashes with keys normalised to trust_key's
    spelling, or None if the file can't be read."""
    raw = read_trusted_hashes(config_toml_path(config_dir))
    if raw is None:
        return None
    return {_normalise_key(key): value for key, value in raw.items()}


def record_path(state_dir) -> Path:
    return Path(state_dir) / RECORD_FILENAME


def load_record(state_dir) -> Dict[str, dict]:
    """The whole record, or {} when it is missing or not what we wrote."""
    try:
        data = json.loads(record_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_record(state_dir, data: Dict[str, dict]) -> None:
    """Atomic: a temp file in the same directory, then os.replace."""
    path = record_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def record_changes(
    state_dir, config_dir: str, changes: List[Tuple[str, int, int]]
) -> None:
    """Record, for each (event, group, handler) in changes, the trusted_hash
    currently under that final key (None if there is none), and stamp
    written_at. Keys for handlers not in changes keep their recorded value.
    Raises OSError if the record can't be written. An unreadable config.toml
    is not an error: nothing new is recorded then, since there is no hash to
    record, and the status reads "unreadable" until it can be read."""
    if not changes:
        return
    hashes = hashes_for_home(config_dir)
    if hashes is None:
        return
    data = load_record(state_dir)
    entry = data.get(home_key(config_dir))
    keys = dict(entry.get("keys", {})) if isinstance(entry, dict) and isinstance(entry.get("keys"), dict) else {}
    for event, group_index, handler_index in changes:
        key = trust_key(config_dir, event, group_index, handler_index)
        keys[key] = hashes.get(key)
    data[home_key(config_dir)] = {"written_at": time.time(), "keys": keys}
    _write_record(state_dir, data)


def forget_home(state_dir, config_dir: str) -> None:
    """Drop the home's record. Raises OSError if the record can't be written."""
    data = load_record(state_dir)
    if data.pop(home_key(config_dir), None) is None:
        return
    _write_record(state_dir, data)


def _distro_gate_blocks(config_dir: str, list_running_distros_fn: Optional[Callable[[], List[str]]]) -> bool:
    """True when config_dir is a WSL home seen from Windows whose distro is
    not running. Opening its UNC path would start the distro."""
    if sys.platform != "win32" or not hi._is_wsl_unc(config_dir):
        return False
    return not hi.distro_is_running(config_dir, list_running_distros_fn)


def _owned_handler_keys(config_dir: str) -> Optional[List[str]]:
    """Trust keys of Tokitty's handlers in hooks.json, or None when hooks.json
    can't be read as an object of event lists. [] means none installed."""
    data, error = hi._load_settings(Path(hi.codex_paths(config_dir)[0]))
    if error or not isinstance(data, dict):
        return None
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    keys: List[str] = []
    for event, _matcher in hi.CODEX_EVENTS:
        for group_index, handler_index in hi._collect_owned_positions(
            hooks.get(event), config_dir, hi.CODEX_PROVIDER
        ):
            keys.append(trust_key(config_dir, event, group_index, handler_index))
    return keys


def codex_hook_status(
    config_dir: str, state_dir, list_running_distros_fn: Optional[Callable[[], List[str]]] = None
) -> Optional[str]:
    """"needs_approval", "approved" or "unreadable"; None when no owned
    handler is installed (or the home is not reachable without starting a
    distro)."""
    if _distro_gate_blocks(config_dir, list_running_distros_fn):
        return None
    keys = _owned_handler_keys(config_dir)
    if not keys:
        return None
    hashes = hashes_for_home(config_dir)
    if hashes is None:
        return UNREADABLE
    entry = load_record(state_dir).get(home_key(config_dir))
    recorded = entry.get("keys") if isinstance(entry, dict) else None
    if not isinstance(recorded, dict):
        recorded = {}
    for key in keys:
        current = hashes.get(key)
        if current is None:
            return NEEDS_APPROVAL
        if key in recorded and recorded[key] == current:
            return NEEDS_APPROVAL
    return APPROVED


def codex_activity_hint(
    config_dir: str, state_dir, list_running_distros_fn: Optional[Callable[[], List[str]]] = None
) -> bool:
    """True when every handler reads as approved but the sessions directory
    has not changed since Tokitty last wrote a handler. A diagnostic hint
    only: it claims nothing about the cause."""
    if codex_hook_status(config_dir, state_dir, list_running_distros_fn) != APPROVED:
        return False
    entry = load_record(state_dir).get(home_key(config_dir))
    written_at = entry.get("written_at") if isinstance(entry, dict) else None
    if not isinstance(written_at, (int, float)):
        return False
    sessions = Path(hi._local_config_path(config_dir)) / "tokitty" / "sessions"
    try:
        return sessions.stat().st_mtime <= written_at
    except FileNotFoundError:
        return True
    except OSError:
        return False
