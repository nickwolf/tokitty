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

from tokitty.activity import ActivityTracker, ActivityView
from tokitty.wsl_probe import list_running_distros

FAST_INTERVAL_S = 1.0
SLOW_INTERVAL_S = 20.0

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

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[ActivityView] = None
        self._thread: Optional[threading.Thread] = None
        self._sleep_fn = sleep_fn or self._stop_event.wait

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def get_latest(self) -> Optional[ActivityView]:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._tick_once()
            latest = self.get_latest()
            interval = self._fast_interval if latest is not None and latest.state != "idle" else self._slow_interval
            self._sleep_fn(interval)

    def _resolve(self, value):
        return value() if callable(value) else value

    def _publish(self, view: ActivityView) -> None:
        with self._lock:
            self._latest = view

    def _tick_once(self) -> None:
        now = self._time_fn()
        sessions_dir = self._resolve(self._sessions_dir)
        if not sessions_dir:
            self._publish(ActivityView(state="idle"))
            return

        distro_name = self._resolve(self._distro_name)
        if distro_name is not None:
            # Cached for this tick only: one running-distros probe per
            # loop iteration, never per-file.
            running = self._list_running_distros_fn()
            if distro_name not in running:
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

        self._publish(self._tracker.aggregate(now))
