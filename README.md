# Tokitty

A cat-themed desktop widget that shows your live Claude Code usage (session %, weekly %, reset countdowns, and extra-usage credits) with a pixel cat whose mood reflects how close you are to the limit. When a limit is capped, the cat rests, then stirs, then wakes up as the reset approaches, then hops back to sleep once usage clears. Does not assist with boredom and existential dread upon hitting weekly limit.

Once Tokitty has a snapshot, it keeps counting down using its own clock, no live connection needed to know when a known reset time arrives. If a poll fails (for example, the OAuth access token going stale between Claude Code sessions), Tokitty keeps showing that same cached countdown rather than blanking out, and only surfaces a small warning once the countdown should already be done and it still can't confirm the reset actually happened.

**Not affiliated with Anthropic (but I am open to it, *wink wink*).** "Claude" and "Claude Code" are Anthropic's marks, used here only to describe compatibility.

## Live activity (thinking / working / permission / done)

Optional, off by default. Run:

```bash
python -m tokitty --install-hooks
```

and the cat starts reacting to what a running Claude Code session is doing: a thinking pose while Claude is composing a response, a working pose (with the tool name) while it's mid-tool-call, a flag when Claude is waiting on you for a permission prompt, and a little done-hop when a work stretch wraps up. `python -m tokitty --uninstall-hooks` removes it again. Existing running Claude Code sessions need to be restarted to pick up a fresh install or uninstall — hook edits aren't hot-reloaded.

## Security & privacy

Tokitty only *reads* your local Claude Code OAuth credentials file: it never writes to it, never touches the refresh token, and never transmits the access token anywhere except in a single request to `api.anthropic.com`. Window position is the only thing Tokitty's core polling persists, and it's stored in your OS's normal per-user config directory, never inside this repo.

The live-activity feature above is opt-in and changes this picture only if you turn it on:

- **Installer.** `--install-hooks` registers a small hook script in each configured Claude Code config dir's `settings.json` (merged additively into any existing hooks, with a timestamped backup of `settings.json` taken first) and copies the hook script itself to `<config-dir>/tokitty/hook_writer.py`. It's idempotent — re-running it skips events already installed — and every entry it adds is tagged so `--uninstall-hooks` can remove exactly tokitty's entries and nothing else.
- **What the hook script sees.** Claude Code invokes it once per hook event (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SubagentStop`, `SessionEnd`) with that event's full JSON payload on stdin, which for `PreToolUse` includes the tool's input arguments. The script only reads that payload to decide what to write — it doesn't read prompts, file contents, or transcripts from anywhere else.
- **What it persists.** Per session, it writes one small JSON state file to `<config-dir>/tokitty/sessions/<session_id>.json` containing just the session id, the event name, a sequence number, a timestamp, and — for tool-call events — the tool name. Prompt text, tool arguments/output, and file contents are never written to that file. On `SessionEnd` the file is deleted; tokitty's own watcher also deletes state files it judges stale (no update within its timeout window) so a crashed or killed session doesn't leave the cat stuck.
- **Failure behavior.** The hook script never writes to stdout and never exits non-zero, under any input — Claude Code treats hook stdout/exit code as live control signals (e.g. a non-zero exit can block the tool call), so the script is wrapped so nothing it does can ever interfere with your actual session. This is covered by tests, not just a claim.
- **Nothing leaves your machine.** None of this activity data is transmitted anywhere; it's read locally by tokitty's own watcher to drive the sprite.

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
- Sprite art is composed from three reusable 28x26 pose templates (sitting calm, sitting alert, lying down) with per-state substitutions, not a fully independent illustration per state.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) — phased plan (higher-res sprites, live activity states with a permission flag, dual-account support, cat customization) plus the backlog (ntfy notifications, autostart, tray icon, per-model bars, click-to-pet, burn-rate projection, and more). Tracked as GitHub milestones/issues on this repo.

## License

MIT, see [LICENSE](LICENSE).
