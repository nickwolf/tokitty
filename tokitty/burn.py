"""Rolling burn-rate samples and cap projection.

Pure logic -- no tkinter, no I/O -- so it is unit-testable without a GUI
toolkit installed, the same rule display.py and geometry.py follow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from tokitty.api import UsageSnapshot
from tokitty.mood import select_binding_capped_limit

# Trailing time window the rate is measured over. Time-based rather than
# last-N because the poll interval switches between 120s and 20s (waking),
# so a fixed sample count would span wildly different durations.
#
# 10 minutes is a deliberate responsiveness choice, not a smoothing one:
# the window IS the decay time. A first-to-last rate keeps reporting a
# burn until every burning sample ages out, so a longer window means a
# projection that lingers that long after you stop -- and that takes just
# as long to notice a new burst. At 600s the estimate visibly wobbles
# poll to poll; that is the accepted trade.
WINDOW_SECONDS = 10 * 60
MIN_SAMPLES = 2
MIN_SPAN_SECONDS = 5 * 60


@dataclass(frozen=True)
class Sample:
    fetched_at: datetime
    session_pct: float
    weekly_pct: float
    session_resets_at: Optional[datetime]
    weekly_resets_at: Optional[datetime]
    capped: bool


@dataclass(frozen=True)
class Projection:
    kind: str  # "session" | "weekly"
    caps_at: datetime


def _pct(sample: Sample, kind: str) -> float:
    return sample.session_pct if kind == "session" else sample.weekly_pct


def _reset(sample: Sample, kind: str) -> Optional[datetime]:
    return sample.session_resets_at if kind == "session" else sample.weekly_resets_at


class BurnTracker:
    """A per-account rolling buffer of usage samples. Memory-only by
    design: a restart re-enters warm-up rather than adding a file to the
    state dir."""

    def __init__(self, window_seconds: float = WINDOW_SECONDS):
        self._window_seconds = window_seconds
        self._samples: List[Sample] = []

    @property
    def samples(self) -> List[Sample]:
        return list(self._samples)

    def add(self, snapshot: UsageSnapshot) -> None:
        # tick() runs every 500ms and re-renders the *cached* snapshot
        # object whenever a poll fails, so the same fetched_at arrives
        # hundreds of times. Rejecting anything not strictly newer both
        # dedupes that and guards against out-of-order arrival.
        if self._samples and snapshot.fetched_at <= self._samples[-1].fetched_at:
            return

        self._samples.append(
            Sample(
                fetched_at=snapshot.fetched_at,
                session_pct=snapshot.session_pct,
                weekly_pct=snapshot.weekly_pct,
                session_resets_at=snapshot.session_resets_at,
                weekly_resets_at=snapshot.weekly_resets_at,
                capped=select_binding_capped_limit(snapshot.limits) is not None,
            )
        )

        cutoff = snapshot.fetched_at - timedelta(seconds=self._window_seconds)
        self._samples = [s for s in self._samples if s.fetched_at >= cutoff]

    def project(self, now: datetime) -> Optional[Projection]:
        """Return the nearer of the session/weekly cap projections, or
        None when no cap is credibly coming before the window resets.

        The window IS the decay time (see WINDOW_SECONDS above), so this
        filters by the caller's clock on every call -- not just at add()
        time -- so a projection decays even when polls stop succeeding
        and add() is no longer being called.
        """
        cutoff = now - timedelta(seconds=self._window_seconds)
        recent = [s for s in self._samples if s.fetched_at >= cutoff]
        if len(recent) < MIN_SAMPLES:
            return None

        newest = recent[-1]
        if newest.capped:
            return None

        candidates = [
            projection
            for projection in (self._project_kind(kind, recent, newest, now) for kind in ("session", "weekly"))
            if projection is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda projection: projection.caps_at)

    def _project_kind(self, kind: str, recent: List[Sample], newest: Sample, now: datetime) -> Optional[Projection]:
        resets_at = _reset(newest, kind)
        if resets_at is None:
            return None

        # Walk back only through samples from the SAME window. A changed
        # resets_at means this limit reset, and measuring across that
        # boundary would read as a large negative burn.
        oldest = newest
        for sample in reversed(recent[:-1]):
            if _reset(sample, kind) != resets_at:
                break
            oldest = sample

        elapsed = (newest.fetched_at - oldest.fetched_at).total_seconds()
        if elapsed < MIN_SPAN_SECONDS:
            return None

        rate = (_pct(newest, kind) - _pct(oldest, kind)) / elapsed
        if rate <= 0:
            return None

        remaining = 100.0 - _pct(newest, kind)
        if remaining <= 0:
            return None

        caps_at = newest.fetched_at + timedelta(seconds=remaining / rate)
        if caps_at >= resets_at or caps_at <= now:
            return None
        return Projection(kind=kind, caps_at=caps_at)
