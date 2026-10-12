# Plan: Qt main widget (#111)

Spec: `docs/superpowers/specs/2026-10-11-qt-main-widget-design.md`. Branch `qt-widget-111`, worktree `.worktrees/qt-widget`. Read the spec before each slice; this plan orders the work and pins the details the spec leaves to implementation. Spike reference code: `git show spike/pyside6:spike/qt_card.py` (read it, never copy the file or merge the branch).

## Setup and validation

Local Qt runs use a venv layered on the system Python, so pytest and the repo's other dependencies come from there: `/usr/bin/python3 -m venv --system-site-packages /tmp/tokitty-qt-venv && /tmp/tokitty-qt-venv/bin/pip install "PySide6-Essentials==6.12.0"`. It lives in `/tmp` because the sandbox cannot write to the home directory, so recreate it after a WSL restart. Tests run from the worktree import the worktree's `tokitty`.

- Headless: `/usr/bin/python3 -m pytest -q` (whole suite, under a minute).
- Tk gui: `xvfb-run -a /usr/bin/python3 -m pytest -m gui -q`.
- Qt: `QT_QPA_PLATFORM=offscreen /tmp/tokitty-qt-venv/bin/python -m pytest -m qt -q`.
- Lint: `ruff check tokitty tests`.

Constraints for every slice:

- The Tk path's behaviour does not change. `ui.py`, `transparency.py`, `dpi.py`, `tray.py`, `menu.py`, `theme.py` and every Tk window module stay as they are. `__main__.py` changes only in slice 1 (the extraction) and slice 5 (the flag in `main()`).
- Nothing under `tokitty/qt/` is imported by the Tk path, and nothing outside `tokitty/qt/` imports PySide6. `app_core.py` imports neither toolkit; add it to the import-isolation pattern `tests/test_theme.py` uses.
- Comment density matches the surrounding modules: a module docstring, short why-comments, nothing restating the code. No em-dashes in code, comments or docstrings.

Order: slices 1 and 2 touch disjoint files and run in parallel. 3 needs 2. 4 needs 3 (both edit `qt/widget.py`). 5 needs all four.

## Slice 1: shared core (no dependencies)

Files: new `tokitty/app_core.py`, `tokitty/__main__.py`, `tests/test_main.py`, new `tests/test_app_core.py`.

1. `build_units(...)`: move the provider pass and unit loop (`__main__.py:769-824`) here, without `window.panes`. Closure captures become parameters (`state_dir`, `accounts`, `customization_store`, `wsl_credentials`, `distro_probe`, `usage_state`). Each unit dict keeps every key it has today except `"pane"`, and gains `"custom"` and `"label"`. The final `save_customization` stays inside it. In `run_gui`, immediately after the call and before any callback is defined, set `unit["pane"] = window.panes[i]` and apply the customization and label exactly as lines 809-810 do now.
2. `make_onboarder(state_dir, usage_state, on_changed) -> Callable[[unit, latest, breakdown], None]`: `maybe_onboard` (`__main__.py:1098-1118`) with `tray.refresh` replaced by `on_changed`. `run_gui` passes `tray.refresh`.
3. `step_unit(unit, *, usage_state, notifier, customization_store, notes_removed, onboard, now) -> Optional[PaneUpdate]`: the loop body of `tick` (`__main__.py:1254-1282`) in the same order, returning `PaneUpdate(kind, kwargs)` instead of calling `render`/`render_usage`, and `None` where the loop `continue`s today. `last_good`, `burn` and `deck_usage` are still updated on the unit. `tick` becomes: for each unit, `update = step_unit(...)`, then `getattr(unit["pane"], "render" if update.kind == "limits" else "render_usage")(**update.kwargs)` when not `None`. `PaneUpdate` is a frozen dataclass in `app_core.py`.
4. `Discovery`: `run_discovery`, `discovery_lock` and `discovery_result` (`__main__.py:594-721`) become a class with `start()` (starts the daemon thread, honouring the debug exclusions at line 720), `take_hook_warnings() -> list`, and accessors for whatever else `tick` and `open_accounts` read from `discovery_result` today (`wsl_matches`). Keep the long comment about never touching the toolkit from the thread, reworded to cover both toolkits.
5. The helpers `step_unit` needs (`_display_state_for`, `_projection_text_for`, `_next_last_good`, `_usage_render_args`, `publish_usage_notes`, `publish_ntfy`, `resolve_pose`) may stay in `__main__.py` if `app_core` can import them without a cycle; otherwise move them and re-export the names from `__main__` so `tests/test_main.py`'s imports at its top keep working.
6. Tests that monkeypatch a name on `tokitty.__main__` whose user has moved stop patching anything, silently. At minimum `main_module.save_customization` and `main_module.retry_pending_hook_op` (grep `monkeypatch.setattr(main_module` and the string forms). Retarget each to the module the code now looks the name up in, and for each one confirm the test still fails when the patched behaviour is broken (temporarily break it, run, restore).
7. `tests/test_app_core.py`: the parity tests from the spec. Keep a verbatim copy of the pre-refactor loop body as a test helper that renders into a recording fake pane, run it and `step_unit` from identical deep-copied units and inputs, and assert equal render calls, equal unit state (`last_good`, `burn` contents, `deck_usage`), and the same onboard, note and ntfy calls in the same order. Cases: limits ok, models view, stale token, error with `last_good`, accented pose, `latest is None`. Also `build_units` for no accounts, one, and two with different providers, against the units and `customization.json` the old code produced.

