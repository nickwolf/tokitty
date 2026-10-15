"""Opacity levels for the card background.

LEVELS are the steps the Settings window offers, DEFAULT_LEVEL is full
opacity, and the helpers clamp a stored value to a supported level and turn it
into a window alpha. No GUI toolkit is imported, so the level arithmetic stays
testable on every CI OS.
"""
from __future__ import annotations

from typing import Tuple

LEVELS: Tuple[int, ...] = (100, 90, 80, 70, 60, 50)
DEFAULT_LEVEL = 100
MIN_LEVEL = min(LEVELS)

# MIN_LEVEL stays well above zero on purpose: a fully transparent card would
# be unrecoverable without the tray icon, which is itself optional.


def clamp_level(value) -> int:
    """Nearest supported level, or DEFAULT_LEVEL for anything unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_LEVEL
    return min(LEVELS, key=lambda level: (abs(level - value), level))


def level_label(level: int) -> str:
    return f"{level}%"


def alpha_for(level: int) -> float:
    return clamp_level(level) / 100.0


def effective_level(opacity: int, accented: bool) -> int:
    """The level actually applied to the card window.

    A pending permission prompt forces full opacity: the accent exists to be
    noticed, and a faded version of it is quieter than the state it
    announces. Opacity is a property of the whole toplevel while the accent
    is per-pane, so with several panes one accented pane makes all of them
    opaque.
    """
    return 100 if accented else clamp_level(opacity)
