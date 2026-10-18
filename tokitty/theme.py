"""Plain-Python description of the widget's look: card, bars and text.

One global theme per user, stored in theme.json next to settings.json and
customization.json (see paths.py). Per-account looks (coat, card_bg,
bar_fill, cat_home) stay in customization.json. The colours are literals
here, not imported from a GUI module, because this module must not need a
toolkit. The default look is "refined" (#139); "classic" is what the widget
drew before and stays available as a preset.

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
from typing import FrozenSet, Mapping, Optional, Sequence, Set, Tuple

from tokitty import sprites

THEME_FILENAME = "theme.json"
_SETTINGS_FILENAME = "settings.json"  # read raw for the opacity migration only

# Card size is fixed: a resizable card is the main way a layout ends up
# with text outside it. Values are the original Tk window's CARD_WIDTH / PANE_HEIGHT.
CARD_WIDTH = 300
CARD_HEIGHT = 128

# Permission accent (the original Tk window's ACCENT_BG / ACCENT_FG). Constants, not theme
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

SPOTS = ("inside", "inside_right", "on_top")
VIEWS = ("limits", "models")

# Opacity levels the Tk build offered (transparency.py LEVELS), the only
# values accepted when migrating from settings.json.
_TK_OPACITY_LEVELS = (100, 90, 80, 70, 60, 50)

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass(frozen=True)
class CardStyle:
    design: str = "refined"     # "refined" | "classic": the arrangement of the card's text
    background: str = "#1b1b21"
    opacity: int = 100          # percent, 0..100 in steps of 10
    radius: int = 10            # 0..16
    border_width: int = 1       # 0..3
    border_color: str = "#2e2e38"


@dataclass(frozen=True)
class BarStyle:
    layout: str = "beside_cat"  # "beside_cat" | "full_width"
    shape: str = "rounded"      # "flat" | "rounded"
    height: int = 6             # 4..10
    track: str = "#2c2c35"
    low: str = "#4caf6b"
    mid: str = "#e0a838"
    high: str = "#e05252"
    neutral: str = "#5a7fa8"
    mid_at: int = 50
    high_at: int = 80


@dataclass(frozen=True)
class TextStyle:
    foreground: str = "#ececf1"
    dim: str = "#8e8e98"
    show: FrozenSet[str] = frozenset(TEXT_ELEMENTS)
    halo: str = "off"           # "off" | "auto" | "on"
    halo_below: int = 60        # "auto" turns the halo on below this opacity


@dataclass(frozen=True)
class CatStyle:
    shadow: bool = True         # the ground shadow under the cat
    wander: bool = True         # the cat now and then strolls along the card's top edge (#22)
    time_of_day: bool = True    # the cat naps more at night and roams more in the morning (#138)


@dataclass(frozen=True)
class Theme:
    card: CardStyle = field(default_factory=CardStyle)
    bars: BarStyle = field(default_factory=BarStyle)
    text: TextStyle = field(default_factory=TextStyle)
    cat: CatStyle = field(default_factory=CatStyle)


# --- looks ------------------------------------------------------------------

DESIGNS = ("refined", "classic")

# What the widget drew before #139, exactly.
CLASSIC = Theme(
    card=CardStyle(design="classic", background="#1c1c22", radius=0, border_width=0,
                   border_color="#333340"),
    bars=BarStyle(shape="flat", height=8, track="#333340"),
    text=TextStyle(foreground="#f0f0f0", dim="#8a8a92"),
)
REFINED = Theme()

# The fields a look owns. Everything else (opacity, bar layout and thresholds,
# what text shows, the halo, the cat) is the user's own and no look touches it.
LOOK_FIELDS = {
    "card": ("design", "background", "radius", "border_width", "border_color"),
    "bars": ("shape", "height", "track", "low", "mid", "high", "neutral"),
    "text": ("foreground", "dim"),
}


def design_preset(design: str) -> Theme:
    """The preset theme for a design name; raises ValueError for another."""
    if design == "refined":
        return REFINED
    if design == "classic":
        return CLASSIC
    raise ValueError(f"unknown design {design!r}")


def apply_design(theme: Theme, design: str) -> Theme:
    """`theme` with the look fields of the named design, keeping the rest."""
    preset = design_preset(design)
    parts = {}
    for section, keys in LOOK_FIELDS.items():
        old, new = getattr(theme, section), getattr(preset, section)
        parts[section] = replace(old, **{k: getattr(new, k) for k in keys})
    return replace(theme, **parts)


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

_REJECT = object()  # a value the field's check refused, as opposed to one that is absent


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


def _bool(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _section(raw, key: str) -> dict:
    sub = raw.get(key)
    return sub if isinstance(sub, dict) else {}


class _Picker:
    """Reads one section's fields through the checks above, noting each
    present-but-refused key as "section.key" in `bad`. A missing key falls
    back silently: only a value someone supplied can be rejected."""

    def __init__(self, raw: dict, section: str, bad: set) -> None:
        self._raw, self._section, self._bad = raw, section, bad

    def _pick(self, key: str, default, check):
        value = check(self._raw.get(key), _REJECT)
        if value is _REJECT:
            if key in self._raw:
                self._bad.add(f"{self._section}.{key}")
            return default
        return value

    def hex(self, key: str, default: str) -> str:
        return self._pick(key, default, _hex)

    def int(self, key: str, lo: int, hi: int, default: int, step: int = 1) -> int:
        return self._pick(key, default, lambda v, d: _int(v, lo, hi, d, step))

    def enum(self, key: str, allowed, default: str) -> str:
        return self._pick(key, default, lambda v, d: _enum(v, allowed, d))

    def bool(self, key: str, default: bool) -> bool:
        return self._pick(key, default, _bool)


def _clean_card(raw: dict, d: CardStyle, bad: Optional[set] = None) -> CardStyle:
    pick = _Picker(raw, "card", set() if bad is None else bad)
    return CardStyle(
        design=pick.enum("design", DESIGNS, d.design),
        background=pick.hex("background", d.background),
        opacity=pick.int("opacity", 0, 100, d.opacity, step=10),
        radius=pick.int("radius", 0, 16, d.radius),
        border_width=pick.int("border_width", 0, 3, d.border_width),
        border_color=pick.hex("border_color", d.border_color),
    )


def _clean_bars(raw: dict, d: BarStyle, bad: Optional[set] = None) -> BarStyle:
    bad = set() if bad is None else bad
    pick = _Picker(raw, "bars", bad)
    bars = BarStyle(
        layout=pick.enum("layout", ("beside_cat", "full_width"), d.layout),
        shape=pick.enum("shape", ("flat", "rounded"), d.shape),
        height=pick.int("height", 4, 10, d.height),
        track=pick.hex("track", d.track),
        low=pick.hex("low", d.low),
        mid=pick.hex("mid", d.mid),
        high=pick.hex("high", d.high),
        neutral=pick.hex("neutral", d.neutral),
        mid_at=pick.int("mid_at", 0, 100, d.mid_at),
        high_at=pick.int("high_at", 0, 100, d.high_at),
    )
    if bars.mid_at >= bars.high_at:
        bad.update(("bars.mid_at", "bars.high_at"))
        bars = replace(bars, mid_at=d.mid_at, high_at=d.high_at)
    return bars


def _clean_text(raw: dict, d: TextStyle, bad: Optional[set] = None) -> TextStyle:
    bad = set() if bad is None else bad
    pick = _Picker(raw, "text", bad)
    show = raw.get("show")
    if isinstance(show, list):
        # Unknown names are dropped without being reported: the Theme tab
        # only offers TEXT_ELEMENTS, so only a hand-edited file has them.
        shown = frozenset(n for n in show if isinstance(n, str) and n in TEXT_ELEMENTS)
    else:
        if "show" in raw:
            bad.add("text.show")
        shown = d.show
    return TextStyle(
        foreground=pick.hex("foreground", d.foreground),
        dim=pick.hex("dim", d.dim),
        show=shown,
        halo=pick.enum("halo", ("off", "auto", "on"), d.halo),
        halo_below=pick.int("halo_below", 0, 100, d.halo_below),
    )


def _clean_cat(raw: dict, d: CatStyle, bad: Optional[set] = None) -> CatStyle:
    pick = _Picker(raw, "cat", set() if bad is None else bad)
    return CatStyle(
        shadow=pick.bool("shadow", d.shadow),
        wander=pick.bool("wander", d.wander),
        time_of_day=pick.bool("time_of_day", d.time_of_day),
    )


def _validate(raw, fallback: Theme) -> Tuple[Theme, set]:
    data = raw if isinstance(raw, dict) else {}
    bad: set = set()
    for name in ("card", "bars", "text", "cat"):
        if name in data and not isinstance(data[name], dict):
            bad.add(name)
    theme = Theme(
        card=_clean_card(_section(data, "card"), fallback.card, bad),
        bars=_clean_bars(_section(data, "bars"), fallback.bars, bad),
        text=_clean_text(_section(data, "text"), fallback.text, bad),
        cat=_clean_cat(_section(data, "cat"), fallback.cat, bad),
    )
    return theme, bad


def theme_from_dict(raw, fallback: Theme) -> Theme:
    """Validate a plain dict shaped like theme.json, with no file read and
    no opacity migration. Each field that fails its check takes `fallback`'s
    value; a mid_at at or above high_at resets both."""
    return _validate(raw, fallback)[0]


def rejected_fields(raw, fallback: Theme) -> Set[str]:
    """The "section.field" names theme_from_dict would replace with
    `fallback`'s value because a value was supplied and refused. An absent
    field is not rejected, and neither is an unknown name inside
    text.show. An inverted mid_at/high_at reports both; a section that is
    not a dict reports the section name."""
    return _validate(raw, fallback)[1]


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
    if "design" not in _section(data, "card"):
        data = _migrate_to_refined(data)
    return theme_from_dict(data, Theme())


def _same(saved, classic) -> bool:
    """A saved value equals the classic one (hex colours compare without case)."""
    if type(saved) is not type(classic):
        return False
    if isinstance(saved, str):
        return saved.lower() == classic.lower()
    return saved == classic


def _migrate_to_refined(data: dict) -> dict:
    """A theme.json with no card.design was written before #139. A look field
    still at its classic value (or absent) moves to the refined one; a field
    the user changed keeps its saved value. The file itself is not rewritten."""
    out = dict(data)
    for section, keys in LOOK_FIELDS.items():
        raw = data.get(section)
        if section in data and not isinstance(raw, dict):
            continue  # reported as a bad section by the validator, not repaired here
        raw = dict(raw or {})
        classic, refined = getattr(CLASSIC, section), getattr(REFINED, section)
        for key in keys:
            if key not in raw or _same(raw[key], getattr(classic, key)):
                raw[key] = getattr(refined, key)
        out[section] = raw
    return out


def theme_to_dict(theme: Theme) -> dict:
    """The shape theme.json holds: nested dicts, show as a sorted list."""
    payload = asdict(theme)
    payload["text"]["show"] = sorted(theme.text.show)  # json.dumps rejects a frozenset
    return payload


def save_theme(state_dir, theme: Theme) -> None:
    path = Path(state_dir) / THEME_FILENAME
    payload = theme_to_dict(theme)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


# --- card layout ----------------------------------------------------------

# Geometry copied from the original Tk ui.py (STATS_X, BAR_WIDTH, MODEL_ROW_Y,
# MODEL_BAR_OFFSET). The step constants below are chosen so the default
# theme reproduces the original Tk coordinates exactly.
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
ACCOUNT_LABEL_RECT = (194, 4, 100, SMALL_H)  # right edge at 294, as in the original Tk ui.py
# The refined design (#139). Rows are taller and breathe more; see _layout_refined.
R_ROW_H = 16                # label row: "Session" left, "42%" right
R_BAR_DY = 19               # label row top to bar top
R_RESET_GAP = 3             # bar bottom to the reset line
R_ROW_STEP = 18             # reset line top to the next row
R_MODEL_NAME_STEP = 17      # model row top to its bar
R_MODEL_GAP = 9             # bar bottom to the next model row
R_ROW_RIGHT = CARD_WIDTH - 10   # right edge of the account label, as in the Tk card
# inside_right: how far the stats block moves left so its left edge sits
# where its old right gap was (CARD_WIDTH - STATS_X - STATS_W = 10).
STATS_SHIFT_RIGHT = STATS_X - (CARD_WIDTH - STATS_X - STATS_W)


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

    def moved(self, dx: int) -> "Rect":
        return Rect(self.x + dx, self.y, self.w, self.h)

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
    wide = theme.bars.layout == "full_width" and pane_spot == "on_top"
    x, w = (FULL_X, FULL_W) if wide else (STATS_X, STATS_W)
    show = theme.text.show
    bar_h = theme.bars.height

    rects = {}
    lines = {}
    if not wide:
        rects["cat_area"] = Rect(*CAT_AREA)

    if theme.card.design == "refined":
        _layout_refined(rects, lines, view, x, w, show, bar_h)
    elif view == "limits":
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

    if theme.card.design != "refined":
        if "status" in show:
            rects["status"] = Rect(x, y, w, SMALL_H)
        if "account_label" in show:
            rects["account_label"] = Rect(*ACCOUNT_LABEL_RECT)
    if pane_spot == "inside_right":
        # Swap the two columns as blocks: nothing inside the stats block is
        # mirrored, so values stay right of their names.
        rects = {name: rect.moved(-STATS_SHIFT_RIGHT) for name, rect in rects.items()}
        lines = {name: rect.moved(-STATS_SHIFT_RIGHT) for name, rect in lines.items()}
        rects["cat_area"] = Rect(CARD_WIDTH - CAT_AREA[0] - CAT_AREA[2], *CAT_AREA[1:])
    return CardLayout("full_width" if wide else "beside_cat", rects, lines)


def _layout_refined(rects: dict, lines: dict, view: str, x: int, w: int,
                    show: FrozenSet[str], bar_h: int) -> None:
    """The refined card's rows, added to `rects` and `lines`.

    Limits: a label row (section name left, percent right), the bar, then the
    reset line. Models: name and value on one row, then the bar. The status
    line and the account label share the row below the last bar; the status
    rect spans the column and the account label is drawn over its right end,
    so the painter elides the status to fit. lines[name] is the whole limits
    row, label row top to reset line bottom, and overlaps its leaves by design.
    """
    y = FIRST_ROW_Y
    if view == "limits":
        for name in ("session", "weekly"):
            top = y
            labels, percent = "section_labels" in show, "percent" in show
            if labels or percent:
                if labels:
                    rects[f"{name}_label"] = Rect(x, y, w - PERCENT_W if percent else w, R_ROW_H)
                if percent:
                    rects[f"{name}_percent"] = Rect(x + w - PERCENT_W, y, PERCENT_W, R_ROW_H)
                y += R_BAR_DY
            rects[f"{name}_bar"] = Rect(x, y, w, bar_h)
            y += bar_h + R_RESET_GAP
            if "reset" in show:
                rects[f"{name}_reset"] = Rect(x, y, w, SMALL_H)
                lines[name] = Rect(x, top, w, y + SMALL_H - top)
                y += R_ROW_STEP
            else:
                lines[name] = Rect(x, top, w, y - R_RESET_GAP - top)
    else:
        for i in range(3):
            if "model_rows" in show:
                rects[f"model_name_{i}"] = Rect(x, y, w - VALUE_W, SMALL_H)
                rects[f"model_value_{i}"] = Rect(x + w - VALUE_W, y, VALUE_W, SMALL_H)
                y += R_MODEL_NAME_STEP
            rects[f"model_bar_{i}"] = Rect(x, y, w, bar_h)
            y += bar_h + R_MODEL_GAP
    # A tall bar in the models view would push the row past the card.
    y = min(y, CARD_HEIGHT - SMALL_H)
    if "status" in show:
        rects["status"] = Rect(x, y, w, SMALL_H)
    if "account_label" in show:
        rects["account_label"] = Rect(x, y, R_ROW_RIGHT - x, SMALL_H)


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
# sprite in it with a 4 px y offset. inside_right is the same canvas on the
# card's other side, mirrored so the cat faces the stats.
_PLACEMENTS = {
    "inside": Placement((8, 12), False),
    "inside_right": Placement((CARD_WIDTH - 8 - SPRITE_W, 12), True),
    "on_top": Placement((8, -SPRITE_H), False),
}

# The cat scratches the near edge of the stats column from inside the card.
SCRATCH_SPOTS = frozenset({"inside", "inside_right"})


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
    """Outside spots a pane can use given only where it sits in the grid:
    on_top from a first-row pane. Nothing sits beside a card."""
    cols, _rows = grid_shape(pane_count)
    return frozenset({"on_top"}) if pane_index < cols else frozenset()


def resolve_home(cat_home: str, pane_index: int, pane_count: int) -> str:
    """The account's cat_home if that spot is reachable from this pane, else
    "inside". Independent of the theme, so it never disagrees with
    layout_card, which falls back to beside_cat for an inside home."""
    if cat_home == "inside_right" or cat_home in _outside_reachable(pane_index, pane_count):
        return cat_home
    return "inside"


def reachable_spots(theme: Theme, cat_home: str, pane_index: int,
                    pane_count: int) -> FrozenSet[str]:
    """Spots #22 may move this pane's cat between. Layout keys on the
    resolved home: a full_width pane whose home is on_top has no cat area, so
    neither inside spot is available. Any other pane (beside_cat, or
    full_width with an inside home, which layout_card renders as beside_cat)
    can always go home."""
    outside = _outside_reachable(pane_index, pane_count)
    if (theme.bars.layout == "full_width"
            and resolve_home(cat_home, pane_index, pane_count) == "on_top"):
        return outside
    return outside | {"inside", "inside_right"}


# --- window geometry ------------------------------------------------------

@dataclass(frozen=True)
class WindowLayout:
    margins: Tuple[int, int, int, int]   # left, top, right, bottom
    size: Tuple[int, int]
    pane_origins: Tuple[Tuple[int, int], ...]


# Rows an on_top cat can hop above its resting place, kept clear of the
# window's top edge.
HOP_HEADROOM = 3 * sprites.SCALE


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
        used |= {home} | (_outside_reachable(i, count) if wander else set())
    top = _round_up4(SPRITE_H) + HOP_HEADROOM if "on_top" in used else 0
    margins = (0, top, 0, 0)  # no spot reaches past the sides or below the card
    origins = tuple(
        ((i % cols) * CARD_WIDTH, top + (i // cols) * CARD_HEIGHT)
        for i in range(count)
    )
    size = (cols * CARD_WIDTH, top + rows * CARD_HEIGHT)
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
