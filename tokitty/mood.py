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


def compute_mood(session_pct: float, weekly_pct: float) -> Tuple[str, str]:
    """Return (mood_key, driving_tag) from the steady-state mood ladder.

    driving_tag is "5h" if the session percent is the larger (or equal)
    of the two, else "7d". Only meaningful when neither limit is capped
    -- callers should check select_binding_capped_limit() first (Task 9).
    """
    driving_tag = "5h" if session_pct >= weekly_pct else "7d"
    percent = max(session_pct, weekly_pct)

    for threshold, mood in MOOD_THRESHOLDS:
        if percent < threshold:
            return mood, driving_tag
    return "panicked", driving_tag


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
