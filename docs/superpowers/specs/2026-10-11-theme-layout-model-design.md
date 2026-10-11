# Widget theme and layout model (design)

Issue: [#110](https://github.com/nickwolf/tokitty/issues/110), part of milestone 8 "Qt widget: free-roaming cat" under [#28](https://github.com/nickwolf/tokitty/issues/28). Consumers: [#111](https://github.com/nickwolf/tokitty/issues/111) (main widget port), [#112](https://github.com/nickwolf/tokitty/issues/112) (settings port, which builds the editors), [#22](https://github.com/nickwolf/tokitty/issues/22) (wandering).

Status: draft, 2026-10-11. Four decisions taken by Nick the same day, listed below. Not reviewed yet.

## Goal

One plain-Python description of how the widget looks and where everything on it sits, so the Qt port paints from data instead of from constants scattered through `ui.py`, and customization can reach past the coat. It also gives #22 a vocabulary of named spots for the cat, so wandering moves between spots rather than between hardcoded coordinates.

## Decided (owner, 2026-10-11)

1. **Global theme, per-account accents.** Card shape, opacity, bar style, which text shows, layout and the cat's home spot are one theme for the whole widget, stored in `settings.json`. Per-account look stays in `customization.json` exactly as today: coat colourway and pattern, `coat_base`/`coat_shade`, `card_bg`, `bar_fill`, label. This is the split the code already has (opacity global in `settings.py:47`, colours per account in `customize.py:31`).
2. **The lowest opacity is invisible but still clickable.** A 0% card paints at alpha 1/255. It looks the same as true 0% and still catches drags and right-clicks across the whole card. The spike measured exactly this on Windows (findings, section 2). There is no "click-through at 0%" option.
3. **Model only.** The Tk widget keeps its constants. Nothing in `ui.py` reads the model in #110; the Qt widget in #111 is its first consumer. The Tk code is being deleted, and corners, borders and halos are things Tk cannot draw on a translucent surface anyway.
4. **No settings UI in #110.** The theme is editable by hand in `settings.json` until #112 builds the editors once, in Qt.

## Scope and non-goals

In scope: `tokitty/theme.py` (data model, defaults, validation, geometry), a `theme` field on `Settings`, the function that combines the theme with one account's `Customization` into what a pane is painted with, and tests that run on all three CI OSes.

Not in scope: any painting, any Qt or Tk import, settings UI, the wandering state machine itself (#22 owns timing, direction and z-order; this model only says where the spots are), macOS click masks (`setMask()` is #111; the model supplies the rectangles), per-pane opacity, fonts beyond size and weight, the duplicated palette in `settings_widgets.py:21-29`.

## Where it lives

`tokitty/theme.py`, next to `customize.py`. Imports allowed: the standard library and `tokitty.sprites` (already GUI-free, `sprites.py:21` imports only `typing`). A test asserts the module imports with `tkinter` and `PySide6` blocked from `sys.modules`, so a stray GUI import fails CI on every OS rather than only where the toolkit happens to be missing.

The theme is persisted as a nested `theme` object inside `settings.json`, as a new `theme: Theme` field on the frozen `Settings` dataclass. It has to be a real field: `save_settings` (`settings.py:347-351`) writes `asdict(settings)` and nothing else, so an unknown top-level key would be wiped by the next unrelated settings save. `load_settings` calls `theme.load_theme(data.get("theme"), legacy_opacity=data.get("opacity"))`.

`customization.json` does not change at all. Its loader, its keys and its file stay as they are, which is the simplest way to keep existing files loading.

## The model

All values are logical pixels at 96 dpi, the unit `ui.py` already uses (`dpi.py:24`). Every default equals what the Tk widget draws today, with the `ui.py` line it comes from, so the first Qt build looks like the current widget before anyone changes a setting.

```python
@dataclass(frozen=True)
class CardStyle:
    width: int = 300                # ui.py:33 CARD_WIDTH
    height: int = 128               # ui.py:34 PANE_HEIGHT
    background: str = "#1c1c22"     # ui.py:39 BG_COLOR
    opacity: int = 100              # percent, 0..100 in steps of 10
    radius: int = 0                 # 0..24
    border_width: int = 0           # 0..4
    border_color: str = "#333340"

@dataclass(frozen=True)
class BarStyle:
    layout: str = "beside_cat"      # "beside_cat" | "full_width"
    shape: str = "flat"             # "flat" | "rounded"
    height: int = 8                 # ui.py:38 BAR_HEIGHT, 4..16
    track: str = "#333340"          # ui.py:42 BAR_BG
    low: str = ...                  # display.py:13-15 GREEN/AMBER/RED
    mid: str = ...
    high: str = ...
    mid_at: int = 50                # display.py:18-23 thresholds
    high_at: int = 80

@dataclass(frozen=True)
class TextStyle:
    foreground: str = "#f0f0f0"     # ui.py:40 FG_COLOR
    dim: str = "#8a8a92"            # ui.py:41 DIM_COLOR
    show: frozenset = frozenset(TEXT_ELEMENTS)
    halo: str = "auto"              # "off" | "auto" | "on"
    halo_below: int = 60            # "auto" turns the halo on below this opacity

@dataclass(frozen=True)
class Theme:
    card: CardStyle
    bars: BarStyle
    text: TextStyle
    cat_home: str = "inside"        # a Spot name, see below
```

The permission accent colours (`ui.py:47-48`) stay constants in `theme.py`, not theme fields. The accent exists to be noticed, so the user does not get to theme it away.

### Opacity

`CardStyle.opacity` takes 0 to 100 in steps of 10, which the mouse wheel and a menu can both walk. The Tk build's `LEVELS` (`transparency.py:29`, 100 down to 50) exist because a colour-keyed Tk card is click-through and unrecoverable at 0; that reason does not carry over to Qt, where decision 2 removes it.

`card_alpha(opacity) -> int` returns `max(1, round(opacity * 255 / 100))`. That `max(1, ...)` is decision 2, as one tested line, so the painter cannot get it wrong.

Migration: when `settings.json` has no `theme` object, `CardStyle.opacity` is seeded from the legacy top-level `opacity` if it is a valid Tk level, else 100. The top-level `opacity` key stays where it is for the Tk build; the Qt build reads `theme.card.opacity` only.

The accent overrides opacity to 100 for that pane, as the Tk build does (`transparency.py:53-62`).

### Text

`TEXT_ELEMENTS` is a closed set covering what the card draws today:

- `section_labels`: "SESSION" and "WEEK" (`ui.py:292,299`)
- `percent`: the "42%" at the start of each reset line
- `reset`: the countdown or reset time after it (`ui.py:489,496` draw `f"{pct:.0f}% · {reset}"` as one label; the model splits it into two runs on the same row)
- `status`: credits or the error hint (`ui.py:313-317`)
- `account_label`: the dim top-right name (`ui.py:342-345`)

Splitting `percent` from `reset` is for the halo. Nick's verdict from the Mac run (findings, gate 5): the halo is needed for "SESSION", "WEEK" and the percentages at low opacity, and makes the small reset text harder to read. So `halo_applies(element)` is true for `section_labels`, `percent` and `account_label`, and false for `reset` and `status`. `halo_active(text, opacity)` resolves `"auto"` against `halo_below`. The default threshold of 60 is a guess to tune in #111 with real screenshots, not a measured value.

Hiding an element removes it from the layout output, it does not leave a hole. Hiding `section_labels` moves each bar up to where its label was.

Fonts stay as the Tk build has them, 9pt bold labels and 8pt everything else, as constants in `theme.py`. The family is left to the painter (Segoe UI on Windows today, the platform default elsewhere).

### Bars

`shape="rounded"` means a pill, radius `height / 2`, for track and fill alike. `layout="beside_cat"` is today's stats column at x=132 (`ui.py:36`), width 158 (`ui.py:37`). `layout="full_width"` runs the bars and text from x=8 to `width - 8` and leaves the cat area empty, for a theme where the cat lives on top or beside. Bar layout is deliberately not tied to where the cat is at the moment: wandering would otherwise rearrange the whole card every time the cat stepped out.

`bar_color(theme, pct, bar_fill_override)` replaces `display.bar_color` for Qt: the account's `bar_fill`, if set, wins over the threshold ramp, as in `ui.py:151-154`.

## Layout

`layout_card(theme, view, has_accent) -> CardLayout` is a pure function returning named rectangles in card coordinates: `cat_area`, `session_label`, `session_bar`, `session_line` (with separate `percent` and `reset` runs), the same for `weekly`, `status`, `account_label`, and for `view="per_model"` the three model rows (`ui.py:60-61`). The painter draws what it gets. Tests pin the default output to today's coordinates (`ui.py:287-345`), so a default Qt card lands every element where the Tk card has it.

## Cat spots

A `Spot` names a place for the cat relative to one card, using the spike's vocabulary (spike brief, line 34):

- `inside`: today's cat canvas, (8, 8), 112 by 112, sprite centred (`ui.py:124-140`)
- `on_top`: standing on the card's top edge, feet on y=0, so the sprite occupies y from -104 to 0
- `beside`: outside the card's right edge, bottom-aligned with the card
- `top_edge_track`: not a spot but a segment, the x range the cat can walk along the top edge, for #22

`spot_origin(spot, theme, sprite_cols, sprite_rows) -> (x, y)` returns the sprite's top-left in card coordinates at SCALE 4 (28 by 26 cells, 112 by 104 logical px). Mirroring does not move the origin.

`window_margins(theme, spots_in_use) -> (left, top, right, bottom)` is how much transparent margin the window needs around the card so every spot in use fits. Every margin is a multiple of 4 (findings, section 1). With only `inside` in use the margins are zero, so a theme that keeps the cat at home gets a window exactly the card's size. The spike's 16/112/128/16 is what all three spots need.

`snap(value, dpr) -> float` returns `round(value * dpr) / dpr`, the spike's snapping rule, so the cat's origin always lands on a whole device pixel. It lives here so the rule is tested once rather than reimplemented by every caller. `cell_size(dpr) -> int` is `round(SCALE * dpr)`, the frame cache key from the findings.

`hit_rects(layout, spot, opacity)` returns the rectangles that should catch the mouse: the card (always, because of decision 2), plus the cat's rectangle at its current spot. #111 turns this into the macOS `setMask()`, and can refine the cat part with `QImage.createAlphaMask()`.

### Several accounts

This is the part the spike did not cover, because it had one card. Today panes sit in a grid with no gap (`ui.py:86-100`, `cols = ceil(N/4)`). A cat on the top edge of a second-row pane would sit on the first-row pane's content.

Proposal: the margins from `window_margins` go around the whole grid, not around each pane, and outside spots are only offered to panes on the matching outer edge. `on_top` is available to first-row panes, `beside` to last-column panes. `available_spots(pane_index, pane_count) -> frozenset[Spot]` encodes that, and a pane whose home spot is unavailable falls back to `inside`. The alternative, a gap between panes wide enough for a cat, makes a two-account widget roughly 1.5 times as large to hold space that is empty most of the time.

This is the decision in the design most likely to be wrong, and the one to look at hardest in review.

## Combining theme and account

`resolve_pane_style(theme, customization, has_accent) -> PaneStyle` gives one pane its final colours: the card background is the account's `card_bg` override if present, else `theme.card.background`; the accent replaces it with `ACCENT_BG` and forces opacity to 100; bar fill follows `bar_color` above; the sprite palette comes from the existing `customize.effective_palette`. This is the only place theme and customization meet, so precedence is defined once.

## Loading and validation

Same contract as `load_settings` and `load_customization`: a hand-edited file degrades, it never crashes the widget. Each field is validated on its own and an invalid value falls back to that field's default, leaving the rest of the theme intact. Colours must match `#rrggbb` (`customize.py:34`). Integers out of range are rejected rather than clamped, so a typo falls back to the default instead of to an extreme. Booleans are rejected where an integer is expected (`True` is an `int` in Python; `settings.py:104` already guards this for opacity). Enum strings outside their set fall back. Unknown keys are dropped. A `theme` value that is not an object gives the default theme with migrated opacity.

Saving goes through the existing `save_settings`, which already writes atomically with a temp file and `os.replace`. `show` is a `frozenset` in memory and a sorted list on disk, so `asdict` output has to be converted; `theme_to_json` does that, and `save_settings` calls it.

Downgrade: an older Tk build (v0.6.1 and earlier) loading a `settings.json` with a `theme` key ignores it, and its next save drops it. The theme returns to defaults after a downgrade. Acceptable for a model nobody can edit without a text editor until #112.

## Tests

All in `tests/test_theme.py`, none marked `gui`, so they run on Linux, macOS and Windows in the existing CI matrix (`ci.yml:18-23`).

- Every default equals the Tk constant it replaces. Since `ui.py` imports `tkinter`, one extra test marked `gui` imports both and compares them directly, so drift is caught in the Linux smoke job; it is deleted with the Tk code in #111.
- `card_alpha` at 0, 10 and 100, and the `max(1, ...)` floor.
- Opacity migration from each Tk level, from a missing key, and from a garbage value.
- Per-field degradation: one bad field in an otherwise valid theme loses only that field. Booleans as integers rejected. Unknown keys dropped. Non-object `theme`.
- Round trip through `save_settings` and `load_settings`, including the `frozenset` conversion.
- `layout_card` default output matches today's coordinates, for both views. Hiding `section_labels` collapses the gap. `full_width` bars span the card.
- `spot_origin` for each spot, `window_margins` for each combination (all multiples of 4, zero for `inside` alone), `available_spots` for 1, 2, 4, 5 and 8 panes.
- `snap` at dpr 1, 1.25, 1.5, 1.75 and 2 lands on whole device pixels; `cell_size` gives 4, 5, 6, 7, 8.
- `resolve_pane_style` precedence: theme only, account override, accent over both.
- The import-isolation test described under "Where it lives".
- Existing `test_customize.py` and `test_settings.py` keep passing unchanged, apart from `Settings` gaining a field.

## Open questions

1. Outside spots in a multi-pane grid (see "Several accounts"). Edge-only is proposed.
2. The `auto` halo threshold of 60. Placeholder until #111 has screenshots.
3. Whether `cat_home` belongs in the global theme or per account. Global follows decision 1, but a two-account widget then has both cats in the same spot, and per-account would let one cat sit on top and the other inside. Cheap to move to `customization.json` later because the loader there already ignores unknown keys.
