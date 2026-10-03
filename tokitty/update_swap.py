"""Swap a staged release into place, hand over to it, and clean up (#77).

The primitives behind the spec's "Installing" step 5 and "The new copy":
promoting a staged copy, the macOS bundle swap, the `pending` record, the
detached launch and the ack, and `cleanup`, which deletes only what the
updater recorded in `owned`. Platform branches take `sys_platform` or an
injectable seam so Linux tests drive the Windows and macOS logic.
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Set

from tokitty.update_install import (
    Staged,
    Target,
    UpdateInstallError,
    _is_link,
    _kind,
    binary_paths,
    dir_identity,
    discard_staging,
    run_self_check,
    validate_layout,
)
from tokitty.updater import UpdateState, add_owned, drop_owned, load_update_state, mutate_update_state, parse_version

RENAME_SWAP = 0x2
PENDING_STALE = timedelta(minutes=5)
TRASH_PREFIX = ".tokitty-trash-"
STAGING_PREFIX = ".tokitty-update-"
ACK_PREFIX = "update-ack-"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def new_token() -> str:
    return uuid.uuid4().hex


def _norm(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _is_dir(path) -> bool:
    """A real directory: not a link or junction, which `_is_link` also reports
    for a missing path."""
    return not _is_link(path) and os.path.isdir(path)


def backup_path(app, old_tag: str) -> Path:
    return Path(app).parent / f".Tokitty-{old_tag}.app"


def promote(
    staged: Staged,
    target: Target,
    tag: str,
    state_dir,
    *,
    sys_platform: Optional[str] = None,
    self_check: Optional[Callable[[Path], None]] = None,
) -> Path:
    """Windows and Linux: rename the staged top folder to
    `<versions dir>/<tag>/<top>`, record it as an owned copy and discard the
    staging folder. An existing final path is reused only if the updater
    recorded it as a copy and it passes the layout check and the self-check;
    otherwise UpdateInstallError and nothing is removed."""
    sys_platform = sys.platform if sys_platform is None else sys_platform
    if _kind(sys_platform) == "macos":
        raise ValueError("macOS installs swap bundles; use mac_swap_in")
    final = target.final_path(tag)
    holder = final.parent
    if os.path.lexists(holder) and (_is_link(holder) or not os.path.isdir(holder)):
        raise UpdateInstallError(f"{holder} is not a plain folder, so the update was not installed there.")
    if os.path.lexists(final):
        owned = any(e["path"] == str(final) and e["kind"] == "copy" for e in load_update_state(state_dir).owned)
        if _is_link(final) or not owned:
            raise UpdateInstallError(f"{final} already exists and Tokitty didn't put it there, so it was left alone.")
        verify = self_check or (lambda gui: run_self_check(gui, tag, sys_platform=sys_platform))
        try:
            verify(binary_paths(validate_layout(holder, sys_platform), sys_platform)[0])
        except UpdateInstallError as exc:
            raise UpdateInstallError(f"The existing {final} failed its check ({exc}), so it was left alone.") from exc
        discard_staging(state_dir, staged.staging)
        return final
    made = not os.path.lexists(holder)
    try:
        holder.mkdir(parents=True, exist_ok=True)
        # The rename keeps the inode, so the identity read first is the copy's.
        add_owned(state_dir, final, tag, "copy", **dir_identity(staged.top))
        os.rename(staged.top, final)
    except OSError as exc:
        drop_owned(state_dir, final)
        if made:
            try:
                holder.rmdir()
            except OSError:
                pass
        raise UpdateInstallError(f"Could not move the update into place: {exc}") from exc
    discard_staging(state_dir, staged.staging)
    return final


def _renamex_np(a, b, flags: int) -> None:
    import ctypes

    fn = ctypes.CDLL(None, use_errno=True).renamex_np
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(os.fsencode(a), os.fsencode(b), flags) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(a))


def swap_bundles(a, b, *, renamex: Callable[[object, object, int], None] = _renamex_np) -> None:
    """Atomically exchange two paths on one volume (`RENAME_SWAP`). An
    unsupported filesystem becomes an UpdateInstallError that tells the caller
    to offer the release page; any other failure is the OSError."""
    try:
        renamex(a, b, RENAME_SWAP)
    except AttributeError as exc:
        raise UpdateInstallError("This system can't swap app bundles, so install from the release page.") from exc
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            raise UpdateInstallError("This disk can't swap app bundles, so install from the release page.") from exc
        raise


def mac_swap_in(
    staged: Staged,
    app,
    old_tag: str,
    state_dir,
    *,
    swap: Callable[[object, object], None] = swap_bundles,
) -> Path:
    """Swap the staged bundle with the installed one. `app` is then the new
    bundle and the staged path holds the old one, still running; that is
    renamed to the hidden `.Tokitty-<old tag>.app`, recorded as a backup, and
    the staging folder is discarded. Returns the backup path."""
    app, staged_app = Path(app), staged.top
    backup = backup_path(app, old_tag)
    if os.path.lexists(backup):
        entry = next(
            (e for e in load_update_state(state_dir).owned if e["path"] == str(backup) and e["kind"] == "backup"), None
        )
        if entry is None or not _recorded(entry, backup) or _trash_and_remove(backup, os.rename) != "removed":
            raise UpdateInstallError(f"{backup} is already there, so the update was not installed.")
        drop_owned(state_dir, backup)
    swap(staged_app, app)
    renamed = False
    try:
        os.rename(staged_app, backup)
        renamed = True
        add_owned(state_dir, backup, old_tag, "backup", **dir_identity(backup))
    except Exception as exc:
        # The swap is applied, so anything failing before we return undoes it.
        try:
            if renamed:
                os.rename(backup, staged_app)
            swap(staged_app, app)
        except (OSError, UpdateInstallError) as undo:
            raise UpdateInstallError(
                f"The update was swapped in but could not be finished ({exc}), and undoing it failed ({undo})."
            ) from exc
        raise UpdateInstallError(f"Could not keep the old copy ({exc}), so the update was not installed.") from exc
    discard_staging(state_dir, staged.staging)
    return backup


def mac_swap_back(app, backup, state_dir, *, swap: Callable[[object, object], None] = swap_bundles) -> None:
    """Undo `mac_swap_in`. The bundle that failed to start is then at `backup`
    and is removed; if it can't be, it stays recorded as staging for cleanup."""
    swap(app, backup)
    if _trash_and_remove(Path(backup), os.rename) == "removed":
        drop_owned(state_dir, backup)
    else:

        try:
            identity = dir_identity(backup)
        except OSError:
            identity = {}

        def retag(state: UpdateState) -> None:
            for entry in state.owned:
                if entry["path"] == str(backup):
                    entry["kind"] = "staging"
                    entry.update(identity)

        mutate_update_state(state_dir, retag)


