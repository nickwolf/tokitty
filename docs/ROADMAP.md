# Tokitty Roadmap

Work is tracked on GitHub — one milestone per phase, one issue per work item.
Each phase is built on its own feature branch and lands on `main` via a PR.
Full design: [docs/superpowers/specs/2026-07-13-tokitty-v2-design.md](superpowers/specs/2026-07-13-tokitty-v2-design.md).

## Phase 1 — Finer cat (`feat/sprite-upgrade`)

Higher-resolution sprites: ~30×26 grid at SCALE 3–4 (from 15×13 @ 7), same on-screen
size, ~4× the detail. Palette gains pattern support (stripes/patches) for Phase 4.
All existing states redrawn once.

## Phase 2 — A live cat (`feat/activity-states`)

Live Claude Code activity via hooks: idle / thinking / working (at a tiny laptop) /
**permission — the cat raises a flag when Claude is waiting on you** / done-hop.
`--install-hooks` merges hook entries into settings.json (backup, additive-only,
idempotent). Multi-session aware: the permission-waiting session wins.

## Phase 3 — Two cats (`feat/dual-account`)

Two subscriptions, two cats: one card, two cat/bar panes, each with its own poller,
mood, capped/wake sequence, and flag. Configured via `accounts.json`; without it,
tokitty behaves exactly as today.

## Phase 4 — Your cat (`feat/customization`)

Coat presets (orange tabby, gray tabby, black, white, calico), full color picker
(stdlib colorchooser) for coat/card/bars, and optional per-cat names.

## Phase 5 — Show the cat (`docs/screenshots`)

README screenshots and a GIF of the flag/wake animations, captured via the
debug-state harness, in `docs/media/`. Gates the public glamour pass.

## Backlog

1. CI matrix (GitHub Actions, ubuntu/macos/windows)
2. ntfy threshold notifications
3. Autostart per OS
4. Tray icon (`pystray`, native path only)
5. Idle wandering across the card
6. Per-model weekly bars
7. Click-to-pet + purr
8. Burn-rate projection
9. PNG sprite upgrade (only if vector rects become limiting)
10. PySide6 transparent-cat rewrite (separate project decision)

## Inspiration

State flow and permission flag inspired by [sidecrab](https://github.com/zvoque/sidecrab)
and [claude-status-bar](https://github.com/m1ckc3s/claude-status-bar); customization,
thinking-along, and done-hop by [comnyang](https://www.comnyang.com/en); coat variants by
[scamp-cat](https://github.com/LordAizen1/scamp-cat). All art and code here are original.
