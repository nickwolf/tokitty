"""Pure text for the hover popup (#130): the limits rows and the cat.

The models rows are described in usage_display.hover_text. Nothing here
imports a GUI toolkit; the popup in tokitty.qt.hover only paints these lines.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from tokitty.burn import PACE_CAPS, PACE_WONT_CAP, Pace
from tokitty.display import format_clock, format_reset_day, format_reset_time, format_time_left


@dataclass(frozen=True)
class LimitHover:
    """What the popup needs about one limit, kept raw so the countdown is
    worked out when the popup shows and not when the poll landed."""
    title: str                  # "Session" | "Week"
    kind: str                   # "session" | "weekly"
    pct: float
    resets_at: Optional[datetime]
    capped: bool
    pace: Pace


@dataclass(frozen=True)
class LimitsHover:
    # Both None when no poll ever succeeded: there are no numbers to show,
    # only the reason below.
    session: Optional[LimitHover]
    weekly: Optional[LimitHover]
    # "Extra usage: $3.62 / $20.00" or "Credits: $12.40 left"; None when the
    # account has no credits data.
    credits: Optional[str] = None
    # Why the latest poll failed, and when the numbers on show were last
    # confirmed. Both None unless the latest poll failed.
    problem: Optional[str] = None
    last_good_at: Optional[datetime] = None


def limit_hover_text(row: LimitHover, now: datetime) -> str:
    """"Session 42%", the reset time with its countdown, and the pace line
    when the burn tracker has one."""
    lines = [f"{row.title} {row.pct:.0f}%"]
    if row.resets_at is not None:
        left = (row.resets_at - now).total_seconds()
        if row.capped:
            lines.append(f"Resets in {format_time_left(left)}")
        else:
            when = format_reset_time(row.resets_at)
            if row.kind == "weekly":
                when = f"{format_reset_day(row.resets_at)}, {when}"
            suffix = f" (in {format_time_left(left)})" if left > 0 else ""
            lines.append(f"Resets {when}{suffix}")
    if not row.capped:
        if row.pace.status == PACE_CAPS and row.pace.caps_at is not None:
            when = format_reset_time(row.pace.caps_at)
            if row.kind == "weekly":
                when = f"{row.pace.caps_at.astimezone().strftime('%a')} {when}"
            lines.append(f"At this pace: caps ~{when}")
        elif row.pace.status == PACE_WONT_CAP:
            lines.append("At this pace: won't cap before reset")
    return "\n".join(lines)


def limits_popup_text(hover: LimitsHover, key: str, now: datetime) -> str:
    """The popup for one limits row: that limit's lines, then the credits row.
    Both rows carry it so it is one hover away whichever bar the pointer is on."""
    row = getattr(hover, key)
    lines = [] if row is None else [limit_hover_text(row, now)]
    if hover.credits is not None:
        lines.append(hover.credits)
    if hover.problem is not None:
        lines.append(f"Poll failed: {hover.problem}")
        if hover.last_good_at is not None:
            lines.append(f"Last good poll: {format_clock(hover.last_good_at, now)}")
    return "\n".join(lines)


def poll_problem(http_status: Optional[int], message: Optional[str]) -> str:
    """A short cause for a failed poll: "rate limited (429)", "server error
    (503)", "timed out", "no network". The message is only consulted when no
    server answered; ApiError carries nothing finer than its text for those."""
    if http_status == 429:
        return "rate limited (429)"
    if http_status is not None and http_status >= 500:
        return f"server error ({http_status})"
    if http_status is not None:
        return f"HTTP error ({http_status})"
    text = (message or "").lower()
    if "timed out" in text:
        return "timed out"
    if "network error" in text:
        return "no network"
    if "invalid json" in text:
        return "unreadable response"
    return "unexpected error"


# ---- the cat ----------------------------------------------------------------

# One phrase per sprite state. "{tool}" and "{elapsed}" are filled in by
# cat_hover_text; a state listed in _TIMED also gets its elapsed time.
_PHRASES = {
    "sleeping": "Sleeping",
    "content": "Content",
    "interested": "Interested",
    "alert": "Alert, usage is high",
    "panicked": "Panicked, nearly capped",
    "confused": "Confused, can't read usage",
    "waking": "Waking up",
    "activate": "Limit just reset",
    "flopped": "Capped, resting",
    "stirring": "Capped, nearly reset",
    "working": "Working",
    "thinking": "Thinking",
    "permission": "Waiting for permission",
    "permission_urgent": "Waiting for permission",
    "permission_frantic": "Waiting for permission",
    "question": "Asking a question",
    "done_hop": "Done",
}
_TIMED = frozenset({"working", "permission", "permission_urgent", "permission_frantic", "question"})


def cat_phase(state: str) -> str:
    """The state with the permission escalations folded into one, so the
    elapsed time does not restart when the cat gets impatient."""
    return "permission" if state.startswith("permission") else state


def format_elapsed(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def cat_hover_text(state: str, tool_label: str, elapsed: Optional[float]) -> str:
    """The cat's popup line: "Working: Bash, 2m", "Waiting for permission, 45s",
    "Thinking". `elapsed` is how long the cat has been in this phase, or None."""
    text = _PHRASES.get(state)
    if text is None:
        text = state.replace("_", " ").capitalize()
    if state == "working" and tool_label:
        text = f"{text}: {tool_label}"
    if state in _TIMED and elapsed is not None and elapsed >= 1:
        text = f"{text}, {format_elapsed(elapsed)}"
    return text
