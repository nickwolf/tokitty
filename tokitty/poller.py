"""Background polling of the usage endpoint, decoupled from the UI."""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from tokitty.api import UsageSnapshot
from tokitty.mood import compute_capped_substate, select_binding_capped_limit

POLL_INTERVAL = 120.0
WAKING_POLL_INTERVAL = 20.0
BACKOFF_INITIAL = 30.0
BACKOFF_MAX = 600.0
BACKOFF_JITTER = 0.2


@dataclass(frozen=True)
class PollResult:
    status: str  # "ok" | "stale_token" | "credentials_unreachable" | "ambiguous_credentials" | "api_error"
    snapshot: Optional[UsageSnapshot]
    message: Optional[str]
    fetched_at: datetime
    source_description: Optional[str] = None


class Poller:
    """Runs `fetch_fn` on a daemon thread at a configurable interval,
    exposing the latest PollResult and a thread-safe refresh-now trigger.
    """

    def __init__(
        self,
        fetch_fn: Callable[[], PollResult],
        poll_interval: float = POLL_INTERVAL,
        waking_poll_interval: float = WAKING_POLL_INTERVAL,
        sleep_fn: Optional[Callable[[float], bool]] = None,
    ):
        self._fetch_fn = fetch_fn
        self._poll_interval = poll_interval
        self._waking_poll_interval = waking_poll_interval
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[PollResult] = None
        self._thread: Optional[threading.Thread] = None
        self._sleep_fn = sleep_fn or self._wake_event.wait

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def request_refresh(self) -> None:
        self._wake_event.set()

    def get_latest(self) -> Optional[PollResult]:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        backoff = BACKOFF_INITIAL
        while not self._stop_event.is_set():
            result = self._poll_once()
            with self._lock:
                self._latest = result

            if result.status == "ok":
                backoff = BACKOFF_INITIAL
                interval = self._next_interval(result)
            else:
                interval = backoff
                jitter = backoff * BACKOFF_JITTER * random.random()
                backoff = min(backoff * 2 + jitter, BACKOFF_MAX)

            if self._wake_event.is_set():
                # A refresh was requested while this fetch was in flight --
                # honor it immediately instead of clearing the signal and
                # sleeping out the full interval.
                self._wake_event.clear()
                continue

            self._sleep_fn(interval)

    def _poll_once(self) -> PollResult:
        try:
            return self._fetch_fn()
        except Exception as exc:
            return PollResult(status="api_error", snapshot=None, message=str(exc), fetched_at=datetime.now(timezone.utc))

    def _next_interval(self, result: PollResult) -> float:
        if result.snapshot is None:
            return self._poll_interval

        binding = select_binding_capped_limit(result.snapshot.limits)
        if binding is None:
            return self._poll_interval

        state = compute_capped_substate(binding)
        if state.substate == "waking":
            return self._waking_poll_interval
        return self._poll_interval
