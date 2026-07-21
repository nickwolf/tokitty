# CI matrix (ubuntu/macos/windows) — design

**Issue:** #18 — "Backlog: CI matrix (ubuntu/macos/windows)". Self-labeled the #1
backlog item and "credibility backbone of the cross-platform claim."

**Date:** 2026-07-21
**Branch:** `ci-matrix`

## Goal

Stand up GitHub Actions CI that proves Tokitty's cross-platform claim: the unit
suite runs green on Linux, macOS, and Windows, and the real Tk GUI actually
constructs on Linux under a headless display. Add a lint gate. Surface a status
badge in the README as the visible payoff.

## Context / grounding (verified 2026-07-21)

- **No CI exists** — there is no `.github/` directory.
- **Zero runtime dependencies.** Pure stdlib + Tkinter. `requires-python
  >=3.10`. `dev` extra is just `pytest>=7`.
- **The 288-test suite is already headless and cross-platform-clean.** It runs
  green in ~3.4s with no display. Only one platform guard exists: a POSIX-`fcntl`
  skip on Windows in `test_lock.py`.
- **tkinter import surface is tightly contained:**
  - `tokitty/ui.py` is the *only* module that imports `tkinter` at module scope.
  - `tokitty/display.py` is deliberately kept tkinter-free ("so it can be
    unit-tested without a GUI").
  - `tokitty/__main__.py` imports `ui`/`tkinter` lazily inside `run_gui()`, not
    at module top.
  - The only test that pulls in `tokitty.ui` is `test_ui_layout.py`, and it
    already guards with `pytest.importorskip("tkinter")` plus function-level
    imports.
  - **Consequence:** on a runner without a working `_tkinter`, `test_ui_layout`
    *skips cleanly* — there is no red collection error on any OS. The suite
    degrades to visible SKIPs, not failures.
- **The GUI is trivial to smoke-test.** `TokittyWindow(root, state_dir,
  pane_count=1)` builds the entire UI from just a Tk root and a state dir — no
  network, no polling. A fake-data render path already exists (the
  `TOKITTY_DEBUG_STATE` / `TOKITTY_DEBUG_ACCOUNTS` debug branch in `run_gui`).
- **Ruff cost measured** (ruff 0.15.22):
  - `ruff check`: **16 errors, 8 auto-fixable.** Breakdown: 8×F401 (unused
    imports, all auto-fix), 4×E402 (module import not at top — in `test_mood.py`
    ×3, `test_sprites.py` ×1), 3×E741 (ambiguous `l` — `test_api.py`,
    `mood.py` ×2), 1×E731 (lambda assignment — `scripts/render_media.py`).
    E501 line-length is *not* in ruff's default select, so no line-length churn.
  - `ruff format`: would reformat **36 of 42 files** — the formatter has never
    been run. **Deferred** (see Out of Scope).

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Full #18: matrix **+** xvfb Tk smoke test | Without the smoke test, CI proves logic runs on 3 OSes but not that the GUI boots — the smoke test is the actual credibility claim. |
| Python versions | `3.10` and `3.14` | Pins both ends of the supported range (floor + newest stable; 3.15 isn't out). Keeps the matrix lean on a pure-stdlib project. |
| Lint | `ruff check` only, **pinned** `ruff==0.15.22` | Unpinned ruff can add checks under existing categories and turn CI red with no code change. |
| Formatter | Deferred | Enforcing `ruff format` means a 36-file reformat that buries the CI change and would collide with the incoming tray-icon branch. |
| Caching | None | Deps are tiny (pytest + ruff). YAGNI. |

## Architecture

One workflow file: `.github/workflows/ci.yml`. Triggers: `push` to `main` and
`pull_request`. Three jobs.

### Job 1 — `test` (the matrix)

- Strategy matrix: `os: [ubuntu-latest, macos-latest, windows-latest]` ×
  `python-version: ['3.10', '3.14']` → 6 jobs. `fail-fast: false` so one OS
  failing doesn't cancel the others.
- Steps: checkout → `actions/setup-python` → `pip install -e ".[dev]"` →
  `pytest`. Editable install also validates `pyproject` packaging.
- `pytest` runs the 288 existing tests. The `gui`-marked smoke test is excluded
  here via `addopts = -m "not gui"` in `pyproject.toml`, so this job stays
  display-free on every OS.

### Job 2 — `smoke` (Linux xvfb GUI boot)

- `ubuntu-latest`, Python 3.14. Install `xvfb` (apt). Run
  `xvfb-run -a pytest -m gui`.
- New test `tests/test_smoke_gui.py`:
  1. `tk.Tk()` root.
  2. `TokittyWindow(root, tmp_path, pane_count=1)`.
  3. Render one fake frame using the existing debug-data shape (`state`,
     `session_pct`, `weekly_pct`, `session_reset_text`, `weekly_reset_text`,
     `driving_tag`, `credits_text`, `hint_text`, `dimmed`).
  4. `root.update()` once — **no `mainloop`**.
  5. `root.destroy()` in a `finally`.
  - Asserts no exception. A genuine "does the real Tk GUI construct on this
    platform" check with zero network/polling.
  - Marked `@pytest.mark.gui` so it's excluded from the default headless run and
    only invoked explicitly under xvfb.

### Job 3 — `lint`

- `ubuntu-latest`, single Python. `pip install ruff==0.15.22`. Run
  `ruff check .`. No `ruff format`.

## Code changes bundled in this PR

1. **Fix the 16 `ruff check` errors.**
   - 8×F401 → `ruff check --fix` (safe auto-fix).
   - 3×E741 → rename `l` to a descriptive name.
   - 1×E731 → convert the lambda to a `def`.
   - 4×E402 → **investigate why each import is late before reordering.** If the
     placement is deliberate (import after a module-level setup), a
     `per-file-ignore` is the correct fix, not a move.
2. **`pyproject.toml`:**
   - `[tool.ruff]` — pin `target-version`, keep the default rule set.
   - `[tool.pytest.ini_options]` — register the `gui` marker and set
     `addopts = -m "not gui"`.
   - Add `ruff` to the `dev` extra.
3. **New `tests/test_smoke_gui.py`** (per Job 2).
4. **README** — add the CI status badge.

## Out of scope (own follow-ups)

- `ruff format` adoption + `.git-blame-ignore-revs` (single isolated reformat PR).
- pip caching.
- Tray icon (#21), autostart (#20), and other backlog items.

## Verification gates (before claiming done)

- Run `xvfb-run -a pytest -m gui` locally once before trusting the smoke test —
  `TokittyWindow` does `overrideredirect(True)` + `-topmost` with no window
  manager under xvfb; construct → `update()` → destroy *should* be fine, but
  confirm rather than assume.
- Confirm the full suite still passes headless after the lint fixes:
  `pytest` (should stay 288 passed, gui deselected).
- On the first CI run, confirm the layout tests **run** (not skip) on macOS and
  Windows — i.e., `setup-python` ships a working `_tkinter` there. If they skip,
  the cross-platform GUI claim leans entirely on the Linux xvfb smoke job, which
  is a weaker claim worth noting.
