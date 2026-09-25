# Hooks groundwork implementation plan

**Goal:** Make `tokitty/hooks_install.py` provider-aware and exact about what it owns, so #48 (installable app) and #64 (Codex activity hooks) build on one seam instead of each rewriting it. No behaviour change for Claude Code users except that uninstall stops deleting a user hook that shares an entry with Tokitty's.

**Design:** `docs/superpowers/specs/2026-09-23-codex-activity-hooks-design.md` (on branch `codex-activity-hooks`, commit 4bd02e3), sections 1 to 3 and "PR A" under Tasks. Codex keeps `activity=False` throughout this PR, so no Codex home is ever written. Codex-specific shapes, `hooks.json` and the Codex event list arrive with #64, not here.

**Branch:** `hooks-groundwork`, cut from `main` at 212f941, worktree `.worktrees/hooks-groundwork`. Merge back with `git merge --no-ff`.

## Global constraints

- Baseline: `python3 -m pytest -q` passes on `main` (62 tests in `tests/test_hooks_install.py`). Every task ends with the full suite green.
- Tests use `tmp_path` only. Never touch a real `~/.claude`, `~/.codex`, or `%LOCALAPPDATA%\tokitty`.
- Public-writing rules apply to code comments and commit messages: no em-dashes, no hard-wrapped prose in docs, one concern per commit, no AI attribution lines.
- Keep names and shapes compatible with #48's branch `installable-app-design` (commits 889f251, eeac702), which already changed this file. Its notes are summarised under "Compatibility with #48" below, and every task must respect them, or #48's merge of `main` turns into a rewrite.

## Compatibility with #48

#48 has already changed `hooks_install.py` on its own branch. So that its later merge of `main` resolves mechanically:

- `_build_command(config_dir, ...)` returns a **handler dict**, `{"type": "command", "command": "<string>"}`, and install appends `{"matcher": m, "hooks": [dict(handler)]}`. This PR changes the return type to that dict. It keeps the name and the first positional argument, and adds nothing else to the signature. #48 adds `frozen=`, `platform=`, `runner=` and the exec-form `args`.
- Trailing separators on the home are stripped before joining (`/h/.claude/` gives `/h/.claude/tokitty/sessions`). Do the same here, so the two branches produce identical strings.
- `ConfigDirResult` gains `warning: Optional[str] = None`. #48 sets it for the stable-link fallback. This PR only adds the field and the places that display it.
- `get_config_dirs(state_dir=None)` returns `List[Tuple[str, str]]` of `(config_dir, provider)`. #48's `ensure_current` iterates it.
- #48 still owns: exec-form ownership, rewriting stale handlers in place, `refresh_hooks_for_dir` / `ensure_current`, collapsing duplicate owned handlers, and dropping an owned duplicate in `settings.json` when `settings.local.json` owns the event. Do not build any of those here.

## Task 1: Handler dict and provider plumbing

**Files:** `tokitty/hooks_install.py`, `tests/test_hooks_install.py`, `tests/test_accounts_ui.py` and `tests/test_main.py` only where signatures change.

