# Tokitty — Design Spec

**Date:** 2026-07-02
**Status:** Approved by owner (v2, 2026-07-03) — ready for implementation planning
**What:** A whimsical, cat-themed, always-on-top desktop widget showing live Claude subscription usage: session (5-hour) %, weekly %, reset times/countdown, and extra-usage credits. An animated pixel cat's mood reflects how close the owner is to being rate-limited, including a wake-up sequence when a capped limit is about to reset. Cross-platform (Windows, Linux, macOS); intended as a public GitHub repo / portfolio piece under MIT license.

## Verified facts (empirically confirmed 2026-07-02, do not re-derive)

- **Data source:** `GET https://api.anthropic.com/api/oauth/usage` with headers `Authorization: Bearer <accessToken>` and `anthropic-beta: oauth-2025-04-20`. This is the same data Claude Code's `/usage` shows. It is an **undocumented endpoint** — treat every field as optional (see Error handling), and expect it can change or vanish without notice. This matters more now that the tool is public: many users could break at once.
- **Response shape (observed):**
  - `five_hour.utilization` (float, **0–100 scale** — confirmed: 66.0 matched `limits[].percent` 66), `five_hour.resets_at` (ISO 8601 UTC).
  - `seven_day.utilization`, `seven_day.resets_at` — same shapes.
  - `limits[]`: entries with `kind` (`session` | `weekly_all` | `weekly_scoped`), `percent`, `severity` (only `"normal"` observed; other values unknown), `resets_at`, `is_active`, and for `weekly_scoped` a `scope.model.display_name` (e.g. "Fable").
  - `extra_usage`: `is_enabled`, `used_credits` (minor units — observed `362.0` = $3.62), `monthly_limit` (same units), `utilization`.
  - `spend`: `used.amount_minor` / `limit.amount_minor` (integer minor units, `exponent: 2` → divide by 100 for dollars), `percent`, `enabled`.
