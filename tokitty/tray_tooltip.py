"""The tray icon's tooltip text. Pure Python: no Qt, no I/O.

One line per account, kept under the length Windows allows for a tray
tooltip. Whole lines are dropped and counted rather than cut mid-number.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

# Windows truncates a tray tooltip past 127 characters (NOTIFYICONDATA's
# szTip holds 128 including the terminator).
MAX_TOOLTIP = 127
DEFAULT_TOOLTIP = "Tokitty"
NO_DATA_TEXT = "no data yet"

# (session %, weekly %, the reset phrase or "" when unknown)
TrayUsage = Tuple[float, float, str]


def usage_line(label: str, usage: Optional[TrayUsage]) -> str:
    if usage is None:
        return f"{label}: {NO_DATA_TEXT}"
    session_pct, weekly_pct, reset = usage
    line = f"{label}: session {round(session_pct)}%, week {round(weekly_pct)}%"
    return f"{line}, {reset}" if reset else line


def fit_tooltip(lines: List[str], limit: int = MAX_TOOLTIP) -> str:
    """The lines joined by newlines, or as many whole leading lines as fit
    plus "+N more". Always at least one line, cut with an ellipsis if even
    that one is over the limit."""
    if not lines:
        return DEFAULT_TOOLTIP
    if len("\n".join(lines)) <= limit:
        return "\n".join(lines)
    for kept in range(len(lines) - 1, 0, -1):
        text = "\n".join(lines[:kept] + [f"+{len(lines) - kept} more"])
        if len(text) <= limit:
            return text
    first = lines[0]
    return first if len(first) <= limit else first[: limit - 1] + "…"


def tooltip_text(rows: List[Tuple[str, Optional[TrayUsage]]]) -> str:
    """`rows` is (label, usage) per account in pane order."""
    return fit_tooltip([usage_line(label, usage) for label, usage in rows])
