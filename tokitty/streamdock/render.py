"""Render the 64x64 key images for the Stream Dock M18.

Every function returns a data:image/png;base64 string, memoised on its inputs
so a steady deck costs nothing per refresh. Text uses the font that ships with
Pillow (no system font paths), and PNG encoding settings are fixed so equal
inputs always give equal bytes.
"""
from __future__ import annotations

import base64
import io
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from tokitty import sprites
from tokitty.display import AMBER, GREEN, RED, bar_color
from tokitty.sprite_raster import raster_frame

SIZE = 64
SPRITE_SCALE = 2
BORDER = 2
TITLE_STRIP = 12
# Characters per line and lines per key for preview_keys.
PREVIEW_COLS = 8
PREVIEW_LINES = 4

BG = (24, 24, 28)
FG = (235, 235, 235)
DIM = (150, 150, 158)
GREY = (84, 84, 92)
TRACK = (52, 52, 60)
_CACHE = 256

_Palette = Tuple[Tuple[str, str], ...]


def _rgb(color: str) -> Tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _encode(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _canvas(bg=BG) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (SIZE, SIZE), bg)
    return img, ImageDraw.Draw(img)


def _border(draw: ImageDraw.ImageDraw, color) -> None:
    draw.rectangle((0, 0, SIZE - 1, SIZE - 1), outline=color, width=BORDER)


def _centered(draw: ImageDraw.ImageDraw, cx: float, cy: float, text: str, size: int, fill) -> None:
    draw.text((cx, cy), text, font=_font(size), fill=fill, anchor="mm")


def _fit(text: str, size: int, width: int) -> str:
    """Truncate `text` with a trailing ellipsis so it renders within `width` px."""
    font = _font(size)
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + "..") > width:
        text = text[:-1]
    return text + ".."


def _sprite_image(sprite_state: str, frame_index: int, palette: _Palette) -> Image.Image:
    frames = sprites.get_frames(sprite_state)
    frame = frames[frame_index % len(frames)]
    grid = raster_frame(frame, dict(palette), SPRITE_SCALE, bytes(BG))
    h, w = len(grid), len(grid[0])
    img = Image.new("RGB", (w, h))
    img.putdata([tuple(px) for row in grid for px in row])
    return img


