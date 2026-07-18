# Phase 3 — Two Cats (Dual Account) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One tokitty window renders one pane per configured Claude account (personal + work), each with its own poller, activity watcher, and cat — falling back to exact v1 single-pane behavior when `accounts.json` is absent.

**Architecture:** A new `accounts.py` module parses `<state-dir>/accounts.json` (the same file the Phase 2 installer already consumes). `credentials.py` and `__main__.build_fetch_fn` gain an optional explicit `config_dir`. `ui.py` is pane-ized: a `Pane` class owns one cat+bars unit inside its own `tk.Frame`; `TokittyWindow` stacks N panes and keeps window-level chrome (drag, menu, position). `__main__.run_gui` builds one (pane, poller, watcher) unit per account. A "resting" display state handles the work account's permanently-stale-outside-work-hours steady state.

**Tech Stack:** Python stdlib only (tkinter, urllib, json, threading). pytest for tests, no GUI needed in tests.

## Global Constraints

- stdlib only — no third-party runtime dependencies
- TDD: failing test first, then minimal implementation, per task
- Conventional commits; **no AI attribution / Co-Authored-By lines ever**
- Tests must never touch the real `~/.claude/settings.json`, `~/.claude-work/`, or any real credentials — tmp_path fixtures only
- `tokitty/ui.py` remains the only module importing tkinter; `--debug-print` must keep working with no GUI toolkit
- Windows: never touch a `\\wsl.localhost` UNC path for a distro not confirmed running (activity watcher); poller credential reads go through `wsl.exe` as in v1
- Existing 223 tests must stay green after every task
- Verified 2026-07-18: the work (Team) account's `/api/oauth/usage` response has the same shape as personal — `five_hour.utilization: 0.0` + `resets_at: null` when idle, `spend.limit: null`. `parse_usage_response` already handles it; no parser changes needed.

---

### Task 1: `accounts.py` — accounts.json parsing + env-conflict warning

**Files:**
- Create: `tokitty/accounts.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Produces:
  - `Account` frozen dataclass: `name: str`, `config_dir: str`, `coat: Optional[str] = None`
  - `load_accounts(state_dir: Path) -> Optional[List[Account]]` — `None` when the file is absent, unparseable, or has no valid entries (⇒ caller uses v1 single-account behavior)
  - `env_conflict_warning(accounts: Optional[List[Account]]) -> Optional[str]` — warning string when both `accounts.json` accounts and `TOKITTY_CREDENTIALS` are set, else `None`
  - `parse_wsl_unc(config_dir: str) -> Optional[Tuple[str, str]]` — `(distro, posix_path)` for `\\wsl.localhost\<d>\...` / `\\wsl$\<d>\...` forms (either slash direction), `None` otherwise

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_accounts.py
import json
from pathlib import Path

import pytest

from tokitty.accounts import Account, env_conflict_warning, load_accounts, parse_wsl_unc


def write_accounts(tmp_path: Path, payload) -> Path:
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    return p


def test_absent_file_returns_none(tmp_path):
    assert load_accounts(tmp_path) is None


def test_two_accounts_parsed_in_order(tmp_path):
    write_accounts(tmp_path, {"accounts": [
        {"name": "personal", "config_dir": "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude", "coat": "orange_tabby"},
        {"name": "work", "config_dir": "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work"},
    ]})
    accounts = load_accounts(tmp_path)
    assert accounts == [
        Account(name="personal", config_dir="\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude", coat="orange_tabby"),
        Account(name="work", config_dir="\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work", coat=None),
    ]


def test_missing_name_defaults_to_account_n(tmp_path):
    write_accounts(tmp_path, {"accounts": [{"config_dir": "/home/u/.claude"}]})
    accounts = load_accounts(tmp_path)
    assert accounts[0].name == "account 1"


def test_invalid_json_returns_none(tmp_path):
    write_accounts(tmp_path, "{not json")
    assert load_accounts(tmp_path) is None


def test_entries_without_config_dir_are_skipped(tmp_path):
    write_accounts(tmp_path, {"accounts": [{"name": "broken"}, {"config_dir": "/home/u/.claude"}]})
    accounts = load_accounts(tmp_path)
    assert len(accounts) == 1


def test_empty_accounts_list_returns_none(tmp_path):
    write_accounts(tmp_path, {"accounts": []})
    assert load_accounts(tmp_path) is None


def test_env_conflict_warning_fires_only_when_both_present(monkeypatch):
    accounts = [Account(name="a", config_dir="/x")]
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    assert env_conflict_warning(accounts) is None
    monkeypatch.setenv("TOKITTY_CREDENTIALS", "/some/path")
    warning = env_conflict_warning(accounts)
    assert "TOKITTY_CREDENTIALS" in warning and "accounts.json" in warning
    assert env_conflict_warning(None) is None  # env var alone, v1 mode: no warning


@pytest.mark.parametrize("unc,expected", [
    ("\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude", ("Ubuntu", "/home/u/.claude")),
    ("\\\\wsl$\\Debian\\home\\u\\.claude-work", ("Debian", "/home/u/.claude-work")),
    ("//wsl.localhost/Ubuntu/home/u/.claude", ("Ubuntu", "/home/u/.claude")),
])
def test_parse_wsl_unc_matches(unc, expected):
    assert parse_wsl_unc(unc) == expected


@pytest.mark.parametrize("not_unc", ["/home/u/.claude", "C:\\Users\\u\\.claude", ""])
def test_parse_wsl_unc_passthrough(not_unc):
    assert parse_wsl_unc(not_unc) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_accounts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tokitty.accounts'`

