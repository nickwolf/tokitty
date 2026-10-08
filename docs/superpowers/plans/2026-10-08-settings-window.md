# Plan: settings window and shorter menu (#78)

Spec: `docs/superpowers/specs/2026-10-08-settings-window-design.md`. Branch `settings-window`, worktree `.worktrees/settings-window`.

Validation for every slice: `/usr/bin/python3 -m pytest <targeted files> -q`, `xvfb-run -a /usr/bin/python3 -m pytest -m gui <targeted files> -q` where gui tests exist, `ruff check tokitty tests`. Full suite runs in the main session after each slice lands.

## Slice A: menu model (no dependencies)

Files: `tokitty/menu.py`, `tokitty/ui.py` (menu parts only), `tests/test_menu.py`, `tests/test_tray.py`, `tests/test_tray_factory.py`, `tests/test_ui_layout.py` and any other test asserting menu labels.

1. `build_menu` takes only: `on_refresh`, `always_on_top`, `on_toggle_always_on_top`, `on_quit`, `on_open_settings`, the view-mode trio, and the update-available pair (`update_available_label`, `on_install_update`). Output order: update item (dynamic, hidden when None), Refresh now, View ▸, Always in front, separator, Settings…, separator, Exit.
2. Delete `streamdock_menu_item` and `INSTALL_STREAMDOCK_COMMAND` from menu.py only if nothing else uses them (the Stream Dock tab in slice D will need the install command string; move the constant rather than delete it if so).
3. `ui.py`: `build_menu_model(pane_index)` passes `on_open_settings=lambda i=pane_index: self.open_settings(i)`, where `self.open_settings` is a new seam attribute (default: a no-op until slice C sets it, guarded so the item is omitted when the seam is None). Remember the clicked pane: a blank cell maps to pane 0. Delete `_PANE_SPECIFIC_LABELS`, the blank-cell filter, the Stream Dock insert in `_rebuild_context_menu`, and `_copy_install_streamdock`.
4. Add `TokittyWindow.notify_state_changed()`: calls `on_menu_action_done` (if set) and then `self.settings_refresh` (a new seam, None by default). `_after_menu_action` calls `notify_state_changed()` instead of `on_menu_action_done` directly.
5. Tray: actions currently wrapped as `root.after(0, action)` (`tray.py:85`) must also end with the window's state-changed notification. Add an optional `on_action_done` callable to `TrayManager` that the wrapper runs on the Tk thread after the action; `run_gui` passes `window.notify_state_changed`. Keep pystray getters reading plain-Python state.
6. Do NOT delete the window seam attributes that `build_menu` stops reading (`on_toggle_tray`, `tray_enabled`, `on_randomize`, `surprise_me`, ... ) or the `run_gui` code that sets them; slice C consumes them. `_open_customize_dialog`, `_open_rename_dialog` and `on_set_budget` stay until slice C replaces them.

## Slice B: hook backend (no dependencies, parallel with A)

Files: `tokitty/hooks_install.py`, new `tokitty/hook_guard.py`, `tokitty/accounts_ui.py` (import change only), `tokitty/__main__.py` (startup retry/ensure_current section only, ~lines 580-620), new/updated tests.

1. Move the `state_dir`-keyed in-flight guard (`_in_flight_operations`, its lock, and the acquire/release helpers, `accounts_ui.py:58-256`) into `tokitty/hook_guard.py` with a public API; `accounts_ui` imports from it. Behaviour unchanged.
2. Startup: the `retry_pending_hook_op` and `ensure_current` calls in `run_gui` acquire the guard for `state_dir` (non-blocking is fine at startup: nothing else can hold it yet, but take it so later writers see it).
3. `hook_status_for_dir(config_dir, provider) -> HookStatus` per the spec's "Hook status per account" section: states `installed`, `outdated`, `local_only`, `not_installed`, `awaiting_approval`, `unreachable`, `unsupported`, `error`. Reuse `_reconcile`'s decision logic in a dry mode instead of a parallel checker; must never write (including no mkdir, no copy). `HookStatus` is a frozen dataclass with `state` and `detail: str`.
4. `apply_hook_operation(state_dir, config_dir, provider, op)` per the spec's "Install and remove from the tab" section (refuse on another dir's pending op, supersede same-dir op, journal, run, clear on success, keep on failure). Returns a result with `ok`, `message`, and `blocked_by` (the other pending dir, if refused). Caller is responsible for holding the guard; document that.
5. Tests per the spec's Tests section for these two functions.

## Slice C: settings window shell, General, Look, Usage (after A)

Files: new `tokitty/settings_ui.py`, `tokitty/ui.py`, `tokitty/__main__.py`, new `tests/test_settings_ui.py`, budget tests.

Theme first: render a minimal notebook both ways on Windows (global `clam` with scoped style names vs a plain-tk tab strip) and pick; record the choice in the spec. Then the window, the three tabs, the budget seam split, deletion of the Customize/Rename dialogs and `simpledialog` budget path, and wiring `window.open_settings` / `window.settings_refresh` in `run_gui`.

## Slice D: Accounts and Stream Dock tabs (after B and C)

Files: `tokitty/settings_ui.py` (two tab classes), `tokitty/__main__.py` (Stream Dock state holder), tests.

## Manual gate

The spec's "Manual gate on Windows" list, run by Nick on Cucumber.
