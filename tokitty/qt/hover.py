"""The hover popup (#130): a small frameless window the card paints itself.

Not a QToolTip: the tooltip's look is the platform's, and the card's own
colours, border and fonts are what make this read as part of it. The popup
never takes focus, never activates the app and ignores the mouse, so it can
sit next to the pointer without changing what the pointer is over.

place_popup is pure arithmetic on tuples so the flipping rules are tested
without a screen.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tokitty.qt import macos

# The pointer rests this long on a target before the popup shows.
HOVER_DELAY_MS = 400
# Between the target and the popup.
GAP = 8
PAD_X = 8
PAD_Y = 6

Rect = Tuple[int, int, int, int]       # x, y, w, h
Area = Tuple[int, int, int, int]       # x0, y0, x1, y1


def place_popup(size: Tuple[int, int], target: Rect, area: Area, gap: int = GAP) -> Tuple[int, int]:
    """Top-left of a popup of `size` beside `target`, kept inside `area`.

    Right of the target by default, level with its top; when that would leave
    the area it flips to the left, and when neither side fits (a narrow
    screen) above the target, then below it. The result is always clamped
    into the area so a popup bigger than the room left still starts on screen.
    """
    w, h = size
    tx, ty, tw, th = target
    x0, y0, x1, y1 = area
    x, y = tx + tw + gap, ty
    if x + w > x1:
        x = tx - gap - w
        if x < x0:
            x = tx
            y = ty - gap - h
            if y < y0:
                y = ty + th + gap
    x = max(x0, min(x, x1 - w))
    y = max(y0, min(y, y1 - h))
    return x, y


@dataclass(frozen=True)
class PopupLook:
    """Everything the popup borrows from the card it describes."""
    background: str
    foreground: str
    dim: str
    border_color: str
    border_width: float
    radius: float
    first_font: QFont
    font: QFont


class HoverPopup(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._lines: Tuple[str, ...] = ()
        self._look = None
        self._native_done = False

    @property
    def text(self) -> str:
        return "\n".join(self._lines)

    def set_content(self, text: str, look: PopupLook) -> None:
        """Take new text and look and size to fit them."""
        self._lines = tuple(text.split("\n"))
        self._look = look
        first, rest = QFontMetrics(look.first_font), QFontMetrics(look.font)
        width = max(
            [first.horizontalAdvance(self._lines[0])]
            + [rest.horizontalAdvance(line) for line in self._lines[1:]]
        )
        self._line_h = max(first.lineSpacing(), rest.lineSpacing())
        self.setFixedSize(width + 2 * PAD_X + 1, self._line_h * len(self._lines) + 2 * PAD_Y)
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._native_done:
            self._native_done = True
            # The same fix the card gets: nothing here may activate the app.
            if (sys.platform == "darwin" and self.windowHandle() is not None
                    and QGuiApplication.platformName() == "cocoa"):
                macos.apply(int(self.winId()))

    def paintEvent(self, event) -> None:
        look = self._look
        if look is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        half = look.border_width / 2
        box = QRectF(self.rect()).adjusted(half, half, -half, -half)
        pen = QPen(QColor(look.border_color))
        pen.setWidthF(look.border_width)
        p.setPen(pen if look.border_width else Qt.PenStyle.NoPen)
        p.setBrush(QColor(look.background))
        if look.radius:
            p.drawRoundedRect(box, look.radius, look.radius)
        else:
            p.drawRect(box)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for i, line in enumerate(self._lines):
            font = look.first_font if i == 0 else look.font
            p.setFont(font)
            p.setPen(QColor(look.foreground if i == 0 else look.dim))
            ascent = QFontMetrics(font).ascent()
            p.drawText(QPointF(PAD_X, PAD_Y + i * self._line_h + ascent), line)
        p.end()