def write_pending(
    state_dir,
    *,
    old_version: str,
    new_version: str,
    old_path,
    new_path,
    token: str,
    staging,
    now: Optional[datetime] = None,
) -> dict:
    """Record the handover about to start. `staging` is the staging folder;
    on macOS the swap uses `<staging>/unpacked/Tokitty.app`."""
    record = {
        "old_version": old_version,
        "new_version": new_version,
        "old_path": str(old_path),
        "new_path": str(new_path),
        "token": token,
        "staging": str(staging),
        "started": (now or datetime.now(timezone.utc)).isoformat(),
    }

    def edit(state: UpdateState) -> None:
        state.pending = record

    mutate_update_state(state_dir, edit)
    return record


def clear_pending(state_dir, token: Optional[str] = None) -> None:
    """Clear `pending`; with a token, only if it is that handover's record."""

    def edit(state: UpdateState) -> None:
        if token is None or (state.pending or {}).get("token") == token:
            state.pending = None

    mutate_update_state(state_dir, edit)


def stale_pending(state: UpdateState, now: datetime) -> bool:
    """A `pending` older than 5 minutes (or with no readable start time): the
    old process died mid-handover."""
    if state.pending is None:
        return False
    try:
        started = datetime.fromisoformat(state.pending["started"])
    except (KeyError, TypeError, ValueError):
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return now - started > PENDING_STALE


def clear_stale_pending(state_dir, now: datetime) -> bool:
    """Clear a stale `pending` under the state lock; True if one was cleared."""
    cleared = []

    def edit(state: UpdateState) -> None:
        if stale_pending(state, now):
            state.pending = None
            cleared.append(True)

    mutate_update_state(state_dir, edit)
    return bool(cleared)


def launch_detached(argv, env, sys_platform: str, *, popen=subprocess.Popen):
    """Start `argv` fully detached from this process and its console."""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if _kind(sys_platform) == "windows":
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
    return popen(list(argv), env=env, **kwargs)


def ack_path(state_dir, token: str) -> Path:
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("bad update token")
    return Path(state_dir) / f"{ACK_PREFIX}{token}"


def write_ack(state_dir, token: str) -> Path:
    """Atomic, so the waiting process never sees a half-written file."""
    path = ack_path(state_dir, token)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(token, encoding="utf-8")
    os.replace(tmp, path)
    return path


def wait_for_ack(
    path,
    timeout: float,
    *,
    poll: float = 0.25,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    abort: Callable[[], bool] = lambda: False,
) -> bool:
    """True once the ack file exists (and is removed). False after `timeout`,
    or as soon as `abort()` is true and there is still no ack."""
    deadline = clock() + timeout
    while True:
        stop = abort()
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
            return True
        if stop or clock() >= deadline:
            return False
        sleep(poll)


