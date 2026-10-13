"""Shared look and widgets for the Qt windows: the counterpart of
settings_widgets.

Same method names as settings_widgets.Kit so each tab ports line for line.
Colours and fonts come from settings_style. Styling is one style sheet
(Kit.style_sheet()) that each top-level window sets on itself, never on the
QApplication, so the widget's context menu and any QMessageBox keep the
platform look. Rules select on a "kit" property the widgets here carry, not on
widget classes, so a dialog parented to a styled window is left alone.
Sizes are logical pixels, which Qt scales: Kit.px is the identity.

Every control separates two paths. A user interaction calls its `on_change`
callback; `set()` only repaints. Settings code re-reads controls from the
live getters with set(), so a programmatic update can never write back.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from tokitty.settings_style import (
    ACCENT_BG, ACCENT_FG, BAD, BG_COLOR, BORDER, DIM_COLOR, FG_COLOR, FONT_BODY, FONT_MEDIUM,
    FONT_SECTION, FONT_SMALL, FONT_TITLE, MUTED, RAIL, SURFACE, SURFACE_HOVER, _PILL_COLORS,
)
from tokitty.sprite_raster import raster_rgba
from tokitty.sprites import PALETTE, get_frames, resolve_palette

WINDOW_NAME = "kitWindow"  # objectName of a top-level window the style sheet paints


def qfont(spec: tuple) -> QFont:
    """A settings_style font tuple (family, points[, "bold"]) as a QFont."""
    font = QFont(spec[0])
    font.setPointSize(spec[1])
    font.setBold("bold" in spec[2:])
    return font


def _css_font(spec: tuple) -> str:
    weight = "bold " if "bold" in spec[2:] else ""
    return f'{weight}{spec[1]}pt "{spec[0]}"'


def repolish(widget: QWidget) -> None:
    """Re-read the style sheet after a property the sheet selects on changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _tagged(widget, kit_role: str, **props):
    widget.setProperty("kit", kit_role)
    for key, value in props.items():
        widget.setProperty(key, value)
    return widget


def text(value: str, *, color: str = FG_COLOR, font: tuple = FONT_BODY, parent=None) -> QLabel:
    """A transparent label. Colour and font are set on the widget (palette and
    QFont), not through the style sheet, so they can change at run time."""
    label = QLabel(value, parent)
    label.setFont(qfont(font))
    set_text_color(label, color)
    return label


def set_text_color(label: QLabel, color: str) -> None:
    palette = label.palette()
    palette.setColor(QPalette.ColorRole.WindowText, QColor(color))
    label.setPalette(palette)


def vbox(parent: Optional[QWidget] = None, spacing: int = 0) -> QVBoxLayout:
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout


def hbox(parent: Optional[QWidget] = None, spacing: int = 0) -> QHBoxLayout:
    layout = QHBoxLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout


def sprite_cells(state: str = "content") -> Tuple[int, int]:
    frame = get_frames(state)[0]
    return len(frame[0]), len(frame)


def sprite_image(palette: dict, state: str = "content", mirrored: bool = False,
                 cell: int = 3, dpr: float = 1.0) -> QImage:
    """The first frame of a sprite state, drawn through raster_rgba as the
    widget does. `palette` is a full char -> colour map; the base PALETTE
    fills any gap. cell is logical pixels per sprite cell."""
    colors = {**PALETTE, **palette}
    device_cell = max(1, round(cell * dpr))
    w, h, data = raster_rgba(get_frames(state)[0], colors, device_cell)
    image = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    if mirrored:
        image = image.flipped(Qt.Orientation.Horizontal)
    image.setDevicePixelRatio(dpr)
    return image


class SpritePreview(QWidget):
    """A sprite on a transparent background, sized to the sprite."""

    def __init__(self, palette: dict, state: str = "content", mirrored: bool = False,
                 cell: int = 3):
        super().__init__()
        self._cell = cell
        self._args = (palette, state, mirrored)
        self._dpr = 0.0
        self.image = sprite_image(palette, state, mirrored, cell)
        cols, rows = sprite_cells(state)
        self.setFixedSize(cols * cell, rows * cell)

    def set_sprite(self, palette: dict, state: str = "content", mirrored: bool = False) -> None:
        self._args = (palette, state, mirrored)
        self._rebuild()
        cols, rows = sprite_cells(state)
        self.setFixedSize(cols * self._cell, rows * self._cell)
        self.update()

    def _rebuild(self) -> None:
        self._dpr = self.devicePixelRatioF()
        self.image = sprite_image(*self._args, cell=self._cell, dpr=self._dpr)

    def paintEvent(self, _event) -> None:
        if self.devicePixelRatioF() != self._dpr:
            self._rebuild()
        painter = QPainter(self)
        painter.drawImage(0, 0, self.image)
        painter.end()