**Acceptance criteria:**
- `_build_command(config_dir)` returns `{"type": "command", "command": <today's quoted python string>}`, with trailing `/` or `\` stripped from the home first. Install appends `{"matcher": m, "hooks": [dict(handler)]}`. Existing command-string tests assert on `handler["command"]`.
- A per-provider target table, one entry today:
  ```python
  @dataclass(frozen=True)
  class HookTarget:
      settings_file: str              # "settings.json"
      local_settings_file: Optional[str]  # "settings.local.json", read-only; None if the harness has none
      events: Tuple[Tuple[str, str], ...]  # (event, matcher), today's HOOK_EVENTS
  _HOOK_TARGETS = {"claude": HookTarget("settings.json", "settings.local.json", tuple(HOOK_EVENTS))}
  ```
  `_hook_target(provider)` returns the entry for `provider or DEFAULT_PROVIDER`, and raises `ValueError` for a provider that has no entry. That can only happen if a provider declares `activity=True` without a target, which is a programming error. Keep the `HOOK_EVENTS` name exported, since tests and #48 reference it.
- `install_hooks_for_dir(config_dir, provider=DEFAULT_PROVIDER)` and `uninstall_hooks_for_dir(config_dir, provider=DEFAULT_PROVIDER)` read file names and events from the target. Default argument, so existing callers and #48's code keep working.
- `get_config_dirs(state_dir=None)` returns `(config_dir, provider)` pairs for hook-enabled accounts, with `state_dir` defaulting to today's resolution. The fallback with no `accounts.json` returns `[(default_dir, DEFAULT_PROVIDER)]`.
- `install_hooks()` and `uninstall_hooks()` pass each pair's provider on.
- `apply_account_mutation` calls `fn(config_dir, provider)`. `retry_pending_hook_op` calls `fn(config_dir, provider)`, using the pending record's provider, or, for a legacy record without one, the provider of the matching account in `accounts.json` (falling back to `DEFAULT_PROVIDER`, the same lookup `_pending_dir_has_hooks` already does). Injected `install_fn`/`uninstall_fn` test doubles take two arguments.
- Tests: the builder strips a trailing separator; `get_config_dirs` returns pairs and skips a Codex account; each of the four call paths passes the provider (record it with a fake `install_fn`); a legacy pending record without a provider resolves to the account's provider.

## Task 2: Exact ownership matcher

**Files:** `tokitty/hooks_install.py`, `tests/test_hooks_install.py`.

**Acceptance criteria:**
- `_is_owned_hook(hook, config_dir, provider=DEFAULT_PROVIDER) -> bool`. For Claude it is true only for a dict with `type == "command"` whose `command` is one of the following, where `<d>` is this home's normalised native path (`_wsl_native_path(config_dir)`, trailing separators stripped) and `py` is `python` for a drive-letter home, `python3` otherwise:
  - quoted, written from d56d27f onward: `py "<d>/tokitty/hook_writer.py" --sessions-dir "<d>/tokitty/sessions"`
  - unquoted, written 2026-07-16 to 07-18 by 9bab1b3: `py <d>/tokitty/hook_writer.py --sessions-dir <d>/tokitty/sessions`

  Parse, don't substring-match. Split the command into the interpreter, script and `--sessions-dir` value (`shlex.split(posix=True)` handles both quoting forms). Then compare the script and sessions paths to `<d>` under **home normalisation**: `\` to `/`, repeated `/` collapsed, trailing `/` stripped, and case folded when the path starts with a drive letter. Accept either interpreter name for either kind of home, since the interpreter isn't what makes a hook Tokitty's.
- A hook aimed at another home, with a different `<d>`, is not owned.
- `_is_tokitty_entry(entry, config_dir, provider=DEFAULT_PROVIDER)` is true iff any hook in the entry is owned. `_events_with_tokitty_entries(data, config_dir, provider=...)` passes both on. Every caller passes `config_dir`. `MARKER` stays exported but is no longer used for matching.
- Ownership is a yes or no answer only. This task never rewrites anything, so an owned hook with an equivalent spelling stays byte-identical.
- Fixtures: `test_install_skips_event_already_marked_in_settings_local` and `test_uninstall_leaves_settings_local_alone_but_reports` use `python3 x/tokitty/hook_writer.py` with no `--sessions-dir`, which an exact matcher rejects. Switch both to a command Tokitty really wrote for that fixture's home, and add a separate test showing that the partial lookalike is **not** owned.
- Tests: both historical forms are owned; `C:\Users\Nick\.claude` hooks stay owned when the account says `c:\users\nick\.claude`; a doubled slash (`/h/.claude//tokitty/sessions`) is owned; `python3 /opt/tokitty/myhook.py` and `bash ~/tokitty-scripts/run.sh` are not owned; the same command aimed at `/other-home` is not owned; `type: "prompt"` is not owned; a WSL UNC home matches a hook written with its POSIX path.

## Task 3: Handler-level uninstall

**Files:** `tokitty/hooks_install.py`, `tests/test_hooks_install.py`.

**Acceptance criteria:**
- Uninstall removes only owned handlers from each entry's `hooks` list. It drops an entry only if its `hooks` list ends up empty, and drops an event only if it ends up with no entries. The user's handlers, the entry's `matcher`, and any other keys on the entry are kept, in their original order.
- `installed_events` in the result still lists the events where something was removed. The `settings.local.json` note and the backup before writing are unchanged.
- Tests: a user handler inside Tokitty's entry survives with its matcher; an entry containing only Tokitty's handler is dropped; an event left with no entries is dropped; a user entry before and after Tokitty's keeps its position; nothing is written and no backup is made when nothing is owned.

## Task 4: A warning on successful results

**Files:** `tokitty/hooks_install.py`, `tokitty/accounts_ui.py`, `tests/test_hooks_install.py`, `tests/test_accounts_ui.py`.

**Acceptance criteria:**
- `ConfigDirResult.__init__` takes `warning: Optional[str] = None` and stores it. Nothing in this PR sets it except tests.
- `install_hooks()` and `uninstall_hooks()` print `f"{config_dir}: warning: {result.warning}"` to stderr when a result is ok and has a warning. The exit code is unchanged.
- `accounts_ui.py`: in `_finish_mutation`, an ok outcome with a warning shows `messagebox.showwarning("Accounts", outcome.warning, parent=self.toplevel)`. The pending-retry completion path (`_poll_retry_done`) does the same when the retried outcome is ok and has a warning. Failure paths are unchanged.
- Tests with a fake `messagebox`, in the style the file already uses: an ok result with a warning shows it once, an ok result without one shows nothing, and a failed result still shows only the error. A CLI test captures stderr for an ok-with-warning result.

## Execution order and review gates

Tasks run in order, each by a fresh subagent that implements, runs the full suite, and commits (one commit per task, message in the imperative, no attribution). After Task 4, send the branch diff against `main` to Codex (`gpt-6-sol`, high, read-only) for review before opening the PR. The brief should include this plan, the #48 notes above, and the #64 spec sections 1 to 3. Nick approves before push and before the PR.