- [ ] **Step 3: Implement `tokitty/accounts.py`**

```python
"""Parsing of the optional multi-account config file, accounts.json.

Lives in the same per-user state dir as position.json (see paths.py).
Absent, unparseable, or empty => None: callers must fall back to v1
single-account behavior. The Phase 2 installer (hooks_install.get_config_dirs)
already reads the same file; this module is the UI/poller-side consumer.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

ACCOUNTS_FILENAME = "accounts.json"


@dataclass(frozen=True)
class Account:
    name: str
    config_dir: str
    coat: Optional[str] = None  # parsed now, rendered in Phase 4


def load_accounts(state_dir: Path) -> Optional[List[Account]]:
    path = Path(state_dir) / ACCOUNTS_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None

    accounts: List[Account] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or not entry.get("config_dir"):
            continue
        accounts.append(
            Account(
                name=str(entry.get("name") or f"account {index}"),
                config_dir=str(entry["config_dir"]),
                coat=entry.get("coat"),
            )
        )
    return accounts or None


def env_conflict_warning(accounts: Optional[List[Account]]) -> Optional[str]:
    if accounts and os.environ.get("TOKITTY_CREDENTIALS"):
        return (
            "Both accounts.json and TOKITTY_CREDENTIALS are set; "
            "accounts.json wins and the env var is ignored."
        )
    return None


def parse_wsl_unc(config_dir: str) -> Optional[Tuple[str, str]]:
    """(distro, posix_path) for \\\\wsl.localhost\\<d>\\... and \\\\wsl$\\<d>\\...
    UNC forms (either slash direction); None for anything else."""
    normalized = config_dir.replace("/", "\\")
    for prefix in ("\\\\wsl.localhost\\", "\\\\wsl$\\"):
        if normalized.lower().startswith(prefix.lower()):
            parts = [p for p in normalized[len(prefix):].split("\\") if p]
            if len(parts) < 2:
                return None
            return parts[0], "/" + "/".join(parts[1:])
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_accounts.py -v`
Expected: all PASS

- [ ] **Step 5: Full suite + commit**

Run: `python3 -m pytest -q` — expected: 223 + new tests pass.

```bash
git add tokitty/accounts.py tests/test_accounts.py
git commit -m "feat(accounts): parse accounts.json with v1 fallback and env-conflict warning"
```

---

### Task 2: Per-account credential resolution

**Files:**
- Modify: `tokitty/credentials.py` (`resolve_credentials_source`)
- Test: `tests/test_credentials.py` (append)

