"""Background rescanning of one account's transcripts, decoupled from the UI.

Shaped after ActivityWatcher on purpose: same start/stop/get_latest
contract, same injectable seams, same "resolution failure means run
without it, never a crash" philosophy. tick() on the Tk thread reads
get_latest() exactly the way it reads the activity watcher's.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from tokitty.usage_scan import (
    DEFAULT_WINDOW,
    STATUS_UNAVAILABLE,
    TranscriptScanner,
    UsageBreakdown,
    window_start,
)

SCAN_INTERVAL = 60.0


class UsageWatcher:
    """Owns a TranscriptScanner on a daemon thread and publishes immutable
    snapshots.

    The record store stays worker-owned and the lock is never held while
    scanning: each pass builds a fully detached UsageBreakdown of tuples
    and publishes it by swapping one reference. The Tk thread can then hold
    an older snapshot across ticks with no tearing, and the size of the
    dict behind it is irrelevant because the dict is never handed out.
    """

    def __init__(
        self,
        projects_dir,
        distro_name: Optional[str] = None,
        window: Callable[[], str] = lambda: DEFAULT_WINDOW,
        interval: float = SCAN_INTERVAL,
        list_running_distros_fn: Optional[Callable[[], list]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        sleep_fn: Optional[Callable[[float], bool]] = None,
    ):
        self._projects_dir = projects_dir
        self._distro_name = distro_name
        self._window = window
        self._interval = interval
        self._list_running_distros_fn = list_running_distros_fn or (lambda: [])
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._scanner = TranscriptScanner(projects_dir, now_fn=self._now_fn)
        self._lock = threading.Lock()
        self._latest: Optional[UsageBreakdown] = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sleep_fn = sleep_fn or self._wake.wait

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def request_refresh(self) -> None:
        self._wake.set()

    def get_latest(self) -> Optional[UsageBreakdown]:
        with self._lock:
            return self._latest

    def rebuild_for_window(self) -> Optional[UsageBreakdown]:
        """Re-aggregate the retained records for the current window without
        rescanning. Switching windows is arithmetic, not I/O."""
        with self._lock:
            previous = self._latest
        if previous is None:
            return None
        snapshot = self._scanner.breakdown(
            self._window(), previous.status, previous.failed_files, previous.failed_rows
        )
        self._publish(snapshot)
        return snapshot

    def _publish(self, snapshot: UsageBreakdown) -> None:
        with self._lock:
            self._latest = snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self._tick_once()
            if self._wake.is_set():
                continue
            self._sleep_fn(self._interval)

    def _unavailable(self) -> UsageBreakdown:
        window = self._window()
        now = self._now_fn()
        return UsageBreakdown(
            status=STATUS_UNAVAILABLE,
            window=window,
            window_start=window_start(window, now),
            scanned_at=now,
        )

    def _tick_once(self) -> None:
        if not self._projects_dir:
            self._publish(self._unavailable())
            return

        if self._distro_name is not None:
            # Same gate ActivityWatcher uses, and for the same reason:
            # touching a \\wsl.localhost path for a stopped distro silently
            # boots it, and a 60-second scanner would then hold it awake
            # indefinitely. A user has no way to connect that to tokitty,
            # which makes it a worse bug than the one this feature fixes.
            if self._distro_name not in self._list_running_distros_fn():
                self._publish(self._unavailable())
                return

        try:
            status, failed_files, failed_rows = self._scanner.scan()
        except Exception:
            # A scan must never take the thread down: a dead worker means a
            # pane frozen on a stale snapshot with nothing to explain it.
            self._publish(self._unavailable())
            return

        self._publish(
            self._scanner.breakdown(self._window(), status, failed_files, failed_rows)
        )