class _Switch(QWidget):
    """The painted on/off track of a Toggle."""

    def __init__(self, toggle: "Toggle"):
        super().__init__()
        self._toggle = toggle
        self.setFixedSize(34, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event) -> None:
        on, enabled = self._toggle.value, self._toggle.enabled
        track = ACCENT_FG if on else "#555560"
        if not enabled:
            track = "#6b4f4a" if on else "#3f3f48"
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(track))
        p.drawRoundedRect(QRectF(1, 2, 32, 16), 8, 8)
        p.setBrush(QColor(ACCENT_BG if on else FG_COLOR))
        cx = 24 if on else 10
        p.drawEllipse(QRectF(cx - 6, 4, 12, 12))
        p.end()


class Toggle(QWidget):
    """A row with a title, optional detail line and an on/off switch."""

    def __init__(self, kit: "Kit", title: str, *, detail: Optional[str] = None,
                 compact: bool = False, on_change: Optional[Callable[[bool], None]] = None):
        super().__init__()
        self.value = False
        self.enabled = True
        self._on_change = on_change
        row = hbox(self)
        box = vbox()
        row.addLayout(box, 1)
        self.title = text(title, font=FONT_BODY if compact else FONT_MEDIUM)
        box.addWidget(self.title)
        self.detail = None
        if detail:
            self.detail = text(detail, color=DIM_COLOR, font=FONT_SMALL)
            self.detail.setContentsMargins(0, 2, 0, 0)
            box.addWidget(self.detail)
        for label in (self.title, self.detail):
            if label is not None:
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.switch = _Switch(self)
        row.addSpacing(10)
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._clicked()

    def _clicked(self) -> None:
        if not self.enabled:
            return
        self.value = not self.value
        self.switch.update()
        if self._on_change is not None:
            self._on_change(self.value)

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self.enabled:
            self.enabled = enabled
            self.setCursor(Qt.CursorShape.PointingHandCursor if enabled
                           else Qt.CursorShape.ArrowCursor)
            self.switch.update()

    def set(self, value: bool) -> None:
        value = bool(value)
        if value != self.value:
            self.value = value
            self.switch.update()

    def get(self) -> bool:
        return self.value


class Segmented(QFrame):
    """A row of mutually exclusive options."""

    def __init__(self, kit: "Kit", options: Sequence[Tuple[str, str]],
                 on_change: Optional[Callable[[str], None]] = None):
        super().__init__()
        _tagged(self, "segmented")
        self.value: Optional[str] = None
        self._on_change = on_change
        row = hbox(self, spacing=1)
        row.setContentsMargins(1, 1, 1, 1)
        self.items: Dict[str, QPushButton] = {}
        for value, title in options:
            item = _tagged(QPushButton(title), "segment", active=False)
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            item.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            item.clicked.connect(lambda _checked=False, v=value: self._clicked(v))
            row.addWidget(item, 1)
            self.items[value] = item

    def _clicked(self, value: str) -> None:
        self.value = value
        self._paint()
        if self._on_change is not None:
            self._on_change(value)

    def set(self, value) -> None:
        self.value = value
        self._paint()

    def get(self) -> Optional[str]:
        return self.value

    def _paint(self) -> None:
        for value, item in self.items.items():
            active = self.value == value
            if item.property("active") != active:
                item.setProperty("active", active)
                repolish(item)


