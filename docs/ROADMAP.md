# Tokitty Roadmap

Work is tracked on GitHub — one milestone per phase, one issue per work item.
Each phase is built on its own feature branch and lands on `main` via a PR.

**Phases 1–5 are complete and shipped.** They are kept below as the record of how
Tokitty was built; ongoing work is in the backlog at the end of this file.
Full design: [docs/superpowers/specs/2026-07-13-tokitty-v2-design.md](superpowers/specs/2026-07-13-tokitty-v2-design.md).

## [Phase 1 — Finer cat](https://github.com/nickwolf/tokitty/milestone/1) (`feat/sprite-upgrade`)

Higher-resolution sprites: ~30×26 grid at SCALE 3–4 (from 15×13 @ 7), same on-screen
size, ~4× the detail. Palette gains pattern support (stripes/patches) for Phase 4.
All existing states redrawn once.

## [Phase 2 — A live cat](https://github.com/nickwolf/tokitty/milestone/2) (`feat/activity-states`)

Live Claude Code activity via hooks: idle / thinking / working (at a tiny laptop) /
**permission — the cat raises a flag when Claude is waiting on you** / done-hop.
`--install-hooks` merges hook entries into settings.json (backup, additive-only,
idempotent). Multi-session aware: the permission-waiting session wins.

## [Phase 3 — Two cats](https://github.com/nickwolf/tokitty/milestone/3) (`feat/dual-account`)

Two subscriptions, two cats: one card, two cat/bar panes, each with its own poller,
mood, capped/wake sequence, and flag. Configured via `accounts.json`; without it,
tokitty behaves exactly as today.

## [Phase 4 — Your cat](https://github.com/nickwolf/tokitty/milestone/4) (`feat/customization`)

Coat presets (orange tabby, gray tabby, black, white, calico), full color picker
(stdlib colorchooser) for coat/card/bars, and optional per-cat names.

## [Phase 5 — Show the cat](https://github.com/nickwolf/tokitty/milestone/5) (`docs/screenshots`)

README screenshots and a GIF of the flag/wake animations, captured via the
debug-state harness, in `docs/media/`. Gates the public glamour pass.

## Backlog

The backlog lives on the [Tokitty backlog board](https://github.com/users/nickwolf/projects/1),
where drag order is the ranking and each item carries a size. The open items are also
listed by the [`backlog` label](https://github.com/nickwolf/tokitty/issues?q=is%3Aissue+is%3Aopen+label%3Abacklog).

This section used to duplicate that list by hand, which meant it quietly went stale
every time something shipped — CI matrix, the tray icon, and burn-rate projection all
sat here as "planned" well after they landed. A link that can't drift is worth more
than a list that does.

## Inspiration

State flow and permission flag inspired by [sidecrab](https://github.com/zvoque/sidecrab)
and [claude-status-bar](https://github.com/m1ckc3s/claude-status-bar); customization,
thinking-along, and done-hop by [comnyang](https://www.comnyang.com/en); coat variants by
[scamp-cat](https://github.com/LordAizen1/scamp-cat). All art and code here are original.
