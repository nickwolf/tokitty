# Settings window and a shorter menu (#78)

## Decisions already made

- The right-click menu and the tray menu keep only: the update-available item (when an update is known), Refresh now, View, Always in front, Settings…, Exit.
- Settings is one window with a `ttk.Notebook`: General, Look, Usage, Accounts, Stream Dock.
- Per-pane controls (Look, budgets) sit under a pane picker at the top of their tab, preselected to the pane that was right-clicked. Opened from the tray or a blank cell, it preselects pane 0.
- The first-run walkthrough (add an account, install hooks) is out of scope and gets its own issue.

## Shape

### Menu

`build_menu` shrinks to the six items above. Its parameters for colorway, pattern, randomize, customize, rename, accounts, tray, autostart, opacity, usage window, usage readout, budget, surprise me, update checks and Stream Dock go away, and it gains `on_open_settings: Callable[[], None]`.

Consequences in `ui.py`:

- `_PANE_SPECIFIC_LABELS` and the blank-cell filtering in `_rebuild_context_menu` are deleted. The menu no longer has pane-specific items; the clicked pane only decides which pane Settings opens on.
- The Stream Dock submenu (`streamdock_menu_item`, the `len(model) - 2` insert, `_copy_install_streamdock`) moves out of the menu entirely.
- The tray keeps calling `build_menu_model(0)`. Its item set is static, so being built once at `tray.start()` is fine. `TrayManager.refresh()` still matters for the Always in front checkbox and the dynamic update label.

### Settings window

New module `tokitty/settings_ui.py`, class `SettingsWindow`, opened with `SettingsWindow.open(window, pane_index)`.

- One instance per root, same singleton pattern as `AccountsManager` and `PresetsDialog`. Opening it again raises the existing window and switches its pane picker to the new pane.
- It reads its seams from the `TokittyWindow` it is given (the `on_*`, getter and state attributes `__main__.run_gui` already sets). The seams that `build_menu` stops taking are still set on the window, and the settings window is their new consumer. Two new seams are needed, below: a budget read/write pair, and a state-changed notification.
- `transient(root)`, not modal, not `-topmost` itself. See the Windows risk below.
- Every write goes through the same callback the menu used, then through `window.on_menu_action_done` (which is `tray.refresh`), so the tray checkbox and the shadow state stay in agreement exactly as they do for menu actions today.
- **Threading rule.** Worker threads never call Tk, not even `root.after`: this repo has a reproduced interpreter abort from worker-side Tk calls, and `run_gui` documents a pre-mainloop `root.after` being dropped. Workers publish results into a lock-guarded slot; a Tk-thread poll scheduled with `toplevel.after` picks them up, as `AccountsManager._poll_retry_done` already does (`accounts_ui.py:330-340`). This applies to hook status, hook install/remove and Stream Dock install/uninstall.

### Keeping it current

The window re-reads every control from the getters when it opens, when its pane picker changes, when a tab is selected, and after each of its own writes. Changes can also come from three other places while it is open, and each needs a path to `SettingsWindow.refresh()`:

- **Right-click menu** (Always in front, View): `_after_menu_action` already runs after every Tk menu command.
- **Tray menu** (same two items): the tray does not go through `_after_menu_action`. Its wrapper is `root.after(0, action)` (`tray.py:85`). Add a single `TokittyWindow.notify_state_changed()` that calls both `on_menu_action_done` and the open settings window's `refresh()`, and route the Tk menu wrapper, the tray wrapper and the settings window's own writes through it. The tray wrapper still schedules onto the Tk thread with `root.after`, which is safe there because pystray's thread runs after mainloop has started and this is the existing, tested path.
- **Manage accounts…** (`AccountsManager`, a separate Toplevel): it can add or remove accounts while Settings stays open. The Accounts tab rebuilds its rows when it is selected and when `AccountsManager` closes, and every hook write revalidates that its target account still exists in `accounts.json` before running.

### Tabs

**General**: Always in front, Show tray icon (only when a tray backend is available, same gate as today), Start at login (same gate), Surprise me, Check for updates automatically, a Check for updates now button, and Transparency as a radio row of the existing discrete `LEVELS`. A slider is not used, so the stored values stay the same set the menu offered.

**Look** (pane picker): Colorway and Pattern as read-only comboboxes, Randomize, a Name entry that saves on Enter or focus-out (replacing the Rename dialog), the four color rows from the Customize dialog (swatch plus Change… opening `colorchooser`), and Reset to preset. `_open_customize_dialog` and `_open_rename_dialog` are deleted; their writes already go through `_fire_customization_changed`, which the tab reuses.

**Usage**: View (also stays on the menu), Usage window, Usage readout as radio groups, then a per-pane Budget entry under the pane picker. Budgets are keyed by account slug (`unit["key"]`) and usage window (`24h`, `7d`, `month`, `ui.py:69`), and the tab has no way to reach `unit["key"]` today, since `window.on_set_budget(pane_index)` opens a `simpledialog` and takes no value. So the budget logic in `run_gui` (`__main__.py:~869`) is split into two seams set on the window:

