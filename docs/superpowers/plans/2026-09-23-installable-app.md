# Installable App (#48) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Tokitty as unsigned PyInstaller onedir builds for Windows, macOS (arm64 and x86_64) and Linux, with hooks that work from the downloaded app.

**Architecture:** One PyInstaller spec builds two executables into one `COLLECT`: the windowed GUI and a `tokitty-hook` runner that can never open a window. `hooks_install` picks the registered hook by where Claude Code runs (python3 for a WSL home; for a same-OS home when frozen, exec form `<state dir>/current/tokitty-hook`, a junction or symlink the app repoints at its own release on every launch), recognises only hook shapes Tokitty wrote for that home, and refreshes stale owned hooks at startup without ever adding one. A release workflow builds, archives, extracts and tests only the extracted copy on every OS.

**Tech Stack:** Python 3.10+ (builds on 3.13), Tk, pystray, Pillow, PyInstaller 6.22.3 + pyinstaller-hooks-contrib 2026.7, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-23-installable-app-design.md`. Read it before any task. Question numbers below (Q1 to Q7) refer to its sections.

## Global Constraints

- Test commands, run from the worktree root after every task: `python3 -m pytest -q` (headless), `xvfb-run -a python3 -m pytest -m gui -q` (gui), `ruff check .`. Baseline before Task 1: 885 passed, 3 skipped headless; 53 gui. The gui run has a pre-existing intermittent failure in `tests/test_main.py` (a different test each run, e.g. `test_run_discovery_survives_wsl_scan_raising_credentials_error`) that passes when re-run alone. A gui failure counts as real only if it reproduces with `xvfb-run -a python3 -m pytest -m gui -q <nodeid>`.
- Every run of a built artifact uses a scratch state dir (`LOCALAPPDATA` on Windows, `HOME` and `XDG_CONFIG_HOME` elsewhere) and a scratch `accounts.json` naming a scratch Claude home. Nothing may touch a real `~/.claude`, `~/.claude-work`, or `\\wsl.localhost\...\.claude`.
- Never `Start-Process`, `Invoke-Item` or ShellExecute a file on Windows. Run `python.exe` or the built exe explicitly with an argv list. Kill anything you launch. Catch intentional failures so a windowed build never shows its "Unhandled exception" dialog.
- Local builds go on ext4 (`/tmp/tokitty-freeze/...`), never under `/mnt/c`: the spike measured a 544 ms hook median from `/mnt/c` against 53.0 ms from ext4 for the same Linux build.
- The copied `hook_writer.py` stays a self-contained stdlib script (its module docstring). `tokitty-hook` must exit 0 with empty stdout for any input.
- Public writing (commit messages, code comments, README): no em-dashes, no hard-wrapped prose, keep real numbers. Match the comment density of the file being edited. No AI attribution lines in commits.
- Do not push, trigger workflows, or open a PR. The controller asks Nick first.
- Merge back with `git merge --no-ff`, never squash.

**User decisions (already made):**
- PyInstaller onedir, windowed. Onefile and Nuitka are out.
- Separate Intel macOS build on `macos-15-intel` alongside arm64 on `macos-latest`. Archive names carry the architecture.
- Minimum Claude Code 2.1.139 for exe hooks, stated in the README.
- README describes the macOS 15 first-launch flow (Open Anyway in Privacy & Security, or `xattr -dr com.apple.quarantine`).
- Ship unsigned.

## Changes from the spec

- The entry scripts, spec file and CI helpers live in `freeze/`, not the repo root. A top-level `packaging/` would shadow the PyPI `packaging` module that PyInstaller imports when run as `python -m PyInstaller` from the repo root.
- Uninstall also narrows to owned hooks: it removes only Tokitty's hook from an entry and keeps a user's hook sharing that entry. Today it deletes the whole entry. Without this, Task 4's mixed-entry rule holds for install and refresh but not for uninstall.
- Ownership recognises the unquoted command form written by 9bab1b3 (2026-07-16 to 2026-07-18) as well as the quoted form, since installs from those two days still carry it.
- Refresh collapses duplicate owned hooks in one event to one, so a home carrying both a python and an exe hook stops firing twice.
- Ownership is also bound to the home being reconciled and to `type: "command"`, so a lookalike hook aimed at another home is never rewritten or removed. An event owned in `settings.local.json` also loses any owned duplicate in `settings.json`, so it cannot fire twice. (Both from the Codex review of this plan.)
- Task 11 records the real-Mac recipe from spec Q6 as an open gate before publishing, since the spec keeps it as the release gate and nobody has a Mac.
- The release job creates a **draft** release. The spec says "creates the release"; a draft keeps publication a manual step for Nick.
- The build ID that makes Task 8's two binaries differ is injected through a generated PyInstaller runtime hook (it changes the executable's embedded archive), and `--self-check` reports it.

## File Structure

- Create `freeze/tokitty.spec`: PyInstaller spec, two `Analysis`/`EXE` pairs, one `COLLECT`, `BUNDLE` on macOS.
- Create `freeze/bundle_data.py`: the list of files bundled as data under `tokitty/`. Imported by the spec and by the test.
- Create `freeze/gui_entry.py`, `freeze/hook_entry.py`: frozen entry points.
- Create `freeze/verify_artifact.py`: CI acceptance test and hook timing for one extracted artifact.
- Create `freeze/keychain_check.py`: macOS Keychain job (Task 8).
- Create `tokitty/frozen.py`: frozen-build support (translocation check, crash log, `--self-check`).
- Create `tokitty/runner_link.py`: the `<state dir>/current` link to the running release (spec Q2a).
- Test: `tests/test_runner_link.py`.
- Modify `tokitty/hooks_install.py`: command builder, ownership, reconcile/refresh, `ensure_current`.
- Modify `tokitty/autostart.py`: frozen launcher skip, translocation refusal.
- Modify `tokitty/__main__.py`: `--self-check`, startup hook refresh, autostart toggle error.
- Modify `pyproject.toml`: `packaging` extra.
- Create `.github/workflows/release.yml`.
- Modify `README.md`.
- Tests: `tests/test_freeze_spec.py`, `tests/test_hooks_install.py`, `tests/test_autostart.py`, `tests/test_frozen.py`, `tests/test_main.py`.

---

### Task 1: PyInstaller spec and entry points

**Goal:** A spec that builds `Tokitty` (windowed) and `tokitty-hook` (windowed, never raises) into one folder sharing `_internal`, proven before anything else depends on it.

**Files:**
- Create: `freeze/tokitty.spec`, `freeze/bundle_data.py`, `freeze/gui_entry.py`, `freeze/hook_entry.py`
- Modify: `pyproject.toml`
- Test: `tests/test_freeze_spec.py`

**Acceptance Criteria:**
- [ ] A Linux build on ext4 produces `dist/tokitty/tokitty`, `dist/tokitty/tokitty-hook` and exactly one `_internal` directory. The report states total size, and the size of a GUI-only build of the same spec, so the cost of the second EXE is a measured number.
- [ ] If the two EXEs do not share `_internal` (a second `_internal`, or duplicated Python runtime), stop and report to the controller before continuing. Do not work around it.
- [ ] `tokitty-hook` fed a real payload writes the state file, exits 0, prints nothing; fed garbage, no args, or an unwritable sessions dir, exits 0 and prints nothing.
- [ ] The built GUI exe's `--install-hooks` against a scratch home copies a byte-identical `hook_writer.py` (proves `datas`).
- [ ] `tests/test_freeze_spec.py` fails if a `[tool.setuptools.package-data]` file or `hook_writer.py` is missing from `freeze/bundle_data.py`.
- [ ] `pip install -e ".[packaging]"` installs pinned PyInstaller 6.22.3 and hooks-contrib 2026.7.

**Verify:** `python3 -m pytest -q tests/test_freeze_spec.py` → pass; full suite green.

**Steps:**

- [ ] **Step 1: packaging extra.** In `pyproject.toml` under `[project.optional-dependencies]` add:

```toml
packaging = ["pyinstaller==6.22.3", "pyinstaller-hooks-contrib==2026.7"]
```

- [ ] **Step 2: bundle list.** `freeze/bundle_data.py`:

```python
"""Files PyInstaller must ship as plain files under tokitty/ in the bundle.

Both are found through Path(__file__) at runtime (hooks_install copies
hook_writer.py, pricing reads prices.json), so they have to exist on disk
next to the frozen modules, not only inside the archive.
"""

DATA_FILES = ["hook_writer.py", "prices.json"]
```

- [ ] **Step 3: failing test.** `tests/test_freeze_spec.py`:

```python
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _bundle_files():
    sys.path.insert(0, str(ROOT / "freeze"))
    try:
        import bundle_data
    finally:
        sys.path.pop(0)
    return set(bundle_data.DATA_FILES)


def test_package_data_is_bundled():
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["tokitty"]
    missing = set(package_data) - _bundle_files()
    assert not missing, f"add {sorted(missing)} to freeze/bundle_data.py"


def test_hook_writer_is_bundled():
    assert "hook_writer.py" in _bundle_files()


def test_bundled_files_exist():
    for name in _bundle_files():
        assert (ROOT / "tokitty" / name).is_file(), name
```

Run `python3 -m pytest -q tests/test_freeze_spec.py`: fails with ImportError until Step 2's file exists, then passes. (Write the test first, run, then Step 2.)

- [ ] **Step 4: hook entry.** `freeze/hook_entry.py`:

```python
"""Entry point of the bundled tokitty-hook runner.

Claude Code reads hook stdout and exit codes as control signals, and a
windowed PyInstaller build turns an escaped exception into a modal dialog,
so nothing may escape here, including a failed import.
"""
import sys

try:
    from tokitty import hook_writer

    hook_writer.main()
except BaseException:
    pass
sys.exit(0)
```

- [ ] **Step 5: GUI entry.** `freeze/gui_entry.py` (Task 6 wraps this):

```python
import sys

from tokitty.__main__ import main

