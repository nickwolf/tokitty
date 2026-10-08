# First-run walkthrough (#88)

## Decisions already made

- The walkthrough replaces the first-run auto-open of the Accounts dialog. `resolve_first_run_action`, `ACTION_ACCOUNTS`, `ACTION_USAGE_SETUP`, `maybe_auto_open` and the `discovery_result["consumed"]` handshake in `tick()` go away. (The issue calls the old path `should_auto_open`; that name was already replaced by `resolve_first_run_action` in `tokitty/startup.py`.)
- It never shows for someone who has run tokitty before, including on the update that ships it.
- Every step can be skipped. Skip means "skip the rest": each step applies only on its own button (step 2's reads "Add 2 accounts", step 3's Install buttons), so Skip never undoes what the user already confirmed and never does anything more. Skipping before step 2's button leaves tokitty exactly as a launch of 0.3.0 would: no `accounts.json`, no hooks. (Decided by Nick 2026-10-08, over an all-or-nothing Finish.)
- Hook installs go through `apply_hook_operation` under `hook_guard`, on worker threads that never call Tk, the way Settings ▸ Accounts does.
- It is drawn with the Settings window's kit (`settings_widgets.Kit`, its colours, fonts, pills and buttons). Any cat in it comes from `tokitty.sprites`.

## Who sees it

A new persisted field, `Settings.first_run` (string, default `""`), plus one check made in `run_gui` right after the single-instance lock is acquired and before anything else is written. Under the lock, so two near-simultaneous launches cannot both classify the dir, and a launch that loses the lock never writes the marker. Debug launches (`TOKITTY_DEBUG_STATE`, `TOKITTY_DEBUG_ACCOUNTS=2`), `--after-update` and `--apply-update` skip both the check and the write.

- `fresh = no entries in the state dir other than tokitty.lock`. Every release since v0.1.0 writes `customization.json` on every GUI launch, so a state dir with anything else in it belongs to someone who has launched tokitty before. Whether `--install-hooks` writes there on its own is not checked; if it does, those users count as existing, which is fine.
- When `fresh`, `run_gui` immediately saves `first_run = "pending"`.
- The walkthrough shows when `first_run == "pending"`. That covers a fresh launch and a launch after one where the walkthrough was killed before it finished.
- Finishing or skipping saves `first_run = "done"`.
- Edge classifications, accepted: a debug GUI launch returns before `customization.json` is saved, so someone who has only ever run debug launches counts as new. Someone who ran `--install-autostart` (writes a launcher into the state dir) or another CLI command before their first GUI launch counts as existing.
- An existing user's state dir is never fresh and their `first_run` stays `""`, so nothing ever shows for them and nothing is written for them. Downgrading to 0.3.0 ignores the unknown key (`load_settings` degrades per field).

Those excluded launch modes never show it either. An env var `TOKITTY_FIRST_RUN=1` forces it, for testing and for the manual gate.

## When it runs

The panes are built once from `load_accounts()` before `TokittyWindow` exists, and adding an account needs a restart for its pane (the Accounts dialog says so). So the walkthrough runs before the widget is built:

1. `root = tk.Tk()` as today, then `root.withdraw()`.
2. The walkthrough is a `Toplevel` of that root. `run_gui` waits on it with `root.wait_window(walkthrough.toplevel)`. Tk's event loop runs inside `wait_window`, so the walkthrough's `after` polls and its worker results work as they do in Settings.
3. When it closes (Finish, Skip, or the window's close button), `run_gui` reloads accounts and settings, computes `pane_count` from the result and builds `TokittyWindow` while the root is still withdrawn, so `overrideredirect`, topmost, geometry, opacity and the Windows colour-key content window are all applied before anything is visible. Only then is the root deiconified (and the content Toplevel shown with it). This avoids a flash of a plain Tk window or a taskbar button between the walkthrough and the widget. The Windows manual gate checks the colour-key ownership and taskbar hiding after this sequence.

The new copy holds the single-instance lock for the whole walkthrough, so a second launch says "already running" as it would for the widget.

Nothing else in `run_gui` starts before the walkthrough closes: no pollers, no `run_discovery` thread, no tray. That keeps the hook guard free for the walkthrough's installs (startup's `run_discovery` takes it for `retry_pending_hook_op` and `ensure_current`), and means `run_discovery`'s `ensure_current` afterwards sees the hooks the walkthrough just installed.

## Steps

The window is the Settings window's size and frame (`BASE_WIDTH` x `BASE_HEIGHT`, scaled by `dpi.init()`'s factor, the same left rail with the brand cat). The rail lists the steps instead of tabs and marks the current one. The footer has Skip on the left and Back / Next (or Finish) on the right. Skip on any step ends the walkthrough with whatever earlier steps already applied (see Skipping below).

