# Widget theme and layout model (design)

Issue: [#110](https://github.com/nickwolf/tokitty/issues/110), part of milestone 8 "Qt widget: free-roaming cat" under [#28](https://github.com/nickwolf/tokitty/issues/28). Consumers: [#111](https://github.com/nickwolf/tokitty/issues/111) (main widget port), [#112](https://github.com/nickwolf/tokitty/issues/112) (settings port, which builds the editors), [#22](https://github.com/nickwolf/tokitty/issues/22) (wandering).

Status: draft, 2026-10-11. Four decisions taken by Nick the same day, listed below. A Codex review (gpt-6-sol, high) the same day found seven design defects and four factual errors in the first draft; all are folded in, and the review section at the end lists what changed.

## Goal

One plain-Python description of how the widget looks and where everything on it sits, so the Qt port paints from data instead of from constants scattered through `ui.py`, and customization can reach past the coat. It also gives #22 a vocabulary of named spots and a walkable track for the cat, so wandering moves between coordinates the model owns rather than ones #22 invents.

## Decided (owner, 2026-10-11)

1. **Global theme, per-account accents.** Card style, opacity, bar style, which text shows and layout are one theme for the whole widget. Per-account look stays in `customization.json`: coat colourway and pattern, `coat_base`/`coat_shade`, `card_bg`, `bar_fill`, label, plus the cat's home spot from decision 5. This is the split the code already has (opacity global in `settings.py:47`, colours per account in `customize.py:31`).
2. **The lowest opacity is invisible but still clickable.** A 0% card paints at alpha 1/255. It looks the same as true 0% and still catches drags and right-clicks across the whole card. The spike measured exactly this on Windows (findings, section 2). There is no "click-through at 0%" option.
3. **Model only.** The Tk widget keeps its constants. Nothing in `ui.py` reads the model in #110; the Qt widget in #111 is its first consumer.
4. **No settings UI in #110.** The theme is editable by hand until #112 builds the editors once, in Qt.
5. **Cats can sit on either side of the card, independently per account** (Nick, later the same day), and may scratch the side of the card, which is a new animation. So the cat's home spot is per account, not part of the global theme, and there are two side spots instead of one `beside`. With cards stacked in one column, each cat can pick its own side, which is what makes several accounts interesting to watch rather than a column of identical cats.

## Scope and non-goals

In scope: `tokitty/theme.py` (data model, defaults, validation, card and window geometry, spots), its own `theme.json`, an optional `cat_home` per account in `customization.json`, the function that combines the theme with one account's `Customization` into what a pane is painted with, and tests that run on all three CI OSes.

Not in scope: any painting, any Qt or Tk import, settings UI, input handling (wheel steps, menus), the scratching frames themselves (art, #116), the wandering state machine (#22 owns timing, direction, z-order and when a cat scratches; this model says where the cat may be and which way it faces), the macOS `setMask()` call itself (#111; the model supplies the region), per-pane card size, fonts beyond size and weight, the duplicated palette in `settings_widgets.py:21-29`.

## Where it lives

`tokitty/theme.py`, next to `customize.py`. Imports allowed: the standard library and `tokitty.sprites` (already GUI-free, `sprites.py:21` imports only `typing`). A test asserts the module imports with `tkinter` and `PySide6` blocked in `sys.modules`, so a stray GUI import fails CI on every OS rather than only where the toolkit happens to be missing.

The theme is stored in its own file, `theme.json`, in the same state directory as `settings.json` and `customization.json` (`paths.py`). The first draft nested it inside `settings.json`, which had two problems Codex found. `save_settings` (`settings.py:347-351`) writes `json.dumps(asdict(settings))`, and `asdict` keeps a `frozenset` as a `frozenset`, which `json.dumps` rejects, so every settings save would have needed a special case. And a downgraded build drops unknown `settings.json` keys on its next unrelated save, so a hand-edited theme would be silently lost and come back as defaults after an upgrade. A separate file is never touched by older builds, and `settings.py` does not change at all.

`customization.json` gains one optional per-account key, `cat_home`, a spot name, defaulting to `inside`. `Customization` gets the matching field and `load_customization` validates it like the other fields (an unknown spot falls back to `inside`). Every existing key and the loader's behaviour for them stay as they are, so existing files load unchanged and simply have every cat at home inside. A downgraded build drops `cat_home` on its next customization save and the cat goes back inside, which costs nothing worth protecting with a separate file.

`load_theme(state_dir) -> Theme` and `save_theme(state_dir, theme)` follow `customize.py`'s pattern: a missing or unparseable file gives the default theme, and saves are atomic through a temp file and `os.replace` (`customize.py:487-492`).

## The model

All values are logical pixels at 96 dpi, the unit `ui.py` already uses (`dpi.py:24`). Every default equals what the Tk widget draws today, with the line it comes from, so the first Qt build looks like the current widget before anyone changes a setting.

```python
@dataclass(frozen=True)
class CardStyle:
    background: str = "#1c1c22"     # ui.py:39 BG_COLOR
    opacity: int = 100              # percent, 0..100 in steps of 10
    radius: int = 0                 # 0..16
    border_width: int = 0           # 0..3
    border_color: str = "#333340"

@dataclass(frozen=True)
class BarStyle:
    layout: str = "beside_cat"      # "beside_cat" | "full_width"
    shape: str = "flat"             # "flat" | "rounded"
    height: int = 8                 # ui.py:38 BAR_HEIGHT, 4..10
    track: str = "#333340"          # ui.py:42 BAR_BG
    low: str = ...                  # display.py:13-15 GREEN/AMBER/RED
    mid: str = ...
    high: str = ...
    neutral: str = "#5a7fa8"        # ui.py:65 NEUTRAL_BAR
    mid_at: int = 50                # display.py:18-23 thresholds
    high_at: int = 80

@dataclass(frozen=True)
class TextStyle:
    foreground: str = "#f0f0f0"     # ui.py:40 FG_COLOR
    dim: str = "#8a8a92"            # ui.py:41 DIM_COLOR
    show: frozenset = frozenset(TEXT_ELEMENTS)
    halo: str = "off"               # "off" | "auto" | "on"
    halo_below: int = 60            # "auto" turns the halo on below this opacity

@dataclass(frozen=True)
class Theme:
    card: CardStyle
    bars: BarStyle
    text: TextStyle
```

Card width and height are constants (`CARD_WIDTH = 300`, `CARD_HEIGHT = 128`, from `ui.py:33-34`), not theme fields. The issue does not ask for card size, and a resizable card is the main way a layout ends up with the stats column or the status line outside the card.

The permission accent colours (`ACCENT_BG` and `ACCENT_FG`, `ui.py:47-48`) stay constants in `theme.py`, not theme fields. The accent exists to be noticed, so the user does not get to theme it away.

Fonts stay as the Tk build has them, as constants: 9pt bold for "SESSION" and "WEEK", 8pt bold for the per-model names (`ui.py:327`), 8pt regular for everything else. The family is left to the painter (Segoe UI on Windows today, the platform default elsewhere).

### Opacity

`CardStyle.opacity` takes 0 to 100 in steps of 10. The Tk build's `LEVELS` (`transparency.py:29`, 100 down to 50) exist because a colour-keyed Tk card is click-through and unrecoverable at 0; decision 2 removes that reason in Qt.

`card_alpha(opacity) -> int` returns `max(1, round(opacity * 255 / 100))`. The `max(1, ...)` is decision 2, as one tested line, so the painter cannot get it wrong.

Migration: when `theme.json` does not exist, `load_theme` seeds `CardStyle.opacity` from the `opacity` in `settings.json` if it is one of the Tk levels, else 100. Once `theme.json` exists it is the only source; the Tk build keeps reading its own key.

The accent forces opacity to 100 for the accented pane only. That is a deliberate change from Tk, where opacity is a property of the whole toplevel and one accented pane makes every pane opaque (`transparency.py:53-62`, applied window-wide at `ui.py:768`). Qt paints each card's alpha itself, so the restriction goes away.

### Text

`TEXT_ELEMENTS` is a closed set covering every piece of text the card draws today:

- `section_labels`: "SESSION" and "WEEK" (`ui.py:292,299`)
- `percent`: the "42%" at the start of each reset line
- `reset`: the countdown or reset time after it (`ui.py:489,496` draw `f"{pct:.0f}% · {reset}"` as one label; the model splits it into two runs on the same row)
- `status`: credits or the error hint (`ui.py:313-317`)
- `account_label`: the dim top-right name (`ui.py:342-345`)
- `model_rows`: the per-model names and values (`ui.py:323-336`), shown only in the `"models"` view
- `cat_tags`: the driving and tool tags on the cat area (`ui.py:525-548`)

Splitting `percent` from `reset` is for the halo. Nick's verdict from the Mac run (findings, gate 5): the halo is needed for "SESSION", "WEEK" and the percentages at low opacity, and makes the small reset text harder to read. So `halo_applies(element)` is true for `section_labels` and `percent` only, the two measured cases. Whether it helps `account_label`, `model_rows` or `cat_tags` is for #111 to try with screenshots. `halo_active(text, opacity)` resolves `"auto"` against `halo_below`. The default is `"off"`, which is what Tk draws; the 60 threshold only matters once someone picks `"auto"`, and is a placeholder until #111.

Hiding an element removes it from the layout output instead of leaving a hole. Hiding `section_labels` moves each bar up to where its label was.

### Bars

`shape="rounded"` means a pill, radius `height / 2`, for track and fill alike.

`bar_fill(theme, ramp_pct, override) -> str` replaces both `display.bar_color` and the per-model branch at `ui.py:396-403`, in the same order: the account's `bar_fill`, if set, wins; otherwise the threshold ramp applies when `ramp_pct` is not `None`; otherwise `neutral`. The limits view passes its own percentage as `ramp_pct`. The models view passes the budget percentage, or `None` for share-of-total, which has no cap to be near.

Layouts:

- `beside_cat` is today's card: a stats column at x=132 (`ui.py:36`), 158 wide (`ui.py:37`), with the cat area to its left.
- `full_width` runs the bars and text from x=8 to x=292 and has no cat area. It only makes sense when the cat lives outside the card, so it is honoured only for a pane whose resolved home spot (see "Several accounts") is not `inside`. A pane that falls back to `inside` uses `beside_cat`, so its text never runs under its cat. Wandering does not change the layout: it keys on the resolved home spot, not on where the cat happens to be standing, and a pane using `full_width` never offers `inside` to #22.

## Layout

`layout_card(theme, view, pane_spot) -> CardLayout` is a pure function returning named rectangles in card coordinates. `view` is `"limits"` or `"models"`, the keys `settings.py:23` already persists. For `"limits"`: `cat_area` (absent under `full_width`), `session_label`, `session_bar`, a `session_line` with separate `percent` and `reset` runs, the same three for `weekly`, `status`, `account_label`. For `"models"`: the three rows from `MODEL_ROW_Y` (`ui.py:60-61`), each with name, value and bar, plus `status` and `account_label`.

Rows are stacked rather than placed at fixed y: each row's y is the previous row's bottom plus a fixed gap, with the gaps chosen so the default bar height of 8 reproduces today's coordinates exactly (`ui.py:286-345`). A taller bar pushes the rows below it down instead of overlapping them. The bar height range of 4 to 10 is provisional: two bars at 10 push the status line down 4 px from y=108, which should still fit, but the test below sets the real upper limit and the range shrinks if it fails. That test walks every allowed combination of layout, bar height, view and shown elements, and asserts every rectangle lies inside the card and no two text or bar rectangles overlap.

Tests also pin the default output to today's coordinates for both views, so a default Qt card lands every element where the Tk card has it.

## Cat spots

The sprite is 28 by 26 cells at SCALE 4 (`sprites.py:23`), 112 by 104 logical px. Every frame is that size, which Codex confirmed across the whole sprite set.

A `Spot` names a place for the cat relative to one card, using the spike's vocabulary (spike brief, line 34). Origins are the sprite's top-left in card coordinates:

- `inside`: (8, 12). The cat canvas sits at (8, 8) and is 112 square (`ui.py:287-290`); `sprite_bounds` centres the 104-high sprite in it with a 4 px y offset (`ui.py:124-140`). The first draft said (8, 8), which is the canvas, not the sprite.
- `on_top`: (8, -104). Same x as `inside`, so stepping out of the card is a straight move up; feet on the card's top edge.
- `beside_left`: (-112, 24). Outside the left edge, flush, bottom-aligned with the card.
- `beside_right`: (300, 24). Outside the right edge, flush, bottom-aligned with the card.

The side spots are flush with the card edge rather than gapped. The sitting frames already leave a column of empty cells at the sprite's edge (`sprites.py:241`), so a sitting cat shows a 4 px gap anyway, and the scratching frames from #116 can put the paws in the column that touches the card.

`placement(spot) -> Placement(origin, mirrored)`. A cat beside the card faces it: `beside_left` is drawn as the art is drawn, `beside_right` mirrored. That assumes the side-on art faces right, which is how #116 describes it ("left-facing frames come from mirroring"); if the first scratching frames come out facing the other way, the two flags swap and nothing else changes. `inside` and `on_top` are not mirrored; #22 mirrors a walking cat itself, as the spike did.

Scratching is a pose, not a spot. The model's part is only that a cat at a side spot touches the card and faces it. #116 draws the frames, and #22 decides when a cat at a side spot scratches. `SCRATCH_SPOTS = {beside_left, beside_right}` names where the pose is valid, so #22 does not hardcode it.

`top_edge_track(theme) -> Track` gives #22 the walkable segment: y = -104, x from 0 to `CARD_WIDTH - 112` = 188, in card coordinates. `on_top` lies on it. The track is not a `Spot` and cannot be a `cat_home`.

`reachable_spots(theme, cat_home, pane_index, pane_count) -> frozenset` is the set #22 may move a pane's cat between, after the multi-pane rule below and the `full_width` rule above.

### Several accounts

The spike had one card. Today panes sit in a grid with no gap (`ui.py:86-100`, `cols = ceil(N/4)`, `rows = ceil(N/cols)`, row-major, so the last row can be ragged). A cat above a second-row card would sit on the first-row card's content.

Rule: an outside spot is reachable only from a pane on the matching outer edge. `on_top` and the top-edge track are reachable from first-row panes. `beside_left` is reachable from the first column. `beside_right` is reachable from panes whose right neighbour is empty: the last column, plus the last pane of a ragged last row. A pane whose `cat_home` is unreachable resolves to `inside` (and so to `beside_cat`).

With up to 4 accounts the grid is one column, so every pane is in both the first and the last column, and each cat can use either side regardless of the others: one cat on the left of the top card, the next on the right of the card below, a third at home inside. Only `on_top` is limited to the top card. From 5 accounts the grid has two or more columns and the sides belong to the outer columns only.

The alternative, a gap between panes wide enough for a cat, makes a two-account widget about 1.5 times as large to hold space that is empty most of the time.

## Window geometry

`layout_window(homes, wander=False) -> WindowLayout` takes every pane's resolved home spot in pane order and is computed once per change of pane count or homes, never per frame, so the window never resizes while a cat wanders. It returns:

- `margins` (left, top, right, bottom): the transparent space needed around the whole grid so every spot any pane can reach fits, each rounded up to a multiple of 4 (findings, section 1). With only `inside` reachable all four are zero and the window is exactly the grid. With `on_top` reachable, top is 104; with `beside_left` or `beside_right`, that side is 112. The spike's 16/112/128/16 had extra padding on every side; the model uses the exact need.
- What "reachable" means for the margins is a choice between two window sizes. Sizing for the homes only gives the smallest window, but then #22 can only move a cat to an outside spot that some pane already calls home. Sizing for everything reachable gives #22 the whole range at the cost of a window up to 300 + 224 = 524 px wide for one column. The model takes a `wander` flag: off sizes for homes, on sizes for reachability. #22 turns it on.
- `size`: grid plus margins.
- `pane_origins`: each pane's top-left in window coordinates. A spot origin plus its pane's origin is the cat's window position.

`hit_region(window_layout, cat_rects) -> list of rects` is the union, in window coordinates, of every pane's card rectangle and every cat's current sprite rectangle. Empty cells of a ragged last row are not in it. Cards are included at every opacity because of decision 2. #111 turns this into the macOS `setMask()` on each cat move, and may refine the cat rectangles with `QImage.createAlphaMask()`. With a corner radius above 0, the few pixels just outside a rounded corner still catch clicks; on the Mac the spike's rectangle mask did this and Nick saw no problem, so the model does not try to round the region.

`snap(value, dpr) -> float` returns `round(value * dpr) / dpr`, the spike's rule that puts the cat's origin on a whole device pixel. `cell_size(dpr) -> int` is `round(SCALE * dpr)`, the frame cache key from the findings. Both live here so the rules are tested once.

## Combining theme and account

`resolve_pane_style(theme, customization, has_accent) -> PaneStyle` gives one pane its final colours and opacity:

- background: `ACCENT_BG` if accented, else the account's `card_bg` override, else `theme.card.background`
- foreground: `ACCENT_FG` if accented (matching `ui.py:432,480`), else `theme.text.foreground`; dim stays `theme.text.dim`
- opacity: 100 if accented, else `theme.card.opacity`
- the account's `bar_fill` override, passed through to `bar_fill`
- the sprite palette from the existing `customize.effective_palette`
- the resolved home spot: the account's `cat_home` if reachable for this pane, else `inside`

This is the only place theme and customization meet, so precedence is defined once.

## Loading and validation

Same contract as `load_settings` and `load_customization`: a hand-edited file degrades, it never crashes the widget.

Field level: each field is validated on its own, and an invalid value falls back to that field's default, leaving the rest of the theme intact. Colours must match `#rrggbb` (`customize.py:34`). Integers out of range are rejected rather than clamped, so a typo falls back to the default instead of to an extreme. Booleans are rejected where an integer is expected (`True` is an `int` in Python; `settings.py:104` already guards this for opacity). Opacity must be a multiple of 10. Enum strings outside their set fall back. `show` must be a list of strings; unknown names are dropped and the known ones kept. `cat_home` in `customization.json` must be one of the four spots. Unknown keys are dropped. A file whose top level is not an object gives the default theme with migrated opacity.

Relationship level, after the field pass: if `mid_at >= high_at`, both thresholds revert to 50 and 80. The geometry relationships Codex raised (card size, bar height against card height, the inside cat fitting the card) cannot go wrong any more, because card size is fixed and the bar height range is the one the layout test proves fits.

`save_theme` writes `show` as a sorted list, since `json.dumps` rejects a `frozenset`.

## Tests

All in `tests/test_theme.py`, none marked `gui`, so they run on Linux, macOS and Windows in the existing CI matrix (`ci.yml:18-23`), except the one noted.

- Defaults equal the Tk constants they replace. `ui.py` imports `tkinter`, so this one test is marked `gui` and runs in the Linux smoke job; it is deleted with the Tk code in #111.
- `card_alpha` at 0, 10 and 100, and the `max(1, ...)` floor.
- Opacity migration from each Tk level, a missing `settings.json`, a missing key, and a garbage value; and that an existing `theme.json` wins over `settings.json`.
- Field degradation: one bad field in an otherwise valid theme loses only that field; booleans as integers rejected; opacity not a multiple of 10 rejected; unknown `show` names dropped; unknown keys dropped; non-object top level. Inverted thresholds revert both.
- Save and load round trip, including `show`.
- `layout_card` defaults match today's coordinates for `"limits"` and `"models"`. The exhaustive walk over layout, bar height, view and shown elements: every rectangle inside the card, no overlaps. Hiding `section_labels` collapses the gap. `full_width` with an `inside` resolved spot falls back to `beside_cat`.
- `bar_fill` precedence: override, ramp, neutral when `ramp_pct` is `None`.
- Spot origins and mirroring for all four spots, the track's endpoints, `reachable_spots` for 1, 2, 3, 4, 5 and 8 panes including ragged last rows. With 2 to 4 panes, both sides reachable from every pane and `on_top` only from the first.
- `layout_window` margins for homes-only and `wander` sizing, all multiples of 4, zero for `inside` alone; a left home moves every pane origin right by 112; pane origins; `hit_region` leaving out empty grid cells.
- `cat_home` in `customization.json`: absent gives `inside`, unknown spot gives `inside`, a file without it loads exactly as before, and save and load round trip it. These go in `tests/test_customize.py`.
- `snap` at dpr 1, 1.25, 1.5, 1.75 and 2 lands on whole device pixels; `cell_size` gives 4, 5, 6, 7, 8.
- `resolve_pane_style` precedence for background, foreground and opacity: theme only, account override, accent over both.
- The import-isolation test described under "Where it lives".
- Existing tests keep passing with no change. `settings.py` is untouched, and `customize.py` only gains the optional `cat_home`.

## Open questions

1. When the cat's home is a side spot, should the window grow on that side (the card moves on screen by 112 px when a left home is first set), or should the card stay put and the window extend outward? The model returns pane origins either way; #111 decides whether to shift the window's screen position to compensate, and should, so the card does not jump.
2. Scratching needs its frames added to #116's scope (it lists lying on top, the jump, and walking, not scratching).

## Review (Codex, 2026-10-11)

gpt-6-sol at high effort, read-only, briefed cold. Every claim it made about the source was checked against the code before changing anything here; all held. What changed:

1. Storage moved from a nested `settings.json` key to `theme.json`: the `frozenset` would have broken `save_settings`, and a downgrade would have dropped the theme.
2. The view key is `"models"`, not `"per_model"`, and bars gained `neutral` and a `ramp_pct` argument so share-of-total bars keep their colour.
3. Hit testing became a window-level union in window coordinates that leaves out empty grid cells, computed from new pane origins.
4. Card size became fixed, rows stack, bar height is capped at 10, and `full_width` is honoured only for panes whose resolved home is outside the card.
5. Spot origins are fully specified, the top-edge track has endpoints, margins are computed once from reachable spots and use the exact need rather than the spike's padding.
6. Thresholds are checked against each other, and `show` and `cat_home` have explicit validation.
7. The accent foreground, per-model text and cat tags are covered.

Factual corrections taken: the Tk accent is window-wide (the per-pane Qt behaviour is now called out as a change), the default sprite origin is (8, 12), per-model names are 8pt bold, and the halo is limited to the two measured cases with `"off"` as the default. Scope cut on Codex's advice: input handling. Kept against its advice: the one `gui`-marked defaults test, because it is the only check that the model's defaults really are today's widget.
