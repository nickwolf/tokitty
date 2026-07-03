"""Pure formatting helpers for the UI: countdowns, local times, bar colors.

Kept free of any tkinter import so it can be unit-tested without a GUI
toolkit installed. Deliberately avoids platform-specific strftime flags
like %-I / %-d -- those are glibc/BSD extensions unsupported by the
Windows C runtime, and Windows is Tokitty's primary target platform.
"""
from __future__ import annotations

from datetime import datetime

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