- **Credentials file:** `~/.claude/.credentials.json` under whichever home directory Claude Code itself runs from. Structure: `claudeAiOauth.{accessToken, refreshToken, expiresAt, scopes, subscriptionType, rateLimitTier}`.
- **`expiresAt` is epoch MILLISECONDS** (13 digits, confirmed). Access tokens live ~1 hour. Claude Code refreshes the token only while it's running; if Claude Code hasn't run recently, the file holds an expired token and re-reading it doesn't help. This is a **normal state**, not an error (usage isn't changing if Claude Code isn't running).
- **This machine's specific layout (owner's setup, not universal):** Claude Code runs inside WSL2 (Ubuntu, user `cptsmidge`); native Windows Python at `C:\Users\nickw\...\Python313\` has its own `.claude` directory but **no credentials file** — the WSL and Windows homes are genuinely different accounts-of-record here. A native Windows Tokitty process must reach into WSL via `\\wsl.localhost\Ubuntu\home\cptsmidge\.claude\.credentials.json` (verified reachable). Path.home()-only resolution would silently find nothing on the Windows side. See Cross-platform architecture below for how this is handled generically.
- **WSLg is active on this machine** (`/mnt/wslg` exists, `$DISPLAY`/`$WAYLAND_DISPLAY` set) — a GUI app launched from inside WSL renders as a normal floating window on the Windows desktop. Confirmed present, but **not adopted as the recommended Windows path** — see Cross-platform architecture for why.
- **Runtime:** Windows has native Python 3.13 with tkinter bundled. WSL/Ubuntu's python3 (3.12.3) does **not** have tkinter installed by default (`sudo apt install python3-tk` required). No pip dependencies planned for the POC on any OS.

## Cross-platform architecture

One stdlib-only Python codebase, structured as a small package (see Repo layout) rather than a single file — a public portfolio repo is judged on first impression, and the OS-branching logic below is exactly the kind of thing that reads badly inline and cleanly when isolated into its own module.

### Credential resolution (`credentials.py`)

Resolution order, stopping at the first hit:

1. **Explicit override**: `TOKITTY_CREDENTIALS` env var, or a path in the user config file, if set. Always wins — this is the escape hatch for anyone with more than one Claude Code install (native + WSL, or multiple machines/profiles).
2. **Home-relative**: `Path.home() / ".claude" / ".credentials.json"`. Correct as-is for native Linux, native macOS, native Windows Claude Code, and for Tokitty itself running inside WSL (same filesystem as Claude Code there).
3. **Windows-only WSL fallback**: if `sys.platform == "win32"` and step 2 found nothing, enumerate distros via `wsl.exe -l -q` and check each one's `/home/<user>/.claude/.credentials.json` (via `wsl.exe -d <distro> -- cat ...` or the `\\wsl.localhost\<distro>\...` UNC path). If **more than one** distro has a credentials file, that's ambiguous — don't guess; drop to the Confused/error state with a prompt to set the override instead of silently picking one.

The **currently-in-use credentials file path is surfaced in the UI** (tooltip or an "i" affordance) so a dual-install mismatch is visible rather than silently showing the wrong account's usage.

Token handling regardless of platform: re-read the file fresh every poll (never cache the token across polls), never touch `refreshToken`, never write the file — refresh is Claude Code's job only. Skip the HTTP call if `expiresAt` (ms) is already past; treat HTTP 401 as a first-class "stale" state, not a crash.

### Recommended launch per OS

- **Windows (this owner's actual setup, and the general case where Claude Code lives in WSL):** run Tokitty as a **native Windows process** (`pythonw.exe`, no console), reaching into WSL via the fallback above. This is the primary, documented, tested path. WSLg-hosted launch (`python3 tokitty.py` from inside WSL) is documented as a secondary option for Linux-minded users, with an explicit caveat: always-on-top may not reliably float over native Windows apps, pixel art may blur at non-100% display scaling, and there's no Windows tray icon from a WSLg process — none of this is verified good or bad yet, it needs an eyeball check on a real monitor before the README claims it works well. If Claude Code runs natively on Windows (no WSL at all), step 2 above resolves directly with no bridge needed.
- **Native Linux:** `python3 -m tokitty` (or an installed console script). Needs `python3-tk` (or distro equivalent) as a system package — documented in README, not silently assumed.
- **macOS:** `python3 -m tokitty`, python.org's installer recommended (bundles a modern Tcl/Tk 8.6 — Apple's system Tk and some Homebrew builds are known-flaky for exactly the window behaviors this app needs). See macOS-specific UI notes below.

Tray icon and OS-native notifications (roadmap items) require the **native** path on every OS — a WSLg-hosted process cannot reach the Windows tray or toast notifications. This is a real constraint on how far the "run inside WSL" option can go, and is one more reason native is the primary recommendation, not an equally-weighted alternative.

### Platform-specific UI guards

- **DPI (Windows only):** `ctypes.windll.shcore.SetProcessDpiAwareness(2)` at startup, guarded by `sys.platform == "win32"`, before creating the root window.
- **macOS topmost reliability:** after window creation, explicit `lift()` + `deiconify()`; re-assert `-topmost` periodically (e.g. every few seconds via `after()`) since Aqua is known to drop it on app-switch. If overrideredirect misbehaves on the target Tk version, the macOS-specific `::tk::unsupported::MacWindowStyle` route is a fallback to try — verify against the actual Tk version in use rather than hardcoding args blindly.
- **Wayland positioning (native Linux, non-XWayland session):** Wayland forbids a client from placing its own top-level window. Detect via `XDG_SESSION_TYPE == "wayland"`; if detected, still launch normally but skip position-restore/persistence and show a one-line note rather than silently failing to honor a saved position. WSLg is XWayland-backed, so this doesn't affect the Windows+WSL path.
- **Rendering stays vector, not bitmap:** the cat is drawn from pixel-grid data as Canvas rectangles (see Pixel cat), not a bitmap image — this is a deliberate choice that sidesteps DPI/scaling blur on every platform, not just Windows.

### Single-instance lock (`lock.py`)

An OS-native **advisory file lock held for the process's lifetime** — `fcntl.flock`/`lockf` on POSIX, `msvcrt.locking` on Windows — not a PID file. The OS releases the lock automatically on process exit, including a crash, which avoids both classic PID-file bugs (stale lock after a crash, and PID-reuse after reboot falsely indicating a live instance). The lock file lives in the OS's proper per-user state directory (see below), not the repo directory. Known limitation, acceptable: the lock is per-OS-filesystem-namespace, so a WSL-hosted instance and a native-Windows instance won't see each other's lock — not worth solving for a personal widget.

### State/config location

Window position and the lock file live in the OS-appropriate user directory, **never in the repo/install directory**:
- Windows: `%LOCALAPPDATA%\Tokitty\`
- macOS: `~/Library/Application Support/Tokitty/`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/tokitty/`

This is both correct practice and a public-repo hygiene guarantee: since nothing stateful is ever written inside the cloned repo tree, there's no file that could accidentally get `git add`ed later, on top of `.gitignore` already excluding it.

