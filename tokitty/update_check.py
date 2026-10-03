"""The daily update check and what it publishes to the menus (#77).

`UpdateChecker.tick()` runs on the Tk thread. The check itself runs on a
daemon thread that never touches Tk: it publishes into a lock-guarded field
that `tick()` reads, the way `Poller.get_latest` works. The menu label is read
from pystray's thread, so it lives in plain Python state. The schedule is in
docs/superpowers/specs/2026-10-02-auto-update-design.md, "The check".
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from tokitty.updater import (
    Release,
    RunningVersion,
    UpdateCheckError,
    fetch_releases,
    is_newer,
    load_update_state,
    mutate_update_state,
    platform_target,
    select_latest,
)

FIRST_CHECK_DELAY = timedelta(seconds=60)
STALE_AFTER = timedelta(hours=20)
CHECK_INTERVAL = timedelta(hours=24)
RETRY_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class CheckResult:
    """`release` is the newest stable release (None if there is none) and
    `newer` whether it is ahead of the running version. `error` is set, and
    nothing else, when the check failed."""

    release: Optional[Release] = None
    newer: bool = False
    error: Optional[str] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UpdateChecker:
    def __init__(
        self,
        state_dir,
        running: RunningVersion,
        *,
        enabled: Callable[[], bool] = lambda: True,
        on_available: Optional[Callable[[Release], None]] = None,
        fetch: Optional[Callable[[], list]] = None,
        target: Optional[str] = None,
        now: Callable[[], datetime] = _utcnow,
    ):
        self._state_dir = state_dir
        self._running = running
        self._enabled = enabled
        self._on_available = on_available
        # Looked up at call time, so a test can replace the module's fetch.
        self._fetch = fetch or (lambda: fetch_releases())
        self._target = platform_target() if target is None else target
        self._now = now
        # Written by the worker thread and read by tick(), under _lock.
        self._lock = threading.Lock()
        self._checking = False
        self._result: Optional[CheckResult] = None
        # Read by pystray's thread for the menu label, so plain state.
        self._latest: Optional[Release] = None
        self._available_tag: Optional[str] = None
        # Tk thread only.
        self._waiters: List[Callable[[CheckResult], None]] = []
        self._announced: Optional[str] = None
        state = load_update_state(state_dir)
        # A tag found on an earlier run shows in the menu straight away, with
        # no Release behind it: clicking it checks again first.
        if is_newer(state.latest_tag, running.version):
            self._available_tag = state.latest_tag
        self._due = self._first_due(state.last_checked)

    def _first_due(self, last_checked: Optional[str]) -> datetime:
        now = self._now()
        try:
            checked = datetime.fromisoformat(last_checked) if last_checked else None
        except ValueError:
            checked = None
        if checked is not None and checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        age = None if checked is None else now - checked
        if age is None or age < timedelta(0) or age > STALE_AFTER:
            return now + FIRST_CHECK_DELAY
        return now + (CHECK_INTERVAL - age)

    @property
    def latest_release(self) -> Optional[Release]:
        """The newest release the last successful check found, or None."""
        with self._lock:
            return self._latest

    @property
    def update_available(self) -> bool:
        with self._lock:
            return self._available_tag is not None

    def menu_label(self) -> Optional[str]:
        """"Update to vX.Y.Z…" while a newer release is known, else None.
        Safe to call from any thread."""
        with self._lock:
            tag = self._available_tag
        return f"Update to {tag}…" if tag else None

    def check_now(self, on_result: Callable[[CheckResult], None]) -> None:
        """A manual check, which runs whatever the setting says. `on_result`
        is called from tick() on the Tk thread. A check already in flight is
        joined rather than doubled."""
        self._waiters.append(on_result)
        self._start()

    def tick(self) -> None:
        with self._lock:
            result, self._result = self._result, None
        if result is not None:
            self._consume(result)
        now = self._now()
        if now >= self._due and self._enabled() and self._start():
            # Held back until the result is in, and retried an hour after a
            # failure: tick() runs far more often than that.
            self._due = now + RETRY_INTERVAL

    def _start(self) -> bool:
        with self._lock:
            if self._checking:
                return False
            self._checking = True
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    def _worker(self) -> None:
        try:
            release = select_latest(self._fetch(), self._target)
            result = CheckResult(release, release is not None and is_newer(release.tag, self._running.version))
        except UpdateCheckError as exc:
            result = CheckResult(error=str(exc))
        except Exception as exc:
            result = CheckResult(error=f"The update check failed: {exc}")
        if result.error is None:
            self._record(result.release)
        with self._lock:
            self._result, self._checking = result, False

    def _record(self, release: Optional[Release]) -> None:
        checked = self._now().isoformat()

        def edit(state) -> None:
            state.last_checked = checked
            if release is not None:
                state.latest_tag = release.tag

        try:
            mutate_update_state(self._state_dir, edit)
        except OSError as exc:
            print(f"tokitty: update: saving the check: {exc}", file=sys.stderr)

    def _consume(self, result: CheckResult) -> None:
        if result.error is None:
            self._due = self._now() + CHECK_INTERVAL
            release = result.release if result.newer else None
            with self._lock:
                self._latest = result.release
                self._available_tag = release.tag if release else None
            if release is not None and release.tag != self._announced:
                self._announced = release.tag
                self._call(self._on_available, release)
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            self._call(waiter, result)

    @staticmethod
    def _call(callback, *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:
            print(f"tokitty: update: callback: {exc}", file=sys.stderr)


def announcer(state_dir, tray) -> Callable[[Release], None]:
    """What to do when a newer release first turns up: nudge the tray menu
    (pystray builds its menu once) and notify once per tag. `notified_tag`
    is only written when the backend took the notification, so a tray that
    can't notify, or is switched off, leaves it for a later run."""

    def announce(release: Release) -> None:
        tray.refresh()
        if load_update_state(state_dir).notified_tag == release.tag:
            return
        if not tray.notify(f"Tokitty {release.tag} is available", "Right-click Tokitty to update"):
            return

        def edit(state) -> None:
            state.notified_tag = release.tag

        try:
            mutate_update_state(state_dir, edit)
        except OSError as exc:
            print(f"tokitty: update: recording the notification: {exc}", file=sys.stderr)

    return announce