def _trash_and_remove(path: Path, rename) -> str:
    """Rename `path` to `.tokitty-trash-<name>`, then remove it. Returns
    "removed", "partial" (renamed, but part of it is still there) or "kept"
    (not a real directory, or the rename failed: it is untouched). The rename
    comes first so a folder with a file in use on Windows fails whole instead
    of being half-deleted, and rmtree never sees a link as the root."""
    if not _is_dir(path):
        return "kept"
    trash = path.with_name(TRASH_PREFIX + path.name)
    if os.path.lexists(trash):
        if _is_dir(trash):
            shutil.rmtree(trash, ignore_errors=True)
        if os.path.lexists(trash):
            return "kept"
    try:
        rename(path, trash)
    except OSError:
        return "kept"
    shutil.rmtree(trash, ignore_errors=True)
    return "partial" if os.path.lexists(trash) else "removed"


def _recorded(entry: dict, path) -> bool:
    """True only if `path` is, right now, the very directory the entry
    recorded. An entry with no identity, or a link or a replacement folder at
    the path, is not."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return entry.get("dev") == st.st_dev and entry.get("ino") == st.st_ino


def _location_ok(entry: dict, target: Target) -> bool:
    """An owned path is only touched where the updater puts things: staging
    folders and macOS backups beside the target, copies at
    `<versions dir>/<version>/<top>`."""
    path, kind = Path(entry["path"]), entry["kind"]
    parent, mac = path.parent, _kind(target.sys_platform) == "macos"
    if _is_link(parent):
        return False
    if kind == "copy":
        return (
            not mac
            and parse_version(entry["version"]) is not None
            and path.name == target.top
            and parent.name == entry["version"]
            and _norm(parent.parent) == _norm(target.parent)
        )
    if _norm(parent) != _norm(target.parent):
        return False
    if kind == "staging":
        return path.name.startswith(STAGING_PREFIX)
    return mac and path.name.startswith(".Tokitty-") and path.name.endswith(".app")


def _protected(path: Path, keep: List[str]) -> bool:
    here = _norm(path)
    return any(k == here or k.startswith(here + os.sep) for k in keep)


def cleanup(
    state_dir,
    *,
    running_release,
    current_target,
    running_version: str,
    target: Target,
    now: datetime,
    rename=os.rename,
) -> List[str]:
    """Delete what the updater installed and no longer needs, and nothing
    else. Only `owned` entries in their expected place are considered. Kept:
    the running release, `current`'s target, any live pending's staging
    folder, and the replaced copy (the newest owned copy or backup below the
    running version). Deleted: other staging folders, and copies or backups
    older than the replaced one. A link at an owned path is never followed or
    touched. Returns the paths removed."""
    state = load_update_state(state_dir)
    keep = [_norm(p) for p in (running_release, current_target) if p]
    live = None
    if state.pending is not None and not stale_pending(state, now) and state.pending.get("staging"):
        live = _norm(state.pending["staging"])
    running = parse_version(running_version)
    dropped: Set[str] = set()
    valid, holders = [], []
    for entry in state.owned:
        path = Path(entry["path"])
        if not path.is_absolute():
            continue
        gone = not os.path.lexists(path)
        if not _location_ok(entry, target):
            if gone:
                dropped.add(entry["path"])
            continue
        # Only this entry's own trash path, the one `_trash_and_remove` makes.
        # An entry whose trash survives stays recorded so the next run retries.
        trash = path.with_name(TRASH_PREFIX + path.name)
        if _is_dir(trash):
            shutil.rmtree(trash, ignore_errors=True)
        if gone and not os.path.lexists(trash):
            dropped.add(entry["path"])
        if entry["kind"] == "copy":
            holders.append(path.parent)
        if gone:
            continue
        if not _recorded(entry, path):
            # No longer the folder the updater made: stop tracking it, never delete it.
            dropped.add(entry["path"])
        elif _is_dir(path):
            valid.append(entry)
    older = [
        v
        for e in valid
        if e["kind"] != "staging" and running and (v := parse_version(e["version"])) and v < running
    ]
    replaced = max(older, default=None)
    removed = []
    for entry in valid:
        path = Path(entry["path"])
        if _protected(path, keep):
            continue
        if entry["kind"] == "staging":
            doomed = _norm(path) != live
        else:
            version = parse_version(entry["version"])
            doomed = replaced is not None and version is not None and version < replaced
        if not doomed:
            continue
        outcome = _trash_and_remove(path, rename)
        if outcome == "removed":
            dropped.add(entry["path"])
            removed.append(entry["path"])
    for holder in holders:
        if _is_dir(holder):
            try:
                holder.rmdir()
            except OSError:
                pass
    if dropped:

        def edit(state: UpdateState) -> None:
            state.owned = [e for e in state.owned if e["path"] not in dropped]

        mutate_update_state(state_dir, edit)
    return removed


def valid_token(token) -> bool:
    return isinstance(token, str) and _TOKEN_RE.fullmatch(token) is not None
