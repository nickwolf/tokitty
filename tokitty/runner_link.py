"""A stable path for the hook runner (spec Q2a, #48).

`<state dir>/current` is a directory junction on Windows or a symlink
elsewhere, repointed at the running release on every launch and before
`--install-hooks` registers anything. Claude Code hooks are then
registered against `<state dir>/current/tokitty-hook[.exe]`, a path that
never changes across releases or moves -- which matters because Codex
pins hooks by a hash of the command string.

Kept import-light on purpose: `hooks_install` needs `ensure_runner_link`
too, so it imports this module lazily to avoid a cycle (this module
imports `hooks_install` at the top instead).
"""
from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path
from typing import NamedTuple, Optional

from tokitty import hooks_install
from tokitty.frozen import AppTranslocatedError, is_translocated
from tokitty.lock import LockAcquisitionError, SingleInstanceLock

LINK_NAME = "current"
LOCK_NAME = "current.lock"

# Only present on Windows Python 3.8+; guarded so importing this module on
# POSIX (where stat has neither the tag nor st_reparse_tag) never raises.
_MOUNT_POINT_TAG = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)


class LinkOutcome(NamedTuple):
    runner: str
    note: Optional[str]


def _is_link(path) -> bool:
    """True for a symlink, or on Windows specifically for a directory
    junction (not any reparse point). False for a path that doesn't exist."""
    path = str(path)
    if sys.platform == "win32":
        try:
            st = os.lstat(path)
        except OSError:
            return False
        return getattr(st, "st_reparse_tag", None) == _MOUNT_POINT_TAG
    return os.path.islink(path)


def _repoint(link_str: str, release: str, existed: bool) -> None:
    """Point link_str at release, leaving the previous target intact if
    the platform call fails partway through.

    POSIX: symlink a sibling temp name, then atomically replace -- the
    real link is only ever touched by the replace, which either fully
    succeeds or leaves it untouched.

    Windows: junctions can't be replaced atomically, so the old target is
    remembered first; if creating the new junction raises, the old one is
    recreated before re-raising, so a failed repoint never leaves
    registered hooks pointing at nothing.
    """
    if sys.platform == "win32":
        import _winapi

        # realpath, not os.readlink: readlink returns the extended-length
        # \\?\-prefixed form for a junction, and CreateJunction fed that
        # back as a target creates a junction that doesn't resolve at all
        # (confirmed by direct reproduction) -- realpath's plain form works.
        old_target = os.path.realpath(link_str) if existed else None
        if existed:
            os.rmdir(link_str)
        try:
            _winapi.CreateJunction(release, link_str)
        except OSError:
            if old_target is not None:
                _winapi.CreateJunction(old_target, link_str)
            raise
    else:
        parent = os.path.dirname(link_str) or "."
        name = os.path.basename(link_str)
        tmp = os.path.join(parent, f".{name}.tmp-{os.getpid()}-{time.monotonic_ns()}")
        os.symlink(release, tmp, target_is_directory=True)
        os.replace(tmp, link_str)


def ensure_runner_link(
    state_dir,
    *,
    executable: Optional[str] = None,
    platform: Optional[str] = None,
    lock_timeout: float = 5.0,
) -> LinkOutcome:
    """Repoint `<state_dir>/current` at the running release and return the
    stable `tokitty-hook[.exe]` path hooks should be registered against.

    Raises AppTranslocatedError if the given or resolved executable is
    translocated, touching nothing. Raises FileNotFoundError if the
    release has no bundled tokitty-hook, touching nothing -- a link is
    never made to a folder without the runner. Raises OSError if the
    cross-process lock times out, or if a repoint fails outright.
    """
    fmt_platform = sys.platform if platform is None else platform
    executable = sys.executable if executable is None else executable

    if is_translocated(executable):
        raise AppTranslocatedError()
    # On Windows a launch through the junction reports the junction path
    # as sys.executable (spec Q2a); realpath resolves it to the real
    # release before anything below reasons about it.
    resolved = os.path.realpath(executable)
    if is_translocated(resolved):
        raise AppTranslocatedError()

    release = os.path.dirname(resolved)
    bundled = hooks_install.hook_runner_path(resolved, fmt_platform)
    if not os.path.isfile(bundled):
        raise FileNotFoundError(bundled)

    state_dir = Path(state_dir)
    link = state_dir / LINK_NAME
    link_str = str(link)

    release_norm = os.path.normcase(os.path.normpath(release))
    link_norm = os.path.normcase(os.path.normpath(link_str))
    if release_norm == link_norm or release_norm.startswith(link_norm + os.sep):
        raise OSError(f"refusing to point {link_str} at itself or a path inside it")

    state_dir.mkdir(parents=True, exist_ok=True)
    lock = SingleInstanceLock(state_dir, name=LOCK_NAME)
    deadline = time.monotonic() + lock_timeout
    while True:
        try:
            lock.acquire()
            break
        except LockAcquisitionError:
            if time.monotonic() >= deadline:
                raise OSError(f"timed out waiting for the lock on {state_dir / LOCK_NAME}")
            time.sleep(0.05)

    try:
        # Redone under the lock, immediately before any removal: a GUI
        # launch and a CLI --install-hooks call race on this exact check.
        exists = os.path.lexists(link_str)
        if exists and not _is_link(link_str):
            return LinkOutcome(
                bundled,
                "<state dir>/current is not a link Tokitty made, so hooks use this release's own path",
            )
        if exists:
            current_real = os.path.normcase(os.path.normpath(os.path.realpath(link_str)))
            if current_real == release_norm:
                return LinkOutcome(hooks_install.stable_runner_path(state_dir, fmt_platform), None)
        _repoint(link_str, release, exists)
        return LinkOutcome(hooks_install.stable_runner_path(state_dir, fmt_platform), None)
    finally:
        lock.release()
