"""Watch the hook's pending/ directory for permission requests and answer them.

The producer is tokitty/hook_writer.py: while a PermissionRequest waits it keeps
<tokitty_dir>/pending/<nonce>.json fresh by touching its mtime (the heartbeat),
and it accepts an answer only from <tokitty_dir>/decisions/<nonce>.json whose
nonce, session id and digest match. One PendingWatcher serves one account.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from tokitty.wsl_probe import list_running_distros

POLL_INTERVAL_S = 0.5
LIVE_MAX_AGE_S = 30.0
# No live hook holds a claim or a decision past its 590 s cap.
STRAY_MAX_AGE_S = 660.0
ENABLED_MARKER = "streamdock.enabled"

_PENDING_NAME = re.compile(r"[0-9a-f]{16}\.json")
_REQUIRED = ("v", "nonce", "session_id", "tool_use_id", "tool_name", "tool_input", "digest", "preview", "started")
_STR_FIELDS = ("nonce", "session_id", "tool_use_id", "tool_name", "digest", "preview")

DistroNameArg = Union[None, str, Callable[[], Optional[str]]]


@dataclass(frozen=True)
class PendingRequest:
    nonce: str
    session_id: str
    tool_use_id: str
    tool_name: str
    tool_input: dict
    digest: str
    preview: str
    started: float
    account_index: int = 0
    cwd: Optional[str] = None
    always_rule: Optional[str] = None


def _default_list_files(directory: Union[str, Path]) -> List[Path]:
    try:
        return sorted(Path(directory).iterdir())
    except OSError:
        return []


def _default_read_file(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _default_stat(path: Path) -> float:
    return os.stat(path).st_mtime


def _default_remove(path: Path) -> None:
    os.remove(path)


def _parse(text: str, stem: str, account_index: int) -> Optional[PendingRequest]:
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict) or any(k not in data for k in _REQUIRED):
        return None
    if data["nonce"] != stem:
        return None
    if any(not isinstance(data[k], str) or not data[k] for k in _STR_FIELDS):
        return None
    started = data["started"]
    if isinstance(started, bool) or not isinstance(started, (int, float)):
        return None
    if not isinstance(data["tool_input"], dict):
        return None
    cwd = data.get("cwd")
    always_rule = data.get("always_rule")
    return PendingRequest(
        nonce=data["nonce"],
        session_id=data["session_id"],
        tool_use_id=data["tool_use_id"],
        tool_name=data["tool_name"],
        tool_input=data["tool_input"],
        digest=data["digest"],
        preview=data["preview"],
        started=float(started),
        account_index=account_index,
        cwd=cwd if isinstance(cwd, str) else None,
        always_rule=always_rule if isinstance(always_rule, str) and always_rule else None,
    )


class PendingWatcher:
    """One daemon thread per account. Polls pending/ only while active.

    Mirrors ActivityWatcher: a single thread does tick, sleep, tick, so reads
    never stack, and a stopped WSL distro is never touched (reading its
    \\\\wsl.localhost path would boot it).
    """

    def __init__(
        self,
        tokitty_dir: Union[str, Path, Callable[[], Optional[Union[str, Path]]]],
        *,
        account_index: int = 0,
        distro_name: DistroNameArg = None,
        list_running_distros_fn: Callable[[], List[str]] = list_running_distros,
        list_files_fn: Callable[[Union[str, Path]], List[Path]] = _default_list_files,
        read_file_fn: Callable[[Path], str] = _default_read_file,
        stat_fn: Callable[[Path], float] = _default_stat,
        remove_fn: Callable[[Path], None] = _default_remove,
        time_fn: Optional[Callable[[], float]] = None,
        monotonic_fn: Optional[Callable[[], float]] = None,
        interval: float = POLL_INTERVAL_S,
        sleep_fn: Optional[Callable[[float], bool]] = None,
    ):
        self._tokitty_dir = tokitty_dir
        self._account_index = account_index
        self._distro_name = distro_name
        self._list_running_distros_fn = list_running_distros_fn
        self._list_files_fn = list_files_fn
        self._read_file_fn = read_file_fn
        self._stat_fn = stat_fn
        self._remove_fn = remove_fn
        self._time_fn = time_fn or time.time
        self._monotonic_fn = monotonic_fn or time.monotonic
        # path -> (last mtime seen, monotonic time it was first seen with that value).
        # An mtime is never compared with the local wall clock: the hook writes it
        # in WSL, whose clock can drift from Windows, so liveness is "has the mtime
        # changed within the last N seconds of this watcher's own clock".
        self._seen: Dict[str, Tuple[float, float]] = {}
        self._interval = interval

        self._stop_event = threading.Event()
        self._active = threading.Event()
        self._lock = threading.Lock()
        self._pending: List[PendingRequest] = []
        self._thread: Optional[threading.Thread] = None
        self._sleep_fn = sleep_fn or self._stop_event.wait

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def set_active(self, active: bool) -> None:
        if active:
            self._active.set()
            return
        self._active.clear()
        self._seen = {}
        with self._lock:
            self._pending = []

    def get_pending(self) -> List[PendingRequest]:
        with self._lock:
            return list(self._pending)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self._active.is_set():
                self._tick_once()
            self._sleep_fn(self._interval)

    def _resolve(self, value):
        return value() if callable(value) else value

    def _publish(self, requests: List[PendingRequest]) -> None:
        with self._lock:
            if self._active.is_set():
                self._pending = requests

    def _try_remove(self, path: Path) -> None:
        if not self._active.is_set():
            return
        try:
            self._remove_fn(path)
        except OSError:
            pass

    def _tick_once(self) -> None:
        if not self._active.is_set():
            return
        tokitty_dir = self._resolve(self._tokitty_dir)
        if not tokitty_dir:
            self._seen = {}
            self._publish([])
            return
        distro_name = self._resolve(self._distro_name)
        if distro_name is not None and distro_name not in self._list_running_distros_fn():
            self._seen = {}
            self._publish([])
            return

        # A file first seen at startup whose hook is already dead stays visible for
        # up to LIVE_MAX_AGE_S; a decision written to a dead hook is harmless.
        now = self._monotonic_fn()
        present: Set[str] = set()
        base = Path(tokitty_dir)
        live: List[PendingRequest] = []
        for path in self._list_files_fn(base / "pending"):
            name = Path(path).name
            if name.endswith(".claim"):
                self._sweep(path, now, STRAY_MAX_AGE_S, present)
            elif _PENDING_NAME.fullmatch(name):
                try:
                    mtime = self._stat_fn(path)
                except OSError:
                    continue
                if self._unchanged_for(path, mtime, now, present) >= LIVE_MAX_AGE_S:
                    self._try_remove(path)
                    continue
                try:
                    req = _parse(self._read_file_fn(path), name[: -len(".json")], self._account_index)
                except Exception:
                    continue
                if req is not None:
                    live.append(req)
        for path in self._list_files_fn(base / "decisions"):
            if _PENDING_NAME.fullmatch(Path(path).name):
                self._sweep(path, now, STRAY_MAX_AGE_S, present)
        self._seen = {k: v for k, v in self._seen.items() if k in present}
        live.sort(key=lambda r: (r.started, r.nonce))
        self._publish(live)

    def _unchanged_for(self, path: Path, mtime: float, now: float, present: Set[str]) -> float:
        """Monotonic seconds this file's mtime has held its current value; a new file counts as just changed."""
        key = str(path)
        present.add(key)
        prev = self._seen.get(key)
        if prev is None or prev[0] != mtime:
            self._seen[key] = (mtime, now)
            return 0.0
        return now - prev[1]

    def _sweep(self, path: Path, now: float, max_age: float, present: Set[str]) -> None:
        try:
            mtime = self._stat_fn(path)
        except OSError:
            return
        if self._unchanged_for(path, mtime, now, present) > max_age:
            self._try_remove(path)

    def write_decision(self, req: PendingRequest, behavior: str) -> None:
        write_decision(self._resolve(self._tokitty_dir), req, behavior, now=self._time_fn())


