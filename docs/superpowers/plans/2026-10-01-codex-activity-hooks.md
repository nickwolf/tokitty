# Codex activity hooks implementation plan (#64, PR C)

**Goal:** Live poses for Codex accounts. Tokitty installs its hook writer into a Codex home's `hooks.json`, reports whether the user has approved those hooks in Codex's own review, keeps the command stable across updates, and teaches the writer and tracker Codex's `PermissionRequest` and `Interrupt` events.

**Design:** `docs/superpowers/specs/2026-09-23-codex-activity-hooks-design.md`, re-checked 2026-10-01 against the merged PR A (#65) and #48 (PR #68). This plan covers spec tasks 1, 5, 6 and 7, plus the per-provider pieces PR A deferred: the Codex `HookTarget`, the two Codex command shapes in `_is_owned_hook`, and the uninstall shift warning. Spec task 8 is Nick's manual gate and is listed last.

**Branch:** `codex-activity-hooks`, rebased onto `main` at de646c1, worktree `.worktrees/codex-activity-hooks`. Merge back with `git merge --no-ff`, never squash.

## Global constraints

- Baseline on the rebased branch: `python3 -m pytest -q` gives 1052 passed, 3 skipped. Every task ends with `python3 -m pytest -q`, `xvfb-run -a python3 -m pytest -q -m gui` and `ruff check .` all clean. The GUI suite has a known intermittent flake from 2 s `_pump_until` timeouts: re-run a failing GUI test alone before treating it as real.
- One commit per task. Commit messages follow the repo's style (imperative, sentence case, no prefix). No em-dashes anywhere, no AI attribution lines, no hard-wrapped prose in docs.
- Tests use `tmp_path` only. Never touch a real `~/.claude`, `~/.codex`, `%LOCALAPPDATA%\tokitty` or `C:\Users\nickw\.codex`.
- Tokitty never writes `config.toml`. Nothing in this plan writes, creates or edits it, and tests assert that.
- Claude behaviour does not change, with one deliberate exception from the spec: `PERMISSION_STALE_S` (Task 1) applies to every provider, so a Claude permission overlay left for 10 minutes now drops to idle instead of lasting until `GONE_S`. Every other Claude test that passes on the baseline passes unchanged. A test may only be edited where a private signature changes, and the edit must not change what it asserts.
- `CodexProvider.capabilities.activity` stays `False` until Task 5. Tasks 2 to 4 are reachable only by calling `hooks_install` functions with `provider="codex"` directly, which is how their tests drive them.
- Codex facts are from openai/codex at `rust-v0.156.1` and the spike against codex-cli 0.156.1. Where this plan quotes one, it is checked: the handler timeout field is `"timeout"` in seconds (`config/src/hook_config.rs`, `#[serde(rename = "timeout")]`); `Interrupt` and `SessionEnd` default to 1 s and clamp to 3 s (`hooks/src/engine/discovery.rs`, `normalize_command_hook`); `MatcherGroup.matcher` is `Option<String>`; the trust key is `format!("{key_source}:{event_snake}:{group_index}:{handler_index}")` (`hooks/src/lib.rs`, `hook_key`).

## Seams in the merged code

These are the names the tasks build on. Read them before starting any task.

- `hooks_install.HookTarget(settings_file, local_settings_file, events)` and `_HOOK_TARGETS = {"claude": ...}`, looked up by `_hook_target(provider)`.
- `_build_command(config_dir, *, frozen=None, platform=None, runner=None) -> dict`. Frozen and not a WSL home seen from Windows gives the exec form (`command` = runner, `args` = `["--sessions-dir", s]`). Otherwise the Python string.
- `_is_owned_hook(hook, config_dir, provider)`, `_is_owned_exec_hook`, `_command_owned_from_parts`, `_normalize_home_path`, `_normalize_token_path`.
- `_reconcile_claude(config_dir, provider, add_missing)` behind `_RECONCILE_TABLE = {"claude": _reconcile_claude}` and `_reconcile`. `install_hooks_for_dir` is `add_missing=True`, `refresh_hooks_for_dir` is `add_missing=False`. `ensure_current` iterates `_config_dirs_from_accounts_file` and skips a provider not in `_RECONCILE_TABLE`.
- `_handler_needs_rewrite(old, desired, *, refresh)`: two handlers without `args` are never a rewrite, and a refresh never demotes exec form to the Python string.
- `_merge_handler` keeps any extra key the old handler carried (such as `timeout`) and overwrites `type`, `command`, `args`.
- `ensure_runner_link`, `stable_runner_path(state_dir, platform)`, `hook_runner_path(executable, platform)`, `LINK_FALLBACK_WARNING`.
- `ConfigDirResult(config_dir, ok, message, installed_events, warning, refreshed_events, note)`. `run_discovery` in `__main__.py` already gathers `warning` (and `message` on failure) from `retry_pending_hook_op` and every `ensure_current` result into one deduplicated startup messagebox. #48 handled the retry warning that used to be discarded. `install_hooks()` prints `note` and `warning`. The Accounts dialog's `_finish_mutation` shows `warning` in a `showwarning` box and ignores `note`.
- `uninstall_hooks_for_dir` already removes only owned handlers and drops a group only when it empties (PR A).
- `providers/codex.py`: `codex_home_for(config_dir) -> (home, distro)`, `CodexProvider.capabilities`, `CodexProvider.resolve_activity_sessions` (today `no_directory`). `providers/claude.py`'s module-level `resolve_activity_sessions` is the model for path handling.
- `activity.py`: `_KNOWN_EVENTS`, `ActivityTracker.observe`, `_effective_view`, `GONE_S`. `hook_writer.py`: its own `_KNOWN_EVENTS`.
- `wsl_probe.list_running_distros()` is what `ActivityWatcher` uses for its running-distro check.

## Changes from the spec

Recorded so a reviewer can see what the merged code forced:

- The "Codex branch of `ensure_current`" is a Codex entry in `_RECONCILE_TABLE`, served by `_reconcile_claude` generalised into `_reconcile_hooks`, not a copy of it.
- `_handler_needs_rewrite`'s "two strings are never a rewrite" rule is Claude-only. Codex compares parsed shapes and `timeout` (Task 2).
- Startup does not report a pending first approval, only a rewrite that invalidated one. The persistent status lives in the Accounts row and the install result (Task 4).
- Hooks inline in `config.toml` are out of scope (spec, Out of scope).

## Task 1: Writer and tracker events (spec task 7)

**Files:** `tokitty/hook_writer.py`, `tokitty/activity.py`, `tests/test_hook_writer.py`, `tests/test_activity.py` (use the existing test files for these modules; create none if they exist under another name).

**Acceptance criteria:**
- `hook_writer._KNOWN_EVENTS` gains `PermissionRequest` and `Interrupt`. The writer stays harness-neutral: no provider logic.
- `activity._KNOWN_EVENTS` gains both.
- `PermissionRequest` raises the permission overlay exactly as `Notification` does: no change to `base_state`, the stretch or the tool label. The line `state.permission = event == "Notification"` becomes membership in a `_PERMISSION_EVENTS = frozenset({"Notification", "PermissionRequest"})`.
- `Interrupt` sets `base_state = "idle"`, clears `stretch_start`, `tool_label` and `done_hop_at`, and never sets `done_hop`. It lowers the overlay like any other non-permission event.
- `PERMISSION_STALE_S = 600.0`, module level beside `GONE_S`. In `_effective_view`, a session whose `permission` is set and whose `now - last_ts >= PERMISSION_STALE_S` is viewed as idle (not as its `base_state`), for any provider. Update the comment there that says the overlay lasts until `GONE_S`.

**Tests:**
- Writer: a `PermissionRequest` payload and an `Interrupt` payload each produce a state file with that event; an unknown event still produces nothing.
- Tracker: `PreToolUse`, `PermissionRequest`, `Interrupt` gives idle with no hop; `PreToolUse`, `PermissionRequest`, `PostToolUse` gives thinking; `PermissionRequest` alone is permission at 599 s and idle at 600 s; a stretch longer than `WORK_STRETCH_MIN_S` ended by `Interrupt` never hops; `Notification` behaviour is unchanged (existing tests stay green).

## Task 2: Codex hook target, command, ownership and reconcile (PR A's deferred pieces, spec task 6)

**Files:** `tokitty/hooks_install.py`, `tests/test_hooks_install.py` (new tests may go in `tests/test_hooks_install_codex.py`).

**Acceptance criteria:**

*Target.*
- `HookTarget` gains `timeouts: Tuple[Tuple[str, int], ...] = ()` and `exec_form: bool = True`. An event whose matcher is `None` gets a group with no `"matcher"` key. Claude's entry is unchanged in effect (its matchers stay `""` and are still written).
- `_HOOK_TARGETS["codex"] = HookTarget("hooks.json", None, CODEX_EVENTS, timeouts=(("Interrupt", 3), ("SessionEnd", 3)), exec_form=False)`, with `CODEX_EVENTS = (("UserPromptSubmit", None), ("PreToolUse", None), ("PostToolUse", None), ("PermissionRequest", None), ("Stop", None), ("SubagentStop", None), ("Interrupt", None), ("SessionEnd", None))` exported.

*Command.*
- `_build_command(config_dir, *, frozen=None, platform=None, runner=None, provider=DEFAULT_PROVIDER)`. Claude output is byte-identical to today for every existing combination. For Codex:
  - not frozen, or frozen on win32 with a WSL UNC home: the same Python string Claude gets.
  - otherwise: `f'"{runner_path}" --sessions-dir "{sessions_dir}"'`, a single string, no `args`, where `runner_path` is `runner` if given, else `stable_runner_path(state_dir_path(), platform)`.
- The reconcile adds `"timeout": n` to the Codex handler for an event listed in `timeouts`, and to no other.
- A helper `codex_paths(config_dir) -> (filesystem_hooks_json, codex_visible_hooks_json)`: the first from `_local_config_path`, the second from `_wsl_native_path` (so a `\\wsl.localhost\<distro>\home\u\.codex` home gives `/home/u/.codex/hooks.json` and `C:\Users\u\.codex` stays a drive-letter path). Used by Task 4 for trust keys.

*Ownership.* `_is_owned_hook(hook, config_dir, "codex")` is true only for a `type == "command"` dict with no `args` key whose `command` is one of:
- source: interpreter `python` or `python3`, then this home's `tokitty/hook_writer.py`, `--sessions-dir`, this home's `tokitty/sessions` (reuse `_command_owned_from_parts` and the existing shlex-then-whitespace parsing).
- frozen: exactly three tokens after `shlex.split(posix=True)`: a path whose basename case-folds to `tokitty-hook` or `tokitty-hook.exe` (folder never checked, as in `_is_owned_exec_hook`), `--sessions-dir`, and this home's `tokitty/sessions` under `_normalize_token_path`.

A Codex hook with an `args` key, a user hook whose path merely contains "tokitty", a hook aimed at another home, and an unbalanced quote are not owned. Claude ownership is unchanged.

*Reconcile.*
- `_reconcile_claude` is renamed `_reconcile_hooks` and driven by the target: it skips `local_settings_file` handling when it is `None`, takes the exec-form branch (link, fallback, re-read) only when `target.exec_form` is true **or** the Codex frozen runner shape applies, and builds the desired handler through `_build_command(..., provider=provider)` plus the target's timeout. `_RECONCILE_TABLE = {"claude": _reconcile_hooks, "codex": _reconcile_hooks}`. The link work (`ensure_runner_link`, `AppTranslocatedError`, `FileNotFoundError`, `OSError` fallback with `LINK_FALLBACK_WARNING`, the fresh re-read after the lock wait) runs for a frozen Codex string command exactly as for Claude's exec form, and the stable/fallback choice per event (`desired_for`, `choose_primary`) works on the runner token of the Codex string.
- Install appends a new group `{"hooks": [handler]}` at the end of the event's list. Never inserts, never reuses a user group.
- Codex rewrite rule, a new `_codex_handler_needs_rewrite(old, desired, *, refresh)`: parse both commands into `(kind, runner, sessions)` where kind is `python` or `runner` (interpreter name ignored, paths under `_normalize_token_path`); rewrite when kind, runner or sessions differ, or when `old.get("timeout") != desired.get("timeout")`. `_handler_needs_rewrite` dispatches to it for Codex and is unchanged for Claude.
- No-demote with timeout repair. Under `refresh=True`, when the old handler is `runner` kind and desired is `python` kind, the effective desired handler is the old command string with the target's timeout: the runner is kept, and only a wrong or missing `timeout` is corrected. So a source launch never swaps a frozen install's command, but still repairs its timeout.
- A Codex rewrite merges with `_merge_handler`, so a user-added key on Tokitty's handler survives, then sets or removes `timeout` to match the target.
- An owned handler whose final `(group, handler)` index differs from where it started (because duplicate collapse removed an earlier owned handler, possibly keeping a later stable-path one) counts as a rewrite for trust purposes, even if its command is unchanged: Codex looks up the old hash at the new key.
- When any Codex handler was rewritten or moved (not just added), `warning` is set to "Codex will ask you to approve Tokitty's hooks again." If a link fallback warning is also set, both sentences are kept, link warning first.

**Tests:**
- `_build_command` for Codex: source on Linux, source on a drive-letter home (`python`), frozen win32 native home (quoted stable `.exe` path), frozen win32 WSL UNC home (`python3` string), frozen Linux (quoted stable path), an explicit `runner` containing a space is quoted as one token. Every existing Claude `_build_command` test is untouched.
- A Windows-only test (`skipif(sys.platform != "win32")`) that builds a Codex command whose runner is a `.cmd` file in a directory with a space, runs it with `subprocess.run(["cmd", "/C", f'"{command}"'])` the way Codex does, and asserts the `.cmd` saw `--sessions-dir` and the sessions path as separate arguments.
- Ownership: both Codex shapes owned; quoted and unquoted source shapes; stable, fallback and old-release runner paths owned; an `args` key not owned; another home not owned; a user command containing "tokitty" not owned; the Claude exec form passed with `provider="codex"` not owned.
- Install into a Codex home: a fresh home writes `hooks.json` with `{"hooks": {...}}`, the eight events, each a single new group with no `matcher` key, `timeout: 3` on exactly `Interrupt` and `SessionEnd`; `settings.json` is never created; `config.toml` is never created or modified; an existing user `SessionStart` group and a user `PreToolUse` group are left byte-identical and Tokitty's group lands after the user's; a non-object `hooks` key aborts; a second install is a no-op that writes nothing.
- Refresh: source to frozen rewrites in place at the same index and warns; a moved stable path (different folder, same basename) rewrites and warns; identical is a no-op with no warning; a frozen handler refreshed from a source launch is not demoted; a missing `timeout` on `SessionEnd` is restored and warns; a refresh never adds a missing event; a frozen runner `SessionEnd` handler missing its `timeout`, refreshed from a source launch, keeps its runner command and gains `timeout: 3`; approved owned duplicates at indices 0 and 1 where index 1 is the stable path collapse to one handler at index 0, warn, and Task 4's status for it reads needs approval; uninstall followed by `ensure_current` leaves the home uninstalled; frozen with a failed link writes the release-folder runner and sets `LINK_FALLBACK_WARNING`.

## Task 3: Shift warning on uninstall and duplicate collapse (spec section 3)

**Files:** `tokitty/hooks_install.py`, its tests.

**Acceptance criteria:**
- A pure helper `_shifted_events(before_hooks, after_hooks, config_dir, provider) -> List[str]`: an event is listed when some non-owned handler's `(group_index, handler_index)` in `after` differs from its position in `before`. Identify handlers by object identity from the `before` list (the rebuild keeps untouched handler objects), not by value, so two identical user handlers are still told apart.
- For Codex only, `uninstall_hooks_for_dir` and a reconcile that collapsed duplicate owned handlers set `warning` to "Codex will ask you to approve these hooks again: <events, comma-separated, in event order>." Claude never gets this warning.
- Uninstall keeps removing only the owned handler, and the group only when it empties (already true, keep it so).

**Tests:** for Codex, a user group after Tokitty's in the same event is warned about with that event named; a user handler after Tokitty's inside Tokitty's group is warned about; a user group before Tokitty's gives no warning; a user handler inside Tokitty's group survives uninstall; two events shifting are both named in order; the same layout under Claude gives no warning; a refresh collapsing a duplicate owned Codex handler ahead of a user group warns.

## Task 4: Trust check and where it shows (spec tasks 5 and section 4)

**Files:** new `tokitty/codex_trust.py`, `tokitty/hooks_install.py`, `tokitty/accounts_ui.py`, tests for each (GUI tests for the dialog under the `gui` marker).

**Acceptance criteria:**

*Reading `config.toml`.*
- `read_trusted_hashes(config_toml_path) -> Optional[Dict[str, str]]`: every `[hooks.state.'<key>']` table's `trusted_hash`. Uses `tomllib` when importable. Otherwise a line parser that matches only `[hooks.state.'...']` and `[hooks.state."..."]` headers (single-quoted literal keys are taken verbatim, so backslashes survive; double-quoted keys have `\\` and `\"` unescaped) and a `trusted_hash = "..."` line before the next header. A missing file gives `{}`. An unreadable or unparseable file gives `None` ("can't read Codex hook state"), which never raises and never blocks install or uninstall.
- Trust keys are split with `rsplit(":", 3)` into path, event, group, handler. A key matches one of Tokitty's handlers when the path equals `codex_paths(config_dir)[1]` under `_normalize_home_path`, the event equals the snake-case name (`user_prompt_submit`, `pre_tool_use`, `post_tool_use`, `permission_request`, `stop`, `subagent_stop`, `interrupt`, `session_end`), and the indices equal the handler's position.

*Recording.*
- `<state dir>/codex_hook_trust.json`: `{"<normalised codex-visible hooks.json path>": {"written_at": <epoch>, "keys": {"<trust key>": "<hash>" | null}}}`, written atomically (temp file, `os.replace`). Every Codex install or rewrite records, for each owned handler it added, rewrote or moved (Task 2), the `trusted_hash` currently found under that handler's **final** key (or `null`), and sets `written_at`. Handlers it left alone keep their recorded value. Uninstall removes the home's record.
- Order: read `config.toml`, write the trust record, then write `hooks.json`. If the record write fails, the reconcile aborts with `ok=False` and `hooks.json` is not touched, since an unrecorded rewrite would later read as approved while Codex skips it. If `hooks.json` then fails to write, the record errs safe: its values equal the live hashes, which reads as needs approval.
- Task 2 lands before this task, so Task 2's reconcile gets a no-op hook point (a `_record_codex_trust(config_dir, changes)` stub called before the write) that this task fills in.

*Status.*
- `codex_hook_status(config_dir, state_dir) -> Optional[str]` returns `"needs_approval"`, `"approved"`, `"unreadable"`, or `None` when no owned handler exists. Per owned handler: no key, needs approval; key present and equal to the recorded value, needs approval; key present and different, or no record, approved. Any handler needing approval makes the home `"needs_approval"`.
- `codex_activity_hint(config_dir, state_dir) -> bool`: true when status is `"approved"` and the sessions directory's mtime is not newer than `written_at` (a missing directory counts as no activity).
- Distro gate: for a WSL home on win32, both functions read nothing and return `None` / `False` unless the distro is in `wsl_probe.list_running_distros()`. The function takes `list_running_distros_fn` for tests.

*Startup gate.* On win32, a Codex account whose home is a WSL UNC path is skipped by `ensure_current` and by `retry_pending_hook_op` unless its distro is in `list_running_distros()` (injectable for tests). A skipped pending op stays recorded for the next launch. Opening `\\wsl.localhost` starts a stopped distro, which the spec forbids for Codex. Claude's existing behaviour is unchanged.

*Surfaces.*
- A Codex install whose status afterwards is `"needs_approval"` sets `note = "Hooks installed, waiting for approval in Codex. Start codex and approve the Tokitty hooks."`. `"unreadable"` sets `note = "Hooks installed, but Tokitty can't read Codex hook state to check approval."`.
- `install_hooks()`'s closing line names Codex when any Codex home was processed: approve the hooks in `codex`'s hook review, and restart running Codex sessions.
- Accounts dialog: `_finish_mutation` shows a successful outcome's `note` in a `showinfo` box when there is no `warning` (with a warning, append the note to the warning box instead of opening two). The Codex row's fact line gains "hooks: waiting for approval in Codex", "hooks: approved", "hooks: approved, no activity from Codex hooks yet" or "hooks: can't read Codex hook state", and nothing when no hooks are installed. Computed only for a local home, under the same rule as `_local_codex_facts` (nothing for a WSL UNC home on win32).
- Startup shows nothing new: the rewrite warning from Task 2 already reaches it.

**Tests:** `read_trusted_hashes` with `tomllib` and with it forced unavailable (monkeypatch the import), against a fixture copied from the spike's `config.toml` layout (backslash keys, an empty `[hooks.state]` table, unrelated tables before and after); a missing file; an unreadable file gives `None`. Status: no `config.toml`; a missing key; a key equal to the recorded value; a key that differs; no record (older build); a WSL home's POSIX key matched from a UNC `config_dir`; a drive-letter key in different case; a stopped distro not read (the fake `list_running_distros_fn` is called, the file is not opened); `ensure_current` and `retry_pending_hook_op` skip a stopped-distro Codex UNC home and keep its pending op; a trust-record write failure aborts with `hooks.json` byte-identical. Recording: install records `null` for an unapproved home, records the old hash when rewriting over an approved key, and `config.toml` is byte-identical before and after every install, refresh and uninstall. Hint: approved with no sessions dir; approved with a sessions dir touched after `written_at`. Dialog (gui): the note box after a Codex add; the row's approval text for a local Codex home; no hook text for a Claude row.

## Task 5: Turn on Codex activity (spec task 1)

**Files:** `tokitty/providers/codex.py`, `tests/test_providers_codex.py` (or the existing Codex provider test file), `README.md`.

**Acceptance criteria:**
- `CodexProvider.capabilities = ProviderCapabilities(rate_limits=True, token_ledger=True, activity=True)`, with the class docstring updated.
- `CodexProvider.resolve_activity_sessions(config_dir, credentials=None)` returns `(sessions_dir, distro)` for `<home>/tokitty/sessions`, using `codex_home_for`, in the same separator style as the home (UNC stays UNC on win32 with the distro name; Linux maps a UNC home to its POSIX form as Claude's resolver does). No `config_dir` uses the default Codex home with no distro.
- `provider_has_hooks("codex")` is now true, so `get_config_dirs`, `ensure_current`, `apply_account_mutation` and `retry_pending_hook_op` include Codex accounts with no further change. Check the pending-op legacy path (`_pending_dir_has_hooks`) still never replays a Claude install into a Codex home.
- README: Codex accounts get live poses after approving Tokitty's hooks in the Codex CLI's hook review; approval happens in the CLI, and the desktop app and IDE extension are untested.

**Tests:** capability flag; sessions dir for a local POSIX home, a drive-letter home, a UNC home on win32 (distro returned), a UNC home on Linux (POSIX path); `get_config_dirs` now returns a Codex pair; `apply_account_mutation` for a Codex account writes `hooks.json` and never `settings.json`; a legacy pending install record for a former Codex home is still not replayed.

## Task 6: Manual gate (Nick, spec task 8)

Not a subagent task. The checklist is handed to Nick after Task 5 and the full-diff review.

## Execution order and review gates

1. Task 1, then Tasks 2, 3, 4, 5 in order. Each task is one fresh Sonnet subagent, given this plan, the spec, and the task number. It runs the three checks in Global constraints and commits once.
2. After each task the controller reads the diff against the acceptance criteria before dispatching the next.
3. After Task 5, the full diff `main..codex-activity-hooks` goes to Codex (gpt-6-sol, high effort, read-only) with a cold-start briefing, or to an Opus subagent if Codex is out of usage. Findings are checked against the code before any fix.
4. Push and PR only after Nick approves. The PR body follows `/mnt/c/Tools/docs/conventions/public_writing_playbook.md`.
