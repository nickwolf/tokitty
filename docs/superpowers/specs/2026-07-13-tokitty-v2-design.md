# Tokitty v2 — Design Spec

**Date:** 2026-07-13
**Status:** Approved by owner — ready for implementation planning
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

## Phase 1 — Finer cat (sprite resolution upgrade)

- Grid goes from **15×13 @ SCALE 7** to **~30×26 @ SCALE 3–4** (exact numbers tuned by eye with the debug harness; on-screen footprint stays roughly the same). Rendering stays Canvas-rect vector — this is the thing that keeps pixel art crisp across DPI, do not switch to bitmaps.
- Same authoring pipeline as v1: procedural template generation (shapes stamped onto a grid) → hand-tuning → baked-in static data. All ~21 existing states are redrawn once at the new resolution.
- The palette dict gains **pattern support** (per-region sub-palettes for tabby stripes / calico patches) now, because Phase 4 needs it and re-plumbing later would touch every sprite again.
- Eyeball pass over every state via `TOKITTY_DEBUG_STATE` before merge.

## Phase 2 — A live cat (activity states + permission flag)

The big architectural addition: a **second data source** alongside the usage poller. Today the cat's state is purely "how close to the limit"; this phase adds "what is Claude Code doing right now".

### Data source: Claude Code hooks

Alternatives considered and rejected: transcript-tail parsing (fragile format, laggy) and process-watching (cannot see permission waits at all). Hooks are how both sidecrab and claude-status-bar do it.

- **`python -m tokitty --install-hooks`** merges tokitty hook entries into `settings.json` for every configured account's config dir (default: `~/.claude`; plus whatever `accounts.json` lists — see Phase 3). Rules:
  - Timestamped backup of each `settings.json` before writing.
  - **Additive-only merge** — never modify or remove existing hook entries (the owner's settings.json carries load-bearing gsd-*/dippy hooks; treat every user's existing hooks the same way).
  - Idempotent: re-running detects already-installed tokitty entries and does nothing.
  - A matching `--uninstall-hooks` removes exactly the entries it added, nothing else.