- `budget_for_pane(pane_index) -> Optional[float]` for the current usage window.
- `set_budget_for_pane(pane_index, text) -> Optional[str]`, which returns an error message or None. It keeps today's rules: blank clears, an optional leading `$` is stripped, the value must be a positive number. It also rejects `inf` and `nan`, which the current path accepts by accident.

The entry label names the current window ("Budget for the last 7 days") and re-reads when either the pane or the usage window changes. Errors show inline. The `simpledialog` path and `on_set_budget` are deleted.

**Accounts**: one row per account, built from the account store (`load_accounts_result`), not from `get_config_dirs`. `get_config_dirs` returns bare `(config_dir, provider)` pairs with no names, drops providers without hooks, and with no accounts file resolves a default Claude home, which on Windows can mean a WSL discovery scan. Each row shows the account name, provider, config dir, hook status, and Install hooks / Remove hooks. Providers without hooks get an `unsupported` row with no buttons. When there is no accounts file, the tab shows the single default target as its own row, labelled as the default, and resolves it on a worker thread. Below the table, Manage accounts… opens the existing `AccountsManager` window unchanged. Embedding `AccountsManager` in the tab is not part of this pass: it is a Toplevel singleton with its own close and destroy handling and a module-level in-flight guard, and refactoring it buys nothing the button does not.

**Stream Dock**: status line, Install or Uninstall button, New-session presets… (opens the existing `PresetsDialog`), and the Show sessions from account checkboxes from `streamdock_account_toggles`.

## New backend pieces

### Hook status per account

There is no read-only status query today, and `_events_with_tokitty_entries` alone is not enough to build one: it reports event names only, not the matcher, the handler shape, duplicates, or whether the owned entry sits in Claude's `settings.local.json` (which `HookTarget` treats as read-only and Remove does not touch). Codex also changes its required event set when an automatic reviewer is configured (`_codex_events_for`, `hooks_install.py:919`), and its hooks can be installed but not yet approved (`codex_trust.py`).

Add `hook_status_for_dir(config_dir, provider) -> HookStatus` in `hooks_install.py`. It never writes. It reuses the same per-provider logic `_reconcile` uses to decide what to add (`HookTarget`, `_load_reconcile_state`, `_codex_events_for`, the owned-hook matchers), run in a dry mode, rather than a second hand-written checker that could drift from it. States:

- `installed`: every required event has a correctly shaped owned entry in the main file, and the hook runner is present and current.
- `outdated`: owned entries exist but some are missing, misshaped, duplicated, or the runner differs. This is what `ensure_current` would fix.
- `local_only`: the only owned entries are in `settings.local.json`. Install skips those events and Remove leaves them in place, so the row says so.
- `not_installed`: no owned entries.
- `awaiting_approval` (Codex only): installed, but `codex_trust` reports the hooks are not yet trusted.
- `unreachable`: the config dir is on a WSL distro that is stopped or the path cannot be read. Checked before touching a `\\wsl$` path, so opening the tab never wakes a distro.
- `unsupported`: `provider_has_hooks(provider)` is false.
- `error`: the settings file is unreadable, not valid JSON, or valid JSON of the wrong shape; carries the message.

### Install and remove from the tab

Calling `install_hooks_for_dir` / `uninstall_hooks_for_dir` directly is not safe. `run_gui` runs `retry_pending_hook_op` and `ensure_current` at startup without the `accounts_ui` in-flight guard (`__main__.py:~600`), and the pending-op journal is a single slot. A Remove from the tab that leaves an older pending Install in the journal is undone by the next startup retry, and a failed tab operation leaves no record at all. `apply_account_mutation` cannot be reused unchanged because it writes `accounts.json` first.

Add `apply_hook_operation(state_dir, config_dir, provider, op)` in `hooks_install.py`, beside `apply_account_mutation`:

1. If a pending op exists for a different config dir, refuse with a message ("Finish the pending change for <name> first", with a Retry button that runs `retry_pending_hook_op`). If it exists for the same dir, it is superseded by this one.
2. Write this op to the journal.
3. Run install or uninstall.
4. Clear the journal on success; leave it on failure so the startup retry finishes the job the user asked for.

The tab runs this off the Tk thread under the same module-level, `state_dir`-keyed in-flight guard `AccountsManager` uses (`_in_flight_operations`, `accounts_ui.py:58`), and the startup `retry_pending_hook_op` / `ensure_current` path takes that guard too, so all three writers are serialized. The guard helpers move to a small shared module so `hooks_install` callers and `accounts_ui` can both use them without an import cycle. While the guard is held, the buttons are disabled and the tab says another change is in progress.

### Stream Dock install and uninstall

