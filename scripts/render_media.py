"""Render README media: per-state PNG stills, a dual-cat card, the flag GIF,
and an animated GIF of every state drawn on tracks (anim-<state>.gif).

Stills reuse render_sheet's stdlib rasterizer; the GIF is assembled with
Pillow, which is a dev-only dependency of this script (the shipped app
stays stdlib-only). Frames render on the ui.py card background so the
media matches what the widget actually looks like.

Usage: python3 scripts/render_media.py --out docs/media [--scale 6] [--anim-only]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokitty.sprites import ALL_STATES, LEGACY_COAT_MAP, get_frames, get_tracks, resolve_palette
from render_sheet import BG, _hex_to_rgb, _write_png, write_gif
from tokitty.sprite_raster import raster_frame

# The states worth photographing, per the Phase 5 spec: mood ladder,
# activity states, permission flag, capped/flopped.
STILL_STATES = ["content", "interested", "alert", "working", "thinking",
                "permission", "flopped", "sleeping", "done_hop"]
GIF_STATE = "permission"  # the flag animation, per spec ("flag or wake")
GIF_SECONDS = 6.4  # a whole number of 800 ms loops, so the GIF loops without a seam
ANIM_SECONDS = 30  # long enough for the random holds to show their range
# Seconds for states whose rare moments need longer to come round: the
# sleeping cat's peek is 20 to 45 s apart.
ANIM_SECONDS_FOR = {"sleeping": 50}


def write_anims(out: Path, scale: int) -> None:
    """anim-<state>.gif for every state with more than one track, played with
    seed 0 so a rerun on unchanged art writes the same file."""
    for state in ALL_STATES:
        if len(get_tracks(state)) > 1:
            seconds = ANIM_SECONDS_FOR.get(state, ANIM_SECONDS)
            frames = write_gif(out / f"anim-{state}.gif", state, scale=scale, seconds=seconds)
            print(f"anim-{state}.gif ({frames} frames)")


def _png_from_grid(path: Path, grid: List[List[bytes]]) -> None:
    _write_png(path, [b"".join(row) for row in grid], len(grid[0]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scale", type=int, default=6)
    parser.add_argument("--anim-only", action="store_true",
                        help="write only the anim-<state>.gif files")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.anim_only:
        write_anims(args.out, args.scale)
        return 0

    bg = _hex_to_rgb(BG)
    palette = resolve_palette("orange", "tabby")

    for state in STILL_STATES:
        frame = get_frames(state)[0]
        _png_from_grid(args.out / f"state-{state}.png", raster_frame(frame, palette, args.scale, bg))
        print(f"state-{state}.png")

    # Dual-cat card: personal orange tabby over work gray tabby, echoing
    # the two-pane layout (the real card also shows bars; this is the cat
    # half, which is what the README is selling).
    top = raster_frame(get_frames("working")[0], resolve_palette(*LEGACY_COAT_MAP["orange_tabby"]),
                        args.scale, bg)
    bottom = raster_frame(get_frames("sleeping")[0], resolve_palette(*LEGACY_COAT_MAP["gray_tabby"]),
                           args.scale, bg)
    width = max(len(top[0]), len(bottom[0]))
    def pad(g):
        return [row + [bg] * (width - len(row)) for row in g]
    gap = [[bg] * width for _ in range(2 * args.scale)]
    _png_from_grid(args.out / "dual-card.png", pad(top) + gap + pad(bottom))
    print("dual-card.png")

    frames = write_gif(args.out / f"{GIF_STATE}.gif", GIF_STATE, scale=args.scale,
                       seconds=GIF_SECONDS)
    print(f"{GIF_STATE}.gif ({frames} frames)")
    write_anims(args.out, args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