def write_decision(
    tokitty_dir: Union[str, Path],
    req: PendingRequest,
    behavior: str,
    *,
    now: Optional[float] = None,
) -> None:
    """Write decisions/<nonce>.json atomically with the fields hook_writer checks."""
    if behavior not in ("allow", "deny", "always"):
        raise ValueError(f"behavior must be 'allow', 'deny' or 'always', not {behavior!r}")
    if behavior == "always" and not req.always_rule:
        raise ValueError("always needs a request with an always_rule")
    directory = Path(tokitty_dir) / "decisions"
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "nonce": req.nonce,
        "session_id": req.session_id,
        "digest": req.digest,
        "behavior": behavior,
        "decided_at": time.time() if now is None else now,
    }
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, directory / f"{req.nonce}.json")
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def touch_enabled(tokitty_dir: Union[str, Path]) -> None:
    """Create or refresh the marker that tells the hook a deck is connected."""
    try:
        base = Path(tokitty_dir)
        base.mkdir(parents=True, exist_ok=True)
        marker = base / ENABLED_MARKER
        marker.touch()
    except OSError:
        pass


def clear_enabled(tokitty_dir: Union[str, Path]) -> None:
    try:
        (Path(tokitty_dir) / ENABLED_MARKER).unlink()
    except OSError:
        pass
