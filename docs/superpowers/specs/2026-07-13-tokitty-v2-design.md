# Tokitty v2 — Design Spec

**Date:** 2026-07-13
**Status:** Approved by owner; revised same day after design review (hook pipeline hardening, Notification matcher, WSL polling cadence, dual-account caveats)
**What:** The second wave of Tokitty features, incorporating ideas from sidecrab, claude-status-bar, comnyang, and scamp-cat: a higher-resolution cat, live Claude Code activity states (idle/working/thinking/permission-flag) fed by hooks, dual-account support (personal + work subscriptions), cat customization, and README screenshots. Work is tracked as a GitHub roadmap (milestones + issues) and built on feature branches per phase.

## Inspiration credit (what was borrowed from where)

- **sidecrab** (github.com/zvoque/sidecrab): hook-based activity detection, working-at-laptop pose, permission "flag you down" state, install-time hook merge with backup.
- **claude-status-bar** (github.com/m1ckc3s/claude-status-bar): multi-session aggregation with permission-waiting prioritized, tool-name labels.
- **comnyang** (comnyang.com): coat color/pattern customization, thinking-along face, "agent done" happy hop, cat naming.
- **scamp-cat** (github.com/LordAizen1/scamp-cat): multiple coat variants as first-class presets.

Ideas only — all sprite art and code remain original (existing repo requirement).

## Process changes (this wave onward)

- **Roadmap lives on GitHub**: one milestone per phase below, one issue per work item, labels `phase-1`…`phase-5`, `backlog`, `enhancement`, `art`. `docs/ROADMAP.md` in-repo mirrors the phase structure and links to the milestones; the README points at `docs/ROADMAP.md`.
- **Branch-per-phase workflow**: each phase is built on a feature branch (`feat/sprite-upgrade`, `feat/activity-states`, `feat/dual-account`, `feat/customization`, `docs/screenshots`) and lands on `main` via a PR that closes its milestone's issues. Commits within a branch stay stage-sized and reviewable, same as v1. No AI-attribution lines (existing convention).
- Phases are ordered 1→5 but 2 may start on v1-resolution placeholder sprites if the art in Phase 1 runs long — the hook engineering and the art are independent; only the *final* activity sprites depend on the new grid.

## Phase 1 — Finer cat (sprite resolution upgrade)

- Grid goes from **15×13 @ SCALE 7** to **~30×26 @ SCALE 3–4** (exact numbers tuned by eye with the debug harness; on-screen footprint stays roughly the same). Rendering stays Canvas-rect vector — this is the thing that keeps pixel art crisp across DPI, do not switch to bitmaps.
- Same authoring pipeline as v1: procedural template generation (shapes stamped onto a grid) → hand-tuning → baked-in static data. All ~21 existing states are redrawn once at the new resolution.
- The palette dict gains **pattern support** (per-region sub-palettes for tabby stripes / calico patches) now, because Phase 4 needs it and re-plumbing later would touch every sprite again.
- Eyeball pass over every state via `TOKITTY_DEBUG_STATE` before merge.

## Phase 2 — A live cat (activity states + permission flag)

The big architectural addition: a **second data source** alongside the usage poller. Today the cat's state is purely "how close to the limit"; this phase adds "what is Claude Code doing right now".

### Data source: Claude Code hooks

Alternatives considered and rejected: transcript-tail parsing (fragile format, laggy) and process-watching (cannot see permission waits at all). Hooks are how both sidecrab and claude-status-bar do it.

**Hook writer script.** A **standalone, single-file, stdlib-only** Python script (`tokitty/hook_writer.py` in the repo, but designed to run with no package context). `--install-hooks` **copies it to a WSL-native ext4 location** — `<config-dir>/tokitty/hook_writer.py` — and registers the command as:

```
python3 /home/<user>/.claude/tokitty/hook_writer.py --sessions-dir /home/<user>/.claude/tokitty/sessions
```

