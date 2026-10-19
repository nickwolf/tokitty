"""Account handoff hints (#129): when one account is close to its cap and
another has room, say so on both cats.

Pure: no toolkit and no clock. The caller (app_core.apply_handoff) builds one Standing per
unit, carries the near-cap flags between ticks and applies the hints. Hints
only ever add a status line and, for a resting cat, a nudge to "interested";
they never change which ladder state a cat is in (rule zero).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

# Near cap: entered at either threshold or a cap, left only once both are
# clear of the lower pair, so a percentage hovering near 85 does not flicker.
NEAR_SESSION_ENTER = 85.0
NEAR_WEEKLY_ENTER = 90.0
NEAR_SESSION_LEAVE = 80.0
NEAR_WEEKLY_LEAVE = 85.0
# Has room: comfortably away from both limits and not capped.
ROOM_SESSION_BELOW = 50.0
ROOM_WEEKLY_BELOW = 70.0


@dataclass(frozen=True)
class Standing:
    """One account's place in the comparison. `confirmed` is False when the
    numbers cannot be trusted (a dimmed display), so the unit counts as
    neither near cap nor roomy."""

    key: str
    label: str
    session_pct: float
    weekly_pct: float
    capped: bool
    confirmed: bool = True


@dataclass(frozen=True)
class Hint:
    """`kind` is "near" (this account is close to its cap; `text` names the
    account with room) or "room" (this account has room; nothing to say)."""

    kind: str
    text: str = ""


def _enters_near(s: Standing) -> bool:
    return s.capped or s.session_pct >= NEAR_SESSION_ENTER or s.weekly_pct >= NEAR_WEEKLY_ENTER


def _stays_near(s: Standing) -> bool:
    return s.capped or s.session_pct >= NEAR_SESSION_LEAVE or s.weekly_pct >= NEAR_WEEKLY_LEAVE


def _has_room(s: Standing) -> bool:
    return not s.capped and s.session_pct < ROOM_SESSION_BELOW and s.weekly_pct < ROOM_WEEKLY_BELOW


def handoff_hints(
    standings: Sequence[Standing], near_flags: Dict[str, bool]
) -> Tuple[Dict[str, Hint], Dict[str, bool]]:
    """Hints by unit key, and the near-cap flags to carry to the next tick.
    Every confirmed account is compared with every other, across providers.
    An unconfirmed standing gets no hint and keeps its previous flag."""
    flags = dict(near_flags)
    near: List[Standing] = []
    roomy: List[Standing] = []
    for s in standings:
        if not s.confirmed:
            continue
        is_near = _stays_near(s) if near_flags.get(s.key, False) else _enters_near(s)
        flags[s.key] = is_near
        if is_near:
            near.append(s)
        elif _has_room(s):
            roomy.append(s)
    if not near or not roomy:
        return {}, flags
    # min() keeps the first of equals, so a tie goes to the earlier account.
    best = min(roomy, key=lambda s: s.session_pct)
    hints = {s.key: Hint("near", f"{best.label} has room") for s in near}
    hints.update({s.key: Hint("room") for s in roomy})
    return hints, flags
