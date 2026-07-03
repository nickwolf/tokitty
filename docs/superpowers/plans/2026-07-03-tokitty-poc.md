# Tokitty POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working, tested, cross-platform Tokitty POC — a cat-themed desktop widget showing live Claude Code usage — as a public-repo-ready Python package with no runtime dependencies.

**Architecture:** A small stdlib-only Python package (`tokitty/`) split into pure, unit-testable logic modules (credential resolution, API parsing, mood/wake-sequence state machine, sprite data, display formatting, window-geometry math, single-instance locking, background polling) and one tkinter-dependent rendering module (`ui.py`) plus an orchestration entry point (`__main__.py`). Pure modules never import `tkinter`, so the full automated test suite runs without a GUI toolkit installed.

**Tech Stack:** Python 3.10+, stdlib only at runtime (`tkinter`, `urllib.request`, `json`, `threading`, `dataclasses`, `pathlib`, `datetime`), `pytest` as a dev-only test dependency.

## Global Constraints

- Python 3.10+ (verified against WSL Python 3.12.3 and native Windows Python 3.13.2).
- Zero runtime pip dependencies — stdlib only. `pytest` is dev-only (`[project.optional-dependencies].dev`).
- MIT License.
- Never write, log, or transmit the OAuth token anywhere except the single request to `api.anthropic.com`. Never touch `refreshToken`. Never write the credentials file.
- State/lock/position files live in the OS user-config directory (`tokitty/paths.py`), never inside the repo/install directory.
- `expiresAt` in the credentials file is epoch **milliseconds** — do not compare against `time.time()` (seconds) directly.
- Every usage-endpoint response field is read defensively (`.get()` with a default) — this is an undocumented endpoint.
- Local times are formatted with `datetime.astimezone()` (no argument) — never a named `zoneinfo` zone, and never platform-specific `strftime` flags like `%-I`/`%-d` (unsupported on Windows).
- Each task below lands as its own git commit. No `Co-Authored-By`/AI-attribution lines in commit messages.
- Spec: `/mnt/c/Tools/tokitty/docs/superpowers/specs/2026-07-02-tokitty-design.md` — consult it for anything this plan doesn't spell out.

## Model Guidance (Sonnet vs Opus)

**Tasks 1-14** (scaffold, `paths.py`, `credentials.py`, `wsl_probe.py`, `api.py`, `lock.py`, `mood.py`, `sprites.py`, `display.py`, `geometry.py`, `poller.py`, and the data-pipeline half of `__main__.py`) are fully mechanical: every step already has complete code, an exact command, and a deterministic pass/fail expected output. **Sonnet should handle these without issue** — all the design judgment was made while writing this plan, not left for the implementer.

**Tasks 15-17** (`ui.py` window shell + rendering, wiring the poller into the GUI, README finalization/WSLg verification) are better suited to **Opus**, for two reasons:
1. Real tkinter subtleties — drag-offset math, `root.after()` as the only safe cross-thread handoff from the poller, the win32-only DPI/topmost guards — have more room for a bug that "runs without crashing" but is visibly wrong on screen. That class of bug is easy to miss without careful reasoning about what each line actually does geometrically.
2. These tasks explicitly require *not* overclaiming: the plan repeatedly says a passing manual step only proves "didn't crash," and the executor must show results to the user and wait for real visual confirmation rather than assuming the pixel art or drag/topmost behavior is correct. That discipline under ambiguity is exactly where a stronger model is more reliable.

If Tasks 15-17 do end up run on Sonnet, flag them for an Opus review pass (or extra owner scrutiny) before treating the POC as done — don't let a "tests pass" report from those tasks stand in for an actual look at the running app.

## File Structure

This plan elaborates the spec's five-module sketch into more, smaller single-responsibility files — each addition is justified by testability (a pure module with no `tkinter` import can be fully unit-tested from any OS, including this Linux dev environment, without installing a GUI toolkit):

```
tokitty/
  tokitty/
    __init__.py       # empty, marks the package
    __main__.py       # entry point: python -m tokitty; orchestrates everything below
    paths.py          # PURE: per-OS state directory resolution
    credentials.py    # PURE: credential source resolution (override/home/WSL-fallback), loading, expiry
    wsl_probe.py       # PURE: Windows-only WSL distro probing (subprocess-injectable, no filesystem/Path use)
    api.py             # PURE: usage-endpoint HTTP client + defensive response parsing
    lock.py            # PURE (uses real OS primitives, no tkinter): single-instance advisory lock
    mood.py            # PURE: steady mood ladder + capped/wake-sequence state machine
    sprites.py          # PURE: pixel-grid data, palette, frame composition
    display.py          # PURE: countdown/time/bar-color formatting (kept separate from ui.py so it's tkinter-free)
    geometry.py          # PURE: window-position clamping math
    poller.py             # PURE: background polling thread (dependency-injected fetch function and clock)
    ui.py                  # TKINTER: window chrome, drag, rendering — the only GUI-dependent module
  tests/
    test_paths.py
    test_credentials.py
    test_wsl_probe.py
    test_api.py
    test_lock.py
    test_mood.py
    test_sprites.py
    test_display.py
    test_geometry.py
    test_poller.py
  docs/
    superpowers/{specs,plans}/...   # already exists
  pyproject.toml
  README.md
  LICENSE
  .gitignore
```

`ui.py` and the GUI-integration parts of `__main__.py` are verified manually (launch + visual check) rather than by automated test, per the spec's Testing section — there is no headless display in this dev environment to drive tkinter automatically, and pixel-art rendering quality needs a human's eyes regardless.

---

### Task 1: Repo scaffold, license, packaging, gitignore

**Files:**
- Create: `tokitty/pyproject.toml`
- Create: `tokitty/LICENSE`
- Create: `tokitty/.gitignore`
- Create: `tokitty/README.md`
- Create: `tokitty/tokitty/__init__.py`

**Interfaces:**
- Produces: an installable package skeleton (`python -m tokitty` will resolve once `__main__.py` exists in Task 11); `pytest` available as a dev tool.

- [ ] **Step 1: Initialize the git repo**

Run:
```bash
cd /mnt/c/Tools/tokitty && git init
```
Expected: `Initialized empty Git repository in /mnt/c/Tools/tokitty/.git/`

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "tokitty"
version = "0.1.0"
description = "A cat-themed desktop widget for tracking Claude Code usage limits"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}

[project.optional-dependencies]
dev = ["pytest>=7"]

[project.scripts]
tokitty = "tokitty.__main__:main"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["tokitty*"]
```

- [ ] **Step 3: Create `LICENSE` (MIT)**

```
MIT License

Copyright (c) 2026 Nick Wolf

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
build/
dist/
.venv/
venv/
.env
```

- [ ] **Step 5: Create a README skeleton (finalized fully in Task 17)**

```markdown
# Tokitty

A cat-themed desktop widget that shows your live Claude Code usage — session %, weekly %, reset countdowns — with a pixel cat whose mood reflects how close you are to the limit.

**Status:** under active development — this README is a placeholder until the POC is complete.

## License

MIT — see [LICENSE](LICENSE).
```

- [ ] **Step 6: Create the package marker**

```python
# tokitty/tokitty/__init__.py
```
(empty file)

- [ ] **Step 7: Install pytest for local test runs**

Run:
```bash
pip install --user --break-system-packages pytest 2>/dev/null || pip install --user pytest
```
Expected: pytest installs successfully (or reports already satisfied). Verify with:
```bash
python3 -m pytest --version
```
Expected: prints a pytest version.

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add pyproject.toml LICENSE .gitignore README.md tokitty/__init__.py
git commit -m "chore: scaffold Tokitty repo (packaging, license, gitignore)"
```

---

### Task 2: `paths.py` — cross-platform state directory

**Files:**
- Create: `tokitty/tokitty/paths.py`
- Test: `tokitty/tests/test_paths.py`

**Interfaces:**
- Produces: `get_state_dir() -> pathlib.Path` — returns (and creates) the per-OS directory for position/lock files. Used by `lock.py` (Task 7) and `ui.py`/`__main__.py` (Tasks 13, 16).

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_paths.py
import sys
from pathlib import Path

from tokitty.paths import get_state_dir


def test_get_state_dir_creates_directory_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = get_state_dir()

    assert result == tmp_path / ".config" / "tokitty"
    assert result.is_dir()


def test_get_state_dir_respects_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "customcfg"))

    result = get_state_dir()

    assert result == tmp_path / "customcfg" / "tokitty"
    assert result.is_dir()


def test_get_state_dir_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    result = get_state_dir()

    assert result == tmp_path / "AppData" / "Local" / "Tokitty"
    assert result.is_dir()


def test_get_state_dir_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = get_state_dir()

    assert result == tmp_path / "Library" / "Application Support" / "Tokitty"
    assert result.is_dir()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.paths'` (or similar import error).

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/paths.py
"""Cross-platform resolution of Tokitty's per-user state directory."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def get_state_dir() -> Path:
    """Return the directory Tokitty should use for state (window position,
    lock file), creating it if it doesn't exist. Never inside the
    repo/install directory -- see the design spec's State/config location
    section for why.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        state_dir = Path(base) / "Tokitty"
    elif sys.platform == "darwin":
        state_dir = Path.home() / "Library" / "Application Support" / "Tokitty"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        if not base:
            base = str(Path.home() / ".config")
        state_dir = Path(base) / "tokitty"

    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_paths.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/paths.py tests/test_paths.py