**Interfaces:**
- Consumes: `accounts.parse_wsl_unc` (Task 1)
- Produces: `resolve_credentials_source(config_dir: Optional[str] = None) -> CredentialsSource`. With `config_dir`: a WSL UNC dir on win32 resolves to `WslDistroCredentialsSource(distro, posix_path + "/.credentials.json")` (read via `wsl.exe cat`, as v1's fallback already does); any other dir resolves to `LocalCredentialsSource(Path(config_dir) / ".credentials.json")`, raising `CredentialsError` if the file is missing. Explicit `config_dir` beats `TOKITTY_CREDENTIALS`. Without `config_dir`: v1 behavior unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_credentials.py`; follow that file's existing fixture style for any helpers it already has)

```python
def test_explicit_config_dir_posix(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKITTY_CREDENTIALS", "/should/be/ignored")
    creds = tmp_path / ".credentials.json"
    creds.write_text("{}", encoding="utf-8")
    source = resolve_credentials_source(config_dir=str(tmp_path))
    assert isinstance(source, LocalCredentialsSource)
    assert source.path == creds


def test_explicit_config_dir_missing_file_raises(tmp_path):
    with pytest.raises(CredentialsError):
        resolve_credentials_source(config_dir=str(tmp_path / "nope"))


def test_explicit_config_dir_wsl_unc(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    source = resolve_credentials_source(config_dir="\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work")
    assert isinstance(source, WslDistroCredentialsSource)
    assert source.distro == "Ubuntu"
    assert source.wsl_path == "/home/u/.claude-work/.credentials.json"


def test_no_config_dir_keeps_v1_override_behavior(tmp_path, monkeypatch):
    creds = tmp_path / "c.json"
    creds.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TOKITTY_CREDENTIALS", str(creds))
    source = resolve_credentials_source()
    assert isinstance(source, LocalCredentialsSource)
    assert source.path == creds
```

(Import `credentials`, `WslDistroCredentialsSource`, `pytest` at top of file if not already there.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_credentials.py -v`
Expected: new tests FAIL — `resolve_credentials_source() got an unexpected keyword argument 'config_dir'`

- [ ] **Step 3: Implement** — in `tokitty/credentials.py` change the signature and prepend the explicit branch:

```python
def resolve_credentials_source(config_dir: Optional[str] = None) -> CredentialsSource:
    """Return the credentials source to use.

    With an explicit config_dir (from accounts.json), that dir's
    .credentials.json is used directly -- a WSL UNC dir on Windows maps to
    a wsl.exe-read source so we never open the UNC path from Python.
    Without one: v1 resolution order (env override, home-relative, WSL probe).
    """
    if config_dir:
        from tokitty.accounts import parse_wsl_unc

        unc = parse_wsl_unc(config_dir)
        if unc is not None and sys.platform == "win32":
            distro, posix_dir = unc
            return WslDistroCredentialsSource(distro=distro, wsl_path=f"{posix_dir}/.credentials.json")
        candidate = Path(_posix_from_unc_or_same(config_dir)) / ".credentials.json"
        if not candidate.is_file():
            raise CredentialsError(f"No credentials file at {candidate} (from accounts.json)")
        return LocalCredentialsSource(path=candidate)
    ...  # existing v1 body unchanged
```

with the small helper (module level):

```python
def _posix_from_unc_or_same(config_dir: str) -> str:
    """On non-Windows, a UNC config_dir from accounts.json still refers to a
    local path once inside the distro -- translate it; otherwise pass through."""
    from tokitty.accounts import parse_wsl_unc

    unc = parse_wsl_unc(config_dir)
    return unc[1] if unc is not None else config_dir
```

(This makes one `accounts.json` — with UNC paths written from the Windows perspective — work from both the Windows GUI and WSL-side `--debug-print`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_credentials.py -v` — expected: all PASS.

- [ ] **Step 5: Full suite + commit**

```bash
python3 -m pytest -q
git add tokitty/credentials.py tests/test_credentials.py
git commit -m "feat(credentials): resolve per-account config_dir from accounts.json"
```

---

### Task 3: Resting display state (steady-stale work pane)

**Files:**
- Modify: `tokitty/__main__.py` (`_display_state_for`)
- Test: `tests/test_main.py` (append)

**Interfaces:**
- Consumes: existing `PollResult`, `_display_from_snapshot`
- Produces: for `status == "stale_token"` **with** a cached last-good snapshot and no overdue capped countdown, `_display_state_for` returns the resting look: `state="sleeping"`, `dimmed=True`, `hint_text="last seen HH:MM"` (local time of `previous.fetched_at`), no accent. All other statuses keep current behavior; overdue-capped keeps its existing warning path.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_main.py`, reusing its existing snapshot/PollResult builders)

```python
def test_stale_token_with_cache_shows_resting_look():
    good = _ok_result(session_pct=42.0)  # use the file's existing helper for an ok PollResult
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime(2026, 7, 18, 20, 0, tzinfo=timezone.utc))
    display = _display_state_for(stale, previous=good)
    assert display["state"] == "sleeping"
    assert display["dimmed"] is True
    assert display["hint_text"].startswith("last seen ")
    assert display["session_pct"] == 42.0  # last-good numbers still shown