sys.exit(main())
```

- [ ] **Step 6: spec.** `freeze/tokitty.spec`:

```python
# PyInstaller spec for Tokitty. Build from the repo root:
#   python -m PyInstaller freeze/tokitty.spec --noconfirm --distpath <dist> --workpath <work>
# TOKITTY_BUILD_ID (optional) is baked into both executables; see --self-check.
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "freeze"))
from bundle_data import DATA_FILES  # noqa: E402

APP_NAME = "tokitty" if sys.platform.startswith("linux") else "Tokitty"
datas = [(str(ROOT / "tokitty" / name), "tokitty") for name in DATA_FILES]

rthook = Path(workpath) / "rthook_build_id.py"
rthook.parent.mkdir(parents=True, exist_ok=True)
rthook.write_text(
    "import os\n"
    f"os.environ['TOKITTY_BUILD_ID'] = {os.environ.get('TOKITTY_BUILD_ID', 'dev')!r}\n",
    encoding="utf-8",
)

gui = Analysis(
    [str(ROOT / "freeze" / "gui_entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    runtime_hooks=[str(rthook)],
)
hook = Analysis(
    [str(ROOT / "freeze" / "hook_entry.py")],
    pathex=[str(ROOT)],
    runtime_hooks=[str(rthook)],
    excludes=["tkinter", "PIL", "pystray"],
)

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
)
hook_exe = EXE(
    PYZ(hook.pure),
    hook.scripts,
    [],
    exclude_binaries=True,
    name="tokitty-hook",
    console=False,
)

coll = COLLECT(
    gui_exe, gui.binaries, gui.datas,
    hook_exe, hook.binaries, hook.datas,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Tokitty.app",
        bundle_identifier="com.nickwolf.tokitty",
    )
```

- [ ] **Step 7: prove the sharing on Linux (ext4).**

```bash
python3 -m venv /tmp/tokitty-freeze/venv
/tmp/tokitty-freeze/venv/bin/pip install -e ".[packaging]"
/tmp/tokitty-freeze/venv/bin/python -m PyInstaller freeze/tokitty.spec --noconfirm \
  --distpath /tmp/tokitty-freeze/dist --workpath /tmp/tokitty-freeze/work
ls /tmp/tokitty-freeze/dist/tokitty
find /tmp/tokitty-freeze/dist -maxdepth 3 -name _internal -type d
find /tmp/tokitty-freeze/dist -name 'libpython*' -o -name 'base_library.zip'
du -sb /tmp/tokitty-freeze/dist/tokitty
```

Expected: `tokitty`, `tokitty-hook`, `_internal` at the top; one `_internal`; one libpython and one `base_library.zip`. Then build a GUI-only copy (temporarily comment the hook pieces out of a scratch copy of the spec under `/tmp`, not the repo) and `du -sb` it. Record both sizes in the task report.

- [ ] **Step 8: run the hook exe.**

```bash
D=/tmp/tokitty-freeze/dist/tokitty; S=/tmp/tokitty-freeze/sessions
echo '{"session_id":"t1","hook_event_name":"PreToolUse","tool_name":"Bash"}' | "$D/tokitty-hook" --sessions-dir "$S"; echo "rc=$?"; cat "$S/t1.json"
echo 'garbage' | "$D/tokitty-hook" --sessions-dir "$S" | wc -c; echo "rc=${PIPESTATUS[1]}"
"$D/tokitty-hook" </dev/null | wc -c
touch /tmp/tokitty-freeze/blocker; echo '{"session_id":"t2","hook_event_name":"Stop"}' | "$D/tokitty-hook" --sessions-dir /tmp/tokitty-freeze/blocker/x | wc -c
```

Expected: rc=0, state file with `"seq": 1`, and `0` bytes of stdout for every case.

- [ ] **Step 9: data files reach disk.** Scratch install through the built GUI exe:

```bash
X=/tmp/tokitty-freeze/scratch; rm -rf "$X"; mkdir -p "$X/home" "$X/xdg/tokitty" "$X/claude-home"
printf '{"accounts":[{"name":"ci","config_dir":"%s","provider":"claude"}]}' "$X/claude-home" > "$X/xdg/tokitty/accounts.json"
HOME="$X/home" XDG_CONFIG_HOME="$X/xdg" timeout 60 "$D/tokitty" --install-hooks; echo "rc=$?"
cmp "$X/claude-home/tokitty/hook_writer.py" tokitty/hook_writer.py && echo IDENTICAL
```

Expected: rc=0, `IDENTICAL`. (Before Tasks 2 and 3 the registered command is still `python3 ...`; that is expected here.)

- [ ] **Step 10: ignore build output.** Add `/build/` and `/dist/` to `.gitignore` if not already covered. Commit:

```bash
git add freeze/ tests/test_freeze_spec.py pyproject.toml .gitignore
git commit -m "Add a PyInstaller spec that builds the app and a hook runner"
```

---

### Task 2: Hook command builder

**Goal:** `_build_command` returns the hook dict to register, following the table in spec Q2.

**Files:**
- Modify: `tokitty/hooks_install.py` (`_build_command`, new `_is_wsl_unc`, `hook_runner_path`, `stable_runner_path`, `HOOK_RUNNER_NAME`; the one caller in `install_hooks_for_dir`)
- Test: `tests/test_hooks_install.py`

**Acceptance Criteria:**
- [ ] Source install, any home: `{"type": "command", "command": "<python3|python> \"<native>/tokitty/hook_writer.py\" --sessions-dir \"<native>/tokitty/sessions\""}`, no `args` key. Byte-identical to today's string.
- [ ] Frozen on win32, `\\wsl.localhost\...` or `\\wsl$\...` home: the same python3 string as source.
- [ ] Frozen, same-OS home: `{"type": "command", "command": <runner>, "args": ["--sessions-dir", "<native>/tokitty/sessions"]}`, where `<runner>` defaults to `stable_runner_path(state_dir_path(), platform)` = `<state dir>/current/tokitty-hook[.exe]` (spec Q2a). Task 3 makes that link exist and passes its own result in as `runner`.
- [ ] `hook_runner_path(executable, platform)` returns the bundled runner beside a frozen executable (`tokitty-hook.exe` on win32, `tokitty-hook` elsewhere); Task 3 and `--self-check` use it.
- [ ] `frozen`, `platform`, `runner` are keyword overrides defaulting to `sys.frozen`, `sys.platform`, and the stable path, like `autostart.resolve_launch_command`, so every row is testable on every CI OS.
- [ ] Trailing separators on the home are stripped before joining (`/home/n/.claude/` gives `/home/n/.claude/tokitty/sessions`, not `//tokitty`), tested for a POSIX and a `C:\` home. A home without a trailing separator produces exactly today's string.
- [ ] The installed entry is `{"matcher": m, "hooks": [<the dict>]}`.
- [ ] The existing test at `tests/test_hooks_install.py:190-208` that calls `_is_tokitty_entry` is left for Task 4, which changes that signature and updates the call.

**Verify:** `python3 -m pytest -q tests/test_hooks_install.py` → pass.

**Steps:**

- [ ] **Step 1: failing tests** (append to `tests/test_hooks_install.py`):

```python
WIN_RUNNER = r"C:\Users\nick\AppData\Local\Tokitty\current\tokitty-hook.exe"


def test_build_command_source_posix_unchanged():
    hook = hi._build_command("/home/nick/.claude", frozen=False, platform="linux")
    assert hook == {
        "type": "command",
        "command": 'python3 "/home/nick/.claude/tokitty/hook_writer.py" --sessions-dir "/home/nick/.claude/tokitty/sessions"',
    }


def test_build_command_source_windows_local_uses_python():
    hook = hi._build_command(r"C:\Users\nick\.claude", frozen=False, platform="win32")
    assert hook["command"].startswith('python "C:\\Users\\nick\\.claude/tokitty/hook_writer.py"')
    assert "args" not in hook


def test_build_command_frozen_windows_wsl_home_keeps_python3():
    hook = hi._build_command(r"\\wsl.localhost\Ubuntu\home\nick\.claude", frozen=True, runner=WIN_RUNNER, platform="win32")
    assert hook == {
        "type": "command",
        "command": 'python3 "/home/nick/.claude/tokitty/hook_writer.py" --sessions-dir "/home/nick/.claude/tokitty/sessions"',
    }


def test_build_command_frozen_windows_wsl_dollar_home_keeps_python3():
    hook = hi._build_command(r"\\wsl$\Ubuntu\home\nick\.claude", frozen=True, runner=WIN_RUNNER, platform="win32")
    assert hook["command"].startswith("python3 ")
    assert "args" not in hook


def test_build_command_frozen_windows_local_home_uses_exec_form():
    hook = hi._build_command(r"C:\Users\nick\.claude", frozen=True, runner=WIN_RUNNER, platform="win32")
    assert hook == {
        "type": "command",
        "command": WIN_RUNNER,
        "args": ["--sessions-dir", "C:\\Users\\nick\\.claude/tokitty/sessions"],
    }


def test_build_command_frozen_linux_uses_exec_form():
    hook = hi._build_command("/home/nick/.claude", frozen=True, runner="/home/nick/.config/tokitty/current/tokitty-hook", platform="linux")
    assert hook == {
        "type": "command",
        "command": "/home/nick/.config/tokitty/current/tokitty-hook",
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions"],
    }


def test_hook_runner_path_sits_beside_executable():
    assert hi.hook_runner_path("/Applications/Tokitty.app/Contents/MacOS/Tokitty", "darwin") == "/Applications/Tokitty.app/Contents/MacOS/tokitty-hook"
    assert hi.hook_runner_path(r"C:\T\Tokitty.exe", "win32") == r"C:\T\tokitty-hook.exe"


def test_stable_runner_path():
    assert hi.stable_runner_path(Path("/home/n/.config/tokitty"), "linux") == str(Path("/home/n/.config/tokitty") / "current" / "tokitty-hook")
    assert hi.stable_runner_path(r"C:\Users\n\AppData\Local\Tokitty", "win32") == r"C:\Users\n\AppData\Local\Tokitty\current\tokitty-hook.exe"


def test_build_command_default_runner_is_stable_path(tmp_path, monkeypatch):
    monkeypatch.setattr(hi, "state_dir_path", lambda: tmp_path)
    hook = hi._build_command("/home/nick/.claude", frozen=True, platform="linux")
    assert hook["command"] == str(tmp_path / "current" / "tokitty-hook")


def test_install_writes_exec_form_entry_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi, "state_dir_path", lambda: tmp_path / "state")
    home = tmp_path / "home"
    result = hi.install_hooks_for_dir(str(home))
    assert result.ok
    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["args"][0] == "--sessions-dir"
    assert Path(hook["command"]).name in ("tokitty-hook", "tokitty-hook.exe")
```

Run: fail (`_build_command` takes no keywords / returns a string).

- [ ] **Step 2: implement** in `hooks_install.py` (add `from pathlib import PurePosixPath, PureWindowsPath`):

```python
HOOK_RUNNER_NAME = "tokitty-hook"

_WSL_UNC_PREFIXES = ("\\\\wsl.localhost\\", "\\\\wsl$\\")


def _is_wsl_unc(config_dir: str) -> bool:
    normalized = config_dir.replace("/", "\\").lower()
    return normalized.startswith(_WSL_UNC_PREFIXES)


def hook_runner_path(executable: str, platform: str) -> str:
    """The bundled tokitty-hook beside a frozen build's own executable."""
    if platform == "win32":
        return str(PureWindowsPath(executable).with_name(HOOK_RUNNER_NAME + ".exe"))
    return str(PurePosixPath(executable).with_name(HOOK_RUNNER_NAME))


def stable_runner_path(state_dir, platform: str) -> str:
    """Where hooks run tokitty-hook from: a link in the state dir that each
    launch repoints at the running release, so the registered command never
    changes (spec Q2a)."""
    if platform == "win32":
        return str(PureWindowsPath(str(state_dir)) / "current" / (HOOK_RUNNER_NAME + ".exe"))
    return str(Path(state_dir) / "current" / HOOK_RUNNER_NAME)


def _build_command(config_dir: str, *, frozen=None, platform=None, runner=None) -> dict:
    """The hook to register, chosen by where Claude Code runs (spec Q2).

    A frozen build registers tokitty-hook in exec form (Claude Code
    2.1.139+), except for a WSL home seen from Windows, which keeps python3:
    WSL ships it and it is twice as fast as the exe through interop.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    native = _wsl_native_path(config_dir).rstrip("/\\") or _wsl_native_path(config_dir)
    sessions_dir = f"{native}/tokitty/sessions"
    if frozen and not (platform == "win32" and _is_wsl_unc(config_dir)):
        return {
            "type": "command",
            "command": runner if runner is not None else stable_runner_path(state_dir_path(), platform),
            "args": ["--sessions-dir", sessions_dir],
        }
    interpreter = "python" if _is_windows_local_path(config_dir) else "python3"
    return {
        "type": "command",
        "command": f'{interpreter} "{native}/tokitty/hook_writer.py" --sessions-dir "{sessions_dir}"',
    }
```

Import `state_dir_path` from `tokitty.paths` at module level (tests patch `hi.state_dir_path`). Make `_wsl_native_path` use `_WSL_UNC_PREFIXES` instead of its inline tuple. In `install_hooks_for_dir` change `command = _build_command(config_dir)` to `hook = _build_command(config_dir)` and append `{"matcher": matcher, "hooks": [dict(hook)]}`. Update any existing test that compared `_build_command(...)` to a string to compare `["command"]`.

- [ ] **Step 3:** run the new tests and the full suite. `_is_tokitty_entry` still matches exec form here because the marker `tokitty` is in the `tokitty-hook` path; Task 4 replaces it.

- [ ] **Step 4: commit** `git commit -am "Register tokitty-hook in exec form from a frozen build"` (add new test lines too).

---

### Task 3: Stable hook path

**Goal:** `<state dir>/current` links to the running release, and frozen hooks register `<state dir>/current/tokitty-hook[.exe]`, so the registered command never changes across releases or moves (spec Q2a).

**Files:**
- Create: `tokitty/runner_link.py`, `tokitty/frozen.py` (translocation part only; Tasks 5 and 6 extend it)
- Modify: `tokitty/hooks_install.py` (`install_hooks_for_dir` passes the link's runner to `_build_command`), `tokitty/__main__.py` (`run_discovery`)
- Test: `tests/test_runner_link.py`, `tests/test_frozen.py`, `tests/test_hooks_install.py`

**Acceptance Criteria:**
- [ ] `tokitty/frozen.py` has `MOVE_TO_APPLICATIONS`, `AppTranslocatedError(OSError)` and `is_translocated(executable=None)` (true iff the path, with `\` normalised to `/`, contains `/AppTranslocation/`), as in Step 2. Task 5 uses them for autostart.
- [ ] `runner_link.ensure_runner_link(state_dir, *, executable=None, platform=None) -> LinkOutcome` (a NamedTuple `runner: str`, `note: Optional[str]`):
  - resolves `os.path.realpath(executable)` first (on Windows a launch through the junction reports the junction path as `sys.executable`, spec Q2a), release = its parent, bundled = `hooks_install.hook_runner_path(resolved, platform)`;
  - raises `AppTranslocatedError` if the given or resolved executable is translocated, touching nothing;
  - raises `FileNotFoundError` if `bundled` is not a file, touching nothing (a link is never made to a folder without the runner);
  - if `<state dir>/current` exists and is not a link (symlink, or on Windows a junction): leaves it alone and returns `LinkOutcome(bundled, "<state dir>/current is not a link Tokitty made, so hooks use this release's own path")`;
  - if it is a link that already resolves to `release`: returns `LinkOutcome(stable_runner_path(state_dir, platform), None)` without writing;
  - otherwise repoints it and returns the stable path. POSIX: `os.symlink(release, tmp, target_is_directory=True)` to a sibling temp name, then `os.replace(tmp, link)`. Windows: remember the old target (`os.readlink` or `realpath`), `os.rmdir(link)` if present, then `_winapi.CreateJunction(release, link)`; if that raises, recreate the junction to the old target before re-raising, so a failed repoint never leaves registered hooks pointing nowhere.
  - the whole check-and-repoint runs under a cross-process lock, `lock.SingleInstanceLock(state_dir, name="current.lock")`, retried every 50 ms for up to 5 s, so a GUI launch and a CLI `--install-hooks` cannot race (the GUI's own single-instance lock does not cover the CLI path, `__main__.py:976-996`). The link-or-real-directory check is redone after the lock is taken, immediately before any removal. Lock timeout raises `OSError`.
  - never calls `shutil.rmtree`, and never points the link at itself or inside itself (guard: refuse if `release` equals or sits under the unresolved link path).
- [ ] `_is_link(path)` is true for a symlink, or on Windows for a directory junction specifically (`os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT`), not any reparse point, and works on Python 3.10.
- [ ] In `hooks_install`, when the desired hook is exec form (frozen, not the WSL row), `install_hooks_for_dir` calls `ensure_runner_link(state_dir_path())` first and passes `outcome.runner` to `_build_command`. `AppTranslocatedError` becomes `ConfigDirResult(config_dir, False, MOVE_TO_APPLICATIONS)` with nothing written. Any other `OSError` (lock timeout, failed repoint) becomes `ok=False` with the reason and nothing written: a transient failure must never rewrite a stable registration to a release-specific path, which would change the string Codex hashes. Only the real-directory case falls back to the bundled absolute path, and its `outcome.note` is appended to the message.
- [ ] `run_discovery` calls `runner_link.ensure_runner_link(state_dir)` when `sys.frozen`, before `retry_pending_hook_op`, in its own `try/except OSError` (which covers `AppTranslocatedError`) so a link failure does not skip the retry. Task 4 Step 6 shows the combined block. A `tests/test_main.py` test drives `run_discovery` with `sys.frozen` patched and a fake release, and asserts the link is repointed with no `--install-hooks` involved.
- [ ] Task 2's `test_install_writes_exec_form_entry_when_frozen` is changed to build a fake release in `tmp_path` with host-native names (`tokitty.exe`/`tokitty-hook.exe` on Windows) and patch `sys.executable` to it, so no test ever links to a real directory. Tests that create links never patch `sys.platform`; platform overrides stay in the pure command-builder tests.
- [ ] Tests also cover: a repoint whose `CreateJunction`/`symlink` is monkeypatched to raise leaves the old link target in place; the lock is held during repoint (a second caller with the lock already taken times out with `OSError`, use a short timeout override).
- [ ] Tests (`tests/test_runner_link.py`, using real links for the host OS, `platform=sys.platform`):
  - first launch creates the link to release A and returns the stable path;
  - **two simulated releases**: the runner path returned for A and for B is identical, the link ends at B, and a recursive listing (names and sizes) of both release folders is unchanged;
  - **move**: `os.rename` release A to a new folder, relaunch from there, the link is repointed to the new folder;
  - launching through the link itself (`executable=<state>/current/tokitty`) keeps the link on the real release, no self-loop;
  - a real directory at `current` is left untouched and the bundled path returned with a note;
  - a translocated executable (a fake release under a `tmp_path/.../AppTranslocation/...` folder) raises and leaves an existing link unchanged;
  - a release without `tokitty-hook` raises `FileNotFoundError` and creates nothing;
  - `hooks_install`: install from release A, then `refresh_hooks_for_dir` from release B: `settings.json` bytes identical, link at B.
  - Every test removes links it made with `os.unlink` (POSIX) or `os.rmdir` (Windows junction) in a fixture teardown, before pytest cleans `tmp_path`.
- [ ] Junction tests run for real on the Windows CI legs and symlink tests elsewhere; no test spoofs `sys.platform` to exercise link creation.

**Verify:** `python3 -m pytest -q tests/test_runner_link.py tests/test_frozen.py tests/test_hooks_install.py` → pass; full suite green.

**Steps:**

- [ ] **Step 1: failing tests.** `tests/test_runner_link.py` core (write the remaining criteria in the same shape):

```python
import os
import sys

import pytest

from tokitty import runner_link
from tokitty.frozen import AppTranslocatedError

EXE = "tokitty.exe" if sys.platform == "win32" else "tokitty"
RUNNER = "tokitty-hook.exe" if sys.platform == "win32" else "tokitty-hook"


def _release(root):
    root.mkdir(parents=True)
    (root / EXE).write_text("gui", encoding="utf-8")
    (root / RUNNER).write_text("hook", encoding="utf-8")
    (root / "_internal").mkdir()
    (root / "_internal" / "marker").write_text("x", encoding="utf-8")
    return root / EXE


def _listing(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file())


@pytest.fixture
def state(tmp_path):
    state_dir = tmp_path / "state"
    yield state_dir
    link = state_dir / "current"
    if runner_link._is_link(str(link)):
        (os.rmdir if sys.platform == "win32" else os.unlink)(link)


def test_stable_path_identical_across_two_releases(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    b = _release(tmp_path / "rel-b")
    before = (_listing(a.parent), _listing(b.parent))
    first = runner_link.ensure_runner_link(state, executable=str(a))
    second = runner_link.ensure_runner_link(state, executable=str(b))
    assert first.runner == second.runner
    assert os.path.realpath(state / "current") == os.path.realpath(b.parent)
    assert (_listing(a.parent), _listing(b.parent)) == before


def test_moved_release_is_repointed(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    runner_link.ensure_runner_link(state, executable=str(a))
    moved = tmp_path / "moved"
    os.rename(a.parent, moved)
    runner_link.ensure_runner_link(state, executable=str(moved / EXE))
    assert os.path.realpath(state / "current") == os.path.realpath(moved)


def test_launch_through_link_keeps_real_target(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    runner_link.ensure_runner_link(state, executable=str(a))
    runner_link.ensure_runner_link(state, executable=str(state / "current" / EXE))
    assert os.path.realpath(state / "current") == os.path.realpath(a.parent)


def test_real_directory_left_alone(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    (state / "current").mkdir(parents=True)
    (state / "current" / "keep.txt").write_text("mine", encoding="utf-8")
    outcome = runner_link.ensure_runner_link(state, executable=str(a))
    assert outcome.runner == str(a.parent / RUNNER) and outcome.note
    assert (state / "current" / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_translocated_leaves_link_alone(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    runner_link.ensure_runner_link(state, executable=str(a))
    t = _release(tmp_path / "private" / "AppTranslocation" / "x" / "rel")
    with pytest.raises(AppTranslocatedError):
        runner_link.ensure_runner_link(state, executable=str(t))
    assert os.path.realpath(state / "current") == os.path.realpath(a.parent)
```

Note: `is_translocated` matches `/AppTranslocation/`; on Windows `tmp_path` uses backslashes, so the implementation checks the path with `\` normalised to `/`.

- [ ] **Step 2: implement** `tokitty/frozen.py`:

```python
"""Support for the PyInstaller build (#48)."""
from __future__ import annotations

import sys
from typing import Optional

MOVE_TO_APPLICATIONS = (
    "Tokitty is running from a temporary copy macOS makes of downloaded apps. "
    "Move Tokitty to your Applications folder, open it from there, and try again."
)


class AppTranslocatedError(OSError):
    def __init__(self):
        super().__init__(MOVE_TO_APPLICATIONS)

    def __str__(self):
        return MOVE_TO_APPLICATIONS


def is_translocated(executable: Optional[str] = None) -> bool:
    """macOS runs a quarantined app from a random read-only path that vanishes;
    nothing may be registered against it."""
    executable = sys.executable if executable is None else executable
    return "/AppTranslocation/" in executable.replace("\\", "/")
```

Then `tokitty/runner_link.py` per the criteria. Keep it stdlib; import `_winapi` lazily inside the Windows branch.
- [ ] **Step 3:** wire `hooks_install` and `run_discovery`, update Task 2's frozen install test, run the full suite. Rebuild on ext4 (Task 1 Step 7 command), then from a scratch env run the built `--install-hooks` twice, from two copies of `dist/tokitty` in different `/tmp` folders. Confirm `settings.json` is byte-identical after the second run and `current` points at the second copy. Record the output.
- [ ] **Step 4: commit** `git commit -m "Register tokitty-hook through a link the app repoints at launch"`.

---

### Task 4: Hook ownership and refresh

> **BLOCKED, re-plan before executing (2026-09-24).** The hooks groundwork PR (from #64, branched from main) now owns the provider plumbing, per-provider exact ownership matching, the hook file chosen by provider, and handler-only uninstall. Do not implement the ownership matcher, `_is_tokitty_entry`, or the uninstall narrowing below. Once that PR merges: merge main into this branch (`git merge --no-ff main`, resolving against Tasks 2 and 3 in `hooks_install.py`), then rewrite this task against the merged API. It keeps only #48's own parts: (1) add the exec-form shape (command ending in `tokitty-hook[.exe]`, `args` `["--sessions-dir", <dir>/tokitty/sessions]`) to the groundwork's Claude Code matcher, bound to the home; (2) the in-place rewrite and `refresh_hooks_for_dir`/`ensure_current`, dispatched per provider from the start, with only the Claude Code branch built (#64 adds Codex); (3) the order rule, where the link is made before any write on all three paths (CLI, Accounts dialog, startup refresh); (4) the never-silent fallback from spec Q2a's addendum, with a `warning` on the result that the CLI prints, the Accounts dialog shows with `messagebox.showwarning` on an ok outcome, and the startup refresh shows once per launch, saying every update will need hook approval again in Codex; an existing stable-path hook is never rewritten to a release path; (5) the equivalent-spelling and duplicate-collapse rules and their tests. The body below is the pre-groundwork draft, kept for reference.

**Goal:** Only hooks Tokitty wrote count as Tokitty's; install and a new startup refresh rewrite stale owned hooks in place, and refresh never adds one.

**Files:**
- Modify: `tokitty/hooks_install.py`, `tokitty/__main__.py` (`run_discovery`)
- Test: `tests/test_hooks_install.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `_is_owned_hook(hook, config_dir)` is true only for a dict with `type == "command"` that is one of: the quoted legacy string `python|python3 "<d>/tokitty/hook_writer.py" --sessions-dir "<d>/tokitty/sessions"`; the unquoted legacy string (same with no quotes, written 2026-07-16 to 07-18); exec form whose `command` basename is `tokitty-hook` or `tokitty-hook.exe` (case-insensitive) with `args == ["--sessions-dir", s]`. In every shape the sessions dir must be this home's own: `<d>` or `s` must equal `_wsl_native_path(config_dir)` + `/tokitty/sessions` after `\` to `/`, trailing-slash stripping, and (for drive-letter paths) case folding. A lookalike aimed at another home is not owned. `_is_tokitty_entry(entry, config_dir)` is true iff any hook in it is owned. Every caller passes `config_dir`.
- [ ] A user hook `python3 /opt/tokitty/myhook.py` and `bash ~/tokitty-scripts/run.sh` are not owned, are never rewritten or removed.
- [ ] Install, per event in `HOOK_EVENTS`: owned hook in `settings.local.json` counts as installed and is never duplicated into `settings.json` (a stale one is reported in the message), and any owned hook for that event in `settings.json` is removed (user hooks kept), so the event never fires twice; owned hook in `settings.json` matching the desired `command`/`args` is a no-op; owned but different is rewritten in place keeping the entry's `matcher`, the hook's other keys (e.g. `timeout`), and neighbouring hooks; further owned hooks in the same event are removed (an entry left with no hooks is dropped); no owned hook means a new entry is appended.
- [ ] `settings.json` is backed up before any write and written only if something changed. `hook_writer.py` is copied on every install, as today.
- [ ] `refresh_hooks_for_dir(config_dir)` does the same reconcile but never appends. With no owned hook in either file it writes nothing at all (no copy, no mkdir). With owned hooks it copies `hook_writer.py`.
- [ ] `ensure_current(state_dir=None, refresh_fn=refresh_hooks_for_dir) -> List[ConfigDirResult]` runs refresh over `get_config_dirs(state_dir)`, turning an `OSError` for one dir into a failed result and continuing.
- [ ] `get_config_dirs` takes an optional `state_dir` (default `get_state_dir()`).
- [ ] Uninstall removes only owned hooks, dropping an entry only if it ends up with no hooks, and an event only if it ends up with no entries.
- [ ] `run_discovery` calls `hooks_install.ensure_current(state_dir)` right after `retry_pending_hook_op`, inside the same OSError guard, off the Tk thread.
- [ ] Equivalent spellings: a hook written for `C:\Users\Nick\.claude` is owned and left byte-identical when the account says `c:\users\nick\.claude`; a hook written with a doubled slash (`/home/n/.claude//tokitty/sessions`, from a trailing-slash home) is owned and left as written. Tests for both.
- [ ] Update the existing call at `tests/test_hooks_install.py:190-208` to the two-argument `_is_tokitty_entry(entry, config_dir)`.
- [ ] The two existing fixtures that use `python3 x/tokitty/hook_writer.py` with no `--sessions-dir` (`test_install_skips_event_already_marked_in_settings_local`, `test_uninstall_leaves_settings_local_alone_but_reports`) switch to a command Tokitty really wrote for that home; a separate test proves the partial lookalike is not owned.
- [ ] Tests from spec Task 4 all present: python to exe; an old absolute-path exec hook to the stable path; identical is a no-op (file mtime and content unchanged, no backup file created); user hook containing "tokitty" left alone; mixed entry keeps the user hook; `--uninstall-hooks` then `ensure_current` stays uninstalled; stale owned entry in `settings.local.json` reported and not duplicated. Plus: unquoted legacy form is owned and gets rewritten; duplicate owned hooks collapse to one; `timeout` key preserved on rewrite; uninstall of a mixed entry keeps the user hook.

**Verify:** `python3 -m pytest -q tests/test_hooks_install.py tests/test_main.py` → pass.

**Steps:**

- [ ] **Step 1: failing tests.** Add to `tests/test_hooks_install.py` (helpers first, then one test per criterion). Core helpers and representative tests; write the rest in the same shape:

```python
def py_hook(home, quoted=True):
    q = '"' if quoted else ""
    return {"type": "command", "command": f"python3 {q}{home}/tokitty/hook_writer.py{q} --sessions-dir {q}{home}/tokitty/sessions{q}"}


def exe_hook(home, runner="/old/release/tokitty-hook"):
    return {"type": "command", "command": runner, "args": ["--sessions-dir", f"{home}/tokitty/sessions"]}


USER_HOOK = {"type": "command", "command": "python3 /opt/tokitty/myhook.py"}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _frozen(monkeypatch, tmp_path):
    """A frozen build for the host OS whose release folder and state dir live
    in tmp_path. Links are only ever created inside tmp_path, pointing at a
    fake release. sys.platform is never patched here: link creation is real."""
    win = sys.platform == "win32"
    release = tmp_path / "release"
    release.mkdir(exist_ok=True)
    (release / ("tokitty-hook.exe" if win else "tokitty-hook")).write_text("", encoding="utf-8")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(release / ("tokitty.exe" if win else "tokitty")))
    monkeypatch.setattr(hi, "state_dir_path", lambda: tmp_path / "state")
    return hi.stable_runner_path(tmp_path / "state", sys.platform)


def _full_settings(hook):
    return {"hooks": {ev: [{"matcher": m, "hooks": [dict(hook)]}] for ev, m in hi.HOOK_EVENTS}}


def test_owned_shapes():
    home = "/h"
    assert hi._is_owned_hook(py_hook(home), home)
    assert hi._is_owned_hook(py_hook(home, quoted=False), home)
    assert hi._is_owned_hook(exe_hook(home), home)
    assert hi._is_owned_hook({"type": "command", "command": r"C:\T\TOKITTY-HOOK.EXE",
                              "args": ["--sessions-dir", "C:\\h/tokitty/sessions"]}, "C:\\h")
    assert not hi._is_owned_hook(USER_HOOK, home)
    assert not hi._is_owned_hook({"type": "command", "command": "bash ~/tokitty-scripts/run.sh"}, home)
    assert not hi._is_owned_hook({"type": "command", "command": "/x/tokitty-hook", "args": ["--other"]}, home)
    assert not hi._is_owned_hook({"type": "command", "command": "python3 x/tokitty/hook_writer.py"}, home)
    assert not hi._is_owned_hook(py_hook("/other-home"), home)
    assert not hi._is_owned_hook(exe_hook("/other-home"), home)
    assert not hi._is_owned_hook(dict(py_hook(home), type="prompt"), home)


def test_refresh_python_to_stable_exe(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _write(home / "settings.json", _full_settings(py_hook(home)))
    runner = _frozen(monkeypatch, tmp_path)
    result = hi.refresh_hooks_for_dir(str(home))
    assert result.ok and set(result.refreshed_events) == {e for e, _ in hi.HOOK_EVENTS}
    hook = _read(home / "settings.json")["hooks"]["Stop"][0]["hooks"][0]
    assert hook == {"type": "command", "command": runner, "args": ["--sessions-dir", f"{home}/tokitty/sessions"]}
    assert (home / "tokitty" / "hook_writer.py").is_file()


def test_refresh_old_absolute_exe_to_stable_path(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _write(home / "settings.json", _full_settings(exe_hook(home)))
    runner = _frozen(monkeypatch, tmp_path)
    hi.refresh_hooks_for_dir(str(home))
    assert _read(home / "settings.json")["hooks"]["Stop"][0]["hooks"][0]["command"] == runner


def test_refresh_identical_is_noop(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _frozen(monkeypatch, tmp_path)
    _write(home / "settings.json", _full_settings(hi._build_command(str(home))))
    before = (home / "settings.json").read_bytes()
    result = hi.refresh_hooks_for_dir(str(home))
    assert result.ok and result.refreshed_events == []
    assert (home / "settings.json").read_bytes() == before
    assert not list(home.glob("settings.json.tokitty-backup-*"))


def test_refresh_never_adds(tmp_path):
    home = tmp_path / "h"
    _write(home / "settings.json", {"hooks": {}})
    result = hi.refresh_hooks_for_dir(str(home))
    assert result.ok
    assert _read(home / "settings.json") == {"hooks": {}}
    assert not (home / "tokitty").exists()


def test_uninstall_then_ensure_current_stays_uninstalled(tmp_path):
    home = tmp_path / "h"
    state = tmp_path / "state"
    _write(state / "accounts.json", {"accounts": [{"name": "a", "config_dir": str(home), "provider": "claude"}]})
    assert hi.install_hooks_for_dir(str(home)).ok
    assert hi.uninstall_hooks_for_dir(str(home)).ok
    hi.ensure_current(state)
    assert not hi._events_with_tokitty_entries(_read(home / "settings.json"), str(home))


def test_mixed_entry_keeps_user_hook_on_refresh_and_uninstall(tmp_path, monkeypatch):
    home = tmp_path / "h"
    timed = dict(py_hook(home), timeout=5)
    _write(home / "settings.json", {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [USER_HOOK, timed]}]}})
    runner = _frozen(monkeypatch, tmp_path)
    hi.refresh_hooks_for_dir(str(home))
    entry = _read(home / "settings.json")["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0] == USER_HOOK
    assert entry["hooks"][1]["command"] == runner and entry["hooks"][1]["timeout"] == 5
    hi.uninstall_hooks_for_dir(str(home))
    assert _read(home / "settings.json")["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [USER_HOOK]}]


def test_stale_local_entry_reported_not_duplicated(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _write(home / "settings.local.json", {"hooks": {"Stop": [{"matcher": "", "hooks": [py_hook(home)]}]}})
    _write(home / "settings.json", {"hooks": {"Stop": [{"matcher": "", "hooks": [USER_HOOK, py_hook(home)]}]}})
    _frozen(monkeypatch, tmp_path)
    result = hi.install_hooks_for_dir(str(home))
    assert result.ok
    assert _read(home / "settings.json")["hooks"]["Stop"] == [{"matcher": "", "hooks": [USER_HOOK]}]
    assert "settings.local.json" in result.message
    assert _read(home / "settings.local.json")["hooks"]["Stop"][0]["hooks"][0] == py_hook(home)
```

Also: unquoted legacy rewritten, duplicates collapse (one event with `[py_hook]` and `[exe_hook]` entries ends with exactly one owned hook), user-only hook untouched by install/uninstall, a lookalike for another home untouched by install/refresh/uninstall, `ensure_current` turns `OSError` from `refresh_fn` into `ok=False` and continues to the next dir. In `tests/test_main.py`, extend the existing `run_discovery` test pattern to assert `hooks_install.ensure_current` is called with `state_dir` after `retry_pending_hook_op` and that an `OSError` from it is swallowed.

- [ ] **Step 2: implement ownership** (`import re`):

```python
_LEGACY_COMMAND = re.compile(
    r'^python3? (?P<q>"?)(?P<dir>.+)/tokitty/hook_writer\.py(?P=q)'
    r' --sessions-dir (?P=q)(?P=dir)/tokitty/sessions(?P=q)$'
)
_RUNNER_NAMES = (HOOK_RUNNER_NAME, HOOK_RUNNER_NAME + ".exe")


def _norm_dir(path: str) -> str:
    path = re.sub(r"/{2,}", "/", path.replace("\\", "/")).rstrip("/")
    return path.lower() if _is_windows_local_path(path) else path


def _is_owned_hook(hook, config_dir: str) -> bool:
    """Only the hook shapes tokitty itself has written for this home, never a
    substring match."""
    if not isinstance(hook, dict) or hook.get("type") != "command" or not isinstance(hook.get("command"), str):
        return False
    home = _norm_dir(_wsl_native_path(config_dir))
    command = hook["command"]
    args = hook.get("args")
    if args is None:
        m = _LEGACY_COMMAND.match(command)
        return bool(m) and _norm_dir(m.group("dir")) == home
    name = command.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return (
        name in _RUNNER_NAMES
        and isinstance(args, list)
        and len(args) == 2
        and args[0] == "--sessions-dir"
        and isinstance(args[1], str)
        and _norm_dir(args[1]) == home + "/tokitty/sessions"
    )


def _is_tokitty_entry(entry, config_dir: str) -> bool:
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    return isinstance(hooks, list) and any(_is_owned_hook(h, config_dir) for h in hooks)
```

`_events_with_tokitty_entries(data, config_dir)` takes the home too. Delete `MARKER` if nothing else uses it (`grep -rn MARKER tokitty tests`).

- [ ] **Step 3: implement reconcile.** Replace the body of `install_hooks_for_dir` with a shared `_reconcile(config_dir, add_missing)`; `install_hooks_for_dir = lambda d: _reconcile(d, True)` as a real `def`, and `refresh_hooks_for_dir(d)` as `_reconcile(d, False)`. Add `refreshed_events` to `ConfigDirResult` (default `[]`). Outline:

```python
def _same_hook(hook: dict, desired: dict) -> bool:
    """Equal, or equal up to how the home is spelled (case of a drive-letter
    path, doubled or trailing separators). An equivalent spelling is kept as
    written: rewriting it would change the command string Codex hashes."""
    if hook.get("command") != desired["command"]:
        return False
    args, want = hook.get("args"), desired.get("args")
    if args is None or want is None:
        return args == want
    return len(args) == len(want) and all(
        a == w or (isinstance(a, str) and _norm_dir(a) == _norm_dir(w)) for a, w in zip(args, want)
    )


def _rewritten(hook: dict, desired: dict) -> dict:
    new = dict(hook)
    new["command"] = desired["command"]
    if "args" in desired:
        new["args"] = list(desired["args"])
    else:
        new.pop("args", None)
    return new


def _owned_in_file(data) -> dict:
    """event -> list of owned hooks, over one settings file."""


def _reconcile(config_dir: str, add_missing: bool) -> ConfigDirResult:
    # 1. load settings.json / settings.local.json with the same parse-error
    #    aborts as today; validate hooks is a dict and each HOOK_EVENTS
    #    event's entries a list, with today's abort messages.
    # 2. desired = _build_command(config_dir)
    # 3. local_owned = events with an owned hook in settings.local.json;
    #    stale_local = sorted(e for e in local_owned if any owned hook there
    #    is not _same_hook(desired)).
    # 4. owned_main = any owned hook in settings.json. If not add_missing and
    #    not owned_main and not local_owned: return ok, "no tokitty hooks
    #    found", touching nothing.
    # 5. copy hook_writer.py (mkdir parents) as today.
    # 6. for event, matcher in HOOK_EVENTS: if event in local_owned, remove
    #    every owned hook for it from settings.json (keep user hooks, drop
    #    emptied entries) and move on. Otherwise walk entries/hooks; first
    #    owned hook: rewrite if not _same_hook
    #    (record refreshed); later owned hooks: remove, drop emptied entries
    #    (record refreshed); none found and add_missing: append
    #    {"matcher": matcher, "hooks": [dict(desired)]} (record installed).
    # 7. if anything changed: _backup(settings_path); _write_settings(...)
    # 8. message: "installed" if installed, else "refreshed" if refreshed,
    #    else "already installed, nothing to do"; append
    #    f" (note: out-of-date tokitty hook in settings.local.json for {', '.join(stale_local)} left untouched, update it by hand)"
    #    when stale_local.
```

The implementer writes this out in full; every branch above has a test from Step 1.

- [ ] **Step 4: uninstall narrowing.** In `uninstall_hooks_for_dir`, replace `kept = [e for e in entries if not _is_tokitty_entry(e)]` with per-hook filtering: for each entry dict, drop owned hooks; keep the entry (with its other keys) if any hooks remain. An event counts as removed if any owned hook was dropped.

- [ ] **Step 5: ensure_current and get_config_dirs.**

```python
def get_config_dirs(state_dir: Optional[Path] = None) -> List[str]:
    state_dir = get_state_dir() if state_dir is None else Path(state_dir)
    ...  # rest unchanged


def ensure_current(state_dir: Optional[Path] = None, refresh_fn=None) -> List[ConfigDirResult]:
    """Startup repair: rewrite tokitty hooks that are present but out of date
    (a moved or updated app). Never adds one, so an uninstall stays put."""
    refresh_fn = refresh_hooks_for_dir if refresh_fn is None else refresh_fn
    results = []
    for config_dir in get_config_dirs(state_dir):
        try:
            results.append(refresh_fn(config_dir))
        except OSError as exc:
            results.append(ConfigDirResult(config_dir, False, str(exc)))
    return results
```

(`refresh_fn=None` default avoids binding the function at import, so tests can monkeypatch `refresh_hooks_for_dir`.)

- [ ] **Step 6: startup wiring.** In `__main__.run_discovery`:

```python
            if getattr(sys, "frozen", False):
                try:
                    runner_link.ensure_runner_link(state_dir)
                except OSError:
                    pass
            try:
                retry_pending_hook_op(state_dir)
                hooks_install.ensure_current(state_dir)
            except (OSError, PermissionError):
                pass
```

with `from tokitty import hooks_install, runner_link` at the top (the `runner_link` block is Task 3's; keep it) (keep the existing `retry_pending_hook_op` import). If the retry raises, the refresh is skipped for this launch; that is fine.

- [ ] **Step 7:** `install_hooks()` CLI prints `refreshed hooks for ...` when `result.refreshed_events` is non-empty. Run the full suite. Commit: `git commit -m "Rewrite out-of-date tokitty hooks in place and refresh them at startup"`.

---

### Task 5: Frozen guards

**Goal:** A frozen build writes no launcher file, and autostart is never registered from a macOS App Translocation path (the hook side is already guarded by Task 3).

**Files:**
- Modify: `tokitty/autostart.py`, `tokitty/__main__.py` (`toggle_autostart`)
- Test: `tests/test_frozen.py`, `tests/test_autostart.py`, `tests/test_hooks_install.py`

**Acceptance Criteria:**
- [ ] `autostart.ensure_current(..., frozen=True)` never writes `autostart_launcher.pyw`; with a translocated executable it returns False without touching the backend.
- [ ] `autostart.write_launcher_and_register(state_dir, backend, *, frozen=None, executable=None)` skips the launcher when frozen, raises `AppTranslocatedError` (backend untouched) when frozen and translocated, otherwise registers `resolve_launch_command(state_dir, frozen=frozen, executable=executable)`. `install_autostart` already prints `OSError`s; its message must be the translocation text.
- [ ] `run_gui`'s `toggle_autostart` catches `AppTranslocatedError` and shows `messagebox.showwarning("Start at login", str(exc))`, leaving the checkbox state as read from the backend.
- [ ] A translocated frozen `install_hooks_for_dir` (Task 3's path) returns `ok=False` with `MOVE_TO_APPLICATIONS` and leaves the home without `settings.json` or `tokitty/`; the Accounts dialog shows `outcome.message` for a failed op. Covered by a test here if Task 3 did not add one.
- [ ] Tests use `frozen=True` and fake executables in `autostart.py`'s injectable style; the existing non-frozen tests still pass unchanged.

**Verify:** `python3 -m pytest -q tests/test_frozen.py tests/test_autostart.py tests/test_hooks_install.py` → pass.

**Steps:**

- [ ] **Step 1: failing tests.**

```python
# tests/test_frozen.py
from tokitty import frozen

TRANSLOCATED = "/private/var/folders/xy/T/AppTranslocation/1234/d/Tokitty.app/Contents/MacOS/Tokitty"


def test_is_translocated():
    assert frozen.is_translocated(TRANSLOCATED)
    assert not frozen.is_translocated("/Applications/Tokitty.app/Contents/MacOS/Tokitty")


def test_error_is_oserror_with_message():
    err = frozen.AppTranslocatedError()
    assert isinstance(err, OSError)
    assert str(err) == frozen.MOVE_TO_APPLICATIONS
```

```python
# tests/test_autostart.py additions
class FakeBackend:
    def __init__(self, registered=True, current=False):
        self.registered, self.current, self.commands = registered, current, []
    def is_registered(self): return self.registered
    def is_current(self, command): return self.current
    def register(self, command): self.commands.append(command)


def test_ensure_current_frozen_writes_no_launcher(tmp_path):
    backend = FakeBackend()
    ensure_current(tmp_path, backend, frozen=True, executable="/opt/tokitty/tokitty", platform="linux")
    assert not (tmp_path / LAUNCHER_FILENAME).exists()
    assert backend.commands == [["/opt/tokitty/tokitty"]]


def test_ensure_current_translocated_leaves_backend_alone(tmp_path):
    backend = FakeBackend()
    assert ensure_current(tmp_path, backend, frozen=True, executable=TRANSLOCATED, platform="darwin") is False
    assert backend.commands == []


def test_register_refuses_translocated(tmp_path):
    backend = FakeBackend(registered=False)
    with pytest.raises(AppTranslocatedError):
        write_launcher_and_register(tmp_path, backend, frozen=True, executable=TRANSLOCATED)
    assert backend.commands == [] and not (tmp_path / LAUNCHER_FILENAME).exists()


def test_register_frozen_skips_launcher(tmp_path):
    backend = FakeBackend(registered=False)
    write_launcher_and_register(tmp_path, backend, frozen=True, executable="/Applications/Tokitty.app/Contents/MacOS/Tokitty")
    assert backend.commands == [["/Applications/Tokitty.app/Contents/MacOS/Tokitty"]]
    assert not (tmp_path / LAUNCHER_FILENAME).exists()
```

Plus in `tests/test_hooks_install.py`, if Task 3 did not already add it: a frozen install whose fake release sits under a `tmp_path/.../AppTranslocation/...` folder returns `ok=False` with the message and leaves the home without `settings.json` or `tokitty/`.

- [ ] **Step 2: implement.** `tokitty/frozen.py` already has the translocation helpers from Task 3.

In `autostart.py`, resolve `frozen`/`executable` at the top of `ensure_current` and `write_launcher_and_register` the same way `resolve_launch_command` does, then:

```python
        if frozen:
            if is_translocated(executable):
                return False
        else:
            write_launcher_file(state_dir, resolved_repo_root)
```

```python
def write_launcher_and_register(state_dir, backend, *, frozen=None, executable=None) -> None:
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    if frozen:
        if is_translocated(executable):
            raise AppTranslocatedError()
    else:
        write_launcher_file(state_dir)
    backend.register(resolve_launch_command(state_dir, frozen=frozen, executable=executable))
```

Update both docstrings in one line each. In `__main__.toggle_autostart`:

```python
                else:
                    try:
                        write_launcher_and_register(state_dir, autostart_backend)
                    except AppTranslocatedError as exc:
                        from tkinter import messagebox

                        messagebox.showwarning("Start at login", str(exc), parent=root)
```

(use whatever the Tk root variable is called in `run_gui`).

- [ ] **Step 3:** full suite. Commit: `git commit -m "Skip the launcher file when frozen and refuse to register from App Translocation"`.

---

### Task 6: Windowed error handling and --self-check

**Goal:** The frozen GUI never shows the bootloader's exception dialog, and `--self-check` verifies a bundle's contents.

**Files:**
- Modify: `tokitty/frozen.py`, `tokitty/__main__.py` (`main`), `freeze/gui_entry.py`
- Test: `tests/test_frozen.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `frozen.run_gui_entry(main_fn, state_dir_fn=get_state_dir) -> int` returns `main_fn()`'s result; lets `SystemExit` through; on any other `Exception` appends a UTC timestamp and the full traceback to `<state_dir>/crash.log` and returns 1. A failure to write the log is swallowed.
- [ ] `freeze/gui_entry.py` does every `tokitty` import inside a guard, so a failed import in the `__main__` chain is logged, not shown as the bootloader's dialog. If even `tokitty.frozen` fails to import, the entry itself appends the traceback to `crash.log` in the state dir, computed inline with the same rules as `paths.state_dir_path`, and exits 1.
- [ ] `tokitty --self-check` runs `frozen.self_check()`: checks `tkinter` (import, `tkinter.Tcl().eval("info patchlevel")`), `PIL` (import, `Image.new("RGBA", (1, 1))`), `pystray` (import), prices (`pricing.build_table(json.loads(pricing.PACKAGED_PRICES.read_text(...)))` with at least one model), `hook_writer.py` beside `hooks_install.__file__`, and when `sys.frozen` also that `hooks_install.hook_runner_path(sys.executable, sys.platform)` exists. It prints one JSON object (each check `{"ok": bool, "detail": str}`, plus `frozen`, `executable`, `build_id` from `TOKITTY_BUILD_ID`, and `state_dir` from `paths.state_dir_path()`, which Task 7's verifier uses to fail closed) to stdout when stdout is not None, and returns 0 only if every check passed.
- [ ] `--self-check` is not listed in any help or README text (hidden flag).
- [ ] Tests: raising main writes crash.log with the exception text and returns 1; SystemExit propagates; normal return passes through; `self_check()` returns 0 in the dev environment (headless test: stub the tkinter check only if `Tcl()` is unavailable, it normally is); `self_check()` returns 1 when `pricing.PACKAGED_PRICES` points at a missing file; `main(["--self-check"])` dispatches.

**Verify:** `python3 -m pytest -q tests/test_frozen.py tests/test_main.py` → pass.

**Steps:**

- [ ] **Step 1: failing tests** (`tests/test_frozen.py`):

```python
import json


def test_run_gui_entry_logs_crash(tmp_path):
    def boom():
        raise RuntimeError("kaboom")
    assert frozen.run_gui_entry(boom, state_dir_fn=lambda: tmp_path) == 1
    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "RuntimeError: kaboom" in log and "Traceback" in log


def test_run_gui_entry_passes_system_exit(tmp_path):
    def leave():
        raise SystemExit(3)
    with pytest.raises(SystemExit):
        frozen.run_gui_entry(leave, state_dir_fn=lambda: tmp_path)


def test_run_gui_entry_returns_result(tmp_path):
    assert frozen.run_gui_entry(lambda: 0, state_dir_fn=lambda: tmp_path) == 0
    assert not (tmp_path / "crash.log").exists()


def test_self_check_passes_in_dev(capsys):
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert all(c["ok"] for c in report["checks"].values())


def test_self_check_fails_without_prices(monkeypatch, tmp_path, capsys):
    from tokitty import pricing
    monkeypatch.setattr(pricing, "PACKAGED_PRICES", tmp_path / "missing.json")
    assert frozen.self_check() == 1
    assert json.loads(capsys.readouterr().out)["checks"]["prices"]["ok"] is False
```

- [ ] **Step 2: implement** in `tokitty/frozen.py`:

```python
CRASH_LOG_FILENAME = "crash.log"


def run_gui_entry(main_fn, state_dir_fn=None) -> int:
    """Run the frozen GUI. A windowed PyInstaller build would show an escaped
    exception as a modal dialog, so log it to <state dir>/crash.log instead."""
    try:
        return main_fn()
    except Exception:
        try:
            from tokitty.paths import get_state_dir

            state_dir = (state_dir_fn or get_state_dir)()
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(Path(state_dir) / CRASH_LOG_FILENAME, "a", encoding="utf-8") as f:
                f.write(f"--- {stamp}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        return 1


def _check(fn) -> dict:
    try:
        return {"ok": True, "detail": str(fn())}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def self_check() -> int:
    """Hidden --self-check: prove a bundle carries what the app needs."""
    ...  # one _check per criterion above, report dict, print json if sys.stdout
```

In `__main__.main`, before `--debug-print`:

```python
    if "--self-check" in argv:
        from tokitty.frozen import self_check

        return self_check()
```

`freeze/gui_entry.py`:

```python
import os
import sys
import traceback


def _fallback_state_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "Tokitty")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Tokitty")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tokitty")


def _run():
    try:
        from tokitty.frozen import run_gui_entry
    except Exception:
        try:
            state_dir = _fallback_state_dir()
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "crash.log"), "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        return 1

    def main():
        from tokitty.__main__ import main as real_main

        return real_main()

    return run_gui_entry(main)


sys.exit(_run())
```

`tokitty.frozen` must keep its module-level imports to the stdlib, so this fallback path is only ever hit by a broken bundle.

- [ ] **Step 3:** rebuild on ext4 (Task 1 Step 7 command) and run `HOME=<scratch> XDG_CONFIG_HOME=<scratch>/xdg xvfb-run -a /tmp/tokitty-freeze/dist/tokitty/tokitty --self-check`; expect exit 0 and every check ok, `frozen: true`. Full suite. Commit: `git commit -m "Log frozen GUI crashes to crash.log and add a bundle self-check"`.

---

### Task 7: Release workflow and artifact verification

**Goal:** `.github/workflows/release.yml` builds four artifacts, tests only the extracted copies, gates the hook median at 100 ms, and on a `v*` tag attaches them to a draft release.

**Files:**
- Create: `freeze/verify_artifact.py`, `.github/workflows/release.yml`
- Test: local run of `verify_artifact.py` against an extracted Linux tarball

**Acceptance Criteria:**
- [ ] `freeze/verify_artifact.py --app-dir <extracted> --work <scratch> --report <json> [--gate-ms 100]` (stdlib only, no `tokitty` import) does, in order, with scratch env only (`LOCALAPPDATA=<work>/localappdata` on Windows; `HOME=<work>/home`, `XDG_CONFIG_HOME=<work>/xdg` elsewhere) and a 60 s timeout on every child:
  1. Locates the GUI exe (`Tokitty.exe` / `Tokitty.app/Contents/MacOS/Tokitty` / `tokitty`) and `tokitty-hook[.exe]` beside it; fails if either is missing.
  2. Runs `<gui> --self-check`; requires exit 0 and all checks ok.
  3. **Fails closed before anything can write hooks.** Takes `state_dir` from step 2's self-check report, requires it to sit under `<work>`, writes `accounts.json` there naming `<work>/claude-home`, reads it back and checks it parses and names only that home. If any of that fails, stop without running `--install-hooks`. Without a readable `accounts.json`, `get_config_dirs` falls back to the default home, which on Windows is the real WSL `.claude` (`hooks_install.py:83-101`). On Windows it also sets `USERPROFILE` to `<work>/home`.
  4. Runs `<gui> --install-hooks`; requires exit 0; `claude-home/tokitty/hook_writer.py` byte-identical to the repo's `tokitty/hook_writer.py` (the artifact-level proof that `datas` reached the bundle); all 7 `HOOK_EVENTS` present, each with an exec-form hook whose `command` is exactly `<state_dir>/current/tokitty-hook[.exe]`; `<state_dir>/current` is a link (symlink or junction) whose `realpath` is the extracted release folder (`Tokitty.app/Contents/MacOS` on macOS), and the command exists through it.
  4b. **Second simulated release:** extracts the same archive again into `<work>/release-b`, runs that copy's `--install-hooks` with the same scratch env, and requires `settings.json` byte-identical to after step 4 and `current` now resolving into `release-b`. Steps 5 and 6 then run through the link at `release-b`.
  5. Times the registered hook argv (`[command] + args`) exactly as the spike did: 1 cold run (fresh session id, expect `seq` 1), then 20 warm runs on one session id asserting exit 0, empty stdout, `seq == i`; payload `{"session_id", "hook_event_name": "PreToolUse", "tool_name": "Bash"}`; `time.perf_counter` around `subprocess.run`. Logs every value; fails if the median exceeds `--gate-ms`.
  6. Runs every registered hook with its own event name, checking the state file (and that `SessionEnd` removes it).
  7. Autostart: `<gui> --install-autostart`, then asserts the registration points at the extracted GUI exe (Linux: `Exec=` line of `<XDG_CONFIG_HOME>/autostart/tokitty.desktop`; macOS: `ProgramArguments == [gui]` in `<HOME>/Library/LaunchAgents/com.nickwolf.tokitty.plist`; Windows: `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Tokitty` equals the quoted exe path), that no `autostart_launcher.pyw` exists in the state dir, then `--uninstall-autostart`. Compare the Windows value with `subprocess.list2cmdline([gui])`, which quotes only when needed, as `WindowsRegistryBackend.register` does. On Windows this step runs only when `CI=true` (skipped with a logged reason otherwise, because the Run key cannot be pointed at a scratch location), reads any existing `Tokitty` value first, and restores or deletes it in a `finally` so a failed assertion or timeout never leaves the extracted exe registered.
  8. Writes the JSON report (sizes, every timing, cold, median, min, max, pass/fail per step) and exits nonzero on any failure. In a `finally`, removes the `current` link with `os.unlink` or `os.rmdir` (never `shutil.rmtree`) and kills any child still alive.
- [ ] `release.yml`: triggers `push: tags: ['v*']` and `workflow_dispatch`. `permissions: contents: read` at top, `contents: write` only on the release job. Matrix (fail-fast false): `windows-latest` → `windows-x64`, `macos-latest` → `macos-arm64`, `macos-15-intel` → `macos-x86_64`, `ubuntu-22.04` → `linux-x86_64`. Python 3.13. Steps: checkout; setup-python; `pip install -e ".[packaging]"`; Linux: `sudo apt-get install -y xvfb`; `python -m PyInstaller freeze/tokitty.spec --noconfirm --distpath dist --workpath build`; archive (`tokitty-<version>-<target>.zip` via Python `shutil.make_archive` on Windows, `ditto -c -k --keepParent dist/Tokitty.app` on macOS, `tar -czf` of `dist/tokitty` on Linux); extract the archive into `$RUNNER_TEMP/extracted` (Python `zipfile`, `ditto -x -k`, `tar -xzf`); run `verify_artifact.py` against the extracted path (Linux under `xvfb-run -a`); append the report's timing lines to `$GITHUB_STEP_SUMMARY`; upload the archive and the report with `actions/upload-artifact@v4`. `<version>` is `$GITHUB_REF_NAME` on a tag, else `dryrun-<short sha>`.
- [ ] Release job: `if: startsWith(github.ref, 'refs/tags/v')`, `needs: build`, downloads the four archives, `gh release create "$GITHUB_REF_NAME" --draft --title "$GITHUB_REF_NAME" --notes "..." <files>` with `GH_TOKEN: ${{ github.token }}`.
- [ ] `ci.yml` unchanged.
- [ ] Local rehearsal on WSL: build on ext4, `tar -czf` then extract to a fresh `/tmp` path, run `xvfb-run -a python3 freeze/verify_artifact.py --app-dir <extracted>/tokitty --work /tmp/tokitty-freeze/verify --report /tmp/tokitty-freeze/report.json`. All steps pass; median printed. (Windows step 7 skip logic is exercised by its own unit-free code path; do not run the Windows exe locally in this task.)
- [ ] Workflow YAML validated locally with `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml'))"` (install `python3-yaml` via apt if missing) and, if available, `actionlint` (install the release binary to `~/.local/bin` if not present; it is free and self-contained).

**Verify:** local rehearsal report shows all steps pass; `actionlint .github/workflows/release.yml` clean.

**Steps:**

- [ ] **Step 1:** write `freeze/verify_artifact.py` implementing criteria 1 to 8. Structure: one function per step returning a dict for the report, `main()` running them in order and stopping at the first hard failure (still writing the report). Reuse the spike's bench loop shape (`/mnt/c/Tools/tokitty/.worktrees/installable-app-spike/spike/bench_hook.py`, read-only reference) but call the registered argv, not `--run-hook`.
- [ ] **Step 2:** local rehearsal as above; paste the median and the `du -sb` / archive size into the task report.
- [ ] **Step 3:** write `release.yml` per the criteria. Linux job runs on `ubuntu-22.04` (oldest hosted Ubuntu image; if GitHub has retired it when the dry run happens, the controller moves to the oldest available and records it).
- [ ] **Step 4:** validate YAML + actionlint; full suite; commit `git commit -m "Add a release workflow that builds, extracts and tests each artifact"`.

---

### Task 8: macOS Keychain job

**Goal:** A `workflow_dispatch`-only job that tests spec Q6's prediction (no Keychain prompt per release) against a synthetic keychain.

**Files:**
- Create: `freeze/keychain_check.py`
- Modify: `.github/workflows/release.yml` (new job `keychain`)

**Acceptance Criteria:**
- [ ] Job `keychain` runs on `macos-latest` only when `github.event_name == 'workflow_dispatch'`, independent of the build matrix.
- [ ] Builds the spec twice from the same checkout with `TOKITTY_BUILD_ID=A` and `B` into `dist-a`/`dist-b` (separate `--workpath`), logs `codesign -dv --verbose=4` for both `Contents/MacOS/Tokitty` binaries, and fails if their CDHashes are equal (the test would prove nothing).
- [ ] `keychain_check.py` follows spec Q6 steps 2 to 6 exactly: throwaway `tk.keychain-db` (password `ci`), unlocked, first in the user search list; three items under service `Claude Code-credentials`, one at a time (`trusted` with `-T /usr/bin/security`, `negative` with `-T ''`, `binary-only` with `-T <build A binary>`), each holding fake credentials JSON in the shape `tokitty/credentials.py` parses, with `expiresAt` in the past (epoch ms); `security dump-keychain -a tk.keychain-db` logged before each run; A and B each run with `--debug-print` under a scratch `HOME` holding no `accounts.json`, no `~/.claude/.credentials.json`, and with every credentials-path override env var the code reads (the implementer greps `credentials.py` and `__main__.py` for `os.environ`) removed from the child env; 30 s external timeout, timeout recorded as "would prompt". Each probe starts in its own process group (`start_new_session=True`); on timeout the whole group is killed with `os.killpg(..., SIGKILL)` and reaped, so a `security -w` child Tokitty spawned (its own timeout is 120 s, `keychain.py:69`) cannot outlive the case and overlap the next ACL. Before each case, `pgrep -x security` must be empty.
- [ ] Classifies each run from `--debug-print` output: `status: stale_token` plus a `credentials source:` line naming the Keychain is a pass-through read; `status: keychain_denied` or timeout is "would prompt"; anything else is a harness error. Verdict table printed and written to `$GITHUB_STEP_SUMMARY`. Exit nonzero if `negative` passes (broken instrument) or on any harness error. A disproved prediction (B denied where A passed, or `binary-only` passes for A) is reported as DISPROVED with exit 0 so the evidence is kept; the controller reads the verdict, not the job colour.
- [ ] The keychain is deleted and the search list restored in a `finally`.
- [ ] Before writing, the implementer confirms the exact `security add-generic-password` account name `keychain.py` looks up (it passes `account=None` today, so the item's account attribute must not matter; verify in `_base_command`) and the fake credentials schema from `credentials.py`, and cites both in the report.

**Verify:** `python3 -m py_compile freeze/keychain_check.py`; actionlint clean. It can only truly run on the macOS runner during the dry run.

**Steps:**

- [ ] **Step 1:** read `tokitty/keychain.py`, `tokitty/credentials.py` (`resolve_credentials_source`, the expiry check at `credentials.py:192`), and `debug_print` in `__main__.py:70-100`. Write `freeze/keychain_check.py` with `--build-a`, `--build-b`, `--work` args, stdlib only.
- [ ] **Step 2:** add the job to `release.yml`. Commit `git commit -m "Add a macOS job that checks Keychain access across two builds"`.

---

### Task 9: README

**Goal:** Install instructions for the downloaded app, per OS, with the decided notes.

**Files:**
- Modify: `README.md`

**Acceptance Criteria:**
- [ ] An "Install" section first: download from GitHub Releases, archive names per OS and architecture (Apple Silicon takes `macos-arm64`, Intel Macs `macos-x86_64`), unzip anywhere, run `Tokitty.exe` / `Tokitty.app` / `tokitty`.
- [ ] First launch per OS: Windows SmartScreen (More info, Run anyway); macOS 15: open once, then System Settings, Privacy & Security, Open Anyway, or `xattr -dr com.apple.quarantine Tokitty.app`; Linux: nothing, tray is X11 (xorg) only in the downloaded build.
- [ ] The three notes, in plain words: hooks from the downloaded app need Claude Code 2.1.139 or newer; after unzipping an update or moving the folder, launch the new copy once before deleting the old one (hooks run through a link Tokitty repoints when it starts, so open Claude Code sessions keep working and nothing needs re-approving); on macOS, move Tokitty to Applications before turning on Start at login or adding an account.
- [ ] A Claude Code home inside WSL keeps using WSL's `python3` for its hook, even with the Windows download.
- [ ] The existing `python -m tokitty` / `pythonw.exe -m tokitty` instructions move under a "Running from source" heading presented as the developer path. Nothing else in the README changes.
- [ ] Playbook rules: no em-dashes, no hard-wrapped paragraphs, no bold lead-ins on every item. `grep -n '—' README.md` returns nothing new.

**Verify:** `git diff README.md` reviewed by the controller against the playbook; `grep -c '—' README.md` unchanged from before.

**Steps:**

- [ ] **Step 1:** read `README.md` in full and `/mnt/c/Tools/docs/conventions/public_writing_playbook.md`. Edit only the sections named above. Commit `git commit -m "Document installing the downloaded app"`.

---

### Task 10: Manual gate on Windows (Nick)

Not a subagent task. After the dry run is green, the controller hands Nick exact steps: download the Windows dry-run artifact, extract to a new folder, launch `Tokitty.exe`, add an account in the dialog, confirm the WSL home still gets the `python3` hook (`settings.json` under `\\wsl.localhost\...`), confirm the cat reacts in a restarted Claude Code session, toggle Start at login, reboot, confirm it launches from the new folder. Then the stable-path check: extract the same artifact to a second new folder, launch that copy once, and confirm that `%LOCALAPPDATA%\Tokitty\current` now points there, that `settings.json` of a native-Windows Claude home (if Nick has one; otherwise a scratch home) did not change, and that a hook keeps working across the switch. With a native-Windows Claude home, that means the cat still reacts in a Claude Code session left open throughout. Without one, the controller supplies a scratch-home check instead: run the scratch home's registered command (`current\tokitty-hook.exe --sessions-dir ...` with a sample payload, via `python.exe` and an argv list) before and after the switch, and confirm its session file updates both times. The WSL home proves nothing here because it uses `python3`.

### Task 11: Manual gate on a real Mac (deferred, before publishing a release)

Not a subagent task, and nobody has a Mac today. Spec Q6 keeps the real-keychain recipe as the release gate for the Keychain question, because Task 8 only covers a synthetic keychain. The controller records it as an open item: before the draft release is published, someone with a Mac runs the recipe in spec Q6 (release N with Always Allow, replace with N+1 in the same Applications path, no dialog, `security dump-keychain -a` lists `/usr/bin/security` and not Tokitty) and the App Translocation check (spec Q4 and Q2a: from `~/Downloads`, both autostart and hook install refuse, and `current` is either absent (fresh state dir) or still points at its previous target. After moving the app to Applications, both register and `current` points into `/Applications/Tokitty.app/Contents/MacOS`).

---

## Execution order and review gates

Task 4 waits for the hooks groundwork PR to merge and is re-planned then; Tasks 5 to 9 do not depend on it and run first. Otherwise tasks run in order 1 to 9, one Sonnet subagent each, spec review then code-quality review, commit between tasks, full suite after each. Task 1 Step 7 is a hard gate: if `_internal` is not shared, stop and report before Task 2. After Task 9 the controller asks Nick before pushing the branch and before triggering `release.yml` via `workflow_dispatch`, then reads the dry-run logs (hook median per OS against 100 ms, keychain verdict), then hands Nick Task 10 and records Task 11 as open.
