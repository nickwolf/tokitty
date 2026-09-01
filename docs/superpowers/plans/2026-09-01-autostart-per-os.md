# Autostart Per OS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tokitty comes back on its own after a reboot, with no installer and no admin rights, controlled by a checkbox in the existing right-click and tray menu, on Windows, macOS and Linux.
**Architecture:** A new `tokitty/autostart.py` owns the whole seam: a pure `resolve_launch_command()` decides what argv the OS should run, a generated per-user launcher file (`autostart_launcher.pyw`) solves the `-m tokitty` working-directory problem that makes the obvious registration silently fail, three OS backends (Windows registry / macOS LaunchAgent / Linux desktop entry) share one `register`/`deregister`/`is_registered`/`is_current` interface behind injectable roots so every one of them is testable on every CI platform, and a startup drift check (`ensure_current`) keeps a stale entry from failing silently at login. Wired into the existing menu model (`menu.py`, `ui.py`) following the established `tray_enabled`/`on_toggle_tray` optional-pair convention, with a plain-Python cached bool as the only thing the menu getter ever reads.
**Tech Stack:** Python 3.10+, `winreg` (Windows, lazily imported), `plistlib` (macOS), plain text (Linux), `subprocess.list2cmdline` for Windows quoting, pytest.
**Spec:** docs/superpowers/specs/2026-09-01-autostart-per-os-design.md

## Global Constraints

Binding on every task below:

- Python 3.10 compatible. Backslashes inside f-string expressions are a SyntaxError before 3.12, and the CI matrix runs 3.10 and 3.14. This bites harder than usual here: every OS backend manipulates Windows-shaped paths that are full of backslashes. Never put one inside an f-string expression; build such strings outside the f-string, or use `repr()` (see Task 2).
- CI matrix is `ubuntu-latest`, `macos-latest`, `windows-latest` x Python `3.10` and `3.14` (`.github/workflows/ci.yml:22-24`). Every new test in every task must be genuinely OS-independent (fakes/injected roots), not just "passes on the machine that wrote it."
- The suite is headless by default: `pyproject.toml:27` sets `addopts = "-m 'not gui'"`. Any new test that constructs a real `tk.Tk()` MUST be marked `@pytest.mark.gui` or it reddens the headless matrix. gui tests run via `xvfb-run -a pytest -m gui`.
- Baseline is 513 collected tests (headless) plus 31 `gui`-marked, 544 total (`python3 -m pytest --collect-only -q -o addopts=""`). Every task must leave the suite green.
- **The autostart checkbox state is never persisted to `settings.json`. `settings.py` gains no new field, in any task in this plan.** The OS registration (registry value / LaunchAgent plist / desktop entry) is the only source of truth. A bool in `settings.json` would drift the moment anything outside tokitty touches that registration, and the checkbox would then confidently show "on" for a mechanism that no longer runs. As a standing check: `grep -n autostart tokitty/settings.py` must return nothing at every task boundary.
- **Menu checkbox getters never do registry or filesystem I/O.** `menu.py`'s module docstring is explicit about why: "The getter fields (checkbox, radio_selected) are evaluated by pystray on its OWN thread when it draws the tray menu. They MUST therefore read only plain-Python shadow state -- never a tkinter Var or widget." Every getter this plan wires (`window.autostart_enabled`) is a closure over a plain dict/bool, read once at startup and refreshed only after a toggle -- never a live read of the OS state at draw time.
- **The Windows entry is never `pythonw.exe -m tokitty`.** Verified broken on real Windows Python 3.13: `cd C:\Users && python.exe -c "import tokitty"` fails with `ModuleNotFoundError`; `cd C:\Tools\tokitty && python.exe -c "import tokitty"` works. A bare `HKCU\...\Run` value has no working-directory field, so `-m tokitty` registered directly fails silently at every login with nothing on screen. Use the generated `autostart_launcher.pyw` from Task 2 instead.
- **The launcher imports, it does not execute.** `from tokitty.__main__ import main` followed by `main()` -- never running `tokitty/__main__.py` as a script, which would put `<repo>/tokitty` on `sys.path` instead of `<repo>`, leaving `import tokitty` still failing.
- **Interpreter paths contain spaces** (`C:\Program Files\...`). A quoting test using a space-containing fake interpreter path is a required acceptance criterion in Task 1 and Task 3, not optional.
- **Every platform backend needs tests that pass on all three CI platforms via injected roots/fakes, not just the one the code describes.** Explicit lesson carried forward from this repo's own accounts-setup-ui branch: every test there was validated only under WSL/Linux, and the first real Windows CI run failed four tests, all genuine platform bugs invisible from WSL. This feature is exactly that risk category -- platform-branching logic over paths and separators. Before pushing any task in this plan, additionally verify by hand against real Windows Python at `C:\Users\nickw\AppData\Local\Programs\Python\Python313\python.exe` (from WSL: `/mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe`), using `--basetemp="C:\tmp\<task-name>"` to route around the locked default pytest temp root.
- Follow the project's tmp-file-plus-`os.replace` write pattern for every new state file on disk: `customize.py:103-108` (`save_customization`), `settings.py:42-46` (`save_settings`), `hooks_install.py:161-164` (`_write_settings`) are the existing examples. Write to a `.tmp` sibling, then `os.replace`, never `open(path, "w")` directly on the target.
- Out of scope, explicitly, per the design: code signing; packaging/frozen binaries (issue #48) -- `resolve_launch_command`'s frozen branch is a stub for that future work, not built out here; package managers (winget, Homebrew); and the `resolve_activity_sessions` WSL double-scan on a no-`accounts.json` Windows+WSL2 install -- owner decision 2026-09-01 was to accept it for this issue and file it separately, not fix it as part of autostart.

## User decisions (already made)

- Menu toggle is the primary surface, not CLI flags only. It mirrors "Show tray icon." (CLI flags still ship, Task 7, as a secondary/headless-scripting path, not the primary UX.)
- No installer, no elevation, no new dependencies -- every mechanism is a user-scope file or registry write using the stdlib (`winreg`, `plistlib`, plain text).
- Unsigned, repo-clone deployment is the target; frozen binaries (#48) are a future consumer of the same `resolve_launch_command()` seam, not a prerequisite for this work.
- Windows uses the `Run` key, not a `shell:startup` `.lnk`: a shortcut needs COM (pywin32), and the dependency list is `pystray` + `Pillow` only (`pyproject.toml:8`). A `.cmd` in Startup avoids COM but flashes a console window every login, worse than either alternative.
- Owner decision, 2026-09-01: the `resolve_activity_sessions` WSL double-scan on a no-`accounts.json` Windows+WSL2 install is accepted for this issue and filed as its own issue separately. No task below touches it.
- Open question 2 from the design (ship CLI flags alongside the toggle?) resolved: yes -- `--install-autostart` / `--uninstall-autostart` (Task 7), mirroring `--install-hooks` / `--uninstall-hooks`.
- Open question 3 from the design (Linux `X-GNOME-Autostart-Delay`?) resolved for this plan: not added. Poll-retry tolerance (Task 6) is the boot-race mitigation on every platform already; a Linux-only startup delay would be one more platform-specific knob for a problem already solved generically. Revisit only if hands-on Linux use surfaces a real race poll-retry doesn't cover.

---

### Task 1: `tokitty/autostart.py` -- pure `resolve_launch_command()`

**Goal:** Give the whole feature one pure, fully-injectable function that decides what argv the OS should run at login: the frozen-executable path alone (a stub for #48), or `[interpreter, launcher_path]` for a repo clone -- with the Windows branch swapping `python.exe` for `pythonw.exe` (no console window) using `pathlib.PureWindowsPath`, not a bare `Path`, so the swap is testable on every CI OS, not just win32.

**Files:**
- Create: `tokitty/autostart.py`
- Test: `tests/test_autostart.py`

**Acceptance Criteria:**
- [ ] `resolve_launch_command(state_dir, frozen=True, executable=<path>)` returns `[<path>]` -- the executable alone, regardless of platform.
- [ ] `resolve_launch_command(state_dir, frozen=False, executable=<python.exe path>, platform="win32", repo_root=<path>)` returns `[<pythonw.exe path>, "<state_dir>/autostart_launcher.pyw"]`.
- [ ] `resolve_launch_command(state_dir, frozen=False, executable="/usr/bin/python3", platform="linux", repo_root=<path>)` returns `["/usr/bin/python3", "<state_dir>/autostart_launcher.pyw"]` (no pythonw-style swap off Windows).
- [ ] Same shape for `platform="darwin"`.
- [ ] `_windows_pythonw_path` swaps `.../python.exe` -> `.../pythonw.exe`, case-insensitively on the filename.
- [ ] `_windows_pythonw_path` leaves an already-`pythonw.exe` path unchanged, and passes through unrecognized shapes (e.g. a frozen `tokitty.exe`) unchanged rather than raising.
- [ ] `_windows_pythonw_path` correctly handles a space-containing directory (`C:\Program Files (x86)\Python313\python.exe`) -- **required acceptance criterion**, not optional, and runs correctly on Linux/macOS CI because it uses `PureWindowsPath`, never a bare `Path`.
- [ ] With no override, `resolve_launch_command` defaults `frozen` from `getattr(sys, "frozen", False)`, `executable` from `sys.executable`, `platform` from `sys.platform`, and `repo_root` from `Path(__file__).resolve().parent.parent` (the real repo root) -- confirmed by asserting `<repo_root>/tokitty/__init__.py` exists.

**Verify:**
```
python3 -m pytest tests/test_autostart.py -v
```
Expected: 9 new tests pass, including the space-containing-directory case and the frozen/win32/linux/darwin branch assertions.

**Steps:**
- [ ] Step 1: Write the failing tests. Create `tests/test_autostart.py`:
```python
from pathlib import Path

from tokitty.autostart import LAUNCHER_FILENAME, _windows_pythonw_path, resolve_launch_command


def test_frozen_returns_executable_alone(tmp_path):
    result = resolve_launch_command(tmp_path, frozen=True, executable=r"C:\Program Files\Tokitty\tokitty.exe")
    assert result == [r"C:\Program Files\Tokitty\tokitty.exe"]


def test_repo_clone_windows_uses_pythonw_and_launcher_path(tmp_path):
    result = resolve_launch_command(
        tmp_path, frozen=False, executable=r"C:\Program Files\Python313\python.exe",
        platform="win32", repo_root=Path(r"C:\Tools\tokitty"),
    )
    assert result == [r"C:\Program Files\Python313\pythonw.exe", str(tmp_path / LAUNCHER_FILENAME)]


def test_repo_clone_linux_uses_executable_directly(tmp_path):
    result = resolve_launch_command(
        tmp_path, frozen=False, executable="/usr/bin/python3", platform="linux",
        repo_root=Path("/home/nick/tokitty"),
    )
    assert result == ["/usr/bin/python3", str(tmp_path / LAUNCHER_FILENAME)]


def test_repo_clone_macos_uses_executable_directly(tmp_path):
    result = resolve_launch_command(
        tmp_path, frozen=False, executable="/usr/bin/python3", platform="darwin",
        repo_root=Path("/Users/nick/tokitty"),
    )
    assert result == ["/usr/bin/python3", str(tmp_path / LAUNCHER_FILENAME)]


def test_windows_pythonw_path_swaps_python_exe_for_pythonw_exe():
    assert _windows_pythonw_path(r"C:\Program Files\Python313\python.exe") == r"C:\Program Files\Python313\pythonw.exe"


def test_windows_pythonw_path_leaves_pythonw_exe_unchanged():
    result = _windows_pythonw_path(r"C:\Program Files\Python313\pythonw.exe")
    assert result == r"C:\Program Files\Python313\pythonw.exe"


def test_windows_pythonw_path_passes_through_unrecognized_shape():
    assert _windows_pythonw_path(r"C:\Tools\venv\Scripts\tokitty.exe") == r"C:\Tools\venv\Scripts\tokitty.exe"


def test_windows_pythonw_path_handles_space_containing_directory():
    # Required acceptance criterion: real interpreter installs commonly
    # live under "C:\Program Files (x86)\...". PureWindowsPath makes this
    # testable on Linux/macOS CI, not just on real Windows.
    result = _windows_pythonw_path(r"C:\Program Files (x86)\Python313\python.exe")
    assert result == r"C:\Program Files (x86)\Python313\pythonw.exe"


def test_default_repo_root_is_the_real_repository_root():
    from tokitty.autostart import _default_repo_root

    assert (_default_repo_root() / "tokitty" / "__init__.py").is_file()
```
- [ ] Step 2: Run and see it fail. `ModuleNotFoundError: No module named 'tokitty.autostart'`.
- [ ] Step 3: Implement. Create `tokitty/autostart.py`:
```python
"""Cross-platform "launch tokitty at login" seam: no installer, no
elevation, stdlib only. See docs/superpowers/specs/
2026-09-01-autostart-per-os-design.md.
"""
from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath
from typing import List, Optional

LAUNCHER_FILENAME = "autostart_launcher.pyw"


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_launch_command(
    state_dir: Path,
    *,
    repo_root: Optional[Path] = None,
    frozen: Optional[bool] = None,
    executable: Optional[str] = None,
    platform: Optional[str] = None,
) -> List[str]:
    """The argv the OS should run to relaunch tokitty at login. Pure:
    every platform-dependent input is an optional override, so this is
    fully testable on any CI OS, never just the one it describes.

    A frozen build (#48) is simplest: the executable alone, no launcher
    needed. A repo clone cannot register "-m tokitty" directly -- it
    isn't pip-installed, so it only imports because the process starts
    with the repo root as cwd, and a Run-key/.desktop/.plist entry has no
    working-directory field (verified on real Windows Python: `cd
    C:\\Users && python.exe -c "import tokitty"` fails,
    `cd C:\\Tools\\tokitty && python.exe -c "import tokitty"` works). So a
    repo clone instead points at the generated launcher file in
    state_dir (see write_launcher_file, Task 2), which pins the repo
    root itself.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    platform = sys.platform if platform is None else platform

    if frozen:
        return [executable]

    launcher = str(Path(state_dir) / LAUNCHER_FILENAME)
    if platform == "win32":
        return [_windows_pythonw_path(executable), launcher]
    return [executable, launcher]


def _windows_pythonw_path(python_executable: str) -> str:
    """Swap .../python.exe for .../pythonw.exe (no console window at
    login); pass through unchanged if the name doesn't match that exact
    shape (e.g. a frozen tokitty.exe, or an already-pythonw.exe path).

    Uses PureWindowsPath rather than pathlib.Path deliberately: a bare
    Path on POSIX has no concept of backslash separators or drive
    letters and would silently treat 'C:\\Program Files\\...\\python.exe'
    as one opaque filename -- exactly the class of bug this repo's own
    accounts-setup-ui branch shipped four of, by validating only under
    WSL/Linux. PureWindowsPath parses Windows paths correctly regardless
    of host OS, so this is testable on every CI platform, not just
    win32."""
    path = PureWindowsPath(python_executable)
    if path.name.lower() == "pythonw.exe":
        return python_executable
    if path.name.lower() == "python.exe":
        return str(path.with_name("pythonw.exe"))
    return python_executable
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_autostart.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/autostart.py tests/test_autostart.py
git commit -m "autostart: pure resolve_launch_command() seam"
```

---

### Task 2: Launcher-file generation

**Goal:** Generate the `autostart_launcher.pyw` file `resolve_launch_command()` points a repo clone at: a small script that pins the repo root onto `sys.path` and imports `tokitty.__main__.main` directly, written atomically into the per-user state dir.

**Files:**
- Modify: `tokitty/autostart.py` (add `launcher_content`, `write_launcher_file`, `import os`)
- Test: `tests/test_autostart.py`

**Acceptance Criteria:**
- [ ] `launcher_content(repo_root)` returns valid Python 3.10 source (`ast.parse` succeeds).
- [ ] The content contains `from tokitty.__main__ import main` and ends with a `main()` call -- never `exec`ing or running `tokitty/__main__.py` as a script.
- [ ] `repo_root` is embedded via `repr(str(repo_root))`, never hand-built inside an f-string expression -- verified for a path containing a space and for a `PureWindowsPath` containing backslashes; both must still parse as valid Python and contain the exact `repr()` output as a substring.
- [ ] `write_launcher_file(state_dir, repo_root)` writes to `<state_dir>/autostart_launcher.pyw` and returns that path.
- [ ] `write_launcher_file` uses a `.tmp` sibling plus `os.replace`, never `open(path, "w")` directly on the target (spied via `monkeypatch.setattr("tokitty.autostart.os.replace", ...)`).
- [ ] Calling `write_launcher_file` twice with the same inputs is idempotent (byte-identical output).
- [ ] `write_launcher_file(state_dir)` with no `repo_root` argument defaults to the real repo root (mirrors `resolve_launch_command`'s default).

**Verify:**
```
python3 -m pytest tests/test_autostart.py -v
```
Expected: all Task 1 tests still pass, plus 7 new tests for `launcher_content`/`write_launcher_file`.

**Steps:**
- [ ] Step 1: Write the failing tests. Add to `tests/test_autostart.py`:
```python
import ast
import os
from pathlib import PureWindowsPath

from tokitty.autostart import launcher_content, write_launcher_file


def test_launcher_content_is_valid_python():
    ast.parse(launcher_content(Path("/home/nick/tokitty")))


def test_launcher_content_imports_main_from_dunder_main():
    content = launcher_content(Path("/home/nick/tokitty"))
    assert "from tokitty.__main__ import main" in content
    assert content.strip().endswith("main()")


def test_launcher_content_embeds_repo_root_with_a_space(tmp_path):
    repo_root = tmp_path / "My Tools" / "tokitty"
    content = launcher_content(repo_root)
    ast.parse(content)
    assert repr(str(repo_root)) in content


def test_launcher_content_handles_windows_style_backslashes():
    repo_root = PureWindowsPath(r"C:\Program Files\tokitty")
    content = launcher_content(repo_root)
    ast.parse(content)
    assert repr(str(repo_root)) in content


def test_write_launcher_file_creates_file_at_state_dir(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = write_launcher_file(state_dir, tmp_path / "repo")
    assert path == state_dir / "autostart_launcher.pyw"
    assert path.is_file()


def test_write_launcher_file_uses_tmp_file_and_replace(tmp_path, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("tokitty.autostart.os.replace", spy_replace)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    write_launcher_file(state_dir, tmp_path / "repo")
    assert len(calls) == 1
    assert calls[0][0].endswith("autostart_launcher.pyw.tmp")
    assert calls[0][1].endswith("autostart_launcher.pyw")


def test_write_launcher_file_is_idempotent(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = write_launcher_file(state_dir, tmp_path / "repo")
    first = path.read_text(encoding="utf-8")
    write_launcher_file(state_dir, tmp_path / "repo")
    assert path.read_text(encoding="utf-8") == first


def test_write_launcher_file_defaults_repo_root(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = write_launcher_file(state_dir)
    assert "tokitty" in path.read_text(encoding="utf-8")
```
(`from pathlib import Path` is already imported at the top of the test file from Task 1.)
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'launcher_content'`.
- [ ] Step 3: Implement. Add to `tokitty/autostart.py` (add `import os` alongside the existing `import sys`):
```python
def launcher_content(repo_root) -> str:
    """Source of the generated launcher: pins repo_root onto sys.path and
    imports tokitty.__main__.main directly, rather than running
    tokitty/__main__.py as a script -- running the file directly would
    put <repo>/tokitty on sys.path instead of <repo>, so "import tokitty"
    would still fail. repr() (not manual string-building) embeds
    repo_root safely: it produces a valid, correctly escaped Python
    string literal for any path on any OS, including one with backslashes
    or spaces, without ever needing a backslash inside an f-string
    expression -- a SyntaxError before Python 3.12, and this repo's
    floor is 3.10."""
    repo_root_literal = repr(str(repo_root))
    return (
        "import sys\n"
        f"sys.path.insert(0, {repo_root_literal})\n"
        "from tokitty.__main__ import main\n"
        "main()\n"
    )


def write_launcher_file(state_dir: Path, repo_root: Optional[Path] = None) -> Path:
    repo_root = _default_repo_root() if repo_root is None else repo_root
    path = Path(state_dir) / LAUNCHER_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(launcher_content(repo_root), encoding="utf-8")
    os.replace(tmp_path, path)
    return path
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_autostart.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/autostart.py tests/test_autostart.py
git commit -m "autostart: generate the repo-clone launcher file atomically"
```

---

### Task 3: Per-OS backends behind one interface

**Goal:** Windows registry, macOS LaunchAgent and Linux desktop-entry backends, each exposing the same four methods (`register`, `deregister`, `is_registered`, `is_current`), each with an injectable root so every one of them runs under `pytest` on all three CI platforms without touching a real registry, `~/Library`, or `~/.config`. `is_current(command)` never parses a stored serialized command back into a list (a genuinely hard, error-prone problem, especially for Windows command-line quoting) -- it always re-serializes the *candidate* command the same way the backend would register it, and compares the two serialized forms. This sidesteps writing a command-line parser entirely.

**Files:**
- Modify: `tokitty/autostart.py` (add `WindowsRegistryBackend`, `_RealWindowsRegistry`, `MacLaunchAgentBackend`, `LinuxDesktopEntryBackend`, `get_backend`; add `import subprocess`, `import plistlib`)
- Test: `tests/test_autostart.py`

**Acceptance Criteria:**
- [ ] `WindowsRegistryBackend(registry=<fake>)` never imports the real `winreg` module when a fake registry accessor is injected -- verified by running the whole Windows-backend test class on Linux/macOS CI, which have no `winreg` at all, and it still passes.
- [ ] `WindowsRegistryBackend.register(command)` writes `subprocess.list2cmdline(command)` -- confirmed with a space-containing interpreter path, producing a correctly double-quoted registry string (**required acceptance criterion**, per Global Constraints).
- [ ] `WindowsRegistryBackend.is_registered()` is `False` before any `register()` call, `True` after.
- [ ] `WindowsRegistryBackend.deregister()` clears the value; `is_registered()` returns to `False`.
- [ ] `WindowsRegistryBackend.is_current(command)` is `True` immediately after `register(command)` with that same command, `False` if the interpreter path changed, and `False` when nothing is registered yet.
- [ ] The real `_RealWindowsRegistry` accessor imports `winreg` lazily inside each method, never at module scope -- confirmed by `import tokitty.autostart` succeeding on a platform with no `winreg` (i.e. every non-Windows CI runner, implicitly, since the whole test suite already runs there).
- [ ] `MacLaunchAgentBackend(launch_agents_dir=<tmp_path>)` writes `<tmp_path>/com.nickwolf.tokitty.plist` with `Label`, `ProgramArguments` (the exact command list) and `RunAtLoad: True`.
- [ ] `MacLaunchAgentBackend.deregister()` on an absent plist does not raise.
- [ ] `MacLaunchAgentBackend.is_current(command)` compares `ProgramArguments` as a list, no serialization step needed (plist arrays are already structured).
- [ ] `LinuxDesktopEntryBackend(autostart_dir=<tmp_path>)` writes `<tmp_path>/tokitty.desktop` with `Type=Application`, `Exec=<rendered command>`, and `X-GNOME-Autostart-enabled=true`.
- [ ] `LinuxDesktopEntryBackend` quotes a space-containing path in the `Exec=` line with double quotes (Desktop Entry Spec quoting, not POSIX `shlex.quote`'s single quotes) and leaves a space-free path unquoted.
- [ ] `LinuxDesktopEntryBackend.is_current(command)` re-renders the candidate command with the same quoting function used at registration and compares the resulting `Exec=` line string -- no unquoting/parsing.
- [ ] `get_backend(platform="win32")` / `"darwin"` / `"linux"` return the matching backend type; an unrecognized platform string returns `None`.

**Verify:**
```
python3 -m pytest tests/test_autostart.py -v
```
Expected: all Task 1/2 tests still pass, plus 21 new tests (7 Windows, 5 macOS, 5 Linux, 4 `get_backend`), all green on every CI platform.

**Steps:**
- [ ] Step 1: Write the failing tests. Add to `tests/test_autostart.py`:
```python
import plistlib

from tokitty.autostart import (
    LinuxDesktopEntryBackend,
    MacLaunchAgentBackend,
    WindowsRegistryBackend,
    get_backend,
)


class _FakeRegistry:
    def __init__(self):
        self.value = None

    def read_value(self):
        return self.value

    def write_value(self, value):
        self.value = value

    def delete_value(self):
        self.value = None


def test_windows_backend_register_writes_quoted_value():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register([r"C:\Program Files\Python313\pythonw.exe", r"C:\Users\nick\AppData\Local\Tokitty\autostart_launcher.pyw"])
    assert backend._registry.value == (
        '"C:\\Program Files\\Python313\\pythonw.exe" '
        '"C:\\Users\\nick\\AppData\\Local\\Tokitty\\autostart_launcher.pyw"'
    )


def test_windows_backend_is_registered_false_initially():
    assert WindowsRegistryBackend(registry=_FakeRegistry()).is_registered() is False


def test_windows_backend_register_then_is_registered_true():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register(["a", "b"])
    assert backend.is_registered() is True


def test_windows_backend_deregister_clears_value():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register(["a", "b"])
    backend.deregister()
    assert backend.is_registered() is False


def test_windows_backend_is_current_true_after_register():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    command = ["a", "b"]
    backend.register(command)
    assert backend.is_current(command) is True


def test_windows_backend_is_current_false_after_interpreter_path_changes():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register([r"C:\Old\pythonw.exe", "launcher.pyw"])
    assert backend.is_current([r"C:\New\pythonw.exe", "launcher.pyw"]) is False


def test_windows_backend_is_current_false_when_not_registered():
    assert WindowsRegistryBackend(registry=_FakeRegistry()).is_current(["a", "b"]) is False


def test_mac_backend_register_writes_plist(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    command = ["/usr/bin/python3", "/Users/nick/Library/Application Support/Tokitty/autostart_launcher.pyw"]
    backend.register(command)
    data = plistlib.loads((tmp_path / "com.nickwolf.tokitty.plist").read_bytes())
    assert data["Label"] == "com.nickwolf.tokitty"
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"] == command


def test_mac_backend_is_registered_roundtrip(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    assert backend.is_registered() is False
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    assert backend.is_registered() is True


def test_mac_backend_deregister_removes_plist(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    backend.deregister()
    assert backend.is_registered() is False


def test_mac_backend_deregister_when_absent_does_not_raise(tmp_path):
    MacLaunchAgentBackend(launch_agents_dir=tmp_path).deregister()


def test_mac_backend_is_current(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    command = ["/usr/bin/python3", "launcher.pyw"]
    backend.register(command)
    assert backend.is_current(command) is True
    assert backend.is_current(["/usr/bin/python3", "different.pyw"]) is False


def test_linux_backend_register_writes_desktop_file(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "/home/nick/.config/tokitty/autostart_launcher.pyw"])
    content = (tmp_path / "tokitty.desktop").read_text(encoding="utf-8")
    assert "Exec=/usr/bin/python3 /home/nick/.config/tokitty/autostart_launcher.pyw" in content
    assert "X-GNOME-Autostart-enabled=true" in content


def test_linux_backend_register_quotes_space_containing_path(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "/home/nick/My Documents/tokitty/autostart_launcher.pyw"])
    content = (tmp_path / "tokitty.desktop").read_text(encoding="utf-8")
    assert 'Exec=/usr/bin/python3 "/home/nick/My Documents/tokitty/autostart_launcher.pyw"' in content


def test_linux_backend_is_registered_roundtrip(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    assert backend.is_registered() is False
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    assert backend.is_registered() is True


def test_linux_backend_deregister_removes_file(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    backend.deregister()
    assert backend.is_registered() is False


def test_linux_backend_is_current_detects_drift(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    assert backend.is_current(["/usr/bin/python3", "launcher.pyw"]) is True
    assert backend.is_current(["/usr/bin/python3.11", "launcher.pyw"]) is False


def test_get_backend_selects_windows():
    assert isinstance(get_backend(platform="win32"), WindowsRegistryBackend)


def test_get_backend_selects_mac():
    assert isinstance(get_backend(platform="darwin"), MacLaunchAgentBackend)


def test_get_backend_selects_linux():
    assert isinstance(get_backend(platform="linux"), LinuxDesktopEntryBackend)


def test_get_backend_none_for_unknown_platform():
    assert get_backend(platform="freebsd13") is None
```
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'WindowsRegistryBackend'`.
- [ ] Step 3: Implement. Add to `tokitty/autostart.py` (add `import subprocess`, `import plistlib` to the imports):
```python
WINDOWS_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_VALUE_NAME = "Tokitty"


class _RealWindowsRegistry:
    """Talks to the real HKCU Run key. winreg is imported lazily inside
    each method, never at module scope -- it doesn't exist off win32, and
    this module must stay importable (and its tests runnable) on every
    CI OS."""

    def read_value(self) -> "Optional[str]":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_KEY_PATH) as key:
                value, _ = winreg.QueryValueEx(key, WINDOWS_VALUE_NAME)
                return value
        except FileNotFoundError:
            return None

    def write_value(self, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_KEY_PATH) as key:
            winreg.SetValueEx(key, WINDOWS_VALUE_NAME, 0, winreg.REG_SZ, value)

    def delete_value(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WINDOWS_VALUE_NAME)
        except FileNotFoundError:
            pass


class WindowsRegistryBackend:
    def __init__(self, registry=None):
        self._registry = registry if registry is not None else _RealWindowsRegistry()

    def register(self, command: List[str]) -> None:
        self._registry.write_value(subprocess.list2cmdline(command))

    def deregister(self) -> None:
        self._registry.delete_value()

    def is_registered(self) -> bool:
        return self._registry.read_value() is not None

    def is_current(self, command: List[str]) -> bool:
        return self._registry.read_value() == subprocess.list2cmdline(command)


MAC_LAUNCH_AGENT_LABEL = "com.nickwolf.tokitty"


class MacLaunchAgentBackend:
    def __init__(self, launch_agents_dir: Optional[Path] = None):
        self._dir = launch_agents_dir if launch_agents_dir is not None else (Path.home() / "Library" / "LaunchAgents")

    def _path(self) -> Path:
        return self._dir / f"{MAC_LAUNCH_AGENT_LABEL}.plist"

    def register(self, command: List[str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"Label": MAC_LAUNCH_AGENT_LABEL, "ProgramArguments": list(command), "RunAtLoad": True}
        path = self._path()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            plistlib.dump(payload, f)
        os.replace(tmp_path, path)
        # Writing the plist takes effect at the NEXT login. This does not
        # shell out to `launchctl load`/`bootstrap` for immediate
        # activation in the current session -- out of scope: the design
        # doesn't specify activation timing, and the hands-on
        # verification gate (Task 9) is Windows-only.

    def deregister(self) -> None:
        try:
            self._path().unlink()
        except FileNotFoundError:
            pass

    def is_registered(self) -> bool:
        return self._path().is_file()

    def is_current(self, command: List[str]) -> bool:
        path = self._path()
        if not path.is_file():
            return False
        try:
            data = plistlib.loads(path.read_bytes())
        except Exception:
            return False
        return data.get("ProgramArguments") == list(command)


LINUX_DESKTOP_FILENAME = "tokitty.desktop"
_DESKTOP_RESERVED_CHARS = set(" \t\n\"'\\><~|&;$*?#()`")


def _quote_desktop_arg(arg: str) -> str:
    """Desktop Entry Spec Exec-key quoting: double quotes, not the
    single-quote style subprocess/shlex.quote would use, with
    backslash-escaping of the few characters still special inside double
    quotes. Only engaged when needed -- a Linux interpreter path with
    none of these characters (the overwhelming common case) passes
    through unquoted."""
    if not any(ch in _DESKTOP_RESERVED_CHARS for ch in arg):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _render_exec_line(command: List[str]) -> str:
    return " ".join(_quote_desktop_arg(a) for a in command)


class LinuxDesktopEntryBackend:
    def __init__(self, autostart_dir: Optional[Path] = None):
        if autostart_dir is not None:
            self._dir = autostart_dir
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
            self._dir = Path(base) / "autostart"

    def _path(self) -> Path:
        return self._dir / LINUX_DESKTOP_FILENAME

    def _content(self, command: List[str]) -> str:
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Tokitty\n"
            f"Exec={_render_exec_line(command)}\n"
            "X-GNOME-Autostart-enabled=true\n"
        )

    def register(self, command: List[str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(self._content(command), encoding="utf-8")
        os.replace(tmp_path, path)

    def deregister(self) -> None:
        try:
            self._path().unlink()
        except FileNotFoundError:
            pass

    def is_registered(self) -> bool:
        return self._path().is_file()

    def is_current(self, command: List[str]) -> bool:
        path = self._path()
        if not path.is_file():
            return False
        target = f"Exec={_render_exec_line(command)}"
        return any(line == target for line in path.read_text(encoding="utf-8").splitlines())


def get_backend(platform: Optional[str] = None):
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return WindowsRegistryBackend()
    if platform == "darwin":
        return MacLaunchAgentBackend()
    if platform.startswith("linux"):
        return LinuxDesktopEntryBackend()
    return None
```
Add `import os` if Task 2 didn't already add it; `import plistlib` and `import subprocess` are new for this task.
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_autostart.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/autostart.py tests/test_autostart.py
git commit -m "autostart: Windows registry, macOS LaunchAgent, Linux desktop-entry backends"
```

---

### Task 4: Stale-entry validity check and rewrite

**Goal:** `ensure_current(state_dir, backend, ...)` is the drift-repair mechanism the design requires: a no-op unless autostart is already registered. Two separate rewrites live here, because they repair two different kinds of drift and neither alone covers both. The registered OS command's argv never encodes the repo path at all -- only the launcher file's *content* does -- so a moved repo clone can only be repaired by unconditionally regenerating the launcher file every time this runs; comparing the registered argv would never notice a repo move. The OS registration itself is rewritten only if the freshly resolved command actually differs from what's registered (e.g. a changed interpreter path), so a normal launch doesn't do a registry/plist/desktop-file write every single time for no reason.

**Files:**
- Modify: `tokitty/autostart.py` (add `ensure_current`)
- Test: `tests/test_autostart.py`

**Acceptance Criteria:**
- [ ] `ensure_current` is a no-op (`False`, no `write_launcher_file` or `backend.register` call) when `backend.is_registered()` is `False` -- this is drift repair, never opt-in registration.
- [ ] When registered and the freshly resolved command differs from what's registered (interpreter-path drift), `ensure_current` calls `backend.register(<fresh command>)` and returns `True`.
- [ ] When registered and already current, `ensure_current` returns `False` and never calls `backend.register`.
- [ ] `ensure_current` always calls `write_launcher_file` when autostart is registered, regardless of whether the registered command itself needed rewriting -- this is what repairs a moved repo clone, since the registered command's argv can't detect that on its own.
- [ ] An `OSError` raised anywhere inside `ensure_current` (a locked file, a denied registry write) is caught and the function returns `False` rather than propagating -- mirrors `retry_pending_hook_op`'s existing "a background-ish startup check must never crash startup" guard in `__main__.py`'s `run_discovery`.

**Verify:**
```
python3 -m pytest tests/test_autostart.py -k ensure_current -v
```
Expected: 5 new tests pass, including the always-rewrites-the-launcher-file case and the OSError-swallowed case.

**Steps:**
- [ ] Step 1: Write the failing tests. Add to `tests/test_autostart.py`:
```python
from tokitty.autostart import LAUNCHER_FILENAME, ensure_current, resolve_launch_command


class _RecordingBackend:
    def __init__(self, registered=False, current_command=None, raise_on_register=False):
        self._registered = registered
        self._command = current_command
        self._raise_on_register = raise_on_register
        self.registered_calls = []

    def is_registered(self):
        return self._registered

    def is_current(self, command):
        return self._command == command

    def register(self, command):
        if self._raise_on_register:
            raise OSError("permission denied")
        self.registered_calls.append(command)
        self._command = command
        self._registered = True


def test_ensure_current_noop_when_not_registered(tmp_path):
    backend = _RecordingBackend(registered=False)
    changed = ensure_current(tmp_path, backend, repo_root=tmp_path / "repo")
    assert changed is False
    assert backend.registered_calls == []


def test_ensure_current_rewrites_registration_on_interpreter_drift(tmp_path):
    stale = ["/usr/bin/python3.10", str(tmp_path / LAUNCHER_FILENAME)]
    backend = _RecordingBackend(registered=True, current_command=stale)
    changed = ensure_current(
        tmp_path, backend, repo_root=tmp_path / "repo", executable="/usr/bin/python3.12", platform="linux",
    )
    assert changed is True
    assert backend.registered_calls[-1][0] == "/usr/bin/python3.12"


def test_ensure_current_noop_when_already_current(tmp_path):
    fresh = resolve_launch_command(tmp_path, executable="/usr/bin/python3", platform="linux", repo_root=tmp_path / "repo")
    backend = _RecordingBackend(registered=True, current_command=fresh)
    changed = ensure_current(
        tmp_path, backend, repo_root=tmp_path / "repo", executable="/usr/bin/python3", platform="linux",
    )
    assert changed is False
    assert backend.registered_calls == []


def test_ensure_current_always_rewrites_the_launcher_file(tmp_path):
    """Repo-moved case: the registered command's argv never encodes the
    repo path (only the launcher file's content does), so the only way
    to repair a moved repo is to unconditionally regenerate the launcher
    file -- covered even when the registered command doesn't change at
    all."""
    fresh = resolve_launch_command(
        tmp_path, executable="/usr/bin/python3", platform="linux", repo_root=tmp_path / "old_repo"
    )
    backend = _RecordingBackend(registered=True, current_command=fresh)
    ensure_current(tmp_path, backend, repo_root=tmp_path / "new_repo", executable="/usr/bin/python3", platform="linux")
    content = (tmp_path / LAUNCHER_FILENAME).read_text(encoding="utf-8")
    assert str(tmp_path / "new_repo") in content


def test_ensure_current_swallows_oserror_and_returns_false(tmp_path):
    stale = ["/usr/bin/python3.10", str(tmp_path / LAUNCHER_FILENAME)]
    backend = _RecordingBackend(registered=True, current_command=stale, raise_on_register=True)
    changed = ensure_current(
        tmp_path, backend, repo_root=tmp_path / "repo", executable="/usr/bin/python3.12", platform="linux",
    )
    assert changed is False
```
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'ensure_current'`.
- [ ] Step 3: Implement. Add to `tokitty/autostart.py`:
```python
def ensure_current(
    state_dir: Path,
    backend,
    *,
    repo_root: Optional[Path] = None,
    executable: Optional[str] = None,
    platform: Optional[str] = None,
    frozen: Optional[bool] = None,
) -> bool:
    """Startup drift repair, never opt-in registration -- a no-op unless
    autostart is already registered. See this task's Goal for why the
    launcher file is rewritten unconditionally while the OS registration
    is rewritten only on an actual mismatch."""
    try:
        if not backend.is_registered():
            return False
        resolved_repo_root = _default_repo_root() if repo_root is None else repo_root
        write_launcher_file(state_dir, resolved_repo_root)
        command = resolve_launch_command(
            state_dir, repo_root=resolved_repo_root, executable=executable, platform=platform, frozen=frozen,
        )
        if backend.is_current(command):
            return False
        backend.register(command)
        return True
    except OSError:
        # A permission hiccup or locked file here must never crash
        # startup (mirrors run_discovery's retry_pending_hook_op guard
        # in __main__.py). Worst case the entry stays stale for one more
        # boot -- exactly the class of failure this function exists to
        # eventually repair, not a new one.
        return False
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_autostart.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/autostart.py tests/test_autostart.py
git commit -m "autostart: startup drift check, rewrite launcher file and stale registration"
```

---

### Task 5: Menu wiring with the cached shadow bool

**Goal:** Add a "Start at login" checkbox to the single-source menu model, following the exact `tray_enabled`/`on_toggle_tray` optional-pair convention already used for the tray toggle, and wire it into `run_gui`: pick a backend once, read its real registered state into a plain-Python cached bool exactly once at startup (plus once after each toggle), call `ensure_current` at startup so drift gets repaired before the checkbox is ever drawn, and never let the getter itself touch the registry or filesystem.

**Files:**
- Modify: `tokitty/menu.py:26-46` (`build_menu` signature), `:74-77` (item assembly)
- Modify: `tokitty/ui.py:285-290` (`TokittyWindow.__init__` seam defaults), `:358-377` (`build_menu_model` pass-through)
- Modify: `tokitty/__main__.py` (`run_gui`, wiring block after the tray setup, before `if warning:`)
- Test: `tests/test_menu.py`, `tests/test_ui_layout.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `build_menu` gains `autostart_enabled: Optional[Callable[[], bool]] = None` and `on_toggle_autostart: Optional[Callable[[], None]] = None`; when both are provided, a `"Start at login"` `MenuItem` is appended between `"Show tray icon"` and `"Surprise me"`.
- [ ] Omitting either (the default) omits the item entirely -- every existing `build_menu` caller and test is unaffected, confirmed by `test_structure_and_labels` continuing to pass unmodified.
- [ ] `TokittyWindow` exposes `self.autostart_enabled: Optional[Callable[[], bool]] = None` and `self.on_toggle_autostart: Optional[Callable[[], None]] = None`, defaulted to `None` in `__init__` (confirmed via `inspect.getsource`, no live `tk.Tk()` needed), matching the `tray_enabled`/`on_toggle_tray` pattern.
- [ ] `build_menu_model` passes both through to `build_menu`, so `"Start at login"` appears in both the Tk right-click menu and (via `TrayManager`'s reuse of `build_menu_model`) the tray menu.
- [ ] `run_gui` calls `get_backend()` once; if it returns `None` (unsupported platform), `window.autostart_enabled`/`window.on_toggle_autostart` are left at their `None` defaults and the menu item never renders.
- [ ] If a backend is available, `run_gui` calls `ensure_current(state_dir, backend)` once at startup (synchronously, before `root.mainloop()` -- registry/plist/desktop-file reads and writes are fast local I/O, the same class of operation as `load_settings`/`load_customization`, which this file already runs synchronously at startup; this is not the class of slow, possibly-blocking-on-a-cold-WSL-boot operation `run_discovery`'s background thread exists for), then reads `backend.is_registered()` once into a plain dict/bool.
- [ ] `window.autostart_enabled` is a closure reading only that cached bool -- no call to `backend.is_registered()` inside the getter itself.
- [ ] The toggle callback (`window.on_toggle_autostart`) performs the actual I/O: on turning on, `write_launcher_file(state_dir)` then `backend.register(resolve_launch_command(state_dir))`; on turning off, `backend.deregister()`; either way, the cached bool is refreshed afterward via one more `backend.is_registered()` read (not assumed from the action's own success) -- consistent with how `toggle_tray`/`toggle_surprise` already perform their own synchronous I/O directly inside a menu action callback (`save_settings`), which is a different code path from the getters this constraint restricts.

**Verify:**
```
python3 -m pytest tests/test_menu.py tests/test_ui_layout.py tests/test_main.py -v
```
Expected: all pass, including the new menu-item presence/absence tests, the `ui.py` seam-default test, and the `gui`-marked `run_gui` wiring tests (run those via `xvfb-run -a pytest -m gui tests/test_main.py`).

**Steps:**
- [ ] Step 1: Write the failing tests.

`tests/test_menu.py` additions:
```python
def test_autostart_item_absent_without_seam():
    kwargs, _, _ = _kwargs()
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Start at login" not in labels


def test_autostart_item_present_with_seam():
    kwargs, calls, state = _kwargs(
        autostart_enabled=lambda: state.get("autostart", True),
        on_toggle_autostart=lambda: calls.__setitem__("toggle_autostart", calls.get("toggle_autostart", 0) + 1),
    )
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    assert "Start at login" in items
    assert items["Start at login"].checkbox() is True
    items["Start at login"].action()
    assert calls["toggle_autostart"] == 1
```

`tests/test_ui_layout.py` additions:
```python
def test_autostart_seam_defaults_none_in_init_source():
    from tokitty import ui

    src = inspect.getsource(ui.TokittyWindow.__init__)
    for attr in ("self.autostart_enabled", "self.on_toggle_autostart"):
        lines = [line.strip() for line in src.splitlines() if attr in line]
        assert lines and lines[0].endswith("= None")


@pytest.mark.gui
def test_autostart_seam_adds_start_at_login_item():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            window.autostart_enabled = lambda: True
            window.on_toggle_autostart = lambda: None
            labels = [i.label for i in window.build_menu_model(0) if not i.separator]
            assert "Start at login" in labels
    finally:
        root.destroy()
```
(`inspect` is already imported at the top of `test_ui_layout.py` for the existing `test_on_customization_changed_default_none_in_init_source` test.)

`tests/test_main.py` additions:
```python
class _FakeToggleBackend:
    def __init__(self, registered=False):
        self.registered = registered
        self.last_registered_command = None

    def is_registered(self):
        return self.registered

    def is_current(self, command):
        return self.registered and self.last_registered_command == command

    def register(self, command):
        self.registered = True
        self.last_registered_command = command

    def deregister(self):
        self.registered = False


@pytest.mark.gui
def test_run_gui_wires_autostart_seam_and_toggle(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import ui
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)

    fake_backend = _FakeToggleBackend(registered=True)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: fake_backend)

    holder = {}
    real_window = ui.TokittyWindow

    class CapturingWindow(real_window):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["window"] = self

    monkeypatch.setattr(ui, "TokittyWindow", CapturingWindow)

    def _mainloop(self):
        window = holder["window"]
        assert window.autostart_enabled() is True
        window.on_toggle_autostart()
        assert fake_backend.registered is False
        assert window.autostart_enabled() is False

    monkeypatch.setattr(tk.Tk, "mainloop", _mainloop)
    assert main_module.run_gui() == 0


@pytest.mark.gui
def test_run_gui_leaves_autostart_seam_none_on_unsupported_platform(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import ui
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: None)

    holder = {}
    real_window = ui.TokittyWindow

    class CapturingWindow(real_window):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["window"] = self

    monkeypatch.setattr(ui, "TokittyWindow", CapturingWindow)

    def _mainloop(self):
        window = holder["window"]
        assert window.autostart_enabled is None
        assert window.on_toggle_autostart is None

    monkeypatch.setattr(tk.Tk, "mainloop", _mainloop)
    assert main_module.run_gui() == 0


@pytest.mark.gui
def test_run_gui_calls_ensure_current_at_startup(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)

    fake_backend = _FakeToggleBackend(registered=True)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: fake_backend)
    calls = []
    monkeypatch.setattr(
        "tokitty.autostart.ensure_current", lambda state_dir, backend: calls.append((state_dir, backend))
    )

    assert main_module.run_gui() == 0
    assert calls == [(tmp_path, fake_backend)]
```
- [ ] Step 2: Run and see it fail. `TypeError: build_menu() got an unexpected keyword argument 'autostart_enabled'`, then (after fixing that) `AttributeError: 'TokittyWindow' object has no attribute 'autostart_enabled'`.
- [ ] Step 3: Implement.

In `tokitty/menu.py`, add the two parameters to `build_menu`'s signature (after `on_open_accounts`):
```python
    autostart_enabled: Optional[Callable[[], bool]] = None,
    on_toggle_autostart: Optional[Callable[[], None]] = None,
```
and insert the item between the tray and surprise blocks:
```python
    if on_toggle_tray is not None and tray_enabled is not None:
        items.append(MenuItem(label="Show tray icon", action=on_toggle_tray, checkbox=tray_enabled))
    if on_toggle_autostart is not None and autostart_enabled is not None:
        items.append(MenuItem(label="Start at login", action=on_toggle_autostart, checkbox=autostart_enabled))
    if on_toggle_surprise is not None and surprise_me is not None:
        items.append(MenuItem(label="Surprise me", action=on_toggle_surprise, checkbox=surprise_me))
```

In `tokitty/ui.py`'s `TokittyWindow.__init__`, alongside `self.on_toggle_tray`/`self.tray_enabled`:
```python
        self.autostart_enabled: Optional[Callable[[], bool]] = None
        self.on_toggle_autostart: Optional[Callable[[], None]] = None
```
In `build_menu_model`, alongside the existing `tray_enabled=self.tray_enabled, on_toggle_tray=self.on_toggle_tray,`:
```python
            autostart_enabled=self.autostart_enabled,
            on_toggle_autostart=self.on_toggle_autostart,
```

In `tokitty/__main__.py`'s `run_gui`, after the existing tray-wiring block (`tray = TrayManager(...)` through `window.on_toggle_tray = toggle_tray`) and before `if warning:`:
```python
    from tokitty.autostart import ensure_current, get_backend, resolve_launch_command, write_launcher_file

    autostart_backend = get_backend()
    if autostart_backend is not None:
        ensure_current(state_dir, autostart_backend)
        autostart_state = {"enabled": autostart_backend.is_registered()}
        window.autostart_enabled = lambda: autostart_state["enabled"]

        def toggle_autostart() -> None:
            if autostart_state["enabled"]:
                autostart_backend.deregister()
            else:
                write_launcher_file(state_dir)
                autostart_backend.register(resolve_launch_command(state_dir))
            autostart_state["enabled"] = autostart_backend.is_registered()

        window.on_toggle_autostart = toggle_autostart
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_menu.py tests/test_ui_layout.py -v && xvfb-run -a pytest -m gui tests/test_main.py -v` (or run the `gui` suite however this environment already does -- see the existing `smoke` CI job).
- [ ] Step 5: Commit.
```
git add tokitty/menu.py tokitty/ui.py tokitty/__main__.py tests/test_menu.py tests/test_ui_layout.py tests/test_main.py
git commit -m "menu: wire Start at login into the right-click and tray menus"
```

---

### Task 6: Boot-race tolerance regression coverage

**Goal:** Confirm, with a permanent regression test, the design's boot-race requirement: a failed *first* poll at startup (e.g. WSL not answering yet at login) is indistinguishable from any other transient poll failure and recovers on the normal poll cadence with no user action. This task is different in kind from the others in this plan: reading `poller.py:68-95` (`Poller._run`'s generic backoff-and-retry loop, which never special-cases "no previous result yet") and `__main__.py:296-313` (`_display_state_for`'s generic non-ok/no-cache fallback, keyed only on `result.status`, never on whether this is the first poll ever) shows the requirement is already satisfied by existing code. Step 2 below should therefore already pass -- that is the point: autostart is what turns a startup credential-resolution failure from rare to routine, so this plan adds it as a named, permanent guard rather than leaving it as an implicit property nobody asserts. If either test below fails, that is a real regression to fix, not a signal to change the test.

**Files:**
- Test: `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `_display_state_for` renders a `"credentials_unreachable"` status with `previous=None` (the first-poll-ever shape) through the exact same fallback branch as `test_non_ok_with_no_good_snapshot_shows_blocking_fallback` already exercises for `"stale_token"`: `state == "confused"`, `dimmed is True`, `hint_text == "can't find credentials"`.
- [ ] A real `Poller`, driven by the real `build_fetch_fn()` (not a fake `fetch_fn`), with `resolve_credentials_source` raising `CredentialsError` on the first 2 calls and succeeding on the 3rd (simulating WSL not answering yet at login, then coming up), reaches `get_latest().status == "ok"` entirely on its own.
- [ ] That same test never calls `poller.request_refresh()` -- recovery happens purely from the existing backoff loop, with no user action.

**Verify:**
```
python3 -m pytest tests/test_main.py -k "credentials_unreachable or boot_race" -v
```
Expected: both tests pass without any change to `poller.py` or `__main__.py`. If either fails, stop and fix the underlying behavior before moving to Task 7 -- do not weaken the test to make it pass.

**Steps:**
- [ ] Step 1: Write the tests. Add to `tests/test_main.py` (add `from pathlib import Path` to the top-of-file imports if not already present):
```python
def test_no_good_snapshot_credentials_unreachable_matches_generic_fallback():
    """Boot-race requirement: a failed FIRST poll (no previous ok result
    yet -- e.g. WSL not answering yet at login) must render through the
    exact same generic non-ok/no-cache path as any other transient
    failure. No special-cased "still booting" state that could get
    stuck exists anywhere in _display_state_for."""
    display = _display_state_for(_error("credentials_unreachable"), previous=None, now=NOW)
    assert display["state"] == "confused"
    assert display["dimmed"] is True
    assert display["hint_text"] == "can't find credentials"


def test_boot_race_recovers_without_manual_refresh(monkeypatch):
    """End-to-end: resolve_credentials_source fails on the first two
    calls (the same CredentialsError build_fetch_fn already maps to
    credentials_unreachable) and succeeds on the third. A real Poller,
    with its real backoff loop, must reach "ok" on its own -- the whole
    point of autostart making a first-poll failure routine instead of
    rare -- with request_refresh() never called."""
    from tokitty.credentials import CredentialsError, LocalCredentialsSource
    from tokitty.poller import Poller

    attempts = {"n": 0}

    def flaky_resolve(config_dir=None):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise CredentialsError("WSL not answering yet")
        return LocalCredentialsSource(path=Path("/does/not/matter"))

    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", flaky_resolve)
    monkeypatch.setattr(
        "tokitty.__main__.load_credentials", lambda src: {"expiresAt": 4102444800000, "accessToken": "tok"}
    )
    monkeypatch.setattr("tokitty.__main__.fetch_usage", lambda token: {"raw": "doesn't matter, parse is stubbed"})
    monkeypatch.setattr("tokitty.__main__.parse_usage_response", lambda raw: _snapshot())

    fetch_fn = build_fetch_fn()
    done = threading.Event()

    def wrapped_fetch():
        result = fetch_fn()
        if result.status == "ok":
            done.set()
        return result

    poller = Poller(fetch_fn=wrapped_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        assert done.wait(timeout=3), "poller never reached ok on its own"
        assert poller.get_latest().status == "ok"
    finally:
        poller.stop()
    assert attempts["n"] == 3  # 2 simulated failures + the recovering success
```
- [ ] Step 2: Run and confirm both already pass. `python3 -m pytest tests/test_main.py -k "credentials_unreachable or boot_race" -v`. As noted in the Goal, this is confirmatory, not TDD-red-then-green -- if it's red, treat it as a real bug in `poller.py`/`_display_state_for`, fix that, then re-run.
- [ ] Step 3: (Only if Step 2 was red.) Fix the gap in `poller.py` or `__main__.py`, re-run until green.
- [ ] Step 4: Commit.
```
git add tests/test_main.py
git commit -m "test: lock in boot-race recovery as a named regression guard"
```

---

### Task 7: Optional CLI flags

**Goal:** `--install-autostart` / `--uninstall-autostart`, mirroring `--install-hooks` / `--uninstall-hooks` exactly in shape (a small function in the feature's own module, dispatched from `main()` via a local import, printing a one-line result and returning `0`/`1`), for headless setup and scripting alongside the menu checkbox.

**Files:**
- Modify: `tokitty/autostart.py` (add `install_autostart`, `uninstall_autostart`; add `from tokitty.paths import get_state_dir`)
- Modify: `tokitty/__main__.py:734-742` (`main`, add the two flag branches before `return run_gui()`)
- Test: `tests/test_autostart.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `install_autostart()` calls `get_backend()`; if `None` (unsupported platform), prints an error naming `sys.platform` to stderr and returns `1` without touching the filesystem.
- [ ] Otherwise, `install_autostart()` calls `write_launcher_file(state_dir)` then `backend.register(resolve_launch_command(state_dir))`, prints a one-line success message, returns `0`.
- [ ] `uninstall_autostart()` mirrors this for `backend.deregister()`.
- [ ] An `OSError` raised by either the launcher write or the backend call is caught, printed to stderr with the underlying message, and returns `1` -- never propagates out of the CLI entry point.
- [ ] `main(["--install-autostart"])` dispatches to `install_autostart()` via a local import, matching the existing `--install-hooks` dispatch shape exactly (`tokitty/__main__.py:734-737`); same for `--uninstall-autostart`.

**Verify:**
```
python3 -m pytest tests/test_autostart.py -k "install_autostart or uninstall_autostart" -v && python3 -m pytest tests/test_main.py -k "install_autostart or uninstall_autostart" -v
```
Expected: 4 new tests in `test_autostart.py` (install, uninstall, unsupported-platform, OSError) and 2 new dispatch tests in `test_main.py`, all pass.

**Steps:**
- [ ] Step 1: Write the failing tests.

Add to `tests/test_autostart.py`:
```python
def test_install_autostart_registers_via_backend(tmp_path, monkeypatch):
    from tokitty import autostart

    fake_backend = _FakeToggleBackendForCli()
    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: fake_backend)

    assert autostart.install_autostart() == 0
    assert fake_backend.registered is True
    assert (tmp_path / autostart.LAUNCHER_FILENAME).is_file()


def test_uninstall_autostart_deregisters_via_backend(tmp_path, monkeypatch):
    from tokitty import autostart

    fake_backend = _FakeToggleBackendForCli(registered=True)
    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: fake_backend)

    assert autostart.uninstall_autostart() == 0
    assert fake_backend.registered is False


def test_install_autostart_unsupported_platform_returns_1(tmp_path, monkeypatch, capsys):
    from tokitty import autostart

    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: None)

    assert autostart.install_autostart() == 1
    assert "not supported" in capsys.readouterr().err


def test_install_autostart_oserror_returns_1(tmp_path, monkeypatch, capsys):
    from tokitty import autostart

    class _RaisingBackend:
        def is_registered(self):
            return False

        def register(self, command):
            raise OSError("permission denied")

        def deregister(self):
            pass

        def is_current(self, command):
            return False

    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: _RaisingBackend())

    assert autostart.install_autostart() == 1
    assert "permission denied" in capsys.readouterr().err


class _FakeToggleBackendForCli:
    def __init__(self, registered=False):
        self.registered = registered

    def is_registered(self):
        return self.registered

    def is_current(self, command):
        return False

    def register(self, command):
        self.registered = True

    def deregister(self):
        self.registered = False
```
(If Task 5 already defined an equivalent fake in `test_main.py`, this is a separate, `test_autostart.py`-local fake -- the two test files don't share fixtures here.)

Add to `tests/test_main.py`:
```python
def test_main_dispatches_install_autostart(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.autostart.install_autostart", lambda: calls.append("install") or 0)

    assert main_module.main(["--install-autostart"]) == 0
    assert calls == ["install"]


def test_main_dispatches_uninstall_autostart(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.autostart.uninstall_autostart", lambda: calls.append("uninstall") or 0)

    assert main_module.main(["--uninstall-autostart"]) == 0
    assert calls == ["uninstall"]
```
- [ ] Step 2: Run and see it fail. `AttributeError: module 'tokitty.autostart' has no attribute 'install_autostart'`.
- [ ] Step 3: Implement. Add to `tokitty/autostart.py` (add `from tokitty.paths import get_state_dir`):
```python
def install_autostart() -> int:
    state_dir = get_state_dir()
    backend = get_backend()
    if backend is None:
        print(f"autostart is not supported on this platform ({sys.platform})", file=sys.stderr)
        return 1
    try:
        write_launcher_file(state_dir)
        backend.register(resolve_launch_command(state_dir))
    except OSError as exc:
        print(f"could not install autostart: {exc}", file=sys.stderr)
        return 1
    print("autostart installed: tokitty will launch at login")
    return 0


def uninstall_autostart() -> int:
    state_dir = get_state_dir()
    backend = get_backend()
    if backend is None:
        print(f"autostart is not supported on this platform ({sys.platform})", file=sys.stderr)
        return 1
    try:
        backend.deregister()
    except OSError as exc:
        print(f"could not remove autostart: {exc}", file=sys.stderr)
        return 1
    print("autostart removed")
    return 0
```
In `tokitty/__main__.py`'s `main`, after the `--uninstall-hooks` branch (`:738-741`) and before `return run_gui()` (`:742`):
```python
    if "--install-autostart" in argv:
        from tokitty.autostart import install_autostart

        return install_autostart()
    if "--uninstall-autostart" in argv:
        from tokitty.autostart import uninstall_autostart

        return uninstall_autostart()
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_autostart.py tests/test_main.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/autostart.py tokitty/__main__.py tests/test_autostart.py tests/test_main.py
git commit -m "autostart: --install-autostart / --uninstall-autostart CLI flags"
```

---

### Task 8: README updates

**Goal:** Document the feature where every other opt-in toggle is documented -- its own section -- and make explicit, in Security & privacy, that autostart's on/off state is not among the things `settings.json` persists (this is the one detail most likely to get contradicted by accident if someone edits that paragraph later without re-reading this plan).

**Files:**
- Modify: `README.md` (new `## Autostart` section after `## Live activity` (ends `README.md:38`) and before `## Accounts` (`README.md:40`); one clarifying sentence appended to the `## Security & privacy` paragraph at `README.md:80`; drop "autostart" from the Roadmap backlog parenthetical at `README.md:174`)

**Acceptance Criteria:**
- [ ] A new `## Autostart` section exists, covering: opt-in/off-by-default, the menu checkbox location (right-click and tray, "Start at login"), the three underlying mechanisms in one sentence each, the two CLI flags, and the stale-entry self-heal behavior (and its one real limitation: deleting a clone with no replacement leaves a permanently broken entry until unchecked or `--uninstall-autostart` is run first).
- [ ] The `## Security & privacy` section's existing sentence about what's persisted (`settings.json` holding "show tray icon" and "surprise me") is **not** edited to add autostart to that list -- instead, one sentence is appended stating autostart's state lives entirely in the OS registration, never in `settings.json`.
- [ ] The Roadmap backlog line (`README.md:174`) no longer lists "autostart" as a backlog item, since it has shipped.
- [ ] No other section (`## Setup`, `## Platforms tested`, `## Configuration`) is modified -- the feature gets one self-contained home, matching how `## Live activity` and `## Accounts` are each self-contained.

**Verify:**
```
grep -n "^## Autostart" README.md && grep -n "autostart" README.md
```
Expected: the new heading exists; every remaining "autostart" mention is either inside the new section or the one added Security & privacy sentence, and the Roadmap line no longer contains the word.

**Steps:**
- [ ] Step 1: Insert the new section into `README.md` immediately after line 38 (the end of `## Live activity`) and before line 40 (`## Accounts`):
```markdown
## Autostart

Optional, off by default. Right-click any pane (or the tray icon) and check **Start at login** to have tokitty launch itself automatically the next time you log in -- no installer, no admin rights, nothing outside your own user account. Unchecking it removes the same registration.

The mechanism is native to each OS and needs no third-party dependency: the `HKCU\...\Run` registry key on Windows, a `LaunchAgent` in `~/Library/LaunchAgents` on macOS, and a `.desktop` file in `~/.config/autostart` on Linux. tokitty reads the real OS registration to decide what the checkbox shows, so removing the registration from outside tokitty (by hand, or by uninstalling tokitty entirely) is reflected correctly the next time the menu is opened, rather than the checkbox confidently showing "on" for something that no longer runs.

`python -m tokitty --install-autostart` and `python -m tokitty --uninstall-autostart` do the same thing from the command line, for headless setup or scripting.

If tokitty's repo clone is moved, or the Python interpreter it was registered against changes, the registration can go stale and silently fail to launch at the next login with nothing on screen to explain it. tokitty checks for this itself at every startup and rewrites the registration if it's drifted -- so as long as tokitty gets launched by hand at least once from wherever it now lives, the next automatic login launch self-heals. The one thing this can't fix: deleting the whole clone with no replacement leaves a permanently broken entry, since nothing is ever running to repair it. Uncheck **Start at login** (or run `--uninstall-autostart`) before deleting a clone that has autostart enabled.
```
- [ ] Step 2: Append one sentence to the `## Security & privacy` paragraph that currently ends `...the latter holding "show tray icon" and "surprise me") are the only things Tokitty's core (non-live-activity) code persists...` (`README.md:80`), without altering the rest of that sentence:
```markdown
Autostart's on/off state is not among them: it lives entirely in the OS's own registration (a registry value, a LaunchAgent plist, or a desktop entry, depending on platform), never in `settings.json`, so what the checkbox shows always reflects the real OS state rather than a copy that could go stale.
```
- [ ] Step 3: Edit the Roadmap line (`README.md:174`) to remove "autostart" from the backlog parenthetical:
```markdown
See [docs/ROADMAP.md](docs/ROADMAP.md) — phased plan (higher-res sprites, live activity states with a permission flag, dual-account support, cat customization) plus the backlog (ntfy notifications, tray icon, per-model bars, click-to-pet, and more). Tracked as GitHub milestones/issues on this repo.
```
- [ ] Step 4: Re-read the whole diff once against `docs/conventions/public_writing_playbook.md` before committing: no em-dashes, no hard-wrapped paragraphs (one line each), every number kept exact (there are none new here to round).
- [ ] Step 5: Commit.
```
git add README.md
git commit -m "docs: document autostart"
```

---

### Task 9: Manual Windows verification

**Goal:** This is the repo's established final gate for platform-integration work: confirm the real mechanism actually launches tokitty at a real Windows login, since nothing in the automated suite drives an actual reboot, an actual registry-triggered process start, or an actual `pythonw.exe` console-window check. No code changes in this task.

**Files:**
- None (verification only).

**Acceptance Criteria:**
- [ ] "Start at login" appears in both the right-click menu and the pystray tray menu, unchecked by default on a machine that has never toggled it.
- [ ] Checking it creates both `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Tokitty` and `%LOCALAPPDATA%\Tokitty\autostart_launcher.pyw`; both inspected directly, not inferred.
- [ ] A real log-off/log-on (not just re-running the app by hand) launches tokitty automatically, with no visible console window, landing in the interactive session rather than an invisible Session 0.
- [ ] Unchecking it removes the registry value; a subsequent log-off/log-on does not launch tokitty.
- [ ] Simulating drift (editing the registry value to a bogus interpreter path via `reg add`, then launching tokitty by hand from the real repo location) results in the entry being rewritten back to the correct value on that launch, confirmed via `reg query` before and after.
- [ ] `python -m tokitty --install-autostart` and `--uninstall-autostart` work standalone from a PowerShell prompt with tokitty not already running.

**Verify:**
```
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v Tokitty
```
Run from an elevated or non-elevated PowerShell as appropriate (this is a normal user-session app, not one that needs elevation), from `C:\Tools\tokitty`. Before trusting what appears on screen after a log-on, confirm the process actually landed in the interactive session rather than an invisible Session 0: `Get-Process pythonw,explorer | Select ProcessName,Id,SessionId` and check `pythonw`'s `SessionId` matches `explorer.exe`'s; if it doesn't, kill the orphan and re-check the registration rather than assuming the feature is broken. Expected: the registry value and launcher file both exist with the correct, currently-valid paths, all 6 acceptance criteria above are confirmed by hand, and no exception appears in a console-visible run (`python.exe -m tokitty` instead of `pythonw.exe -m tokitty`, to keep a console for errors during this verification only).

**Steps:**
- [ ] Step 1: From `C:\Tools\tokitty`, run `python.exe -m tokitty` (console visible, for this verification only) and confirm the app opens normally with autostart unchecked.
- [ ] Step 2: Right-click (or tray-click) and check **Start at login**. Confirm via `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v Tokitty` that the value now exists, and that `%LOCALAPPDATA%\Tokitty\autostart_launcher.pyw` exists with the correct repo root embedded.
- [ ] Step 3: Close tokitty. Log off and log back on (or reboot). Confirm tokitty appears automatically, with no console window, in the interactive session (`Get-Process pythonw,explorer | Select ProcessName,Id,SessionId` check).
- [ ] Step 4: Uncheck **Start at login**. Confirm the registry value is gone. Log off and log back on again; confirm tokitty does not launch.
- [ ] Step 5: Check **Start at login** again. Manually corrupt the registered value: `reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v Tokitty /t REG_SZ /d "C:\bogus\pythonw.exe C:\bogus\autostart_launcher.pyw" /f`. Launch tokitty by hand from `C:\Tools\tokitty` (`python.exe -m tokitty`). Confirm via `reg query` that the value has been rewritten back to the correct interpreter and launcher paths.
- [ ] Step 6: Close tokitty (ensure it isn't running). From PowerShell, run `python.exe -m tokitty --uninstall-autostart`; confirm it prints success and the registry value is gone. Run `python.exe -m tokitty --install-autostart`; confirm it prints success and the value is back, correctly.
- [ ] Step 7: Uncheck **Start at login** (or run `--uninstall-autostart`) to leave the machine clean afterward.
- [ ] Step 8: Report the outcome of all 6 acceptance criteria back in the plan-execution log. This task carries no `userGate` metadata; it is a plain task, not a gated one.