def test_stale_token_resting_uses_last_good_fetch_time():
    good = _ok_result()
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime.now(timezone.utc))
    display = _display_state_for(stale, previous=good)
    expected = good.fetched_at.astimezone().strftime("%H:%M")
    assert display["hint_text"] == f"last seen {expected}"


def test_stale_token_without_cache_keeps_v1_hint():
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime.now(timezone.utc))
    display = _display_state_for(stale, previous=None)
    assert display["state"] == "confused"
    assert display["hint_text"] == "token stale, open Claude Code"


def test_overdue_capped_beats_resting():
    # last-good has an active capped limit whose resets_at is already past:
    # the "can't confirm" warning must win over the resting look.
    good = _ok_result_capped(resets_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime.now(timezone.utc))
    display = _display_state_for(stale, previous=good)
    assert display["hint_text"] == "token expired, reopen Claude Code"
    assert display["dimmed"] is True
```

(If `test_main.py` lacks a capped-ok helper, build the snapshot inline with a `LimitInfo(kind="session", percent=100.0, severity="capped", resets_at=..., is_active=True)`.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_main.py -v` — expected: the new tests FAIL (current code returns the countdown display with `hint_text=None`/`dimmed=False` for non-overdue stale).

- [ ] **Step 3: Implement** — in `_display_state_for`, inside the `last_good is not None` branch, after computing `overdue`:

```python
        if overdue:
            display["hint_text"] = _STALE_HINTS.get(result.status, "can't confirm, reconnect")
            display["dimmed"] = True
        elif result.status == "stale_token":
            # Resting look: a work account's token expires ~1h after that
            # account's Claude Code last ran, so outside work hours this is
            # the pane's normal steady state -- not an error. Dim the
            # last-good numbers, sleep the cat, timestamp it quietly.
            last_seen = previous.fetched_at.astimezone().strftime("%H:%M")
            display["state"] = "sleeping"
            display["hint_text"] = f"last seen {last_seen}"
            display["dimmed"] = True
        else:
            display["hint_text"] = None
            display["dimmed"] = False
        return display
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_main.py -v` — expected: all PASS.

- [ ] **Step 5: Full suite + commit**

```bash
python3 -m pytest -q
git add tokitty/__main__.py tests/test_main.py
git commit -m "feat(display): resting look for steady-stale accounts (sleeping cat, last-seen)"
```

---

### Task 4: Pane-ize `ui.py`

**Files:**
- Modify: `tokitty/ui.py` (biggest diff of the phase)
- Test: `tests/test_geometry.py` (append clamp cases), plus a non-GUI import guard test in `tests/test_ui_layout.py` (new)

**Interfaces:**
- Consumes: nothing new
- Produces:
  - `PANE_HEIGHT = 128` (module constant; replaces `CARD_HEIGHT` as the per-unit height — `CARD_HEIGHT` becomes a function of pane count)
  - `class Pane` — constructor `Pane(parent: "tk.Frame")`; method `render(...)` with the **exact same signature** as today's `TokittyWindow.render`; attributes `_current_state`, `_frame_index` (read by the window's shared animate loop)
  - `TokittyWindow(root, state_dir, pane_count: int = 1)` — exposes `self.panes: List[Pane]`; keeps `render(...)` delegating to `panes[0]` so v1 callers/debug path are untouched; window height = `pane_count * PANE_HEIGHT`
