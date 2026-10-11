# Plan: widget theme and layout model (#110)

Spec: `docs/superpowers/specs/2026-10-11-theme-layout-model-design.md`. Branch `theme-model-110`, worktree `.worktrees/theme-model`. Read the spec before each slice; this plan only orders the work and pins the details the spec leaves to implementation.

Validation for every slice: `/usr/bin/python3 -m pytest <targeted files> -q`, `xvfb-run -a /usr/bin/python3 -m pytest -m gui tests/test_theme.py -q` where gui tests exist, `ruff check tokitty tests`. Full suite runs in the main session after each slice lands.

Constraints for every slice:

- `tokitty/theme.py` imports only the standard library and `tokitty.sprites`. No `tkinter`, no `PySide6`, no `tokitty.ui`.
- Nothing in `ui.py`, `__main__.py`, `settings.py` or any Tk module changes. The Tk widget does not consume the model (spec, decision 3).
- Comment density matches `customize.py`: a module docstring, short why-comments, no restating the code.

The slices all write `tokitty/theme.py`, so they run one after another, not in parallel.

## Slice A: data model and persistence (no dependencies)

Files: new `tokitty/theme.py`, `tokitty/customize.py`, new `tests/test_theme.py`, `tests/test_customize.py`.

1. Frozen dataclasses `CardStyle`, `BarStyle`, `TextStyle`, `Theme` with the spec's fields and defaults (nested defaults via `field(default_factory=...)`). Constants: `CARD_WIDTH = 300`, `CARD_HEIGHT = 128`, `ACCENT_BG`, `ACCENT_FG`, font sizes, `TEXT_ELEMENTS`, `HALO_ELEMENTS = {"section_labels", "percent"}`, `SPOTS = ("inside", "on_top", "beside_left", "beside_right")`, `VIEWS = ("limits", "models")`. Colour defaults copied from `ui.py:39-65` and `display.py:13-15` as literals, not imported.
2. `THEME_FILENAME = "theme.json"`, `load_theme(state_dir) -> Theme`, `save_theme(state_dir, theme)`. Load: missing file means migrate opacity from `settings.json` (read the raw JSON key directly, do not import `settings.py`; accept only the Tk levels 100, 90, 80, 70, 60, 50, else 100). Unparseable file or non-object top level gives defaults plus migrated opacity. Field-level validation exactly as the spec's "Loading and validation" section, then the relationship pass (`mid_at >= high_at` reverts both). Save: atomic temp file plus `os.replace`, `show` written as a sorted list.
3. `card_alpha(opacity)`, `halo_applies(element)`, `halo_active(text_style, opacity)`, `bar_fill(theme, pct, ramp_pct, override)` with the precedence from the spec (override, then ramp when `ramp_pct is not None`, then `neutral`).
4. `customize.py`: add `cat_home: str = "inside"` as the last `Customization` field. `load_customization` keeps it only if it is a string in the four spot names, else `"inside"`. Import the spot names from `theme.py` only if that creates no cycle (`theme.py` must not import `customize.py` at module level; if `resolve_pane_style` in slice C needs `effective_palette`, import it inside the function or pass the palette in). Otherwise define the tuple once in `theme.py` and import it into `customize.py`.
5. Tests from the spec's Tests section covering this slice: defaults, `card_alpha`, migration (each Tk level, missing `settings.json`, missing key, garbage, existing `theme.json` wins), every degradation case, round trip, `bar_fill`, the import-isolation test (run `import tokitty.theme` in a subprocess with `sys.modules["tkinter"] = None` and `sys.modules["PySide6"] = None`), and the `cat_home` cases in `test_customize.py`.
6. The one `gui`-marked test: import `tokitty.ui` and `tokitty.display` and assert each theme default equals the Tk constant it was copied from.

## Slice B: card layout (after A)

Files: `tokitty/theme.py`, `tests/test_theme.py`.