`install_streamdock(state_dir=, appdata=, print_fn=)` and `uninstall_streamdock(state_dir=, appdata=, tokitty_dirs=, print_fn=)` are called with the same arguments `run_install` / `run_uninstall` pass, from a worker thread, with a `print_fn` that collects lines into the tab. Both are Windows-only today (`_real_appdata`), so the buttons are disabled with an explanation on other platforms. The tab checks the return code, and after install also checks the plugin folder exists, since `install_streamdock` saves the port and token before copying and a failed copy would otherwise read as installed. Uninstall asks for confirmation first, since it removes the plugin folder.

The running process does not change state on either operation. `start_streamdock` runs once in `run_gui`, and `streamdock_state` closes over the startup `settings` object (`__main__.py:1016-1028`). So:

- `streamdock_state` reads a small mutable holder instead of the captured `settings`, and the tab updates the holder after either operation.
- Two new states, `restart_to_connect` (installed this session, no server running) and `restart_to_finish_removal` (uninstalled this session, runtime still up). Both show "Restart tokitty to finish" and a Restart now button is out of scope.
- After an uninstall, presets and Show sessions from are disabled until restart, rather than stopping the live runtime, its tick loop and the in-window deck views mid-session.

## Tests

- `tests/test_menu.py`: rewritten for the six-item menu; checks gone items are gone and `on_open_settings` is wired.
- `tests/test_tray.py`, `tests/test_tray_factory.py`: updated item sets.
- New headless tests for `hook_status_for_dir` covering every state for both Claude and Codex, including a Codex dir with an automatic reviewer configured, a `settings.local.json`-only install, valid JSON of the wrong shape, and a stopped-distro path; each asserts nothing was written.
- New headless tests for `apply_hook_operation`: refuses when another dir has a pending op; supersedes a same-dir pending op; leaves the journal on failure and clears it on success; a Remove followed by a simulated startup retry does not reinstall.
- New headless tests for `set_budget_for_pane`: blank clears, `$12.50` accepted, `0`, `-1`, `abc`, `inf` and `nan` rejected, and the write lands on the right slug and window.
- New `gui` tests (xvfb) for `SettingsWindow`: opens on the requested pane; reopening switches pane and does not create a second window; each General toggle calls its seam and then `on_menu_action_done`; a tray-path action and a Tk-menu action both refresh an open window; the Look tab writes through `on_customization_changed` for the selected pane, not pane 0; the budget entry re-reads when the usage window changes; hook buttons are disabled while the in-flight guard is held; an account removed via `AccountsManager` disappears from the table; no worker thread calls into Tk (assert via a guarded fake root).
- Existing `AccountsManager` and `PresetsDialog` tests stay as they are, since neither class changes.

## Manual gate on Windows

- Right-click on a pane, on a blank cell, and the tray menu all show the same six items.
- Settings… from each of those opens on the right pane.
- Toggle Always in front from the menu, then from Settings, then from the tray, with Settings left open throughout: all three views agree after each.
- With Always in front on, Settings and the dialogs it opens (colorchooser, Manage accounts…, presets) appear above the widget and take focus.
- Install hooks, then Remove hooks, on a WSL account from the tab: status updates, and the live session's activity follows.

## Risks

- **Z-order on Windows.** The widget is `-topmost` and owns a content window (`_own_content_window`). A transient, non-topmost Toplevel can open behind it. The manual gate checks this; if it fails, the fix is to raise and focus the settings window after mapping, and to parent colorchooser on it.
- **Two writers to customization.json.** The Look tab and any still-open Customize path must not hold separate snapshots. Both go through `handle_customization_changed`, which reloads before each write, so this holds as long as the tab never caches a `Customization` across writes.
- **Users who knew the old menu.** Transparency, colorway and pattern were one right-click away and are now two clicks away. That is the trade #78 asks for.

## Theme

Dark, to match the widget: a `clam`-based `ttk.Style` built from the `ui.py` palette (`BG_COLOR`, `FG_COLOR`, `ACCENT_BG`, `ACCENT_FG`), using style names the settings window owns so it does not restyle the ttk widgets in `streamdock/window.py` and `presets_ui.py`. Check for updates now lives only in General; the menu shows the update item only once an update is found.

Caveat: `ttk.Style().theme_use()` is global to the Tk interpreter, not per window, and the Windows `vista` theme ignores most color options on Notebook tabs. Switching the whole app to `clam` would also restyle the Stream Dock window and the presets Treeview. Either accept that (and check both still look right), or build the tab strip from plain `tk` widgets (a row of buttons over a frame stack) so no global theme change is needed. Decide in the plan after a quick render of both on Windows.

**Decided 2026-10-08 after rendering both on Windows 11:** plain-tk tab strip (a row of `tk.Label`/`tk.Button` tabs over a stack of `tk.Frame`s), dark palette, `tk.Checkbutton` with `selectcolor` for toggles, `tk.OptionMenu` (or a styled `tk.Menubutton`) instead of `ttk.Combobox` so dropdowns stay dark. No `theme_use` call anywhere. In the `clam` render, an unrelated default ttk Button and Treeview in the same process switched to clam's grey look, which confirms the global bleed.