- Constraint: `ui.py` stays the only tkinter importer; no logic change to drag/menu/position code beyond height parameterization.

- [ ] **Step 1: Write the failing geometry tests** (append to `tests/test_geometry.py`)

```python
def test_saved_bottom_edge_position_clamped_for_taller_card():
    # A position saved by the 128px single-pane card, restored by the
    # 256px dual-pane card, must be pulled up so the card stays on screen.
    screen_w, screen_h = 1920, 1080
    saved_x, saved_y = 1596, 1080 - 128 - 24  # v1 default bottom-right
    x, y = clamp_position(saved_x, saved_y, 300, 256, screen_w, screen_h)
    assert y + 256 <= screen_h
    assert x == saved_x
```

Run: `python3 -m pytest tests/test_geometry.py -v` — if `clamp_position` already passes this (it should — that's the spec's "the existing clamp should catch it; test it does"), the test documents it; if it fails, fix `clamp_position` minimally.

- [ ] **Step 2: Write the non-GUI structure test** (`tests/test_ui_layout.py`)

```python
"""Layout-constant tests that must not require a display. ui.py imports
tkinter at module level, so only run what's importable headlessly."""
import pytest

tk = pytest.importorskip("tkinter")


def test_pane_height_and_card_width_constants():
    from tokitty import ui
    assert ui.PANE_HEIGHT == 128
    assert ui.CARD_WIDTH == 300


def test_window_height_scales_with_pane_count():
    from tokitty import ui
    assert ui.card_height(1) == 128
    assert ui.card_height(2) == 256
```

