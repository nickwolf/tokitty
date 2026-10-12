# Qt main widget (design)

Issue: [#111](https://github.com/nickwolf/tokitty/issues/111), milestone v0.7.0 "Qt widget". Builds on the theme and layout model from [#110](https://github.com/nickwolf/tokitty/issues/110) (`tokitty/theme.py`, spec `2026-10-11-theme-layout-model-design.md`) and the go decision from the [#109](https://github.com/nickwolf/tokitty/issues/109) spike (`2026-10-10-pyside6-spike-findings.md`, cited below as "findings"). Siblings that stay out of this issue: #112 (settings, accounts, first-run), #113 (updater UI), #114 (tray), #115 (packaging), #116 (new poses), #117 (release gate), #128 (reference-image CI), #22 (wandering).

Status: draft, 2026-10-11. Two decisions taken by Nick the same day, listed below. A Codex review (gpt-6-sol, high) the same day found ten defects and three factual errors; all are folded in, and the review section at the end lists what changed.

## Goal

The main widget drawn by Qt: one frameless, translucent, always-on-top window holding every pane, with the card, bars, text and cat painted by `QPainter` from the #110 model, and the cat blitted from `sprite_raster.raster_rgba` so it is the same pixels the spike proved. Right-click menu, drag and position restore behave as they do in the Tk widget. On Windows it replaces the two-window colour-key arrangement from #37. On macOS it gets the three fixes the spike found it needs.

## Decided (Nick, 2026-10-11)

1. **The Qt widget lives behind a flag until the whole port is done.** The Tk widget stays the default and keeps working unchanged. #111 adds a separate Qt entry point that runs its own event loop and never creates a Tk root, so one process never hosts both toolkits. Features that are still Tk windows (settings, accounts, first run, updater, tray, Stream Dock in-window views) are simply absent in Qt mode until their own issue brings them over. A last step in v0.7.0 makes Qt the default and deletes the Tk code (see Open questions).
2. **The macOS native helper is in #111, all three parts, through `ctypes`.** `setMask()` for click-through, `_setPreventsActivation:` for focus (a private AppKit method, guarded by `respondsToSelector:`), and `collectionBehavior` for Spaces and full screen. No `pyobjc`.

Why the flag rather than replacing the widget in place: today one `tk.Tk()` (`__main__.py:517`) and one `mainloop` (`__main__.py:1330`) carry everything. The poll `tick` (`__main__.py:1231-1291`), the walkthrough (`__main__.py:527-545`), Settings (`__main__.py:1017-1019`), accounts (`__main__.py:899`), the update UI (`__main__.py:1167-1187`), the Stream Dock windows (`__main__.py:1128-1157`), every `messagebox`, and pystray's marshalling back to the UI thread (`tray.py:102`) all hang off that root. Replacing only the widget would leave Tk and Qt in one process. On Windows that can be made to work by pumping Tk from a `QTimer`, though a modal Tk dialog would still freeze the cat while it is open. On macOS both toolkits want to own `NSApplication` on the main thread, and nobody here can test that combination before it ships.

## Scope and non-goals

In scope:

- A toolkit-free core pulled out of `run_gui`, used by both entry points, so the Qt path does not copy the polling and display logic.
- `tokitty/qt/`: the widget, the pane, the menu renderer, position handling, the macOS helper, and `run_qt`.
- The flag, an optional `qt` dependency extra, a `qt` pytest marker, and a CI job that runs the Qt tests on Windows, macOS and Linux.

Not in scope: anything listed under "Absent in Qt mode", any change to how the Tk widget looks or behaves, deleting Tk code or `transparency.py`, packaging (`freeze/`, `frozen.py`, the LGPL notices are #115), reference-image tests (#128), walking or any cat movement between spots (#22), new frames (#116), opacity on the mouse wheel. The spike had a wheel. The Tk widget has none (opacity is a Settings control, `ui.py` binds no wheel event), so parity means none, and #112 can add one when it builds the opacity editor.

## Launch

`TOKITTY_QT=1` in the environment makes `main()` call `run_qt` instead of `run_gui`. An environment variable rather than a command-line flag follows `TOKITTY_DEBUG_STATE`, and keeps the flag out of the autostart command and the update hand-off, which pass their own arguments. If `PySide6` does not import, `run_qt` prints `tokitty: TOKITTY_QT is set but PySide6 is not installed (pip install -e ".[qt]")` and returns 1. It does not fall back to Tk, because a silent fallback makes a broken Qt setup look like a working one.

Qt mode ignores `--after-update` and `--apply-update`: with either present, `main()` takes the Tk path, since the update hand-off is #113's and has to keep working for real users during the whole port. Frozen builds do not contain PySide6 until #115, so Qt mode is source-only for now.

`pyproject.toml` gains an optional extra, `qt = ["PySide6-Essentials==6.12.0"]`, the version the spike measured. 6.12.0 declares `requires_python >=3.10,<3.16` and ships wheels for Windows x64, macOS 14 and later (universal2) and manylinux x86_64, which covers the CI matrix's 3.10 and 3.14. It stays an extra until #115 moves it into `dependencies`.

## The shared core

`run_gui` is one 897-line function (`__main__.py:446-1342`). The parts the Qt path needs and that touch no toolkit move into `tokitty/app_core.py`, and `run_gui` calls them in place of the inline code. The Tk path's behaviour does not change; the existing tests are what prove that.

- `build_units(...)`: the provider pass and the unit loop (`__main__.py:769-824`) without the pane. It returns the units with their pollers, watchers, usage watchers, customization and initial label, and saves `customization.json` as it does today. Each caller then sets `unit["pane"]` to its own pane and applies the customization and label (`__main__.py:809-810`) before wiring any callback, because the Tk callbacks read `unit["pane"]` (customization changes at `__main__.py:837`, onboarding at `__main__.py:1098`, the Stream Dock anchor at `__main__.py:1120-1122`).
- `step_unit(unit, usage_state, notifier, customization_store, notes_removed, now) -> Optional[PaneUpdate]`: the body of the loop in `tick` (`__main__.py:1254-1282`), from `get_latest()` to `last_good`, without the final `render` call. It returns `PaneUpdate(kind, kwargs)`, where `kind` is `"limits"` or `"models"` and `kwargs` are exactly the keyword arguments `Pane.render` or `Pane.render_usage` receive today. `maybe_onboard` moves with it and takes the tray refresh as a callback, since Qt mode has no tray.
- `run_discovery` (`__main__.py:606-718`) and its `discovery_lock`/`discovery_result` pair become a small class with a `start()` and a `take_hook_warnings()`. Both paths start it and poll it from their own tick. The rule in the comment at `__main__.py:594-601` (the thread never calls into the UI toolkit) is unchanged and now applies to both toolkits. Qt shows the warnings as Tk does (`__main__.py:1234-1252`): one `QMessageBox.warning`, deferred with `QTimer.singleShot(0, ...)` so the modal box never holds up the tick.

`tick` in `run_gui` keeps everything Tk-specific (the update controller, the Stream Dock, `root.after`) and calls `step_unit` for each unit. The parity tests cover more than the returned arguments. For the same inputs, the old loop body (kept as a test fixture) and `step_unit` must leave identical unit state (`last_good`, `burn`, `deck_usage`), make the same `maybe_onboard`, usage-note and ntfy calls in the same order, and produce the same render arguments.

## Absent in Qt mode

Until the issue in brackets lands, Qt mode has no walkthrough (#112), Settings (#112), accounts manager (#112), Stream Dock presets (#112), update check, install or hand-off (#113), tray icon (#114), About panel on macOS (#114, see Open questions), or Stream Dock at all (see Open questions). The menu leaves their items out. `menu.build_menu` already omits Settings and the update item when their callbacks are `None` (`menu.py`), so nothing new is needed for that.

First run: `run_qt` calls `first_run.classify` with `excluded=False` and ignores the result, so a fresh state directory is marked `pending` (`first_run.py:121-124`) before `build_units` writes `customization.json`. Qt mode then starts with one default pane, and the next Tk launch shows the walkthrough because the marker says it is pending. Passing `excluded=True` instead would leave the directory unmarked but no longer fresh, and Tk would skip the walkthrough for good. Debug launches still pass `excluded=True`, as they do in Tk.

The credentials hint (`__main__.py:258-262`) tells the user to open Settings, which Qt mode lacks. It stays as it is: Qt mode is a source-only developer mode until #112, and the way to Settings is a launch without the flag.

`TOKITTY_DEBUG_STATE` and `TOKITTY_DEBUG_ACCOUNTS=2` work in Qt mode as they do in Tk (`__main__.py:723-735`). They are what the manual checks and the screenshots use.

## Window

`tokitty/qt/widget.py`, one `QWidget` for every pane, with the spike's flags (findings, section 3): `FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus | NoDropShadowWindowHint`, plus `WA_TranslucentBackground`, `WA_ShowWithoutActivating` and `WA_MacAlwaysShowToolWindow` (`spike/qt_card.py:118`). Without the last, Qt hides a `Tool` window on macOS whenever its app is not active, which with the focus fix below is all the time. On Windows `Tool` gives `WS_EX_TOOLWINDOW`, so there is no taskbar button and no equivalent of `_keep_off_taskbar` (`ui.py:776-790`) is needed. There is no colour key and no second window, so `KEY_COLOR`, `avoid_key`, `set_content_owner` and the keyed split in `Pane` have no Qt counterpart. They stay in place for the Tk path.

`dpi.init()` is not called in Qt mode, and the spike never called it. It sets system DPI awareness (`dpi.py:9-17`), while Qt sets per-monitor awareness itself when `QApplication` is created. With `dpi.init()` first, Qt's call fails (Windows allows one change per process) and the process stays system-aware, so a second monitor at a different scale gets Windows' bitmap stretching instead of Qt's own per-monitor rendering. Qt works in logical pixels, the unit the #110 model uses, and the widget reads `devicePixelRatioF()` for anything drawn at device resolution.

Geometry comes from `theme.layout_window(homes, wander=False)`, where `homes` are the panes' resolved home spots (`theme.resolve_home`). The window is `size` big, each pane draws at its `pane_origins` entry, and the window is rebuilt (resized, origins recomputed) only when the pane count or a home changes, never per frame. In #111 the cat never leaves its home, so `wander` stays off and the window is exactly the grid unless an account has set `cat_home` by hand.

When a home change alters the margins, the window moves by the difference so the cards stay where they are on screen. That is #110's open question 1, answered the way it suggested.

## Painting

Each pane is a `QtPane`: a plain object with the Tk pane's public surface (`render(**kwargs)`, `render_usage(**kwargs)`, `set_appearance(...)`, `draw_next_frame()`, and the attributes `__main__` reads, `palette`, `_label`, `_colorway`, `_pattern`), which records state and asks the widget to repaint. Keeping the surface means `PaneUpdate.kwargs` apply to either pane unchanged, and the later Settings port (#112) finds the same attributes it reads today (`settings_ui.py:76`). The painting itself is in the widget's `paintEvent`, which walks the panes.

Per pane, in order:

1. Style from `theme.resolve_pane_style(theme, customization, has_accent, pane_index, pane_count)`: background, foreground, dim, opacity, bar fill override, palette, resolved home. When the last `render` or `render_usage` had `dimmed=True` (a stale token, an error), text uses the dim colour instead of the foreground, as Tk does (`ui.py:432`, `ui.py:480`). `resolve_pane_style` has no `dimmed` input, so the pane applies it.
2. Card: the card rectangle filled with the background at `theme.card_alpha(opacity)`, so 0% paints at alpha 1/255 and stays clickable (#110 decision 2). Corner radius and border from `CardStyle`.
3. Rectangles from `theme.layout_card(theme, view, home)`. Bars as track then fill, flat or pill per `BarStyle.shape`, fill colour from `theme.bar_fill`. Text in each named rectangle, with the font sizes and weights from the `theme.py` constants. Family is Segoe UI on Windows as today and the platform default elsewhere.
4. Halo where `theme.halo_applies(element)` and `theme.halo_active(text_style, opacity)`: the text drawn once in a dark colour at the eight one-pixel offsets, then in its own colour on top, as the spike did. The default theme has the halo off, which is what Tk draws.
5. Cat: `raster_rgba(frame, palette, theme.cell_size(dpr))` wrapped in a `QImage` (`Format_RGBA8888`) with `setDevicePixelRatio(dpr)`, drawn at the pane origin plus `theme.placement(home).origin`, each coordinate passed through `theme.snap(value, dpr)`, mirrored when the placement says so. Images are cached on (state, frame index, palette signature, cell size, mirrored), where the signature is the sorted tuple of the palette's (character, colour) pairs, since `effective_palette` returns a `dict` (`customize.py:143`) and cannot be a key itself, and the cache is dropped when the device pixel ratio changes (the window's screen changes, or the screen's scale does). Qt never scales the image, which is what made the spike byte-identical (findings, section 1).
6. Cat tags (driving and tool, `ui.py:525-548`) over the cat area. `layout_card` gives only `cat_area`, not the tags (`theme.py:342-344`), so the tags go at the offsets `ui.py` uses, relative to `cat_area`. Tk draws a `BG_COLOR` chip under each tag only on the keyed window (`ui.py:543-550`); Qt draws the tag as the non-keyed Tk path does.

The accent forces 100% opacity on the accented pane only, a deliberate change from Tk, where it makes the whole window opaque (#110 spec, "Opacity"). The two views and the per-model rows come from `layout_card` for `"limits"` and `"models"`.

Pixel identity with today's Tk cat: Tk draws one rectangle per sprite cell with boundaries from `sprite_bounds` (`ui.py:124-141`), `round(i * SCALE * scale)`. Where `SCALE * scale` is a whole number (100, 125, 150, 175 and 200%) those cells are all exactly `round(SCALE * scale)` wide, so Tk and `raster_rgba` produce the same pixels. At other scales, such as a custom 110%, Tk stretches the sprite with uneven cells and Qt draws even cells of `round(SCALE * dpr)`, so the Qt cat is crisp but a different size: at 110% Tk's sprite is `round(28 * 4 * 1.1)` = 123 device pixels wide (`ui.py:139`) and Qt's is 28 cells of `cell_size(1.1)` = 4, 112 pixels, 11 narrower. That is accepted: `theme.cell_size` already fixes the rule, and #128 pins it with reference images.

Animation: one `QTimer` at 800 ms (`FRAME_INTERVAL_MS`, `ui.py:73`) calls `draw_next_frame` on every pane and repaints. Nothing in #111 animates faster than about 15 fps, so the timer stays coarse; `Qt.PreciseTimer` is #22's when walking arrives (findings, section 7).

## Input

Drag: press on any painted pixel, move by the global mouse delta, save the position on release. Same as `ui.py:812-827`, with Qt's `globalPosition()`.

Right-click: a right-button press, as in the spike (`spike/qt_card.py:280`), plus Control-click on macOS, which Tk binds explicitly (`ui.py:76-82`). Qt on macOS reports the Control key as `MetaModifier` (Command is `ControlModifier`), so the check is a left press with `MetaModifier` there. The clicked pane comes from the pane origins and card size (the Qt equivalent of `pane_index_at`, `ui.py:101-121`); a click outside every card maps to pane 0. The menu is rebuilt from `menu.build_menu` on every right-click, as Tk does (`ui.py:948-956`), and rendered by `tokitty/qt/menu.py`, a `QMenu` counterpart of `_render_tk_menu` (`ui.py:916-946`) covering dynamic labels, separators, disabled items, submenus, radio groups and checkboxes. In Qt mode the menu is: Refresh now, View (Limits, Models), Always in front, Exit.

Always in front: toggling `WindowStaysOnTopHint` with `setWindowFlag` makes Qt recreate the native window, which then has to be shown again without activating it, and on macOS has to have the helper and the mask applied again. The widget does all three in one method so none of them is forgotten. It starts on and is not saved, as in Tk (`ui.py:569`, `ui.py:981-984`).

Hit testing: on Windows, per-pixel alpha already does it (findings, section 2). The empty margin and transparent sprite cells pass through, and the card catches clicks at every opacity because 0% paints at alpha 1. On macOS the window catches every click inside its rectangle (findings, macOS results), so the widget calls `setMask()` with `theme.hit_region(window_layout, cat_rects)`, cards plus each cat's sprite rectangle, whenever the layout or a cat rectangle changes. In #111 cats do not move, so that is at startup and on a layout change. The rectangle mask still catches clicks on transparent cells inside the cat's box, which Nick saw no problem with in the spike experiment; `QImage.createAlphaMask()` is left for #22, when the mask changes every frame anyway. Linux gets the mask too, since it costs nothing and the spike measured nothing there either way.

## Position

`position.json` keeps its shape and path, with one change of meaning. Tk saves the root window's top-left (`ui.py:1002-1008`) in the coordinates of a system-DPI-aware process (`dpi.py`), which on Windows are physical pixels. Qt's coordinates are logical, so at 150% the same numbers point somewhere else. And the Qt window's top-left is not the grid's when margins are non-zero.

So Qt mode saves `{"x": ..., "y": ..., "unit": "logical"}`, where x and y are the top-left of the first card, not of the window. On load, a file with `"unit": "logical"` is used as is. A file without `unit` was written by Tk. On macOS its x and y are used as is, because Tk there reports points, the same unit as Qt. On Windows the conversion is per monitor, not one division of the whole point: Qt keeps each screen's top-left at its native position and scales only within the screen, so dividing everything by the primary's ratio puts a point on a secondary monitor into the gap between screens. With a 2560 px primary at 150% and a 100% monitor starting at physical x=2560, a saved x=2700 divided by 1.5 is 1800, which is on no screen at all, and the fallback would move the card to the primary. So the loader finds the screen whose native geometry contains the saved point, and converts relative to that screen: `screen.x + (x - screen_native.x) / ratio`, with the ratio a system-aware process used for its coordinates (the primary's). `QScreen.geometry()` and `devicePixelRatio()` give what that needs. On Linux the ratio is normally 1 and the same rule changes nothing. Tests cover mixed-DPI pairs in both orders and a point on no screen. The window is then placed so the first card lands there, which with margins puts the window's own top-left up and left of it.

Clamping uses the grid's rectangle, not the window's, so a transparent margin may sit off screen but no card can. The work area comes from `QGuiApplication.screenAt()` and `availableGeometry()`, with the primary screen as the fallback, through the existing `geometry.clamp_to_area` and `geometry.default_position`. `monitors.py` is Windows-only and works in physical pixels, so Qt mode does not use it.

Tk reads a Qt-written file without complaint, since it reads only x and y (`ui.py:993`). At 100% it lands in the same place. At other scales it lands off by the scale factor and is clamped onto the screen. That is a dev-mode wart while both paths exist and disappears with the Tk code.

## macOS helper

`tokitty/qt/macos.py`, `ctypes` only, loading `libobjc` and resolving `objc_getClass`, `sel_registerName` and `objc_msgSend` with explicit `argtypes` and `restype` per call shape (the arm64 calling convention needs them). From the widget's `winId()`, which is an `NSView *`, it sends `window` to get the `NSWindow`, then:

- `respondsToSelector:` with `_setPreventsActivation:`, and if it does, sends it `YES`. This is the spike's proven focus fix (findings, macOS results, "Focus"). If the selector is gone in some later macOS, the call is skipped and the widget behaves as it does today: clicking it activates the app.
- `setCollectionBehavior:` with `canJoinAllSpaces | fullScreenAuxiliary` (`1 << 0 | 1 << 8`), so the widget follows to every Space and shows over full-screen apps. Untried in the spike, so it is the first thing to check on Nick's Mac.

Each step is wrapped so a failure prints one line to stderr and the widget carries on. It runs after the first `show()`, because there is no `NSWindow` before that, and again whenever the native window is recreated (the always-in-front toggle). It does nothing on other platforms, or when the Qt platform plugin is not `cocoa` (offscreen CI runs).

Running from source, macOS shows a Dock icon and a Python app menu for Qt mode, as it did for the spike. `LSUIElement` is a bundle setting and comes with #115.

## Tests

Two kinds. A new `qt` marker is registered in `pyproject.toml` and `addopts` changes from `-m 'not gui'` (`pyproject.toml:38`) to `-m 'not gui and not qt'`, so a plain `pytest` stays headless and needs no PySide6. The new CI job selects `-m qt` explicitly.

Unmarked, run everywhere in the existing matrix:

- `step_unit` against the captured Tk arguments, for the limits view, the models view, a stale result, an error result and an accented pose.
- `build_units` yields the same units, labels and saved `customization.json` as the inline code it replaces, for no accounts, one account and two accounts with different providers.
- Position: logical file used as is; Tk file divided by the ratio on Windows and not elsewhere (platform patched as in `test_main.py`); garbage, missing keys and booleans fall back to the default position; clamping keeps the grid on screen with the margin off screen; a margin change keeps the first card in place.
- The macOS helper against a fake `objc_msgSend` that records selectors: both calls made in order, `_setPreventsActivation:` skipped when `respondsToSelector:` says no, a raising call logged once without stopping the rest, nothing called off macOS.
- `run_qt` without PySide6 prints the message and returns 1; `main()` with `TOKITTY_QT=1` and `--apply-update` takes the Tk path.

Marked `qt`, run in a new CI job on `ubuntu-latest`, `macos-latest` and `windows-latest` with Python 3.14, `pip install -e ".[dev,qt]"`, and `QT_QPA_PLATFORM=offscreen`:

- The window: size and pane origins equal `layout_window` for 1, 2, 4, 5 and 8 panes, and for a pane with a side home.
- The cat, by the spike's method (`compare()` in `spike/qt_card.py`): in `grab()` of each pane's sprite rectangle, every opaque cell equals `raster_rgba` (mirrored for a mirrored placement), and every transparent cell equals a second grab with the cat hidden, which shows the card behind it. A whole-block equality only holds over the empty margin, so it is checked for a side home only. Every state's first frame and two coats, at dpr 1. Dpr 2 runs the same check in a subprocess with `QT_SCALE_FACTOR=2`, since a process gets one `QApplication`.
- The card: at 0% a pixel in the empty card area has alpha 1; an accented pane is opaque while its neighbour is at the theme's opacity.
- The menu: the rendered `QMenu` for the Qt-mode model has the expected labels, the current view checked, Always in front reflecting the setting; triggering an action runs it.
- Drag: synthesized press, move and release (`QTest`) move the window by the delta and write `position.json` with `"unit": "logical"`.
- Repaint on `render`: a `PaneUpdate` applied to a pane changes the drawn percentage text's region.

The Linux job may need `libegl1` and `libxkbcommon0` from apt for the offscreen plugin to load; the first CI run settles that and the workflow installs whatever it asks for. Existing tests keep passing unchanged, apart from imports that move with the core extraction. The `gui`-marked theme defaults test stays: the #110 spec says it goes with the Tk code "in #111", but under decision 1 the Tk code goes in the later default-switch step.

Manual, before the PR merges: on Windows (Cucumber, 100%, and 150% set for real as in the spike), from source with `TOKITTY_QT=1`, one and two panes: the card looks like the Tk card, compared as screenshots taken one after the other with the same debug state (both modes take the same single-instance lock, `lock.py:25`, so they cannot run at once), drag, right-click, every menu item, position restored after a restart and after a switch from Tk mode, click-through on the margin with a side home set by hand. On Nick's Mac: focus stays with Terminal while dragging, the widget follows across Spaces and shows over a full-screen app, the empty margin passes clicks to Finder.

## Slices

1. Core extraction: `app_core.py`, `run_gui` calling it, the parity tests. No Qt. Lands first because every later slice uses it and it carries the most regression risk for real users.
2. Pane and painting: `QtPane`, the widget's window and `paintEvent`, the cat cache, the `qt` extra and marker, the CI job, the window, cat and card tests.
3. Input and position: drag, the menu renderer, always in front, `position.json`, their tests. Depends on 2.
4. macOS helper and mask, its tests. Depends on 2; can run beside 3.
5. `run_qt` and the flag: lock, settings, theme, units, discovery, the tick and animation timers, debug states, shutdown. Depends on 1 to 4.

## Follow-ups (settled with Nick, 2026-10-11)

1. **Who flips the default and deletes the Tk code?** No issue owns it. It removes `ui.py`, the #37 parts of `transparency.py`, `dpi.init`'s system-aware mode, the `gui`-marked Tk tests and the flag, and has to come after #112, #113 and #114 and before #117. #115 is close (it already touches dependencies and the frozen self-check in `frozen.py:66-83`), but it is already the largest remaining item. Filed as [#142](https://github.com/nickwolf/tokitty/issues/142), after #115 and before #117 on the board.
2. **The Stream Dock in Qt mode.** The deck runtime itself is toolkit-free, and `DeckStarter` already takes `after` and `cancel` as callables (`__main__.py:1131-1136`), but `InWindowViews` (`streamdock/window.py:58`) opens Tk `Toplevel`s and `pane_anchor` (`__main__.py:1120-1122`) reads Tk geometry. #112 names the presets window and nothing names the in-window views. Added to #112's scope, so Qt mode gains the whole Stream Dock at once.
3. **The macOS About panel** (`ui.py:840-864`) imports `AppKit`, which comes from `pyobjc`, which comes from pystray. Once #114 drops pystray it needs a Qt version, probably `QMessageBox.about` from the tray menu. Noted on #114.

## Review (Codex, 2026-10-11)

gpt-6-sol at high effort, read-only, briefed cold. Every claim was checked against the code before changing anything here; all held. What changed:

1. First run: Qt mode marks a fresh state directory `pending` instead of passing `excluded=True`, which would have left it unmarked but no longer fresh, so Tk would never have shown the walkthrough.
2. `WA_MacAlwaysShowToolWindow` added. The spike set it and the first draft dropped it; without it a `Tool` window hides on macOS whenever the app is inactive.
3. Position migration converts per monitor. One division by the primary's ratio puts a point on a secondary Windows monitor into the gap between screens.
4. The image cache keys on a palette signature, since the palette is a `dict`.
5. `addopts` gains `not qt`; the first draft assumed it already excluded the new marker.
6. `build_units` callers re-attach their pane before wiring callbacks, and the parity tests check unit state and side effects, not only render arguments.
7. Dimmed text (stale token, errors) restored; `resolve_pane_style` has no `dimmed` input.
8. The cat test uses the spike's opaque and transparent cell method, since an inside cat's transparent cells show the card.
9. Hook warnings in Qt mode get a deferred `QMessageBox`, and the Settings hint is called out as a known gap.
10. The manual Tk and Qt comparison uses sequential screenshots, because both modes take the same lock.

Factual corrections taken: `snap` takes `(value, dpr)`, `layout_card` returns no tag rectangles, and the 110% size difference is 11 px on the whole sprite, not half a cell per pixel.
