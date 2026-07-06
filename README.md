# Tokitty

A cat-themed desktop widget that shows your live Claude Code usage (session %, weekly %, reset countdowns, and extra-usage credits) with a pixel cat whose mood reflects how close you are to the limit. When a limit is capped, the cat rests, then stirs, then wakes up as the reset approaches, then hops back to sleep once usage clears. Does not assist with boredom and existential dread upon hitting weekly limit.

Once Tokitty has a snapshot, it keeps counting down using its own clock, no live connection needed to know when a known reset time arrives. If a poll fails (for example, the OAuth access token going stale between Claude Code sessions), Tokitty keeps showing that same cached countdown rather than blanking out, and only surfaces a small warning once the countdown should already be done and it still can't confirm the reset actually happened.

**Not affiliated with Anthropic (but I am open to it, *wink wink*).** "Claude" and "Claude Code" are Anthropic's marks, used here only to describe compatibility.

## Security & privacy

Tokitty only *reads* your local Claude Code OAuth credentials file: it never writes to it, never touches the refresh token, and never transmits the access token anywhere except in a single request to `api.anthropic.com`. Window position is the only thing Tokitty persists, and it's stored in your OS's normal per-user config directory, never inside this repo.

## Platforms tested

- **Windows 11, native Python 3.13 (`pythonw.exe`), with Claude Code running inside WSL2**: the primary, recommended setup, and the only platform verified end-to-end. The full pipeline (credential resolution, WSL fallback, live API polling, mood/wake-sequence logic, rendering) runs clean against a real account, and the window itself (drag, always-on-top, sizing, text legibility, animation) has been visually confirmed on a real desktop across several rounds of screenshot-driven fixes. In daily use..
- **Native Linux, macOS**: untested. Don't assume these work until someone actually runs it there, but it'll probably work just fine.

*(Update this table as each platform is actually verified.)*

## Setup

### Windows (Claude Code in WSL2, recommended path)

1. Install Python 3.10+ from [python.org](https://www.python.org/) (bundles tkinter).
2. `git clone` this repo, then from the repo root: `pythonw.exe -m tokitty`

### Windows (Claude Code installed natively, no WSL)

Same as above: `resolve_credentials_source()` finds `~/.claude/.credentials.json` directly, no WSL bridge involved.

### Linux

1. `sudo apt install python3-tk` (or your distro's equivalent).
2. `python3 -m tokitty`

### macOS

1. Install Python from [python.org](https://www.python.org/) (recommended over Apple's system Python or some Homebrew builds, which can have flaky Tcl/Tk).
2. `python3 -m tokitty`

## Configuration

If Tokitty can't find your Claude Code credentials automatically (e.g. more than one install), set:

```bash
export TOKITTY_CREDENTIALS=/path/to/.claude/.credentials.json
```

## How this was built

Tokitty was built with [Claude](https://claude.com/product/claude-code) (Fable 5) using a subagent-driven-development workflow: an owner session designed the spec and implementation plan, then dispatched a fresh implementer subagent per task with a reviewer subagent checking spec compliance and code quality before each task landed. Model tiers were deliberately mixed: cheaper/faster models handled the mechanical, fully-specified logic modules (credentials, API client, locking, mood/wake-sequence state machine, formatting), while a standard-tier model handled the threading/integration work. The pixel-art sprites, the tkinter window, and the animation loop were built directly by the owner session rather than delegated, since that's the part where a bit of craft mattered most. The sprite templates were generated procedurally (simple shapes stamped onto a grid) to avoid hand-counting errors, then hand-tuned and baked in as static data.

The review loop caught and fixed several real bugs along the way: a monkeypatch self-recursion bug in a test, a refresh-request race condition in the polling worker (found by review, "fixed" once by the owner session in a way that introduced a *worse* regression, caught again by review, fixed properly on the third pass), a `wsl.exe` argv-mangling quirk found only by actually running the WSL-fallback code path against a real account instead of trusting mocked tests, and an unconditional `tkinter` import that would have silently broken the no-GUI-toolkit-needed `--debug-print` path. The commit history is the actual record of that process, not just the finished result.

## Known limitations (POC)

- This uses `api.anthropic.com/api/oauth/usage`, an **undocumented endpoint** that may change or disappear without notice.
- Running Tokitty *inside* WSL (via WSLg) is architecturally supported (same credential-resolution code path as native Linux), but has never actually been run: `python3-tk` isn't installed in the reference dev environment.
- Sprite art is a first pass: a 15x13 grid composed from three reusable pose templates (sitting calm, sitting alert, lying down), not a fully independent illustration per state. Recognizable and animated, not polished.

## Roadmap

See `docs/superpowers/specs/2026-07-02-tokitty-design.md` for the full stack-ranked enhancement list (ntfy notifications, autostart, tray icon, per-model bars, click-to-pet, burn-rate projection, color customization, and more).

## License

MIT, see [LICENSE](LICENSE).
