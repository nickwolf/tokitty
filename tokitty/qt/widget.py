"""The Qt main widget: one frameless, translucent, always-on-top window that
holds every pane and paints them from the #110 model in tokitty.theme.

Geometry, colours, the cat's spot and the pixel rules all come from
theme.py; this module only turns them into QPainter calls. Input, the

"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QRegion
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from tokitty import shadow as shadow_mod
from tokitty import sprites
from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.display import reset_phrase
from tokitty.hover import limit_hover_text
from tokitty.menu import MenuItem
from tokitty.qt import macos, position
from tokitty.qt.hover import HOVER_DELAY_MS, HoverPopup, PopupLook, place_popup
from tokitty.qt.menu import render_menu
from tokitty.qt.pane import QtPane, fit_tag, monotonic_ms, palette_signature  # noqa: F401
from tokitty.sprite_raster import raster_rgba
from tokitty.sprites import known_grids
from tokitty.wander import Pose
from tokitty.usage_display import ROW_SLOTS, hover_text as model_hover_text

MODEL_NAME_MAX = 14       # ui.render_usage's fit_tag limit
CAT_CANVAS_SIZE = 112     # ui.CAT_CANVAS_SIZE
CAT_CANVAS_DY = 4         # the sprite sits 4 px below its canvas origin (placement "inside")
TAG_INSET = 6
NOTCH_W = 1               # logical px of each threshold notch (#131)
HALO_COLOR = QColor(0, 0, 0, 200)
# Card text sits this far in from its layout rect, the inset the original Tk
# label drew with, kept so the card looks as it always has. Measured on
# Windows at 100%.
TK_LABEL_PAD_X = 3
TK_LABEL_PAD_Y = 2
# The chip under a cat tag, as the original Windows card drew it.
TAG_CHIP_PAD_X = 3
TAG_CHIP_PAD_Y = 1
# Frame ticks run this far ahead of the clock so panes whose frames fall due a
# few ms apart advance and repaint together instead of waking the loop twice.
COALESCE_MS = 8
# How far a pane's cached card image reaches past the card on every side, for
# the halo offsets, the border and any text that overflows its box.
CARD_PAD = 4
_HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1))

_LEFT_VCENTER = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_RIGHT_VCENTER = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_LEFT_BOTTOM = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
_RIGHT_BOTTOM = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
# Refined card: room kept between the status line and the account label.
STATUS_LABEL_GAP = 6


def is_context_press(button, modifiers, platform: str = sys.platform) -> bool:
    """A press that opens the menu: the right button, or on macOS a left
    press with the Control key, which Qt reports as MetaModifier there
    (Command is ControlModifier)."""
    if button == Qt.MouseButton.RightButton:
        return True
    return (platform == "darwin" and button == Qt.MouseButton.LeftButton
            and bool(modifiers & Qt.KeyboardModifier.MetaModifier))


def _qrect(r: T.Rect) -> QRectF:
    return QRectF(r.x, r.y, r.w, r.h)


def _text_rect(r: T.Rect, right: bool = False) -> QRectF:
    """A layout rect moved by the card text inset (TK_LABEL_PAD_X/Y). Right-aligned text moves left by the same amount."""
    dx = -TK_LABEL_PAD_X if right else TK_LABEL_PAD_X
    return QRectF(r.x + dx, r.y + TK_LABEL_PAD_Y, r.w, r.h)


def _bar_override(style: T.PaneStyle, name: str):
    """The pane style's colour override for the "session" or "weekly" bar."""
    return style.session_bar_override if name == "session" else style.weekly_bar_override