### 1. Welcome

One paragraph: tokitty shows your Claude Code and Codex usage as cats on your desktop. The next two steps pick which installs to watch and turn on live activity. A Next button and Skip.

### 2. Accounts

"Looking for Claude Code and Codex…" with a spinner, while one worker runs discovery. The rows then appear, each with a checkbox, a provider label (Claude Code / Codex), where it was found ("This PC", "WSL: Ubuntu"), the path elided the way Settings ▸ Accounts elides it, and a note when it has transcripts but no sign-in ("Usage history only, no sign-in found").

Discovery, all on the worker:

- Native Claude Code: `~/.claude` when it has `.credentials.json` or a `projects` dir. `TOKITTY_CREDENTIALS` set means that dir instead.
- WSL (Windows only): `find_all_wsl_credentials()` and `find_all_wsl_claude_dirs()` through the process's `WslCredentialsCache` (`all_matches()` and the memoized `claude_dirs()` that #87 adds, so #87 merges first), merged by `(distro, config dir)`. This is more than `run_discovery` does today: it only sweeps for transcripts when it found no credentials, and the walkthrough runs both sweeps so that a distro with transcripts but no sign-in still shows up. Both run once per process and on the worker, behind the spinner. Tests count the actual `wsl.exe` probes.
- Codex: `discover_local_codex_home([])`.

Every row starts checked. The primary button reads "Add N accounts" and writes `accounts.json` with the checked rows, the same way the Accounts dialog's Add does (`assign_identity_slug` + `save_identity_history`, `absorb_implicit_default` for the first Claude row so the default look carries over, a `random_look` for the rest), but through `save_accounts` alone, with no hook install. That is a new helper next to `apply_account_mutation`, because today the only save path installs hooks in the same operation and the walkthrough asks about hooks on the next step.

Nothing found: the step says so, says Claude Code or Codex need to be installed and signed in once, and offers Skip. A single row is still shown, so the user sees what tokitty will watch.

Unchecking everything and pressing Next is the same as Skip for this step: no `accounts.json`, and the hooks step is skipped too, going straight to Done. It never falls back to the implicit default row `collect_rows` would show, because on Windows `_default_config_dir` can pick an install the user just unchecked.

Why write `accounts.json` even for one native account: on Windows with no `accounts.json`, `resolve_activity_sessions` only looks in WSL, so a native Windows install gets limits but no live activity. An explicit account fixes that for every new user.

### 3. Live activity (hooks)

Shown only when step 2 saved at least one account (on macOS, see below). One row per saved account, each with the same pill as Settings ▸ Accounts (`settings_accounts.PILLS`, from `hook_status_for_dir` on a worker) and an Install button, plus an "Install for all" primary button.

Before any Install button is enabled, a worker runs `settings_accounts.run_finish_pending` (guarded `retry_pending_hook_op`), so a walkthrough resumed after a crash mid-install is not blocked by its own journal entry. If that recovery cannot complete, the step shows the Accounts tab's `blocked_by` banner with its Finish it button.

Installs use `settings_accounts.run_hook_op(state_dir, row, "install")`, which takes the hook guard, revalidates the account, and calls `apply_hook_operation`. Results come back through a `Mailbox` and a `Poller`, exactly as in the Accounts tab. Rows re-query their status after each result. A row whose status is `unsupported` or `unreachable` shows its pill and no button.

Below the rows, the same reminders the Accounts tab and `--install-hooks` give: restart running Claude Code sessions for hooks to apply; Codex asks you to approve tokitty's hooks the next time it starts.

### 4. Done

The brand cat in its content pose, "You're set", and: right-click any cat for Settings…, where accounts, hooks, looks and the Stream Dock live. Finish closes the walkthrough and the widget appears.

## Skipping

- Skip on Welcome: nothing written except `first_run = "done"`. Same state as a 0.3.0 launch.
- Skip on Accounts (or uncheck everything): no `accounts.json`.
- Skip on Live activity: accounts from step 2 stay, no hooks are installed. Same as using Manage accounts… with a provider that has no hooks, and fixable from Settings ▸ Accounts.
- Closing the window is Skip.
- Skip and the close button are disabled while a hook install is in flight (it takes a second or two), so nothing gets installed after the user skipped. Discovery is read-only, so Skip stays enabled during it; its worker is left to finish on its daemon thread and its result is dropped (`Poller.cancel`).

## macOS