git commit -m "feat: cross-platform state directory resolution"
```

---

### Task 3: `credentials.py` — override + home-relative resolution, loading, expiry

**Files:**
- Create: `tokitty/tokitty/credentials.py`
- Test: `tokitty/tests/test_credentials.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ENV_OVERRIDE = "TOKITTY_CREDENTIALS"` (module constant)
  - `class CredentialsError(Exception)`
  - `class AmbiguousCredentialsError(CredentialsError)`
  - `@dataclass(frozen=True) class LocalCredentialsSource: path: pathlib.Path`
  - `@dataclass(frozen=True) class WslDistroCredentialsSource: distro: str; wsl_path: str`
  - `CredentialsSource = Union[LocalCredentialsSource, WslDistroCredentialsSource]`
  - `describe_source(source: CredentialsSource) -> str`
  - `resolve_credentials_source() -> CredentialsSource` (raises `CredentialsError`/`AmbiguousCredentialsError`)
  - `load_credentials(source: CredentialsSource) -> dict` (raises `CredentialsError`)
  - `is_token_expired(creds: dict, now_ms: Optional[int] = None) -> bool`
  - On Windows fallback, lazily imports `tokitty.wsl_probe.find_wsl_credentials()` and `read_wsl_credentials()` (built in Task 4) — this task's tests only exercise the override/home-relative/expiry paths and the "nothing found on non-Windows" error path; Task 4 covers the WSL fallback itself.

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_credentials.py
import json
import sys
import time
from pathlib import Path

import pytest

from tokitty.credentials import (
    ENV_OVERRIDE,
    CredentialsError,
    LocalCredentialsSource,
    describe_source,
    is_token_expired,
    load_credentials,
    resolve_credentials_source,
)


def test_resolve_prefers_override_when_set(tmp_path, monkeypatch):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text('{"claudeAiOauth": {}}', encoding="utf-8")
    monkeypatch.setenv(ENV_OVERRIDE, str(creds_file))

    source = resolve_credentials_source()

    assert isinstance(source, LocalCredentialsSource)
    assert source.path == creds_file


def test_resolve_override_raises_if_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDE, str(tmp_path / "missing.json"))

    with pytest.raises(CredentialsError):
        resolve_credentials_source()


def test_resolve_falls_back_to_home_relative_path(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text('{"claudeAiOauth": {}}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    source = resolve_credentials_source()

    assert isinstance(source, LocalCredentialsSource)
    assert source.path == claude_dir / ".credentials.json"


def test_resolve_raises_when_nothing_found_on_non_windows(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(CredentialsError):
        resolve_credentials_source()


def test_load_credentials_returns_oauth_dict(tmp_path):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "abc"}}), encoding="utf-8")

    result = load_credentials(LocalCredentialsSource(path=creds_file))

    assert result == {"accessToken": "abc"}


def test_load_credentials_raises_on_invalid_json(tmp_path):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text("not json", encoding="utf-8")

    with pytest.raises(CredentialsError):
        load_credentials(LocalCredentialsSource(path=creds_file))


def test_load_credentials_raises_when_oauth_key_missing(tmp_path):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text(json.dumps({"somethingElse": {}}), encoding="utf-8")

    with pytest.raises(CredentialsError):
        load_credentials(LocalCredentialsSource(path=creds_file))


def test_describe_source_for_local():
    source = LocalCredentialsSource(path=Path("/tmp/x.json"))
    assert describe_source(source) == "/tmp/x.json"


def test_is_token_expired_true_when_past():
    now_ms = int(time.time() * 1000)
    assert is_token_expired({"expiresAt": now_ms - 1000}, now_ms=now_ms) is True


def test_is_token_expired_false_when_future():
    now_ms = int(time.time() * 1000)
    assert is_token_expired({"expiresAt": now_ms + 60_000}, now_ms=now_ms) is False


def test_is_token_expired_true_when_missing():
    assert is_token_expired({}) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.credentials'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/credentials.py
"""Cross-platform resolution and loading of Claude Code's OAuth credentials."""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

ENV_OVERRIDE = "TOKITTY_CREDENTIALS"


class CredentialsError(Exception):
    """Raised when the credentials file cannot be found or read."""


class AmbiguousCredentialsError(CredentialsError):
    """Raised when more than one candidate credentials file is found."""


@dataclass(frozen=True)
class LocalCredentialsSource:
    path: Path


@dataclass(frozen=True)
class WslDistroCredentialsSource:
    distro: str
    wsl_path: str


CredentialsSource = Union[LocalCredentialsSource, WslDistroCredentialsSource]


def describe_source(source: CredentialsSource) -> str:
    if isinstance(source, LocalCredentialsSource):
        return str(source.path)
    return f"WSL:{source.distro}:{source.wsl_path}"


def _override_source() -> Optional[LocalCredentialsSource]:
    value = os.environ.get(ENV_OVERRIDE)
    if not value:
        return None
    return LocalCredentialsSource(path=Path(value))


def _home_relative_source() -> Optional[LocalCredentialsSource]:
    candidate = Path.home() / ".claude" / ".credentials.json"
    if candidate.is_file():
        return LocalCredentialsSource(path=candidate)
    return None


def resolve_credentials_source() -> CredentialsSource:
    """Return the credentials source to use.

    Resolution order: explicit override, then home-relative, then (on
    Windows only) a WSL fallback probe. Raises CredentialsError if nothing
    is found, or AmbiguousCredentialsError if more than one WSL distro has
    a credentials file and no override is set.
    """
    override = _override_source()
    if override is not None:
        if not override.path.is_file():
            raise CredentialsError(f"{ENV_OVERRIDE} points to a missing file: {override.path}")
        return override

    home_relative = _home_relative_source()
    if home_relative is not None:
        return home_relative

    if sys.platform == "win32":
        from tokitty.wsl_probe import find_wsl_credentials

        distro, wsl_path = find_wsl_credentials()
        return WslDistroCredentialsSource(distro=distro, wsl_path=wsl_path)

    raise CredentialsError(
        "No Claude Code credentials found at ~/.claude/.credentials.json. "
        f"Set {ENV_OVERRIDE} to the correct path."
    )


def load_credentials(source: CredentialsSource) -> dict:
    """Read and parse the credentials file, returning the claudeAiOauth dict."""
    if isinstance(source, LocalCredentialsSource):
        try:
            raw = source.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CredentialsError(f"Could not read credentials file at {source.path}: {exc}") from exc
    else:
        from tokitty.wsl_probe import read_wsl_credentials

        raw = read_wsl_credentials(source.distro, source.wsl_path)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"Credentials file is not valid JSON: {exc}") from exc

    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise CredentialsError("Credentials file is missing 'claudeAiOauth'")

    return oauth


def is_token_expired(creds: dict, now_ms: Optional[int] = None) -> bool:
    """True if the access token's expiresAt (epoch ms) is in the past."""
    expires_at = creds.get("expiresAt")
    if expires_at is None:
        return True
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return now_ms >= expires_at
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_credentials.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/credentials.py tests/test_credentials.py
git commit -m "feat: credential source resolution (override + home-relative), loading, expiry check"
```

---

### Task 4: `wsl_probe.py` — Windows-only WSL fallback probing

**Files:**
- Create: `tokitty/tokitty/wsl_probe.py`
- Test: `tokitty/tests/test_wsl_probe.py`

**Interfaces:**
- Consumes: `tokitty.credentials.{ENV_OVERRIDE, CredentialsError, AmbiguousCredentialsError}` (Task 3).
- Produces:
  - `list_wsl_distros(run: Callable = subprocess.run) -> List[str]`
  - `find_wsl_credentials(run: Callable = subprocess.run) -> Tuple[str, str]` — returns `(distro, wsl_side_path)`; raises `AmbiguousCredentialsError` / `CredentialsError`.
  - `read_wsl_credentials(distro: str, wsl_path: str, run: Callable = subprocess.run) -> str` — raw file contents.
  - All three accept an injectable `run` callable (defaulting to `subprocess.run`) so tests never invoke a real `wsl.exe`. This module deliberately avoids `pathlib.Path`/UNC-path filesystem access for probing — `pathlib.Path` on a POSIX dev machine cannot represent or traverse Windows UNC paths, and the production code only ever runs this branch on real Windows anyway (guarded by `sys.platform == "win32"` in `credentials.py`), so subprocess-based probing is both the only thing testable here and the more robust choice in production.
  - Consumed by: `tokitty.credentials.resolve_credentials_source()` / `load_credentials()` (Task 3, already wired via lazy imports).

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_wsl_probe.py
import pytest

from tokitty.credentials import AmbiguousCredentialsError, CredentialsError
from tokitty.wsl_probe import find_wsl_credentials, list_wsl_distros, read_wsl_credentials


class FakeCompletedProcess:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_list_wsl_distros_parses_utf16_output():
    def fake_run(cmd, **kwargs):
        assert cmd == ["wsl.exe", "-l", "-q"]
        return FakeCompletedProcess(stdout="Ubuntu\r\ndocker-desktop\r\n".encode("utf-16-le"))

    distros = list_wsl_distros(run=fake_run)

    assert distros == ["Ubuntu", "docker-desktop"]


def test_find_wsl_credentials_returns_single_match():
    def fake_run(cmd, **kwargs):
        if cmd == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))
        if cmd[:4] == ["wsl.exe", "-d", "Ubuntu", "--"]:
            return FakeCompletedProcess(stdout=b"/home/cptsmidge/.claude/.credentials.json\n")
        raise AssertionError(f"unexpected command: {cmd}")

    distro, path = find_wsl_credentials(run=fake_run)

    assert distro == "Ubuntu"
    assert path == "/home/cptsmidge/.claude/.credentials.json"


def test_find_wsl_credentials_raises_when_multiple_distros_match():
    def fake_run(cmd, **kwargs):
        if cmd == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\r\nDebian\r\n".encode("utf-16-le"))
        return FakeCompletedProcess(stdout=b"/home/someone/.claude/.credentials.json\n")

    with pytest.raises(AmbiguousCredentialsError):
        find_wsl_credentials(run=fake_run)


def test_find_wsl_credentials_raises_when_none_found():
    def fake_run(cmd, **kwargs):
        if cmd == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))
        return FakeCompletedProcess(stdout=b"")

    with pytest.raises(CredentialsError):
        find_wsl_credentials(run=fake_run)


def test_read_wsl_credentials_returns_file_contents():
    def fake_run(cmd, **kwargs):
        assert cmd == ["wsl.exe", "-d", "Ubuntu", "--", "cat", "/home/cptsmidge/.claude/.credentials.json"]
        return FakeCompletedProcess(stdout=b'{"claudeAiOauth": {}}')

    contents = read_wsl_credentials("Ubuntu", "/home/cptsmidge/.claude/.credentials.json", run=fake_run)

    assert contents == '{"claudeAiOauth": {}}'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_wsl_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.wsl_probe'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/wsl_probe.py