## Development process

Build in logical stages, each landing as its own commit rather than one giant commit for the whole POC (e.g.: repo scaffold + license/readme skeleton → credentials resolution → api client → lock → sprites/mood ladder → ui shell/window chrome → wire polling into ui → capped/wake sequence → error states → tests). The implementation plan should be structured so each step is a coherent, reviewable, individually-committable unit rather than a single end-to-end diff. Commit message authorship follows existing convention: no Co-Authored-By/AI-attribution lines.

## Repo layout

```
tokitty/
  tokitty/
    __main__.py     # entry point; python -m tokitty
    api.py          # usage endpoint client, response parsing (all .get()-defensive)
    credentials.py  # cross-platform resolution (see above)
    sprites.py      # pixel-grid data, palette, mood -> frame mapping
    ui.py           # tkinter window, drag, topmost/DPI/Wayland guards, rendering
    lock.py         # single-instance advisory lock
  tests/
    test_api.py
    test_credentials.py
    test_lock.py
  docs/
    superpowers/{specs,plans}/...
  pyproject.toml    # console entry point, zero runtime deps
  README.md
  LICENSE           # MIT
  .gitignore
```

`pyproject.toml` gives `python -m tokitty` and a `pipx install` path, and doubles as the answer to "how do I run this?" in the README.

## UI

- Frameless (`overrideredirect(True)`), always-on-top (`-topmost`, with the macOS re-assertion guard above), drag anywhere (`<Button-1>` + `<B1-Motion>`), plain dark rounded-rectangle *look* on a solid card ~300×110 logical px. No `-transparentcolor` chroma-key in v1 (click-through + fringing rabbit hole — deferred).
- **Right-click menu:** `Refresh now` / `Always in front` (checkbutton toggle of `-topmost`) / `Exit`.
- **Left side:** pixel cat on a tkinter Canvas.
- **Right side:**
  - `SESSION` bar + percent + reset time in local time — parse `resets_at` with `datetime.fromisoformat()`, convert with `.astimezone()` **with no argument** (never a named `zoneinfo` zone; Windows has no IANA tz database by default).
  - `WEEK` bar + percent + reset day, same local-time handling.
  - **When a limit is capped (≥100%), that bar's reset text is replaced by a live ticking countdown** ("resets in 2h 14m 03s") — see Capped state / wake sequence. Computed locally from `resets_at - now()` on every UI tick (no extra network calls needed to tick the clock).
  - Credits line `$3.62 / $20.00` (from `spend.amount_minor / 10^exponent`) — shown only when `used_credits > 0`.
- Bar fill color by that bar's own percent: green (<50) → amber (50–80) → red (≥80).
- A tiny tag ("5h" / "7d") next to the cat showing **which** limit is driving its mood.
- Stale/error states: keep last-good numbers visible but dimmed, plus a one-line hint (see states below).

## Pixel cat

- Sprites authored as ASCII pixel grids in the source — each character is one pixel, mapped through a palette dict (`k`=outline, `o`=orange, `O`=dark orange, `p`=pink, `w`=white, `g`=green eyes, `.`=transparent). Rendered as filled rectangles at `SCALE`≈4 device px per sprite px on the Canvas. **On screen this is genuine pixel art** (orange tabby), not ASCII text — and drawing it as vector shapes rather than a bitmap is what keeps it crisp across every OS's DPI handling. Grid ≈ 20×16. Palette swap = trivial future re-skin.
- Two frames per steady-state mood, alternating at ~800ms (`root.after`). Redraw cost is negligible; don't optimize.
- **Mood ladder**, driven by `max(session %, weekly %)` while neither is capped:

| Range | Mood | Animation (frame A / frame B) |
|---|---|---|
| < 25% | **Sleeping** | curled up, eyes closed / "z" bobs |
| 25–50% | **Content** | sitting, slow blink / grooming a paw |
| 50–75% | **Interested** | head up, ears forward / head tilt toward the bars |
| 75–90% | **Alert** | ears up, eyes wide open / tail swish |
| 90–100% | **Panicked** | wide eyes, paw pointing at the usage bars / "!" flicker |

- If a `limits[]` entry that `is_active` carries a `severity` suggesting exceeded/blocked (non-`"normal"` values are unknown — compare case-insensitively against a small guess-list like `exceeded`, `blocked`, `rate_limited`), treat as capped regardless of percent.
- Plus one **Confused** state (head tilt / "?") for the unhappy states in Error handling below.
- Steady states total: 6 (5 mood + confused) × 2 frames = 12 sprites, **plus the capped/wake sequence below.**

