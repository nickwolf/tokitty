"""The Accounts tab's data side, toolkit-free: read the account store, query
each hook status, run an Install or Remove under the guard.

Split out of settings_accounts so the Qt Accounts tab can share it. Every
function here is meant for a worker thread. settings_accounts re-exports the
names; tests that fake hook_status_for_dir and friends patch this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from tokitty import hook_guard
from tokitty.accounts import load_accounts_result
from tokitty.hooks_install import (
    HookStatus, _default_config_dir, _same_config_dir, apply_hook_operation,
    hook_status_for_dir, retry_pending_hook_op,
)

DEFAULT_PROVIDER = "claude"
BUSY_MESSAGE = "Another account change is in progress"

# state -> (pill label, pill kind)
PILLS = {
    "installed": ("Installed", "good"),
    "outdated": ("Outdated", "warn"),
    "local_only": ("In local settings", "neutral"),
    "not_installed": ("Not installed", "neutral"),
    "awaiting_approval": ("Awaiting approval", "blue"),
    "unreachable": ("Unreachable", "bad"),
    "unsupported": ("Not supported", "neutral"),
    "error": ("Error", "bad"),
}
INSTALL_STATES = frozenset({"not_installed", "outdated", "error"})
REMOVE_STATES = frozenset({"installed", "outdated", "awaiting_approval"})
NO_BUTTON_STATES = frozenset({"unsupported", "unreachable", "local_only"})


def elide_middle(path: str, limit: int = 38) -> str:
    """Shorten a path from the middle, keeping the drive/root and the last
    two components: C:\\Users\\nickw\\…\\Claude\\personal."""
    if len(path) <= limit:
        return path
    parts = re.split(r"([\\/])", path)  # keeps the separators
    names = parts[0::2]
    seps = parts[1::2]
    if len(names) > 5:
        sep = seps[0] if seps else "\\"
        head = names[:3]
        tail = names[-2:]
        short = sep.join(head) + sep + "…" + sep + sep.join(tail)
        if len(short) < len(path):
            path = short
    if len(path) > limit:
        keep = (limit - 1) // 2
        path = path[:keep] + "…" + path[-keep:]
    return path


@dataclass(frozen=True)
class RowData:
    name: str
    provider: str
    config_dir: Optional[str]  # None: a Keychain-only account, read-only
    is_default: bool
    status: Optional[HookStatus]


@dataclass(frozen=True)
class Snapshot:
    rows: Tuple[RowData, ...]
    note: Optional[str] = None


def collect_rows(state_dir, distro_running: Optional[Callable[[str], bool]]) -> Snapshot:
    """Worker-thread: read the account store and query each hook status."""
    loaded = load_accounts_result(state_dir)
    entries: List[Tuple[str, str, Optional[str], bool]] = []
    note = None
    if loaded.state == "absent":
        # The default resolution may run WSL discovery, hence the worker.
        entries.append(("Default", DEFAULT_PROVIDER, _default_config_dir(), True))
    elif loaded.state == "malformed":
        note = "accounts.json could not be read."
    elif loaded.state == "valid_empty":
        note = "No accounts yet."
    else:
        for account in loaded.accounts:
            entries.append((account.name, account.provider, account.config_dir, False))
    rows = []
    for name, provider, config_dir, is_default in entries:
        if not config_dir:
            rows.append(RowData(name, provider, None, is_default, None))
            continue
        try:
            status = hook_status_for_dir(
                config_dir, provider, distro_running=distro_running, state_dir=Path(state_dir))
        except Exception as exc:
            status = HookStatus("error", str(exc) or exc.__class__.__name__)
        rows.append(RowData(name, provider, config_dir, is_default, status))
    return Snapshot(tuple(rows), note)


def _still_listed(state_dir, row: RowData) -> bool:
    loaded = load_accounts_result(state_dir)
    if row.is_default:
        return loaded.state == "absent"
    return any(a.provider == row.provider and _same_config_dir(a.config_dir, row.config_dir)
               for a in loaded.accounts)


def run_hook_op(state_dir, row: RowData, op: str):
    """Worker-thread: guard, revalidate, journalled op, release.
    Returns ("busy", None), ("gone", None) or ("done", HookOperationResult)."""
    token = hook_guard.try_acquire(Path(state_dir), f"settings-{op}")
    if token is None:
        return "busy", None
    try:
        if not _still_listed(state_dir, row):
            return "gone", None
        return "done", apply_hook_operation(Path(state_dir), row.config_dir, row.provider, op)
    finally:
        hook_guard.release(token)


def run_finish_pending(state_dir, running_distros):
    """Worker-thread: finish the journalled op under the guard."""
    token = hook_guard.try_acquire(Path(state_dir), "settings-finish")
    if token is None:
        return "busy", None
    try:
        return "done", retry_pending_hook_op(Path(state_dir), list_running_distros_fn=running_distros)
    finally:
        hook_guard.release(token)
