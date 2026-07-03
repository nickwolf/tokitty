"""Pure logic for mapping usage data to the cat's mood and animation state."""
from __future__ import annotations

from typing import List, Tuple

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
