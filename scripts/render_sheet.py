"""Render every sprite state's frames into one PNG contact sheet.

Stdlib-only (struct + zlib hand-rolled PNG, 8-bit RGB, single IDAT).
Exists because this repo's dev environment has no GUI: art review
happens by eyeballing the emitted PNG, not a live tkinter window.

Usage: python3 scripts/render_sheet.py --out sheet.png [--scale 8] [--colorway orange] [--pattern tabby]
       python3 scripts/render_sheet.py --grid --out grid.png [--scale 6]
Prints one state name per line (row legend, top to bottom); with --grid,
prints one "colorway/pattern" label per cell, row-major.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokitty.sprites import ALL_STATES, COLORWAYS, PATTERNS, get_frames, resolve_palette

BG = "#1c1c22"  # ui.py card background
GAP = 4  # background pixels between sprites, in device px


def _hex_to_rgb(color: str) -> bytes:
    return bytes(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _write_png(path: Path, rows: List[bytes], width: int) -> None:
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, len(rows), 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def render_sheet(out_path: Path, scale: int = 8, colorway: str = "orange",
                 pattern: str = "tabby") -> List[str]:
    palette = resolve_palette(colorway, pattern)
    bg = _hex_to_rgb(BG)
    states = list(ALL_STATES)
    all_frames = {s: get_frames(s) for s in states}

    cell_w = max(len(f[0][0]) for f in all_frames.values()) * scale
    cell_h = max(len(f[0]) for f in all_frames.values()) * scale
    max_frames = max(len(f) for f in all_frames.values())
    width = GAP + max_frames * (cell_w + GAP)
    height = GAP + len(states) * (cell_h + GAP)

    grid = [[bg] * width for _ in range(height)]
    for row_i, state in enumerate(states):
        for frame_i, frame in enumerate(all_frames[state]):
            x0 = GAP + frame_i * (cell_w + GAP)
            y0 = GAP + row_i * (cell_h + GAP)
            for r, row in enumerate(frame):
                for c, ch in enumerate(row):
                    color = palette.get(ch, "")
                    if not color:
                        continue
                    px = _hex_to_rgb(color)
                    for dy in range(scale):
                        for dx in range(scale):
                            grid[y0 + r * scale + dy][x0 + c * scale + dx] = px

    _write_png(out_path, [b"".join(row) for row in grid], width)
    return states


def render_grid(out_path: Path, scale: int = 6) -> List[str]:
    """One 'content' cat per colorway x pattern, laid out as a matrix.
    Returns the row-major cell legend ("colorway/pattern")."""
    from tokitty.sprites import _apply, SITTING_TEMPLATE
    pose = _apply(SITTING_TEMPLATE, {"L": "e", "R": "e", "A": "n"})
    rows_n, cols_n = len(pose), len(pose[0])
    cell_w, cell_h = cols_n * scale, rows_n * scale
    colorways, patterns = list(COLORWAYS), list(PATTERNS)
    width = GAP + len(patterns) * (cell_w + GAP)
    height = GAP + len(colorways) * (cell_h + GAP)
    bg = _hex_to_rgb(BG)
    grid = [[bg] * width for _ in range(height)]
    legend: List[str] = []
    for r, cw in enumerate(colorways):
        for c, pat in enumerate(patterns):
            palette = resolve_palette(cw, pat)
            x0 = GAP + c * (cell_w + GAP)
            y0 = GAP + r * (cell_h + GAP)
            for ry, row in enumerate(pose):
                for cx, ch in enumerate(row):
                    color = palette.get(ch, "")
                    if not color:
                        continue
                    px = _hex_to_rgb(color)
                    for dy in range(scale):
                        for dx in range(scale):
                            grid[y0 + ry * scale + dy][x0 + cx * scale + dx] = px
            legend.append(f"{cw}/{pat}")
    _write_png(out_path, [b"".join(row) for row in grid], width)
    return legend


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scale", type=int, default=8)
    parser.add_argument("--colorway", default="orange")
    parser.add_argument("--pattern", default="tabby")
    parser.add_argument("--grid", action="store_true")
    args = parser.parse_args()
    if args.grid:
        for label in render_grid(args.out, scale=args.scale):
            print(label)
    else:
        for state in render_sheet(args.out, scale=args.scale, colorway=args.colorway,
                                   pattern=args.pattern):
            print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
