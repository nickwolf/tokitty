# Codex activity hooks (#64)

Status: design only, 2026-09-23. Nothing here is built. The installer groundwork ships first as its own PR, then #48, then the rest of this issue, because the hook command on a frozen install depends on #48's stable runner path (see Tasks). Decided with Nick the same day: Tokitty never writes Codex trust itself (the user approves hooks in Codex's own review), and #48 registers its hook runner at a stable per-user path that survives releases. Codex facts come from the openai/codex source at tag `rust-v0.156.1` and a live spike against codex-cli 0.156.1 (Windows build) on Cucumber, using a scratch `CODEX_HOME` and `gpt-6-luna`.

## Recommendation

- Give `CodexProvider` the activity capability and install the same `hook_writer.py` into `<CODEX_HOME>/hooks.json`, with the state files in `<CODEX_HOME>/tokitty/sessions`. The payloads already carry everything the writer needs.
- Install eight events: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop`, `SubagentStop`, `Interrupt`, `SessionEnd`. Give `Interrupt` and `SessionEnd` an explicit `timeout` of 3, the most Codex allows for them.
- Teach the writer and the tracker two new events. `PermissionRequest` raises the permission overlay, the way `Notification` does for Claude. `Interrupt` ends the turn with no hop, and lowers the overlay.
- Leave trust to Codex. Tokitty reads `config.toml` only to tell the user that review is still needed, and never writes it.
- Append Tokitty's entries after any existing ones, and warn on uninstall when that would shift a user hook's trust key.

## What the spike found

Everything below was observed live unless it is marked as coming from source. Raw notes and screens are in the spike notes, which are not committed. The preserved payload log holds only the last run. Earlier payloads are quoted in the notes, and the permission and denial sequence was captured once cleanly.

**The payload fits the writer as is.** From source: every Codex hook input schema requires `session_id` and `hook_event_name`. Observed: tool events carry `tool_name` (`"Bash"` for a shell call), `tool_input` and `tool_use_id`. For one `codex exec` with one shell command, the events fired in the order `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`. `session_id` was the same on every event and equal to the thread UUID in the rollout file name. The unmodified `hook_writer.py` created its state file and deleted it on `SessionEnd`, but only because five of its seven known event names happen to match Codex's. `PermissionRequest`, `Interrupt`, `SessionStart`, `SubagentStart`, `PreCompact` and `PostCompact` were dropped silently.

**`hooks.json` wraps the events.** The file is `{"hooks": {"<Event>": [group, ...]}}`, the same shape as the `hooks` key in Claude Code's `settings.json`. A bare event map at the top level is a parse error.

**Untrusted hooks are skipped with no output at all.** Without trust entries and without `--dangerously-bypass-hook-trust`, no hook ran, and nothing about skipped hooks appeared on stdout or stderr of `codex exec`. The bypass flag never writes to `config.toml`.

**Review happens in the TUI.** At startup, after folder trust, the TUI says "Hooks need review" with a count, then shows a table per event and a detail screen per hook (source, command, mode, timeout, trust). Approving writes `[hooks.state.'<absolute hooks.json path>:<event_snake_case>:<group index>:<handler index>']` with `trusted_hash = "sha256:..."` to `config.toml` immediately. The spike's 24 hooks (12 events, two hooks each) meant 24 approvals. Tokitty's eight events mean eight.

**The hash covers the command, not the script.** Adding a dummy argument to one command string dropped that hook silently, and reverting the string restored it with no new review. Appending a line to the script file itself, with the command unchanged, left it running. So updating the copied `hook_writer.py` costs the user nothing, and changing the command string costs a full review. This is why #48 now registers a stable runner path.

**Permission and denial.** `PermissionRequest` fires while the approval dialog is on screen, before the user answers, and a hook that prints nothing leaves the dialog unchanged. After approval come `PostToolUse` and `Stop`. After denial neither fires. Codex fires `Interrupt` instead and shows "Conversation interrupted". Esc during a plain text reply also fires `Interrupt`, and `Stop` never fired alongside it.

**`Interrupt` and `SessionEnd` run on a 1 second default timeout.** Source: both default to 1 s and are clamped to 3 s. In the spike, `/quit` lost both `SessionEnd` hooks to "hook timed out after 1s", and one of two `Interrupt` attempts lost both hooks the same way, while Ctrl-C's `SessionEnd` succeeded. `reason` was `"other"` on every `SessionEnd`. Ctrl-D exited normally but produced neither a payload nor an error, which the spike left unresolved.

**A running session keeps the hooks it started with.** Editing `hooks.json` mid-session changed nothing in the running process. Only a new `exec` or TUI launch reads the file again.

**Latency.** 20 warm runs each, real payload on stdin, state file checked:

| Path | Min | Median | Max |
|---|---|---|---|
| Windows, `cmd /C python hook_writer.py` (how native Codex runs it) | 65.9 ms | 71.5 ms | 133.3 ms |
| WSL, `python3 hook_writer.py` direct | 26.5 ms | 27.3 ms | 32.2 ms |
| WSL, `bash -lc "python3 hook_writer.py"` (how Codex runs it on POSIX) | 66.8 ms | 71.2 ms | 87.2 ms |

Both real paths have a median inside the 100 ms gate from `docs/hook-preflight-2026-07-16.md`. None of these explains the 1 s timeouts, which came from live sessions with two hooks per event on a freshly touched `%TEMP%` scratch home. Cold start after idle, Defender scanning, and the two hooks running back to back are all candidates, and none was confirmed.

## Design

### 1. Provider and paths

`CodexProvider.capabilities` gains `activity=True`, and `resolve_activity_sessions` returns `<CODEX_HOME>/tokitty/sessions`, using `codex_home_for` so a WSL Codex home gets its distro name for the running-distro check, the same as a WSL Claude home. `provider_has_hooks` then lets Codex accounts through everywhere it is already consulted (startup, the Accounts dialog, pending hook ops).

Letting them through is not enough on its own. Today every caller of the directory functions passes only a path: `install_hooks` and `uninstall_hooks` loop over `get_config_dirs()`, which returns bare strings, and `apply_account_mutation` and `retry_pending_hook_op` call `fn(config_dir)` even though they hold the provider. With `activity=True` and nothing else changed, a Codex home would get a Claude `settings.json` with Claude's events, and the install would report success. So `get_config_dirs` returns `(config_dir, provider)` pairs, and the provider is passed through every call, including pending-op replay. A pending op recorded without a provider (older builds) keeps today's fallback of looking the account up in `accounts.json`.

Each Codex home has two path forms. The filesystem path is the one Tokitty opens (`\\wsl.localhost\<distro>\...` from Windows for a WSL home, a plain path otherwise). The Codex-visible path is the one Codex itself uses, and it is what appears in trust keys (`/home/<user>/.codex/hooks.json` for a WSL home, `C:\Users\...\.codex\hooks.json` for a native Windows one). Section 4 builds trust keys from the Codex-visible form. Using the UNC form would find no keys and report "waiting for approval" forever. `_wsl_native_path` already does this conversion for hook commands. Every read of a WSL Codex home, including the `config.toml` read in section 4, is gated on the distro already running, as the activity watcher is, so Tokitty never starts a stopped distro by probing it.

### 2. Installing into `hooks.json`

`install_hooks_for_dir` and `uninstall_hooks_for_dir` take the provider and pick the file: `settings.json` plus a read-only `settings.local.json` for Claude, `hooks.json` alone for Codex. The JSON under the `hooks` key has the same shape in both, so the entry handling is shared. Codex also accepts hooks inline in `config.toml`. Tokitty never writes there, and treats an owned-looking entry it finds there the way it treats `settings.local.json` today: counted as installed, reported, never rewritten.

The event list becomes per provider. Codex gets the eight events above, with no matcher, and `timeout: 3` on `Interrupt` and `SessionEnd`. The timeout is part of the hashed config, so it has to be right the first time. Changing it later means another review.

The command follows #48's table, with one Codex difference: Codex has no exec form, so the entry is always a single `command` string, run by `cmd /C "<command>"` on Windows and `$SHELL -lc "<command>"` elsewhere. For a source install that is today's `python`/`python3 "<dir>/tokitty/hook_writer.py" --sessions-dir "<dir>/tokitty/sessions"`. For a frozen install on the same OS as the Codex home, it is the quoted stable `tokitty-hook` path from #48 followed by the same `--sessions-dir` argument. Codex wraps the whole string in one extra pair of quotes on Windows, which `cmd /C` strips, so a quoted first token survives. That was exercised only with a path free of spaces. A path containing spaces needs a test.

**Ownership and refresh for Codex.** #48 narrows ownership to the exact shapes Tokitty writes for Claude: the historical Python string and an exec-form `command` plus `args`. Codex never gets the exec form, so its owned shapes are two single strings:

- source: `python`/`python3 "<dir>/tokitty/hook_writer.py" --sessions-dir "<dir>/tokitty/sessions"`
- frozen: `"<stable path>/tokitty-hook[.exe]" --sessions-dir "<dir>/tokitty/sessions"`

Here `<dir>` is the Codex-visible home, and the `--sessions-dir` value must match it exactly. Anything else, including a user hook whose path contains "tokitty", is not ours. #48's `ensure_current` gains a Codex branch that follows the same rules as for Claude: it rewrites an owned handler whose string differs, in place, and never adds one. Every such rewrite changes the hash, so it is reported as "Codex will ask you to approve Tokitty's hooks again" (section 4 tracks it). Switching between the source and frozen installs is a rewrite of this kind and costs one review.

This depends on #48 making the stable path exist before anything writes a command that names it. The junction or symlink has to be repointed before `--install-hooks`, before the Accounts dialog installs, and before the startup refresh. If #48 falls back to the real release folder because the link can't be made, the command stops being stable. Every update then costs a Codex review, and the UI has to say so rather than fail silently.

### 3. Trust keys and entry order

A Codex trust key is `<Codex-visible hooks.json path>:<event_snake_case>:<group index>:<handler index>`, with the event in Codex's snake case (`pre_tool_use`, `session_end`, and so on), so it depends on position. Removing a group shifts every later group in the same event down by one. Removing a handler shifts every later handler in the same group. Either way, the shifted hooks come back as untrusted.

- Install appends a new group containing only Tokitty's handler at the end of each event's list, so installing never moves a user hook.
- Uninstall removes only the owned handler, not the group around it, and removes the group only if that leaves it empty. Today's `uninstall_hooks_for_dir` drops any whole entry that contains a Tokitty hook, which would delete a handler the user added to Tokitty's group. That changes for both providers. For Codex, if any later group in the event, or any later handler in the same group, shifts as a result, the message says those hooks will need review again in Codex and names the events. There is no placeholder to preserve the index, since an inert entry would itself need review and would outlive Tokitty.
- Refresh rewrites a command in place, which keeps the index. Its command changes should be rare once the runner path is stable.

### 4. Telling the user that review is needed

Before approval the hooks don't run, so the cat behaves as it does today for Codex: rate limits and ledger, no live poses. Nothing breaks. The user just needs to know why.

After a Codex install, and on each startup for Codex accounts with owned entries, Tokitty reads `config.toml` and looks for a `trusted_hash` under each key its entries occupy. It does not compute the hash. That would mean reproducing Codex's internal TOML serialisation. Key presence alone can't tell a current approval from a stale one, though. After Tokitty rewrites a command, the old `trusted_hash` stays in place, Codex skips the hook, and a check based only on presence would still say approved.

Tokitty knows when it changes a command, because it is the one changing it. So each install or rewrite records, in Tokitty's own state dir, the `trusted_hash` value found under that key at the time of writing, or none if there was no key. The rule for each owned handler is then:

- **No key:** needs approval.
- **Key present, and its value equals the one recorded when Tokitty last wrote this command:** the approval predates the current command. Needs approval.
- **Key present, and its value differs from the recorded one, or nothing was recorded (installed by an older build):** approved.

The Accounts dialog shows "Hooks installed, waiting for approval in Codex. Start `codex` and approve the Tokitty hooks" while any handler needs approval, and the install or refresh result says the same. This does not catch a user editing Tokitty's command by hand, or reverting a command to one Codex approved earlier (Codex re-trusts that silently, and Tokitty would still say approval is needed until the user opens Codex). Both are accepted.

Missing activity is a separate diagnostic, not a trust status. If every handler reads as approved and no state file has been written to the account's sessions dir since the last install or rewrite, the row adds "no activity from Codex hooks yet". That is a hint for a bug report, and makes no claim about the cause.

Python 3.10 has no `tomllib`, and the floor is `>=3.10`. Only the `[hooks.state.'...']` table headers are needed, so a narrow line match on those headers and the `trusted_hash` line under them is enough. `tomllib` is used when present. Tests cover both paths against the `config.toml` layout the spike captured, including backslashes in the key.

### 5. Writer and tracker

`hook_writer.py` adds `PermissionRequest` and `Interrupt` to `_KNOWN_EVENTS` and records them like any other event. It stays harness-neutral: no provider logic, since the event names don't collide.

`ActivityTracker` adds both events to its own `_KNOWN_EVENTS`, which is separate from the writer's and drops unknown records before any mapping happens. Then:

- `PermissionRequest` sets the permission overlay, the same as `Notification`. Codex fires `PreToolUse` before the approval dialog and `PostToolUse` after approval, so an approved prompt goes working, then permission, then thinking. That is the existing mapping (only `PreToolUse` means working, and `PostToolUse` means the model is processing a tool result), and it applies unchanged.
- `Interrupt` sets `base_state` to idle, clears the stretch and the tool label, and never plays the done hop. It lowers the permission overlay, as any non-permission event does. This is also the path for a denied permission, since denial produces `Interrupt` and nothing else.

A lost `Interrupt` is the realistic failure, given the timeouts above. With the permission overlay raised, the existing rule keeps it up until the session is swept at `GONE_S` (30 min). For Claude that was an accepted edge case. For Codex, every denial goes through an event with a 3 s ceiling, so it is less of an edge. The tracker adds a permission timeout of `PERMISSION_STALE_S = 600` for any provider: an overlay with no newer event for 10 minutes drops to idle. A real approval prompt left for longer than that loses its overlay, and that trade is accepted. A lost `SessionEnd` leaves a state file that the existing `GONE_S` sweep deletes. No change there.

Short `codex exec` runs are outside the live-pose promise. The writer keeps only the latest state per session and deletes it on `SessionEnd`, and an idle watcher polls every 20 s. An exec that finishes between two polls leaves nothing to see, even though every hook ran. The same is already true of very short Claude Code sessions. The poses target interactive sessions, where a turn outlasts one idle poll.

`SessionStart`, `SubagentStart` and the compact events are not installed. They would add reviews without changing any pose.

## Out of scope

- The Codex desktop app and IDE extension. The spike covered the CLI only, and whether those surfaces offer a hook review at all is untested. The README should say that approval currently happens in the CLI.
- HTTP hooks, for the reasons #48 records.
- Writing trust into `config.toml` (decided against, above).

## Risks

- **Timeouts on `Interrupt` and `SessionEnd`.** Seen in live runs with two hooks per event and not reproduced in isolation. With one Tokitty hook, a 3 s ceiling and roughly 70 ms per run it should hold, but it is unconfirmed. Task 8 measures it in a real session.
- **Codex hook behaviour is young.** The hash identity, key format and file shape could change in a Codex release. The installer and the trust check should fail soft: a parse failure reports "can't read Codex hook state" and never blocks install or uninstall.
- **Ctrl-D may skip `SessionEnd`.** Unresolved. If it does, `GONE_S` cleans up.
- **Frozen installs inherit #48's path work.** If #48 ships without the stable runner path, every Tokitty update forces eight Codex reviews. That is the reason for the dependency.

## Tasks

The work ships as three PRs, each cut from `main` after the previous one merges.

- **PR A, hooks groundwork (before #48).** Tasks 2, 3 and 4, minus anything Codex-specific that only makes sense once Codex has hooks. Specifically: the provider passed through every call, the hook file and event list chosen by provider, exact ownership matching keyed by provider (both historical Claude Python strings), handler-level uninstall, and a `warning` field on successful results for #48's link fallback. The per-provider table holds only Claude; the Codex shapes, `hooks.json` and the Codex events arrive with PR C. No behaviour changes for Claude users except the uninstall fix. `CodexProvider` keeps `activity=False`, so no Codex home is touched yet. Plan: `docs/superpowers/plans/2026-09-24-hooks-groundwork.md` on branch `hooks-groundwork`.
- **PR B, #48.** It builds its stable runner path, the exec-form Claude command and `ensure_current` on PR A's per-provider seams, not on a Claude-only matcher.
- **PR C, the rest of #64.** Tasks 1, 5, 6, 7 and 8: the Codex capability and sessions dir, the trust check, the Codex branch of `ensure_current`, the writer and tracker events, and the manual gate.

1. **Provider capability, sessions dir and path forms.** `activity=True`, `resolve_activity_sessions` for local and WSL Codex homes, and a helper returning both the filesystem path and the Codex-visible path of a home. Tests beside the existing Claude ones, including a `\\wsl.localhost` home mapping to its POSIX form.
2. **Provider through every call.** `get_config_dirs` returns `(config_dir, provider)`, and the CLI loops, `apply_account_mutation` and `retry_pending_hook_op` pass the provider on. Tests: a Codex account through each of the four paths writes `hooks.json` and never `settings.json`, and a pending op recorded without a provider still resolves from `accounts.json`.
3. **Per-provider hook file, event list and ownership.** `hooks.json`, the eight events, the timeouts, a new group appended at the end, and the two exact Codex command shapes. Tests for a fresh file, an existing user `SessionStart` group left untouched, a non-object `hooks` key aborting, install being idempotent, and a user command containing "tokitty" not being treated as owned.
4. **Handler-level uninstall and shift warning.** Remove only the owned handler for both providers. Tests: a user handler inside Tokitty's group survives, a user group after Tokitty's is warned about with its event named, a later handler in the same group is warned about, a user group before Tokitty's gives no warning.
5. **Trust check.** The `config.toml` reader with and without `tomllib`, the recorded-hash rule from section 4, keys built from the Codex-visible path with snake-case events, and the distro-running gate. Tests for no `config.toml`, a missing key, a key equal to the recorded value, a key that differs, no recorded value, a WSL home's POSIX key, an unreadable file, and a stopped distro not being read.
6. **Codex refresh (with #48).** The Codex branch of `ensure_current`: in-place rewrite of owned handlers, never adding one, and recording the hash for section 4. Tests: source to frozen, a moved stable path, no-op when identical, and uninstall followed by a restart staying uninstalled.
7. **Writer and tracker events.** `PermissionRequest` and `Interrupt` in both `_KNOWN_EVENTS` sets, the tracker rules in section 5, `PERMISSION_STALE_S`. Tests: permission then `Interrupt` gives idle with no hop, permission then `PostToolUse` gives thinking, permission alone decays after 600 s, `Interrupt` mid-stretch never hops.
8. **Manual gate (Nick).** On Cucumber's native Windows Codex home: install from the Accounts dialog, see the waiting-for-approval text, approve in `codex`, and confirm the cat thinks, works, shows permission on an approval prompt, goes idle on a denial and on Esc, and that `/quit` removes the state file. Repeat once with a frozen #48 build to confirm the stable-path command and that updating Tokitty does not trigger a review.
