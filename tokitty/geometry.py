"""Pure window-position math -- no tkinter import, so it's testable
without a GUI toolkit installed."""
from __future__ import annotations

from typing import Tuple

# left, top, right, bottom in virtual-desktop coordinates, the shape of a
# Win32 RECT. right and bottom are exclusive.
Area = Tuple[int, int, int, int]

MARGIN = 24


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
        return max(screen_w - width - MARGIN, 0), max(screen_h - height - MARGIN, 0)

    return x, y


def clamp_to_area(x: int, y: int, width: int, height: int, area: Area) -> Tuple[int, int]:
    """clamp_position against an area that need not start at the origin,
    such as a secondary monitor or a work area with the taskbar on the
    left or top. Translates into the area's own coordinates and back."""
    left, top, right, bottom = area
    local_x, local_y = clamp_position(x - left, y - top, width, height, right - left, bottom - top)
    return local_x + left, local_y + top


def default_position(width: int, height: int, area: Area) -> Tuple[int, int]:
    """Near the bottom-right corner of the area, the spot a first launch
    and an unusable saved position both land on."""
    left, top, right, bottom = area
    return max(right - width - MARGIN, left), max(bottom - height - MARGIN, top)
