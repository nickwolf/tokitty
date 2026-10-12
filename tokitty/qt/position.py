"""Position.json for the Qt widget: loading, saving and placing, all in
plain data so none of it needs Qt.

Qt works in logical pixels. A file the Tk build wrote has no "unit" key and
holds the coordinates of a system-DPI-aware process, so on Windows and Linux
it is converted per monitor (see from_tk); the Qt build writes
"unit": "logical" and its files are used as they are. x and y are always the
top-left of the first card, not of the window, because the window's own
top-left moves with the margins.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

from tokitty.geometry import Area, clamp_to_area, default_position

UNIT = "logical"

Rect4 = Tuple[int, int, int, int]  # x, y, width, height


@dataclass(frozen=True)
class ScreenInfo:
    native_rect: Rect4    # top-left as Qt reports it, size in device pixels
    logical_rect: Rect4   # Qt's own geometry()
    ratio: float          # devicePixelRatio()


def _contains(rect: Rect4, x: int, y: int) -> bool:
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def from_tk(x: int, y: int, screens: Sequence[ScreenInfo], system_ratio: float,
            platform: str) -> Tuple[int, int]:
    """Convert coordinates a Tk build saved into Qt's logical ones.

    macOS: Tk reports points, the same unit as Qt, so identity. Elsewhere
    Tk's x and y came from a system-aware process, in physical pixels. Qt
    keeps each screen's top-left where it is and scales only inside the
    screen, so one division of the whole point would land between screens.
    Find the screen whose native rect holds the point and convert relative
    to its corner. A point on no screen comes back unchanged and clamping
    decides what to do with it.
    """
    if platform == "darwin":
        return x, y
    for screen in screens:
        if _contains(screen.native_rect, x, y):
            nx, ny = screen.native_rect[0], screen.native_rect[1]
            lx, ly = screen.logical_rect[0], screen.logical_rect[1]
            return (round(lx + (x - nx) / system_ratio),
                    round(ly + (y - ny) / system_ratio))
    return x, y


def _int(value) -> Optional[int]:
    # bool is an int subclass; True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def load(path, screens: Sequence[ScreenInfo], system_ratio: float,
         platform: str) -> Optional[Tuple[int, int]]:
    """The saved first-card position in logical pixels, or None when the
    file is missing or unusable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    x, y = _int(data.get("x")), _int(data.get("y"))
    if x is None or y is None:
        return None
    unit = data.get("unit")
    if unit == UNIT:
        return x, y
    if unit is None:
        return from_tk(x, y, screens, system_ratio, platform)
    return None


def save(path, x: int, y: int) -> bool:
    """Write the first card's position atomically. Failure is cosmetic, as
    in the Tk build, so it is reported but never raised."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps({"x": x, "y": y, "unit": UNIT}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def place(first_card: Optional[Tuple[int, int]], grid_size: Tuple[int, int],
          margins: Tuple[int, int, int, int], area: Area) -> Tuple[int, int]:
    """The window position that puts the first card at first_card.

    The clamp uses the grid's rectangle, not the window's, so a transparent
    margin may hang off screen but no card can. None means no saved
    position: the default corner.
    """
    if first_card is None:
        x, y = default_position(grid_size[0], grid_size[1], area)
    else:
        x, y = clamp_to_area(first_card[0], first_card[1], grid_size[0], grid_size[1], area)
    return x - margins[0], y - margins[1]
