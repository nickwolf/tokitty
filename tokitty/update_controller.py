"""The install flow and the Tk-thread state machine that drives it (#77).

`UpdateController` runs the spec's "Installing" steps. A worker thread stages
and self-checks a release; a second one waits for the new copy's ack. Neither
touches Tk: they publish into lock-guarded fields that `tick()` reads on the
Tk thread, which also does the handover itself (step 5). `TkHost` holds the
Tk-side actions so the controller can be driven by fakes. The reasoning is in
docs/superpowers/specs/2026-10-02-auto-update-design.md.

Everything the handover and the rollback need is imported at the top of this
module: between the macOS bundle swap and the ack the old process runs from a
renamed bundle and must not import anything.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from tokitty import autostart, runner_link
from tokitty.lock import acquire_with_retry
from tokitty.update_install import (
    Staged,
    UpdateCancelled,
    UpdateInstallError,
    _kind,
    binary_paths,
    child_env,
    discard_staging,
    install_target,
    probe_writable,
    run_self_check,
    stage,
)
from tokitty.update_swap import (
    ack_path,
    cleanup,
    clear_pending,
    clear_stale_pending,
    launch_detached,
    mac_swap_back,
    mac_swap_in,
    new_token,
    promote,
    stale_pending,
    wait_for_ack,
    write_pending,
)
from tokitty.updater import (
    API_URL_ENV,
    Release,
    RunningVersion,
    UpdateCheckError,
    fetch_releases,
    is_newer,
    load_update_state,
    platform_target,
    running_version,
    select_latest,
)

ACK_TIMEOUT = 120.0
RESTORE_LOCK_TIMEOUT = 10.0
NO_ACK_ENV = "TOKITTY_UPDATE_TEST_NO_ACK"

# What on_done receives: "installed" (the new copy acked), "rolled_back" (it
# didn't start and this copy is running again), "failed" or "cancelled".
OUTCOMES = ("installed", "rolled_back", "failed", "cancelled")


@dataclass(frozen=True)
class UpdateResult:
    outcome: str
    message: str = ""
    tag: Optional[str] = None


@dataclass(frozen=True)
class UpdateStatus:
    phase: str  # "idle", "staging", "waiting" (for the new copy's ack)
    done: int = 0
    total: int = 0


def skips_ack(environ) -> bool:
    """The test-only switch that makes a new copy exit before acking. It is
    honoured only next to TOKITTY_UPDATE_API_URL, so a stray variable on a
    real machine can't make an update look like it failed."""
    return environ.get(NO_ACK_ENV) == "1" and bool(environ.get(API_URL_ENV))


class TkHost:
    """The Tk-side actions of a handover and of its rollback. All of them run
    on the Tk thread. `quiet` (the hidden --apply-update) opens no messagebox,
    since nobody is there to close it; the caller reports through on_done."""

    def __init__(
        self,
        root,
        windows,
        tray,
        lock,
        state_dir,
        autostart_backend,
        *,
        quiet: bool = False,
        messagebox=None,
        tray_enabled: Callable[[], bool] = lambda: True,
    ):
        self._root = root
        self._windows = [w for i, w in enumerate(windows) if w is not None and w not in windows[:i]]
        self._tray = tray
        self._lock = lock
        self._state_dir = state_dir
        self._backend = autostart_backend
        self._quiet = quiet
        self._tray_enabled = tray_enabled
        if messagebox is None and not quiet:
            from tkinter import messagebox
        self._messagebox = messagebox

    def hide(self) -> None:
        """Handover step 5.3: tray off, windows away, lock released."""
        self._tray.stop()
        for window in self._windows:
            window.withdraw()
        self._root.update_idletasks()
        self._lock.release()

    def restore(self) -> None:
        """Handover step 5.8 without the message. Raises LockAcquisitionError
        if another copy holds the lock, in which case nothing else is done."""
        acquire_with_retry(self._lock, RESTORE_LOCK_TIMEOUT)
        try:
            runner_link.ensure_runner_link(self._state_dir)
        except Exception as exc:
            print(f"tokitty: update: relinking hooks: {exc}", file=sys.stderr)
        if self._backend is not None:
            try:
                autostart.ensure_current(self._state_dir, self._backend)
            except Exception as exc:
                print(f"tokitty: update: autostart: {exc}", file=sys.stderr)
        for window in self._windows:
            window.deiconify()
        if self._tray_enabled():
            self._tray.start()
        self._tray.refresh()

    def finish(self) -> None:
        """Close the app. Deferred, because tick() calls this and still has
        widgets to touch, and safe to call twice."""
        self._root.after(0, self._destroy)

    def _destroy(self) -> None:
        try:
            self._root.destroy()
        except Exception:
            pass  # already destroyed

    def notice(self, text: str) -> None:
        if self._quiet:
            return

        def show() -> None:
            self._messagebox.showwarning("Tokitty", text, parent=self._root)

        # Deferred like the other startup messages, so tick() isn't held up.
        self._root.after(0, show)