"""Windows-only fallback: locate Claude Code's credentials inside WSL.

Deliberately subprocess-only (no pathlib/UNC filesystem access) -- see
the module docstring in the design spec's Cross-platform architecture
section. All three functions accept an injectable `run` callable so
tests never invoke a real wsl.exe.
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Tuple

from tokitty.credentials import ENV_OVERRIDE, AmbiguousCredentialsError, CredentialsError

_CHECK_SCRIPT = 'for u in /home/*; do f="$u/.claude/.credentials.json"; [ -f "$f" ] && echo "$f"; done'


def list_wsl_distros(run: Callable = subprocess.run) -> List[str]:
    """Return the names of installed WSL distros, via `wsl.exe -l -q`."""
    try:
        result = run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not list WSL distros: {exc}") from exc

    raw = result.stdout
    text = raw.decode("utf-16-le", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def _credentials_paths_in_distro(distro: str, run: Callable = subprocess.run) -> List[str]:
    """Return WSL-side absolute paths to credentials files found under /home in a distro."""
    try:
        result = run(
            ["wsl.exe", "-d", distro, "--", "sh", "-c", _CHECK_SCRIPT],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    raw = result.stdout
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_wsl_credentials(run: Callable = subprocess.run) -> Tuple[str, str]:
    """Probe every installed WSL distro for a Claude Code credentials file.

    Returns (distro_name, wsl_side_path) for the single match. Raises
    AmbiguousCredentialsError if more than one match exists across all
    distros, or CredentialsError if none do.
    """
    distros = list_wsl_distros(run=run)
    matches: List[Tuple[str, str]] = []

    for distro in distros:
        for path in _credentials_paths_in_distro(distro, run=run):
            matches.append((distro, path))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        joined = ", ".join(f"{d}:{p}" for d, p in matches)
        raise AmbiguousCredentialsError(
            f"Multiple Claude Code installs found across WSL distros: {joined}. "
            f"Set {ENV_OVERRIDE} to the correct path."
        )

    raise CredentialsError(
        "No Claude Code credentials found in any WSL distro. "
        f"Set {ENV_OVERRIDE} to the correct path."
    )


def read_wsl_credentials(distro: str, wsl_path: str, run: Callable = subprocess.run) -> str:
    """Return the raw file contents of a credentials file inside WSL, via `wsl.exe cat`."""
    try:
        result = run(["wsl.exe", "-d", distro, "--", "cat", wsl_path], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not read credentials from WSL distro {distro}: {exc}") from exc

    if result.returncode != 0:
        raise CredentialsError(f"wsl.exe cat failed for {distro}:{wsl_path}")

    raw = result.stdout
    return raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_wsl_probe.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/wsl_probe.py tests/test_wsl_probe.py
git commit -m "feat: Windows-only WSL distro probing for credential fallback"
```

---

### Task 5: `api.py` — defensive response parsing

**Files:**
- Create: `tokitty/tokitty/api.py`
- Test: `tokitty/tests/test_api.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class LimitInfo: kind: str; percent: float; severity: str; resets_at: Optional[datetime]; is_active: bool; model_display_name: Optional[str] = None`
  - `@dataclass(frozen=True) class UsageSnapshot: session_pct: float; session_resets_at: Optional[datetime]; weekly_pct: float; weekly_resets_at: Optional[datetime]; limits: List[LimitInfo]; credits_used: Optional[float]; credits_limit: Optional[float]; fetched_at: datetime`
  - `parse_usage_response(raw: dict) -> UsageSnapshot`
  - Consumed by: `mood.py` (Task 8/9), `poller.py`/`__main__.py` (Tasks 15/16).

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_api.py
import pytest

from tokitty.api import parse_usage_response

# Trimmed from a real captured response (2026-07-02).
FIXTURE = {
    "five_hour": {
        "utilization": 66.0,
        "resets_at": "2026-07-03T07:29:59.950281+00:00",
    },
    "seven_day": {
        "utilization": 32.0,
        "resets_at": "2026-07-06T23:59:59.950314+00:00",
    },
    "limits": [
        {
            "kind": "session",
            "percent": 66,
            "severity": "normal",
            "resets_at": "2026-07-03T07:29:59.950281+00:00",
            "scope": None,
            "is_active": True,
        },
        {
            "kind": "weekly_all",
            "percent": 32,
            "severity": "normal",
            "resets_at": "2026-07-06T23:59:59.950314+00:00",
            "scope": None,
            "is_active": False,
        },
        {
            "kind": "weekly_scoped",
            "percent": 33,
            "severity": "normal",
            "resets_at": "2026-07-06T23:59:59.950584+00:00",
            "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
            "is_active": False,
        },
    ],
    "spend": {
        "used": {"amount_minor": 362, "currency": "USD", "exponent": 2},
        "limit": {"amount_minor": 2000, "currency": "USD", "exponent": 2},
        "percent": 18,
        "enabled": True,
    },
}


def test_parse_usage_response_extracts_session_and_weekly_percent():
    snapshot = parse_usage_response(FIXTURE)

    assert snapshot.session_pct == 66.0
    assert snapshot.weekly_pct == 32.0


def test_parse_usage_response_converts_reset_times_to_aware_datetimes():
    snapshot = parse_usage_response(FIXTURE)

    assert snapshot.session_resets_at.tzinfo is not None
    assert snapshot.session_resets_at.year == 2026
    assert snapshot.session_resets_at.month == 7
    assert snapshot.session_resets_at.day == 3


def test_parse_usage_response_extracts_limits():
    snapshot = parse_usage_response(FIXTURE)

    assert len(snapshot.limits) == 3
    scoped = [l for l in snapshot.limits if l.kind == "weekly_scoped"][0]
    assert scoped.model_display_name == "Fable"
    assert scoped.percent == 33.0


def test_parse_usage_response_converts_credits_from_minor_units():
    snapshot = parse_usage_response(FIXTURE)

    assert snapshot.credits_used == pytest.approx(3.62)
    assert snapshot.credits_limit == pytest.approx(20.00)


def test_parse_usage_response_handles_missing_fields_gracefully():
    snapshot = parse_usage_response({})

    assert snapshot.session_pct == 0.0
    assert snapshot.weekly_pct == 0.0
    assert snapshot.limits == []
    assert snapshot.credits_used is None


def test_parse_usage_response_ignores_malformed_limit_entries():
    raw = {"limits": ["not-a-dict", {"kind": "session", "percent": 10}]}

    snapshot = parse_usage_response(raw)

    assert len(snapshot.limits) == 1
    assert snapshot.limits[0].kind == "session"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.api'`.

- [ ] **Step 3: Write the implementation (parsing only — HTTP client added in Task 6)**

```python
# tokitty/tokitty/api.py
"""Client for Claude Code's usage endpoint."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class LimitInfo:
    kind: str
    percent: float
    severity: str
    resets_at: Optional[datetime]
    is_active: bool
    model_display_name: Optional[str] = None


