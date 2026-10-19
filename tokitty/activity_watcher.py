"""Background thread that watches the hook-writer session files and
publishes an ActivityView the UI can read, mirroring Poller's
threading/injectability shape (worker thread publishes into a
lock-protected snapshot; nothing here ever touches tkinter widgets).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from tokitty.activity import ActivityTracker, ActivityView, SessionView
from tokitty.wsl_probe import list_running_distros

FAST_INTERVAL_S = 1.0
SLOW_INTERVAL_S = 20.0
# While a WSL session is active, a "running" probe is trusted this long (each probe
# spawns wsl.exe, ~1.8% of a core at 1 s). Trade-off: up to this long after a manual
# `wsl --terminate`/`--shutdown`, a read can start the distro again.
RUNNING_TRUST_S = 5.0

SessionsDirArg = Union[None, str, Path, Callable[[], Optional[Union[str, Path]]]]
DistroNameArg = Union[None, str, Callable[[], Optional[str]]]


def _default_list_files(sessions_dir: Union[str, Path]) -> List[Path]:
    try:
        return sorted(Path(sessions_dir).glob("*.json"))
    except OSError:
        return []


def _default_read_file(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _default_delete_file(sessions_dir: Union[str, Path], session_id: str) -> None:
    try:
        (Path(sessions_dir) / f"{session_id}.json").unlink()
    except OSError:
        pass


class ActivityWatcher:
    """One instance per account (single default account for now).

    Loop structure note (single in-flight read, skip-if-busy): this is a
    single dedicated thread that does one tick, then sleeps, then does
    the next tick -- there is no timer callback that could re-fire while
    a previous tick's read is still in flight. A slow UNC read simply
    delays the *next* tick by however long it took; reads never stack.
    """

    def __init__(
        self,
        sessions_dir: SessionsDirArg,
        tracker: ActivityTracker,
        *,
        distro_name: DistroNameArg = None,
        list_running_distros_fn: Callable[[], List[str]] = list_running_distros,
        list_files_fn: Callable[[Union[str, Path]], List[Path]] = _default_list_files,
        read_file_fn: Callable[[Path], str] = _default_read_file,
        delete_file_fn: Callable[[Union[str, Path], str], None] = _default_delete_file,
        time_fn: Callable[[], float] = None,
        fast_interval: float = FAST_INTERVAL_S,
        slow_interval: float = SLOW_INTERVAL_S,
        sleep_fn: Optional[Callable[[float], bool]] = None,
        running_trust_s: float = RUNNING_TRUST_S,
        monotonic_fn: Optional[Callable[[], float]] = None,
        name: Optional[str] = None,
    ):
        import time

        self._sessions_dir = sessions_dir
        self._tracker = tracker
        self._distro_name = distro_name
        self._list_running_distros_fn = list_running_distros_fn
        self._list_files_fn = list_files_fn
        self._read_file_fn = read_file_fn
        self._delete_file_fn = delete_file_fn
        self._time_fn = time_fn or time.time
        self._fast_interval = fast_interval
        self._slow_interval = slow_interval
        self._idle_cap: Optional[float] = None
        self._running_trust_s = running_trust_s
        self._monotonic_fn = monotonic_fn or time.monotonic
        self._name = name
        # (distro, monotonic time) of the last probe that found it running, else None.
        self._running_seen: Optional[tuple] = None

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[ActivityView] = None
        self._sessions: List[SessionView] = []
        self._thread: Optional[threading.Thread] = None
        self._sleep_fn = sleep_fn or self._stop_event.wait

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def set_idle_cap(self, seconds: Optional[float]) -> None:
        """Poll at least this often while idle (None restores the slow interval).
        The Stream Dock sets it while connected, so a new session gets a key quickly."""
        self._idle_cap = seconds

    def get_latest(self) -> Optional[ActivityView]:
        with self._lock:
            return self._latest

    def get_sessions(self) -> List[SessionView]:
        with self._lock:
            return list(self._sessions)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._tick_once()
            latest = self.get_latest()
            interval = self._fast_interval if latest is not None and latest.state != "idle" else self._slow_interval
            cap = self._idle_cap
            if cap is not None:
                interval = min(interval, cap)
            self._sleep_fn(interval)

    def _resolve(self, value):
        return value() if callable(value) else value

    def _publish(self, view: ActivityView, sessions: Optional[List[SessionView]] = None) -> None:
        with self._lock:
            self._latest = view
            self._sessions = sessions or []

    def _trust_running(self, distro_name: str) -> bool:
        seen = self._running_seen
        if seen is None or seen[0] != distro_name:
            return False
        latest = self.get_latest()
        if latest is None or latest.state == "idle":
            return False
        return self._monotonic_fn() - seen[1] < self._running_trust_s

    def _tick_once(self) -> None:
        now = self._time_fn()
        sessions_dir = self._resolve(self._sessions_dir)
        if not sessions_dir:
            self._publish(ActivityView(state="idle"))
            return

        distro_name = self._resolve(self._distro_name)
        if distro_name is not None:
            # One running-distros probe per loop iteration, never per-file,
            # and skipped while active and recently seen running.
            if not self._trust_running(distro_name):
                running = self._list_running_distros_fn()
                if distro_name in running:
                    self._running_seen = (distro_name, self._monotonic_fn())
                else:
                    self._running_seen = None
                    # WSL-respectful: never touch the \\wsl.localhost path for
                    # a stopped distro -- that silently boots it and defeats
                    # WSL's idle auto-shutdown.
                    self._publish(ActivityView(state="idle"))
                    return

        records: Dict[str, dict] = {}
        for path in self._list_files_fn(sessions_dir):
            try:
                raw = self._read_file_fn(path)
                data = json.loads(raw)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            session_id = Path(path).stem
            records[session_id] = data

        self._tracker.observe(records, now)
        for session_id in self._tracker.stale_session_ids(now):
            try:
                self._delete_file_fn(sessions_dir, session_id)
            except Exception:
                pass

        self._publish(self._tracker.aggregate(now), self._tracker.sessions(now))
