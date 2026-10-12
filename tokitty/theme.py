"""Plain-Python description of the widget's look: card, bars and text.

One global theme per user, stored in theme.json next to settings.json and
customization.json (see paths.py). Per-account looks (coat, card_bg,
bar_fill, cat_home) stay in customization.json. Every default equals what
the Tk widget draws today; the colours are copied here as literals, not
imported, because ui.py needs tkinter and this module must not.

Imports are limited to the standard library and tokitty.sprites (also GUI-free) so the module loads on every
CI OS with tkinter and PySide6 absent. A test enforces that.

A hand-edited theme.json degrades, it never crashes: each field is checked
on its own and a bad one falls back to that field's default. Integers out
of range are rejected rather than clamped, so a typo lands on the default
instead of on an extreme.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import FrozenSet, Mapping, Optional, Sequence, Tuple

from tokitty import sprites

THEME_FILENAME = "theme.json"
_SETTINGS_FILENAME = "settings.json"  # read raw for the opacity migration only

# Card size is fixed: a resizable card is the main way a layout ends up
# with text outside it. Values are ui.py CARD_WIDTH / PANE_HEIGHT.
CARD_WIDTH = 300
CARD_HEIGHT = 128

# Permission accent (ui.py ACCENT_BG / ACCENT_FG). Constants, not theme
# fields: the accent exists to be noticed, so it cannot be themed away.
ACCENT_BG = "#3a1620"
ACCENT_FG = "#ffb4a8"

# Font sizes in points; the family is the painter's business.
FONT_SIZE_LABEL = 9      # "SESSION" / "WEEK", bold
FONT_SIZE_MODEL = 8      # per-model names, bold
FONT_SIZE_TEXT = 8       # everything else, regular

TEXT_ELEMENTS = (
    "section_labels", "percent", "reset", "status",
    "account_label", "model_rows", "cat_tags",
)
# The halo is only measured to help these two; the rest is for the Qt port to try.
HALO_ELEMENTS = frozenset({"section_labels", "percent"})

SPOTS = ("inside", "on_top", "beside_left", "beside_right")
VIEWS = ("limits", "models")

# Opacity levels the Tk build offers (transparency.py LEVELS), the only
# values accepted when migrating from settings.json.
_TK_OPACITY_LEVELS = (100, 90, 80, 70, 60, 50)

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass(frozen=True)
class CardStyle:
    background: str = "#1c1c22"
    opacity: int = 100          # percent, 0..100 in steps of 10
    radius: int = 0             # 0..16
    border_width: int = 0       # 0..3
    border_color: str = "#333340"


@dataclass(frozen=True)
class BarStyle:
    layout: str = "beside_cat"  # "beside_cat" | "full_width"
    shape: str = "flat"         # "flat" | "rounded"
    height: int = 8             # 4..10
    track: str = "#333340"
    low: str = "#4caf6b"
    mid: str = "#e0a838"
    high: str = "#e05252"
    neutral: str = "#5a7fa8"
    mid_at: int = 50
    high_at: int = 80


@dataclass(frozen=True)
class TextStyle:
    foreground: str = "#f0f0f0"
    dim: str = "#8a8a92"
    show: FrozenSet[str] = frozenset(TEXT_ELEMENTS)
    halo: str = "off"           # "off" | "auto" | "on"
    halo_below: int = 60        # "auto" turns the halo on below this opacity


@dataclass(frozen=True)
class Theme:
    card: CardStyle = field(default_factory=CardStyle)
    bars: BarStyle = field(default_factory=BarStyle)
    text: TextStyle = field(default_factory=TextStyle)


# --- pure helpers ---------------------------------------------------------

def card_alpha(opacity: int) -> int:
    """0..255 alpha for a card. Floored at 1 so a 0% card is invisible but
    still catches clicks and drags (a fully transparent pixel does not)."""
    return max(1, round(opacity * 255 / 100))


def halo_applies(element: str) -> bool:
    return element in HALO_ELEMENTS


def halo_active(text: TextStyle, opacity: int) -> bool:
    if text.halo == "on":
        return True
    if text.halo == "auto":
        return opacity < text.halo_below
    return False


def bar_fill(theme: Theme, ramp_pct: Optional[float], override: Optional[str]) -> str:
    """Bar colour: the account's override, else the threshold ramp, else
    neutral. ramp_pct is None for share-of-total bars, which have no cap to
    be near. Comparisons match display.bar_color (>= on both thresholds)."""
    if override:
        return override
    if ramp_pct is None:
        return theme.bars.neutral
    if ramp_pct >= theme.bars.high_at:
        return theme.bars.high
    if ramp_pct >= theme.bars.mid_at:
        return theme.bars.mid
    return theme.bars.low


# --- validation -----------------------------------------------------------

def _hex(value, default: str) -> str:
    if isinstance(value, str) and _HEX_RE.fullmatch(value):
        return value
    return default


def _int(value, lo: int, hi: int, default: int, step: int = 1) -> int:
    # bool is an int subclass; True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if value < lo or value > hi or value % step:
        return default
    return value


def _enum(value, allowed, default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _section(raw, key: str) -> dict:
    sub = raw.get(key)
    return sub if isinstance(sub, dict) else {}


def _clean_card(raw: dict, d: CardStyle) -> CardStyle:
    return CardStyle(
        background=_hex(raw.get("background"), d.background),
        opacity=_int(raw.get("opacity"), 0, 100, d.opacity, step=10),
        radius=_int(raw.get("radius"), 0, 16, d.radius),
        border_width=_int(raw.get("border_width"), 0, 3, d.border_width),
        border_color=_hex(raw.get("border_color"), d.border_color),
    )


def _clean_bars(raw: dict, d: BarStyle) -> BarStyle:
    bars = BarStyle(
        layout=_enum(raw.get("layout"), ("beside_cat", "full_width"), d.layout),
        shape=_enum(raw.get("shape"), ("flat", "rounded"), d.shape),
        height=_int(raw.get("height"), 4, 10, d.height),
        track=_hex(raw.get("track"), d.track),
        low=_hex(raw.get("low"), d.low),
        mid=_hex(raw.get("mid"), d.mid),
        high=_hex(raw.get("high"), d.high),
        neutral=_hex(raw.get("neutral"), d.neutral),
        mid_at=_int(raw.get("mid_at"), 0, 100, d.mid_at),
        high_at=_int(raw.get("high_at"), 0, 100, d.high_at),
    )
    if bars.mid_at >= bars.high_at:
        bars = replace(bars, mid_at=d.mid_at, high_at=d.high_at)
    return bars


def _clean_text(raw: dict, d: TextStyle) -> TextStyle:
    show = raw.get("show")
    if isinstance(show, list):
        shown = frozenset(n for n in show if isinstance(n, str) and n in TEXT_ELEMENTS)
    else:
        shown = d.show
    return TextStyle(
        foreground=_hex(raw.get("foreground"), d.foreground),
        dim=_hex(raw.get("dim"), d.dim),
        show=shown,
        halo=_enum(raw.get("halo"), ("off", "auto", "on"), d.halo),
        halo_below=_int(raw.get("halo_below"), 0, 100, d.halo_below),
    )


def _migrated_opacity(state_dir: Path) -> int:
    """Seed opacity from the Tk build's settings.json key. Read as raw JSON
    so this module never imports settings.py."""
    try:
        data = json.loads((state_dir / _SETTINGS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CardStyle.opacity
    value = data.get("opacity") if isinstance(data, dict) else None
    if isinstance(value, bool) or value not in _TK_OPACITY_LEVELS:
        return CardStyle.opacity
    return value


def _default_theme(state_dir: Path) -> Theme:
    return Theme(card=CardStyle(opacity=_migrated_opacity(state_dir)))


# --- persistence ----------------------------------------------------------

def load_theme(state_dir) -> Theme:
    state_dir = Path(state_dir)
    path = state_dir / THEME_FILENAME
    if not path.is_file():
        return _default_theme(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_theme(state_dir)
    if not isinstance(data, dict):
        return _default_theme(state_dir)
    return Theme(
        card=_clean_card(_section(data, "card"), CardStyle()),
        bars=_clean_bars(_section(data, "bars"), BarStyle()),
        text=_clean_text(_section(data, "text"), TextStyle()),
    )


def save_theme(state_dir, theme: Theme) -> None:
    path = Path(state_dir) / THEME_FILENAME
    payload = asdict(theme)
    payload["text"]["show"] = sorted(theme.text.show)  # json.dumps rejects a frozenset
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


# --- card layout ----------------------------------------------------------

# Geometry copied from ui.py (STATS_X, BAR_WIDTH, MODEL_ROW_Y,
# MODEL_BAR_OFFSET). The step constants below are chosen so the default
# theme reproduces today's Tk coordinates exactly.
STATS_X = 132
STATS_W = 158
FULL_X = 8
FULL_W = 284
CAT_AREA = (8, 8, 112, 112)
FIRST_ROW_Y = 12            # top of the limits stack
MODELS_FIRST_ROW_Y = 20
LABEL_H = 16                # nominal height of a 9pt label
SMALL_H = 14                # nominal height of an 8pt line
LABEL_STEP = 18             # label top to bar top, and line top to next row
LINE_GAP = 4                # bar bottom to the reset line
MODEL_NAME_STEP = 14        # name top to bar top (MODEL_BAR_OFFSET)
MODEL_ROW_GAP = 6           # bar bottom to the next row
MODEL_STATUS_GAP = 10       # last bar bottom to the status line
PERCENT_W = 40              # nominal width of the "42%" run
VALUE_W = 60                # nominal width of a per-model value
ACCOUNT_LABEL_RECT = (194, 4, 100, SMALL_H)  # right edge at 294, as ui.py


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def intersects(self, other: "Rect") -> bool:
        """True when the interiors overlap; rects that only touch do not."""
        return (self.x < other.right and other.x < self.right
                and self.y < other.bottom and other.y < self.bottom)

    def contains(self, other: "Rect") -> bool:
        return (self.x <= other.x and self.y <= other.y
                and other.right <= self.right and other.bottom <= self.bottom)


@dataclass(frozen=True)
class CardLayout:
    """Named rectangles in card coordinates. Absent elements have no entry.

    rects holds the leaf elements, which never overlap each other (except
    account_label, drawn over the corner as in Tk). lines holds the full row
    behind each percent/reset pair ("session", "weekly") and overlaps its
    two runs by design.
    """
    layout: str                 # resolved: "beside_cat" or "full_width"
    rects: Mapping[str, Rect]
    lines: Mapping[str, Rect]

    def __getitem__(self, name: str) -> Rect:
        return self.rects[name]

    def __contains__(self, name: str) -> bool:
        return name in self.rects

    def get(self, name: str) -> Optional[Rect]:
        return self.rects.get(name)


def layout_card(theme: Theme, view: str, pane_spot: str) -> CardLayout:
    """Place every shown element of one card. Pure; run once per change.

    view and pane_spot come from code, not user files, so a bad one is a
    programmer error. Hidden elements are absent and the rows below them
    move up; a taller bar pushes later rows down.
    """
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}")
    if pane_spot not in SPOTS:
        raise ValueError(f"unknown spot {pane_spot!r}")
    # full_width has no cat area, so it only holds when the cat is outside.
    wide = theme.bars.layout == "full_width" and pane_spot != "inside"
    x, w = (FULL_X, FULL_W) if wide else (STATS_X, STATS_W)
    show = theme.text.show
    bar_h = theme.bars.height

    rects = {}
    lines = {}
    if not wide:
        rects["cat_area"] = Rect(*CAT_AREA)

    if view == "limits":
        y = FIRST_ROW_Y
        for name in ("session", "weekly"):
            if "section_labels" in show:
                rects[f"{name}_label"] = Rect(x, y, w, LABEL_H)
                y += LABEL_STEP
            rects[f"{name}_bar"] = Rect(x, y, w, bar_h)
            y += bar_h + LINE_GAP
            # With both runs hidden the line and its step go too, so the
            # next row sits one gap below the bar.
            if "percent" in show or "reset" in show:
                run_x = x
                if "percent" in show:
                    rects[f"{name}_percent"] = Rect(run_x, y, PERCENT_W, SMALL_H)
                    run_x += PERCENT_W
                if "reset" in show:
                    rects[f"{name}_reset"] = Rect(run_x, y, x + w - run_x, SMALL_H)
                lines[name] = Rect(x, y, w, SMALL_H)
                y += LABEL_STEP
    else:
        y = MODELS_FIRST_ROW_Y
        value_x = FULL_X + FULL_W - VALUE_W  # right-aligned at CARD_WIDTH - 8
        for i in range(3):
            if "model_rows" in show:
                rects[f"model_name_{i}"] = Rect(x, y, value_x - x, SMALL_H)
                rects[f"model_value_{i}"] = Rect(value_x, y, VALUE_W, SMALL_H)
                y += MODEL_NAME_STEP
            rects[f"model_bar_{i}"] = Rect(x, y, w, bar_h)
            y += bar_h + MODEL_ROW_GAP
        # y is one row gap below the last bar; status sits MODEL_STATUS_GAP below it
        y += MODEL_STATUS_GAP - MODEL_ROW_GAP

    if "status" in show:
        rects["status"] = Rect(x, y, w, SMALL_H)
    if "account_label" in show:
        rects["account_label"] = Rect(*ACCOUNT_LABEL_RECT)
    return CardLayout("full_width" if wide else "beside_cat", rects, lines)


# --- cat spots ------------------------------------------------------------

# One sprite frame in logical px: template cells times sprites.SCALE.
SPRITE_W = len(sprites.SITTING_TEMPLATE[0]) * sprites.SCALE
SPRITE_H = len(sprites.SITTING_TEMPLATE) * sprites.SCALE


@dataclass(frozen=True)
class Placement:
    origin: Tuple[int, int]     # the sprite's top-left in card coordinates
    mirrored: bool              # draw the art flipped (a cat facing the card)


@dataclass(frozen=True)
class Track:
    """A walkable horizontal segment in card coordinates."""
    y: int
    x_min: int
    x_max: int


# inside: the cat canvas is at (8, 8) and sprite_bounds centres the 104-high
# sprite in it with a 4 px y offset. Side spots are flush with the card edge
# and bottom-aligned with it. The side-on art is assumed to face right, so
# beside_right is the mirrored one; swap the flags if #116 draws it the other way.
_PLACEMENTS = {
    "inside": Placement((8, 12), False),
    "on_top": Placement((8, -SPRITE_H), False),
    "beside_left": Placement((-SPRITE_W, CARD_HEIGHT - SPRITE_H), False),
    "beside_right": Placement((CARD_WIDTH, CARD_HEIGHT - SPRITE_H), True),
}

# Where the scratching pose is valid: the cat touches the card and faces it.
SCRATCH_SPOTS = frozenset({"beside_left", "beside_right"})


def placement(spot: str) -> Placement:
    return _PLACEMENTS[spot]


def top_edge_track() -> Track:
    """The cat's walkable segment along the card's top edge. on_top lies on it."""
    return Track(y=-SPRITE_H, x_min=0, x_max=CARD_WIDTH - SPRITE_W)


# --- several accounts -----------------------------------------------------

_MAX_ROWS = 4  # matches ui.grid_size


def grid_shape(pane_count: int) -> Tuple[int, int]:
    """(cols, rows), filled row-major and capped at 4 rows, as ui.grid_size."""
    if pane_count < 1:
        raise ValueError("pane_count must be at least 1")
    cols = math.ceil(pane_count / _MAX_ROWS)
    return cols, math.ceil(pane_count / cols)


def _outside_reachable(pane_index: int, pane_count: int) -> FrozenSet[str]:
    """Outside spots a pane can use given only where it sits in the grid: a
    spot is reachable from a pane on the matching outer edge."""
    cols, _rows = grid_shape(pane_count)
    row, col = divmod(pane_index, cols)
    spots = set()
    if row == 0:
        spots.add("on_top")
    if col == 0:
        spots.add("beside_left")
    if col == cols - 1 or pane_index + 1 >= pane_count:
        spots.add("beside_right")  # no right neighbour
    return frozenset(spots)


def resolve_home(cat_home: str, pane_index: int, pane_count: int) -> str:
    """The account's cat_home if that outside spot is reachable from this
    pane, else "inside". Independent of the theme, so it never disagrees
    with layout_card, which falls back to beside_cat for an inside home."""
    if cat_home in _outside_reachable(pane_index, pane_count):
        return cat_home
    return "inside"


def reachable_spots(theme: Theme, cat_home: str, pane_index: int,
                    pane_count: int) -> FrozenSet[str]:
    """Spots #22 may move this pane's cat between. Layout keys on the
    resolved home: a full_width pane whose home is outside has no room for
    the cat in the card, so inside is left out. Any other pane (beside_cat,
    or full_width with an inside or unreachable home, which layout_card
    renders as beside_cat) can always go home."""
    outside = _outside_reachable(pane_index, pane_count)
    if (theme.bars.layout == "full_width"
            and resolve_home(cat_home, pane_index, pane_count) != "inside"):
        return outside
    return outside | {"inside"}


# --- window geometry ------------------------------------------------------

@dataclass(frozen=True)
class WindowLayout:
    margins: Tuple[int, int, int, int]   # left, top, right, bottom
    size: Tuple[int, int]
    pane_origins: Tuple[Tuple[int, int], ...]


def _round_up4(value: int) -> int:
    return -(-value // 4) * 4


def layout_window(homes: Sequence[str], wander: bool = False) -> WindowLayout:
    """Window size, margins and pane origins. Compute on a change of theme,
    pane count or homes, never per frame, so the window never resizes while
    a cat wanders. homes is every pane's resolved home spot in pane order.
    wander=False sizes the margins for the homes only; wander=True for every
    spot any pane can reach, so #22 can use the whole range."""
    count = len(homes)
    cols, rows = grid_shape(count)
    used = set()
    for i, home in enumerate(homes):
        spots = {home} | (_outside_reachable(i, count) if wander else set())
        if "beside_right" in spots and i % cols != cols - 1:
            # last pane of a ragged row: its right side is an empty grid
            # cell, already inside the window
            spots.discard("beside_right")
        used |= spots
    left = _round_up4(SPRITE_W) if "beside_left" in used else 0
    top = _round_up4(SPRITE_H) if "on_top" in used else 0
    right = _round_up4(SPRITE_W) if "beside_right" in used else 0
    margins = (left, top, right, 0)  # no spot reaches below the card
    origins = tuple(
        (left + (i % cols) * CARD_WIDTH, top + (i // cols) * CARD_HEIGHT)
        for i in range(count)
    )
    size = (left + cols * CARD_WIDTH + right, top + rows * CARD_HEIGHT)
    return WindowLayout(margins, size, origins)


def hit_region(window: WindowLayout, cat_rects: Sequence[Rect]) -> list:
    """Rects, in window coordinates, that should catch clicks: every pane's
    card (at every opacity) plus the given cat rects. Empty cells of a
    ragged last row are left out."""
    cards = [Rect(x, y, CARD_WIDTH, CARD_HEIGHT) for x, y in window.pane_origins]
    return cards + list(cat_rects)


def snap(value: float, dpr: float) -> float:
    """Round to a whole device pixel."""
    return round(value * dpr) / dpr


def cell_size(dpr: float) -> int:
    """Device pixels per sprite cell, the frame cache key."""
    return round(sprites.SCALE * dpr)


# --- combining theme and account -----------------------------------------

@dataclass(frozen=True)
class PaneStyle:
    background: str
    foreground: str
    dim: str
    opacity: int
    bar_fill_override: Optional[str]
    palette: Mapping[str, str]
    home: str


def resolve_pane_style(theme: Theme, customization, has_accent: bool,
                       pane_index: int, pane_count: int) -> PaneStyle:
    """The one place theme and account meet, so precedence is defined once:
    accent over the account override over the theme."""
    # customize imports this module, so import it here to avoid a cycle.
    from tokitty.customize import effective_palette

    overrides = customization.overrides
    if has_accent:
        background, foreground, opacity = ACCENT_BG, ACCENT_FG, 100
    else:
        background = overrides.get("card_bg") or theme.card.background
        foreground, opacity = theme.text.foreground, theme.card.opacity
    return PaneStyle(
        background=background,
        foreground=foreground,
        dim=theme.text.dim,
        opacity=opacity,
        bar_fill_override=overrides.get("bar_fill"),
        palette=effective_palette(customization),
        home=resolve_home(customization.cat_home, pane_index, pane_count),
    )
