"""The Qt main widget: one frameless, translucent, always-on-top window that
holds every pane and paints them from the #110 model in tokitty.theme.

Geometry, colours, the cat's spot and the pixel rules all come from
theme.py; this module only turns them into QPainter calls. Input, the
menu, position handling and the macOS helper are other slices.
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.qt.pane import QtPane, fit_tag
from tokitty.sprite_raster import raster_rgba
from tokitty.usage_display import ROW_SLOTS

FRAME_INTERVAL_MS = 800   # ui.FRAME_INTERVAL_MS
MODEL_NAME_MAX = 14       # ui.render_usage's fit_tag limit
CAT_CANVAS_SIZE = 112     # ui.CAT_CANVAS_SIZE
CAT_CANVAS_DY = 4         # the sprite sits 4 px below its canvas origin (placement "inside")
TAG_INSET = 6             # ui.py:525-529
HALO_COLOR = QColor(0, 0, 0, 200)
_HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1))

_LEFT_VCENTER = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_RIGHT_VCENTER = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def _qrect(r: T.Rect) -> QRectF:
    return QRectF(r.x, r.y, r.w, r.h)


def palette_signature(palette: Dict[str, str]) -> Tuple[Tuple[str, str], ...]:
    """Hashable stand-in for a palette dict in the image cache key."""
    return tuple(sorted(palette.items()))


class TokittyQtWidget(QWidget):
    def __init__(self, theme: T.Theme, customizations: Sequence[Customization],
                 pane_count: int, *, parent=None):
        super().__init__(parent)
        if len(customizations) != pane_count:
            raise ValueError("need one customization per pane")
        self.theme = theme
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar button on Windows
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Without this Qt hides a Tool window on macOS whenever the app is inactive.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)

        self.panes: List[QtPane] = [
            QtPane(c, on_change=self.update) for c in customizations
        ]
        self.homes: List[str] = [
            T.resolve_home(c.cat_home, i, pane_count) for i, c in enumerate(customizations)
        ]
        self.window_layout = T.layout_window(self.homes)
        self.setFixedSize(*self.window_layout.size)

        self._layouts: Dict[Tuple[str, str], T.CardLayout] = {}
        self._images: Dict[tuple, QImage] = {}
        self._fonts: Optional[Tuple[QFont, QFont, QFont]] = None
        self._watched = None  # the QWindow whose screenChanged is connected

        self.anim = QTimer(self)
        self.anim.timeout.connect(self._next_frames)
        self.anim.start(FRAME_INTERVAL_MS)

    # ---- layout ---------------------------------------------------------

    def rebuild_layout(self, homes: Sequence[str]) -> None:
        """Recompute geometry for new homes. The window moves by the change
        in margins so every card stays where it is on screen."""
        if len(homes) != len(self.panes):
            raise ValueError("need one home per pane")
        old = self.window_layout
        self.homes = list(homes)
        self.window_layout = T.layout_window(self.homes)
        self.setFixedSize(*self.window_layout.size)
        dx = self.window_layout.margins[0] - old.margins[0]
        dy = self.window_layout.margins[1] - old.margins[1]
        if dx or dy:
            self.move(self.x() - dx, self.y() - dy)
        self.update()

    def cat_origin(self, index: int) -> Tuple[float, float]:
        """The sprite's top-left in window coordinates, snapped to a whole
        device pixel."""
        dpr = self.devicePixelRatioF()
        ox, oy = self.window_layout.pane_origins[index]
        px, py = T.placement(self.homes[index]).origin
        return T.snap(ox + px, dpr), T.snap(oy + py, dpr)

    def _layout_for(self, view: str, home: str) -> T.CardLayout:
        key = (view, home)
        layout = self._layouts.get(key)
        if layout is None:
            layout = self._layouts[key] = T.layout_card(self.theme, view, home)
        return layout

    # ---- timers and screen changes --------------------------------------

    def _next_frames(self) -> None:
        for pane in self.panes:
            pane.draw_next_frame()

    def _drop_images(self) -> None:
        self._images.clear()
        self.update()

    def _watch_screen(self) -> None:
        # windowHandle() is None until the native window exists, so this runs
        # from showEvent and after a recreate, never from __init__.
        handle = self.windowHandle()
        if handle is None or handle is self._watched:
            return
        self._watched = handle
        handle.screenChanged.connect(lambda _screen: self._drop_images())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._watch_screen()

    def event(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.DevicePixelRatioChange:
            self._drop_images()
        elif kind == QEvent.Type.WinIdChange:
            # The always-in-front toggle recreates the native window.
            self._watched = None
            self._watch_screen()
        return super().event(event)

    # ---- cat images -----------------------------------------------------

    def _cat_image(self, state: str, frame_index: int, frame, palette, dpr: float,
                   mirrored: bool) -> QImage:
        cell = T.cell_size(dpr)
        key = (state, frame_index, palette_signature(palette), cell, mirrored)
        image = self._images.get(key)
        if image is None:
            w, h, data = raster_rgba(frame, palette, cell)
            image = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
            if mirrored:
                image = image.flipped(Qt.Orientation.Horizontal)
            # Qt never rescales it: device pixels in equals device pixels out.
            image.setDevicePixelRatio(dpr)
            self._images[key] = image
        return image

    # ---- painting -------------------------------------------------------

    def _font_set(self) -> Tuple[QFont, QFont, QFont]:
        if self._fonts is None:
            def make(points: int, bold: bool) -> QFont:
                font = QFont(self.font())
                if sys.platform == "win32":
                    font.setFamily("Segoe UI")
                font.setPointSize(points)
                font.setBold(bold)
                return font
            self._fonts = (
                make(T.FONT_SIZE_LABEL, True),
                make(T.FONT_SIZE_MODEL, True),
                make(T.FONT_SIZE_TEXT, False),
            )
        return self._fonts

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        dpr = self.devicePixelRatioF()
        for i, pane in enumerate(self.panes):
            self._paint_pane(p, i, pane, dpr)
        p.end()

    def _paint_pane(self, p: QPainter, index: int, pane: QtPane, dpr: float) -> None:
        style = T.resolve_pane_style(
            self.theme, pane.customization, pane._accent, index, len(self.panes))
        # The pane's own palette wins: set_appearance may have been handed one.
        palette = pane.palette
        home = self.homes[index]
        ox, oy = self.window_layout.pane_origins[index]
        layout = self._layout_for(pane._view_mode, home)

        p.save()
        p.translate(ox, oy)
        self._paint_card(p, style)
        label_fg = QColor(style.dim if pane._dimmed else style.foreground)
        dim = QColor(style.dim)
        halo = T.halo_active(self.theme.text, style.opacity)
        if pane._view_mode == "models":
            self._paint_models(p, pane, layout, style, label_fg, dim)
        else:
            self._paint_limits(p, pane, layout, style, label_fg, dim, halo)
        if "status" in layout:
            text = pane.status_text if pane._view_mode == "limits" else (
                pane.usage_view.status_text if pane.usage_view is not None else "")
            rect = layout["status"]
            # The status line wraps within the stats column, as Tk's does.
            box = QRectF(rect.x, rect.y, rect.w, T.CARD_HEIGHT - rect.y)
            p.setFont(self._font_set()[2])
            p.setPen(dim)
            p.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                       | Qt.TextFlag.TextWordWrap, text)
        if "account_label" in layout and pane._label:
            p.setFont(self._font_set()[2])
            p.setPen(dim)
            p.drawText(_qrect(layout["account_label"]), _RIGHT_VCENTER, pane._label)
        p.restore()

        self._paint_cat(p, index, pane, palette, home, dpr)
        self._paint_tags(p, index, pane, style, home)

    def _paint_card(self, p: QPainter, style: T.PaneStyle) -> None:
        card = self.theme.card
        color = QColor(style.background)
        color.setAlpha(T.card_alpha(style.opacity))
        rect = QRectF(0, 0, T.CARD_WIDTH, T.CARD_HEIGHT)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, card.radius > 0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        if card.radius:
            p.drawRoundedRect(rect, card.radius, card.radius)
        else:
            p.drawRect(rect)
        if card.border_width:
            half = card.border_width / 2
            pen = QPen(QColor(card.border_color))
            pen.setWidthF(card.border_width)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            inset = rect.adjusted(half, half, -half, -half)
            if card.radius:
                p.drawRoundedRect(inset, card.radius, card.radius)
            else:
                p.drawRect(inset)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _text(self, p: QPainter, color: QColor, rect: QRectF, flags, text: str,
              halo: bool = False) -> None:
        if halo:
            p.setPen(HALO_COLOR)
            for dx, dy in _HALO_OFFSETS:
                p.drawText(rect.translated(dx, dy), flags, text)
        p.setPen(color)
        p.drawText(rect, flags, text)

    def _bar(self, p: QPainter, rect: T.Rect, pct: float, fill: str) -> None:
        bars = self.theme.bars
        track = _qrect(rect)
        width = rect.w * min(max(pct, 0), 100) / 100
        filled = QRectF(rect.x, rect.y, width, rect.h)
        pill = bars.shape == "rounded"
        p.setRenderHint(QPainter.RenderHint.Antialiasing, pill)
        p.setPen(Qt.PenStyle.NoPen)
        for area, color in ((track, bars.track), (filled, fill)):
            if area.width() <= 0:
                continue
            p.setBrush(QColor(color))
            if pill:
                p.drawRoundedRect(area, rect.h / 2, rect.h / 2)
            else:
                p.drawRect(area)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _paint_limits(self, p, pane, layout, style, label_fg, dim, halo) -> None:
        label_font, _model_font, text_font = self._font_set()
        rows = (
            ("session", "SESSION", pane.session_pct, pane.session_reset_text),
            ("weekly", "WEEK", pane.weekly_pct, pane.weekly_reset_text),
        )
        for name, title, pct, reset in rows:
            if f"{name}_label" in layout:
                p.setFont(label_font)
                self._text(p, label_fg, _qrect(layout[f"{name}_label"]), _LEFT_VCENTER, title,
                           halo and T.halo_applies("section_labels"))
            self._bar(p, layout[f"{name}_bar"], pct,
                      T.bar_fill(self.theme, pct, style.bar_fill_override))
            p.setFont(text_font)
            if f"{name}_percent" in layout:
                self._text(p, dim, _qrect(layout[f"{name}_percent"]), _LEFT_VCENTER,
                           f"{pct:.0f}%", halo and T.halo_applies("percent"))
            if f"{name}_reset" in layout:
                # Tk shows "42% · resets ..." as one run; keep the dot when
                # the percent is there to separate from.
                lead = "· " if f"{name}_percent" in layout else ""
                self._text(p, dim, _qrect(layout[f"{name}_reset"]), _LEFT_VCENTER, lead + reset)

    def _paint_models(self, p, pane, layout, style, label_fg, dim) -> None:
        _label_font, model_font, text_font = self._font_set()
        view = pane.usage_view
        rows = view.rows if view is not None else ()
        for i in range(ROW_SLOTS):
            row = rows[i] if i < len(rows) else None
            if f"model_name_{i}" in layout and row is not None:
                p.setFont(model_font)
                self._text(p, label_fg, _qrect(layout[f"model_name_{i}"]), _LEFT_VCENTER,
                           fit_tag(row.label, MODEL_NAME_MAX))
                p.setFont(text_font)
                self._text(p, dim, _qrect(layout[f"model_value_{i}"]), _RIGHT_VCENTER,
                           row.value_text)
            # Like Tk, the track is always there; the fill only when there is a share.
            pct = row.bar_pct if row is not None else 0.0
            fill = T.bar_fill(self.theme, view.ramp_pct if view else None, style.bar_fill_override)
            self._bar(p, layout[f"model_bar_{i}"], pct, fill)

    def _paint_cat(self, p, index, pane, palette, home, dpr) -> None:
        frame, frame_index = pane.current_frame()
        image = self._cat_image(pane._current_state, frame_index, frame, palette, dpr,
                                T.placement(home).mirrored)
        x, y = self.cat_origin(index)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(QPointF(x, y), image)

    def _paint_tags(self, p, index, pane, style, home) -> None:
        if "cat_tags" not in self.theme.text.show:
            return
        if not (pane._driving_tag or pane._tool_label):
            return
        # Tags are relative to the cat's canvas, which follows an outside cat.
        ox, oy = self.window_layout.pane_origins[index]
        px, py = T.placement(home).origin
        cx, cy = ox + px, oy + py - CAT_CANVAS_DY
        p.setFont(self._font_set()[2])
        if pane._driving_tag:
            box = QRectF(cx + TAG_INSET, cy, CAT_CANVAS_SIZE - TAG_INSET,
                         CAT_CANVAS_SIZE - TAG_INSET)
            self._text(p, QColor(style.dim), box,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       pane._driving_tag)
        if pane._tool_label:
            box = QRectF(cx + TAG_INSET, cy + TAG_INSET, CAT_CANVAS_SIZE - TAG_INSET,
                         T.SMALL_H)
            self._text(p, QColor(style.foreground), box,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       fit_tag(pane._tool_label))
