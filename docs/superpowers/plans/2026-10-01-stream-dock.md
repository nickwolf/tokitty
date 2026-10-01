# Stream Dock M18 implementation plan

**Goal:** A VSDinside Stream Dock M18 shows one key per live Claude Code session, jumps to that session's Windows Terminal tab on a press, answers its permission prompts, and carries usage, interrupt, and new-session keys. Tokitty does all of the logic; a small HTML plugin inside VSD Craft only draws and forwards presses.

**Design:** `docs/superpowers/specs/2026-09-30-stream-dock-design.md` on this branch (commit 8a229a7). Read it first, especially "Permission prompts" (findings 1 to 7) and "Hardware findings". This plan deviates from it in three places, recorded in Task 0.

**Branch:** `streamdock-design`, cut from `main` at 8789f79, worktree `.worktrees/streamdock-design`. Merge back with `git merge --no-ff`.

**Session handoff:** if `.handoff/session.md` exists at the worktree root, read it after this plan and the spec. It is untracked and machine-specific (leftover probe tooling, local gotchas, open questions for the owner). Treat it as advisory and check live state before relying on any of it.

## Global constraints

- Baseline: `python3 -m pytest -q` gives 926 passed, 3 skipped on `main`. Every task ends with the full suite green and `ruff check` clean.
- Tests use `tmp_path` and injected fakes only. Never touch a real `~/.claude`, `%LOCALAPPDATA%\Tokitty`, `%APPDATA%\HotSpot`, a real Windows Terminal, or the network beyond `127.0.0.1` in a test.
- Everything new is Windows-only at runtime and must import cleanly on Linux and macOS, so CI stays green everywhere. Windows-specific imports go inside functions or behind `sys.platform == "win32"`.
- `hook_writer.py` is copied into each Claude config dir and run by whatever `python3` that environment has. It stays standard library only, Python 3.10 compatible, and must never raise: any failure means "print nothing, exit 0", which leaves Claude Code's own prompt in charge.
- No new required dependency. `comtypes` (UI Automation) is an optional extra, `streamdock = ["comtypes; sys_platform == 'win32'"]`. Without it, everything except tab focus and interrupt still works.
- Public-writing rules apply to code comments, docs, and commit messages: no em-dashes, no hard-wrapped prose, one concern per commit, no AI attribution lines. Match the comment density of the surrounding file.
- Per-session layout state never goes through `accounts.json`.

## Compatibility with #48 and #64