@dataclass(frozen=True)
class UsageSnapshot:
    session_pct: float
    session_resets_at: Optional[datetime]
    weekly_pct: float
    weekly_resets_at: Optional[datetime]
    limits: List[LimitInfo] = field(default_factory=list)
    credits_used: Optional[float] = None
    credits_limit: Optional[float] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def parse_usage_response(raw: dict) -> UsageSnapshot:
    """Defensively parse the usage endpoint's response into a UsageSnapshot.

    Every field is read with .get() and a safe default -- this is an
    undocumented endpoint that can add, remove, or rename fields at any
    time.
    """
    five_hour = raw.get("five_hour") or {}
    seven_day = raw.get("seven_day") or {}

    limits = []
    for entry in raw.get("limits") or []:
        if not isinstance(entry, dict):
            continue
        scope = entry.get("scope") or {}
        model = scope.get("model") or {}
        limits.append(
            LimitInfo(
                kind=entry.get("kind", "unknown"),
                percent=float(entry.get("percent") or 0.0),
                severity=str(entry.get("severity") or "normal"),
                resets_at=_parse_iso(entry.get("resets_at")),
                is_active=bool(entry.get("is_active", False)),
                model_display_name=model.get("display_name"),
            )
        )

    spend = raw.get("spend") or {}
    spend_used = spend.get("used") or {}
    spend_limit = spend.get("limit") or {}
    exponent = spend_used.get("exponent", spend_limit.get("exponent", 2))

    credits_used = None
    credits_limit = None
    used_minor = spend_used.get("amount_minor")
    limit_minor = spend_limit.get("amount_minor")
    if used_minor is not None:
        credits_used = used_minor / (10 ** exponent)
    if limit_minor is not None:
        credits_limit = limit_minor / (10 ** exponent)

    return UsageSnapshot(
        session_pct=float(five_hour.get("utilization") or 0.0),
        session_resets_at=_parse_iso(five_hour.get("resets_at")),
        weekly_pct=float(seven_day.get("utilization") or 0.0),
        weekly_resets_at=_parse_iso(seven_day.get("resets_at")),
        limits=limits,
        credits_used=credits_used,
        credits_limit=credits_limit,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_api.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/api.py tests/test_api.py
git commit -m "feat: defensive parsing of the usage-endpoint response"
```

---

### Task 6: `api.py` — HTTP fetch client

**Files:**
- Modify: `tokitty/tokitty/api.py`
- Modify: `tokitty/tests/test_api.py`

**Interfaces:**
- Produces (added to `api.py`):
  - `BASE_URL = "https://api.anthropic.com/api/oauth/usage"`
  - `BETA_HEADER = "oauth-2025-04-20"`
  - `class ApiError(Exception): status_code: Optional[int]`
  - `fetch_usage(access_token: str, timeout: float = 10.0) -> dict`
  - Consumed by: `__main__.py`'s `build_fetch_fn()` (Task 11).

- [ ] **Step 1: Add the failing tests**

Append to `tokitty/tests/test_api.py`:

```python
import urllib.error
from unittest.mock import MagicMock, patch

from tokitty.api import BASE_URL, ApiError, fetch_usage


def test_fetch_usage_sends_bearer_token_and_beta_header():
    fake_response = MagicMock()
    fake_response.read.return_value = b'{"ok": true}'
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with patch("tokitty.api.urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        result = fetch_usage("test-token-123")

    assert result == {"ok": True}
    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url == BASE_URL
    assert sent_request.get_header("Authorization") == "Bearer test-token-123"
    assert sent_request.get_header("Anthropic-beta") == "oauth-2025-04-20"


def test_fetch_usage_raises_api_error_with_status_code_on_http_error():
    http_error = urllib.error.HTTPError(BASE_URL, 401, "Unauthorized", {}, None)

    with patch("tokitty.api.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(ApiError) as exc_info:
            fetch_usage("expired-token")

    assert exc_info.value.status_code == 401


def test_fetch_usage_raises_api_error_on_network_error():
    with patch("tokitty.api.urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        with pytest.raises(ApiError) as exc_info:
            fetch_usage("some-token")

    assert exc_info.value.status_code is None


def test_fetch_usage_raises_api_error_on_invalid_json():
    fake_response = MagicMock()
    fake_response.read.return_value = b"not json"
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with patch("tokitty.api.urllib.request.urlopen", return_value=fake_response):
        with pytest.raises(ApiError):
            fetch_usage("some-token")
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_api.py -v`
Expected: the four new tests FAIL with `ImportError`/`AttributeError` (`BASE_URL`, `ApiError`, `fetch_usage` don't exist yet); the six from Task 5 still PASS.

- [ ] **Step 3: Add the HTTP client to `api.py`**

Add near the top of `tokitty/tokitty/api.py` (after the existing imports):

```python
import json
import urllib.error
import urllib.request

BASE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
USER_AGENT = "tokitty/0.1"


class ApiError(Exception):
    """Raised for any usage-endpoint failure: network, timeout, or non-2xx."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def fetch_usage(access_token: str, timeout: float = 10.0) -> dict:
    """Call the usage endpoint and return the parsed JSON body."""
    request = urllib.request.Request(
        BASE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ApiError(f"HTTP {exc.code} from usage endpoint", status_code=exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Network error reaching usage endpoint: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Usage endpoint returned invalid JSON: {exc}") from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_api.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/api.py tests/test_api.py
git commit -m "feat: usage-endpoint HTTP client"
```

---

### Task 7: `lock.py` — cross-platform single-instance advisory lock

**Files:**
- Create: `tokitty/tokitty/lock.py`
- Test: `tokitty/tests/test_lock.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (takes a plain `pathlib.Path` directory).
- Produces:
  - `class LockAcquisitionError(Exception)`
  - `class SingleInstanceLock: def __init__(self, lock_dir: Path, name: str = "tokitty.lock")`, `.acquire() -> None`, `.release() -> None`, context manager (`__enter__`/`__exit__`).
  - Consumed by: `__main__.py`'s `run_gui()` (Task 16), constructed with `get_state_dir()` from Task 2.

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_lock.py
import sys
import types

import pytest

from tokitty.lock import LockAcquisitionError, SingleInstanceLock


def test_second_lock_acquisition_fails_while_first_holds_it(tmp_path):
    first = SingleInstanceLock(tmp_path)
    first.acquire()
    try:
        second = SingleInstanceLock(tmp_path)
        with pytest.raises(LockAcquisitionError):
            second.acquire()
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path):
    first = SingleInstanceLock(tmp_path)
    first.acquire()
    first.release()

    second = SingleInstanceLock(tmp_path)
    second.acquire()
    second.release()


def test_context_manager_releases_on_exit(tmp_path):
    with SingleInstanceLock(tmp_path):
        pass

    with SingleInstanceLock(tmp_path):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="targets the POSIX fcntl branch specifically")
def test_uses_flock_on_posix(tmp_path, monkeypatch):
    import fcntl as real_fcntl

    calls = []

    def spy_flock(fd, operation):
        calls.append(operation)
        return real_fcntl.flock(fd, operation)

    monkeypatch.setattr("fcntl.flock", spy_flock)

    with SingleInstanceLock(tmp_path):
        pass

    assert (real_fcntl.LOCK_EX | real_fcntl.LOCK_NB) in calls


def test_windows_branch_calls_msvcrt_locking(tmp_path, monkeypatch):
    calls = []

    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.LK_NBLCK = 1
    fake_msvcrt.LK_UNLCK = 2
    fake_msvcrt.locking = lambda fd, mode, nbytes: calls.append(mode)

    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sys, "platform", "win32")

    lock = SingleInstanceLock(tmp_path)
    lock.acquire()
    lock.release()

    assert calls == [1, 2]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_lock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.lock'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/lock.py
"""Cross-platform single-instance advisory file lock."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


class LockAcquisitionError(Exception):
    """Raised when another instance already holds the lock."""


class SingleInstanceLock:
    """An OS-native advisory lock held for the process's lifetime.

    Uses fcntl.flock on POSIX and msvcrt.locking on Windows. The OS
    releases the lock automatically if the process exits or crashes, so
    unlike a PID file this can never go stale.
    """

    def __init__(self, lock_dir: Path, name: str = "tokitty.lock"):
        self._path = lock_dir / name
        self._file = None

    def acquire(self) -> None:
        self._file = open(self._path, "a+")
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self._file.close()
                self._file = None
                raise LockAcquisitionError("Another Tokitty instance is already running") from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._file.close()
                self._file = None
                raise LockAcquisitionError("Another Tokitty instance is already running") from exc

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_lock.py -v`
Expected: 5 passed (4 on Windows, where the POSIX-specific test is skipped).

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/lock.py tests/test_lock.py
git commit -m "feat: cross-platform single-instance advisory lock"
```

---

### Task 8: `mood.py` — steady mood ladder

**Files:**
- Create: `tokitty/tokitty/mood.py`
- Test: `tokitty/tests/test_mood.py`

**Interfaces:**
- Consumes: `tokitty.api.{LimitInfo, UsageSnapshot}` (Task 5).
- Produces:
  - `MOOD_THRESHOLDS: List[Tuple[float, str]]`
  - `compute_mood(session_pct: float, weekly_pct: float) -> Tuple[str, str]` — returns `(mood_key, driving_tag)`, `driving_tag` in `{"5h", "7d"}`.
  - Consumed by: `__main__.py`'s `_display_state_for()` (Task 16).

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_mood.py
import pytest

from tokitty.mood import compute_mood


@pytest.mark.parametrize(
    "session_pct,weekly_pct,expected_mood,expected_tag",
    [
        (0, 0, "sleeping", "5h"),
        (24.9, 0, "sleeping", "5h"),
        (25.0, 0, "content", "5h"),
        (49.9, 0, "content", "5h"),
        (50.0, 0, "interested", "5h"),
        (74.9, 0, "interested", "5h"),
        (75.0, 0, "alert", "5h"),
        (89.9, 0, "alert", "5h"),
        (90.0, 0, "panicked", "5h"),
        (99.9, 0, "panicked", "5h"),
        (10, 60, "interested", "7d"),
    ],
)
def test_compute_mood_thresholds(session_pct, weekly_pct, expected_mood, expected_tag):
    mood, tag = compute_mood(session_pct, weekly_pct)

    assert mood == expected_mood
    assert tag == expected_tag
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_mood.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.mood'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/mood.py
"""Pure logic for mapping usage data to the cat's mood and animation state."""
from __future__ import annotations

from typing import List, Tuple

MOOD_THRESHOLDS: List[Tuple[float, str]] = [
    (25.0, "sleeping"),
    (50.0, "content"),
    (75.0, "interested"),
    (90.0, "alert"),
    (100.0, "panicked"),
]


def compute_mood(session_pct: float, weekly_pct: float) -> Tuple[str, str]:
    """Return (mood_key, driving_tag) from the steady-state mood ladder.

    driving_tag is "5h" if the session percent is the larger (or equal)
    of the two, else "7d". Only meaningful when neither limit is capped
    -- callers should check select_binding_capped_limit() first (Task 9).
    """
    driving_tag = "5h" if session_pct >= weekly_pct else "7d"
    percent = max(session_pct, weekly_pct)

    for threshold, mood in MOOD_THRESHOLDS:
        if percent < threshold:
            return mood, driving_tag
    return "panicked", driving_tag
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_mood.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/mood.py tests/test_mood.py
git commit -m "feat: steady-state mood ladder"
```

---

### Task 9: `mood.py` — capped/wake substate + activate-transition detection

**Files:**
- Modify: `tokitty/tokitty/mood.py`
- Modify: `tokitty/tests/test_mood.py`

**Interfaces:**
- Produces (added to `mood.py`):
  - `BLOCKED_SEVERITIES: set`
  - `CAPPED_WAKE_WINDOWS: dict` — `{"session": {"stir": 900, "wake": 180}, "weekly": {"stir": 10800, "wake": 1200}}`
  - `is_capped(limit: LimitInfo) -> bool`
  - `select_binding_capped_limit(limits: List[LimitInfo]) -> Optional[LimitInfo]`
  - `@dataclass(frozen=True) class CappedState: substate: str; time_to_reset: timedelta; driving_tag: str`
  - `compute_capped_substate(binding_limit: LimitInfo, now: Optional[datetime] = None) -> CappedState`
  - `detect_activate(previous: Optional[UsageSnapshot], current: UsageSnapshot) -> bool`
  - Consumed by: `poller.py` (Task 15), `__main__.py`'s `_display_state_for()` (Task 16).

- [ ] **Step 1: Add the failing tests**

Append to `tokitty/tests/test_mood.py`:

```python
from datetime import datetime, timedelta, timezone

from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.mood import (
    compute_capped_substate,
    detect_activate,
    is_capped,
    select_binding_capped_limit,
)


def _limit(kind="session", percent=100.0, severity="normal", is_active=True, resets_at=None):
    if resets_at is None:
        resets_at = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
    return LimitInfo(kind=kind, percent=percent, severity=severity, resets_at=resets_at, is_active=is_active)


def test_is_capped_true_at_100_percent():
    assert is_capped(_limit(percent=100.0)) is True


def test_is_capped_false_below_100_percent():
    assert is_capped(_limit(percent=99.9)) is False


def test_is_capped_true_for_blocked_severity_regardless_of_percent():
    assert is_capped(_limit(percent=10.0, severity="exceeded")) is True


def test_select_binding_capped_limit_picks_soonest_reset():
    soon = _limit(kind="session", resets_at=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc))
    later = _limit(kind="weekly_all", resets_at=datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc))

    result = select_binding_capped_limit([later, soon])

    assert result is soon


def test_select_binding_capped_limit_ignores_uncapped_and_inactive():
    uncapped = _limit(percent=10.0)
    inactive = _limit(is_active=False)

    result = select_binding_capped_limit([uncapped, inactive])

    assert result is None


def test_compute_capped_substate_flopped_when_far_from_reset():
    limit = _limit(kind="session")
    now = limit.resets_at - timedelta(hours=1)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "flopped"
    assert state.driving_tag == "5h"


def test_compute_capped_substate_stirring_within_stir_window():
    limit = _limit(kind="session")
    now = limit.resets_at - timedelta(minutes=10)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "stirring"


def test_compute_capped_substate_waking_within_wake_window():
    limit = _limit(kind="session")
    now = limit.resets_at - timedelta(minutes=1)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "waking"


def test_compute_capped_substate_uses_weekly_window_for_weekly_kinds():
    limit = _limit(kind="weekly_all")
    now = limit.resets_at - timedelta(hours=1)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "stirring"
    assert state.driving_tag == "7d"


def _snapshot(limits):
    return UsageSnapshot(session_pct=0.0, session_resets_at=None, weekly_pct=0.0, weekly_resets_at=None, limits=limits)


def test_detect_activate_true_when_capped_clears():
    previous = _snapshot([_limit(percent=100.0)])
    current = _snapshot([_limit(percent=0.0, is_active=False)])

    assert detect_activate(previous, current) is True


def test_detect_activate_false_when_still_capped():
    capped_limit = _limit(percent=100.0)
    previous = _snapshot([capped_limit])
    current = _snapshot([capped_limit])

    assert detect_activate(previous, current) is False


def test_detect_activate_false_when_no_previous_snapshot():
    current = _snapshot([])

    assert detect_activate(None, current) is False
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_mood.py -v`
Expected: the 12 new tests FAIL with `ImportError` (new names don't exist yet); the 11 from Task 8 still PASS.

- [ ] **Step 3: Add the capped/wake logic to `mood.py`**

Add to `tokitty/tokitty/mood.py` (after the existing imports, update the `typing` import line to include `Optional`):

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from tokitty.api import LimitInfo, UsageSnapshot

BLOCKED_SEVERITIES = {"exceeded", "blocked", "rate_limited", "over_limit"}

CAPPED_WAKE_WINDOWS = {
    "session": {"stir": 15 * 60, "wake": 3 * 60},
    "weekly": {"stir": 3 * 3600, "wake": 20 * 60},
}


def is_capped(limit: LimitInfo) -> bool:
    """True if this limit is blocking usage right now."""
    if limit.severity.lower() in BLOCKED_SEVERITIES:
        return True
    return limit.percent >= 100.0


def _wake_window_key(kind: str) -> str:
    return "session" if kind == "session" else "weekly"


def select_binding_capped_limit(limits: List[LimitInfo]) -> Optional[LimitInfo]:
    """Return the capped, active limit that resets soonest, or None if
    nothing is currently capped."""
    candidates = [l for l in limits if l.is_active and is_capped(l) and l.resets_at is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda l: l.resets_at)


@dataclass(frozen=True)
class CappedState:
    substate: str  # "flopped" | "stirring" | "waking"
    time_to_reset: timedelta
    driving_tag: str  # "5h" | "7d"


def compute_capped_substate(binding_limit: LimitInfo, now: Optional[datetime] = None) -> CappedState:
    """Return which sub-state of the capped/wake sequence applies right now."""
    if now is None:
        now = datetime.now(timezone.utc)

    time_to_reset = binding_limit.resets_at - now
    seconds_left = max(time_to_reset.total_seconds(), 0.0)

    windows = CAPPED_WAKE_WINDOWS[_wake_window_key(binding_limit.kind)]

    if seconds_left <= windows["wake"]:
        substate = "waking"
    elif seconds_left <= windows["stir"]:
        substate = "stirring"
    else:
        substate = "flopped"

    driving_tag = "5h" if binding_limit.kind == "session" else "7d"
    return CappedState(substate=substate, time_to_reset=time_to_reset, driving_tag=driving_tag)


def detect_activate(previous: Optional[UsageSnapshot], current: UsageSnapshot) -> bool:
    """True exactly when this poll observes a previously-capped limit clear.

    This is data-driven (comparing two real polls), not a wall-clock guess,
    since the server's actual reset can lag the advertised resets_at.
    """
    if previous is None:
        return False

    was_capped = select_binding_capped_limit(previous.limits) is not None
    is_now_capped = select_binding_capped_limit(current.limits) is not None

    return was_capped and not is_now_capped
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_mood.py -v`
Expected: 23 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/mood.py tests/test_mood.py
git commit -m "feat: capped/wake-sequence state machine and activate-transition detection"
```

---

### Task 10: `sprites.py` — pixel data, palette, frame composition

**Files:**
- Create: `tokitty/tokitty/sprites.py`
- Test: `tokitty/tests/test_sprites.py`

**Interfaces:**
- Produces:
  - `SCALE = 4`
  - `PALETTE: Dict[str, str]`
  - `ALL_STATES: Tuple[str, ...]`
  - `get_frames(state: str) -> List[List[str]]` (raises `KeyError` for unknown state)
  - States: `sleeping, content, interested, alert, panicked, confused, waking, activate, flopped, stirring` (10 states x 2 frames = 20 sprites).
  - Consumed by: `ui.py` (Tasks 13/14).

Sprite art is a deliberately simple first pass — one 9x7-pixel base body template reused across sitting states via eye/accent-cell substitution, and a second 9x7 "lying down" template for the capped/wake states. This guarantees every frame is structurally valid by construction (equal dimensions, only palette-defined characters) rather than by hand-aligning ~20 independent hand-typed grids. Refining the art (bigger grid, more per-state variation) is roadmap item 8 in the spec (PNG sprite upgrade).

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_sprites.py
import pytest

from tokitty.sprites import ALL_STATES, PALETTE, get_frames


def test_all_states_have_at_least_two_frames():
    for state in ALL_STATES:
        assert len(get_frames(state)) >= 2


def test_all_frame_rows_are_equal_length_within_a_frame():
    for state in ALL_STATES:
        for frame in get_frames(state):
            row_lengths = {len(row) for row in frame}
            assert len(row_lengths) == 1, f"{state} has mismatched row widths"


def test_all_frames_for_a_state_share_dimensions():
    for state in ALL_STATES:
        frames = get_frames(state)
        shapes = {(len(frame), len(frame[0])) for frame in frames}
        assert len(shapes) == 1, f"{state} frames differ in overall shape"


def test_every_character_used_is_in_the_palette():
    for state in ALL_STATES:
        for frame in get_frames(state):
            for row in frame:
                for ch in row:
                    assert ch in PALETTE, f"{state} uses undefined character {ch!r}"


def test_unknown_state_raises_key_error():
    with pytest.raises(KeyError):
        get_frames("nonexistent-mood")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_sprites.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.sprites'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/sprites.py
"""Pixel-art data for the cat: palette, base templates, and per-mood frames.

Two 9x7 base templates (a sitting pose and a lying-down pose) are each
composed with small per-state substitution dicts to produce concrete
frames. Placeholder letters (L, R, A, T) each appear exactly once in
their template, so "replace every occurrence of this character" is
equivalent to "replace this one designated cell".
"""
from __future__ import annotations

from typing import Dict, List

SCALE = 4  # device pixels per sprite pixel when rendered on the Canvas

PALETTE: Dict[str, str] = {
    ".": "",  # transparent -- not drawn
    "k": "#2b1a12",  # outline
    "o": "#e8823c",  # coat
    "O": "#c26a2c",  # coat shading
    "p": "#f2a5b8",  # ears
    "w": "#fff6ec",  # muzzle / belly
    "e": "#3fae5c",  # eye, open
    "z": "#8fd0e8",  # sleep accent
    "!": "#e6483c",  # alarm accent
    "?": "#e8c23c",  # confused accent
    "h": "#f2a5b8",  # happy/pounce accent
}

# Sitting pose. L/R are the left/right eye cells, A is a single accent
# cell (nose/whisker marker) each state can repaint.
BASE_TEMPLATE: List[str] = [
    "..kppk...",
    ".kkoookk.",
    "kLooAoRk.",
    "kOoooooOk",
    "kwwwwwwok",
    ".kkkkkkk.",
    "..k...k..",
]

# Lying-down pose for the capped/wake sequence. L is the (near) eye, T
# is the tail-tip cell.
FLOPPED_TEMPLATE: List[str] = [
    ".........",
    ".kk......",
    "kLoOOOOk.",
    "kooooook.",
    ".kkkkkkT.",
    ".........",
    ".........",
]

FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "sleeping": [{"L": "k", "R": "k", "A": "z"}, {"L": "k", "R": "k", "A": "."}],
    "content": [{"L": "e", "R": "e", "A": "."}, {"L": "k", "R": "k", "A": "."}],
    "interested": [{"L": "e", "R": "e", "A": "."}, {"L": "e", "R": "k", "A": "."}],
    "alert": [{"L": "e", "R": "e", "A": "."}, {"L": "e", "R": "e", "A": "!"}],
    "panicked": [{"L": "e", "R": "e", "A": "!"}, {"L": "e", "R": "e", "A": "."}],
    "confused": [{"L": "e", "R": "k", "A": "?"}, {"L": "k", "R": "e", "A": "?"}],
    "waking": [{"L": "e", "R": "k", "A": "."}, {"L": "k", "R": "e", "A": "."}],
    "activate": [{"L": "e", "R": "e", "A": "h"}, {"L": "e", "R": "e", "A": "."}],
}

FLOPPED_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "flopped": [{"L": "k", "T": "k"}, {"L": "k", "T": "O"}],
    "stirring": [{"L": "k", "T": "O"}, {"L": "e", "T": "k"}],
}

ALL_STATES = tuple(FRAME_SPECS.keys()) + tuple(FLOPPED_FRAME_SPECS.keys())


def _apply(template: List[str], subs: Dict[str, str]) -> List[str]:
    return ["".join(subs.get(ch, ch) for ch in row) for row in template]


def get_frames(state: str) -> List[List[str]]:
    """Return the list of frame grids (each a list of equal-length row
    strings) for the given mood/sub-state name."""
    if state in FRAME_SPECS:
        template, specs = BASE_TEMPLATE, FRAME_SPECS[state]
    elif state in FLOPPED_FRAME_SPECS:
        template, specs = FLOPPED_TEMPLATE, FLOPPED_FRAME_SPECS[state]
    else:
        raise KeyError(f"Unknown sprite state: {state!r}")

    return [_apply(template, subs) for subs in specs]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_sprites.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/sprites.py tests/test_sprites.py
git commit -m "feat: pixel-art sprite data for all mood and wake-sequence states"
```

---

### Task 11: `display.py` — countdown/time/bar-color formatting

**Files:**
- Create: `tokitty/tokitty/display.py`
- Test: `tokitty/tests/test_display.py`

**Interfaces:**
- Produces:
  - `format_countdown(seconds_left: float) -> str` — e.g. `"1h 02m 03s"`, `"2m 05s"`, `"45s"`.
  - `format_reset_time(dt: datetime) -> str` — 12-hour clock, no leading zero, portable (no `%-I`, which Windows' C runtime doesn't support).
  - `format_reset_day(dt: datetime) -> str` — e.g. `"Mon Jul 6"`.
  - `bar_color(percent: float) -> str` — hex color, green/amber/red by threshold.
  - Consumed by: `ui.py` (Task 14), `__main__.py` (Task 16). Kept in its own module (no `tkinter` import) so it's unit-testable without a GUI toolkit installed.

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_display.py
import re
from datetime import datetime, timezone

from tokitty.display import bar_color, format_countdown, format_reset_day, format_reset_time


def test_format_countdown_shows_hours_minutes_seconds():
    assert format_countdown(3723) == "1h 02m 03s"


def test_format_countdown_shows_minutes_seconds_under_an_hour():
    assert format_countdown(125) == "2m 05s"


def test_format_countdown_shows_seconds_only_under_a_minute():
    assert format_countdown(45) == "45s"


def test_format_countdown_floors_negative_to_zero():
    assert format_countdown(-10) == "0s"


def test_bar_color_green_below_50():
    assert bar_color(10) == "#4caf6b"


def test_bar_color_amber_between_50_and_80():
    assert bar_color(60) == "#e0a838"


def test_bar_color_red_at_80_and_above():
    assert bar_color(80) == "#e05252"


def test_format_reset_time_has_no_leading_zero_hour():
    dt = datetime(2026, 7, 3, 1, 29, tzinfo=timezone.utc)
    result = format_reset_time(dt)
    assert ("AM" in result) or ("PM" in result)
    assert not result.startswith("0")


def test_format_reset_day_format_is_weekday_month_day():
    dt = datetime(2026, 7, 6, 23, 59, tzinfo=timezone.utc)
    result = format_reset_day(dt)
    assert re.match(r"^[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2}$", result)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_display.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.display'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/display.py
"""Pure formatting helpers for the UI: countdowns, local times, bar colors.

Kept free of any tkinter import so it can be unit-tested without a GUI
toolkit installed. Deliberately avoids platform-specific strftime flags
like %-I / %-d -- those are glibc/BSD extensions unsupported by the
Windows C runtime, and Windows is Tokitty's primary target platform.
"""
from __future__ import annotations

from datetime import datetime

GREEN = "#4caf6b"
AMBER = "#e0a838"
RED = "#e05252"


def bar_color(percent: float) -> str:
    if percent >= 80:
        return RED
    if percent >= 50:
        return AMBER
    return GREEN


def format_countdown(seconds_left: float) -> str:
    seconds_left = max(int(seconds_left), 0)
    hours, remainder = divmod(seconds_left, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def format_reset_time(dt: datetime) -> str:
    local = dt.astimezone()
    hour_12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    return f"{hour_12}:{local.minute:02d} {period}"


def format_reset_day(dt: datetime) -> str:
    local = dt.astimezone()
    return f"{local.strftime('%a')} {local.strftime('%b')} {local.day}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_display.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/display.py tests/test_display.py
git commit -m "feat: portable countdown/time/bar-color formatting"
```

---

### Task 12: `geometry.py` — window-position clamping

**Files:**
- Create: `tokitty/tokitty/geometry.py`
- Test: `tokitty/tests/test_geometry.py`

**Interfaces:**
- Produces: `clamp_position(x: int, y: int, width: int, height: int, screen_w: int, screen_h: int) -> Tuple[int, int]`.
- Consumed by: `ui.py` (Task 13). Kept separate from `ui.py` (no `tkinter` import) for the same testability reason as `display.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_geometry.py
from tokitty.geometry import clamp_position


def test_clamp_position_leaves_in_bounds_position_unchanged():
    assert clamp_position(100, 100, 300, 110, 1920, 1080) == (100, 100)


def test_clamp_position_resets_offscreen_negative_position():
    x, y = clamp_position(-500, 100, 300, 110, 1920, 1080)
    assert 0 <= x <= 1920 - 300
    assert 0 <= y <= 1080 - 110


def test_clamp_position_resets_position_beyond_screen_bounds():
    x, y = clamp_position(5000, 5000, 300, 110, 1920, 1080)
    assert x == 1920 - 300 - 24
    assert y == 1080 - 110 - 24


def test_clamp_position_handles_window_larger_than_screen():
    assert clamp_position(100, 100, 5000, 5000, 1920, 1080) == (0, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_geometry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.geometry'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/geometry.py
"""Pure window-position math -- no tkinter import, so it's testable
without a GUI toolkit installed."""
from __future__ import annotations

from typing import Tuple


def clamp_position(x: int, y: int, width: int, height: int, screen_w: int, screen_h: int) -> Tuple[int, int]:
    """Clamp a saved window position so the window stays fully on-screen.

    If the window doesn't fit at all (e.g. it's larger than the current
    screen), anchor at the top-left. If a saved position is out of
    bounds (e.g. a disconnected monitor), fall back to near the
    bottom-right corner.
    """
    if width > screen_w or height > screen_h:
        return 0, 0

    max_x = screen_w - width
    max_y = screen_h - height

    if x < 0 or x > max_x or y < 0 or y > max_y:
        margin = 24
        return max(screen_w - width - margin, 0), max(screen_h - height - margin, 0)

    return x, y
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_geometry.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/geometry.py tests/test_geometry.py
git commit -m "feat: window-position clamping"
```

---

### Task 13: `poller.py` — background polling worker

**Files:**
- Create: `tokitty/tokitty/poller.py`
- Test: `tokitty/tests/test_poller.py`

**Interfaces:**
- Consumes: `tokitty.api.UsageSnapshot` (Task 5), `tokitty.mood.{compute_capped_substate, select_binding_capped_limit}` (Task 9).
- Produces:
  - `POLL_INTERVAL = 120.0`, `WAKING_POLL_INTERVAL = 20.0`, `BACKOFF_INITIAL = 30.0`, `BACKOFF_MAX = 600.0`
  - `@dataclass(frozen=True) class PollResult: status: str; snapshot: Optional[UsageSnapshot]; message: Optional[str]; fetched_at: datetime; source_description: Optional[str] = None` — `status` in `{"ok", "stale_token", "credentials_unreachable", "ambiguous_credentials", "api_error"}`.
  - `class Poller: def __init__(self, fetch_fn: Callable[[], PollResult], poll_interval=POLL_INTERVAL, waking_poll_interval=WAKING_POLL_INTERVAL, sleep_fn: Optional[Callable[[float], bool]] = None)`, `.start()`, `.stop()`, `.request_refresh()`, `.get_latest() -> Optional[PollResult]`.
  - Consumed by: `__main__.py`'s `run_gui()` (Task 16), constructed with a `fetch_fn` built in Task 16.

- [ ] **Step 1: Write the failing tests**

```python
# tokitty/tests/test_poller.py
import threading
import time
from datetime import datetime, timezone

from tokitty.poller import PollResult, Poller


def _ok_result():
    return PollResult(status="ok", snapshot=None, message=None, fetched_at=datetime.now(timezone.utc))


def test_poller_calls_fetch_fn_and_stores_latest_result():
    call_count = {"n": 0}
    ready = threading.Event()

    def fake_fetch():
        call_count["n"] += 1
        ready.set()
        return _ok_result()

    poller = Poller(fetch_fn=fake_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        assert ready.wait(timeout=2)
        time.sleep(0.05)
        assert poller.get_latest().status == "ok"
        assert call_count["n"] >= 1
    finally:
        poller.stop()


def test_poller_recovers_after_an_error():
    results = iter(
        [
            PollResult(status="api_error", snapshot=None, message="boom", fetched_at=datetime.now(timezone.utc)),
            _ok_result(),
        ]
    )
    done = threading.Event()

    def fake_fetch():
        try:
            result = next(results)
        except StopIteration:
            done.set()
            return _ok_result()
        if result.status == "ok":
            done.set()
        return result

    poller = Poller(fetch_fn=fake_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        assert done.wait(timeout=2)
        time.sleep(0.05)
        assert poller.get_latest().status == "ok"
    finally:
        poller.stop()


def test_request_refresh_wakes_the_poller_immediately():
    call_count = {"n": 0}
    lock = threading.Lock()

    def fake_fetch():
        with lock:
            call_count["n"] += 1
        return _ok_result()

    poller = Poller(fetch_fn=fake_fetch, poll_interval=3600)
    poller.start()
    try:
        time.sleep(0.05)
        with lock:
            first_count = call_count["n"]
        poller.request_refresh()
        time.sleep(0.1)
        with lock:
            assert call_count["n"] > first_count
    finally:
        poller.stop()


def test_poller_never_raises_out_of_the_loop_when_fetch_fn_raises():
    def raising_fetch():
        raise RuntimeError("boom")

    poller = Poller(fetch_fn=raising_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        time.sleep(0.1)
        latest = poller.get_latest()
        assert latest is not None
        assert latest.status == "api_error"
    finally:
        poller.stop()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_poller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tokitty.poller'`.

- [ ] **Step 3: Write the implementation**

```python
# tokitty/tokitty/poller.py
"""Background polling of the usage endpoint, decoupled from the UI."""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from tokitty.api import UsageSnapshot
from tokitty.mood import compute_capped_substate, select_binding_capped_limit

POLL_INTERVAL = 120.0
WAKING_POLL_INTERVAL = 20.0
BACKOFF_INITIAL = 30.0
BACKOFF_MAX = 600.0
BACKOFF_JITTER = 0.2


@dataclass(frozen=True)
class PollResult:
    status: str  # "ok" | "stale_token" | "credentials_unreachable" | "ambiguous_credentials" | "api_error"
    snapshot: Optional[UsageSnapshot]
    message: Optional[str]
    fetched_at: datetime
    source_description: Optional[str] = None


class Poller:
    """Runs `fetch_fn` on a daemon thread at a configurable interval,
    exposing the latest PollResult and a thread-safe refresh-now trigger.
    """

    def __init__(
        self,
        fetch_fn: Callable[[], PollResult],
        poll_interval: float = POLL_INTERVAL,
        waking_poll_interval: float = WAKING_POLL_INTERVAL,
        sleep_fn: Optional[Callable[[float], bool]] = None,
    ):
        self._fetch_fn = fetch_fn
        self._poll_interval = poll_interval
        self._waking_poll_interval = waking_poll_interval
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[PollResult] = None
        self._thread: Optional[threading.Thread] = None
        self._sleep_fn = sleep_fn or self._wake_event.wait

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def request_refresh(self) -> None:
        self._wake_event.set()

    def get_latest(self) -> Optional[PollResult]:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        backoff = BACKOFF_INITIAL
        while not self._stop_event.is_set():
            result = self._poll_once()
            with self._lock:
                self._latest = result

            if result.status == "ok":
                backoff = BACKOFF_INITIAL
                interval = self._next_interval(result)
            else:
                interval = backoff
                jitter = backoff * BACKOFF_JITTER * random.random()
                backoff = min(backoff * 2 + jitter, BACKOFF_MAX)

            self._wake_event.clear()
            self._sleep_fn(interval)

    def _poll_once(self) -> PollResult:
        try:
            return self._fetch_fn()
        except Exception as exc:
            return PollResult(status="api_error", snapshot=None, message=str(exc), fetched_at=datetime.now(timezone.utc))

    def _next_interval(self, result: PollResult) -> float:
        if result.snapshot is None:
            return self._poll_interval

        binding = select_binding_capped_limit(result.snapshot.limits)
        if binding is None:
            return self._poll_interval

        state = compute_capped_substate(binding)
        if state.substate == "waking":
            return self._waking_poll_interval
        return self._poll_interval
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/test_poller.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/poller.py tests/test_poller.py
git commit -m "feat: background polling worker with backoff and waking-window tightening"
```

---

### Task 14: `__main__.py` (data pipeline only) — `--debug-print`, no GUI yet

**Files:**
- Create: `tokitty/tokitty/__main__.py`

**Interfaces:**
- Consumes: `tokitty.credentials.{resolve_credentials_source, load_credentials, is_token_expired, describe_source, CredentialsError, AmbiguousCredentialsError}` (Task 3), `tokitty.api.{fetch_usage, parse_usage_response, ApiError}` (Tasks 5/6), `tokitty.poller.PollResult` (Task 13).
- Produces:
  - `build_fetch_fn() -> Callable[[], PollResult]` — wires credentials + api together into a single zero-argument function suitable for `Poller`.
  - `debug_print() -> int` — calls the fetch function once, prints the result, exits (no GUI).
  - `main(argv: Optional[list] = None) -> int` — dispatches to `debug_print()` on `--debug-print`, otherwise (for now) prints a "GUI not wired up yet" message; Task 17 replaces that branch with the real `run_gui()`.

This task exists to get a real, verifiable end-to-end pipeline (credentials → HTTP → parsing) working and checkable against a live account *before* any GUI code is written — a natural checkpoint the spec's Testing section calls "Manual: verify against Claude Code's own `/usage`."

- [ ] **Step 1: Write the implementation**

```python
# tokitty/tokitty/__main__.py
"""Entry point: python -m tokitty."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from tokitty.api import ApiError, fetch_usage, parse_usage_response
from tokitty.credentials import (
    AmbiguousCredentialsError,
    CredentialsError,
    describe_source,
    is_token_expired,
    load_credentials,
    resolve_credentials_source,
)
from tokitty.poller import PollResult


def build_fetch_fn():
    def fetch() -> PollResult:
        now = datetime.now(timezone.utc)
        try:
            source = resolve_credentials_source()
        except AmbiguousCredentialsError as exc:
            return PollResult(status="ambiguous_credentials", snapshot=None, message=str(exc), fetched_at=now)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)

        try:
            creds = load_credentials(source)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)

        if is_token_expired(creds):
            return PollResult(
                status="stale_token",
                snapshot=None,
                message="access token expired",
                fetched_at=now,
                source_description=describe_source(source),
            )

        try:
            raw = fetch_usage(creds["accessToken"])
        except ApiError as exc:
            status = "stale_token" if exc.status_code == 401 else "api_error"
            return PollResult(status=status, snapshot=None, message=str(exc), fetched_at=now)

        snapshot = parse_usage_response(raw)
        return PollResult(
            status="ok", snapshot=snapshot, message=None, fetched_at=now, source_description=describe_source(source)
        )

    return fetch


def debug_print() -> int:
    result = build_fetch_fn()()
    print(f"status: {result.status}")
    if result.message:
        print(f"message: {result.message}")
    if result.source_description:
        print(f"credentials source: {result.source_description}")
    if result.snapshot is not None:
        s = result.snapshot
        print(f"session: {s.session_pct:.1f}% (resets {s.session_resets_at})")
        print(f"weekly:  {s.weekly_pct:.1f}% (resets {s.weekly_resets_at})")
        if s.credits_used is not None:
            print(f"credits: ${s.credits_used:.2f} / ${s.credits_limit:.2f}")
    return 0


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--debug-print" in argv:
        return debug_print()
    print("GUI not wired up yet -- run with --debug-print for now.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against the real account (WSL side) to verify the pipeline end-to-end**

Run:
```bash
cd /mnt/c/Tools/tokitty && PYTHONPATH=. python3 -m tokitty --debug-print
```
Expected: prints `status: ok`, a `session: NN.N% (resets ...)` line, a `weekly: NN.N% (resets ...)` line, and (if you've used extra-usage credits) a `credits: $X.XX / $Y.YY` line. Compare the percentages against what Claude Code's own `/usage` command reports at the same moment — they should match (within rounding).

- [ ] **Step 3: Run it against the real account via native Windows Python, to verify the WSL-fallback branch**

Run:
```bash
cd /mnt/c/Tools/tokitty && TOKITTY_TEST="native-windows" /mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe -m tokitty --debug-print
```
Expected: same `status: ok` output. Since native Windows Python's home directory has no `.claude` folder with credentials on this machine, this run exercises `resolve_credentials_source()`'s `sys.platform == "win32"` branch through `wsl_probe.py` for real — if this succeeds, both credential-resolution code paths are now verified against the live account, not just unit tests.

- [ ] **Step 4: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/__main__.py
git commit -m "feat: end-to-end data pipeline with --debug-print (no GUI yet)"
```

---

### Task 15: `ui.py` — window shell (chrome, drag, topmost, menu, position persistence)

**Files:**
- Create: `tokitty/tokitty/ui.py`

**Interfaces:**
- Consumes: `tokitty.geometry.clamp_position` (Task 12).
- Produces: `class TokittyWindow` with `__init__(self, root: tk.Tk, state_dir: Path)`, a public `on_refresh_requested` attribute (callable, set externally), and (stubbed in this task, filled in Task 16) a `render(...)` method.
- No automated test — this is the GUI module. Verified manually in Step 3 below.

- [ ] **Step 1: Install tkinter in WSL (needed to run/verify this module from this dev environment)**

Run:
```bash
sudo apt install -y python3-tk
```
Expected: installs successfully. Verify with:
```bash
python3 -c "import tkinter; print('tkinter', tkinter.TkVersion)"
```
Expected: prints `tkinter 8.6` (or similar).

- [ ] **Step 2: Write the window shell**

```python
# tokitty/tokitty/ui.py
"""Tkinter window: chrome, drag, always-on-top, right-click menu,
position persistence. Rendering (bars, cat frames) is added in Task 16.
The only module in this package that imports tkinter.
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path

from tokitty.geometry import clamp_position

CARD_WIDTH = 300
CARD_HEIGHT = 110
BG_COLOR = "#1c1c22"
FG_COLOR = "#f0f0f0"
DIM_COLOR = "#8a8a92"
BAR_BG = "#333340"

POSITION_FILENAME = "position.json"


class TokittyWindow:
    def __init__(self, root: tk.Tk, state_dir: Path):
        self.root = root
        self.state_dir = state_dir
        self._position_path = state_dir / POSITION_FILENAME
        self._drag_offset = (0, 0)
        self._always_on_top = tk.BooleanVar(value=True)
        self.on_refresh_requested = None  # set externally by __main__.py

        self._configure_window()
        self._build_widgets()
        self._restore_position()
        self._bind_drag()
        self._build_context_menu()

    def _configure_window(self) -> None:
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG_COLOR)
        self.root.geometry(f"{CARD_WIDTH}x{CARD_HEIGHT}")

        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

    def _build_widgets(self) -> None:
        self.canvas = tk.Canvas(self.root, width=100, height=100, bg=BG_COLOR, highlightthickness=0)
        self.canvas.place(x=6, y=5)

        self.tag_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.tag_label.place(x=8, y=108)

        self.session_label = tk.Label(self.root, text="SESSION", fg=FG_COLOR, bg=BG_COLOR, font=("Segoe UI", 9, "bold"))
        self.session_label.place(x=112, y=8)
        self.session_bar_bg = tk.Canvas(self.root, width=170, height=8, bg=BAR_BG, highlightthickness=0)
        self.session_bar_bg.place(x=112, y=26)
        self.session_reset_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.session_reset_label.place(x=112, y=38)

        self.weekly_label = tk.Label(self.root, text="WEEK", fg=FG_COLOR, bg=BG_COLOR, font=("Segoe UI", 9, "bold"))
        self.weekly_label.place(x=112, y=54)
        self.weekly_bar_bg = tk.Canvas(self.root, width=170, height=8, bg=BAR_BG, highlightthickness=0)
        self.weekly_bar_bg.place(x=112, y=72)
        self.weekly_reset_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.weekly_reset_label.place(x=112, y=84)

        self.credits_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.credits_label.place(x=112, y=96)

        self.hint_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.hint_label.place(x=8, y=86)

    def _bind_drag(self) -> None:
        self.root.bind("<Button-1>", self._on_drag_start)
        self.root.bind("<B1-Motion>", self._on_drag_move)
        self.root.bind("<ButtonRelease-1>", self._on_drag_end)

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_offset = (event.x, event.y)

    def _on_drag_move(self, event: tk.Event) -> None:
        x = self.root.winfo_x() + event.x - self._drag_offset[0]
        y = self.root.winfo_y() + event.y - self._drag_offset[1]
        self.root.geometry(f"+{x}+{y}")

    def _on_drag_end(self, _event: tk.Event) -> None:
        self._save_position()

    def _build_context_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Refresh now", command=self._on_refresh_now)
        self.menu.add_checkbutton(label="Always in front", variable=self._always_on_top, command=self._toggle_always_on_top)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.root.destroy)
        self.root.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _on_refresh_now(self) -> None:
        if self.on_refresh_requested is not None:
            self.on_refresh_requested()

    def _toggle_always_on_top(self) -> None:
        self.root.attributes("-topmost", self._always_on_top.get())

    def _restore_position(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x, y = screen_w - CARD_WIDTH - 24, screen_h - CARD_HEIGHT - 24

        if self._position_path.is_file():
            try:
                saved = json.loads(self._position_path.read_text(encoding="utf-8"))
                x, y = clamp_position(int(saved["x"]), int(saved["y"]), CARD_WIDTH, CARD_HEIGHT, screen_w, screen_h)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

        self.root.geometry(f"{CARD_WIDTH}x{CARD_HEIGHT}+{x}+{y}")

    def _save_position(self) -> None:
        try:
            self._position_path.write_text(
                json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}), encoding="utf-8"
            )
        except OSError:
            pass
```

- [ ] **Step 3: Manually verify the window shell launches**

Run (native Windows Python, the primary target platform):
```bash
cd /mnt/c/Tools/tokitty && /mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe -c "
import sys; sys.path.insert(0, '.')
import tkinter as tk
from tokitty.paths import get_state_dir
from tokitty.ui import TokittyWindow
root = tk.Tk()
TokittyWindow(root, get_state_dir())
root.after(5000, root.destroy)
root.mainloop()
print('window shell ran without crashing')
"
```
Expected: prints `window shell ran without crashing` with no traceback (the window closes itself after 5 seconds). **This only confirms it doesn't crash — it does not confirm the window looks right, drags correctly, or stays on top of other apps.** Ask the user to run this once themselves (or wait for the full app in Task 17) and visually confirm: the card appears, is draggable, right-click shows the three-item menu, and it stays above other windows when you click into a different app.

- [ ] **Step 4: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/ui.py
git commit -m "feat: window shell (chrome, drag, always-on-top, context menu, position persistence)"
```

---

### Task 16: `ui.py` rendering + `sprites.py` wiring + `TOKITTY_DEBUG_STATE`

**Files:**
- Modify: `tokitty/tokitty/ui.py`
- Modify: `tokitty/tokitty/__main__.py`

**Interfaces:**
- Consumes: `tokitty.sprites.{PALETTE, SCALE, get_frames}` (Task 10), `tokitty.display.bar_color` (Task 11).
- Produces (added to `ui.py`): `TokittyWindow.render(self, state, session_pct, weekly_pct, session_reset_text, weekly_reset_text, driving_tag, credits_text, hint_text, dimmed) -> None`; internal `_animate()` / `_draw_frame()` driven by `root.after`.
- Produces (added to `__main__.py`): `run_gui() -> int`, honoring the `TOKITTY_DEBUG_STATE` env var to force a single state without polling.

- [ ] **Step 1: Add rendering to `ui.py`**

Add to the imports at the top of `tokitty/tokitty/ui.py`:

```python
from typing import Optional

from tokitty.display import bar_color
from tokitty.sprites import PALETTE, SCALE, get_frames
```

Add these constants near the existing ones:

```python
FRAME_INTERVAL_MS = 800
CAT_CANVAS_SIZE = 100
```

Add these methods to the `TokittyWindow` class (after `__init__`, which now also calls `self._animate()` as its last line — add that call):

```python
    def render(
        self,
        state: str,
        session_pct: float,
        weekly_pct: float,
        session_reset_text: str,
        weekly_reset_text: str,
        driving_tag: str,
        credits_text: Optional[str],
        hint_text: Optional[str],
        dimmed: bool,
    ) -> None:
        self._current_state = state
        self.tag_label.configure(text=driving_tag)

        fg = DIM_COLOR if dimmed else FG_COLOR
        self.session_label.configure(fg=fg)
        self.weekly_label.configure(fg=fg)

        self.session_bar_bg.delete("fill")
        self.session_bar_bg.create_rectangle(
            0, 0, 170 * min(session_pct, 100) / 100, 8, fill=bar_color(session_pct), width=0, tags="fill"
        )
        self.session_reset_label.configure(text=f"{session_pct:.0f}% · {session_reset_text}")

        self.weekly_bar_bg.delete("fill")
        self.weekly_bar_bg.create_rectangle(
            0, 0, 170 * min(weekly_pct, 100) / 100, 8, fill=bar_color(weekly_pct), width=0, tags="fill"
        )
        self.weekly_reset_label.configure(text=f"{weekly_pct:.0f}% · {weekly_reset_text}")

        self.credits_label.configure(text=credits_text or "")

        if hint_text:
            self.hint_label.configure(text=hint_text)
            self.hint_label.lift()
        else:
            self.hint_label.configure(text="")

    def _animate(self) -> None:
        frames = get_frames(self._current_state)
        frame = frames[self._frame_index % len(frames)]
        self._draw_frame(frame)
        self._frame_index += 1
        self.root.after(FRAME_INTERVAL_MS, self._animate)

    def _draw_frame(self, frame) -> None:
        self.canvas.delete("cat")
        for row_index, row in enumerate(frame):
            for col_index, ch in enumerate(row):
                color = PALETTE.get(ch, "")
                if not color:
                    continue
                x0, y0 = col_index * SCALE, row_index * SCALE
                self.canvas.create_rectangle(x0, y0, x0 + SCALE, y0 + SCALE, fill=color, width=0, tags="cat")
```

In `__init__`, initialize the two new instance attributes the animation loop needs, and start it. Update `__init__` to add these lines right after `self.on_refresh_requested = None` and add the call at the end:

```python
        self._current_state = "sleeping"
        self._frame_index = 0
```

and as the final line of `__init__` (after `self._build_context_menu()`):

```python
        self._animate()
```

- [ ] **Step 2: Manually verify sprite rendering with the forced-state harness**

Run (native Windows Python), cycling through a few states by editing the env var between runs:
```bash
cd /mnt/c/Tools/tokitty && TOKITTY_DEBUG_STATE=panicked /mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe -c "
import sys, os; sys.path.insert(0, '.')
import tkinter as tk
from tokitty.paths import get_state_dir
from tokitty.ui import TokittyWindow
root = tk.Tk()
w = TokittyWindow(root, get_state_dir())
w.render(state=os.environ['TOKITTY_DEBUG_STATE'], session_pct=92, weekly_pct=40, session_reset_text='11:40 PM', weekly_reset_text='Mon Jul 6', driving_tag='5h', credits_text='\$3.62 / \$20.00', hint_text=None, dimmed=False)
root.after(4000, root.destroy)
root.mainloop()
print('rendered', os.environ['TOKITTY_DEBUG_STATE'])
"
```
Expected: prints `rendered panicked` with no traceback. **This confirms the render path executes without error — it does not confirm the pixel art looks right.** This step should be repeated for at least `sleeping`, `panicked`, `flopped`, and `confused` and the result shown to the user, since only a human can judge whether the pixel cat is visually recognizable and the layout looks clean — do not claim the visuals are "good" without the user having actually looked.

- [ ] **Step 3: Wire `TOKITTY_DEBUG_STATE` and `run_gui()` into `__main__.py`**

Add these imports to the top of `tokitty/tokitty/__main__.py`:

```python
import os
import tkinter as tk

from tokitty.credentials import CredentialsError  # already imported above if not present
from tokitty.lock import LockAcquisitionError, SingleInstanceLock
from tokitty.mood import compute_capped_substate, compute_mood, detect_activate, select_binding_capped_limit
from tokitty.paths import get_state_dir
from tokitty.poller import Poller
from tokitty.ui import TokittyWindow
from tokitty.display import format_countdown, format_reset_day, format_reset_time
```

Add these functions and replace the placeholder branch in `main()`:

```python
DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"
UI_REFRESH_MS = 500


def _display_state_for(result: PollResult, previous: Optional[PollResult]) -> dict:
    """Translate a PollResult into what the UI should show."""
    if result.status != "ok" or result.snapshot is None:
        hints = {
            "stale_token": "token stale — open Claude Code",
            "credentials_unreachable": "can't find credentials",
            "ambiguous_credentials": "multiple installs — set TOKITTY_CREDENTIALS",
            "api_error": "API hiccup, retrying",
        }
        last_good = previous.snapshot if previous and previous.snapshot else None
        return {
            "state": "confused",
            "session_pct": last_good.session_pct if last_good else 0.0,
            "weekly_pct": last_good.weekly_pct if last_good else 0.0,
            "session_reset_text": "—",
            "weekly_reset_text": "—",
            "driving_tag": "",
            "credits_text": None,
            "hint_text": hints.get(result.status, "unknown error"),
            "dimmed": True,
        }

    snapshot = result.snapshot
    now = datetime.now(timezone.utc)
    binding = select_binding_capped_limit(snapshot.limits)

    if binding is not None:
        capped = compute_capped_substate(binding, now=now)
        countdown = format_countdown(capped.time_to_reset.total_seconds())
        if binding.kind == "session":
            session_text = countdown
            weekly_text = format_reset_day(snapshot.weekly_resets_at) if snapshot.weekly_resets_at else "—"
        else:
            session_text = format_reset_time(snapshot.session_resets_at) if snapshot.session_resets_at else "—"
            weekly_text = countdown
        state = capped.substate
        driving_tag = capped.driving_tag
    else:
        mood, driving_tag = compute_mood(snapshot.session_pct, snapshot.weekly_pct)
        state = mood
        session_text = format_reset_time(snapshot.session_resets_at) if snapshot.session_resets_at else "—"
        weekly_text = format_reset_day(snapshot.weekly_resets_at) if snapshot.weekly_resets_at else "—"

    if previous and previous.snapshot and detect_activate(previous.snapshot, snapshot):
        state = "activate"

    credits_text = None
    if snapshot.credits_used is not None and snapshot.credits_used > 0:
        credits_text = f"${snapshot.credits_used:.2f} / ${snapshot.credits_limit:.2f}"

    return {
        "state": state,
        "session_pct": snapshot.session_pct,
        "weekly_pct": snapshot.weekly_pct,
        "session_reset_text": session_text,
        "weekly_reset_text": weekly_text,
        "driving_tag": driving_tag,
        "credits_text": credits_text,
        "hint_text": None,
        "dimmed": False,
    }


def run_gui() -> int:
    state_dir = get_state_dir()
    lock = SingleInstanceLock(state_dir)
    try:
        lock.acquire()
    except LockAcquisitionError:
        print("Tokitty is already running.", file=sys.stderr)
        return 1

    root = tk.Tk()
    window = TokittyWindow(root, state_dir)

    debug_state = os.environ.get(DEBUG_STATE_ENV)
    if debug_state:
        window.render(
            state=debug_state, session_pct=0.0, weekly_pct=0.0,
            session_reset_text="—", weekly_reset_text="—",
            driving_tag="debug", credits_text=None, hint_text=None, dimmed=False,
        )
        root.mainloop()
        lock.release()
        return 0

    poller = Poller(fetch_fn=build_fetch_fn())
    window.on_refresh_requested = poller.request_refresh
    previous_holder = {"result": None}

    def tick():
        latest = poller.get_latest()
        if latest is not None:
            display = _display_state_for(latest, previous_holder["result"])
            window.render(**display)
            previous_holder["result"] = latest
        root.after(UI_REFRESH_MS, tick)

    poller.start()
    root.after(UI_REFRESH_MS, tick)

    try:
        root.mainloop()
    finally:
        poller.stop()
        lock.release()

    return 0
```

Replace the body of `main()`:

```python
def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--debug-print" in argv:
        return debug_print()
    return run_gui()
```

- [ ] **Step 4: Run the automated test suite to confirm nothing broke**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/ -v`
Expected: all tests from Tasks 2-13 still pass (this task adds no new automated tests — `ui.py` and the GUI branch of `__main__.py` are GUI code, verified manually per the spec's Testing section).

- [ ] **Step 5: Manually verify the full app against the live account**

Run (native Windows Python — the primary path):
```bash
cd /mnt/c/Tools/tokitty && /mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe -m tokitty
```
Expected: the window appears showing real session/weekly percentages matching Claude Code's `/usage`, right-click menu works, dragging works, closing via the menu's Exit works cleanly. **Show this to the user and ask them to confirm the window looks right, floats correctly, and behaves as expected — this is exactly the kind of thing that needs a human's eyes, not an automated check.**

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add tokitty/ui.py tokitty/__main__.py
git commit -m "feat: wire polling, mood/capped-state, and rendering into the live app"
```

---

### Task 17: Finalize README, verify WSLg secondary path, close out

**Files:**
- Modify: `tokitty/README.md`

**Interfaces:**
- None — this is the documentation/verification finish line.

- [ ] **Step 1: Write the full README**

```markdown
# Tokitty

A cat-themed desktop widget that shows your live Claude Code usage — session %, weekly %, reset countdowns, and extra-usage credits — with a pixel cat whose mood reflects how close you are to the limit. When a limit is capped, the cat rests, then stirs, then wakes up as the reset approaches, then hops back to sleep once usage clears.

**Not affiliated with Anthropic.** "Claude" and "Claude Code" are Anthropic's marks, used here only to describe compatibility.

## Security & privacy

Tokitty only *reads* your local Claude Code OAuth credentials file — it never writes to it, never touches the refresh token, and never transmits the access token anywhere except in a single request to `api.anthropic.com`. Window position is the only thing Tokitty persists, and it's stored in your OS's normal per-user config directory, never inside this repo.

## Platforms tested

- Windows 11, native Python 3.13 (`pythonw.exe`), with Claude Code running inside WSL2 — the primary, recommended setup.
- *(Update this table as other platforms are verified — don't claim untested support.)*

## Setup

### Windows (Claude Code in WSL2 — recommended path)

1. Install Python 3.10+ from [python.org](https://www.python.org/) (bundles tkinter).
2. `git clone` this repo, then from the repo root: `pythonw.exe -m tokitty`

### Windows (Claude Code installed natively, no WSL)

Same as above — `resolve_credentials_source()` finds `~/.claude/.credentials.json` directly, no WSL bridge involved.

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

Tokitty was built with [Claude](https://claude.com/product/claude-code) (Fable 5) using a subagent-driven-development workflow: an owner session designed the spec and implementation plan, then dispatched a fresh implementer subagent per task with a reviewer subagent checking spec compliance and code quality before each task landed — deliberately tiered to cheaper/faster models for the mechanical, fully-specified logic modules (credentials, API client, locking, mood/wake-sequence state machine, formatting), with a standard-tier model for the threading/integration work. The pixel-art sprites, the tkinter window, and the animation loop were built directly by the owner session rather than delegated, since that's the part where a bit of craft mattered most. The review loop caught and fixed several real bugs along the way (a monkeypatch self-recursion bug in a test, a refresh-request race condition in the polling worker, a `wsl.exe` argv-mangling quirk found during live verification) — the commit history is the actual record of that process, not just the finished result.

## Known limitations (POC)

- This uses `api.anthropic.com/api/oauth/usage`, an **undocumented endpoint** that may change or disappear without notice.
- Running Tokitty *inside* WSL (via WSLg) also works, but always-on-top behavior over native Windows apps and pixel-art crispness at non-100% display scaling haven't been verified as reliable — native `pythonw.exe` is the tested, recommended path on Windows.
- Sprite art is a simple first pass (roadmap: PNG sprite upgrade).

## Roadmap

See `docs/superpowers/specs/2026-07-02-tokitty-design.md` for the full stack-ranked enhancement list (ntfy notifications, autostart, tray icon, per-model bars, click-to-pet, burn-rate projection, color customization, and more).

## License

MIT — see [LICENSE](LICENSE).
```

- [ ] **Step 2: Attempt the WSLg secondary path and record the result (don't claim it works without checking)**

Run:
```bash
cd /mnt/c/Tools/tokitty && python3 -m tokitty --debug-print
```
Expected: `status: ok` (this exercises the WSL-native, non-GUI half of the secondary path).

Then, if the user is available to look at their screen:
```bash
cd /mnt/c/Tools/tokitty && python3 -m tokitty
```
Ask the user to check: does the window appear on the Windows desktop via WSLg, does it stay on top of native Windows apps, and does the pixel art look crisp or blurry. Update the README's "Known limitations" section with whatever is actually observed — do not leave the hedged language in place if it's been empirically confirmed one way or the other.

- [ ] **Step 3: Run the full test suite one final time**

Run: `cd /mnt/c/Tools/tokitty && python3 -m pytest tests/ -v`
Expected: all tests across all modules pass (paths, credentials, wsl_probe, api, lock, mood, sprites, display, geometry, poller — roughly 77 tests total).

- [ ] **Step 4: Commit**

```bash
cd /mnt/c/Tools/tokitty
git add README.md
git commit -m "docs: finalize README with setup instructions, security statement, and known limitations"
```

---

## Self-Review Notes

**Spec coverage:** credential resolution order incl. override/ambiguity (Tasks 3-4) — covered; token expiry/401 handling (Tasks 3, 6, 14) — covered; defensive response parsing (Task 5) — covered; single-instance lock (Task 7) — covered; steady mood ladder (Task 8) — covered; capped/wake/activate sequence incl. adaptive poll interval (Tasks 9, 13) — covered; sprites/palette (Task 10) — covered; countdown/local-time/portable formatting (Task 11) — covered; window clamp/DPI/drag/topmost/menu (Tasks 12, 15) — covered; error-state hints with dimmed last-good display (Task 16) — covered; public-repo requirements (license, security statement, platform-honesty, `.gitignore`) — Tasks 1 and 17; per-task commits — every task ends with one. Not included in this plan (correctly, per spec's "Out of scope for POC"): ntfy notifications, autostart, tray icon, per-model bars, click-to-pet/purr, burn-rate projection, PNG sprite upgrade, PySide6 rewrite, color customization, CI matrix — all remain roadmap items for future plans.

**Placeholder scan:** no TBD/TODO markers; every step has complete, runnable code or an exact command with an expected result.

**Type consistency:** `PollResult.status` values (`"ok"`, `"stale_token"`, `"credentials_unreachable"`, `"ambiguous_credentials"`, `"api_error"`) are used identically in `__main__.py`'s `build_fetch_fn()`/`_display_state_for()` and match the spec's Error handling table. `CredentialsSource`/`LocalCredentialsSource`/`WslDistroCredentialsSource` are defined once in Task 3 and consumed consistently in Tasks 4 and 14. `UsageSnapshot`/`LimitInfo` field names introduced in Task 5 are used identically in Tasks 9, 13, and 16. `get_frames(state)` state-name strings match exactly between `sprites.py` (Task 10), `mood.py`'s substate/mood outputs (Tasks 8-9), and the `"confused"`/`"activate"` literals used directly in `__main__.py` (Task 16).