class Dropdown(QPushButton):
    """A dark dropdown: a button over a QMenu, so the popup is styled by the
    same sheet as everything else. set() and set_choices() never call
    on_change."""

    def __init__(self, kit: "Kit", choices: Iterable[str], *, width: Optional[int] = None,
                 on_change: Optional[Callable[[str], None]] = None):
        super().__init__("")
        _tagged(self, "dropdown")
        self._on_change = on_change
        self._value = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if width is not None:
            self.setMinimumWidth(width * 7 + 34)
        self.menu = _tagged(QMenu(self), "dropdown-menu")
        self.clicked.connect(self._popup)
        self.set_choices(choices)

    def _popup(self) -> None:
        self.menu.setMinimumWidth(self.width())
        self.menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def set_choices(self, choices: Iterable[str]) -> None:
        self.menu.clear()
        for choice in choices:
            action = self.menu.addAction(choice)
            action.triggered.connect(lambda _checked=False, c=choice: self._picked(c))

    def choices(self) -> List[str]:
        return [a.text() for a in self.menu.actions()]

    def _picked(self, choice: str) -> None:
        self.set(choice)
        if self._on_change is not None:
            self._on_change(choice)

    def set(self, value: str) -> None:
        self._value = value
        self.setText(value)

    def get(self) -> str:
        return self._value

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setPen(QColor(MUTED))
        p.setFont(qfont(FONT_BODY))
        arrow = self.rect().adjusted(0, 0, -9, 0)
        p.drawText(arrow, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "▾")
        p.end()


class Spin(QSpinBox):
    """A number field that leaves the mouse wheel to the scroll area. set()
    never calls on_change; typing or the arrows do."""

    def __init__(self, lo: int, hi: int, *, step: int = 1, suffix: str = "",
                 on_change: Optional[Callable[[int], None]] = None):
        super().__init__()
        _tagged(self, "spin")
        self.setRange(lo, hi)
        self.setSingleStep(step)
        self.setSuffix(suffix)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._on_change = on_change
        self.valueChanged.connect(self._changed)

    def _changed(self, value: int) -> None:
        if self._on_change is not None:
            self._on_change(value)

    def set(self, value: int) -> None:
        self.blockSignals(True)
        try:
            self.setValue(int(value))
        finally:
            self.blockSignals(False)

    def get(self) -> int:
        return self.value()

    def wheelEvent(self, event) -> None:
        event.ignore()


class Rail(QFrame):
    """The window's left rail: the brand block, one strip per entry and the
    version text. With on_select None the strips are plain labels."""

    def __init__(self, kit: "Kit", entries: Sequence[Tuple[str, str]], version: str,
                 on_select: Optional[Callable[[str], None]] = None):
        super().__init__()
        _tagged(self, "rail")
        self.setFixedWidth(132)
        self.on_select = on_select
        column = vbox(self)
        brand = QWidget()
        brand_row = hbox(brand)
        brand_row.setContentsMargins(17, 21, 17, 30)
        logo = QLabel()
        logo.setPixmap(QPixmap.fromImage(
            sprite_image(resolve_palette("orange", "tabby"), "content", False, cell=1)))
        brand_row.addWidget(logo)
        brand_row.addSpacing(7)
        brand_row.addWidget(text("tokitty", font=("Segoe UI", 11, "bold")))
        brand_row.addStretch(1)
        column.addWidget(brand)

        self.strips: Dict[str, "_Strip"] = {}
        for key, title in entries:
            strip = _Strip(key, title, clickable=on_select is not None)
            strip.clicked.connect(self._clicked)
            column.addWidget(strip)
            column.addSpacing(2)
            self.strips[key] = strip
        column.addStretch(1)

        bottom = QWidget()
        bottom_col = vbox(bottom)
        bottom_col.setContentsMargins(18, 0, 18, 19)
        rule = _tagged(QFrame(), "rule")
        rule.setFixedHeight(1)
        bottom_col.addWidget(rule)
        bottom_col.addSpacing(13)
        bottom_col.addWidget(text(version, color=DIM_COLOR, font=FONT_SMALL))
        column.addWidget(bottom)

    def _clicked(self, key: str) -> None:
        if self.on_select is not None:
            self.on_select(key)

    def paint(self, current: str) -> None:
        """Mark `current` with the accent strip and 3 px marker."""
        for key, strip in self.strips.items():
            strip.set_active(key == current)


class _Strip(QFrame):
    """One rail entry: a 3 px marker and a name."""

    clicked = Signal(str)

    def __init__(self, key: str, title: str, *, clickable: bool):
        super().__init__()
        _tagged(self, "rail-strip", active=False)
        self.key = key
        self.setFixedHeight(38)
        row = hbox(self)
        self.marker = _tagged(QFrame(), "rail-marker", active=False)
        self.marker.setFixedWidth(3)
        row.addWidget(self.marker)
        self.name = text(title, color=MUTED, font=FONT_MEDIUM)
        self.name.setContentsMargins(14, 0, 8, 0)
        row.addWidget(self.name, 1)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clickable = clickable

    def set_active(self, active: bool) -> None:
        for widget in (self, self.marker):
            widget.setProperty("active", active)
            repolish(widget)
        set_text_color(self.name, FG_COLOR if active else MUTED)

    def mousePressEvent(self, event) -> None:
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)


