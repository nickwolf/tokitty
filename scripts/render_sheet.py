"""Render every sprite state's frames into one PNG contact sheet.

Stdlib-only (struct + zlib hand-rolled PNG, 8-bit RGB, single IDAT).
Exists because this repo's dev environment has no GUI: art review
happens by eyeballing the emitted PNG, not a live tkinter window.

Usage: python3 scripts/render_sheet.py --out sheet.png [--scale 8] [--colorway orange] [--pattern tabby]
                                       [--state NAME ...] [--wrap 8] [--spot SPOT]
                                       [--gif DIR [--seed 0] [--gif-seconds 12]]
       python3 scripts/render_sheet.py --grid --out grid.png [--scale 6]
       python3 scripts/render_sheet.py --tones COAT,SHADE,MARK,LIGHT,EAR --out custom.png
--tones (five #rrggbb, repeatable) renders a temporary custom colorway instead of
--colorway; with --grid the grid is one row per --tones, across every pattern.
Prints one legend line per track (top to bottom; "state" for a one-track state,
"state/track" otherwise) with its frame holds in ms, e.g.
"content: 800 800 (loop from 0)"; with --grid, prints one "colorway/pattern"
label per cell, row-major. Multi-track rows show a track's frames with the
other tracks at frame 0. --gif DIR also writes one GIF per selected state
(needs Pillow): the tracks played together for --gif-seconds with --seed.
--state also takes a transition as "from>to"; its GIF plays the transition
and then the target, and is named <from>-to-<to>.gif (no ">" in a filename).
It also takes a spot variant as "state@spot" (<state>-at-<spot>.gif) and a
move as "move:name" (move-<name>.gif). A state whose frames move the sprite
(Frame.offset) gets a canvas that holds every offset, each frame drawn at its
own. --spot {inside,inside_right,on_top} draws each frame in context instead:
the card, the cat at that spot (a plain state plays as its variant for the spot
when it has one) and its ground shadow, cropped to the cat's side of the card
and widened when the frames move the cat out of it.
"""
from __future__ import annotations

import argparse
import random
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokitty import shadow, theme
from tokitty.sprites import (
    ALL_STATES, ANY_STATE, COLORWAYS, MOVE_PREFIX, MOVES, PATTERNS, SCALE as CELL, SPOT_SEP, SPOT_VARIANTS,
    TRANSITION_SEP, TRANSITIONS, compose, compose_offset, frame_ms_max, get_frames, get_tracks,
    move_name, resolve_palette, set_custom_colorways, spot_state, transition_name,
)

BG = "#1c1c22"  # ui.py card background
SCENE_BACKDROP = "#3c3c48"  # outside the card in a --spot scene; BG would hide the card's edge
SCENE_W = 128  # logical px of a --spot scene: the cat's side of the card
SCENE_ABOVE = theme.SPRITE_H + 12  # logical px kept above the card, for the on_top cat
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
    """(label, track, grids, offsets, state) per sheet row: one per track of
    every state. A track's grids show its frames with every other track at
    frame 0, so a row reads as that strand alone; a one-track state is
    labelled with just its name."""
    rows = []
    for state in states:
        tracks = get_tracks(state)
        for t, track in enumerate(tracks):
            at = [[i if k == t else 0 for k in range(len(tracks))] for i in range(len(track.frames))]
            rows.append((state if len(tracks) == 1 else f"{state}/{track.name}", track,
                         [compose(state, pos) for pos in at],
                         [compose_offset(state, pos) for pos in at], state))
    return rows


def _bounds(offsets: Sequence[Tuple[int, int]]) -> Tuple[int, int, int, int]:
    """(min dx, min dy, max dx, max dy) over the offsets and (0, 0), in cells."""
    xs, ys = [0] + [o[0] for o in offsets], [0] + [o[1] for o in offsets]
    return min(xs), min(ys), max(xs), max(ys)