Done when the whole headless suite and the xvfb gui suite pass with no test deleted or weakened, and `git diff main -- tokitty/__main__.py` shows only moves and call sites.

## Slice 2: pane, painting, CI (no dependencies)

Files: new `tokitty/qt/__init__.py`, `tokitty/qt/pane.py`, `tokitty/qt/widget.py`, `pyproject.toml`, `.github/workflows/ci.yml`, new `tests/qt/test_widget.py` (with a `conftest.py` that does `pytest.importorskip("PySide6")` and provides one session-wide `QApplication`).

1. `pyproject.toml`: extra `qt = ["PySide6-Essentials==6.12.0"]`; register the `qt` marker; `addopts = "-m 'not gui and not qt'"`. Mark every test in `tests/qt/` `qt` (module-level `pytestmark`).
2. `ci.yml`: a `qt` job, matrix `ubuntu-latest`, `macos-latest`, `windows-latest`, Python 3.14, `pip install -e ".[dev,qt]"`, `QT_QPA_PLATFORM=offscreen pytest -m qt`. On Linux, add `sudo apt-get install -y libegl1 libxkbcommon0 libfontconfig1` before the tests; adjust to whatever the first run reports missing.
3. `QtPane` (`qt/pane.py`): pure state, no painting. Same public surface as `ui.Pane`: `render(...)` and `render_usage(...)` with `ui.py`'s exact signatures and defaults, `set_appearance(palette, card_bg, bar_fill, label, colorway, pattern)` with None meaning unchanged, `draw_next_frame()` (cycles `sprites.get_frames(state)` like `ui.py:505-509`), attributes `palette`, `_label`, `_colorway`, `_pattern`, `_accent`, `on_accent_changed`. Holds a `Customization` for `resolve_pane_style`. Every state change calls an `on_change` callback the widget sets.
4. `TokittyQtWidget` (`qt/widget.py`): constructor `(theme, customizations, pane_count, *, parent=None)`. Flags and attributes exactly as the spec's "Window" section, including `WA_MacAlwaysShowToolWindow`. Geometry from `theme.layout_window` with each pane's `theme.resolve_home`. `paintEvent` per the spec's "Painting" list. Fonts: Segoe UI on Windows, default family elsewhere, sizes from `theme.py` constants set with `setPointSize`, since they are points as in Tk. Cat image cache keyed by (state, frame index, palette signature, cell size, mirrored), cleared on `QEvent.DevicePixelRatioChange` and on `windowHandle().screenChanged`. Tags at the cat canvas origin (card 8, 8) plus `ui.py:525-529`'s offsets: driving tag bottom-left at (6, 112 - 6) in the dim colour, tool tag top-left at (6, 6) in the foreground colour, 8pt, `ui.fit_tag` logic copied (do not import `tokitty.ui`, it imports tkinter). An 800 ms `QTimer` calls `draw_next_frame` on each pane. `rebuild_layout(homes)` recomputes geometry and moves the window by the margin change (spec, "Window", last paragraph).
5. Tests from the spec's Tests section for the window, the cat (spike method, including the `QT_SCALE_FACTOR=2` subprocess), the card alpha and accent, and repaint on `render`.

## Slice 3: input and position (after 2)

Files: new `tokitty/qt/menu.py`, new `tokitty/qt/position.py`, `tokitty/qt/widget.py`, new `tests/qt/test_input.py`, new `tests/test_qt_position.py` (unmarked: the conversion functions take plain data, no Qt).