class TokittyQtWidget(QWidget):
    def __init__(self, theme: T.Theme, customizations: Sequence[Customization],
                 pane_count: int, *, parent=None, position_path=None,
                 build_menu_model: Optional[Callable[[int], List[MenuItem]]] = None,
                 clock: Callable[[], int] = monotonic_ms):
        super().__init__(parent)
        if len(customizations) != pane_count:
            raise ValueError("need one customization per pane")
        self.theme = theme
        self.position_path = position_path
        # The owner's model factory; called with the clicked pane on every open.
        self.build_menu_model = build_menu_model
        # Called after a menu action ran (the host's notify_state_changed).
        self.on_menu_action_done: Optional[Callable[[], None]] = None
        self._always_on_top = True  # starts on, never persisted
        self._drag: Optional[Tuple[int, int]] = None
        self._native_done = False  # _after_native_window ran for this native window
        # Hover popup (#130). The target is (pane index, "cat" | "limits" | "models",
        # row key); _hover_pos is the pointer in window coordinates while it is
        # inside the window. Set before the panes, whose callbacks read them.
        self.hover_delay_ms = HOVER_DELAY_MS
        self._hover_pos: Optional[Tuple[float, float]] = None
        self._hover_target: Optional[tuple] = None
        self._hover_win: Optional[HoverPopup] = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._hover_fire)
        self.setMouseTracking(True)
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

        # One monotonic ms clock for the panes' schedules and the timer.
        self._clock = clock
        self.homes: List[str] = [
            T.resolve_home(c.cat_home, i, pane_count) for i, c in enumerate(customizations)
        ]
        self.panes: List[QtPane] = [
            QtPane(c, on_change=self._pane_changed, clock=lambda: self._clock(), spot=home,
                   roams=self._roams(c, i, pane_count))
            for i, (c, home) in enumerate(zip(customizations, self.homes))
        ]
        self.window_layout = T.layout_window(self.homes, wander=theme.cat.wander)
        # Per pane: where its cat can be drawn, to remask only when that changed.
        self._mask_places = self._current_mask_places()
        self.setFixedSize(*self.window_layout.size)

        self._layouts: Dict[Tuple[str, str], T.CardLayout] = {}
        self._push_bar_rects()
        # Keyed by the frame grid's identity (sprites keeps the grids alive
        # for the process), so repeated frames share one image.
        self._images: Dict[tuple, QImage] = {}
        # The shadow images by (cell alphas, device cell size).
        self._shadow_images: Dict[tuple, QImage] = {}
        # Per pane: the last shadow_for inputs and result. The dirty rect and
        # the paint both ask for the same frame's shadow, and composed grids
        # are interned, so an identity check finds the repeat.
        self._shadow_memo: Dict[int, tuple] = {}
        # Per pane: the window rect its cat, shadow and tags last painted, so a
        # frame that moves the cat also repaints where it was.
        self._painted: Dict[int, QRectF] = {}
        # Per pane: (inputs signature, card image, window-space origin of the
        # image). The card is drawn once and blitted until an input changes.
        self._cards: Dict[int, tuple] = {}
        # Per pane: ((theme, customization, accent), PaneStyle).
        self._styles: Dict[int, tuple] = {}
        # Window-space origin of the card image being drawn (0, 0 otherwise),
        # for the pixel tests that map text rects back to window coordinates.
        self._card_origin = (0.0, 0.0)
        self._fonts: Optional[Tuple[QFont, QFont, QFont]] = None
        self._watched = None  # the QWindow whose screenChanged is connected

        # Single-shot, armed for the earliest frame any pane has due. Precise
        # so a 40 ms blink is not rounded to Qt's 5% coarse granularity.
        self.anim = QTimer(self)
        self.anim.setSingleShot(True)
        self.anim.setTimerType(Qt.TimerType.PreciseTimer)
        self.anim.timeout.connect(self._next_frames)
        self._arm()

        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_application_state)

    # ---- layout ---------------------------------------------------------

    def _roams(self, custom: Customization, index: int, count: int) -> bool:
        """Whether this pane's cat goes on trips: it needs the wander setting
        and a way up to the top edge, which only a first-row pane has."""
        return self.theme.cat.wander and "on_top" in T.reachable_spots(
            self.theme, custom.cat_home, index, count)

    def rebuild_layout(self, homes: Sequence[str]) -> None:
        """Recompute geometry for new homes (or a changed wander setting).
        The window moves by the change in margins so every card stays where
        it is on screen."""
        if len(homes) != len(self.panes):
            raise ValueError("need one home per pane")
        old = self.window_layout
        self.hover_cancel()
        self.homes = list(homes)
        self.window_layout = T.layout_window(self.homes, wander=self.theme.cat.wander)
        self.setFixedSize(*self.window_layout.size)
        self._painted.clear()
        count = len(self.panes)
        for i, (pane, home) in enumerate(zip(self.panes, self.homes)):
            pane.set_spot(home, self._roams(pane.customization, i, count))
        self._push_bar_rects()
        dx = self.window_layout.margins[0] - old.margins[0]
        dy = self.window_layout.margins[1] - old.margins[1]
        if dx or dy:
            self.move(self.x() - dx, self.y() - dy)
        self._update_mask()
        self._arm()
        self.update()

    def set_theme(self, theme: T.Theme) -> None:
        """Swap the theme and repaint. The cached card layouts and cat images
        belong to the old one; the homes are recomputed in case the model ever
        ties them to the theme, and the window only rebuilds when they or the
        wander setting moved its layout."""
        self.theme = theme
        self.hover_cancel()
        self._layouts.clear()
        self._images.clear()
        self._shadow_images.clear()
        self._cards.clear()
        self._styles.clear()
        count = len(self.panes)
        homes = [T.resolve_home(pane.customization.cat_home, i, count)
                 for i, pane in enumerate(self.panes)]
        if homes != self.homes or self.window_layout != T.layout_window(
                homes, wander=theme.cat.wander):
            self.rebuild_layout(homes)
        else:
            # The wander setting can change who roams without moving the window.
            for i, pane in enumerate(self.panes):
                pane.set_spot(pane.spot, self._roams(pane.customization, i, count))
            self._push_bar_rects()
        self.update()

    def pane_anchor(self, index: int) -> Tuple[int, int]:
        """Where a window about the pane at `index` opens: 16 logical pixels
        in from the card's top-left corner, on screen."""
        ox, oy = self.window_layout.pane_origins[index]
        return self.x() + ox + 16, self.y() + oy + 16

    def grid_size(self) -> Tuple[int, int]:
        """The cards' bounding size: the window minus its margins."""
        left, top, right, bottom = self.window_layout.margins
        w, h = self.window_layout.size
        return w - left - right, h - top - bottom

    def first_card_pos(self) -> Tuple[int, int]:
        """Where the first card is on screen (window position plus margins)."""
        return self.x() + self.window_layout.margins[0], self.y() + self.window_layout.margins[1]

    def cat_origin(self, index: int, offset: Tuple[int, int] = (0, 0)) -> Tuple[float, float]:
        """The sprite's top-left in window coordinates, snapped to a whole
        device pixel, where the pane's pose puts it. `offset` is a frame's
        move in cells; a mirrored cat moves the other way in x."""
        dpr = self.devicePixelRatioF()
        ox, oy = self.window_layout.pane_origins[index]
        pane = self.panes[index]
        return self._origin_of(ox, oy, pane.pose, offset, dpr, len(pane.grid()[0]))

    @staticmethod
    def _origin_of(ox: int, oy: int, pose: Pose, offset: Tuple[int, int], dpr: float,
                   cols: int = T.SPRITE_W // sprites.SCALE):
        """A grid wider than the cat (a move's props, see sprites.grid_size)
        has the cat in its left columns; mirrored, those end up on the right,
        so the image starts that much further left and the cat stays put."""
        dx, dy = offset
        if pose.mirrored:
            dx = -dx - (cols - T.SPRITE_W // sprites.SCALE)
        py = T.placement(pose.spot).origin[1]
        return (T.snap(ox + pose.x + dx * sprites.SCALE, dpr),
                T.snap(oy + py + dy * sprites.SCALE, dpr))

    def _push_bar_rects(self) -> None:
        """Tell each pane where its bars are, for the cat that watches them.
        Straight from layout_card: this runs only when layouts change, and
        must not refill the cache set_theme just emptied."""
        for pane, home in zip(self.panes, self.homes):
            layout = T.layout_card(self.theme, "limits", home)
            pane.set_bar_rects(layout["session_bar"], layout["weekly_bar"])

    def _layout_for(self, view: str, home: str) -> T.CardLayout:
        key = (view, home)
        layout = self._layouts.get(key)
        if layout is None:
            layout = self._layouts[key] = T.layout_card(self.theme, view, home)
        return layout

    # ---- timers and screen changes --------------------------------------

    def _pane_changed(self) -> None:
        """A pane re-rendered: repaint, forget images no pane shows any more (state or coat),
        and re-arm (a new state restarts its loop, so its due time moved)."""
        self.update()
        self._prune_images()
        self._sync_mask()
        self._arm()
        self._hover_recheck()

    def _prune_images(self) -> None:
        live_grids = {id(grid) for pane in self.panes
                      for name in {pane.playing, pane.target}
                      for grid in known_grids(name)}
        live_palettes = {pane.palette_key for pane in self.panes}
        for key in [k for k in self._images if k[0] not in live_grids or k[1] not in live_palettes]:
            del self._images[key]

    def _arm(self) -> None:
        """Wake at the earliest frame due on any pane; nothing due, no timer."""
        dues = [d for d in (pane.next_due_ms() for pane in self.panes) if d is not None]
        if not dues:
            self.anim.stop()
            return
        self.anim.start(max(0, math.ceil(min(dues) - self._clock())))

    def _next_frames(self) -> None:
        # Ticking ahead of the clock advances every frame due within the
        # window now. Each track's next due time still counts from its own
        # schedule, so the shift does not accumulate.
        now = self._clock() + COALESCE_MS
        for index, pane in enumerate(self.panes):
            if pane.tick(now):
                # Only the cat changed: repaint its rect, not the window.
                self.update(self._cat_dirty_rect(index, pane))
        self._sync_mask()
        self._arm()
        self._hover_recheck()

    def _cat_dirty_rect(self, index: int, pane: QtPane):
        """Window rect (device-independent) a cat frame change can touch: what
        the last paint covered and what this frame covers, so a moved cat
        leaves no ghost."""
        rect = self._art_rect(index, pane)
        before = self._painted.get(index)
        if before is not None:
            rect = rect.united(before)
        return rect.toAlignedRect()

    def _art_rect(self, index: int, pane: QtPane) -> QRectF:
        """The sprite, its shadow and the tag boxes drawn over it, padded so
        nothing clips."""
        rect = self._cat_rect(index, pane)
        placed = self._shadow_placed(index, pane)
        if placed is not None:
            rect = rect.united(placed[0])
        if self._tags_shown(pane):
            driving, tool, _side = self.tag_boxes(index, self.homes[index])
            pad = TAG_CHIP_PAD_X + 1
            rect = rect.united(driving.adjusted(-pad, -pad, pad, pad)).united(
                tool.adjusted(-pad, -pad, pad, pad))
        return rect.adjusted(-1, -1, 1, 1)

    def _cat_rect(self, index: int, pane: QtPane) -> QRectF:
        grid = pane.grid()
        cell = T.cell_size(self.devicePixelRatioF())
        dpr = self.devicePixelRatioF()
        x, y = self.cat_origin(index, pane.offset())
        return QRectF(x, y, len(grid[0]) * cell / dpr, len(grid) * cell / dpr)

    def _shadow_placed(self, index: int, pane: QtPane):
        """(window rect, cells) of the pane's shadow, or None when it is off
        or the frame has no body. The rect is snapped to device pixels."""
        if not self.theme.cat.shadow:
            return None
        pose = pane.pose
        rest, grid, offset = pane.rest_grid(), pane.grid(), pane.offset()
        memo = self._shadow_memo.get(index)
        if memo is not None and memo[0] is rest and memo[1] is grid and memo[2:4] == (offset, pose):
            placed = memo[4]
        else:
            placed = shadow_mod.shadow_for(rest, grid, offset, pose.spot, pose.mirrored, x=pose.x)
            self._shadow_memo[index] = (rest, grid, offset, pose, placed)
        if placed is None:
            return None
        dpr = self.devicePixelRatioF()
        ox, oy = self.window_layout.pane_origins[index]
        cell = T.cell_size(dpr)
        rect = QRectF(T.snap(ox + placed.x, dpr), T.snap(oy + placed.y, dpr),
                      len(placed.cells[0]) * cell / dpr, len(placed.cells) * cell / dpr)
        return rect, placed.cells

    def _tags_shown(self, pane: QtPane) -> bool:
        return "cat_tags" in self.theme.text.show and bool(pane._driving_tag or pane._tool_label)

    def _drop_images(self) -> None:
        self._images.clear()
        self._shadow_images.clear()
        self._cards.clear()
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
        if not self._native_done:
            self._native_done = True
            self._after_native_window()

    def event(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.DevicePixelRatioChange:
            self._drop_images()
        elif kind == QEvent.Type.WinIdChange:
            # The always-in-front toggle recreates the native window.
            self._watched = None
            self._native_done = False
            self._watch_screen()
        return super().event(event)

    # ---- cat images -----------------------------------------------------

    def _cat_image(self, frame, palette, palette_key, dpr: float, mirrored: bool) -> QImage:
        cell = T.cell_size(dpr)
        key = (id(frame), palette_key, cell, mirrored)
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

    def _shadow_image(self, cells, dpr: float) -> QImage:
        cell = T.cell_size(dpr)
        key = (cells, cell)
        image = self._shadow_images.get(key)
        if image is None:
            image = QImage(len(cells[0]) * cell, len(cells) * cell,
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            ip = QPainter(image)
            ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            for r, row in enumerate(cells):
                for c, alpha in enumerate(row):
                    if alpha:
                        ip.fillRect(c * cell, r * cell, cell, cell,
                                    QColor(*shadow_mod.COLOR, alpha))
            ip.end()
            image.setDevicePixelRatio(dpr)
            self._shadow_images[key] = image
        return image

    # ---- painting -------------------------------------------------------

    def _make_font(self, points: int, weight: QFont.Weight) -> QFont:
        """One of the card's fonts. The seam the pixel tests override."""
        font = QFont(self.font())
        if sys.platform == "win32":
            font.setFamily("Segoe UI")
        font.setPointSize(points)
        font.setWeight(weight)
        return font

    def _font_set(self) -> Tuple[QFont, QFont, QFont, QFont, QFont]:
        """(label, model, text, label_semibold, model_semibold). The classic
        card uses the bold pair, the refined card the semibold one."""
        if self._fonts is None:
            bold, regular, semi = QFont.Weight.Bold, QFont.Weight.Normal, QFont.Weight.DemiBold
            self._fonts = (
                self._make_font(T.FONT_SIZE_LABEL, bold),
                self._make_font(T.FONT_SIZE_MODEL, bold),
                self._make_font(T.FONT_SIZE_TEXT, regular),
                self._make_font(T.FONT_SIZE_LABEL, semi),
                self._make_font(T.FONT_SIZE_MODEL, semi),
            )
        return self._fonts

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        dpr = self.devicePixelRatioF()
        dirty = QRectF(event.rect())
        for i, pane in enumerate(self.panes):
            self._paint_pane(p, i, pane, dpr, dirty)
        p.end()

    def _paint_pane(self, p: QPainter, index: int, pane: QtPane, dpr: float,
                    dirty: QRectF) -> None:
        """Paint what intersects `dirty`, in the same order as a full repaint
        (card, then shadow, then cat, then tags), so a cat-only update stays
        correct."""
        style = self._style_for(index, pane)
        # The pane's own palette wins: set_appearance may have been handed one.
        palette = pane.palette
        home = self.homes[index]
        ox, oy = self.window_layout.pane_origins[index]
        layout = self._layout_for(pane._view_mode, home)

        # A little slack for the halo offsets and the card border.
        card = QRectF(ox, oy, T.CARD_WIDTH, T.CARD_HEIGHT).adjusted(
            -CARD_PAD, -CARD_PAD, CARD_PAD, CARD_PAD)
        if card.intersects(dirty):
            self._draw_card(p, index, pane, style, layout, ox, oy, dpr)
        cat = self._cat_rect(index, pane)
        placed = self._shadow_placed(index, pane)
        art = cat if placed is None else cat.united(placed[0])
        if art.adjusted(-1, -1, 1, 1).intersects(dirty):
            if placed is not None:
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                p.drawImage(placed[0].topLeft(), self._shadow_image(placed[1], dpr))
            self._paint_cat(p, index, pane, palette, home, dpr)
            self._painted[index] = self._art_rect(index, pane)
        if self._tags_shown(pane):
            driving, tool, _side = self.tag_boxes(index, home)
            pad = TAG_CHIP_PAD_X + 1
            if driving.united(tool).adjusted(-pad, -pad, pad, pad).intersects(dirty):
                self._paint_tags(p, index, pane, style, home)

    def _style_for(self, index: int, pane: QtPane) -> T.PaneStyle:
        """The pane's resolved style, redone only when the theme or the
        customization is a different object or the accent flipped
        (set_appearance and set_customization replace the customization,
        never mutate it)."""
        entry = self._styles.get(index)
        if (entry is not None and entry[0] is self.theme and entry[1] is pane.customization
                and entry[2] == pane._accent):
            return entry[3]
        style = T.resolve_pane_style(
            self.theme, pane.customization, pane._accent, index, len(self.panes))
        self._styles[index] = (self.theme, pane.customization, pane._accent, style)
        return style

    def _card_signature(self, pane: QtPane, style: T.PaneStyle, layout, ox, oy, dpr) -> tuple:
        """Everything _paint_card_contents reads, so the cached card image is
        redrawn exactly when one of them changes."""
        models = pane._view_mode == "models"
        if models:
            body = (pane.usage_view,)
        else:
            body = (pane.session_pct, pane.weekly_pct, pane.session_reset_text,
                    pane.weekly_reset_text, pane.status_text)
        return (id(self.theme), self.theme.card.design, id(layout), dpr, ox, oy, models, pane._dimmed, pane._label,
                style.background, style.foreground, style.dim, style.opacity,
                style.session_bar_override, style.weekly_bar_override, id(self._fonts), body)

    def _draw_card(self, p: QPainter, index: int, pane: QtPane, style, layout,
                   ox, oy, dpr: float) -> None:
        """Blit the pane's card from its cached image, drawing the image first
        when an input changed. The image holds device pixels aligned to the
        window's, so the blit lands the same pixels a direct draw would."""
        self._font_set()  # before the signature: it keys on the font set
        sig = self._card_signature(pane, style, layout, ox, oy, dpr)
        entry = self._cards.get(index)
        if entry is None or entry[0] != sig:
            px = math.floor(round((ox - CARD_PAD) * dpr, 6))
            py = math.floor(round((oy - CARD_PAD) * dpr, 6))
            lx, ly = px / dpr, py / dpr
            size = QSize(math.ceil((T.CARD_WIDTH + 2 * CARD_PAD) * dpr) + 1,
                         math.ceil((T.CARD_HEIGHT + 2 * CARD_PAD) * dpr) + 1)
            image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            image.setDevicePixelRatio(dpr)
            ip = QPainter(image)
            ip.translate(-lx, -ly)  # window coordinates, as in a direct draw
            self._card_origin = (lx, ly)
            self._paint_card_contents(ip, pane, style, layout, ox, oy)
            ip.end()
            self._card_origin = (0.0, 0.0)
            entry = self._cards[index] = (sig, image, lx, ly)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(QPointF(entry[2], entry[3]), entry[1])

    def _paint_card_contents(self, p: QPainter, pane: QtPane, style, layout, ox, oy) -> None:
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
        if self.theme.card.design == "refined":
            self._paint_footer(p, pane, layout, dim)
            p.restore()
            return
        if "status" in layout:
            text = pane.status_text if pane._view_mode == "limits" else (
                pane.usage_view.status_text if pane.usage_view is not None else "")
            rect = layout["status"]
            # Wraps at the card's right edge. The original card wrapped at 160 px in
            # its own font metrics, and on Windows its text reached x=298, wider than
            # the stats column; wrapping at the column put "Code" of "open
            # Claude Code" on a second line below the card.
            left = rect.x + TK_LABEL_PAD_X
            box = QRectF(left, rect.y + TK_LABEL_PAD_Y, T.CARD_WIDTH - left,
                         T.CARD_HEIGHT - rect.y - TK_LABEL_PAD_Y)
            p.setFont(self._font_set()[2])
            self._text(p, dim, box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                       | Qt.TextFlag.TextWordWrap, text)
        if "account_label" in layout and pane._label:
            p.setFont(self._font_set()[2])
            self._text(p, dim, _text_rect(layout["account_label"], right=True),
                       _RIGHT_VCENTER, pane._label)
        p.restore()

    def _paint_footer(self, p: QPainter, pane: QtPane, layout, dim: QColor) -> None:
        """The refined status line and account label, one row. The label is
        right-aligned; the status is one line, elided so it never runs under
        the label."""
        text_font = self._font_set()[2]
        p.setFont(text_font)
        label = pane._label if "account_label" in layout else ""
        if label:
            self._text(p, dim, _qrect(layout["account_label"]), _RIGHT_VCENTER, label)
        if "status" in layout:
            status = pane.status_text if pane._view_mode == "limits" else (
                pane.usage_view.status_text if pane.usage_view is not None else "")
            rect = layout["status"]
            width = rect.w
            if label:
                width -= p.fontMetrics().horizontalAdvance(label) + STATUS_LABEL_GAP
            shown = p.fontMetrics().elidedText(status, Qt.TextElideMode.ElideRight, max(width, 0))
            self._text(p, dim, QRectF(rect.x, rect.y, max(width, 0), rect.h), _LEFT_VCENTER, shown)

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

    def _notches(self, p: QPainter, rect: T.Rect, style: T.PaneStyle) -> None:
        """Cut each threshold notch through the bar down to the card, so it
        shows the card's own colour and alpha whatever the bar's colour."""
        xs = T.notch_xs(self.theme.bars, rect)
        if not xs:
            return
        color = QColor(style.background)
        color.setAlpha(T.card_alpha(style.opacity))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for x in xs:
            p.fillRect(QRectF(x, rect.y, NOTCH_W, rect.h), color)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _paint_limits(self, p, pane, layout, style, label_fg, dim, halo) -> None:
        if self.theme.card.design == "refined":
            return self._paint_limits_refined(p, pane, layout, style, label_fg, dim, halo)
        label_font, _model_font, text_font = self._font_set()[:3]
        rows = (
            ("session", "SESSION", pane.session_pct, pane.session_reset_text),
            ("weekly", "WEEK", pane.weekly_pct, pane.weekly_reset_text),
        )
        for name, title, pct, reset in rows:
            if f"{name}_label" in layout:
                p.setFont(label_font)
                self._text(p, label_fg, _text_rect(layout[f"{name}_label"]), _LEFT_VCENTER, title,
                           halo and T.halo_applies("section_labels"))
            self._bar(p, layout[f"{name}_bar"], pct,
                      T.bar_fill(self.theme, pct, _bar_override(style, name)))
            self._notches(p, layout[f"{name}_bar"], style)
            p.setFont(text_font)
            reset_box = _text_rect(layout[f"{name}_reset"]) if f"{name}_reset" in layout else None
            if f"{name}_percent" in layout:
                percent = f"{pct:.0f}%"
                box = _text_rect(layout[f"{name}_percent"])
                self._text(p, dim, box, _LEFT_VCENTER, percent, halo and T.halo_applies("percent"))
                if reset_box is not None:
                    # "42% · resets ..." reads as one run, so the reset text
                    # starts right after the percent rather than at its own rect.
                    start = box.left() + p.fontMetrics().horizontalAdvance(percent + " ")
                    reset_box.setLeft(start)
                    reset = "· " + reset
            if reset_box is not None:
                self._text(p, dim, reset_box, _LEFT_VCENTER, reset)

    def _paint_limits_refined(self, p, pane, layout, style, label_fg, dim, halo) -> None:
        """Section name left and percent right on one row, the bar, then the
        reset line under it."""
        _label, _model, text_font, label_font, _model_semi = self._font_set()
        rows = (
            ("session", "Session", pane.session_pct, pane.session_reset_text),
            ("weekly", "Week", pane.weekly_pct, pane.weekly_reset_text),
        )
        for name, title, pct, reset in rows:
            p.setFont(label_font)
            if f"{name}_label" in layout:
                self._text(p, label_fg, _qrect(layout[f"{name}_label"]), _LEFT_BOTTOM, title,
                           halo and T.halo_applies("section_labels"))
            if f"{name}_percent" in layout:
                self._text(p, label_fg, _qrect(layout[f"{name}_percent"]), _RIGHT_BOTTOM,
                           f"{pct:.0f}%", halo and T.halo_applies("percent"))
            self._bar(p, layout[f"{name}_bar"], pct,
                      T.bar_fill(self.theme, pct, _bar_override(style, name)))
            self._notches(p, layout[f"{name}_bar"], style)
            phrase = reset_phrase(reset)
            if f"{name}_reset" in layout and phrase:
                p.setFont(text_font)
                self._text(p, dim, _qrect(layout[f"{name}_reset"]), _LEFT_VCENTER, phrase)

    def _paint_models(self, p, pane, layout, style, label_fg, dim) -> None:
        refined = self.theme.card.design == "refined"
        fonts = self._font_set()
        model_font, text_font = (fonts[4], fonts[2]) if refined else (fonts[1], fonts[2])
        view = pane.usage_view
        rows = view.rows if view is not None else ()
        for i in range(ROW_SLOTS):
            row = rows[i] if i < len(rows) else None
            if f"model_name_{i}" in layout and row is not None:
                p.setFont(model_font)
                name_box, value_box = layout[f"model_name_{i}"], layout[f"model_value_{i}"]
                if refined:
                    self._text(p, label_fg, _qrect(name_box), _LEFT_BOTTOM,
                               fit_tag(row.label, MODEL_NAME_MAX))
                    p.setFont(text_font)
                    self._text(p, dim, _qrect(value_box), _RIGHT_BOTTOM, row.value_text)
                else:
                    self._text(p, label_fg, _text_rect(name_box), _LEFT_VCENTER,
                               fit_tag(row.label, MODEL_NAME_MAX))
                    p.setFont(text_font)
                    self._text(p, dim, _text_rect(value_box, right=True), _RIGHT_VCENTER,
                               row.value_text)
            # The track is always there; the fill only when there is a share.
            pct = row.bar_pct if row is not None else 0.0
            fill = T.bar_fill(self.theme, view.ramp_pct if view else None, style.weekly_bar_override)
            self._bar(p, layout[f"model_bar_{i}"], pct, fill)

    def _paint_cat(self, p, index, pane, palette, home, dpr) -> None:
        image = self._cat_image(pane.grid(), palette, pane.palette_key, dpr, pane.pose.mirrored)
        x, y = self.cat_origin(index, pane.offset())
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(QPointF(x, y), image)

    def _paint_tags(self, p, index, pane, style, home) -> None:
        if "cat_tags" not in self.theme.text.show:
            return
        if not (pane._driving_tag or pane._tool_label):
            return
        driving, tool, side = self.tag_boxes(index, home)
        p.setFont(self._font_set()[2])
        if pane._driving_tag:
            self._tag(p, driving, side | Qt.AlignmentFlag.AlignBottom,
                      pane._driving_tag, QColor(style.dim), style)
        if pane._tool_label:
            self._tag(p, tool, side | Qt.AlignmentFlag.AlignTop,
                      fit_tag(pane._tool_label), QColor(style.foreground), style)

    def tag_boxes(self, index: int, home: str):
        """(driving box, tool box, horizontal alignment) in window coordinates.
        Tags are relative to the cat's canvas and sit on the card's outer
        side, so a mirrored cat (inside_right) gets them on its right."""
        ox, oy = self.window_layout.pane_origins[index]
        placed = T.placement(home)
        cx, cy = ox + placed.origin[0], oy + placed.origin[1] - CAT_CANVAS_DY
        x = cx if placed.mirrored else cx + TAG_INSET
        side = Qt.AlignmentFlag.AlignRight if placed.mirrored else Qt.AlignmentFlag.AlignLeft
        width = CAT_CANVAS_SIZE - TAG_INSET
        driving = QRectF(x, cy, width, CAT_CANVAS_SIZE - TAG_INSET)
        tool = QRectF(x, cy + TAG_INSET, width, T.SMALL_H)
        return driving, tool, side

    def _tag(self, p, box: QRectF, flags, text: str, color: QColor, style) -> None:
        # A chip in the card colour under the tag, as on the Windows card, so
        # the dim text stays legible over the cat's fur.
        bounds = p.fontMetrics().boundingRect(box.toRect(), int(flags), text)
        chip = QColor(style.background)
        chip.setAlpha(T.card_alpha(style.opacity))
        p.fillRect(QRectF(bounds).adjusted(-TAG_CHIP_PAD_X, -TAG_CHIP_PAD_Y,
                                           TAG_CHIP_PAD_X, TAG_CHIP_PAD_Y), chip)
        self._text(p, color, box, flags, text)

    # ---- position -------------------------------------------------------

    def _screens(self) -> List[position.ScreenInfo]:
        out = []
        for screen in QGuiApplication.screens():
            g, ratio = screen.geometry(), screen.devicePixelRatio()
            out.append(position.ScreenInfo(
                (g.x(), g.y(), round(g.width() * ratio), round(g.height() * ratio)),
                (g.x(), g.y(), g.width(), g.height()), ratio))
        return out

    def _area_at(self, point: Optional[Tuple[int, int]]):
        screen = QGuiApplication.screenAt(QPoint(*point)) if point else None
        screen = screen or QGuiApplication.primaryScreen()
        a = screen.availableGeometry()
        return a.x(), a.y(), a.x() + a.width(), a.y() + a.height()

    def restore_position(self) -> None:
        """Place the first card at its saved spot. The owner calls this
        before the first show()."""
        saved = None
        if self.position_path is not None:
            primary = QGuiApplication.primaryScreen()
            saved = position.load(self.position_path, self._screens(),
                                  primary.devicePixelRatio(), sys.platform)
        x, y = position.place(saved, self.grid_size(), self.window_layout.margins,
                              self._area_at(saved))
        if sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa":
            # Moved before its native window exists, a frameless window
            # lands 20 points higher on its first show, and the next drag
            # saves that, so the widget crept up on every launch.
            self.winId()
        self.move(x, y)

    def save_position(self) -> None:
        if self.position_path is not None:
            position.save(self.position_path, *self.first_card_pos())

    # ---- input ----------------------------------------------------------

    def pane_index_at(self, x: float, y: float) -> int:
        """The pane whose card holds the window point; 0 outside every card."""
        for i, (ox, oy) in enumerate(self.window_layout.pane_origins):
            if ox <= x < ox + T.CARD_WIDTH and oy <= y < oy + T.CARD_HEIGHT:
                return i
        return 0

    def _after_menu_action(self, action):
        if action is None:
            return None

        def run() -> None:
            action()
            if self.on_menu_action_done is not None:
                self.on_menu_action_done()

        return run

    def context_menu(self, pane_index: int) -> Optional[QMenu]:
        """A fresh menu from the owner's model, rebuilt on every open so it
        reads live state."""
        if self.build_menu_model is None:
            return None
        menu = QMenu(self)
        render_menu(menu, self.build_menu_model(pane_index), self._after_menu_action)
        if sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa":
            # Each menu and submenu is its own native window, and clicking
            # an item in one activated the app, taking focus from whatever
            # the user was typing in, unless the helper ran on it too.
            for each in [menu, *menu.findChildren(QMenu)]:
                each.aboutToShow.connect(lambda each=each: macos.apply(int(each.winId())))
        return menu

    def _popup(self, menu: QMenu, global_pos) -> None:
        menu.exec(global_pos)

    def mousePressEvent(self, event) -> None:
        # A drag or a menu starts here; neither wants a popup in the way.
        self.hover_cancel()
        if is_context_press(event.button(), event.modifiers()):
            pos = event.position()
            menu = self.context_menu(self.pane_index_at(pos.x(), pos.y()))
            if menu is not None:
                self._popup(menu, event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.LeftButton:
            # Global coordinates: the press can land anywhere in the window.
            g = event.globalPosition().toPoint()
            self._drag = (g.x() - self.x(), g.y() - self.y())

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            g = event.globalPosition().toPoint()
            self.move(g.x() - self._drag[0], g.y() - self._drag[1])
        elif self._drag is None:
            pos = event.position()
            self._hover_pos = (pos.x(), pos.y())
            self._hover_update(self.hover_target_at(pos.x(), pos.y()), moved=True)

    def leaveEvent(self, event) -> None:
        self.hover_cancel()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self.hover_cancel()
        super().hideEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.save_position()

    # ---- hover popup ----------------------------------------------------

    def hover_target_at(self, x: float, y: float) -> Optional[tuple]:
        """What a window point is over: (pane, "cat", None) on the cat's drawn
        pixels, else (pane, "limits", "session" | "weekly") or (pane,
        "models", row) on a row's block, else None. The cat is tested first
        because it is painted over the card, and against its alpha so the
        transparent corners of its frame are nothing."""
        for i in reversed(range(len(self.panes))):
            if self._cat_hit(i, self.panes[i], x, y):
                return (i, "cat", None)
        for i, pane in enumerate(self.panes):
            ox, oy = self.window_layout.pane_origins[i]
            lx, ly = x - ox, y - oy
            if not (0 <= lx < T.CARD_WIDTH and 0 <= ly < T.CARD_HEIGHT):
                continue
            for kind, key, rect in self._row_rects(i, pane):
                if rect.x <= lx < rect.right and rect.y <= ly < rect.bottom:
                    return (i, kind, key)
        return None

    def _cat_hit(self, index: int, pane: QtPane, x: float, y: float) -> bool:
        rect = self._cat_rect(index, pane)
        if not rect.contains(x, y):
            return False
        dpr = self.devicePixelRatioF()
        image = self._cat_image(pane.grid(), pane.palette, pane.palette_key, dpr, pane.pose.mirrored)
        px, py = int((x - rect.x()) * dpr), int((y - rect.y()) * dpr)
        if not (0 <= px < image.width() and 0 <= py < image.height()):
            return False
        return image.pixelColor(px, py).alpha() > 0

    def _row_rects(self, index: int, pane: QtPane):
        """(kind, key, card rect) for each hoverable row: the whole block, from
        its label row to its reset line, or a model's name row to its bar."""
        layout = self._layout_for(pane._view_mode, self.homes[index])
        if pane._view_mode == "models":
            view = pane.usage_view
            count = len(view.rows) if view is not None else 0
            groups = [("models", i, (f"model_name_{i}", f"model_value_{i}", f"model_bar_{i}"))
                      for i in range(min(count, ROW_SLOTS))]
        else:
            groups = [("limits", name, (f"{name}_label", f"{name}_percent", f"{name}_bar", f"{name}_reset"))
                      for name in ("session", "weekly")]
        for kind, key, names in groups:
            parts = [layout.get(n) for n in names]
            if kind == "limits" and key in layout.lines:
                parts.append(layout.lines[key])
            parts = [r for r in parts if r is not None]
            if not parts:
                continue
            x0, y0 = min(r.x for r in parts), min(r.y for r in parts)
            x1, y1 = max(r.right for r in parts), max(r.bottom for r in parts)
            yield kind, key, T.Rect(x0, y0, x1 - x0, y1 - y0)

    def hover_text_for(self, target: tuple) -> Optional[str]:
        index, kind, key = target
        pane = self.panes[index]
        if kind == "cat":
            return pane.cat_hover_text()
        if kind == "limits":
            hover = pane.limits_hover
            if hover is None:
                return None
            return limit_hover_text(getattr(hover, key), datetime.now(timezone.utc))
        view = pane.usage_view
        if view is None or key >= len(view.rows) or view.rows[key].detail is None:
            return None
        return model_hover_text(view.rows[key].detail, view.window_text)

    def hover_popup(self) -> Optional[HoverPopup]:
        """The popup while it is on screen, else None."""
        return self._hover_win if self._hover_win is not None and self._hover_win.isVisible() else None

    def _hover_active(self) -> bool:
        return self._hover_timer.isActive() or self.hover_popup() is not None

    def hover_cancel(self) -> None:
        self._hover_timer.stop()
        self._hover_target = None
        self._hover_pos = None
        if self._hover_win is not None:
            self._hover_win.hide()

    def _hover_update(self, target: Optional[tuple], moved: bool) -> None:
        """The pointer is over `target` (None for nothing). A move restarts the
        delay; a frame recheck leaves a running one alone."""
        if target is None:
            self._hover_timer.stop()
            self._hover_target = None
            if self._hover_win is not None:
                self._hover_win.hide()
            return
        shown = self.hover_popup() is not None
        if target != self._hover_target:
            self._hover_target = target
            if shown:
                self._hover_show(target)
                return
            self._hover_timer.start(self.hover_delay_ms)
        elif not shown and moved:
            self._hover_timer.start(self.hover_delay_ms)
        elif shown:
            self._hover_refresh(target)

    def _hover_recheck(self) -> None:
        """Called on every cat frame and pane render: the cat may have walked
        out from under a still pointer, a row may have changed. Only does
        anything while a popup is up or about to be."""
        if self._hover_pos is not None and self._hover_active():
            self._hover_update(self.hover_target_at(*self._hover_pos), moved=False)

    def _hover_fire(self) -> None:
        target = self._hover_target
        if target is None or self._hover_pos is None:
            return
        if self.hover_target_at(*self._hover_pos) != target:
            self._hover_update(self.hover_target_at(*self._hover_pos), moved=True)
            return
        self._hover_show(target)

    def _popup_look(self, index: int) -> PopupLook:
        pane = self.panes[index]
        style = self._style_for(index, pane)
        card = self.theme.card
        fonts = self._font_set()
        refined = card.design == "refined"
        return PopupLook(
            background=style.background, foreground=style.foreground, dim=style.dim,
            border_color=card.border_color, border_width=card.border_width,
            radius=card.radius, first_font=fonts[4] if refined else fonts[1], font=fonts[2],
        )

    def _hover_target_rect(self, target: tuple) -> Tuple[int, int, int, int]:
        """The popup sits beside this window rect: a row's height across the
        card's width (so it clears the card), or the cat's drawn rectangle,
        widened to the card while the cat is inside it."""
        index, kind, key = target
        ox, oy = self.window_layout.pane_origins[index]
        if kind == "cat":
            r = self._cat_rect(index, self.panes[index])
            card = QRectF(ox, oy, T.CARD_WIDTH, T.CARD_HEIGHT)
            if r.intersects(card):
                # Sitting on the card: clear the whole card, not just the cat.
                r = r.united(card)
            return round(r.x()), round(r.y()), round(r.width()), round(r.height())
        rect = next(r for k, kk, r in self._row_rects(index, self.panes[index]) if (k, kk) == (kind, key))
        return ox, oy + rect.y, T.CARD_WIDTH, rect.h

    def _hover_show(self, target: tuple) -> None:
        text = self.hover_text_for(target)
        if text is None:
            self._hover_update(None, moved=False)
            return
        if self._hover_win is None:
            self._hover_win = HoverPopup()
        popup = self._hover_win
        popup.set_content(text, self._popup_look(target[0]))
        x, y, w, h = self._hover_target_rect(target)
        top_left = self.mapToGlobal(QPoint(x, y))
        area = self._area_at((top_left.x(), top_left.y()))
        px, py = place_popup((popup.width(), popup.height()), (top_left.x(), top_left.y(), w, h), area)
        popup.move(px, py)
        popup.show()

    def _hover_refresh(self, target: tuple) -> None:
        """Same target, new numbers: keep the popup where it is."""
        text = self.hover_text_for(target)
        if text is None:
            self._hover_update(None, moved=False)
        elif text != self._hover_win.text:
            self._hover_win.set_content(text, self._popup_look(target[0]))

    def closeEvent(self, event) -> None:
        # The frameless, focusless widget has no close button, but an OS-level
        # close must not leave the lock held with nothing on screen. Quitting
        # is always explicit, because quitOnLastWindowClosed is off.
        QApplication.quit()
        super().closeEvent(event)

    # ---- always in front ------------------------------------------------

    @property
    def always_on_top(self) -> bool:
        return self._always_on_top

    def set_always_on_top(self, on: bool) -> None:
        """Toggling the hint makes Qt recreate the native window, which hides
        it: show it again without activating, then redo whatever a new native
        window needs. All three stay in this one method so none is forgotten."""
        self._always_on_top = on
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()  # WA_ShowWithoutActivating keeps focus where it was
        self._after_native_window()

    def _after_native_window(self) -> None:
        """Everything that needs the native window: the macOS helper, then
        the click mask. Runs after the first show and after every recreate."""
        self._apply_macos()
        self._update_mask()

    def _apply_macos(self) -> None:
        """The macOS helper, only under the cocoa platform plugin, so
        offscreen runs skip it."""
        if (sys.platform == "darwin" and self.windowHandle() is not None
                and QGuiApplication.platformName() == "cocoa"):
            macos.apply(int(self.winId()), on_top=self._always_on_top)

    def _on_application_state(self, state) -> None:
        """Qt raises a Qt.Tool window back to the floating level as the app
        becomes active, which undoes the helper's normal level when Always
        in front is off. The state change arrives after that, so redo it."""
        if state == Qt.ApplicationState.ApplicationActive and not self._always_on_top:
            self._apply_macos()

    def cat_rects(self) -> List[T.Rect]:
        """Each cat's rectangle in window coordinates: the sprite at every
        offset its animation can move it to, so a jump or a stride is never
        cut off by the mask, which clips painting on Windows."""
        dpr = self.devicePixelRatioF()
        rects = []
        for i, (pose, offsets, (cols, rows)) in enumerate(self._current_mask_places()):
            ox, oy = self.window_layout.pane_origins[i]
            corners = [self._origin_of(ox, oy, pose, o, dpr, cols) for o in offsets]
            x0, y0 = min(c[0] for c in corners), min(c[1] for c in corners)
            x1, y1 = max(c[0] for c in corners), max(c[1] for c in corners)
            rects.append(T.Rect(round(x0), round(y0), round(x1 - x0) + cols * sprites.SCALE,
                                round(y1 - y0) + rows * sprites.SCALE))
        return rects

    def _current_mask_places(self) -> tuple:
        return tuple(pane.mask_place for pane in self.panes)

    def _sync_mask(self) -> None:
        """Remask when some cat's animation or pose changed, never per frame."""
        if self._current_mask_places() != self._mask_places:
            self._update_mask()

    def _update_mask(self) -> None:
        """Limit clicks to the cards and the cats. Windows and Linux already
        pass clicks through transparent pixels, but macOS catches every click
        in the window rectangle, so the mask does it there; it costs nothing
        elsewhere. A rectangle per cat still catches its transparent cells."""
        self._mask_places = self._current_mask_places()
        region = QRegion()
        for r in T.hit_region(self.window_layout, self.cat_rects()):
            region = region.united(QRegion(QRect(r.x, r.y, r.w, r.h)))
        self.setMask(region)
