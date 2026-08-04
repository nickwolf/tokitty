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