Rationale (all three bite otherwise):
- `python3 -m tokitty.hook_writer` would fail everywhere: the package is never installed in WSL and hooks run with arbitrary cwd.
- Interpreter + script startup from ext4 is ~50 ms; from `/mnt/c` (drvfs) it's 500 ms+ — an unacceptable tax on **every tool call in every session**. **Benchmark gate: hook wall-time < 100 ms or Phase 2 doesn't ship.**
- Baking `--sessions-dir` into each settings.json's command means `~/.claude-work`'s hooks write to `~/.claude-work/tokitty/sessions/` **by construction**. Do not rely on `CLAUDE_CONFIG_DIR` being visible to the hook process — it is not a documented hook environment variable.

**Path duality:** the installer may run on the Windows side (where the config dir is `\\wsl.localhost\Ubuntu\home\...`) but the command embedded in settings.json needs the WSL-native `/home/...` form. The installer must map explicitly between the two representations.

**Output discipline (session-safety critical).** Hook stdout/exit codes are live control signals to Claude Code: on `PreToolUse`, stdout JSON can be parsed as a **permission decision** and exit 2 **blocks the tool**; on `UserPromptSubmit`, stdout is **injected into the prompt context**. A stray print or traceback could auto-answer permissions or contaminate prompts in every session, including load-bearing third-party hook workflows. Therefore the script: **never writes to stdout, never exits non-zero**, and wraps its entire `main()` in a top-level bare `try/except: pass`. This is a tested invariant, not a convention.

**Writes are atomic and ordered.** Session file written via temp-file + `os.replace` (atomic on ext4; the UNC reader sees old-or-new, never torn), carrying a **monotonic timestamp/counter**; the reader ignores regressions. This single mechanism handles: parallel tool calls flapping states out of order, stale hours-old `Stop` files causing a spurious done-hop at tokitty startup, and races with stale-file cleanup.

**Installer (`python -m tokitty --install-hooks`)** merges tokitty hook entries into `settings.json` for every configured account's config dir (default `~/.claude`; plus whatever `accounts.json` lists). Rules:
- Timestamped backup of each `settings.json` before writing.
- **Additive-only merge** — never modify or remove existing hook entries (the owner's settings.json carries load-bearing gsd-*/dippy hooks; treat every user's existing hooks the same way).
- Idempotent: detects already-installed tokitty entries (identified by a `tokitty` marker token in the command string) in **both `settings.json` and `settings.local.json`** and does nothing.
- `--uninstall-hooks` removes exactly the marker-matched entries, nothing else.
- Prints "restart running Claude Code sessions if the cat doesn't react" — settings hot-reload is claimed by docs but verified once empirically, not assumed.

**Events registered:** `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification` **with matcher `permission_prompt`** (a bare Notification also fires on `idle_prompt` and other noise — the matcher is what makes the flag mean "waiting on you", and payload-string matching is not a safe substitute), `Stop`, `SubagentStop`, `SessionEnd`.

### Per-session state machine (inside tokitty)

| Event | Activity state |
|---|---|
| `UserPromptSubmit` | **thinking** |
| `PreToolUse` | **working** (tool name captured for a small label, e.g. "Editing") |
| `PostToolUse` | thinking |
| `Notification` (matcher `permission_prompt`) | **permission** — flag goes up |
| `Stop` | **done-hop** (one-shot, ~1.5s), then idle |
| `SessionEnd` | session gone; file cleaned up |
| No events for 2–5 min (tunable) | decay to **idle** (catches Esc-interrupts, which fire no hook) |
| No events for 30 min | session considered gone; stale file cleaned up |

Known semantics, so nobody chases "bugs" later:
- `Stop` fires after **every** assistant response, not at "task done". The done-hop is gated on a preceding working/thinking stretch of ≥ some threshold (tunable, e.g. 20s) so conversational back-and-forth doesn't trigger constant hopping.
- **No hook fires when the user answers a permission prompt.** The flag's contract is: *raised at the prompt, lowered on the next activity event from that session* (approval → next `PostToolUse`/`PreToolUse`; denial → next event). A long tool run right after approval keeps the flag up until it completes — accepted, not fixable with current hooks.
- Whether subagent (Task) tool calls fire parent-session hooks, and under which `session_id`, is **undocumented — one empirical test before this state machine is finalized**. `SubagentStop` is registered to avoid a stuck "working (Task)".
- Whether OS-notification preferences suppress the `Notification` hook is undocumented — verify empirically before the README says anything about it.