1. `qt/menu.py`: `render_menu(qmenu, items, after_action)`, a direct counterpart of `ui._render_tk_menu` (`ui.py:916-946`) over `menu.MenuItem`, using a `QActionGroup` per radio group.
2. Widget: right press, or on macOS left press with `MetaModifier`, rebuilds the menu from a `build_menu_model(pane_index)` callable the owner supplies and pops it at the cursor; pane index from pane origins, outside every card means 0. Left press, move, release drag with `globalPosition()`; release calls `save_position`. `set_always_on_top(on)` does flag change, `show()` without activation, then `_after_native_window()` (an empty hook here; slice 4 fills it).
3. `qt/position.py`, pure: `ScreenInfo(native_rect, logical_rect, ratio)`; `from_tk(x, y, screens, system_ratio, platform) -> (x, y)` per the spec's "Position" paragraph (macOS identity; otherwise find the screen whose native rect contains the point, convert relative to it; no containing screen returns the point unchanged and lets clamping handle it); `load(path, ...)`, `save(path, x, y)` writing `"unit": "logical"` atomically; `place(first_card, grid_size, margins, area)` returning the window position after clamping the grid with `geometry.clamp_to_area`, default from `geometry.default_position`. The widget builds `ScreenInfo` from `QGuiApplication.screens()` (native rect = `geometry()` top-left plus size times `devicePixelRatio()`).
4. Tests from the spec for the menu, drag, and every position case including both mixed-DPI orders.

## Slice 4: macOS helper and mask (after 3)

Files: new `tokitty/qt/macos.py`, `tokitty/qt/widget.py`, new `tests/test_qt_macos.py` (unmarked, fake objc layer), additions to `tests/qt/test_input.py`.

1. `macos.py` per the spec's "macOS helper" section. Separate `objc_msgSend` prototypes per call shape (`id(id, SEL)`, `bool(id, SEL, SEL)`, `void(id, SEL, bool)`, `void(id, SEL, unsigned long)`), each via `ctypes.cast` of the one symbol. The objc layer is injectable so tests replace it. `apply(win_id)` returns which steps succeeded; it is a no-op unless `sys.platform == "darwin"` and `QGuiApplication.platformName() == "cocoa"` (checked by the caller in the widget, so `macos.py` itself needs no Qt import).
2. Widget `_after_native_window()`: `macos.apply(int(self.winId()))` on macOS, then `_update_mask()`. Call it after the first `show()` and from `set_always_on_top`. `_update_mask()` sets `QRegion` from `theme.hit_region(window_layout, cat_rects)` on every platform, called also from `rebuild_layout`.
3. Tests: the fake-objc cases from the spec; under offscreen, the mask region equals the cards plus cat rects, and excludes an empty ragged-row cell.

## Slice 5: run_qt and the flag (after 1 to 4)

Files: new `tokitty/qt/app.py`, `tokitty/__main__.py` (`main()` only), new `tests/test_qt_launch.py` (unmarked parts), `tests/qt/test_app.py`.

1. `main()`: when `TOKITTY_QT == "1"` and neither `--after-update` nor `--apply-update` is present, return `run_qt()`; import it lazily.
2. `run_qt()`: PySide6 import check with the spec's exact message and return 1; lock exactly as `run_gui` (`__main__.py:452-466`, without the update branches); `first_run.classify(excluded=bool(debug))` result ignored; accounts, settings, `usage_state` as in `run_gui`; `theme.load_theme`; `QApplication`; widget; `build_units`, attach `QtPane`s; `Discovery` started; `make_onboarder` with a no-op `on_changed`; a `QTimer` at `UI_REFRESH_MS` running `step_unit` for each unit plus hook warnings via a deferred `QMessageBox.warning`; debug states rendered as `run_gui` does (`__main__.py:723-735`); menu model from `menu.build_menu` with refresh (the `refresh_all` logic), view modes writing `usage_state` and settings as `run_gui`'s handler does, always in front, quit. Shutdown in a `finally` mirroring `__main__.py:1331-1340` minus tray, deck and update host.
3. Tests: missing PySide6 (patch the import), `main()` routing with and without the update arguments, a fresh state dir ends with `first_run == "pending"`, and under `qt`: a debug-state launch builds one and two panes and quits from a timer.

## After the slices

Main session: review each diff against the spec; run all four validation commands; the manual checks from the spec on Cucumber (100% and real 150%, from source, `TOKITTY_QT=1`, `TOKITTY_DEBUG_STATE` for the side-by-side screenshots), and confirm the window's `SessionId` matches `explorer.exe` when launched from WSL. Then Nick's Mac checklist (focus with Terminal while dragging, Spaces, full screen, click-through on the margin with a side home). Ask Nick before pushing.
