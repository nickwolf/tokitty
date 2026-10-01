# Stream Dock M18 integration

Status: design, 2026-09-30, hardware spikes run 2026-10-01 with a probe plugin on a real M18 (VSD Craft 3.10.205.0918, firmware V3.VSDM18_HXJDF.02.018). Nothing built.

## Goal

Use a VSDinside Stream Dock M18 (15 LCD keys, 3 plain buttons, an RGB ring) as a second surface for tokitty: see every live Claude Code session at a glance, jump to its terminal tab, answer its permission prompts, and see usage, without giving the device over to Claude entirely. The M18 also stays in use for unrelated things (OBS, media keys), so it keeps running the vendor software.

## Shape

Tokitty stays the brain. It already knows per-session state from its hooks, per-account usage, and the cat sprites. A thin Stream Dock plugin does two things only: it draws images tokitty sends it onto keys, and it forwards key presses back. All logic, rendering, and Windows calls live in tokitty's Python.

    Claude Code (WSL or native)
      hooks -> session state files, pending permission files
    tokitty (Windows, Python)
      reads state, renders key images, answers permissions, focuses tabs
      WebSocket server on 127.0.0.1
    VSD Craft -> tokitty plugin (HTML/JS page in VSD Craft's embedded Chromium)
      setImage / keyDown / keyUp over the vendor's plugin WebSocket

The plugin finds tokitty through a small discovery file in tokitty's state directory (`streamdock.json`: port and a random token written at startup). Bound to loopback only; the plugin and tokitty are both Windows processes, so the WSL-to-Windows localhost problem does not apply to this link.

Rejected alternatives: porting an existing Stream Deck plugin such as agentsd (it would duplicate tokitty's session tracking, and all of them assume macOS), and driving the M18 over raw USB HID (that takes the device away from the vendor software, which rules out the OBS use).

## Keys

One action type in the plugin, "Tokitty key", with a role picked in its property inspector:

- **Session slot.** Sessions fill slots in order of first appearance and keep their slot until they end, so keys don't shuffle under a finger. The key shows the cat in the account's colourway, the session's pose (content, thinking, working, permission flag, done hop), and a short title. More sessions than slots: the last slot shows `+N`.
- **Usage.** Session and weekly % for one account, coloured the same way as the main window, with the burn-rate warning when one is active.
- **Interrupt.** Sends Esc to the session this key was last pointed at (the most recently pressed session slot), and only after re-checking at press time that the session's tab is the selected one. If the tab can't be confirmed, the key does nothing and flashes. Esc on the wrong tab can dismiss a different session's prompt, so it never falls back to "whatever tab is selected".
- **New session.** Opens a new Windows Terminal tab running `claude` in a preset repo for a preset account. One key per preset; presets live in tokitty's config. A preset names its environment: WSL presets launch `wsl.exe -d <distro> --cd <repo> claude`, native presets run `claude` directly. Everything else in this design is the same for both, since tokitty already resolves each account's config dir either way.

Press on a session slot: focus that session's tab. If the session is waiting on a permission, the other Tokitty keys on the page turn into a decision overlay (Allow, Deny, Always, plus the command preview spread across a row) until the prompt is resolved or the slot is pressed again. The overlay only ever repaints keys that already belong to Tokitty, so an OBS or media key next to them is never touched. A vendor profile switch is a possible later refinement, but only if the hardware spike shows it can return to the right profile on its own.

The existing activity watcher publishes one aggregate view, not a per-session list, so the slot assignment is new state in tokitty rather than a reuse of what the main window draws.

## Permission prompts

Verified with throwaway sessions and a `PermissionRequest` command hook that slept before answering, on Claude Code 2.1.283 and 2.1.286:

1. The terminal prompt is drawn **while the hook is still running**. A capture 3 s into a 25 s hook sleep already showed "Do you want to proceed?". The terminal, Remote Control, and the deck can all answer the same prompt.
2. A hook that prints an `allow` decision after the prompt is already showing wins. The prompt closed and the tool ran with "Allowed by PermissionRequest hook". This also overrode an `ask` from an unrelated global PreToolUse hook.
3. Answering in the terminal first does **not** kill the hook. The prompt was answered at 18:50:26, the tool ran, and the hook process carried on to its own end at 18:50:38.
4. A late answer is ignored. Terminal "Yes" at 19:46:07, hook printed `deny` at 19:46:14: the tool had already run and nothing else happened. A late "Always" (with `updatedPermissions`) has not been tested yet.
5. A hook that exits with no output leaves the terminal prompt in place.
6. **The real payload has no `tool_use_id`**, although the hooks reference lists one. On 2.1.286 it was exactly `session_id`, `transcript_path`, `cwd`, `prompt_id`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`.
7. The transcript fills the gap. The assistant `tool_use` entry, with its real id, was written at 01:46:04.491Z, about 150 ms before the hook started, and the matching `tool_result` was written at 01:46:07.797Z, as soon as the terminal answer landed.

Design that follows:

- A new command hook for `PermissionRequest`, installed by `--install-hooks` alongside the existing activity hook. It runs where Claude Code runs (WSL on the primary setup). On start it generates a nonce, reads the tail of `transcript_path` to find the newest `tool_use` without a `tool_result` whose name and input match the payload, and takes its id. It then writes `pending/<nonce>.json`: nonce, session id, tool use id (or null if the lookup failed), tool name, a digest of the exact tool input, a short preview, and its own start time.
- While pending, the hook refreshes the file's mtime every few seconds as a heartbeat, and polls two things: `decisions/<nonce>.json`, and the transcript for a `tool_result` carrying its tool use id. A decision is applied only if its nonce, session id, and input digest all match. A `tool_result` means the prompt was answered elsewhere (allowed or denied), and the hook exits silently. Either way it deletes its pending file on exit, including on its own timeout.
- The transcript is local to the hook, so that polling never crosses the VM boundary. Tokitty reads only the small `pending/` directory over the `\wsl.localhost` path, with one dedicated poller at sub-second intervals that runs only while the plugin is connected and a pending file exists. The existing activity watcher (one thread, slowing to 20 s when idle) is the wrong loop for this.
- A pending file whose heartbeat is older than 30 s is treated as dead and ignored, then removed. Age since creation alone never counts as dead, since a prompt can legitimately wait for hours.
- After the deck writes a decision, its keys show "sent" until the pending file disappears. If the file vanished because the transcript showed a result first, the press was a no-op, which finding 4 shows is harmless.
- If the tool use id lookup fails, or two pending calls in one session have identical name and input, the key still lights but the hook cannot detect an answer given elsewhere. It falls back to its timeout, and tokitty clears the key when that session's next activity event arrives.
- **Always** returns a session-scoped `updatedPermissions` entry. The reference is inconsistent about the shape (one section shows an array of `{type, rules, behavior, destination}`, another an object), so the exact shape is pinned by experiment before implementation, along with what the terminal's own "don't ask again" writes. The rule is the narrowest that matches this call. Where no narrow rule exists, the Always key is not offered. A rule that would persist beyond the session is never sent.
- Files, not HTTP. Claude Code supports HTTP hooks. This machine runs WSL in NAT mode, where WSL's localhost does not reach Windows, so an HTTP hook would need a LAN-facing listener. Mirrored networking would fix that, but it is not the default and tokitty can't assume it. The file path is already proven by the activity hooks and needs no port.

## Focusing a tab

Verified: UI Automation from Windows lists every Windows Terminal window (`CASCADIA_HOSTING_WINDOW_CLASS`) and its tabs, with each tab's name and whether it is selected. A Claude Code tab's name is a spinner or status glyph followed by the session's AI title, and that same title is written into the session transcript as `{"type":"ai-title","aiTitle":...,"sessionId":...}`. So tokitty can map session id to title from the transcript and title to tab from UI Automation, then select the tab and bring its window forward.

Weak points: two sessions with the same title, a session that has no AI title yet, and `/rename`. Hooks also see `WT_SESSION` (a per-tab GUID that Windows Terminal passes into WSL), which can tell two same-titled sessions apart once one of them has been focused and confirmed, but Windows Terminal has no public way to focus a tab by that GUID.

When the match is ambiguous or missing, tokitty does not guess. The key shows that it can't jump to the tab, and if the session is waiting on a permission, the full command is shown in tokitty's own window instead, so the decision overlay is never armed with the command visible only in the wrong tab.

## Ring

Not reachable from a plugin. VSD Craft's binary contains a `setRGB` string next to its plugin-server code, but ten payload shapes for `setRGB`, `setLEDColor`, and `setKeyboardRGBBacklight` (by context and by device id, sent in sequence and then all at once as solid white) produced no visible change. v1 ships without the ring rather than dropping to raw HID.

## Out of scope for v1

Codex sessions (the Codex hooks work in #64 has to land first), macOS and Linux (the M18 software is Windows and macOS only, and the tab-focusing half is Windows-specific), macro keys that type text into a session, and using the three plain buttons for anything until a spike shows plugins can see them.

## Hardware findings

From a probe plugin that logged every event and accepted commands from outside:

- **Plugins are HTML pages, not Node.** Every bundled plugin uses `CodePath: index.html` with `SDKVersion: 1`, and VSD Craft calls the Elgato-style `connectElgatoStreamDeckSocket(port, uuid, registerEvent, info)` inside its embedded Chromium. The probe used the same shape, needed no build step, and could `fetch` a loopback HTTP server, so it can equally open a WebSocket to tokitty.
- **Grid.** `info.devices` reports the M18 as `{"columns":5,"rows":3}`. Presses arrive as `keyDown`/`keyUp` with `coordinates.column`/`row`. The registration info also listed stale entries (a `VSDN3`, a second `VSDM18`) from the vendor's default config, so the plugin keys off the `device` id on `willAppear`, not off the device list.
- **Images.** `setImage` with a base64 PNG data URL works at 64, 144, and 288 px; all are scaled to fit. One-pixel ticks along the bottom edge were crisp only in the 64 px image, so the native key size is 64x64 and tokitty renders at exactly that.
- **The three plain buttons are fixed page switchers.** They send nothing to plugins; each press swaps the visible page, which reaches the plugin as `willDisappear` for every Tokitty key followed by `willAppear` when the page comes back. Every `willDisappear` arrived twice. So tokitty re-sends images on every `willAppear`, treats repeated appear/disappear events as idempotent, and does not draw to keys that are off-page.
- **Ring.** Not reachable; see above.
- **Focus, non-elevated.** A `LIMITED` scheduled task (non-elevated, not the process that received the last input) found both Windows Terminal windows and all their tabs through UI Automation, selected a tab with `SelectionItemPattern.Select()`, and `SetForegroundWindow` returned true with the Terminal window actually becoming foreground while another app had focus. Tokitty as a long-running process may be treated differently from a freshly started task by the foreground lock, so this gets rechecked once tokitty itself does it.

## Remaining spikes

1. "Always": the `updatedPermissions` shape that the current Claude Code actually accepts, what the terminal's own "don't ask again" writes for Bash, Edit/Write, WebFetch, and MCP tools, and whether a late Always (after a terminal answer) is ignored like a late deny.
2. Parallel tool calls in one turn that each need permission: confirm the transcript lookup assigns each hook the right tool use id.
3. Foreground from the real tokitty process, as noted above.

## Dependencies

Python side: UI Automation through `comtypes` (or the `uiautomation` package, which wraps it) and the existing Pillow for key images, both Windows-only and optional, so non-Windows installs are unaffected. Plugin side: a plain HTML/JS page with a manifest, no build step and no npm dependencies, kept in a `streamdock/` folder in this repo and copied into `%APPDATA%\HotSpot\StreamDock\plugins\<uuid>.sdPlugin` by an install command. VSD Craft only loads new plugins at startup.