One account, and `accounts.json` mode is file-only, so step 2 shows a single read-only row and Next writes nothing. Its label comes from a worker-side probe of `resolve_credentials_source()`: "Signed in (Keychain)" or "Signed in (~/.claude)", since a local credentials file wins over the Keychain, or "Not signed in yet" when neither exists. Step 3 still applies on macOS, with the one Default row `collect_rows` builds: Claude Code's hooks go in `~/.claude/settings.json` whatever holds the credentials. (The issue says macOS has no hooks install target; the code and the review both say it has one, so this follows the code.)

## Existing users who relied on the auto-open

Today a user with no `accounts.json` and several WSL installs gets the Accounts dialog on every launch until they pick. After this change, an existing user never sees the walkthrough and nothing auto-opens. Their pane already shows the ambiguity error. That error text gains "Right-click ▸ Settings… ▸ Accounts to choose." so they still have a pointer.

The same goes for API-key users with no OAuth credentials, whom `ACTION_USAGE_SETUP` sent to Accounts with the usage section focused. Their pane shows "can't find credentials"; that text gains "Right-click ▸ Settings… ▸ Accounts to read usage from transcripts."

## New and changed pieces

- `tokitty/first_run.py`: `is_fresh_state_dir(state_dir)`, `should_show(settings, environ, flags)`, discovery (`discover_candidates(credentials_cache, ...)` returning a list of `Candidate(provider, config_dir, where, signed_in)`), and `save_picked_accounts(state_dir, picked)`. No Tk.
- `tokitty/first_run_ui.py`: `Walkthrough(root, state_dir, scale, ...)` built with `Kit`. Same threading rule as Settings: workers publish into a `Mailbox`, a Tk-side `Poller` drains it.
- `tokitty/settings.py`: `first_run` field.
- `tokitty/__main__.py`: the fresh check at the top of `run_gui`, the withdrawn-root walkthrough before `TokittyWindow`, and removal of `maybe_auto_open` and its handshake. `run_discovery` keeps the frozen-runner relink, `retry_pending_hook_op`, `ensure_current` and hook warnings, and loses `wsl_matches` / `transcript_matches`, which only fed `maybe_auto_open`.
- `tokitty/startup.py`: `resolve_first_run_action` and its constants go. `should_auto_select_models` stays.
- The ambiguity and no-credentials error texts, as above.

## Tests

- `is_fresh_state_dir`: empty dir, lock only, any other file. `should_show` across `first_run` values, the force env var and the excluded launch modes.
- Fresh launch writes `pending`. Finish and every Skip write `done`. A killed walkthrough (`pending` left behind) shows again. An existing state dir never shows it and never gains `first_run`.
- Discovery: native, env override, WSL credentials, WSL transcripts only, Codex, merging of duplicates, nothing found. Workers never call Tk (assert by thread in the fakes, as the Accounts tab tests do).
- `save_picked_accounts`: slugs and identity history, implicit look absorbed, no hook install called, nothing written for an empty pick.
- Hooks step: installs run through `run_hook_op` on a worker, pills update from the re-query, busy and blocked results show the Accounts tab's banners.
- `run_gui`: walkthrough opens before the window, `pane_count` follows what was saved, no poller or discovery thread starts before it closes, existing users go straight to the widget. The tests that stub `resolve_first_run_action` and `AccountsManager.open` are rewritten against this.
- Smoke: the gui suite opens the walkthrough under xvfb and clicks through every step with fake discovery.

## Manual gate on Windows

1. Rename the state dir aside (`%APPDATA%\tokitty` or wherever `get_state_dir` resolves), launch the frozen build: the walkthrough shows, discovery finds this PC's `~/.claude`, the WSL installs and Codex.
2. Pick two, install hooks for one, Finish: two panes, live activity on the one with hooks, `accounts.json` as expected.
3. Launch again: straight to the widget.
4. Restore the real state dir, launch: straight to the widget, `settings.json` has no `first_run` key.
5. Fresh again, kill the process on step 3: next launch shows the walkthrough again.

## Risks

- Building the window after a `wait_window` on a withdrawn root is new for this app. `dpi.init()` must still run before `tk.Tk()` (unchanged), and `TokittyWindow` must not assume the root was never withdrawn. Covered by the smoke test and the manual gate.
- The WSL sweep can take seconds per distro, and up to 10 s when `wsl.exe` hangs (#87). It runs on the worker behind a spinner, so the window stays responsive, but the user waits. Skip stays enabled during it.
- A user who ran a CLI command (`--install-hooks`, `--install-streamdock`) before ever launching the GUI may have a non-empty state dir and be treated as existing. Acceptable: they already know their way around.
