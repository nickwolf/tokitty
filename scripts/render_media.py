"""Render README media: per-state PNG stills, a dual-cat card, and one GIF.

Stills reuse render_sheet's stdlib rasterizer; the GIF is assembled with
Pillow, which is a dev-only dependency of this script (the shipped app
stays stdlib-only). Frames render on the ui.py card background so the
media matches what the widget actually looks like.

Usage: python3 scripts/render_media.py --out docs/media [--scale 6]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokitty.sprites import get_frames, get_palette
from render_sheet import BG, _hex_to_rgb, _write_png

# The states worth photographing, per the Phase 5 spec: mood ladder,
# activity states, permission flag, capped/flopped.
STILL_STATES = ["content", "interested", "alert", "working", "thinking",
                "permission", "flopped", "sleeping", "done_hop"]
GIF_STATE = "permission"  # the flag animation, per spec ("flag or wake")
GIF_FRAME_MS = 800  # matches ui.FRAME_INTERVAL_MS


def _raster(frame: List[str], palette: dict, scale: int, bg: bytes) -> List[List[bytes]]:
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


def _png_from_grid(path: Path, grid: List[List[bytes]]) -> None:
    _write_png(path, [b"".join(row) for row in grid], len(grid[0]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scale", type=int, default=6)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    bg = _hex_to_rgb(BG)
    palette = get_palette()

    for state in STILL_STATES:
        frame = get_frames(state)[0]
        _png_from_grid(args.out / f"state-{state}.png", _raster(frame, palette, args.scale, bg))
        print(f"state-{state}.png")

    # Dual-cat card: personal orange tabby over work gray tabby, echoing
    # the two-pane layout (the real card also shows bars; this is the cat
    # half, which is what the README is selling).
    top = _raster(get_frames("working")[0], get_palette("orange_tabby"), args.scale, bg)
    bottom = _raster(get_frames("sleeping")[0], get_palette("gray_tabby"), args.scale, bg)
    width = max(len(top[0]), len(bottom[0]))
    pad = lambda g: [row + [bg] * (width - len(row)) for row in g]
    gap = [[bg] * width for _ in range(2 * args.scale)]
    _png_from_grid(args.out / "dual-card.png", pad(top) + gap + pad(bottom))
    print("dual-card.png")

    from PIL import Image

    frames = get_frames(GIF_STATE)
    images = []
    for frame in frames:
        grid = _raster(frame, palette, args.scale, bg)
        raw = b"".join(b"".join(row) for row in grid)
        images.append(Image.frombytes("RGB", (len(grid[0]), len(grid)), raw))
    images[0].save(args.out / f"{GIF_STATE}.gif", save_all=True,
                   append_images=images[1:], duration=GIF_FRAME_MS, loop=0)
    print(f"{GIF_STATE}.gif ({len(images)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