- The hook command is a small **stdlib-only Python script shipped in the repo** (`tokitty/hook_writer.py`, invoked as `python3 -m tokitty.hook_writer`): reads the hook event JSON from stdin, writes/updates a per-session state file `<config-dir>/tokitty/sessions/<session_id>.json` (~100 bytes: last event, tool name, timestamp). No daemon, no socket, no dependencies. Must never block or crash the hook pipeline: any error → exit 0 silently.
- Hook events registered: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SessionEnd`.

### Per-session state machine (inside tokitty)

| Event | Activity state |
|---|---|
| `UserPromptSubmit` | **thinking** |
| `PreToolUse` | **working** (tool name captured for a small label, e.g. "Editing") |
| `PostToolUse` | thinking |
| `Notification` (permission request) | **permission** — flag goes up |
| `Stop` | **done-hop** (one-shot, ~1.5s), then idle |
| `SessionEnd`, or no events for N minutes (default 30) | session gone; stale session files cleaned up by tokitty |

### Aggregation and pose priority

- Each cat watches **all** session files for its account and displays the highest-priority session: **permission > working > thinking > idle** (claude-status-bar's model).
- When activity meets the existing usage mood: **permission flag > capped/wake sequence > working / thinking / done-hop > idle**. An idle cat behaves exactly as v1 (mood ladder from usage %). Permission is the one state that also puts a color accent on the card, so it registers in peripheral vision.
- Windows-side tokitty polls the session files (over `\\wsl.localhost` when the config dir is in WSL) every ~1s on the existing worker thread. Tiny files; the access pattern is already proven by credential reads.

### New sprites

Working-at-laptop (2 frames), thinking (2), permission-flag raised/waving (2), done-hop (2–3) ≈ **9 more sprites**, authored at the Phase-1 resolution.

### Risks / defensive notes

- Hook payload shapes are Claude Code implementation details — parse as defensively as the usage endpoint (every field optional).
- The permission state depends on `Notification` firing for permission prompts; document that users with notifications disabled won't get the flag.
- The hook writes happen inside WSL, tokitty reads from Windows: last-writer-wins on a ~100-byte JSON file is acceptable; a torn read is handled by try/except-and-keep-last-good, same philosophy as the API client.

## Phase 3 — Two cats (dual account)

- **One process, one window, two cat/bar panes** stacked vertically (~300×220 card; exact geometry tuned with the debug harness). Not two instances: the single-instance lock stays meaningful, and one always-on-top card beats two floating windows.
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
- Each pane owns its own poller, hook-watcher, mood/activity state machine, capped/wake sequence, and flag. Credential resolution per pane: explicit `config_dir` (or `credentials` path) from `accounts.json`; `TOKITTY_CREDENTIALS` remains the single-account override and is ignored when `accounts.json` exists.
- Owner's real layout (verified 2026-07-13): personal = `~/.claude`, work = `~/.claude-work`, both inside WSL Ubuntu, launched via `claude-personal` / `claude-work` bashrc aliases setting `CLAUDE_CONFIG_DIR`.

## Phase 4 — Your cat (customization)

- **Coat presets**: orange tabby, gray tabby, black, white, calico — palette + pattern dicts over the same sprite grids (plumbing from Phase 1). Chosen via right-click menu per cat; persisted in `accounts.json` (single-account mode persists to the existing config file).
- **Full color picker**: right-click → "Customize…" opens a small dialog built on stdlib `tkinter.colorchooser` for coat base/accent, card background, and bar colors. Stored as hex overrides layered on top of the chosen preset; "reset to preset" clears them.
- **Named cats**: optional per-cat label rendered on the card (defaults "personal"/"work" in dual mode, none in single mode). Doubles as the account disambiguator.
- Explicitly out: hats/accessories (considered, cut — backlog if ever).

## Phase 5 — Show the cat (README media)

- PNG stills of key states (idle mood ladder, working, thinking, permission flag, capped/flopped; dual-cat card) plus one GIF of the flag or wake animation. Captured via `TOKITTY_DEBUG_STATE` — no waiting for real caps.
- Stored in `docs/media/`, embedded in the README above the fold. This is the gate Nick set for publicizing the repo: media lands only after Phases 1–4 make the cat worth photographing.

## Backlog (carried from v1 spec, re-ranked)

1. CI matrix (GitHub Actions ubuntu/macos/windows — non-GUI tests + xvfb Tk smoke test)
2. ntfy threshold notifications (edge-triggered with hysteresis)
3. Autostart per OS
4. Tray icon (`pystray`, native path only)
5. Idle wandering across the card (sidecrab/scamp-style; supersedes v1's "cat roams the card" item)
6. Per-model weekly bars
7. Click-to-pet + purr
8. Burn-rate projection
9. PNG sprite upgrade (only if vector rects ever become limiting)
10. PySide6 transparent-cat rewrite (fenced as a separate project decision)

## Testing

- **Unit tests** (pytest, no GUI): `test_activity.py` (event → state machine, aggregation priority, stale-session cleanup), `test_hook_writer.py` (stdin JSON → session file, error-swallowing), `test_hook_install.py` (additive merge / idempotency / uninstall against a fixture copied from a real settings.json **with existing hooks**), `test_accounts.py` (accounts.json parsing, absent-file fallback to v1 behavior).
- **Forced-state harness** extended: `TOKITTY_DEBUG_STATE` gains the new activity states; a `TOKITTY_DEBUG_ACCOUNTS=2` flag renders the dual-pane layout with fake data.
- **Manual per phase**: run against the real personal + work accounts before each PR merges; verify flag raises on a real permission prompt.

## Risks accepted

- Hooks and the usage endpoint are both undocumented/unversioned Claude Code surfaces; either can change without notice. Mitigation unchanged: defensive parsing, last-good display, Confused degradation, README disclaimer.
- `settings.json` merge is the one place tokitty writes to a file it doesn't own. Mitigations: explicit opt-in command, timestamped backup, additive-only, idempotent, tested against fixtures with pre-existing hooks.