@lru_cache(maxsize=_CACHE)
def _session_key(sprite_state: str, frame_index: int, palette: _Palette, title: str, accent: bool) -> str:
    img, draw = _canvas()
    cat = _sprite_image(sprite_state, frame_index, palette)
    # Whatever size the frame grid yields is centred in the area above the strip.
    img.paste(cat, ((SIZE - cat.width) // 2, max(0, (SIZE - TITLE_STRIP - cat.height) // 2)))
    inner = SIZE - 2 * BORDER - 4
    text = _fit(title, 8, inner)
    _centered(draw, SIZE / 2, SIZE - TITLE_STRIP / 2 - 1, text, 8, FG)
    if accent:
        _border(draw, _rgb(AMBER))
    return _encode(img)


def session_key(sprite_state: str, frame_index: int, palette: Dict[str, str], title: str, accent: bool = False) -> str:
    """A cat with a title strip below. `frame_index` wraps modulo the state's
    frame count, so callers can pass a running counter."""
    return _session_key(sprite_state, frame_index, tuple(sorted(palette.items())), title, bool(accent))


@lru_cache(maxsize=_CACHE)
def _usage_key(session_pct: int, weekly_pct: int, warn: bool) -> str:
    img, draw = _canvas()
    for i, (label, pct) in enumerate((("5h", session_pct), ("wk", weekly_pct))):
        top = 5 + i * 29
        draw.text((7, top), label, font=_font(9), fill=DIM, anchor="lt")
        draw.text((SIZE - 7, top), f"{pct}%", font=_font(12), fill=FG, anchor="rt")
        bar_y = top + 15
        draw.rectangle((7, bar_y, SIZE - 8, bar_y + 5), fill=TRACK)
        fill_w = round((SIZE - 14) * pct / 100)
        if fill_w > 0:
            draw.rectangle((7, bar_y, 7 + fill_w - 1, bar_y + 5), fill=_rgb(bar_color(pct)))
    if warn:
        _border(draw, _rgb(RED))
    return _encode(img)


def usage_key(session_pct: float, weekly_pct: float, warn: bool = False) -> str:
    """Session and weekly bars with numbers, coloured like the window's bars."""
    clamp = lambda v: max(0, min(100, int(round(v))))  # noqa: E731
    return _usage_key(clamp(session_pct), clamp(weekly_pct), bool(warn))


@lru_cache(maxsize=_CACHE)
def decision_key(kind: str, armed: bool = True) -> str:
    """allow (green), deny (red), always (amber), sent (grey), cancel (grey back arrow).

    An unarmed allow or always key is drawn grey with a dimmed mark, so it reads
    as unavailable until the request is known to be on screen."""
    if kind not in ("allow", "deny", "always", "sent", "cancel"):
        raise ValueError(f"unknown decision kind: {kind!r}")
    bg = {"allow": GREEN, "deny": RED, "always": AMBER}.get(kind)
    disabled = not armed and kind in ("allow", "always")
    img, draw = _canvas(_rgb(bg) if bg and not disabled else GREY)
    ink = DIM if disabled else (255, 255, 255)
    if kind == "allow":
        draw.line([(16, 34), (27, 45), (48, 20)], fill=ink, width=6, joint="curve")
    elif kind == "deny":
        draw.line([(19, 19), (45, 45)], fill=ink, width=6)
        draw.line([(45, 19), (19, 45)], fill=ink, width=6)
    elif kind == "always":
        draw.line([(10, 30), (17, 38), (30, 20)], fill=ink, width=4, joint="curve")
        _centered(draw, SIZE / 2, 48, "always", 10, ink)
    elif kind == "sent":
        _centered(draw, SIZE / 2, SIZE / 2, "sent", 14, ink)
    else:
        draw.polygon([(14, 32), (30, 16), (30, 26), (50, 26), (50, 38), (30, 38), (30, 48)], fill=ink)
    return _encode(img)


@lru_cache(maxsize=_CACHE)
def _text_lines(lines: Tuple[str, ...], size: int, cell: Optional[int]) -> str:
    img, draw = _canvas()
    pitch = (SIZE - 4) // max(1, len(lines)) if cell else SIZE // max(1, len(lines))
    for row, line in enumerate(lines):
        y = 2 + row * pitch + pitch / 2
        if cell:
            # Fixed cells keep columns aligned, which matters for dense commands.
            x0 = (SIZE - cell * PREVIEW_COLS) / 2
            for col, ch in enumerate(line):
                _centered(draw, x0 + col * cell + cell / 2, y, ch, size, FG)
        else:
            _centered(draw, SIZE / 2, y, line, size, FG)
    return _encode(img)


def status_key(text: str) -> str:
    """Short status text (`+3`, `no tab`, empty slots). Long words shrink to fit,
    and words are stacked one per line."""
    words = text.split()[:3] or [""]
    size = 18 if len(words) == 1 else 12
    while size > 7 and any(_font(size).getlength(w) > SIZE - 8 for w in words):
        size -= 1
    return _text_lines(tuple(_fit(w, size, SIZE - 8) for w in words), size, None)


@lru_cache(maxsize=_CACHE)
def _preview_keys(text: str, n: int) -> Tuple[str, ...]:
    per_key = PREVIEW_COLS * PREVIEW_LINES
    flat = " ".join(text.split())
    truncated = len(flat) > per_key * n
    if truncated:
        flat = flat[: per_key * n - 1] + "…"
    out = []
    for k in range(n):
        chunk = flat[k * per_key:(k + 1) * per_key]
        lines = tuple(chunk[i:i + PREVIEW_COLS] for i in range(0, per_key, PREVIEW_COLS))
        out.append(_text_lines(lines, 10, 8))
    return tuple(out)


def preview_keys(text: str, n: int) -> List[str]:
    """Split a command preview over `n` keys, 8 characters by 4 lines each,
    wrapping by character. A truncated preview ends with an ellipsis."""
    if n <= 0:
        return []
    return list(_preview_keys(text, n))
