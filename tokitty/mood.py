"""Pure logic for mapping usage data to the cat's mood and animation state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from tokitty.api import LimitInfo, UsageSnapshot

MOOD_THRESHOLDS: List[Tuple[float, str]] = [
    (25.0, "sleeping"),
    (50.0, "content"),
    (75.0, "interested"),
    (90.0, "alert"),
    (100.0, "panicked"),
]
# The weekly bar climbs for days, so it only reaches alert and panicked
# closer to the cap; at the session thresholds a weekly bar at 92% kept the
# cat panicked for most of a week.
WEEKLY_MOOD_THRESHOLDS: List[Tuple[float, str]] = [
    (25.0, "sleeping"),
    (50.0, "content"),
    (85.0, "interested"),
    (97.0, "alert"),
    (100.0, "panicked"),
]
_MOOD_RANK = {mood: rank for rank, (_, mood) in enumerate(MOOD_THRESHOLDS)}


def _ladder_mood(percent: float, thresholds: List[Tuple[float, str]]) -> str:
    for threshold, mood in thresholds:
        if percent < threshold:
            return mood
    return "panicked"


def compute_mood(session_pct: float, weekly_pct: float) -> Tuple[str, str]:
    """Return (mood_key, driving_tag) from the steady-state mood ladder.

    Each bar has its own ladder (the weekly one is later at the top) and the
    cat shows the worse of the two moods. driving_tag is the bar behind that
    mood: "5h" or "7d", and on equal moods the fuller bar, the session bar
    on a tie. Only meaningful when neither limit is capped -- callers should
    check select_binding_capped_limit() first (Task 9).
    """
    session = _ladder_mood(session_pct, MOOD_THRESHOLDS)
    weekly = _ladder_mood(weekly_pct, WEEKLY_MOOD_THRESHOLDS)
    if _MOOD_RANK[weekly] > _MOOD_RANK[session] or (
            weekly == session and weekly_pct > session_pct):
        return weekly, "7d"
    return session, "5h"


BLOCKED_SEVERITIES = {"exceeded", "blocked", "rate_limited", "over_limit"}

CAPPED_WAKE_WINDOWS = {
    "session": {"stir": 15 * 60, "wake": 3 * 60},
    "weekly": {"stir": 3 * 3600, "wake": 20 * 60},
}


def is_capped(limit: LimitInfo) -> bool:
    """True if this limit is blocking usage right now."""
    if limit.severity.lower() in BLOCKED_SEVERITIES:
        return True
    return limit.percent >= 100.0


def _wake_window_key(kind: str) -> str:
    return "session" if kind == "session" else "weekly"


def select_binding_capped_limit(limits: List[LimitInfo]) -> Optional[LimitInfo]:
    """Return the capped, active limit that resets soonest, or None if
    nothing is currently capped."""
    candidates = [lim for lim in limits if lim.is_active and is_capped(lim) and lim.resets_at is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda lim: lim.resets_at)


@dataclass(frozen=True)
class CappedState:
    substate: str  # "flopped" | "stirring" | "waking"
    time_to_reset: timedelta
    driving_tag: str  # "5h" | "7d"


def compute_capped_substate(binding_limit: LimitInfo, now: Optional[datetime] = None) -> CappedState:
    """Return which sub-state of the capped/wake sequence applies right now."""
    if now is None:
        now = datetime.now(timezone.utc)

    time_to_reset = binding_limit.resets_at - now
    seconds_left = max(time_to_reset.total_seconds(), 0.0)

    windows = CAPPED_WAKE_WINDOWS[_wake_window_key(binding_limit.kind)]

    if seconds_left <= windows["wake"]:
        substate = "waking"
    elif seconds_left <= windows["stir"]:
        substate = "stirring"
    else:
        substate = "flopped"

    driving_tag = "5h" if binding_limit.kind == "session" else "7d"
    return CappedState(substate=substate, time_to_reset=time_to_reset, driving_tag=driving_tag)


def detect_activate(previous: Optional[UsageSnapshot], current: UsageSnapshot) -> bool:
    """True exactly when this poll observes a previously-capped limit clear.

    This is data-driven (comparing two real polls), not a wall-clock guess,
    since the server's actual reset can lag the advertised resets_at.
    """
    if previous is None:
        return False

    was_capped = select_binding_capped_limit(previous.limits) is not None
    is_now_capped = select_binding_capped_limit(current.limits) is not None

    return was_capped and not is_now_capped
