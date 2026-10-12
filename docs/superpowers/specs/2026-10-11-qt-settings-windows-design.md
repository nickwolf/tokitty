# Qt settings, accounts, first run and Stream Dock windows (design)

Issue: [#112](https://github.com/nickwolf/tokitty/issues/112), milestone v0.7.0 "Qt widget". Builds on the Qt widget from [#111](https://github.com/nickwolf/tokitty/issues/111) (`tokitty/qt/`, `tokitty/app_core.py`, spec `2026-10-11-qt-main-widget-design.md`) and the theme model from [#110](https://github.com/nickwolf/tokitty/issues/110) (`tokitty/theme.py`, spec `2026-10-11-theme-layout-model-design.md`). The Tk windows being ported were designed in `2026-10-08-settings-window-design.md` (#78) and `2026-10-08-first-run-walkthrough-design.md` (#88); their behaviour is the target here, and this spec only describes what changes or needs deciding for Qt. Siblings that stay out: #113 (updater UI), #114 (tray, macOS About panel), #115 (packaging), #142 (Qt becomes the default, Tk code deleted).

Status: draft, 2026-10-11, written before any decision from Nick. The choices marked "proposed" below are mine and are the ones to check. A Codex review (gpt-6-sol, high) the same day found seven defects and one wrong citation; they are folded in and listed in the review section at the end.

## Goal

Qt mode (`TOKITTY_QT=1`) gains everything #111 left out that is not the updater or the tray: Settings with all its tabs, the accounts manager, the first-run walkthrough, the Stream Dock presets window, and the Stream Dock itself, including the in-window permission views. Settings also gains the editors for the #110 theme, which nothing can edit today except a hand-edited `theme.json`. After this issue, the only things Qt mode lacks compared with Tk are the update check and install (#113), the tray icon (#114) and the macOS About panel (#114).

## Scope and non-goals

In scope: a toolkit-free home for the Settings seams that `run_gui` builds inline, the pure helpers pulled out of the Tk modules so Qt never imports `tkinter`, a Qt widget kit, Qt versions of `SettingsWindow` and its five tabs, a new Theme tab, `AccountsManager`, `Walkthrough`, `PresetsDialog`, `InWindowViews` and `pane_anchor`, the Stream Dock started from `run_qt`, live theme and cat-home changes in the Qt widget, and the macOS app-menu Settings item.

Not in scope: any change to the Tk windows' look or behaviour, deleting Tk code (#142), the update section of General (absent until #113 supplies its seams; the tab already hides it when they are `None`, `settings_ui.py:142`), the tray toggle (#114, hidden the same way, `settings_ui.py:126`), the macOS About panel (#114), opacity on the mouse wheel (proposed decision 6), theme presets (#132), a card design pass (#139).

## Proposed decisions

1. **The seams move into a toolkit-free object, and the Tk path uses it too.** Today about 40 seams are closures inside `run_gui` (`__main__.py:343-529` and `:552-717`) assigned onto `TokittyWindow` as attributes, and the Tk tabs read them as `self.win.<name>` (`settings_ui.py:92`). Copying those closures into `run_qt` would give two copies of the budget, ntfy, usage-note and customization rules that drift. Instead they move to `tokitty/app_controls.py` as one `Controls` object built from `state_dir`, `units`, `usage_state`, `customization_store` and a few toolkit hooks (below). `run_gui` builds it and assigns its members onto the window under the same attribute names, so no Tk tab changes. The Qt tabs read the same names from a `QtHost` that holds the `Controls` plus the Qt-only seams. This is the same move #111 made with `app_core`, and it carries the same regression risk for real users, so it lands first with parity tests.
2. **Pure helpers leave the Tk modules; the Tk modules re-export them.** Qt code must import without `tkinter`, both because #142 removes it and because the frozen Qt build in #115 may not ship it. The pieces that hold no Tk but live in Tk modules move to toolkit-free modules: `Mailbox` and `run_worker` (`settings_async.py:16-44`), `version_text` (`settings_ui.py:56`), the colour and font constants and `BASE_WIDTH`/`BASE_HEIGHT` (`settings_widgets.py:21-47`), the Accounts tab's data side (`elide_middle`, `RowData`, `Snapshot`, `collect_rows`, `run_hook_op`, `run_finish_pending`, `PILLS` and the state sets, `settings_accounts.py:29-143`), the accounts manager's pure functions (`describe_capabilities` through `reconcile_before_save`, `accounts_ui.py:66-212`), its three `_run_*_off_thread` workers (`accounts_ui.py:215-247`, thread orchestration rather than pure functions: their `on_done` runs on the worker thread, so a caller's `on_done` may only publish plain data under a lock, as Tk's `_complete_operation` does at `accounts_ui.py:820`), and the Stream Dock tab's status logic. Each old module imports them back, so every existing import and test keeps working. A test imports every `tokitty.qt` module and every new toolkit-free module with `tkinter` blocked in `sys.modules`, the method `tests/test_theme.py:245-256` already uses.
3. **Same threading rule, same `Mailbox`, a `QTimer` poller.** Workers publish into the existing `Mailbox`; a `QtPoller` (a `QTimer` at the same 120 ms, `settings_async.py:72`) drains it on the GUI thread. Signals would also work, but keeping the mailbox means the worker code and its tests are shared unchanged with Tk, and the rule stays one rule: a worker never touches a widget or a `QObject`.
4. **Windows are top-level, not children of the widget, and follow its Always in front.** The Qt widget is a frameless `Tool` window with `WindowStaysOnTopHint` and `WindowDoesNotAcceptFocus` (`qt/widget.py:86-92`). A dialog parented to it inherits ownership quirks: on Windows an owned window follows its owner's z-order band, and on macOS a `Tool` parent can hide its children when the app deactivates. So Settings, the accounts manager, the walkthrough and the presets window are parentless `QWidget` windows (normal title bar, taskbar button). A parentless window without the stay-on-top hint sits below a topmost widget, and `raise()` cannot change that, so each of these windows carries `WindowStaysOnTopHint` exactly while the widget's Always in front is on, and is updated when it is toggled (from the menu or from General); the walkthrough runs before the widget exists and needs neither. The in-window views always carry it, as Tk sets `-topmost` on them (`streamdock/window.py:84`). Modal confirmations and errors (`QMessageBox`, `QInputDialog`, `QColorDialog`, `QFileDialog`) are parented to the window that asked, as Tk parents them today, which keeps them above it. The deferred hook-warning box stays parented to the widget (`qt/app.py:190-191`), an owned window above its topmost owner. `activateWindow()` cannot take the foreground from another application on Windows; Settings is normally opened from the widget's own context menu, so the process already has the foreground then, and the manual gate checks it with Always in front on and off.
5. **A new Theme tab, Qt only.** Global theme fields get their own tab between Look and Usage, since Look is per pane and the theme is not. Per-account fields stay on Look, which gains one row, "Cat sits", for `cat_home`, over the revised spots below. Details under "Theme tab" and "Cat spots, revised".
6. **No opacity on the mouse wheel.** #111 left it for #112 to decide. With 0% now invisible (#110 decision 2), a wheel over the widget makes it easy to scroll a card out of sight by accident while scrolling the window behind it, and Tk never had one. Opacity is a segmented row on the Theme tab.
7. **The walkthrough runs in Qt before the widget exists**, as it does in Tk, through a local `QEventLoop`. Qt mode then stops marking a fresh state dir pending for Tk's benefit (`qt/app.py:72-76`): it shows the walkthrough itself and marks it done.
8. **Two PRs from one branch line.** Slice 1 changes the Tk path real users run, and slices 2 to 5 are Qt work behind the flag, so slice 1 merges first on its own with its parity tests, and the Qt slices follow in a second PR cut from main after it.

## The shared seams

`tokitty/app_controls.py`, no toolkit imports. `make_controls(state_dir, units, usage_state, customization_store, *, apply_customization, on_layout_changed, discovery, distro_probe, deck_fn)` returns a `Controls` dataclass whose fields are the existing seam names with their existing signatures:

- Look and per-pane: `on_customization_changed`, `on_randomize`, `surprise_me`, `on_toggle_surprise`. `handle_customization_changed` (`__main__.py:348-382`) moves in whole and gains one field, `"cat_home"`, accepted when the value is in `theme.SPOTS`. After saving it calls `on_layout_changed(pane_index, custom)`, which the Tk path sets to a no-op (Tk does not consume the model, #110 decision 3). The Qt path sets it to one adapter in `QtHost`: it gives the pane its whole new `Customization` (a new `QtPane.set_customization`, since `set_appearance` rebuilds the customization without `cat_home`, `qt/pane.py:86-89`), resolves every pane's home with `theme.resolve_home`, and calls `widget.rebuild_layout(homes)`, which takes the full list (`qt/widget.py:119`).
- Usage: `view_mode`/`on_view_mode`, `usage_window`/`on_usage_window`, `usage_readout`/`on_usage_readout`, `budget_for_pane`/`set_budget_for_pane`, the four usage-note seams and the five ntfy seams (`__main__.py:415-526`).
- Accounts: `on_open_accounts` takes a toolkit-specific opener, since it opens a window; `running_distros` is `distro_probe.get_running`.
- Stream Dock: `streamdock_state`, `streamdock_info`, `streamdock_install`, `streamdock_uninstall`, `streamdock_install_supported` (all from `StreamdockHolder`, already toolkit-free, `__main__.py:629-639`), `streamdock_account_toggles` (`__main__.py:690-717`, moves in whole), and `on_edit_streamdock_presets`, which again takes a toolkit opener.
- Autostart: `autostart_enabled`/`on_toggle_autostart` (`__main__.py:560-603`). The one Tk call in it, `messagebox.showwarning` for `AppTranslocatedError` (`:594-596`), becomes a `warn(title, text)` hook each toolkit supplies.

The seams that belong to the window stay with each toolkit: always in front, opacity (Tk only; Qt's lives in the theme), `notify_state_changed`, `settings_refresh`, `panes`, and the tray and update seams. `QtHost` carries the Qt versions. Its `notify_state_changed` refreshes an open Settings window; it has no tray to refresh until #114. The Qt widget's `on_menu_action_done` points at it, so toggling Always in front or View from the right-click menu updates an open Settings window, as in Tk.

Parity: the closures being moved are first copied into `tests/fixtures/` unchanged, and for the same inputs the old closure and the `Controls` member must write the same `settings.json` and `customization.json`, leave the same `usage_state`, return the same error strings and make the same pane calls. The existing Tk gui tests run unchanged on top.

## Qt widget kit

`tokitty/qt/kit.py`: the Qt counterpart of `settings_widgets.Kit`, with the same method names so each tab ports line for line: `page_header`, `section_header`, `button` (primary, secondary, danger, compact), `pill`/`set_pill`, `toggle` (a switch with title and detail), `segmented`, `dropdown`, `entry` with `set_entry_error`, the left rail, and a sprite preview that draws with `sprite_raster.raster_rgba`, the same path the widget uses, instead of `settings_widgets.draw_sprite`. Colours and fonts come from the shared constants (decision 2). Styling is one Qt style sheet set on each window this issue creates, not on the `QApplication`, so the widget's context menu and any `QMessageBox` keep the platform look. Kit sizes are logical pixels; Qt scales them, so `Kit.px` becomes the identity and `dpi.init()` stays out of Qt mode (#111, "Window").

The scroll area is a `QScrollArea` with a thin custom scrollbar style. Tk needed its own `ThinScrollbar` and wheel and trackpad bindings (`settings_ui.py:609-622`, `:689-710`); Qt handles both.

## Settings window

`tokitty/qt/settings.py`, `SettingsWindow.open(host, pane_index)`, one instance per `QApplication`, reopening raises it and switches the pane picker, as in Tk (`settings_ui.py:551-561`). Size `BASE_WIDTH` by `BASE_HEIGHT` logical (640 by 540), growing to the tallest tab as `_fit_to_tallest_tab` does. Tabs, in rail order: General, Look, Theme, Usage, Accounts, Stream Dock. Every write goes through `write()`: run the seam, then `host.notify_state_changed()` (`settings_ui.py:106-109`). Controls re-read on open, on tab switch, on pane change and after every write.

- **General**: Always in front, Start at login (when the backend exists), Surprise me. Show tray icon and Updates are hidden until #114 and #113 supply their seams. Transparency moves to the Theme tab in Qt mode, because Qt's opacity is `theme.card.opacity` (0 to 100 in steps of 10) and Tk's is `settings.opacity` over `transparency.LEVELS`.
- **Look**: as Tk (`settings_ui.py:184-321`), but not line for line: the Tk tab reads `pane._card_bg` and `pane._bar_fill` (`settings_ui.py:76-83`), which `QtPane` does not have, and writes through the window's `_select_colorway`, `_select_pattern` and `_fire_customization_changed` (`settings_ui.py:214-291`). `QtHost` provides `pane_color(index, field)`, reading the pane's `Customization.overrides` and palette, with an unset bar fill reported as automatic as Tk does, and routes the three writes to `on_customization_changed`. Controls: pane picker, colorway, pattern, randomize, name, the four colour rows through `QColorDialog`, reset. Plus "Cat sits": Left, Right, and On top for a first-row pane only, built from `theme.resolve_home` per spot for this pane index and count, so a pane never offers a spot that would silently fall back. A saved On top that is no longer reachable (an account was removed and the pane left the first row) shows as Left, which is what the widget draws. The preview cat is mirrored for Right, as `theme.placement` says. Qt only: Tk ignores `cat_home` and is deleted in #142, so its Look tab does not get the row (Nick, 2026-10-12).
- **Theme**: below.
- **Usage**: as Tk (`settings_ui.py:323-530`), including the per-pane budget and the ntfy test on a worker.
- **Accounts**: as Tk (`settings_accounts.py:187-432`), on the shared data side, with Manage accounts... opening the Qt accounts manager.
- **Stream Dock**: as Tk (`settings_streamdock.py`), with New-session presets... opening the Qt presets window. On macOS and Linux install is unsupported, and the tab says so, as today.

On macOS, Qt mode creates a parentless `QMenuBar`, held for the life of the application, with one `QMenu` holding a Settings... `QAction` whose menu role is `PreferencesRole`, all set before the first window shows. Qt moves an action with that role out of its menu into the application menu, with Command-comma; the role applies to actions in a menu, not to bare actions on the bar. Offscreen tests cannot see native menu placement, so this is a manual Mac check. That replaces `register_app_menu_settings` (`ui.py:834-838`, `tk::mac::ShowPreferences`).

## Cat spots, revised (Nick, 2026-10-12)

#110 gave each account four homes: `inside`, `on_top`, `beside_left` and `beside_right`. Seen as a dropdown, `beside_left` is the cat's current position with a gap added: under the default `beside_cat` bar layout the card keeps its empty cat area (x 8 to 120, the stats start at x=132, `theme.py:258`), so a cat outside the left edge sits about 120 px from the text it belongs to, and `beside_right` leaves the same empty patch on the card's other side. Nick's call: Left is today's inside position, Right is the same card with the cat and the stats swapped, and no cat sits outside a side edge. On top stays. A redesign where the card shrinks to the bars and the cat always lives outside it was considered and goes to #139 (v0.8.0), since it changes the widget's look and #112 is a port.

Changes to `theme.py` (toolkit-free, so in slice 1):

- `SPOTS` becomes `("inside", "inside_right", "on_top")`. `inside` keeps its name, so every existing `customization.json` means what it meant. `beside_left` and `beside_right` go; `load_customization` already turns an unknown spot into `inside` (`customize.py:99`), which covers any file someone hand-edited since #110 merged.
- `layout_card(theme, view, "inside_right")` swaps the two columns of the `inside` layout: the cat area moves to x=180 (`CARD_WIDTH - 8 - 112`), and every other rectangle and line, the account label included, moves 122 px left as one block, so the stats column runs from x=10 to x=168 (`CARD_WIDTH - STATS_X - STATS_W` is 10). Nothing inside the block is mirrored or realigned: a full mirror would put each model's value to the left of its name and drop the left-aligned account label onto "SESSION". The exhaustive inside-the-card and no-overlap test runs over the new spot as well.
- `placement("inside_right")` is `Placement((CARD_WIDTH - 8 - SPRITE_W, 12), True)`, (180, 12), mirrored so the cat faces the stats as it does on the left. The cat tags are placed relative to the placement origin (`qt/widget.py:396-399`), which lands on the mirrored cat area at x=180, so they move with the cat.
- `full_width` stays honoured only when the cat is outside the card, which is now `on_top` alone: `layout_card`'s `pane_spot != "inside"` test (`theme.py:337`) becomes `pane_spot == "on_top"`, or `inside_right` would lose its cat area.
- Reachability: `inside` and `inside_right` are reachable from every pane; `on_top` from first-row panes, as before. `layout_window` never needs side margins, so a home change only ever moves the window vertically.
- `SCRATCH_SPOTS` becomes `{inside, inside_right}`: the cat scratches the near edge of the stats column from inside the card (the cat area ends at x=120 and the stats start at x=132, mirrored for Right), not the card's outer edge (Nick, 2026-10-12). #124 draws the frames and #22 decides when; their issue text still describes outside side spots and needs a note.

The Qt widget paints from `layout_card` and `placement`, so it follows without new drawing code. Tests pin the `inside_right` rectangles against the `inside` ones for both views, the placement, the `full_width` rule for `inside_right`, and zero side margins for any mix of homes.

## Theme tab

Global, written to `theme.json` through `theme.save_theme`, applied live with a new `TokittyQtWidget.set_theme(theme)` that swaps the theme, clears the layout cache (`qt/widget.py:153-158`), recomputes `rebuild_layout` when the bar layout changes which spots are reachable, and repaints. Controls, grouped as the dataclasses are:

- Card: Opacity as a segmented row 0, 10, ... 100 (labelled "Invisible" at 0, with the hint "still catches clicks"); Background colour; Corner radius 0 to 16; Border width 0 to 3 and Border colour.
- Bars: Layout (Beside the cat, Full width, with the hint that full width only applies to cards whose cat sits outside); Shape (Flat, Rounded); Height 4 to 10; Track, Low, Mid, High and Neutral colours; Mid from and High from as percentages.
- Text: Text and Dim colours; Show, one checkbox per `TEXT_ELEMENTS` entry with a plain name ("SESSION and WEEK labels", "Percentages", "Reset times", "Status line", "Account name", "Per-model rows", "Cat tags"); Outline (Off, Auto, On) and "Auto below" as an opacity.
- Reset theme, which writes the default theme.

Validation stays in `theme.py`. The tab never builds a `Theme` itself: it edits a plain dict, passes it through a new public `theme.theme_from_dict(raw, fallback)`, which is the existing `_clean_card`, `_clean_bars` and `_clean_text` pass (`theme.py:164-205`) without the file read and the opacity migration, and saves what comes back. A field the validator rejected is shown with `set_entry_error` and the old value stays, so the tab can never write something `load_theme` would later throw away. Mid from at or above High from is shown as an error on both entries rather than silently reset to 50 and 80, which is what the loader does to a hand-edited file.

Number fields are spin boxes with the model's ranges, so out-of-range input mostly cannot be typed; the validator is the guard for the rest.

## Accounts manager

`tokitty/qt/accounts.py`, `AccountsManager.open(state_dir, discovered_matches)`, one per process, behaviour as `accounts_ui.py:250-1009` over the shared pure functions (decision 2). That is the whole manager, not only add, rename and remove: the provider picker (`:448`), the usage section with its budget entry (`:465-556`), the discovered-path and discovered-Codex rows (`:599-640`), the capability facts (`:700`), the malformed-file row (`:721`), the macOS virtual row and its rename (`:830-846`), and the restart notice (`:435`). The in-flight state of a mutation lives at module level, as Tk's `_in_flight_operations` does, so closing and reopening the manager mid-operation picks the operation back up. `simpledialog.askstring` becomes `QInputDialog.getText` and `messagebox.show*` becomes `QMessageBox`, both parented to the manager. The module-level in-flight guard (`_in_flight_operations`) and the `hook_guard` it shares with Settings and startup stay exactly as they are, since they are toolkit-free. Its result polls (`_poll_retry_done`, `_poll_mutation_done`, `accounts_ui.py:341`, `:794`) run on a `QTimer` at their current intervals. The Accounts tab rebuilds when the manager closes, as in Tk, through a `closed` callback.

## First-run walkthrough

`tokitty/qt/walkthrough.py`, `Walkthrough(state_dir, credentials_cache, running_distros)`, the four steps and every rule in the #88 spec, on the shared `first_run` module and the shared Accounts data side. `run_qt` changes to match `run_gui` (`__main__.py:205-253`):

1. Classify under the lock as now, but keep the answer: `show = first_run.classify(...)`.
2. Build the `WslCredentialsCache` enabled when `show`, as `run_gui` does (`:213-215`), and reset it to disabled after the walkthrough when credentials were found natively (`:248-252`). Today `run_qt` builds it after the classify with only the native-credentials guard (`qt/app.py:127-129`).
3. Create the `QApplication`, show the walkthrough, and wait in a `QEventLoop` that its `closed` signal quits. `QApplication.quitOnLastWindowClosed` is turned off for the whole Qt run first, or closing the walkthrough (the last window at that point) would end the application before the widget is built. It stays off after the widget is shown, because the widget is a `Tool` window and Qt does not count it: measured with PySide6 offscreen, a `Tool` window plus a normal window, closing the normal one ended `app.exec()` with code 0. With the default left on, closing Settings would quit tokitty. Quitting is therefore always explicit: Exit calls `app.quit()`, and so does the widget's `closeEvent` (the frameless, focusless widget has no close button, but an OS-level close must not leave the lock and the deck running with nothing on screen).
4. Reload accounts and settings, then build the units and the widget as now. Nothing else (pollers, discovery, the deck) starts before the walkthrough closes, which keeps the hook guard free for its installs, as in Tk.

`TOKITTY_FIRST_RUN=1` forces it in Qt mode too, and debug launches never show it.

## Stream Dock

`run_qt` starts the deck the way `run_gui` does (`__main__.py:610-655`, `:748-755`): `DeckStarter` with `after` and `cancel` backed by single-shot `QTimer`s, `refresh_streamdock_plugin`, `StreamdockHolder`, `deck.runtime.tick(*gather_inputs(units))` and `apply_idle_cap` in the Qt tick. On exit, `run_qt`'s `finally` calls `deck_views.close_all()` and then `deck.stop()`, in the order `run_gui` does (`__main__.py:800-801`); `DeckStarter.stop` (`streamdock/wiring.py:217`) stops the runtime, which closes its server and watchers. Both run whether setup failed or `app.exec()` returned, and each is guarded so a failure in one still lets the other and the unit shutdown run. `DeckStarter` already takes `after` and `cancel` as callables (`streamdock/wiring.py:120`), so it does not change. The deck's port-retry deadline references `update_controller.ACK_TIMEOUT`, a constant; importing it pulls no toolkit.

`InWindowViews` splits in two. The bookkeeping (one view per nonce, `sync` closing views whose request left pending, `close`, `close_all`, the `in_window_opened` and `in_window_closed` calls) becomes toolkit-free and takes a `make_view(request, title, explain, anchor, on_answer, on_close)` factory returning an object with `close()`. The bookkeeping computes `title` from `title_text(name_fn(request.account_index), request)` and `explain` from `explain_text(reason, runtime.session_title(request), request)` (`streamdock/window.py:82`, `:91`), so both toolkits show the same account name and session title. `streamdock/window.py` keeps the Tk factory, so the Tk path is unchanged; `tokitty/qt/deck_views.py` adds the Qt one: a top-level `QWidget` with `WindowStaysOnTopHint`, the explanation, a read-only `QPlainTextEdit` with the tool input, and Allow, Deny and the optional Always button. Unlike the widget it takes focus, since it is asking a question; Tk's `Toplevel` does too.

`pane_anchor(index)` becomes a widget method: the window position plus `window_layout.pane_origins[index]` plus 16, in logical pixels, where Tk's `winfo_rootx() + 16` (`__main__.py:610-612`) is in the physical pixels of a system-aware process. Same place on screen, different unit, matching how #111 handles `position.json`.

`PresetsDialog` moves to `tokitty/qt/presets.py` with the same list, form and validation (`streamdock/presets_ui.py`, validation already in `streamdock/presets.py`), `filedialog.askdirectory` becoming `QFileDialog.getExistingDirectory`.

## Tests

The `qt` marker and CI job from #111 carry the Qt tests; plain `pytest` stays headless.

Unmarked, every OS:

- `Controls` parity against the copied closures, as described above, for every seam that writes: customization fields including the new `cat_home`, surprise, view, usage window and readout, budgets including `inf` and `nan`, note thresholds, every ntfy field and the toggle that generates a topic, the Stream Dock account toggles, and autostart with the translocated error routed to `warn`.
- The moved pure helpers keep their current tests, imported from both the old and the new module.
- `theme.theme_from_dict`: a valid dict round trips; one bad field falls back alone; inverted thresholds are reported; `show` with unknown names keeps the known ones.
- `InWindowViews` bookkeeping with a fake factory: one view per nonce, `sync` closes exactly the gone ones, answers call `decide_from_window` then close, `close_all` calls `in_window_closed` for each.
- The import-isolation test (decision 2).

Marked `qt`, offscreen:

- Settings: opens on the requested pane; reopening switches the pane and does not create a second window; each General control calls its seam then `notify_state_changed`; a right-click menu action refreshes an open Settings window; Look writes go to the selected pane; Cat sits offers On top only to first-row panes for 1, 2, 4 and 5 panes; Right swaps the cat and the stats without resizing the window; On top grows the window upward while the first card stays put on screen; the budget entry re-reads when the usage window changes.
- Theme: each control writes `theme.json`, the widget's theme changes, and a pixel in the card shows it (card alpha at 0% is 1, as #111 checks); a rejected value is not saved; Reset writes defaults.
- Accounts tab and manager: hook buttons disabled while the guard is held; add, rename and remove through fake mutations with `QInputDialog` and `QMessageBox` patched; an account removed in the manager leaves the tab.
- Walkthrough: click through every step with fake discovery; Skip on each step writes what the #88 spec says; no worker thread touches a `QObject` (the fakes assert the thread, as the Tk tests do); `run_qt` builds no units and starts no poller before it closes.
- Stream Dock: `DeckStarter` on the `QTimer` `after` retries and cancels; a fake runtime's pending request opens one Qt view at the pane's anchor, an answer reaches `decide_from_window`, and `sync` closes it when the request leaves pending; presets add, edit and remove with `QFileDialog` patched.
- No `tkinter` import: `run_qt` in a subprocess with a debug state and `tkinter` blocked, quitting from a `QTimer`.
- Lifetime: with the widget shown, opening and closing Settings leaves `app.exec()` running; closing the widget quits; `run_qt`'s `finally` calls `close_all` and `deck.stop` on a normal exit and when setup raises.
- Stacking: toggling Always in front sets and clears the stay-on-top hint on an open Settings window and accounts manager.
- Cat sits followed by another Look edit and a theme change keeps the new home (the customization is not rebuilt without `cat_home`).

Manual, Windows (Cucumber, 100% and 150%), from source with `TOKITTY_QT=1` and a scratch `LOCALAPPDATA` holding only the WSL accounts, never the real state dir (a source run rewrites the Windows-local Codex hook command, see the #111 lessons): Settings from the right-click menu on each pane opens on that pane and comes up in front of the widget with Always in front on and with it off, and stays in front when Always in front is toggled while it is open; every Theme control changes the widget live; Right swaps the cat and the stats with no gap, and On top moves the cat up while the card stays put; the colour, input and folder dialogs come up in front; a fresh scratch state dir shows the walkthrough, and Finish brings up the widget with the picked panes; Install hooks then Remove hooks on a WSL account from the Accounts tab; a Stream Dock permission request with the deck unplugged opens the in-window view at the right pane. On Nick's Mac, before #117 with the other Mac checks: Command-comma opens Settings, and Settings and the in-window view come up in front of the widget.

## Slices

1. Seams, helpers and spots: the revised cat spots in `theme.py`, `app_controls.py`, the moved pure helpers with re-exports, `run_gui` on `Controls`, `InWindowViews` split with the Tk factory, `theme.theme_from_dict`, the parity and isolation tests. No Qt. Lands first, and is what real users run.
2. Kit, poller, Settings shell with General, Look, Usage and Theme, `QtHost`, `set_theme`, Cat sits, the menu's Settings item and the macOS preferences action. Depends on 1.
3. Accounts tab and accounts manager. Depends on 2.
4. Walkthrough and the `run_qt` first-run flow. Depends on 3 (the hooks step uses the Accounts data side and pills).
5. Stream Dock: deck in `run_qt`, Qt views, `pane_anchor`, presets window, Stream Dock tab. Depends on 2; can run beside 3 and 4.

## Open questions

1. Should the Tk Look tab also get Cat sits? Decided (Nick, 2026-10-12): no, Tk is going away in #142.
2. At 0% every card is invisible but still catches clicks, so a user who sets it and closes Settings has to remember where the widget was to right-click it. Should the Theme tab warn at 0%, or should 0% need a confirmation? Decided (Nick, 2026-10-12): warn, with an inline hint under the opacity row while 0% is selected ("You'll need to right-click where the widget is to get back here"), no confirmation.

## Review (Codex, 2026-10-11)

gpt-6-sol at high effort, read-only, briefed cold. Every claim was checked against the code before changing anything here; all held, apart from one proposed fix, measured and declined. What changed:

1. Stream Dock shutdown: `run_qt` calls `close_all` and `deck.stop()` as `run_gui` does; the draft only closed the views.
2. Stacking: parentless windows carry the stay-on-top hint while Always in front is on. The draft relied on `raise()`, which cannot lift a normal window over a topmost one.
3. Cat sits: `on_layout_changed` passes the pane and its customization, and a `QtHost` adapter gives `rebuild_layout` the full list of homes; `QtPane` gains `set_customization` because `set_appearance` drops `cat_home`.
4. Look: `QtHost` supplies colour getters and the three write routes the Tk tab reads from the Tk window.
5. macOS: the preferences action sits in a menu on an application-lifetime menu bar, as Qt's role relocation requires.
6. Accounts: the `_run_*_off_thread` workers are called out as thread orchestration whose callbacks only publish data; the manager's full scope is listed; in-flight state survives a close and reopen.
7. The permission-view factory receives the title and explanation, so the Qt view shows the same account and session.
8. Slice 1 ships as its own PR before the Qt slices.

Declined: restoring `quitOnLastWindowClosed` once the widget is up. A PySide6 offscreen run showed that a `Tool` window does not keep the application alive, so with the default on, closing Settings would quit tokitty. The policy stays off and quitting is explicit, which also covers Codex's concern about a stranded process.

Factual correction taken: the Accounts helper range ends at `settings_accounts.py:143`, not 135.
