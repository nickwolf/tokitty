"""Process-scoped cache of which WSL distros are currently running,
shared across every ActivityWatcher instead of each spawning its own
wsl.exe --list --running --quiet. See docs/superpowers/specs/
2026-08-24-accounts-setup-ui-design.md, Shared distro probe.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, FrozenSet, List

SUCCESS_TTL_S = 0.0  # Coalescing-only: no result is ever served from a
                      # time window, so "never restart a stopped distro"
                      # is an invariant rather than best-effort. It used
                      # to be 1.0 (ActivityWatcher.FAST_INTERVAL_S), which
                      # let a caller act on a distro state up to a second
                      # old. Concurrent callers still share one wsl.exe
                      # call -- that is now carried by the generation
                      # counter in get_result, not by this constant, which
                      # is why dropping it to 0.0 was not the one-line
                      # change issue #52 assumed.
FAILURE_BACKOFF_S = 20.0
SUBPROCESS_TIMEOUT_S = 2.0

_NO_CONSOLE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ProbeStatus(Enum):
    CONFIRMED = "confirmed"
    EMPTY = "empty"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    distros: FrozenSet[str]


_UNKNOWN_RESULT = ProbeResult(status=ProbeStatus.UNKNOWN, distros=frozenset())


class RunningDistroProbe:
    """One instance, constructed once per process and injected into every
    ActivityWatcher via list_running_distros_fn=probe.get_running.
    threading.Condition gives single-flight refresh: callers that arrive
    while a probe is in flight coalesce onto its result instead of forming
    a thundering herd. That is the only sharing there is -- a result is
    never replayed to a caller that arrives after the probe finished."""

    def __init__(
        self,
        run: Callable = subprocess.run,
        time_fn: Callable[[], float] = time.monotonic,
        success_ttl: float = SUCCESS_TTL_S,
        failure_backoff: float = FAILURE_BACKOFF_S,
        subprocess_timeout: float = SUBPROCESS_TIMEOUT_S,
    ):
        self._run = run
        self._time_fn = time_fn
        self._success_ttl = success_ttl
        self._failure_backoff = failure_backoff
        self._subprocess_timeout = subprocess_timeout

        self._condition = threading.Condition()
        self._result: ProbeResult = _UNKNOWN_RESULT
        self._result_at: float = float("-inf")
        self._last_failure_at: float = float("-inf")
        self._refreshing = False
        # Bumped on every published result. Waiters block on this
        # advancing rather than on a clock, which is what makes
        # coalescing survive a zero TTL.
        self._generation = 0

    def get_running(self) -> List[str]:
        return list(self.get_result().distros)

    def get_result(self) -> ProbeResult:
        with self._condition:
            now = self._time_fn()
            if self._is_fresh(now):
                return self._result
            if self._refreshing:
                # Park until the in-flight probe publishes, then take its
                # result. The wait is keyed on the generation counter and
                # not on _is_fresh: with SUCCESS_TTL_S at 0.0 nothing is
                # ever fresh, so a freshness re-check here would release
                # every waiter to run its own wsl.exe the moment the first
                # one finished -- serial probes instead of one shared one.
                target = self._generation + 1
                while self._generation < target and self._refreshing:
                    self._condition.wait()
                if self._generation >= target:
                    return self._result
                # The refresher left without publishing (an exception the
                # narrow excepts in _do_refresh don't convert to a result).
                # Fall through and probe rather than return a stale value.
            self._refreshing = True
        try:
            return self._do_refresh()
        finally:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()

    def _is_fresh(self, now: float) -> bool:
        if self._result.status is ProbeStatus.UNKNOWN:
            return (now - self._last_failure_at) < self._failure_backoff
        return (now - self._result_at) < self._success_ttl

    def _do_refresh(self) -> ProbeResult:
        try:
            result = self._run(
                ["wsl.exe", "--list", "--running", "--quiet"],
                capture_output=True,
                timeout=self._subprocess_timeout,
                check=False,
                creationflags=_NO_CONSOLE_FLAGS,
            )
        except (OSError, subprocess.TimeoutExpired):
            now = self._time_fn()
            with self._condition:
                self._result = _UNKNOWN_RESULT
                self._result_at = now
                self._last_failure_at = now
                self._generation += 1
                self._condition.notify_all()
                return self._result

        raw = result.stdout
        text = raw.decode("utf-16-le", errors="ignore") if isinstance(raw, bytes) else raw
        names = frozenset(line.strip() for line in text.splitlines() if line.strip())
        now = self._time_fn()
        with self._condition:
            status = ProbeStatus.CONFIRMED if names else ProbeStatus.EMPTY
            self._result = ProbeResult(status=status, distros=names)
            self._result_at = now
            self._generation += 1
            self._condition.notify_all()
            return self._result
