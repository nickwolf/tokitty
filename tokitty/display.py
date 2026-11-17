"""Pure formatting helpers for the UI: countdowns, local times, bar colors.

Kept free of any tkinter import so it can be unit-tested without a GUI
toolkit installed. Deliberately avoids platform-specific strftime flags
like %-I / %-d -- those are glibc/BSD extensions unsupported by the
Windows C runtime, and Windows is Tokitty's primary target platform.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

GREEN = "#4caf6b"
AMBER = "#e0a838"
RED = "#e05252"


def bar_color(percent: float) -> str:
    if percent >= 80:
        return RED
    if percent >= 50:
        return AMBER
    return GREEN


def format_countdown(seconds_left: float) -> str:
    seconds_left = max(int(seconds_left), 0)
    hours, remainder = divmod(seconds_left, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def format_time_left(seconds_left: float) -> str:
    """The two coarsest units of a wait, for the hover popup: "2d 3h",
    "2h 14m", "45m", "30s". Truncates, so it never promises more time than
    there is."""
    seconds_left = max(int(seconds_left), 0)
    days, remainder = divmod(seconds_left, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


_COUNTDOWN = re.compile(r"\d+[hms]( |$)")


def reset_phrase(reset_text: str) -> str:
    """The refined card's reset line: "resets 6:10 PM", "resets in 2h 14m 05s"
    for a capped countdown, and nothing when the reset is unknown."""
    if not reset_text or reset_text == "\u2014":
        return ""
    if _COUNTDOWN.match(reset_text):
        return "resets in " + reset_text
    return "resets " + reset_text


def format_reset_time(dt: datetime) -> str:
    local = dt.astimezone()
    hour_12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    return f"{hour_12}:{local.minute:02d} {period}"


def format_reset_day(dt: datetime) -> str:
    local = dt.astimezone()
    return f"{local.strftime('%a')} {local.strftime('%b')} {local.day}"


def format_projection(kind: str, caps_at: datetime) -> str:
    """Render a cap projection for the pane's status line.

    The weekly form deliberately carries the weekday only. Adding the
    month and day of month makes the string wider than the 158 px stats
    column, so it would be elided or wrapped. A weekday is unambiguous
    inside a 7-day window.
    """
    when = format_reset_time(caps_at)
    if kind == "session":
        return f"session caps ~{when}"
    return f"week caps ~{caps_at.astimezone().strftime('%a')} {when}"


def format_credits(snapshot) -> Optional[str]:
    """The credits line for a snapshot: "$3.62 / $20.00" for Claude's extra
    usage, "$12.40 left" for a Codex balance. None when there is nothing
    spent or left to report."""
    if snapshot.credits_used is not None and snapshot.credits_used > 0 and snapshot.credits_limit is not None:
        return f"${snapshot.credits_used:.2f} / ${snapshot.credits_limit:.2f}"
    if snapshot.credits_balance is not None and snapshot.credits_balance > 0:
        return f"${snapshot.credits_balance:.2f} left"
    return None


def resolve_status_text(
    hint_text: Optional[str],
    credits_text: Optional[str],
    projection_text: Optional[str],
    stale_text: Optional[str] = None,
    handoff_text: Optional[str] = None,
) -> str:
    """Pick what the single shared status line shows. An error hint always
    wins, then the observation age, then the handoff hint, then credits, then
    the burn projection.

    Age outranks both of the lines below it because they describe the same
    numbers it is qualifying: a projection computed from a snapshot hours
    old is the most confidently wrong thing the pane can say.
    """
    return hint_text or stale_text or handoff_text or credits_text or projection_text or ""


# How old a snapshot has to be before the pane says so. A Claude poll
# stamps its snapshot at parse time, so its age is always a second or two
# and this never fires. A Codex snapshot is stamped with the transcript
# event's own time and only advances when Codex takes a turn, so the age
# is real information rather than a fault.
STALE_AFTER_SECONDS = 10 * 60


def format_observed_at(observed_at: Optional[datetime], now: datetime) -> Optional[str]:
    """"as of 4:12 PM" once a snapshot is older than STALE_AFTER_SECONDS,
    with the weekday added once it is no longer today. None while it is
    fresh, and None for a timestamp in the future, which means a clock
    disagreement rather than an age worth reporting.

    The weekday alone is ambiguous past a week, which nothing currently
    reaches: the Codex reader only looks back 8 days and a 7-day limit
    window resets inside that.
    """
    if observed_at is None:
        return None
    age = (now - observed_at).total_seconds()
    if age < STALE_AFTER_SECONDS:
        return None
    return f"as of {format_clock(observed_at, now)}"


def format_clock(when: datetime, now: datetime) -> str:
    """"4:12 PM" for today, "Tue 4:12 PM" for any other day."""
    local = when.astimezone()
    if local.date() == now.astimezone().date():
        return format_reset_time(when)
    return f"{local.strftime('%a')} {format_reset_time(when)}"


# Separates the harness from the cat's own name in a pane label. A middle
# dot rather than a slash or a colon: it reads as one label at 8pt instead
# of two fields, and costs three pixels.
PROVIDER_SEPARATOR = "\u00b7"


def format_pane_label(label: str, provider_kind: Optional[str]) -> str:
    """The pane's top-right label. `provider_kind` is None when the window
    holds only one harness, which is when naming it on every pane would be
    noise rather than information."""
    if not provider_kind:
        return label
    if not label:
        return provider_kind
    return f"{provider_kind}{PROVIDER_SEPARATOR}{label}"
