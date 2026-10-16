"""Render every sprite state's frames into one PNG contact sheet.

Stdlib-only (struct + zlib hand-rolled PNG, 8-bit RGB, single IDAT).
Exists because this repo's dev environment has no GUI: art review
happens by eyeballing the emitted PNG, not a live tkinter window.

Usage: python3 scripts/render_sheet.py --out sheet.png [--scale 8] [--colorway orange] [--pattern tabby]
                                       [--state NAME ...] [--wrap 8]
                                       [--gif DIR [--seed 0] [--gif-seconds 12]]
       python3 scripts/render_sheet.py --grid --out grid.png [--scale 6]
Prints one legend line per track (top to bottom; "state" for a one-track state,
"state/track" otherwise) with its frame holds in ms, e.g.
"content: 800 800 (loop from 0)"; with --grid, prints one "colorway/pattern"
label per cell, row-major. Multi-track rows show a track's frames with the
other tracks at frame 0. --gif DIR also writes one GIF per selected state
(needs Pillow): the tracks played together for --gif-seconds with --seed.
"""
from __future__ import annotations

import argparse
import random
import struct
import sys
import zlib
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokitty.sprites import (
    ALL_STATES, COLORWAYS, PATTERNS, compose, frame_ms_max, get_tracks, resolve_palette,
)

BG = "#1c1c22"  # ui.py card background
GAP = 4  # background pixels between sprites, in device px
DEFAULT_GIF_SECONDS = 12  # simulated playback length of a --gif
DEFAULT_WRAP = 8  # frames per row before a state wraps onto a continuation row
# Duration strip under each frame, in device px: a gap, the bar, then the
# row that holds the loop_from marker.
STRIP_GAP = 2
STRIP_H = 4
MARK_H = 4
MIN_STRIP_FULL_MS = 1000  # a cell-wide bar is at least this long, so an 800 ms hold fills 80%
STRIP_SOLID = "#9a9aa8"  # neutral on BG; the lo end of a hold
STRIP_LIGHT = "#4a4a58"  # a range's lo-to-hi tail
STRIP_MARK = "#e0c050"   # the loop_from marker


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


def _blit(grid: List[List[bytes]], frame: List[str], palette, x0: int, y0: int,
          scale: int) -> None:
    for r, row in enumerate(frame):
        for c, ch in enumerate(row):
            color = palette.get(ch, "")
            if not color:
                continue
            px = _hex_to_rgb(color)
            for dy in range(scale):
                for dx in range(scale):
                    grid[y0 + r * scale + dy][x0 + c * scale + dx] = px


def _fill(grid: List[List[bytes]], x0: int, y0: int, w: int, h: int, px: bytes) -> None:
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            grid[y][x] = px


def _rows(states: Sequence[str]):
    """(label, track, grids) per sheet row: one per track of every state. A
    track's grids show its frames with every other track at frame 0, so a
    row reads as that strand alone; a one-track state is labelled with just
    its name."""
    rows = []
    for state in states:
        tracks = get_tracks(state)
        for t, track in enumerate(tracks):
            grids = [compose(state, [i if k == t else 0 for k in range(len(tracks))])
                     for i in range(len(track.frames))]
            rows.append((state if len(tracks) == 1 else f"{state}/{track.name}", track, grids))
    return rows


def legend_line(label: str, track) -> str:
    """'content: 800 800 (loop from 0)'; ranges print as lo-hi."""
    holds = " ".join(str(f.ms) if isinstance(f.ms, int) else f"{f.ms[0]}-{f.ms[1]}"
                     for f in track.frames)
    tail = "play once" if track.loop_from is None else f"loop from {track.loop_from}"
    return f"{label}: {holds} ({tail})"


def legend(states: Optional[Sequence[str]] = None) -> List[str]:
    return [legend_line(label, track) for label, track, _ in _rows(ALL_STATES if states is None else states)]