Run: `python3 -m pytest tests/test_ui_layout.py -v` — expected FAIL (`PANE_HEIGHT`/`card_height` don't exist). Note: if `tkinter` itself is missing in the dev env, the file skips — that's fine; the constants are still exercised on Windows/CI later.

- [ ] **Step 3: Restructure `ui.py`**

Mechanical transformation, keeping every widget, coordinate, color, and comment:

```python
PANE_HEIGHT = 128  # was CARD_HEIGHT; one cat+bars unit


def card_height(pane_count: int) -> int:
    return PANE_HEIGHT * pane_count


class Pane:
    """One cat + bars unit. Owns its widgets inside a parent Frame; knows
    nothing about window chrome, drag, or position."""

    def __init__(self, parent):
        self.parent = parent
        self._current_state = "sleeping"
        self._frame_index = 0
        self._driving_tag = ""
        self._tool_label = ""
        self._accent = False
        self._build_widgets()

    def _build_widgets(self) -> None:
        # identical to today's TokittyWindow._build_widgets, but every
        # widget's master is self.parent (a Frame) instead of self.root,
        # and wraplength uses CARD_WIDTH - STATS_X - 8 as before
        ...

    def render(self, state, session_pct, weekly_pct, session_reset_text,
               weekly_reset_text, driving_tag, credits_text, hint_text,
               dimmed, tool_label="", accent=False) -> None:
        # identical body to today's TokittyWindow.render, except the
        # background target is self.parent.configure(bg=bg) (the Frame),
        # not self.root -- accent is now per-pane
        ...

    def draw_next_frame(self) -> None:
        frames = get_frames(self._current_state)
        frame = frames[self._frame_index % len(frames)]
        self._draw_frame(frame)   # today's _draw_frame body, unchanged
        self._frame_index += 1


class TokittyWindow:
    def __init__(self, root, state_dir, pane_count: int = 1):
        self.root = root
        self.state_dir = state_dir
        self._pane_count = pane_count
        self._height = card_height(pane_count)
        # ... existing init fields (drag offset, topmost var, on_refresh_requested)
        self._configure_window()          # uses self._height in geometry()
        self.panes = []
        for i in range(pane_count):
            frame = tk.Frame(root, width=CARD_WIDTH, height=PANE_HEIGHT,
                             bg=BG_COLOR)
            frame.place(x=0, y=i * PANE_HEIGHT)
            self.panes.append(Pane(frame))
        self._restore_position()          # uses self._height for clamp
        self._bind_drag()
        self._build_context_menu()
        self._animate()

    def render(self, **kwargs) -> None:
        # v1 compatibility: single-pane callers (debug-state path) untouched
        self.panes[0].render(**kwargs)

    def _animate(self) -> None:
        for pane in self.panes:
            pane.draw_next_frame()
        self.root.after(FRAME_INTERVAL_MS, self._animate)
```

Details the implementer must carry over exactly:
- `_configure_window`: `self.root.geometry(f"{CARD_WIDTH}x{self._height}")`
- `_restore_position` / default position: replace every `CARD_HEIGHT` with `self._height`
- Drag bindings stay on `self.root`; **also bind `<Button-3>` on each pane's Frame and child widgets' events propagate** — verify right-click still opens the menu when clicking a pane (Frames swallow events bound only on root; bind the context menu with `bind_all` if needed, but then unbind on destroy is unnecessary since the app dies with the window)
- Delete the old `CARD_HEIGHT` constant; grep the repo for other users first (`grep -rn CARD_HEIGHT tokitty/ tests/`) and update them
- Keep the status/credits single-label comment block — it documents a real screenshot-verified bug

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q` — expected: all pass (ui.py has no direct unit tests today; test_ui_layout.py + geometry cover the new surface).

- [ ] **Step 5: Visual check via debug harness**

Run (Windows or any GUI env): `TOKITTY_DEBUG_STATE=content python -m tokitty` — single pane must look pixel-identical to v1. This is a checkpoint for the human reviewer if a GUI env is available; otherwise defer to the end-of-phase manual test.

- [ ] **Step 6: Commit**

```bash
git add tokitty/ui.py tests/test_ui_layout.py tests/test_geometry.py
git commit -m "refactor(ui): extract Pane component; window height scales with pane count"
```

---

### Task 5: Dual-account wiring in `__main__.py`

**Files:**
- Modify: `tokitty/__main__.py` (`build_fetch_fn`, `resolve_activity_sessions`, `run_gui`, `debug_print`)
- Test: `tests/test_main.py` (append)

**Interfaces:**
- Consumes: `load_accounts`, `env_conflict_warning`, `parse_wsl_unc` (Task 1); `resolve_credentials_source(config_dir=...)` (Task 2); `TokittyWindow(root, state_dir, pane_count)` + `window.panes` (Task 4)
- Produces:
  - `build_fetch_fn(config_dir: Optional[str] = None)` — threads `config_dir` into `resolve_credentials_source`
  - `resolve_activity_sessions(config_dir: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]` — with `config_dir`: sessions dir is `<config_dir>/tokitty/sessions` (UNC kept UNC on win32 with distro name parsed from it for the running-distro check; posix on Linux via `_posix_from_unc_or_same`-equivalent translation); without: v1 behavior
  - `TOKITTY_DEBUG_ACCOUNTS=2` env var renders a two-pane window with fake data and no worker threads

- [ ] **Step 1: Write the failing tests** (append to `tests/test_main.py`)

```python
def test_resolve_activity_sessions_explicit_posix_dir(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sessions_dir, distro = resolve_activity_sessions("/home/u/.claude-work")
    assert sessions_dir == "/home/u/.claude-work/tokitty/sessions"
    assert distro is None


def test_resolve_activity_sessions_explicit_unc_dir(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    sessions_dir, distro = resolve_activity_sessions(
        "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work")
    assert sessions_dir == "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work\\tokitty\\sessions"
    assert distro == "Ubuntu"


def test_resolve_activity_sessions_unc_dir_on_linux_translates(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sessions_dir, distro = resolve_activity_sessions(
        "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work")
    assert sessions_dir == "/home/u/.claude-work/tokitty/sessions"
    assert distro is None


def test_build_fetch_fn_passes_config_dir(monkeypatch, tmp_path):
    seen = {}
    def fake_resolve(config_dir=None):
        seen["config_dir"] = config_dir
        raise CredentialsError("stop here")
    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", fake_resolve)
    result = build_fetch_fn(config_dir="/home/u/.claude-work")()
    assert seen["config_dir"] == "/home/u/.claude-work"
    assert result.status == "credentials_unreachable"
```

(`sys` here is the `sys` imported inside `tokitty.__main__` — monkeypatch `tokitty.__main__.sys.platform` if the file imports it as a module, which it does.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_main.py -v` — expected: FAIL on unexpected keyword args.

- [ ] **Step 3: Implement**

`build_fetch_fn`:

```python
def build_fetch_fn(config_dir: Optional[str] = None):
    def fetch() -> PollResult:
        now = datetime.now(timezone.utc)
        try:
            source = resolve_credentials_source(config_dir=config_dir)
        ...  # rest of the body unchanged
```

`resolve_activity_sessions` — prepend the explicit branch before the v1 body:

```python
def resolve_activity_sessions(config_dir: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    if config_dir:
        from tokitty.accounts import parse_wsl_unc

        unc = parse_wsl_unc(config_dir)
        if sys.platform == "win32":
            if unc is not None:
                distro = unc[0]
                sessions = config_dir.rstrip("\\/") + "\\tokitty\\sessions"
                return sessions, distro
            return str(Path(config_dir) / "tokitty" / "sessions"), None
        base = unc[1] if unc is not None else config_dir
        return str(Path(base) / "tokitty" / "sessions"), None
    ...  # existing v1 body unchanged
```

`run_gui` — replace the single poller/watcher block:

```python
    from tokitty.accounts import env_conflict_warning, load_accounts

    accounts = load_accounts(state_dir)
    warning = env_conflict_warning(accounts)
    if warning:
        print(f"tokitty: {warning}", file=sys.stderr)

    debug_accounts = os.environ.get("TOKITTY_DEBUG_ACCOUNTS")
    pane_count = 2 if debug_accounts == "2" else (len(accounts) if accounts else 1)

    root = tk.Tk()
    window = TokittyWindow(root, state_dir, pane_count=pane_count)

    debug_state = os.environ.get(DEBUG_STATE_ENV)
    if debug_state or debug_accounts == "2":
        fake = dict(
            state=debug_state or "content", session_pct=37.0, weekly_pct=62.0,
            session_reset_text="resets 9pm", weekly_reset_text="resets Fri",
            driving_tag="debug", credits_text=None, hint_text=None, dimmed=False,
        )
        for index, pane in enumerate(window.panes):
            resting = dict(fake, state="sleeping", dimmed=True,
                           hint_text="last seen 17:40")
            pane.render(**(fake if index == 0 else resting))
        root.mainloop()
        lock.release()
        return 0

    units = []
    for index, account in enumerate(accounts or [None]):
        config_dir = account.config_dir if account else None
        poller = Poller(fetch_fn=build_fetch_fn(config_dir))
        sessions_dir, distro_name = resolve_activity_sessions(config_dir)
        watcher = ActivityWatcher(sessions_dir, ActivityTracker(), distro_name=distro_name)
        units.append({"pane": window.panes[index], "poller": poller,
                      "watcher": watcher, "last_good": None})

    def refresh_all():
        for unit in units:
            unit["poller"].request_refresh()

    window.on_refresh_requested = refresh_all
    if warning:
        window.panes[0].render(state="confused", session_pct=0.0, weekly_pct=0.0,
                               session_reset_text="—", weekly_reset_text="—", driving_tag="",
                               credits_text=None, hint_text=warning, dimmed=True)

    def tick():
        for unit in units:
            latest = unit["poller"].get_latest()
            if latest is None:
                continue
            display = _display_state_for(latest, unit["last_good"])
            activity = unit["watcher"].get_latest()
            pose = resolve_pose(display["state"], activity)
            display["state"] = pose["sprite_state"]
            display["tool_label"] = pose["tool_label"]
            display["accent"] = pose["accent"]
            unit["pane"].render(**display)
            unit["last_good"] = _next_last_good(latest, unit["last_good"])
        root.after(UI_REFRESH_MS, tick)

    for unit in units:
        unit["poller"].start()
        unit["watcher"].start()
    root.after(UI_REFRESH_MS, tick)

    try:
        root.mainloop()
    finally:
        for unit in units:
            unit["poller"].stop()
            unit["watcher"].stop()
        lock.release()

    return 0
```

`debug_print` — loop over accounts when present:

```python
def debug_print() -> int:
    from tokitty.accounts import load_accounts

    accounts = load_accounts(get_state_dir())
    for account in accounts or [None]:
        if account is not None:
            print(f"— {account.name} ({account.config_dir})")
        config_dir = account.config_dir if account else None
        result = build_fetch_fn(config_dir)()
        ...  # existing printing body, plus per-account resolve_activity_sessions(config_dir)
    return 0
```

- [ ] **Step 4: Run tests, then full suite**

Run: `python3 -m pytest tests/test_main.py -v && python3 -m pytest -q` — expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tokitty/__main__.py tests/test_main.py
git commit -m "feat(dual): per-account pollers, watchers, and panes wired from accounts.json"
```

---

### Task 6: Docs + README + manual verification

**Files:**
- Modify: `README.md` (dual-account section), `docs/superpowers/specs/2026-07-13-tokitty-v2-design.md` is NOT edited (spec is a record)

**Interfaces:** none — documentation and the phase's human gate.

- [ ] **Step 1: README** — add a "Two accounts" section after the install/hooks section:
  - `accounts.json` location per OS (same dir as `position.json`), the exact JSON example from the spec (Phase 3 section, lines 106–113), and: absent file ⇒ identical v1 single-account behavior
  - `TOKITTY_CREDENTIALS` + `accounts.json` conflict rule (accounts.json wins; startup warning)
  - The resting look: a work account's pane sleeping + dimmed + "last seen HH:MM" outside work hours **is normal**, not an error
  - Re-run `--install-hooks` after creating/changing `accounts.json` (the installer reads it too), and restart open Claude Code sessions (hooks aren't hot-reloaded)
  - Security section: dual mode reads a second account's credentials/settings — extend the existing wording honestly, keep every existing claim true
- [ ] **Step 2: Full suite one more time**

Run: `python3 -m pytest -q` — expected: all green.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: dual-account setup, conflict rule, and resting-state explanation"
```

- [ ] **Step 4: Manual verification (human gate — spec requires it before merge)**
  - On Windows: create `accounts.json` with Nick's real personal (`\\wsl.localhost\Ubuntu\home\cptsmidge\.claude`) + work (`...\.claude-work`) dirs; run `python -m tokitty`; confirm two panes, correct per-account numbers, drag/menu/position persistence, and (outside work hours) the work pane resting look
  - `TOKITTY_DEBUG_ACCOUNTS=2 python -m tokitty` renders the fake dual card
  - Re-run `--install-hooks`; confirm both config dirs get hooks; trigger activity in a personal session and confirm only the personal cat reacts

---

## Not in this phase (explicitly)

- Coat rendering (accounts.json `coat` is parsed and carried, rendered in Phase 4)
- Named-cat labels on panes (Phase 4)
- Pane-count changes at runtime (edit accounts.json ⇒ restart tokitty)
- No new sprite art: the resting look reuses the existing `sleeping` frames — **no art subagent needed this phase**

## Self-review notes

- Spec coverage: single window/panes (T4/T5), accounts.json + fallback + conflict warning (T1/T5), per-pane pollers/watchers/credentials (T2/T5), work-account shape check (done pre-plan, recorded in Global Constraints), resting look (T3), clamp-taller-card test (T4), `TOKITTY_DEBUG_ACCOUNTS=2` (T5), `test_accounts.py` from spec's Testing section (T1), manual dual-account gate (T6)
- Type consistency: `resolve_credentials_source(config_dir: Optional[str])` used identically in T2 and T5; `parse_wsl_unc -> Optional[Tuple[str,str]]` consumed in T2/T5; `Pane.render` keeps the exact v1 `render` signature consumed by T5's `tick`
