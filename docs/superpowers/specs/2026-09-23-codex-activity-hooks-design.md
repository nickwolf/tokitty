# Codex activity hooks (#64)

Status: design 2026-09-23, re-checked 2026-10-01 against the merged code of PR A (#65) and #48 (PR #68). Sections 1 to 5 use the names those PRs shipped. The installer groundwork ships first as its own PR, then #48, then the rest of this issue, because the hook command on a frozen install depends on #48's stable runner path (see Tasks). Decided with Nick the same day: Tokitty never writes Codex trust itself (the user approves hooks in Codex's own review), and #48 registers its hook runner at a stable per-user path that survives releases. Codex facts come from the openai/codex source at tag `rust-v0.156.1` and a live spike against codex-cli 0.156.1 (Windows build) on Cucumber, using a scratch `CODEX_HOME` and `gpt-6-luna`.

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

Letting them through is not enough on its own: every hook call has to know the provider, or a Codex home would get a Claude `settings.json`. PR A (#65) built that seam. `get_config_dirs` and `_config_dirs_from_accounts_file` return `(config_dir, provider)` pairs, and the CLI loops, `apply_account_mutation`, `retry_pending_hook_op` (including a legacy record without a provider) and #48's `ensure_current` all pass the provider on. `ensure_current` skips a provider with no entry in `_RECONCILE_TABLE`, so Codex needs an entry there as well as `activity=True`. Nothing else in this section's plumbing is left to build.

Each Codex home has two path forms. The filesystem path is the one Tokitty opens (`\\wsl.localhost\<distro>\...` from Windows for a WSL home, a plain path otherwise). The Codex-visible path is the one Codex itself uses, and it is what appears in trust keys (`/home/<user>/.codex/hooks.json` for a WSL home, `C:\Users\...\.codex\hooks.json` for a native Windows one). Section 4 builds trust keys from the Codex-visible form. Using the UNC form would find no keys and report "waiting for approval" forever. `_wsl_native_path` already does this conversion for hook commands. Every read of a WSL Codex home, including the `config.toml` read in section 4, is gated on the distro already running, as the activity watcher is, so Tokitty never starts a stopped distro by probing it.

### 2. Installing into `hooks.json`

The provider picks the file through PR A's `HookTarget` table in `hooks_install.py` (`settings_file`, `local_settings_file`, `events`). Codex's entry is `hooks.json` with `local_settings_file=None`. The JSON under the `hooks` key has the same shape in both harnesses, so #48's reconcile (`_reconcile_claude`, behind `_RECONCILE_TABLE`) is generalised to serve both rather than copied. Codex also accepts hooks inline in `config.toml`. Tokitty neither writes nor reads them there (see Out of scope).

The event list is per provider. Codex gets the eight events above, with no `matcher` key at all (Codex's `MatcherGroup.matcher` is optional, and the matcher is part of the hashed config), and `"timeout": 3` on the `Interrupt` and `SessionEnd` handlers. The field name is `timeout`, in seconds (`HookHandlerConfig::Command`, `#[serde(rename = "timeout")]`, rust-v0.156.1). The timeout is part of the hashed config, so it has to be right the first time. Changing it later means another review. `HookTarget` gains the per-event timeouts and a way to say "no matcher key".

The command follows #48's `_build_command` table, with one Codex difference: Codex has no exec form, so the entry is always a single `command` string, run by `cmd /C "<command>"` on Windows and `$SHELL -lc "<command>"` elsewhere. For a source install, and for a WSL home seen from a frozen Windows build (the same `python3` row #48 keeps for Claude), it is `python`/`python3 "<dir>/tokitty/hook_writer.py" --sessions-dir "<dir>/tokitty/sessions"`. Otherwise a frozen install writes the quoted `stable_runner_path(...)` (`<state dir>/current/tokitty-hook[.exe]`) followed by the same `--sessions-dir` argument, falling back to the quoted `hook_runner_path(...)` in the release folder with `LINK_FALLBACK_WARNING` when `ensure_runner_link` fails, exactly as #48 does for Claude. Codex wraps the whole string in one extra pair of quotes on Windows, which `cmd /C` strips, so a quoted first token survives. That was exercised only with a path free of spaces. A path containing spaces needs a test.

**Ownership and refresh for Codex.** #48's `_is_owned_hook` recognises, for Claude, the historical Python string and an exec-form `command` plus `args` (`_is_owned_exec_hook`, which checks only the runner's basename, never its folder). Codex never gets the exec form, so its owned shapes are two single strings, and a Codex handler carrying `args` is not owned:

- source: `python`/`python3 "<dir>/tokitty/hook_writer.py" --sessions-dir "<dir>/tokitty/sessions"`
- frozen: `"<stable path>/tokitty-hook[.exe]" --sessions-dir "<dir>/tokitty/sessions"`

Here `<dir>` is the Codex-visible home, and the `--sessions-dir` value must match it under #48's token normalisation. For the frozen shape, as with `_is_owned_exec_hook`, only the runner's basename is checked, so a stable-path, fallback-path or older-release handler is still ours. Anything else, including a user hook whose path contains "tokitty", is not ours.

The Codex entry in `_RECONCILE_TABLE` makes #48's `ensure_current` / `refresh_hooks_for_dir` cover Codex, with the same rules as for Claude: rewrite an owned handler in place when it differs, never add one, and collapse duplicates. One Claude rule cannot carry over. `_handler_needs_rewrite` treats two interpreter strings as never needing a rewrite, because for Claude a string is always the Python form. For Codex both shapes are strings, so the comparison parses each into (kind, runner path, sessions dir) and also compares `timeout`. The refresh guard that never demotes exec form to Python (so a source launch can't flip a frozen install's hooks) applies to Codex as "never demote the runner string to the Python string". Every rewrite changes the hash, so it is reported as "Codex will ask you to approve Tokitty's hooks again" in the result's `warning`, which the startup refresh already gathers into its one messagebox (section 4 tracks it). Switching between the source and frozen installs by an explicit install is a rewrite of this kind and costs one review.

#48 shipped the stable path: `ensure_runner_link` repoints `<state dir>/current` at startup (`run_discovery`, before the pending-op retry and `ensure_current`) and inside every frozen reconcile before anything is written. If the link can't be made, the reconcile falls back to the release folder and sets `LINK_FALLBACK_WARNING`, whose text already names the Codex review. Every update then costs one, and the user is told.

### 3. Trust keys and entry order

A Codex trust key is `<Codex-visible hooks.json path>:<event_snake_case>:<group index>:<handler index>`, with the event in Codex's snake case (`pre_tool_use`, `session_end`, and so on), so it depends on position. Removing a group shifts every later group in the same event down by one. Removing a handler shifts every later handler in the same group. Either way, the shifted hooks come back as untrusted.

- Install appends a new group containing only Tokitty's handler at the end of each event's list, so installing never moves a user hook.
- Uninstall removes only the owned handler, not the group around it, and removes the group only if that leaves it empty. PR A built this for both providers. What remains for Codex: if any later group in the event, or any later handler in the same group, shifts as a result, the result's `warning` says those hooks will need review again in Codex and names the events. There is no placeholder to preserve the index, since an inert entry would itself need review and would outlive Tokitty.
- Refresh rewrites a command in place, which keeps the index. Its command changes should be rare once the runner path is stable. Collapsing duplicate owned handlers does remove entries, so it gets the same shift check as uninstall.

### 4. Telling the user that review is needed

Before approval the hooks don't run, so the cat behaves as it does today for Codex: rate limits and ledger, no live poses. Nothing breaks. The user just needs to know why.

After a Codex install, and on each startup for Codex accounts with owned entries, Tokitty reads `config.toml` and looks for a `trusted_hash` under each key its entries occupy. It does not compute the hash. That would mean reproducing Codex's internal TOML serialisation. Key presence alone can't tell a current approval from a stale one, though. After Tokitty rewrites a command, the old `trusted_hash` stays in place, Codex skips the hook, and a check based only on presence would still say approved.

Tokitty knows when it changes a command, because it is the one changing it. So each install or rewrite records, in Tokitty's own state dir, the `trusted_hash` value found under that key at the time of writing, or none if there was no key. The rule for each owned handler is then:

- **No key:** needs approval.
- **Key present, and its value equals the one recorded when Tokitty last wrote this command:** the approval predates the current command. Needs approval.
- **Key present, and its value differs from the recorded one, or nothing was recorded (installed by an older build):** approved.

The record lives in `<state dir>/codex_hook_trust.json`, keyed by the Codex-visible `hooks.json` path, then by trust key, with the recorded value (or `null`) and the time of the write. Trust keys are parsed with `rsplit(":", 3)`, since a drive-letter path has its own colon, and the path part is compared under #48's home normalisation (separators, and case for a drive letter), because Codex writes the path the way it resolved `CODEX_HOME`.

Where the status shows:

- The install result. `install_hooks_for_dir` for Codex sets the result's `note` to "Hooks installed, waiting for approval in Codex. Start `codex` and approve the Tokitty hooks" while any handler needs approval. `--install-hooks` already prints `note`. The Accounts dialog shows it as an information box after a successful add (today it only shows `warning`).
- The Accounts dialog row. The Codex fact line gains "hooks: waiting for approval in Codex" or "hooks: approved". It is computed on the Tk thread only for a local home, the same rule `_local_codex_facts` follows, and shows nothing for a WSL home seen from Windows.
- Startup. A refresh that rewrote a Codex command sets `warning` ("Codex will ask you to approve Tokitty's hooks again"), which `run_discovery` already shows in its one messagebox. Startup does not nag about a still-pending first approval. That status lives in the dialog.

This does not catch a user editing Tokitty's command by hand, or reverting a command to one Codex approved earlier (Codex re-trusts that silently, and Tokitty would still say approval is needed until the user opens Codex). Both are accepted.

Missing activity is a separate diagnostic, not a trust status. If every handler reads as approved and no state file has been written to the account's sessions dir since the last install or rewrite (judged by the sessions directory's own mtime against the recorded write time, since `SessionEnd` deletes the state file and a create or delete inside the directory moves its mtime; a missing directory counts as no activity), the row adds "no activity from Codex hooks yet". That is a hint for a bug report, and makes no claim about the cause.

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
- Hooks declared inline in `config.toml`. Recognising a Tokitty command there would mean parsing nested TOML arrays of tables, which Python 3.10 can't do without a dependency. The only consequence of missing one is that the writer runs twice per event, which is harmless, since it keeps only the latest state per session.

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
6. **Codex refresh (with #48).** A Codex entry in `_RECONCILE_TABLE`, so `ensure_current` and `refresh_hooks_for_dir` cover Codex: in-place rewrite of owned handlers, never adding one, and recording the hash for section 4. Tests: source to frozen, a moved stable path, no-op when identical, and uninstall followed by a restart staying uninstalled.
7. **Writer and tracker events.** `PermissionRequest` and `Interrupt` in both `_KNOWN_EVENTS` sets, the tracker rules in section 5, `PERMISSION_STALE_S`. Tests: permission then `Interrupt` gives idle with no hop, permission then `PostToolUse` gives thinking, permission alone decays after 600 s, `Interrupt` mid-stretch never hops.
8. **Manual gate (Nick).** On Cucumber's native Windows Codex home: install from the Accounts dialog, see the waiting-for-approval text, approve in `codex`, and confirm the cat thinks, works, shows permission on an approval prompt, goes idle on a denial and on Esc, and that `/quit` removes the state file. Repeat once with a frozen #48 build to confirm the stable-path command and that updating Tokitty does not trigger a review.
