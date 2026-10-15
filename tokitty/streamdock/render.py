"""Render the 64x64 key images for the Stream Dock M18.

Every function returns a data:image/png;base64 string, memoised on its inputs
so a steady deck costs nothing per refresh. Images are drawn with Qt (QImage
and QPainter) in Aileron Regular, a font that ships in tokitty/streamdock/fonts
and is registered by register_font(), so the keys look the same on every OS
whatever fonts the system has. PNG encoding settings are fixed so equal inputs
give equal bytes within one process.

Rendering needs a QGuiApplication and a registered font. It runs off the GUI
thread (DeckServer calls the image function from its request handlers), which
Qt allows for QPainter on a QImage.
"""
from __future__ import annotations

import base64
import threading
from functools import lru_cache
from importlib import resources
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPen, QPolygonF

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
FONT_FILE = "Aileron-Regular.ttf"

BG = (24, 24, 28)
FG = (235, 235, 235)
DIM = (150, 150, 158)
GREY = (84, 84, 92)
TRACK = (52, 52, 60)
_CACHE = 256

_Palette = Tuple[Tuple[str, str], ...]
_Color = Tuple[int, int, int]

_font_lock = threading.Lock()
_family: Optional[str] = None


def register_font() -> str:
    """Load the packaged font into Qt's font database and return its family name.

    Call once on the GUI thread, after the QGuiApplication exists and before
    any key is drawn. Calling it again returns the same family."""
    global _family
    with _font_lock:
        if _family is not None:
            return _family
        path = resources.files("tokitty.streamdock") / "fonts" / FONT_FILE
        # From bytes, so a package imported from a zip works as well.
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"could not read the Stream Dock key font at {path}: {exc}") from exc
        font_id = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if not families:
            raise RuntimeError(f"could not load the Stream Dock key font from {path}")
        _family = families[0]
        return _family


def font_family() -> str:
    """The registered family, or an error: drawing with a fallback font would
    silently change every key."""
    family = _family
    if family is None:
        raise RuntimeError("the Stream Dock key font is not registered; call render.register_font() first")
    return family


def _rgb(color: str) -> _Color:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _qcolor(color: _Color) -> QColor:
    return QColor(*color)


def _font(size: int) -> QFont:
    """A new QFont per call: QFont is reentrant, not thread-safe, and the
    request handlers draw keys on several threads at once."""
    font = QFont(font_family())
    font.setPixelSize(size)
    return font


def _encode(img: QImage) -> str:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise RuntimeError("could not encode a Stream Dock key image as PNG")
    buf.close()
    return "data:image/png;base64," + base64.b64encode(data.data()).decode("ascii")


class _Canvas:
    """A 64x64 RGB image and the painter that draws on it."""

    def __init__(self, bg: _Color = BG) -> None:
        font_family()  # fail before a painter is open
        self.img = QImage(SIZE, SIZE, QImage.Format.Format_RGB32)
        self.img.fill(_qcolor(bg))
        self.p = QPainter(self.img)
        self.p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def finish(self) -> str:
        self.p.end()
        return _encode(self.img)

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: _Color) -> None:
        """Fill the pixels x0..x1 by y0..y1, both ends included."""
        self.p.fillRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1, _qcolor(color))

    def border(self, color: _Color) -> None:
        last = SIZE - 1
        self.rect(0, 0, last, BORDER - 1, color)
        self.rect(0, last - BORDER + 1, last, last, color)
        self.rect(0, 0, BORDER - 1, last, color)
        self.rect(last - BORDER + 1, 0, last, last, color)

    def line(self, points, color: _Color, width: int) -> None:
        pen = QPen(_qcolor(color), width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.p.setPen(pen)
        self.p.setBrush(Qt.BrushStyle.NoBrush)
        # Pixel centres sit at +0.5 in Qt's coordinates.
        self.p.drawPolyline(QPolygonF([QPointF(x + 0.5, y + 0.5) for x, y in points]))

    def polygon(self, points, color: _Color) -> None:
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(_qcolor(color))
        self.p.drawPolygon(QPolygonF([QPointF(x + 0.5, y + 0.5) for x, y in points]))

    def text(self, x: float, y: float, text: str, size: int, color: _Color, anchor: str) -> None:
        """Draw `text` at (x, y). `anchor` is two letters like Pillow's: the
        first is horizontal (l, m, r), the second vertical (t = ascender line,
        m = halfway between ascender and descender)."""
        font = _font(size)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text)
        left = {"l": x, "m": x - width / 2, "r": x - width}[anchor[0]]
        top = {"t": y, "m": y - (metrics.ascent() + metrics.descent()) / 2}[anchor[1]]
        self.p.setFont(font)
        self.p.setPen(_qcolor(color))
        self.p.drawText(QPointF(left, top + metrics.ascent()), text)

    def centered(self, cx: float, cy: float, text: str, size: int, color: _Color) -> None:
        self.text(cx, cy, text, size, color, "mm")


