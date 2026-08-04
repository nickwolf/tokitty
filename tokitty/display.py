"""Pure formatting helpers for the UI: countdowns, local times, bar colors.

Kept free of any tkinter import so it can be unit-tested without a GUI
toolkit installed. Deliberately avoids platform-specific strftime flags
like %-I / %-d -- those are glibc/BSD extensions unsupported by the
Windows C runtime, and Windows is Tokitty's primary target platform.
"""
from __future__ import annotations

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

    The weekly form deliberately carries the weekday only -- adding the
    month and day-of-month pushes the string past status_label's 160px
    wrap width, and a wrapped second line falls below the pane's bottom
    edge. A weekday is unambiguous inside a 7-day window.
    """
    when = format_reset_time(caps_at)
    if kind == "session":
        return f"session caps ~{when}"
    return f"week caps ~{caps_at.astimezone().strftime('%a')} {when}"


def resolve_status_text(
    hint_text: Optional[str], credits_text: Optional[str], projection_text: Optional[str]
) -> str:
    """Pick what the single shared status line shows. An error hint always
    wins, then credits, then the burn projection."""
    return hint_text or credits_text or projection_text or ""