class UpdateController:
    def __init__(
        self,
        state_dir,
        host,
        *,
        running: Optional[RunningVersion] = None,
        executable: Optional[str] = None,
        sys_platform: Optional[str] = None,
        environ=None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        stage=stage,
        self_check=None,
        promote=promote,
        mac_swap_in=mac_swap_in,
        mac_swap_back=mac_swap_back,
        launch=launch_detached,
        wait_for_ack=wait_for_ack,
        cleanup=cleanup,
        fetch=fetch_releases,
        new_token=new_token,
    ):
        self._state_dir = Path(state_dir)
        self._host = host
        self._running = running or running_version()
        self._executable = sys.executable if executable is None else executable
        self._sys_platform = sys.platform if sys_platform is None else sys_platform
        self._environ = os.environ if environ is None else environ
        self._clock = clock
        self._stage = stage
        self._self_check = self_check or (lambda gui, tag: run_self_check(gui, tag, sys_platform=self._sys_platform))
        self._promote = promote
        self._mac_swap_in = mac_swap_in
        self._mac_swap_back = mac_swap_back
        self._launch = launch
        self._wait_for_ack = wait_for_ack
        self._cleanup = cleanup
        self._fetch = fetch
        self._new_token = new_token
        self._target = install_target(self._executable, self._sys_platform)
        self._mac = _kind(self._sys_platform) == "macos"
        # Written by the worker threads and read by tick(), under _lock.
        self._lock = threading.Lock()
        self._phase = "idle"
        self._progress = (0, 0)
        self._progress_dirty = False
        self._stage_result = None
        self._ack_result: Optional[bool] = None
        # Set while no startup cleanup is running. Cleanup deletes every
        # owned staging folder that no live `pending` names, so an install
        # must not start staging until it has finished.
        self._cleanup_done = threading.Event()
        self._cleanup_done.set()
        self._cancel = threading.Event()
        # Tk thread only.
        self._on_progress = None
        self._on_done = None
        self._handover_state = None

    @property
    def running(self) -> RunningVersion:
        return self._running

    @property
    def status(self) -> UpdateStatus:
        with self._lock:
            return UpdateStatus(self._phase, *self._progress)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._phase != "idle"

    def refusal(self, release: Release) -> Optional[str]:
        """Why this release can't be installed from the app, or None. Writes
        and removes a probe file, so call it from the Tk thread sparingly."""
        if not self._running.installable:
            return "This copy of Tokitty can't update itself."
        if not is_newer(release.tag, self._running.version):
            return f"Tokitty {self._running.version} is already up to date."
        if not release.installable_from_app:
            return "This release has no download for this system, or no SHA256SUMS to check it against."
        if self._target.refusal:
            return self._target.refusal
        if not probe_writable(self._target.parent):
            return f"Tokitty can't write to {self._target.parent}."
        return None

    def startup(self, *, frozen: bool) -> None:
        """Run on every launch, after the lock is held: clear a stale
        `pending`, and in a frozen build start the cleanup thread."""
        try:
            if stale_pending(load_update_state(self._state_dir), self._clock()):
                clear_stale_pending(self._state_dir, self._clock())
        except OSError as exc:
            print(f"tokitty: update: clearing pending: {exc}", file=sys.stderr)
        if not frozen:
            return
        exe = os.path.realpath(self._executable)
        link = self._state_dir / "current"
        kwargs = dict(
            running_release=exe,
            current_target=os.path.realpath(link) if os.path.lexists(link) else None,
            running_version=self._running.version,
            target=self._target,
            now=self._clock(),
        )
        self._cleanup_done.clear()
        threading.Thread(target=self._cleanup_worker, args=(kwargs,), daemon=True).start()

    def _cleanup_worker(self, kwargs: dict) -> None:
        try:
            self._cleanup(self._state_dir, **kwargs)
        except Exception as exc:
            print(f"tokitty: update: cleanup: {exc}", file=sys.stderr)
        finally:
            self._cleanup_done.set()

    def start_install(self, release: Release, on_progress=None, on_done=None) -> bool:
        """Stage, check and hand over to `release`. `on_progress(done, total)`
        and `on_done(UpdateResult)` are called from tick(), on the Tk thread,
        and on_done exactly once. False if an install is already running."""
        return self._begin(release, on_progress, on_done)

    def start_apply_newest(self, on_done=None) -> bool:
        """The hidden --apply-update: install the newest release, found on
        the worker thread, with no dialog."""
        return self._begin(None, None, on_done)

    def _begin(self, release, on_progress, on_done) -> bool:
        with self._lock:
            if self._phase != "idle":
                return False
            self._phase = "staging"
            self._progress, self._progress_dirty = (0, 0), False
            self._stage_result = self._ack_result = None
        self._cancel.clear()
        self._on_progress, self._on_done = on_progress, on_done
        threading.Thread(target=self._stage_worker, args=(release,), daemon=True).start()
        return True

    def cancel(self) -> None:
        """Stop a download in progress. Too late once the handover has begun."""
        self._cancel.set()

    def _publish_progress(self, done: int, total: int) -> None:
        with self._lock:
            self._progress, self._progress_dirty = (done, total), True

    def _find_newest(self) -> Release:
        try:
            release = select_latest(self._fetch(), platform_target(self._sys_platform))
        except UpdateCheckError as exc:
            raise UpdateInstallError(f"Could not check for updates: {exc}") from exc
        if release is None or not is_newer(release.tag, self._running.version):
            raise UpdateInstallError(f"There is no release newer than {self._running.version}.")
        return release

    def _stage_worker(self, release: Optional[Release]) -> None:
        try:
            while not self._cleanup_done.wait(0.1):
                if self._cancel.is_set():
                    raise UpdateCancelled()
            if release is None:
                release = self._find_newest()
            reason = self.refusal(release)
            if reason:
                raise UpdateInstallError(reason)
            staged = self._stage(
                release,
                self._target,
                self._state_dir,
                sys_platform=self._sys_platform,
                progress=self._publish_progress,
                cancelled=self._cancel.is_set,
                verify=lambda gui: self._self_check(gui, release.tag),
            )
            result = ("ok", release, staged)
        except BaseException as exc:  # the Tk side must always hear back
            result = ("err", release, exc)
        with self._lock:
            self._stage_result = result

    def tick(self) -> None:
        """Advance the state machine; the Tk thread calls this every pass."""
        with self._lock:
            progress = self._progress if self._progress_dirty else None
            self._progress_dirty = False
            stage_result, self._stage_result = self._stage_result, None
            ack, self._ack_result = self._ack_result, None
        if progress is not None:
            self._call(self._on_progress, *progress)
        if stage_result is not None:
            self._after_stage(*stage_result)
        if ack is not None:
            self._after_ack(ack)

    def _call(self, callback, *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:
            print(f"tokitty: update: callback: {exc}", file=sys.stderr)

    def _conclude(self, outcome: str, message: str = "", tag: Optional[str] = None) -> None:
        with self._lock:
            self._phase = "idle"
        self._handover_state = None
        self._call(self._on_done, UpdateResult(outcome, message, tag))
        if outcome == "installed":
            self._host.finish()

    def _after_stage(self, kind: str, release, payload) -> None:
        tag = getattr(release, "tag", None)
        if kind == "err":
            if isinstance(payload, UpdateCancelled):
                self._conclude("cancelled", tag=tag)
            elif isinstance(payload, UpdateInstallError):
                self._conclude("failed", str(payload), tag)
            else:
                self._conclude("failed", f"The update failed: {payload}", tag)
            return
        if self._cancel.is_set():
            self._discard(payload)
            self._conclude("cancelled", tag=tag)
            return
        self._handover(release, payload)

    def _discard(self, staged: Staged) -> None:
        try:
            discard_staging(self._state_dir, staged.staging)
        except OSError as exc:
            print(f"tokitty: update: discarding staging: {exc}", file=sys.stderr)

    def _handover(self, release: Release, staged: Staged) -> None:
        """Spec step 5 on the Tk thread. Windows and Linux: pending, promote,
        hide, launch. macOS: pending, hide, swap, launch."""
        tag, target, mac = release.tag, self._target, self._mac
        token = self._new_token()
        old_path = target.final_path(tag) if mac else Path(os.path.realpath(self._executable)).parent
        try:
            write_pending(
                self._state_dir,
                old_version=self._running.version,
                new_version=tag,
                old_path=old_path,
                new_path=target.final_path(tag),
                token=token,
                staging=staged.staging,
            )
            top = None if mac else self._promote(
                staged, target, tag, self._state_dir, sys_platform=self._sys_platform, self_check=self._self_check
            )
        except (UpdateInstallError, OSError) as exc:
            self._discard(staged)
            self._clear_pending(token)
            self._conclude("failed", str(exc), tag)
            return
        backup = None
        try:
            self._host.hide()
            if mac:
                backup = self._mac_swap_in(staged, target.final_path(tag), self._running.version, self._state_dir)
                top = target.final_path(tag)
            argv = [str(binary_paths(top, self._sys_platform)[0]), "--after-update", token]
            child = self._launch(argv, child_env(self._environ, self._sys_platform), self._sys_platform)
        except Exception as exc:
            self._rollback(tag, token, backup, f"Tokitty {tag} couldn't be started ({exc}).", "failed")
            return
        with self._lock:
            self._phase = "waiting"
        self._handover_state = (tag, token, backup)
        threading.Thread(target=self._ack_worker, args=(token, child), daemon=True).start()

    def _clear_pending(self, token: str) -> None:
        try:
            clear_pending(self._state_dir, token)
        except OSError as exc:
            print(f"tokitty: update: clearing pending: {exc}", file=sys.stderr)

    def _ack_worker(self, token: str, child) -> None:
        try:
            # A new copy that exits without acking has failed: no point
            # waiting out the timeout.
            acked = bool(
                self._wait_for_ack(
                    ack_path(self._state_dir, token), ACK_TIMEOUT, abort=lambda: child.poll() is not None
                )
            )
        except Exception:
            acked = False
        if not acked:
            self._kill(child)
        with self._lock:
            self._ack_result = acked

    @staticmethod
    def _kill(child) -> None:
        try:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        except Exception as exc:
            print(f"tokitty: update: stopping the new copy: {exc}", file=sys.stderr)

    def _after_ack(self, acked: bool) -> None:
        tag, token, backup = self._handover_state
        if acked:
            self._clear_pending(token)
            self._conclude("installed", f"Updated to {tag}.", tag)
            return
        self._rollback(tag, token, backup, f"Tokitty {tag} didn't start.", "rolled_back")

    def _rollback(self, tag: str, token: str, backup, message: str, outcome: str) -> None:
        """Handover step 5.8, after the child has been stopped. Every step is
        attempted even if an earlier one failed, because the windows must come
        back whatever happens."""
        message += f" Still running {self._running.version}."
        if backup is not None:
            try:
                self._mac_swap_back(self._target.final_path(tag), backup, self._state_dir)
            except (OSError, UpdateInstallError) as exc:
                message += f" Could not put the old app back ({exc})."
        try:
            self._host.restore()
        except Exception as exc:
            # Another copy holds the lock (or restoring failed outright), so
            # this one can't carry on as the single instance.
            self._clear_pending(token)
            message += f" Couldn't resume ({exc})."
            self._host.notice(message)
            self._conclude("failed", message, tag)
            self._host.finish()
            return
        self._clear_pending(token)
        self._host.notice(message)
        self._conclude(outcome, message, tag)
