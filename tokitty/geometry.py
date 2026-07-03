"""Pure window-position math -- no tkinter import, so it's testable
without a GUI toolkit installed."""
from __future__ import annotations

from typing import Tuple


def clamp_position(x: int, y: int, width: int, height: int, screen_w: int, screen_h: int) -> Tuple[int, int]:
    """Clamp a saved window position so the window stays fully on-screen.

    If the window doesn't fit at all (e.g. it's larger than the current
    screen), anchor at the top-left. If a saved position is out of
    bounds (e.g. a disconnected monitor), fall back to near the
    bottom-right corner.
    """
    if width > screen_w or height > screen_h:
        return 0, 0

    max_x = screen_w - width
    max_y = screen_h - height

    if x < 0 or x > max_x or y < 0 or y > max_y:
        margin = 24
        return max(screen_w - width - margin, 0), max(screen_h - height - margin, 0)

    return x, y
