"""Shared sprite rasterizer: frame + palette -> pixel data.

Lifted out of scripts/render_media.py so the shipped tray icon (tray.py)
and the README media build from the exact same pixels. render_media.py
imports raster_frame; tray.py builds its RGBA icon from raster_rgba.
The package must not import from scripts/, so the tiny hex helper is
duplicated here (identical to render_sheet._hex_to_rgb).
"""
from __future__ import annotations

from typing import Dict, List, Tuple


def _hex_to_rgb(color: str) -> bytes:
    return bytes(int(color[i:i + 2], 16) for i in (1, 3, 5))


def raster_frame(frame: List[str], palette: Dict[str, str], scale: int, bg: bytes) -> List[List[bytes]]:
    """RGB pixel grid; empty/unmapped cells filled with `bg`. Byte-identical
    to the rasterizer render_media.py used before this module existed."""
    grid = [[bg] * (len(frame[0]) * scale) for _ in range(len(frame) * scale)]
    for r, row in enumerate(frame):
        for c, ch in enumerate(row):
            color = palette.get(ch, "")
            if not color:
                continue
            rgb = _hex_to_rgb(color)
            for dy in range(scale):
                for dx in range(scale):
                    grid[r * scale + dy][c * scale + dx] = rgb
    return grid


def raster_rgba(frame: List[str], palette: Dict[str, str], scale: int) -> Tuple[int, int, bytes]:
    """RGBA bytes for the tray icon; empty/unmapped cells fully transparent."""
    width = len(frame[0]) * scale
    height = len(frame) * scale
    out = bytearray()
    for row in frame:
        line = bytearray()
        for ch in row:
            color = palette.get(ch, "")
            if color:
                px = _hex_to_rgb(color) + b"\xff"
            else:
                px = b"\x00\x00\x00\x00"
            line += px * scale
        for _ in range(scale):
            out += line
    return width, height, bytes(out)