def render_sheet(out_path: Path, scale: int = 8, colorway: str = "orange",
                 pattern: str = "tabby", states: Optional[Sequence[str]] = None,
                 wrap: int = DEFAULT_WRAP) -> List[str]:
    """One block per track (a one-track state is one block): its frames left
    to right, wrapping onto continuation rows after `wrap` frames, each with
    a duration strip under it. The strip's length is proportional to the hold
    (a range is drawn as a solid lo plus a lighter lo-to-hi tail); a marker
    under the loop_from frame shows where the loop restarts. Returns the row
    labels, top to bottom."""
    palette = resolve_palette(colorway, pattern)
    bg = _hex_to_rgb(BG)
    rows = _rows(list(ALL_STATES if states is None else states))

    cell_w = max(len(g[0]) for _, _, grids in rows for g in grids) * scale
    sprite_h = max(len(g) for _, _, grids in rows for g in grids) * scale
    cell_h = sprite_h + STRIP_GAP + STRIP_H + MARK_H
    # One scale for every strip, so bars compare across states.
    full_ms = max(MIN_STRIP_FULL_MS, max(frame_ms_max(f) for _, t, _ in rows for f in t.frames))
    per_row = max(1, min(wrap, max(len(grids) for _, _, grids in rows)))
    lines = sum(-(-len(grids) // per_row) for _, _, grids in rows)
    width = GAP + per_row * (cell_w + GAP)
    height = GAP + lines * (cell_h + GAP)

    grid = [[bg] * width for _ in range(height)]
    solid, light, mark = (_hex_to_rgb(c) for c in (STRIP_SOLID, STRIP_LIGHT, STRIP_MARK))
    line = 0
    for _label, track, grids in rows:
        for frame_i, frame in enumerate(grids):
            x0 = GAP + (frame_i % per_row) * (cell_w + GAP)
            y0 = GAP + (line + frame_i // per_row) * (cell_h + GAP)
            _blit(grid, frame, palette, x0, y0, scale)
            ms = track.frames[frame_i].ms
            lo, hi = (ms, ms) if isinstance(ms, int) else ms
            sy = y0 + sprite_h + STRIP_GAP
            lo_w = max(1, round(cell_w * lo / full_ms))
            hi_w = max(lo_w, round(cell_w * hi / full_ms))
            _fill(grid, x0, sy, hi_w, STRIP_H, light)
            _fill(grid, x0, sy, lo_w, STRIP_H, solid)
            if track.loop_from == frame_i:
                _fill(grid, x0, sy + STRIP_H, max(MARK_H, cell_w // 4), MARK_H, mark)
        line += -(-len(grids) // per_row)

    _write_png(out_path, [b"".join(row) for row in grid], width)
    return [label for label, _, _ in rows]


def write_gif(path: Path, state: str, scale: int = 8, colorway: str = "orange",
              pattern: str = "tabby", seed: int = 0, seconds: float = DEFAULT_GIF_SECONDS) -> int:
    """Write one animated GIF of a state: the tracks played together on their
    own clocks for `seconds` of simulated time with a seeded rng, one GIF
    frame per change of the composed grid and the exact holds between them.
    Returns the frame count. Pillow is only needed here, so it is imported
    lazily."""
    from PIL import Image

    from tokitty.animation import simulate
    from tokitty.sprite_raster import raster_frame

    palette = resolve_palette(colorway, pattern)
    bg = _hex_to_rgb(BG)
    images, durations = [], []
    for grid, ms in simulate(state, seconds, random.Random(seed)):
        px = raster_frame(grid, palette, scale, bg)
        raw = b"".join(b"".join(row) for row in px)
        images.append(Image.frombytes("RGB", (len(px[0]), len(px)), raw))
        durations.append(ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:], duration=durations, loop=0)
    return len(images)


def write_gifs(out_dir: Path, states: Sequence[str], **kw) -> List[Path]:
    """write_gif for each state, as DIR/<state>.gif."""
    paths = []
    for state in states:
        paths.append(out_dir / f"{state}.gif")
        write_gif(paths[-1], state, **kw)
    return paths


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
    parser.add_argument("--state", action="append", choices=ALL_STATES,
                        help="only this state (repeatable; default all)")
    parser.add_argument("--wrap", type=int, default=DEFAULT_WRAP,
                        help="max frames per row before wrapping")
    parser.add_argument("--gif", type=Path, metavar="DIR",
                        help="also write one animated GIF per selected state")
    parser.add_argument("--seed", type=int, default=0, help="rng seed for --gif ranged holds")
    parser.add_argument("--gif-seconds", type=float, default=DEFAULT_GIF_SECONDS)
    args = parser.parse_args()
    if args.grid:
        for label in render_grid(args.out, scale=args.scale):
            print(label)
    else:
        states = args.state or list(ALL_STATES)
        render_sheet(args.out, scale=args.scale, colorway=args.colorway,
                     pattern=args.pattern, states=states, wrap=args.wrap)
        for line in legend(states):
            print(line)
        if args.gif is not None:
            write_gifs(args.gif, states, scale=args.scale, colorway=args.colorway,
                       pattern=args.pattern, seed=args.seed, seconds=args.gif_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