class Kit:
    """Factory for the Qt windows' widgets. Parents are QWidgets that carry a
    layout; page_header, section_header and the like add themselves to it."""

    # Logical pixels are Qt's own unit, so there is nothing to scale.
    @staticmethod
    def px(logical: float) -> int:
        return round(logical)

    @staticmethod
    def add(parent, widget, stretch: int = 0) -> None:
        layout = parent if isinstance(parent, QLayout) else parent.layout()
        layout.addWidget(widget, stretch)

    def page_header(self, parent, title: str, subtitle: str) -> None:
        self.add(parent, text(title, font=FONT_TITLE))
        sub = text(subtitle, color=MUTED, font=FONT_SMALL)
        sub.setContentsMargins(0, 1, 0, 14)
        self.add(parent, sub)

    def section_header(self, parent, title: str, hint: Optional[str] = None, *, top: int = 12):
        row = QWidget()
        layout = hbox(row)
        layout.setContentsMargins(0, top, 0, 6)
        layout.addWidget(text(title, font=FONT_SECTION))
        layout.addStretch(1)
        row.hint = None
        if hint:
            row.hint = text(hint, color=DIM_COLOR, font=FONT_SMALL)
            layout.addWidget(row.hint)
        self.add(parent, row)
        return row

    def surface(self) -> QFrame:
        """A raised card, with a vertical layout ready for rows."""
        frame = _tagged(QFrame(), "surface")
        vbox(frame)
        return frame

    def button(self, title: str, style: str = "secondary",
               command: Optional[Callable[[], None]] = None, *, compact: bool = False,
               disabled: bool = False) -> QPushButton:
        button = _tagged(QPushButton(title), "button", variant=style, compact=compact)
        button.setCursor(Qt.CursorShape.ArrowCursor if disabled else Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setEnabled(not disabled)
        if command is not None:
            button.clicked.connect(lambda _checked=False: command())
        return button

    def pill(self, title: str, kind: str = "good") -> QLabel:
        label = _tagged(QLabel(title), "pill", tone=kind)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    @staticmethod
    def set_pill(label: QLabel, title: str, kind: str) -> None:
        label.setText(title)
        label.setProperty("tone", kind)
        repolish(label)

    def toggle(self, title: str, **kwargs) -> Toggle:
        return Toggle(self, title, **kwargs)

    def segmented(self, options, on_change=None) -> Segmented:
        return Segmented(self, options, on_change)

    def dropdown(self, choices, **kwargs) -> Dropdown:
        return Dropdown(self, choices, **kwargs)

    def entry(self, *, width: Optional[int] = None):
        """(holder frame, QLineEdit). Call set_entry_error on the holder."""
        holder = _tagged(QFrame(), "entry-holder", error=False)
        row = hbox(holder)
        row.setContentsMargins(1, 1, 1, 1)
        widget = _tagged(QLineEdit(), "entry")
        row.addWidget(widget)
        if width:
            widget.setMinimumWidth(width * 7 + 16)
        return holder, widget

    def spin(self, lo: int, hi: int, **kwargs):
        """(holder frame, Spin), the number counterpart of entry()."""
        holder = _tagged(QFrame(), "entry-holder", error=False)
        row = hbox(holder)
        row.setContentsMargins(1, 1, 1, 1)
        widget = Spin(lo, hi, **kwargs)
        row.addWidget(widget)
        holder.setFixedWidth(72)
        return holder, widget

    @staticmethod
    def set_entry_error(holder: QFrame, error: bool) -> None:
        if holder.property("error") != error:
            holder.setProperty("error", error)
            repolish(holder)

    def rail(self, entries: Sequence[Tuple[str, str]], version: str,
             on_select: Optional[Callable[[str], None]] = None) -> Rail:
        return Rail(self, entries, version, on_select)

    def sprite_preview(self, palette: dict, state: str = "content", mirrored: bool = False,
                       cell: int = 3) -> SpritePreview:
        return SpritePreview(palette, state, mirrored, cell)

    @staticmethod
    def style_sheet() -> str:
        """The one sheet a top-level window sets on itself."""
        pills = "".join(
            f'QLabel[kit="pill"][tone="{kind}"] {{ color: {fg}; background: {bg}; }}\n'
            for kind, (fg, bg) in _PILL_COLORS.items())
        buttons = "".join(
            f'QPushButton[kit="button"][variant="{variant}"] {{ color: {fg}; background: {bg}; }}\n'
            f'QPushButton[kit="button"][variant="{variant}"]:hover {{ color: {hover_fg}; '
            f'background: {hover_bg}; }}\n'
            for variant, (fg, bg, hover_fg, hover_bg) in {
                "primary": (ACCENT_FG, ACCENT_BG, "#ffd0c7", ACCENT_BG),
                "secondary": (FG_COLOR, SURFACE, "#ffffff", SURFACE_HOVER),
                "danger": (BAD, "#3b252b", "#ffd0d3", "#3b252b"),
                "quiet": (MUTED, BG_COLOR, FG_COLOR, BG_COLOR),
            }.items())
        return f"""
QWidget#{WINDOW_NAME} {{ background: {BG_COLOR}; }}
QFrame[kit="surface"] {{ background: {SURFACE}; }}
QFrame[kit="rail"] {{ background: {RAIL}; }}
QFrame[kit="rail-strip"] {{ background: {RAIL}; }}
QFrame[kit="rail-strip"][active="true"] {{ background: {ACCENT_BG}; }}
QFrame[kit="rail-marker"] {{ background: {RAIL}; }}
QFrame[kit="rail-marker"][active="true"] {{ background: {ACCENT_FG}; }}
QFrame[kit="rule"] {{ background: {BORDER}; }}
QPushButton[kit="button"] {{ border: none; padding: 7px 13px; font: {_css_font(FONT_MEDIUM)}; }}
QPushButton[kit="button"][compact="true"] {{ padding: 5px 10px; font: {_css_font(FONT_SMALL)}; }}
QPushButton[kit="button"]:disabled {{ color: #686872; background: #292930; }}
{buttons}{pills}\
QLabel[kit="pill"] {{ padding: 3px 7px; font: {_css_font(FONT_SMALL)}; }}
QFrame[kit="segmented"] {{ background: {BORDER}; }}
QPushButton[kit="segment"] {{ border: none; padding: 7px 10px; background: {SURFACE};
    color: {MUTED}; font: {_css_font(FONT_SMALL)}; }}
QPushButton[kit="segment"][active="true"] {{ background: {ACCENT_BG}; color: {ACCENT_FG}; }}
QPushButton[kit="dropdown"] {{ border: 1px solid {BORDER}; padding: 6px 24px 6px 9px;
    text-align: left; background: {SURFACE}; color: {FG_COLOR}; font: {_css_font(FONT_BODY)}; }}
QPushButton[kit="dropdown"]:hover {{ background: {SURFACE_HOVER}; }}
QMenu[kit="dropdown-menu"] {{ background: {SURFACE}; color: {FG_COLOR}; border: 1px solid {BORDER};
    font: {_css_font(FONT_BODY)}; }}
QMenu[kit="dropdown-menu"]::item {{ padding: 5px 20px 5px 12px; }}
QMenu[kit="dropdown-menu"]::item:selected {{ background: {ACCENT_BG}; color: {ACCENT_FG}; }}
QFrame[kit="entry-holder"] {{ background: {BORDER}; }}
QFrame[kit="entry-holder"][error="true"] {{ background: {BAD}; }}
QLineEdit[kit="entry"], QSpinBox[kit="spin"] {{ background: {SURFACE}; color: {FG_COLOR};
    border: none; padding: 6px 8px; font: {_css_font(FONT_BODY)};
    selection-background-color: {ACCENT_BG}; selection-color: {FG_COLOR}; }}
QScrollBar[kit="thin"]:vertical {{ background: {BG_COLOR}; width: 10px; margin: 0; }}
QScrollBar[kit="thin"]::handle:vertical {{ background: {BORDER}; margin: 2px; min-height: 20px; }}
QScrollBar[kit="thin"]::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar[kit="thin"]::add-line:vertical, QScrollBar[kit="thin"]::sub-line:vertical {{ height: 0; }}
QScrollBar[kit="thin"]::add-page:vertical, QScrollBar[kit="thin"]::sub-page:vertical {{
    background: none; }}
"""

    def style_window(self, window: QWidget) -> None:
        """Paint a top-level window with the sheet. Set on the window itself,
        never on the QApplication."""
        window.setObjectName(WINDOW_NAME)
        window.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        window.setStyleSheet(self.style_sheet())
