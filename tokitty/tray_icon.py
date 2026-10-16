"""The tray icon's sprite choice, used by the Qt tray.

Toolkit-free: qt/tray.py pads the raster into a QImage without copying the frame, scale
or palette decision.
"""
from __future__ import annotations

from typing import Tuple

from tokitty.sprite_raster import raster_rgba
from tokitty.sprites import get_frames, resolve_palette

TRAY_ICON_SCALE = 2  # 28x26 content sprite -> 56x52, padded to a 56x56 square


def tray_raster(colorway: str, pattern: str, tail: str = "plain") -> Tuple[int, int, bytes]:
    """(width, height, RGBA bytes) of frame 0 of the content state."""
    frame = get_frames("content")[0]
    return raster_rgba(frame, resolve_palette(colorway, pattern, tail), TRAY_ICON_SCALE)