`installable-app-design` (#48) rewrites `hooks_install.py` heavily (frozen builds register a bundled `tokitty-hook` runner in exec form, and ownership matching learns that form). `codex-activity-hooks` (#64) adds a Codex hook target. To keep both merges mechanical:

- Do not add a second hook script, a second command shape, or new ownership rules. The permission wait lives inside `hook_writer.py` as one more event, so frozen builds and ownership pick it up for free.
- The only change to `hooks_install.py` is one more entry in the `HOOK_EVENTS` list literal. If #48 has merged to `main` by then, rebase this branch first and make the same one-line change on top.
- Do not touch `_build_command`, `_is_owned_hook`, or the `HookTarget` table.

## Task 0: Spec amendments

**Files:** `docs/superpowers/specs/2026-09-30-stream-dock-design.md`.

Record three decisions made while planning, each as a short paragraph in the section it changes:

1. **HTTP long-poll instead of a WebSocket server.** Tokitty serves `127.0.0.1` with the standard library's `ThreadingHTTPServer`; the plugin long-polls for key images and POSTs events. Python's standard library has no WebSocket server and adding the `websockets` package for one connection is not worth a dependency. The hardware probe already proved a VSD plugin can `fetch` a loopback server.
2. **The permission wait lives in `hook_writer.py`**, not a new hook script, for the #48 reason above.
3. **Opt-in at runtime, not at install.** `PermissionRequest` is registered for everyone with activity hooks, but the hook returns immediately (no output, no pending file) unless `<config>/tokitty/streamdock.enabled` exists with an mtime under 120 s old. Tokitty touches that file every 30 s while the plugin is connected. So someone without a deck pays one extra hook process per prompt that exits at once, and a deck that goes away stops holding hooks within two minutes.

Commit: `Record Stream Dock transport and hook placement decisions`.

## Task 1: Per-session activity view

**Files:** `tokitty/activity.py`, `tokitty/activity_watcher.py`, `tests/test_activity.py`, `tests/test_activity_watcher.py`.

**Acceptance criteria:**
- New dataclass `SessionView(session_id: str, state: str, tool_label: str, first_seen: float, last_ts: float)`. `state` is one of `permission`, `working`, `thinking`, `done_hop`, `idle`.
- `ActivityTracker.sessions(now) -> List[SessionView]` returns every tracked session (including idle ones, unlike `aggregate`), ordered by `first_seen` ascending. `first_seen` is the `ts` of the first record seen for that session and never changes afterwards.
- `ActivityWatcher` publishes both, and gains `get_sessions() -> List[SessionView]` next to `get_latest()`. `get_latest()` and `aggregate()` are unchanged, so every existing caller and test keeps passing untouched.
- Tests: ordering by first appearance survives a later event on an older session; an idle session is listed with state `idle`; a deleted session file drops out; `get_sessions()` returns `[]` before the first tick and when the distro is stopped.

## Task 2: Permission wait in hook_writer

**Files:** `tokitty/hook_writer.py`, `tests/test_hook_writer.py`.

This is the safety-critical task. Keep it small, injectable, and boring.

**Behaviour, on `hook_event_name == "PermissionRequest"`:**
1. Resolve `tokitty_dir` as the parent of `--sessions-dir`. If `tokitty_dir/streamdock.enabled` is missing or its mtime is 120 s old or more, exit 0 with no output.
2. Generate `nonce = secrets.token_hex(8)`. Compute `digest = sha256(json.dumps(tool_input, sort_keys=True, separators=(",", ":")))`.
3. Find the tool use id: read at most the last 256 KiB of `transcript_path`, walk assistant entries newest first, and take the newest `tool_use` block whose `name == tool_name` and whose `input` digests to `digest`, and which has no later `tool_result` with that id. Retry the lookup up to 5 times, 100 ms apart, because the spike measured the `tool_use` line landing only about 150 ms ahead of the hook. If it still fails (transcript missing or unreadable, no match, or more than one unresolved match), exit 0 with no output and write no pending file. Without the id the hook has no way to notice an answer given elsewhere, so it must not wait; the prompt simply stays a terminal-only prompt.
4. Write `tokitty_dir/pending/<nonce>.json` atomically (temp file then `os.replace`, the same helper the session writer uses): `{"v": 1, "nonce", "session_id", "tool_use_id", "tool_name", "tool_input", "digest", "preview", "cwd", "started": time.time(), "pid": os.getpid()}`. `tool_input` is the complete input, so the in-window view (Task 11) can show everything that would be approved. `preview` is a short tool-specific summary for the 64 px keys only: the `command` for Bash, `file_path` for Edit/Write/Read, `url` for WebFetch, otherwise compact JSON, cut to 200 characters. The keys never count as showing the request.
5. Loop every 250 ms until a stop condition:
   - Every 5 s, `os.utime` the pending file (heartbeat).
   - If `tokitty_dir/decisions/<nonce>.json` exists, read it, delete it, and apply it only if its `nonce`, `session_id`, and `digest` all match. A valid decision prints the `hookSpecificOutput` for `allow` or `deny` and stops. A mismatched one is deleted and ignored.
   - If the transcript tail now contains a `tool_result` for it, the prompt was answered elsewhere: stop with no output.
   - If `streamdock.enabled` has gone stale (step 1 rule), stop with no output.
   - If 590 s have passed since start, stop with no output. That is just under Claude Code's 600 s default hook timeout, so the hook exits on its own terms and cleans up rather than being killed.
6. On every exit path, including exceptions, delete the pending file (`try/finally`).

**Output shapes:** `allow` prints `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`. `deny` adds `"message": "Denied from Stream Dock"`. No `updatedPermissions` in this task; Always arrives in Task 10.

**Testability:** factor the loop as `wait_for_decision(payload, tokitty_dir, *, now_fn, sleep_fn, read_tail_fn, rand_fn)` so tests drive time and files without real sleeps.

**Tests:** disabled marker gives no output and no pending file; stale marker same; a matching decision prints allow and removes both files; a decision with the wrong digest or session is ignored and deleted; a `tool_result` appearing for the looked-up id ends the wait silently; marker going stale mid-wait ends it; the 590 s cap; the pending file is removed when the loop raises; tool use id lookup picks the newest unresolved match and survives a truncated first line in the tail; a failed lookup (missing transcript, no match, two identical unresolved calls) gives no output and no pending file; a non-PermissionRequest event still writes the session state file exactly as today.

## Task 3: Register the event

**Files:** `tokitty/hooks_install.py`, `tests/test_hooks_install.py`.

- Add `("PermissionRequest", "")` to the `HOOK_EVENTS` list literal in the source (not a runtime append: `_HOOK_TARGETS["claude"]` takes `tuple(HOOK_EVENTS)` at import, so the literal is what counts). No other change in this file. A test asserts `install_hooks_for_dir` writes a `PermissionRequest` entry to `settings.json`.
- Update the tests that enumerate installed events. Add one test that an existing install missing only `PermissionRequest` gets exactly that event added on the next `install_hooks_for_dir`, which is the upgrade path for current users.
- README, "Live activity" section: one paragraph saying the permission hook is registered but inert unless a Stream Dock is connected, and that restarting running Claude Code sessions picks it up.

## Task 4: Pending watcher and decision writer

**Files:** new `tokitty/streamdock/pending.py`, new `tests/test_streamdock_pending.py`.

- `PendingRequest` dataclass mirroring the pending file, plus `account_index`.
- `PendingWatcher(tokitty_dir, *, distro_name, list_running_distros_fn, list_files_fn, read_file_fn, stat_fn, time_fn, sleep_fn)`, one per account, one daemon thread, same skip-when-distro-stopped rule as `ActivityWatcher` (never touch `\\wsl.localhost` for a stopped distro).
- Polls `pending/` every 0.5 s while `active` is set, and not at all otherwise. `set_active(bool)` is driven by the deck connection (Task 11).
- A request is live while the file exists and its mtime is under 30 s old. Creation age never makes it dead.
- `get_pending() -> List[PendingRequest]`.
- `write_decision(req, behavior)` writes `decisions/<nonce>.json` atomically with `nonce`, `session_id`, `digest`, `behavior`, `decided_at`. Creates `decisions/` if needed.
- `touch_enabled()` creates or touches `streamdock.enabled`; `clear_enabled()` removes it. Both swallow `OSError`.
- Tests with fake filesystem functions: stale heartbeat hides a request; a stopped distro produces no reads; inactive watcher does not poll; decision file contents and atomic write; touch and clear.

## Task 5: Key rendering

**Files:** new `tokitty/streamdock/render.py`, new `tests/test_streamdock_render.py`.

All images are 64x64 RGB PNGs returned as `data:image/png;base64,...` strings, memoised by their input tuple.

- `session_key(sprite_state, frame_index, palette, title, accent)`: the cat from `sprites.get_frames` via `sprite_raster` at scale 2 (56x52), centred at the top, a 12 px title strip below with the title truncated to fit, and a 2 px amber border when `accent`.
- `usage_key(session_pct, weekly_pct, warn)`: two bars and two numbers, coloured with `display.bar_color` so the deck matches the window.
- `decision_key(kind)` for `allow` (green), `deny` (red), `always` (amber), `sent` (grey, "sent"), `cancel` (grey, back arrow).
- `preview_keys(text, n)`: splits a command preview across `n` keys, readable at 64 px (about 8 characters by 4 lines per key), last key ending in an ellipsis when truncated.
- `status_key(text)`, used for `+N`, "no tab", "no comtypes", and empty slots.
- Tests: every function returns a decodable 64x64 PNG; output is deterministic; accent changes pixels on the border only; memoisation returns the identical string.

## Task 6: Deck model

**Files:** new `tokitty/streamdock/model.py`, new `tests/test_streamdock_model.py`.

A pure, thread-safe state machine. No I/O, no Tk, no Windows calls. This is where most logic and most tests live.

- Keys are identified by VSD's `context` string. Each has `coordinates`, `device`, and `settings` (the role from the property inspector: `slot`, `usage:<account_index>`, `interrupt`, `new:<preset_name>`). `appear(context, coords, device, settings)` and `disappear(context)` are idempotent, since VSD sends every `willDisappear` twice.
- Slots are the visible `slot` keys ordered by (row, column). Sessions get slots in `first_seen` order and keep them until they end. A new session takes the lowest free slot. With more sessions than slots, the last slot shows `+N` and pressing it cycles which overflow session it shows.
- **Threading:** the model is owned by the Tk thread and is not itself thread-safe. Only `tick()` calls into it. After each tick it publishes an immutable render snapshot (revision plus plan) under a lock, which is all the HTTP thread ever reads.
- `update(sessions_by_account, pending, usage, focus_status)` replaces the inputs and recomputes. `render_plan() -> Dict[context, KeySpec]` describes what every visible key should show; `revision` increments only when the plan actually changes.
- `press(context) -> Action` returns one of `Focus(session)`, `FocusAndOpen(session, request)`, `Decide(request, behavior)`, `Interrupt(session)`, `NewSession(preset)`, `CancelOverlay`, `Noop(reason)`.
- **Decision overlay.** Pressing a slot whose session has a live pending request returns `FocusAndOpen(session, request)`: the runtime focuses the tab and the model enters overlay mode for that request. Only keys with the `slot` or `interrupt` role take part; usage and new-session keys are never repainted, and non-Tokitty keys are never in the plan at all. The pressed slot becomes Cancel. The other participating keys, in (row, column) order, become Allow, Deny, Always (only when Task 10 has marked the request as having a narrow rule), then preview keys. The overlay needs at least two participating keys besides the pressed one; with fewer, there is no overlay and the press opens the in-window view instead (Task 11), where the decision is made. The overlay exits when the request disappears from `pending`, when Cancel is pressed, or on a second press of the pressed key.
- **Arming rule.** Allow and Always are live only when the complete request is visible somewhere authoritative: either `focus_status` for that session is `focused` (its tab was confirmed, selected, and frontmost, where Claude Code's own prompt shows the full input) or the request is open in tokitty's in-window view (Task 11). Otherwise those keys render greyed and pressing them is `Noop("request not visible")`. Deny is always live; denying something you can't see is safe.
- **Re-check at press time.** A press on a live Allow or Always returns `VerifyThenDecide(request, behavior)`, not `Decide`. When the arming came from tab focus, the runtime re-checks `is_selected` on the focus worker before the decision file is written, and turns a failed check into `Noop` plus a `focus_status` downgrade, so switching tabs by hand after the overlay opened disarms Allow. When the arming came from the in-window view, the decision is written directly.
- After `Decide`, the decision keys show `sent` until the request leaves `pending`.
- `Interrupt` is only returned for the session most recently focused through the deck, and only if `focus_status` for it is still `focused`; otherwise `Noop`.
- Tests cover: a manual tab switch between arming and pressing (Allow becomes a no-op); slot stability as sessions come and go; overflow and cycling; duplicate and out-of-order appear/disappear; overlay entry and every exit path; a layout with too few participating keys falls back to the in-window view; arming rule both ways; `sent` state; interrupt gating; that a usage key and a new-session key never become decision keys; revision only bumps on change.

## Task 7: Tab focus and interrupt

**Files:** new `tokitty/streamdock/wt_focus.py`, new `tests/test_streamdock_wt_focus.py`.

- `session_title(transcript_path) -> Optional[str]`: the last `{"type":"ai-title"}` line in the transcript tail. The transcript for a session lives at `<config>/projects/*/<session_id>.jsonl`; resolve it once per session with a glob and cache the path.
- `class WtFocus` wraps a small UIA adapter with three methods (`list_tabs() -> List[Tab(window_handle, name, selected, element)]`, `select(tab)`, `bring_to_front(window_handle) -> bool`). The real adapter uses `comtypes.client.CreateObject` on `CUIAutomation` and the `CASCADIA_HOSTING_WINDOW_CLASS` window class, plus `ShowWindow`/`SetForegroundWindow` through `ctypes`. Tests use a fake adapter.
- Tab matching: strip a leading non-alphanumeric glyph and whitespace from the tab name, compare to the AI title exactly. Exactly one match gives `focused` after select and bring-to-front; zero gives `not_found`; more than one gives `ambiguous` and does nothing. A title missing from the transcript gives `no_title`.
- `is_selected(session) -> bool`: re-reads the tab list and checks the matched tab is still the selected one in the foreground window.
- `send_escape(session) -> bool`: `is_selected` first, then `SendInput` of Esc down and up. Returns False and sends nothing if the check fails.
- Calls run on a single worker thread owned by tokitty, never on the Tk thread or the HTTP server thread. UIA calls can take hundreds of milliseconds.
- If `comtypes` is not importable, `WtFocus.available` is False and every call returns `unavailable`.
- Manual check, recorded in the commit message: from a running tokitty (not a scheduled task), with another app in front, a slot press brings the right tab forward. This closes the spec's open foreground question.

## Task 8: New-session presets

**Files:** new `tokitty/streamdock/launch.py`, `tokitty/settings.py`, tests for both.

- Preset: `name`, `account_index`, `env` (`wsl` or `native`), `distro` (WSL only), `cwd`. Stored as a new `streamdock_presets: List[dict]` field on the `Settings` dataclass, validated in `load_settings` the same way `usage_budgets` is (malformed entries dropped, never a crash).
- `build_command(preset, account) -> List[str]`: WSL gives `wt.exe -w 0 new-tab wsl.exe -d <distro> --cd <cwd> -- bash -lic claude`, with `CLAUDE_CONFIG_DIR=<native path>` exported in that command when the account is not the distro's default `~/.claude`; native gives `wt.exe -w 0 new-tab -d <cwd> claude` with the environment variable set the same way.
- Launch through `subprocess.Popen` with no shell. Tests assert on the argument list only.
- Presets are edited by hand in v1. The property inspector picks among existing preset names.

## Task 9: Deck HTTP server and the plugin

**Files:** new `tokitty/streamdock/server.py`, new `streamdock/com.tokitty.deck.sdPlugin/` (`manifest.json`, `index.html`, `pi.html`, `icon.png`), new `tokitty/streamdock/install.py`, `tokitty/__main__.py` (CLI flags only), tests for server and install.

**Server:**
- `ThreadingHTTPServer` on `127.0.0.1:<port>`. Only CORS "simple" requests are used, so Chromium never sends a preflight: no custom headers, the token travels as a `t` query parameter, and POST bodies are sent as `text/plain`. Every request must have a valid token and a `Host` header of exactly `127.0.0.1:<port>` (which blocks DNS-rebinding pages); anything else gets 403 and nothing about the request is logged. Responses carry `Access-Control-Allow-Origin: *` so the plugin page can read them; that grants nothing without the token. `OPTIONS` gets 405.
- **Trust boundary.** The token keeps out web pages and anything that doesn't have it. It does not keep out other programs running as the same Windows user, which can read `config.js`. That is accepted: such a program can already type into the terminal, so the deck adds no new power.
- `GET /v1/plan?rev=N` long-polls up to 25 s and returns `{"rev", "keys": {context: {"image", "title"}}}` as soon as the model's revision differs from `N`.
- `POST /v1/event` takes the VSD event as the plugin received it (`willAppear`, `willDisappear`, `keyDown`, `keyUp`, `didReceiveSettings`) and returns 204. `keyUp` drives `press`; `keyDown` is ignored, so a held key never fires twice.
- `GET /v1/meta` returns account names and preset names for the property inspector.
- Server marks the deck connected while plan requests keep arriving (one within the last 40 s), and disconnected otherwise. That flag drives `PendingWatcher.set_active` and the `streamdock.enabled` marker.

**Plugin:** the probe from the spike, made permanent. One action, `com.tokitty.deck.key`, `Controllers: ["Keypad"]`. `index.html` loads `config.js` (port and token), registers with VSD, forwards events, runs the long-poll loop, and calls `setImage` only for keys whose image changed. On a failed poll it backs off to 2 s and shows a "tokitty not running" image on its keys. `pi.html` offers the role dropdown (Session slot, Usage per account, Interrupt, New session per preset) and saves it with `setSettings`.

**Install:** `python -m tokitty --install-streamdock` picks a free port once and a random token, stores both as new `Settings` fields (`streamdock_port: int = 0`, `streamdock_token: str = ""`, validated in `load_settings`), copies the plugin to `%APPDATA%\HotSpot\StreamDock\plugins\com.tokitty.deck.sdPlugin`, writes `config.js` there, and prints that VSD Craft must be fully exited and restarted to load it. `--uninstall-streamdock` removes the folder, the settings keys, and every account's `streamdock.enabled`. The token is never printed.

**Early gate:** before the rest of this task, load a stub plugin into VSD Craft that makes the authenticated GET and POST exactly as specified, and confirm both succeed from the real plugin page. **Tests:** 403 without or with a wrong token, and with a wrong `Host`; long-poll returns immediately on a stale rev and after a change; event routing calls the model; install into a fake `%APPDATA%` writes `config.js` with the stored port and token and is idempotent; uninstall cleans up.

## Task 10: Always

**Files:** `tokitty/hook_writer.py`, `tokitty/streamdock/model.py`, `tokitty/streamdock/rules.py` (new), tests, and a findings section appended to the spec.

Starts with an experiment, the same tmux harness the spec's findings used, before any code:
1. Answer a Bash prompt with the terminal's own "don't ask again" option, and record exactly what it writes and where.
2. Return `updatedPermissions` in the array form (`[{"type":"addRules","rules":[{"toolName":"Bash","ruleContent":"..."}],"behavior":"allow","destination":"session"}]`) and in the other form the reference shows, and record which one Claude Code accepts and whether the rule then auto-allows a second identical call.
3. Answer in the terminal first and then return an Always from the hook, and confirm the late rule is ignored the way the late deny was.

Then:
- `rules.narrow_rule(tool_name, tool_input) -> Optional[str]`: the exact Bash command (no prefix wildcards), the exact file path for Edit/Write, the exact URL's domain for WebFetch (matching what the terminal writes), `None` for everything else.
- The pending file gains `"always_rule"` (or null). The model offers Always only when it is non-null. The hook emits `updatedPermissions` with `destination` `session` and nothing else. If step 2 shows `session` is not accepted, Always is dropped from v1 entirely rather than writing to a settings file.
- Tests for every rule shape and for the hook's output.

## Task 11: Wiring and the in-window fallback

**Files:** `tokitty/__main__.py`, `tokitty/ui.py`, new `tokitty/streamdock/runtime.py`, tests where logic is not Tk.

- `StreamdockRuntime` owns the server, the model, one `PendingWatcher` per account, the focus worker, and the 30 s `streamdock.enabled` heartbeat. Started only when `Settings` has a non-zero port and a token (that is, after `--install-streamdock`). `run_gui` creates it after the units and stops it in the `finally` block.
- `tick()` (Tk thread, every 500 ms) gathers `watcher.get_sessions()`, `get_pending()`, and the usage numbers it already has per unit, plus the current pose per session, and calls `model.update`. This is the only place model inputs change, apart from key presses.
- **Threading contract.** Three threads touch Stream Dock state, and only the Tk thread owns any of it. The HTTP server thread puts each incoming event on an inbound `queue.Queue` and serves long-polls from the latest published snapshot; it never calls the model. The focus worker takes jobs from its own queue and puts results (focus status, verify outcomes) on the same inbound queue. `tick()` drains the inbound queue, applies events and results to the model, executes the returned actions (decision file writes happen here, on the Tk thread; focus, verify, interrupt, and launch jobs are queued to the worker; the in-window view is created here), and finally publishes a new snapshot. `PendingWatcher` threads only publish their own lists, read through `get_pending()` like the other watchers. Tick runs every 500 ms, so a press takes at most half a second to act on; if that feels slow in Task 12, the server thread may call `root.event_generate` to wake Tk early, but never the model.
- In-window view: when a `FocusAndOpen` comes back from the worker as anything but `focused`, or the overlay had too few keys, tokitty opens a small always-on-top Tk `Toplevel` near its card showing the account, the tool, and the complete `tool_input` in a scrollable read-only text widget, with Allow and Deny buttons of its own. Only then does it mark the request visible, which arms Allow on the deck. The window closes itself when the request leaves `pending`.
- Right-click menu: a "Stream Dock" submenu showing connected or not, and the install command if not installed.
- Tests for `StreamdockRuntime` with fakes: events arriving on the server thread are only applied inside `tick()`; a focus result and a key press arriving in the same tick are applied in arrival order; heartbeat touches and clears the marker on connect and disconnect; decisions route to the right account's watcher; focus results feed back into the model.

## Task 12: End-to-end check on the hardware

Not code. Run through with the real M18, VSD Craft, two Claude Code sessions in two tabs, and record the results in the PR description:

1. Session keys appear in order, keep their slots as a third session starts and the first ends, and survive a page switch with the plain buttons.
2. A Bash prompt in a background tab: the key raises its flag, a press brings the tab forward and opens the overlay, Allow runs the command, the keys return to normal.
3. Same, answered in the terminal instead: the deck clears within 2 s and no hook process is left running (`pgrep -f hook_writer`).
3a. Press a waiting slot, then switch tabs by hand before pressing Allow: Allow does nothing and the overlay greys it.
4. Deny from the deck shows "Denied from Stream Dock" in the session.
5. Two sessions with the same title: the key reports ambiguous, the in-window fallback appears, Allow works from there.
6. Close tokitty while a prompt is pending: within 2 minutes the hook has exited and the terminal prompt still works.
7. Interrupt only acts on the tab that was focused through the deck, and does nothing after switching tabs by hand.
8. New-session key opens a tab in the right repo for the right account.
9. Usage keys match the tokitty window.
10. Always, if Task 10 kept it: a second identical command runs without a prompt in the same session only.

## Execution order and review gates

Tasks 0 to 3 are the hook side and can merge alone, since without a deck they change nothing visible. Tasks 4 to 9 are independent of each other except that 6 is needed by 9; build them in order. 10 needs 2 and 6. 11 needs everything. 12 needs the device.

Codex reviews (read-only, cold-start briefing per the global instructions) after Task 2, after Task 6, and on the full diff before merge. Task 2 is the one where a mistake approves the wrong command, so its review is not optional.