def _paint(canvas: List[List[bytes]], cx: int, cy: int, scale: int, px: bytes, alpha: int = 255) -> None:
    """One cell at cell position (cx, cy), clipped; alpha < 255 blends `px`
    over what is already there."""
    if not (0 <= cy * scale < len(canvas) and 0 <= cx * scale < len(canvas[0])):
        return
    for y in range(cy * scale, (cy + 1) * scale):
        line = canvas[y]
        for x in range(cx * scale, (cx + 1) * scale):
            if alpha >= 255:
                line[x] = px
            else:
                line[x] = bytes((u * (255 - alpha) + p * alpha) // 255 for u, p in zip(line[x], px))


def scene_span(spot: str, offsets: Sequence[Tuple[int, int]] = ()) -> Tuple[int, int]:
    """Logical px (left, width) of a --spot scene in card coordinates: the
    cat's side of the card, widened so the cat stays in view at every offset
    (a walk along the edge), with a cell to spare."""
    place = theme.placement(spot)
    left = theme.CARD_WIDTH - SCENE_W if spot == "inside_right" else 0
    right = left + SCENE_W
    for dx, _ in offsets:
        x = place.origin[0] + (-dx if place.mirrored else dx) * CELL
        left, right = min(left, x - CELL), max(right, x + theme.SPRITE_W + CELL)
    return left, right - left


def scene_size(scale: int, width: int = SCENE_W) -> Tuple[int, int]:
    """Device px (width, height) of a --spot frame `width` logical px wide."""
    return width // CELL * scale, (SCENE_ABOVE + theme.CARD_HEIGHT) // CELL * scale


def scene_frame(frame: List[str], offset: Tuple[int, int], rest: List[str], spot: str,
                palette, scale: int, span: Optional[Tuple[int, int]] = None) -> List[List[bytes]]:
    """One frame in context: the backdrop, the card, the cat's ground shadow
    and the cat at `spot` moved by `offset`, cropped to `span` (scene_span;
    by default the cat's side of the card), in card coordinates scaled by
    scale / sprites.SCALE. `rest` is the animation's frame 0."""
    place = theme.placement(spot)
    left, span_w = scene_span(spot) if span is None else span
    crop_x = left // CELL
    crop_y = -SCENE_ABOVE // CELL
    width, height = scene_size(scale, span_w)
    canvas = [[_hex_to_rgb(SCENE_BACKDROP)] * width for _ in range(height)]
    card = _hex_to_rgb(theme.CLASSIC.card.background)
    for cy in range(theme.CARD_HEIGHT // CELL):
        for cx in range(theme.CARD_WIDTH // CELL):
            _paint(canvas, cx - crop_x, cy - crop_y, scale, card)
    cast = shadow.shadow_for(rest, frame, offset, spot, place.mirrored)
    if cast is not None:
        for r, alphas in enumerate(cast.cells):
            for c, alpha in enumerate(alphas):
                if alpha:
                    _paint(canvas, cast.x // CELL + c - crop_x, cast.y // CELL + r - crop_y,
                           scale, bytes(shadow.COLOR), alpha)
    dx, dy = offset
    ox = place.origin[0] // CELL + (-dx if place.mirrored else dx) - crop_x
    oy = place.origin[1] // CELL + dy - crop_y
    for r, row in enumerate(frame):
        for c, ch in enumerate(row):
            color = palette.get(ch, "")
            if color:
                _paint(canvas, ox + (len(row) - 1 - c if place.mirrored else c), oy + r,
                       scale, _hex_to_rgb(color))
    return canvas


def resolve_states(states: Sequence[str], spot: Optional[str]) -> List[str]:
    """With a spot, each plain state as its variant for that spot if it has one."""
    return list(states) if spot is None else [spot_state(s, spot) for s in states]


def legend_line(label: str, track) -> str:
    """'content: 800 800 (loop from 0)'; ranges print as lo-hi."""
    holds = " ".join(str(f.ms) if isinstance(f.ms, int) else f"{f.ms[0]}-{f.ms[1]}"
                     for f in track.frames)
    tail = "play once" if track.loop_from is None else f"loop from {track.loop_from}"
    return f"{label}: {holds} ({tail})"


def legend(states: Optional[Sequence[str]] = None) -> List[str]:
    return [legend_line(label, track) for label, track, *_ in _rows(ALL_STATES if states is None else states)]


def render_sheet(out_path: Path, scale: int = 8, colorway: str = "orange",
                 pattern: str = "tabby", states: Optional[Sequence[str]] = None,
                 wrap: int = DEFAULT_WRAP, spot: Optional[str] = None) -> List[str]:
    """One block per track (a one-track state is one block): its frames left
    to right, wrapping onto continuation rows after `wrap` frames, each with
    a duration strip under it. The strip's length is proportional to the hold
    (a range is drawn as a solid lo plus a lighter lo-to-hi tail); a marker
    under the loop_from frame shows where the loop restarts. Returns the row
    labels, top to bottom. Frames that move the sprite get a cell that holds
    every offset; with a `spot`, each cell is the frame in context instead."""
    palette = resolve_palette(colorway, pattern)
    bg = _hex_to_rgb(BG)
    rows = _rows(resolve_states(ALL_STATES if states is None else states, spot))

    min_dx, min_dy, max_dx, max_dy = _bounds([o for _, _, _, offsets, _ in rows for o in offsets])
    cell_w = (max(len(g[0]) for _, _, grids, _, _ in rows for g in grids) + max_dx - min_dx) * scale
    sprite_h = (max(len(g) for _, _, grids, _, _ in rows for g in grids) + max_dy - min_dy) * scale
    if spot is not None:
        span = scene_span(spot, [o for _, _, _, offsets, _ in rows for o in offsets])
        cell_w, sprite_h = scene_size(scale, span[1])
    cell_h = sprite_h + STRIP_GAP + STRIP_H + MARK_H
    # One scale for every strip, so bars compare across states.
    full_ms = max(MIN_STRIP_FULL_MS, max(frame_ms_max(f) for _, t, *_ in rows for f in t.frames))
    per_row = max(1, min(wrap, max(len(row[2]) for row in rows)))
    lines = sum(-(-len(grids) // per_row) for _, _, grids, *_ in rows)
    width = GAP + per_row * (cell_w + GAP)
    height = GAP + lines * (cell_h + GAP)

    grid = [[bg] * width for _ in range(height)]
    solid, light, mark = (_hex_to_rgb(c) for c in (STRIP_SOLID, STRIP_LIGHT, STRIP_MARK))
    line = 0
    for _label, track, grids, offsets, state in rows:
        for frame_i, frame in enumerate(grids):
            x0 = GAP + (frame_i % per_row) * (cell_w + GAP)
            y0 = GAP + (line + frame_i // per_row) * (cell_h + GAP)
            if spot is None:
                dx, dy = offsets[frame_i]
                _blit(grid, frame, palette, x0 + (dx - min_dx) * scale, y0 + (dy - min_dy) * scale, scale)
            else:
                scene = scene_frame(frame, offsets[frame_i], get_frames(state)[0], spot, palette, scale,
                                    span)
                for y, scan in enumerate(scene):
                    grid[y0 + y][x0:x0 + len(scan)] = scan
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
    return [row[0] for row in rows]


def gif_name(state: str) -> str:
    """The GIF file name for a state, a "from>to" transition, a "state@spot"
    variant or a "move:name" move (none of ">", "@", ":", "*" in a filename;
    an any-state transition is "any-to-<to>")."""
    if state.startswith(MOVE_PREFIX):
        state = "move-" + state[len(MOVE_PREFIX):]
    state = state.replace(ANY_STATE + TRANSITION_SEP, "any" + TRANSITION_SEP)
    return state.replace(TRANSITION_SEP, "-to-").replace(SPOT_SEP, "-at-") + ".gif"


def write_gif(path: Path, state: str, scale: int = 8, colorway: str = "orange",
              pattern: str = "tabby", seed: int = 0, seconds: float = DEFAULT_GIF_SECONDS,
              from_state: Optional[str] = None, spot: Optional[str] = None) -> int:
    """Write one animated GIF of a state: the tracks played together on their
    own clocks for `seconds` of simulated time with a seeded rng, one GIF
    frame per change of the composed grid or offset and the exact holds
    between them. A from_state with a transition into `state` plays that
    first. The canvas holds every offset the play reaches; with a `spot` each
    frame is drawn in context instead (see scene_frame). Returns the frame
    count. Pillow is only needed here, so it is imported lazily."""
    from PIL import Image

    from tokitty.animation import simulate_frames

    palette = resolve_palette(colorway, pattern)
    bg = _hex_to_rgb(BG)
    if spot is not None:
        state = spot_state(state, spot)
        from_state = None if from_state is None else spot_state(from_state, spot)
    played = simulate_frames(state, seconds, random.Random(seed), from_state)
    rest = get_frames(state)[0]
    min_dx, min_dy, max_dx, max_dy = _bounds([offset for _, _, offset in played])
    span = None if spot is None else scene_span(spot, [offset for _, _, offset in played])
    width = (max(len(g[0]) for g, _, _ in played) + max_dx - min_dx) * scale
    height = (max(len(g) for g, _, _ in played) + max_dy - min_dy) * scale
    images, durations = [], []
    for frame, ms, (dx, dy) in played:
        if spot is None:
            px = [[bg] * width for _ in range(height)]
            _blit(px, frame, palette, (dx - min_dx) * scale, (dy - min_dy) * scale, scale)
        else:
            px = scene_frame(frame, (dx, dy), rest, spot, palette, scale, span)
        raw = b"".join(b"".join(row) for row in px)
        images.append(Image.frombytes("RGB", (len(px[0]), len(px)), raw))
        durations.append(ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:], duration=durations, loop=0)
    return len(images)


def write_gifs(out_dir: Path, states: Sequence[str], **kw) -> List[Path]:
    """write_gif for each state, as DIR/<state>.gif. A "from>to" transition
    plays the transition and then its target, as DIR/<from>-to-<to>.gif."""
    paths = []
    for state in states:
        paths.append(out_dir / gif_name(state))
        if TRANSITION_SEP in state:
            from_state, to_state = state.split(TRANSITION_SEP, 1)
            write_gif(paths[-1], to_state, from_state=from_state, **kw)
        else:
            write_gif(paths[-1], state, **kw)
    return paths


TONE_ORDER = ("coat", "shade", "mark", "light", "ear")
TONES_ID_BASE = 999  # --tones rows register as custom-999, custom-998, ...


def parse_tones(text: str) -> dict:
    """"COAT,SHADE,MARK,LIGHT,EAR" (five #rrggbb) -> a tones dict."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != len(TONE_ORDER) or not all(re.fullmatch(r"#[0-9a-fA-F]{6}", p) for p in parts):
        raise ValueError(f"expected five #rrggbb colours as COAT,SHADE,MARK,LIGHT,EAR, got {text!r}")
    return dict(zip(TONE_ORDER, (p.lower() for p in parts)))


def register_tones(specs: Sequence[str]) -> List[str]:
    """Register each --tones value as a temporary custom colorway and return
    their ids, in order."""
    table = {f"custom-{TONES_ID_BASE - i}": parse_tones(spec) for i, spec in enumerate(specs)}
    set_custom_colorways(table)
    return list(table)


def render_grid(out_path: Path, scale: int = 6,
                colorways: Optional[Sequence[str]] = None) -> List[str]:
    """One 'content' cat per colorway x pattern, laid out as a matrix
    (the built-in colorways unless `colorways` is given).
    Returns the row-major cell legend ("colorway/pattern")."""
    from tokitty.sprites import _apply, SITTING_TEMPLATE
    pose = _apply(SITTING_TEMPLATE, {"L": "e", "R": "e", "A": "n"})
    rows_n, cols_n = len(pose), len(pose[0])
    cell_w, cell_h = cols_n * scale, rows_n * scale
    colorways = list(COLORWAYS) if colorways is None else list(colorways)
    patterns = list(PATTERNS)
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
    parser.add_argument("--tones", action="append", metavar="COAT,SHADE,MARK,LIGHT,EAR",
                        help="five #rrggbb tones for a temporary custom colorway (repeatable; "
                             "replaces --colorway; --grid draws one row per value)")
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--state", action="append",
                        choices=(list(ALL_STATES) + [transition_name(*pair) for pair in TRANSITIONS]
                                 + [spot_state(*key) for key in SPOT_VARIANTS]
                                 + [move_name(name) for name in MOVES]),
                        help="only this state, from>to transition, state@spot variant or move:name "
                             "move (repeatable; default all)")
    parser.add_argument("--spot", choices=theme.SPOTS,
                        help="draw each frame in context: the card, the cat at this spot, its shadow")
    parser.add_argument("--wrap", type=int, default=DEFAULT_WRAP,
                        help="max frames per row before wrapping")
    parser.add_argument("--gif", type=Path, metavar="DIR",
                        help="also write one animated GIF per selected state")
    parser.add_argument("--seed", type=int, default=0, help="rng seed for --gif ranged holds")
    parser.add_argument("--gif-seconds", type=float, default=DEFAULT_GIF_SECONDS)
    args = parser.parse_args()
    colorways = None
    if args.tones:
        try:
            colorways = register_tones(args.tones)
        except ValueError as exc:
            parser.error(str(exc))
        args.colorway = colorways[0]
    if args.grid:
        for label in render_grid(args.out, scale=args.scale, colorways=colorways):
            print(label)
    else:
        states = resolve_states(args.state or list(ALL_STATES), args.spot)
        render_sheet(args.out, scale=args.scale, colorway=args.colorway,
                     pattern=args.pattern, states=states, wrap=args.wrap, spot=args.spot)
        for line in legend(states):
            print(line)
        if args.gif is not None:
            write_gifs(args.gif, states, scale=args.scale, colorway=args.colorway,
                       pattern=args.pattern, seed=args.seed, seconds=args.gif_seconds,
                       spot=args.spot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