### Aggregation and pose priority

- Each cat watches **all** session files for its account and displays the highest-priority session: **permission > working > thinking > idle** (claude-status-bar's model).
- When activity meets the existing usage mood: **permission flag > capped/wake sequence > working / thinking / done-hop > idle**. An idle cat behaves exactly as v1 (mood ladder from usage %). Permission is the one state that also puts a color accent on the card, so it registers in peripheral vision.

### Activity watcher (reader side)

- Each account gets its **own watcher thread**, separate from its usage poller — the shipped `Poller` sleeps in 120s cycles and a usage fetch can hold it for the 10s HTTP timeout; 1s activity reads cannot share it. All threads publish through the existing lock-protected snapshot + `root.after` UI-tick pattern (tkinter is not thread-safe; keep the v1 architecture).
- **Adaptive cadence, WSL-respectful:** ~1s only while a session file has been active recently; decays to 10–30s when everything is idle. Accessing `\\wsl.localhost` boots a stopped distro and continuous access defeats WSL idle shutdown (permanent multi-GB vmmem) — the watcher must **detect WSL-down and back off rather than being the thing that keeps it awake**; v1's "proven access pattern" claim was at 120s cadence, which does not transfer to 1 Hz. Multi-second UNC hangs must not pile up ticks (single in-flight read, skip-if-busy).

### New sprites

Working-at-laptop (2 frames), thinking (2), permission-flag raised/waving (2), done-hop (2–3) ≈ **9 more sprites**, authored at the Phase-1 resolution (placeholder v1-res versions acceptable while Phase 1 is in flight).

### README security section (part of this phase, not optional)

Phase 2 falsifies v1's public promises ("only reads", "persists nothing but window position"): tokitty now writes to `settings.json` (opt-in, backed up), installs a hook that sees tool-call metadata, and writes session-state files. The README security statement is rewritten in the Phase 2 PR — this is a hard blocker on the Phase 5 publicity gate.

## Phase 3 — Two cats (dual account)

- **One process, one window, two cat/bar panes** stacked vertically (~300×220 card; exact geometry tuned with the debug harness). Not two instances: the single-instance lock stays meaningful, and one always-on-top card beats two floating windows.
- **Named work item: pane-ize `ui.py`.** The shipped layout is absolute `.place()` coordinates off module constants (`STATS_X`, `CARD_WIDTH/HEIGHT`); extracting a parameterized Pane component touches every widget, the debug harness, and position-restore (the taller card can push a saved bottom-edge position off-screen — the existing clamp should catch it; test it does). This is Phase 3's biggest diff.
- New optional config file `accounts.json` in the existing per-user state dir (`%LOCALAPPDATA%\Tokitty\` etc.):

```json
{
  "accounts": [
    {"name": "personal", "config_dir": "\\\\wsl.localhost\\Ubuntu\\home\\cptsmidge\\.claude", "coat": "orange_tabby"},
    {"name": "work",     "config_dir": "\\\\wsl.localhost\\Ubuntu\\home\\cptsmidge\\.claude-work", "coat": "gray_tabby"}
  ]
}
```

- If `accounts.json` is absent, tokitty behaves exactly as v1: single account, automatic credential resolution, single-pane card. Zero-config users are unaffected.
- Each pane owns its own usage poller, activity watcher, mood/activity state machine, capped/wake sequence, and flag (dual mode = 4 worker threads). Credential resolution per pane: explicit `config_dir` (or `credentials` path) from `accounts.json`. `TOKITTY_CREDENTIALS` remains the single-account override; when both it and `accounts.json` are present, `accounts.json` wins and tokitty **logs/shows a startup warning** rather than silently ignoring the env var.
- **Verify the work account's response shape before planning this phase** — every v1 "verified fact" came from one personal Pro account; a Teams/Enterprise account may differ (seat limits, absent fields). One curl with the work token.
- **The work pane's steady state is stale.** Work tokens expire ~1h after that account's Claude Code last ran, so outside work hours the pane is permanently in "stale token" territory — v1's grace logic was designed for transient gaps. Define the resting look: last-good numbers dimmed + sleeping cat + "last seen HH:MM", no warning styling (this is normal, not an error).
- Owner's real layout (verified 2026-07-13): personal = `~/.claude`, work = `~/.claude-work`, both inside WSL Ubuntu, launched via `claude-personal` / `claude-work` bashrc aliases setting `CLAUDE_CONFIG_DIR`. (Docs-verified: with `CLAUDE_CONFIG_DIR` set, hooks are read from that dir's settings.json — the dual wiring is sound.)

## Phase 4 — Your cat (customization)

- **Coat presets**: orange tabby, gray tabby, black, white, calico — palette + pattern dicts over the same sprite grids (plumbing from Phase 1). Chosen via right-click menu per cat; persisted in `accounts.json` (single-account mode persists to the existing config file).
- **Full color picker**: right-click → "Customize…" opens a small dialog built on stdlib `tkinter.colorchooser` for coat base/accent, card background, and bar colors. Stored as hex overrides layered on top of the chosen preset; "reset to preset" clears them.
- **Named cats**: optional per-cat label rendered on the card (defaults "personal"/"work" in dual mode, none in single mode). Doubles as the account disambiguator.
- Explicitly out: hats/accessories (considered, cut — backlog if ever).

## Phase 5 — Show the cat (README media)

- PNG stills of key states (idle mood ladder, working, thinking, permission flag, capped/flopped; dual-cat card) plus one GIF of the flag or wake animation. Captured via `TOKITTY_DEBUG_STATE` — no waiting for real caps.
- Stored in `docs/media/`, embedded in the README above the fold. This is the gate Nick set for publicizing the repo: media lands only after Phases 1–4 make the cat worth photographing. Blocked on the Phase 2 security-section rewrite having landed.

## Backlog (carried from v1 spec, re-ranked)

1. CI matrix (GitHub Actions ubuntu/macos/windows — non-GUI tests + xvfb Tk smoke test)
2. ntfy threshold notifications (edge-triggered with hysteresis)
3. Autostart per OS
4. Tray icon (`pystray`, native path only)
5. Idle wandering across the card (sidecrab/scamp-style; supersedes v1's "cat roams the card" item)
6. Per-model weekly bars
7. Click-to-pet + purr
8. Burn-rate projection
9. Per-project opt-out for the permission flag (unattended/background sessions shouldn't flag an empty room)
10. PNG sprite upgrade (only if vector rects ever become limiting)
11. PySide6 transparent-cat rewrite (fenced as a separate project decision)

## Testing

- **Unit tests** (pytest, no GUI): `test_activity.py` (event → state machine, aggregation priority, done-hop gating, idle decay, stale-session cleanup, monotonic-regression rejection), `test_hook_writer.py` (stdin JSON → atomic session file; **stdout stays empty and exit code stays 0 on every malformed input**), `test_hook_install.py` (additive merge / idempotency across settings.json + settings.local.json / uninstall marker-matching, against a fixture copied from a real settings.json **with existing hooks**; Windows↔WSL path mapping), `test_accounts.py` (accounts.json parsing, absent-file fallback to v1 behavior, TOKITTY_CREDENTIALS conflict warning).
- **Empirical pre-flight for Phase 2** (before the state machine is finalized): subagent/Task hook behavior and session_id attribution; settings hot-reload; Notification-vs-OS-notification-preferences; hook wall-time benchmark (< 100 ms gate).
- **Forced-state harness** extended: `TOKITTY_DEBUG_STATE` gains the new activity states; a `TOKITTY_DEBUG_ACCOUNTS=2` flag renders the dual-pane layout with fake data.
- **Manual per phase**: run against the real personal + work accounts before each PR merges; verify flag raises on a real permission prompt and lowers on approval-then-activity.

## Risks accepted

- Hooks and the usage endpoint are both undocumented/unversioned Claude Code surfaces; either can change without notice. Mitigation unchanged: defensive parsing, last-good display, Confused degradation, README disclaimer.
- `settings.json` merge is the one place tokitty writes to a file it doesn't own. Mitigations: explicit opt-in command, timestamped backup, additive-only, idempotent, marker-based uninstall, tested against fixtures with pre-existing hooks.
- The permission flag has an inherent lag window (no hook on prompt-answer); documented contract rather than a bug to fix.