1. `Rect` (frozen: x, y, w, h, with `right`, `bottom`, `intersects`, `contains`) and `CardLayout` (a mapping of element name to `Rect`, plus the `percent` and `reset` runs of each line).
2. `layout_card(theme, view, pane_spot) -> CardLayout`. Column: `beside_cat` is x=132, w=158; `full_width` is x=8, w=284 and only when `pane_spot != "inside"`, else fall back to `beside_cat`. `cat_area` is (8, 8, 112, 112) under `beside_cat` and absent under `full_width`.
3. Stacking for `"limits"`, derived from today's coordinates (`ui.py:292-318`) so the defaults reproduce them: section label top, then bar top 18 below it, then the line `bar_height + 4` below the bar top, then the next section label 18 below the line, status 18 below the weekly line. At bar height 8 that is 12, 30, 42, 60, 78, 90, 108. Hidden `section_labels` remove their 18 px step. Text rects use nominal heights (`LABEL_H = 16` for 9pt, `SMALL_H = 14` for 8pt) and the column width.
4. `"models"`: the three rows from `ui.py:60-61` (`MODEL_ROW_Y = (20, 48, 76)`, bar at `+14`), name on the left and value right-aligned at the column's right edge, re-stacked the same way when bar height changes. `status` and `account_label` as in limits.
5. `account_label`: anchored top-right at (294, 4), nominal width 100. It is drawn over the corner today, so the overlap test exempts it.
6. Tests: default coordinates for both views equal today's; the exhaustive walk (layout x bar height 4..10 x view x every subset of hideable elements) asserts every rect is inside the card and no bar or text rect overlaps another, except `account_label`. If some heights fail, lower the upper bound in `BarStyle` validation and tell the main session the new limit, because the spec states 10 as provisional.

## Slice C: spots, window geometry, pane style (after B)

Files: `tokitty/theme.py`, `tests/test_theme.py`.

1. `SPRITE_W = 112`, `SPRITE_H = 104` from `sprites.SCALE` and the template size (derive, do not hardcode 28 and 26 if `sprites` exposes them; check).
2. `Placement(origin, mirrored)` and `placement(spot)`: inside (8, 12) not mirrored, on_top (8, -104) not mirrored, beside_left (-112, 24) not mirrored, beside_right (300, 24) mirrored. `SCRATCH_SPOTS`. `top_edge_track() -> Track(y=-104, x_min=0, x_max=188)`.
3. `grid_shape(pane_count) -> (cols, rows)` copied from `ui.py:91-100`'s rule (row-major, `cols = ceil(N/4)`). `reachable_spots(theme, pane_index, pane_count)` per the spec's "Several accounts" rule, including the ragged last row, and with `inside` removed when the theme's bar layout is `full_width` and the pane's resolved home is outside. `resolve_home(cat_home, pane_index, pane_count)` falls back to `inside` when unreachable.
4. `layout_window(theme, homes, wander=False) -> WindowLayout(margins, size, pane_origins)`. Margins from homes only, or from every reachable spot when `wander`. Every margin a multiple of 4. Pane origin = margin offset plus grid position at `CARD_WIDTH` by `CARD_HEIGHT`.
5. `hit_region(window_layout, cat_rects) -> list[Rect]` in window coordinates: one rect per occupied pane plus the given cat rects. Empty grid cells never included.
6. `snap(value, dpr)` and `cell_size(dpr)`.
7. `resolve_pane_style(theme, customization, has_accent, pane_index, pane_count) -> PaneStyle` per the spec's "Combining theme and account" list, including the resolved home spot.
8. Tests from the spec's Tests section for all of the above, including 1, 2, 3, 4, 5 and 8 panes, a left home shifting every pane origin by 112, snapping at dpr 1, 1.25, 1.5, 1.75 and 2.

## After the slices

Main session: review each diff, run the full suite (`/usr/bin/python3 -m pytest -q` and the xvfb gui run), check the module still imports with the GUI toolkits blocked, and confirm the spec still matches what was built (bar height limit especially). Then ask Nick about shipping. No manual gate: nothing user-visible changes until #111.