def _fit(text: str, size: int, width: int) -> str:
    """Truncate `text` with a trailing ellipsis so it renders within `width` px."""
    metrics = QFontMetricsF(_font(size))
    if metrics.horizontalAdvance(text) <= width:
        return text
    while text and metrics.horizontalAdvance(text + "..") > width:
        text = text[:-1]
    return text + ".."


def _sprite_image(sprite_state: str, frame_index: int, palette: _Palette) -> QImage:
    frames = sprites.get_frames(sprite_state)
    frame = frames[frame_index % len(frames)]
    grid = raster_frame(frame, dict(palette), SPRITE_SCALE, bytes(BG))
    h, w = len(grid), len(grid[0])
    raw = b"".join(px for row in grid for px in row)
    # copy() so the image owns its pixels rather than pointing at `raw`.
    return QImage(raw, w, h, w * 3, QImage.Format.Format_RGB888).copy()


@lru_cache(maxsize=_CACHE)
def _session_key(sprite_state: str, frame_index: int, palette: _Palette, title: str, accent: bool) -> str:
    canvas = _Canvas()
    cat = _sprite_image(sprite_state, frame_index, palette)
    # Whatever size the frame grid yields is centred in the area above the strip.
    canvas.p.drawImage((SIZE - cat.width()) // 2, max(0, (SIZE - TITLE_STRIP - cat.height()) // 2), cat)
    inner = SIZE - 2 * BORDER - 4
    text = _fit(title, 8, inner)
    canvas.centered(SIZE / 2, SIZE - TITLE_STRIP / 2 - 1, text, 8, FG)
    if accent:
        canvas.border(_rgb(AMBER))
    return canvas.finish()


def session_key(sprite_state: str, frame_index: int, palette: Dict[str, str], title: str, accent: bool = False) -> str:
    """A cat with a title strip below. `frame_index` wraps modulo the state's
    frame count, so callers can pass a running counter."""
    return _session_key(sprite_state, frame_index, tuple(sorted(palette.items())), title, bool(accent))


@lru_cache(maxsize=_CACHE)
def _usage_key(session_pct: int, weekly_pct: int, warn: bool) -> str:
    canvas = _Canvas()
    for i, (label, pct) in enumerate((("5h", session_pct), ("wk", weekly_pct))):
        top = 5 + i * 29
        canvas.text(7, top, label, 9, DIM, "lt")
        canvas.text(SIZE - 7, top, f"{pct}%", 12, FG, "rt")
        bar_y = top + 15
        canvas.rect(7, bar_y, SIZE - 8, bar_y + 5, TRACK)
        fill_w = round((SIZE - 14) * pct / 100)
        if fill_w > 0:
            canvas.rect(7, bar_y, 7 + fill_w - 1, bar_y + 5, _rgb(bar_color(pct)))
    if warn:
        canvas.border(_rgb(RED))
    return canvas.finish()


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
    canvas = _Canvas(_rgb(bg) if bg and not disabled else GREY)
    ink = DIM if disabled else (255, 255, 255)
    if kind == "allow":
        canvas.line([(16, 34), (27, 45), (48, 20)], ink, 6)
    elif kind == "deny":
        canvas.line([(19, 19), (45, 45)], ink, 6)
        canvas.line([(45, 19), (19, 45)], ink, 6)
    elif kind == "always":
        canvas.line([(10, 30), (17, 38), (30, 20)], ink, 4)
        canvas.centered(SIZE / 2, 48, "always", 10, ink)
    elif kind == "sent":
        canvas.centered(SIZE / 2, SIZE / 2, "sent", 14, ink)
    else:
        canvas.polygon([(14, 32), (30, 16), (30, 26), (50, 26), (50, 38), (30, 38), (30, 48)], ink)
    return canvas.finish()


@lru_cache(maxsize=_CACHE)
def _text_lines(lines: Tuple[str, ...], size: int, cell: Optional[int]) -> str:
    canvas = _Canvas()
    pitch = (SIZE - 4) // max(1, len(lines)) if cell else SIZE // max(1, len(lines))
    for row, line in enumerate(lines):
        y = 2 + row * pitch + pitch / 2
        if cell:
            # Fixed cells keep columns aligned, which matters for dense commands.
            x0 = (SIZE - cell * PREVIEW_COLS) / 2
            for col, ch in enumerate(line):
                canvas.centered(x0 + col * cell + cell / 2, y, ch, size, FG)
        else:
            canvas.centered(SIZE / 2, y, line, size, FG)
    return canvas.finish()


def status_key(text: str) -> str:
    """Short status text (`+3`, `no tab`, empty slots). Long words shrink to fit,
    and words are stacked one per line."""
    words = text.split()[:3] or [""]
    size = 18 if len(words) == 1 else 12
    while size > 7:
        metrics = QFontMetricsF(_font(size))
        if not any(metrics.horizontalAdvance(w) > SIZE - 8 for w in words):
            break
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