### Capped state and wake sequence (≥100%, or forced-capped per above)

Instead of one static "flopped" pose, capped is a short sequence driven by time-to-reset of whichever limit is soonest to reset among the capped ones:

| Sub-state | When | Animation |
|---|---|---|
| **Flopped** (resting) | `time_to_reset > stir_threshold` | dramatically flat on its side / slow tail thump — the steady resting pose for most of the capped duration |
| **Stirring** | `wake_threshold < time_to_reset <= stir_threshold` | ear twitch / one eye cracks open |
| **Waking** | `0 < time_to_reset <= wake_threshold` | stretches, sits up / tail flicks eagerly |
| **Activate** (one-shot) | triggered when a poll observes the limit actually cleared (percent drops back under 100 / `is_active` flips false) — **not** purely wall-clock reaching zero, since server-side reset may lag the advertised `resets_at` slightly | brief pounce/hop, 2–3 frames over ~1.5s, then falls straight through to normal mood evaluation on the fresh data (which will read near-0% and land on **Sleeping** — "activates, then goes right back to sleep") |

Default thresholds (tunable constants, not load-bearing precision — this is flavor, not a promise):

```
CAPPED_WAKE_WINDOWS = {
    "session": {"stir": 15 * 60, "wake": 3 * 60},     # 15 min / 3 min before reset
    "weekly":  {"stir": 3 * 3600, "wake": 20 * 60},    # 3 h / 20 min before reset
}
```

**Poll interval tightens during Waking**: while `time_to_reset <= wake_threshold`, shorten the worker's poll interval (e.g. to 20s) so the Activate trigger fires close to the real reset moment rather than lagging by up to the normal `POLL_INTERVAL`. Reverts to normal cadence once Activate fires or the capped state clears.

If both session and weekly are capped simultaneously, the countdown/wake sequence tracks whichever resets sooner (the soonest actionable relief); the "5h"/"7d" tag next to the cat shows which.

Capped/wake sub-states add: Flopped (2 frames) + Stirring (2) + Waking (2) + Activate (2–3) ≈ 8–9 more sprites. **Total sprite count ≈ 20–21.**

## Error handling / states

| State | Trigger | Cat | UI hint |
|---|---|---|---|
| OK | fresh data | mood ladder / capped-wake sequence | — |
| Stale token | `expiresAt` past, or HTTP 401 | Confused | "token stale — open Claude Code" + "last good HH:MM" |
| Credentials unreachable | file missing/unreadable (WSL asleep, wrong path, permissions) | Confused | "can't find credentials" + currently-resolved path |
| Ambiguous credentials | more than one WSL distro has a credentials file, no override set | Confused | "multiple Claude Code installs found — set TOKITTY_CREDENTIALS" |
| API/schema error | non-401 HTTP error (incl. 429 — no retry-storm, just back off), timeout, JSON/parse failure | Confused | "API hiccup, retrying" |

All states keep showing last-good data, dimmed. The widget must never show a crash dialog and never exit on its own.

## Hardening (in scope for POC)

- Single-instance advisory lock (see Cross-platform architecture) — cross-platform, no PID-file bugs.
- Restore position clamped to current virtual-screen bounds (`winfo_vrootwidth/height`); off-screen → reset to bottom-right default. Skipped gracefully on native-Wayland Linux (see above).
- Worker thread `daemon=True`; Exit tears down cleanly.
- HTTP timeout 10s is the hang guard; credentials read happens on the worker thread so a hung mount/share can't freeze the UI.
- Backoff with jitter on any poll error (e.g. 30s → 60s → … cap 10 min), reset to normal interval on success; never applies during the Waking tightened-interval window (see above) once data is otherwise healthy.

## Public repo requirements

