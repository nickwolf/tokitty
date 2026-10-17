"""Where the cat's ground shadow goes and what it looks like, in card pixels.

Pure geometry with no toolkit, like theme.py: the Qt pane and the media
scripts both paint the returned cells (sprites.SCALE logical px each). The
shadow is two rows of cells straddling the surface the cat is over, as wide
as its feet plus a rim cell each side. It narrows and fades as the cat lifts
off the surface, so a hop reads as height.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from tokitty import theme
from tokitty.sprites import SCALE

# Transparent, the floating accents (zzz, sparkles, !, ?) and the tail bands:
# none of them stand on anything, so they never define the feet or the
# footprint (a tail draped over the card's edge hangs below the feet).
# Everything else, props included, is body.
NON_BODY = frozenset(".zh!?yY")
COLOR = (0, 0, 0)
CORE_ALPHA = 77  # out of 255
RIM_ALPHA = 38
MIN_SCALE = 0.4  # the smallest width and alpha fraction a lifted shadow shrinks to
FADE_PER_ROW = 0.15  # alpha lost per cell row of lift


@dataclass(frozen=True)
class Shadow:
    x: int  # card px of the top-left cell
    y: int
    cells: Tuple[Tuple[int, ...], ...]  # rows of alpha 0..255, one entry per SCALE px cell


def ground_row(grid: Sequence[str]) -> Optional[int]:
    """The lowest row holding a body char, or None."""
    for r in range(len(grid) - 1, -1, -1):
        if any(ch not in NON_BODY for ch in grid[r]):
            return r
    return None


def footprint(grid: Sequence[str]) -> Optional[Tuple[int, int]]:
    """First and last body column over the two lowest body rows."""
    bottom = ground_row(grid)
    if bottom is None:
        return None
    cols = [c for r in (bottom - 1, bottom) if r >= 0
            for c, ch in enumerate(grid[r]) if ch not in NON_BODY]
    return min(cols), max(cols)


def _surface(feet_y: int, surfaces: List[int]) -> int:
    below = [y for y in surfaces if y >= feet_y]
    return min(below) if below else max(surfaces)


def shadow_for(rest_grid: Sequence[str], grid: Sequence[str], offset: Tuple[int, int],
               home: str, mirrored: bool, x: Optional[int] = None) -> Optional[Shadow]:
    """The shadow of `grid` (the frame on screen, moved by `offset` cells) for a
    cat whose spot is `home`, in card coordinates. `rest_grid` is the animation's
    frame 0, which fixes the footprint and where the floor is. `x` is the
    sprite's left edge in card px where it is not at `home`'s own. None when
    the grid has no body."""
    feet_row, rest_feet = ground_row(grid), ground_row(rest_grid)
    foot = footprint(rest_grid)
    if feet_row is None or rest_feet is None or foot is None:
        return None
    origin_x, origin_y = theme.placement(home).origin
    if x is not None:
        origin_x = x
    dx, dy = offset
    feet_y = origin_y + (feet_row + 1) * SCALE + dy * SCALE
    if home == "on_top":
        floor = theme.placement("inside").origin[1] + theme.SPRITE_H
    else:
        floor = origin_y + (rest_feet + 1) * SCALE
    surface_y = _surface(feet_y, [0, floor])
    lift = max(0, surface_y - feet_y) / SCALE

    first, last = foot
    if mirrored:
        first, last, dx = len(rest_grid[0]) - 1 - last, len(rest_grid[0]) - 1 - first, -dx
    first, last = first + dx, last + dx
    visible0 = last - first + 1 + 2  # footprint plus a rim cell each side
    # Two cells narrower per two whole rows of lift; always an even step, so
    # the shadow stays centred.
    visible_min = max(2, round(MIN_SCALE * visible0))
    steps = min(int(lift) // 2, (visible0 - visible_min) // 2)
    visible = visible0 - 2 * steps
    fade = max(MIN_SCALE, 1 - FADE_PER_ROW * lift)
    core, rim = round(CORE_ALPHA * fade), round(RIM_ALPHA * fade)
    row = (0, rim) + (core,) * (visible - 2) + (rim, 0)
    left = first - 2 + steps  # the leftmost (transparent) cell
    return Shadow(origin_x + left * SCALE, surface_y - SCALE, (row, row))