- **License:** MIT.
- **Security/privacy statement in README, stated plainly:** Tokitty only *reads* the local Claude Code credentials file and sends the access token only to `api.anthropic.com`; it never transmits, logs, or persists the token anywhere else. State/lock files (position only) live in the OS user-config dir, never the repo.
- **"Not affiliated with Anthropic"** disclaimer; "Claude" and "Claude Code" are Anthropic's marks, used only to describe compatibility.
- **Document only tested platforms.** Don't claim macOS/Linux support before actually running it there — update the README's platform table as each gets verified, not before.
- **Own art only** — all pixel-grid sprite data is original, no lifted assets.
- `.gitignore`: `__pycache__/`, build artifacts, any local `.env`. (State files can't leak into the repo by construction — see State/config location above — but keep the gitignore entry anyway as defense in depth.)

## Out of scope for POC (→ enhancement plans)

Stack-ranked for future Sonnet/Opus sessions; each gets its own plan doc in `tokitty/docs/superpowers/plans/` when picked up:

1. **CI matrix (GitHub Actions, ubuntu/macos/windows)** — import the package and run the non-GUI unit tests (`credentials.py` resolution logic, `api.py` parsing, `lock.py`) on all three; a headless Tk smoke test via `xvfb` on Linux. Cheap, and it's the credibility backbone of the cross-platform claim — sequence early, right after the POC ships.
2. **ntfy threshold notifications** — owner runs ntfy already. Edge-triggered with hysteresis: fire once on crossing a threshold upward, re-arm only after dropping below or after `resets_at`. Session and weekly independently. Native path only (see tray icon note).
3. **Autostart** — `shell:startup` shortcut (Windows) / login item (macOS) / autostart desktop entry (Linux) + boot-race tolerance (backoff + Confused cat, never a crash).
4. **Tray icon** — needs `pystray` (first pip dependency); runs its own event loop in a second thread alongside tkinter's. **Native path only — not reachable from a WSLg-hosted process.**
5. **Per-model weekly bars** — data already in `limits[]` `weekly_scoped` entries; main constraint is card space.
6. **Click-to-pet + purr** — pet animation frames + platform-appropriate sound: `winsound` (Windows stdlib), `afplay` via subprocess (macOS), `paplay`/`aplay` via subprocess (Linux). No single stdlib module covers all three — small OS-branch, not a trap if planned for upfront.
7. **Burn-rate projection** — "at this pace, capped by 11:40 PM." Needs rolling timestamped samples **within the current window only** (never project across a reset), divide-by-zero/flat-usage guards.
8. **PNG sprite upgrade** — swap ASCII-grid sprites for embedded base64 PNGs if fancier art is wanted; rendering becomes `PhotoImage`. Trades away the vector-scaling benefit noted above — evaluate against real DPI testing before committing.
9. **PySide6 free-floating transparent cat ("v2")** — full rewrite, heavy Qt pip install, real per-pixel transparency. Explicitly fenced: do not bolt onto the tkinter package; separate project decision.
10. **Cat roams the whole card** — instead of staying fixed in its left-side canvas, the cat wanders across the full 300x110 card (including over/past the usage bars), picking up pace or direction based on mood. Bigger than it sounds: needs the cat layer re-z-ordered above the bar/text widgets and a position/direction state machine, so it's scoped as its own follow-up rather than an extension of the POC's animation task. (A much smaller version — a subtle idle sway/bob within the existing canvas — was cheap enough to fold directly into the POC.)
11. **Cat/card color customization** — undecided between user-selectable palette (color pickers for coat/card/bar colors) vs. a "random color" button that re-rolls the palette. Palette is already a swappable dict (see Pixel cat), so either is a small addition on top of the existing structure. Decide which (or both) when this is picked up.

## Risks accepted

- **Undocumented endpoint.** Anthropic may rename fields, change the beta header, or gate the route at any time — more consequential now that this is public (many users could break at once). Mitigation: defensive parsing, Confused-cat degradation, last-good display, README disclaimer that this uses an unofficial endpoint that may break.
- **Polling cadence:** 120s (tightening to 20s only during the brief Waking window) ≈ 720 req/day baseline against a metadata endpoint — negligible; no rate-limit concern. 429s handled as a normal backoff case, not a retry-storm.
- **WSLg Windows launch path is unverified for quality** (topmost-over-native-apps behavior and DPI blur specifically) — documented as secondary/optional until eyeballed on a real monitor, not promoted to primary.

## Testing

- **Unit tests (`tests/`, pytest):** `credentials.py` resolution order and ambiguity handling (with mocked filesystem), `api.py` response parsing against a captured real fixture (percent extraction, ms-vs-s expiry logic, local-time formatting, defensive handling of missing fields), `lock.py` acquire/release semantics.
- **Manual:** launch via `pythonw.exe -m tokitty` (Windows) / `python3 -m tokitty` (Linux/macOS); verify numbers against Claude Code's own `/usage`.
- **Forced-state harness:** a debug flag (`TOKITTY_DEBUG_STATE=flopped|stirring|waking|activate|confused|...`) to render each mood/sub-state/error state without waiting for real usage — needed to eyeball all ~21 sprites and the 4 error states, and to verify the wake-sequence timing logic without waiting hours for a real cap to approach reset.
