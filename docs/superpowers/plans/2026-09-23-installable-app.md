# Installable App (#48) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Tokitty as unsigned PyInstaller onedir builds for Windows, macOS (arm64 and x86_64) and Linux, with hooks that work from the downloaded app.

**Architecture:** One PyInstaller spec builds two executables into one `COLLECT`: the windowed GUI and a `tokitty-hook` runner that can never open a window. `hooks_install` picks the registered hook by where Claude Code runs (python3 for a WSL home, exec form `tokitty-hook` for a same-OS home when frozen), recognises only hook shapes Tokitty wrote, and refreshes stale owned hooks at startup without ever adding one. A release workflow builds, archives, extracts and tests only the extracted copy on every OS.

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
- Uninstall also narrows to owned hooks: it removes only Tokitty's hook from an entry and keeps a user's hook sharing that entry. Today it deletes the whole entry. Without this, Task 3's mixed-entry rule holds for install and refresh but not for uninstall.
- Ownership recognises the unquoted command form written by 9bab1b3 (2026-07-16 to 2026-07-18) as well as the quoted form, since installs from those two days still carry it.
- Refresh collapses duplicate owned hooks in one event to one, so a home carrying both a python and an exe hook stops firing twice.
- The release job creates a **draft** release. The spec says "creates the release"; a draft keeps publication a manual step for Nick.
- The build ID that makes Task 7's two binaries differ is injected through a generated PyInstaller runtime hook (it changes the executable's embedded archive), and `--self-check` reports it.

## File Structure

- Create `freeze/tokitty.spec`: PyInstaller spec, two `Analysis`/`EXE` pairs, one `COLLECT`, `BUNDLE` on macOS.
- Create `freeze/bundle_data.py`: the list of files bundled as data under `tokitty/`. Imported by the spec and by the test.
- Create `freeze/gui_entry.py`, `freeze/hook_entry.py`: frozen entry points.
- Create `freeze/verify_artifact.py`: CI acceptance test and hook timing for one extracted artifact.
- Create `freeze/keychain_check.py`: macOS Keychain job (Task 7).
- Create `tokitty/frozen.py`: frozen-build support (translocation check, crash log, `--self-check`).
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

- [ ] **Step 5: GUI entry.** `freeze/gui_entry.py` (Task 5 wraps this):

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

Expected: rc=0, `IDENTICAL`. (Before Task 2 the registered command is still `python3 ...`; that is expected here.)

- [ ] **Step 10: ignore build output.** Add `/build/` and `/dist/` to `.gitignore` if not already covered. Commit:

```bash
git add freeze/ tests/test_freeze_spec.py pyproject.toml .gitignore
git commit -m "Add a PyInstaller spec that builds the app and a hook runner"
```

---

### Task 2: Hook command builder

**Goal:** `_build_command` returns the hook dict to register, following the table in spec Q2.

**Files:**
- Modify: `tokitty/hooks_install.py` (`_build_command`, new `_is_wsl_unc`, `hook_runner_path`, `HOOK_RUNNER_NAME`; the one caller in `install_hooks_for_dir`)
- Test: `tests/test_hooks_install.py`

**Acceptance Criteria:**
- [ ] Source install, any home: `{"type": "command", "command": "<python3|python> \"<native>/tokitty/hook_writer.py\" --sessions-dir \"<native>/tokitty/sessions\""}`, no `args` key. Byte-identical to today's string.
- [ ] Frozen on win32, `\\wsl.localhost\...` or `\\wsl$\...` home: the same python3 string as source.
- [ ] Frozen, same-OS home: `{"type": "command", "command": "<dir of executable>/tokitty-hook[.exe]", "args": ["--sessions-dir", "<native>/tokitty/sessions"]}`.
- [ ] `frozen`, `executable`, `platform` are keyword overrides defaulting to `sys.frozen`, `sys.executable`, `sys.platform`, like `autostart.resolve_launch_command`, so every row is testable on every CI OS.
- [ ] The installed entry is `{"matcher": m, "hooks": [<the dict>]}`.

**Verify:** `python3 -m pytest -q tests/test_hooks_install.py` → pass.

**Steps:**

- [ ] **Step 1: failing tests** (append to `tests/test_hooks_install.py`):

```python
WIN_EXE = r"C:\Users\nick\Tokitty\Tokitty.exe"


def test_build_command_source_posix_unchanged():
    hook = hi._build_command("/home/nick/.claude", frozen=False, executable="/usr/bin/python3", platform="linux")
    assert hook == {
        "type": "command",
        "command": 'python3 "/home/nick/.claude/tokitty/hook_writer.py" --sessions-dir "/home/nick/.claude/tokitty/sessions"',
    }


def test_build_command_source_windows_local_uses_python():
    hook = hi._build_command(r"C:\Users\nick\.claude", frozen=False, executable=r"C:\Py\python.exe", platform="win32")
    assert hook["command"].startswith('python "C:\\Users\\nick\\.claude/tokitty/hook_writer.py"')
    assert "args" not in hook


def test_build_command_frozen_windows_wsl_home_keeps_python3():
    hook = hi._build_command(r"\\wsl.localhost\Ubuntu\home\nick\.claude", frozen=True, executable=WIN_EXE, platform="win32")
    assert hook == {
        "type": "command",
        "command": 'python3 "/home/nick/.claude/tokitty/hook_writer.py" --sessions-dir "/home/nick/.claude/tokitty/sessions"',
    }


def test_build_command_frozen_windows_wsl_dollar_home_keeps_python3():
    hook = hi._build_command(r"\\wsl$\Ubuntu\home\nick\.claude", frozen=True, executable=WIN_EXE, platform="win32")
    assert hook["command"].startswith("python3 ")
    assert "args" not in hook


def test_build_command_frozen_windows_local_home_uses_exec_form():
    hook = hi._build_command(r"C:\Users\nick\.claude", frozen=True, executable=WIN_EXE, platform="win32")
    assert hook == {
        "type": "command",
        "command": r"C:\Users\nick\Tokitty\tokitty-hook.exe",
        "args": ["--sessions-dir", "C:\\Users\\nick\\.claude/tokitty/sessions"],
    }


def test_build_command_frozen_linux_uses_exec_form():
    hook = hi._build_command("/home/nick/.claude", frozen=True, executable="/opt/tokitty/tokitty", platform="linux")
    assert hook == {
        "type": "command",
        "command": "/opt/tokitty/tokitty-hook",
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions"],
    }


def test_build_command_frozen_macos_hook_sits_beside_app_binary():
    exe = "/Applications/Tokitty.app/Contents/MacOS/Tokitty"
    hook = hi._build_command("/Users/nick/.claude", frozen=True, executable=exe, platform="darwin")
    assert hook["command"] == "/Applications/Tokitty.app/Contents/MacOS/tokitty-hook"


def test_install_writes_exec_form_entry_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(tmp_path / "app" / "tokitty"))
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


def _build_command(config_dir: str, *, frozen=None, executable=None, platform=None) -> dict:
    """The hook to register, chosen by where Claude Code runs (spec Q2).

    A frozen build registers its own tokitty-hook in exec form (Claude Code
    2.1.139+), except for a WSL home seen from Windows, which keeps python3:
    WSL ships it and it is twice as fast as the exe through interop.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    platform = sys.platform if platform is None else platform
    native = _wsl_native_path(config_dir)
    sessions_dir = f"{native}/tokitty/sessions"
    if frozen and not (platform == "win32" and _is_wsl_unc(config_dir)):
        return {
            "type": "command",
            "command": hook_runner_path(executable, platform),
            "args": ["--sessions-dir", sessions_dir],
        }
    interpreter = "python" if _is_windows_local_path(config_dir) else "python3"
    return {
        "type": "command",
        "command": f'{interpreter} "{native}/tokitty/hook_writer.py" --sessions-dir "{sessions_dir}"',
    }
```

Make `_wsl_native_path` use `_WSL_UNC_PREFIXES` instead of its inline tuple. In `install_hooks_for_dir` change `command = _build_command(config_dir)` to `hook = _build_command(config_dir)` and append `{"matcher": matcher, "hooks": [dict(hook)]}`. Update any existing test that compared `_build_command(...)` to a string to compare `["command"]`.

- [ ] **Step 3:** run the new tests and the full suite. `_is_tokitty_entry` still matches exec form here because the marker `tokitty` is in the `tokitty-hook` path; Task 3 replaces it.

- [ ] **Step 4: commit** `git commit -am "Register the bundled tokitty-hook in exec form from a frozen build"` (add new test lines too).

---

### Task 3: Hook ownership and refresh

**Goal:** Only hooks Tokitty wrote count as Tokitty's; install and a new startup refresh rewrite stale owned hooks in place, and refresh never adds one.

**Files:**
- Modify: `tokitty/hooks_install.py`, `tokitty/__main__.py` (`run_discovery`)
- Test: `tests/test_hooks_install.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `_is_owned_hook(hook)` is true only for: the quoted legacy string `python|python3 "<d>/tokitty/hook_writer.py" --sessions-dir "<d>/tokitty/sessions"`; the unquoted legacy string (same with no quotes); and exec form whose `command` basename is `tokitty-hook` or `tokitty-hook.exe` (case-insensitive) with `args == ["--sessions-dir", s]` where `s` ends in `/tokitty/sessions` (after `\` to `/`). `_is_tokitty_entry(entry)` is true iff any hook in it is owned.
- [ ] A user hook `python3 /opt/tokitty/myhook.py` and `bash ~/tokitty-scripts/run.sh` are not owned, are never rewritten or removed.
- [ ] Install, per event in `HOOK_EVENTS`: owned hook in `settings.local.json` counts as installed and is never duplicated into `settings.json` (a stale one is reported in the message); owned hook in `settings.json` matching the desired `command`/`args` is a no-op; owned but different is rewritten in place keeping the entry's `matcher`, the hook's other keys (e.g. `timeout`), and neighbouring hooks; further owned hooks in the same event are removed (an entry left with no hooks is dropped); no owned hook means a new entry is appended.
- [ ] `settings.json` is backed up before any write and written only if something changed. `hook_writer.py` is copied on every install, as today.
- [ ] `refresh_hooks_for_dir(config_dir)` does the same reconcile but never appends. With no owned hook in either file it writes nothing at all (no copy, no mkdir). With owned hooks it copies `hook_writer.py`.
- [ ] `ensure_current(state_dir=None, refresh_fn=refresh_hooks_for_dir) -> List[ConfigDirResult]` runs refresh over `get_config_dirs(state_dir)`, turning an `OSError` for one dir into a failed result and continuing.
- [ ] `get_config_dirs` takes an optional `state_dir` (default `get_state_dir()`).
- [ ] Uninstall removes only owned hooks, dropping an entry only if it ends up with no hooks, and an event only if it ends up with no entries.
- [ ] `run_discovery` calls `hooks_install.ensure_current(state_dir)` right after `retry_pending_hook_op`, inside the same OSError guard, off the Tk thread.
- [ ] Tests from spec Task 3 all present: python to exe; exe to moved exe; identical is a no-op (file mtime and content unchanged, no backup file created); user hook containing "tokitty" left alone; mixed entry keeps the user hook; `--uninstall-hooks` then `ensure_current` stays uninstalled; stale owned entry in `settings.local.json` reported and not duplicated. Plus: unquoted legacy form is owned and gets rewritten; duplicate owned hooks collapse to one; `timeout` key preserved on rewrite; uninstall of a mixed entry keeps the user hook.

**Verify:** `python3 -m pytest -q tests/test_hooks_install.py tests/test_main.py` → pass.

**Steps:**

- [ ] **Step 1: failing tests.** Add to `tests/test_hooks_install.py` (helpers first, then one test per criterion). Core helpers and representative tests; write the rest in the same shape:

```python
PY_HOOK = {"type": "command", "command": 'python3 "/h/tokitty/hook_writer.py" --sessions-dir "/h/tokitty/sessions"'}
OLD_UNQUOTED = {"type": "command", "command": "python3 /h/tokitty/hook_writer.py --sessions-dir /h/tokitty/sessions"}
EXE_HOOK = {"type": "command", "command": "/old/tokitty-hook", "args": ["--sessions-dir", "/h/tokitty/sessions"]}
USER_HOOK = {"type": "command", "command": "python3 /opt/tokitty/myhook.py"}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _frozen(monkeypatch, exe):
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", exe)
    monkeypatch.setattr(hi.sys, "platform", "linux")


def _full_settings(hook):
    return {"hooks": {ev: [{"matcher": m, "hooks": [dict(hook)]}] for ev, m in hi.HOOK_EVENTS}}


def test_owned_shapes():
    assert hi._is_owned_hook(PY_HOOK)
    assert hi._is_owned_hook(OLD_UNQUOTED)
    assert hi._is_owned_hook(EXE_HOOK)
    assert hi._is_owned_hook({"type": "command", "command": r"C:\T\TOKITTY-HOOK.EXE",
                              "args": ["--sessions-dir", "C:\\h/tokitty/sessions"]})
    assert not hi._is_owned_hook(USER_HOOK)
    assert not hi._is_owned_hook({"type": "command", "command": "bash ~/tokitty-scripts/run.sh"})
    assert not hi._is_owned_hook({"type": "command", "command": "/x/tokitty-hook", "args": ["--other"]})


def test_refresh_python_to_exe(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _write(home / "settings.json", _full_settings(PY_HOOK))
    _frozen(monkeypatch, "/new/tokitty")
    result = hi.refresh_hooks_for_dir(str(home))
    assert result.ok and set(result.refreshed_events) == {e for e, _ in hi.HOOK_EVENTS}
    hook = _read(home / "settings.json")["hooks"]["Stop"][0]["hooks"][0]
    assert hook == {"type": "command", "command": "/new/tokitty-hook", "args": ["--sessions-dir", f"{home}/tokitty/sessions"]}
    assert (home / "tokitty" / "hook_writer.py").is_file()


def test_refresh_identical_is_noop(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _frozen(monkeypatch, "/app/tokitty")
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


def test_uninstall_then_ensure_current_stays_uninstalled(tmp_path, monkeypatch):
    home = tmp_path / "h"
    state = tmp_path / "state"
    _write(state / "accounts.json", {"accounts": [{"name": "a", "config_dir": str(home), "provider": "claude"}]})
    assert hi.install_hooks_for_dir(str(home)).ok
    assert hi.uninstall_hooks_for_dir(str(home)).ok
    hi.ensure_current(state)
    assert not hi._events_with_tokitty_entries(_read(home / "settings.json"))


def test_mixed_entry_keeps_user_hook_on_refresh_and_uninstall(tmp_path, monkeypatch):
    home = tmp_path / "h"
    timed = dict(PY_HOOK, timeout=5)
    _write(home / "settings.json", {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [USER_HOOK, timed]}]}})
    _frozen(monkeypatch, "/app/tokitty")
    hi.refresh_hooks_for_dir(str(home))
    entry = _read(home / "settings.json")["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0] == USER_HOOK
    assert entry["hooks"][1]["command"] == "/app/tokitty-hook" and entry["hooks"][1]["timeout"] == 5
    hi.uninstall_hooks_for_dir(str(home))
    assert _read(home / "settings.json")["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [USER_HOOK]}]


def test_stale_local_entry_reported_not_duplicated(tmp_path, monkeypatch):
    home = tmp_path / "h"
    _write(home / "settings.local.json", {"hooks": {"Stop": [{"matcher": "", "hooks": [PY_HOOK]}]}})
    _frozen(monkeypatch, "/app/tokitty")
    result = hi.install_hooks_for_dir(str(home))
    assert result.ok
    assert "Stop" not in _read(home / "settings.json")["hooks"]
    assert "settings.local.json" in result.message
    assert _read(home / "settings.local.json")["hooks"]["Stop"][0]["hooks"][0] == PY_HOOK
```

Also: exe to moved exe (EXE_HOOK then `_frozen(..., "/moved/tokitty")`), unquoted legacy rewritten, duplicates collapse (one event with `[PY_HOOK]` and `[EXE_HOOK]` entries ends with exactly one owned hook), user-only hook untouched by install/uninstall, `ensure_current` turns `OSError` from `refresh_fn` into `ok=False` and continues to the next dir. In `tests/test_main.py`, extend the existing `run_discovery` test pattern to assert `hooks_install.ensure_current` is called with `state_dir` after `retry_pending_hook_op` and that an `OSError` from it is swallowed.

- [ ] **Step 2: implement ownership** (`import re`):

```python
_LEGACY_COMMAND = re.compile(
    r'^python3? (?P<q>"?)(?P<dir>.+)/tokitty/hook_writer\.py(?P=q)'
    r' --sessions-dir (?P=q)(?P=dir)/tokitty/sessions(?P=q)$'
)
_RUNNER_NAMES = (HOOK_RUNNER_NAME, HOOK_RUNNER_NAME + ".exe")


def _is_owned_hook(hook) -> bool:
    """Only the hook shapes tokitty itself has written, never a substring match."""
    if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
        return False
    command = hook["command"]
    args = hook.get("args")
    if args is None:
        return bool(_LEGACY_COMMAND.match(command))
    name = command.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return (
        name in _RUNNER_NAMES
        and isinstance(args, list)
        and len(args) == 2
        and args[0] == "--sessions-dir"
        and isinstance(args[1], str)
        and args[1].replace("\\", "/").endswith("/tokitty/sessions")
    )


def _is_tokitty_entry(entry) -> bool:
    return isinstance(entry, dict) and any(_is_owned_hook(h) for h in entry.get("hooks", []) if isinstance(entry.get("hooks"), list))
```

(Keep `_is_tokitty_entry` readable; the expression above can be split.) Delete `MARKER` if nothing else uses it (`grep -rn MARKER tokitty tests`).

- [ ] **Step 3: implement reconcile.** Replace the body of `install_hooks_for_dir` with a shared `_reconcile(config_dir, add_missing)`; `install_hooks_for_dir = lambda d: _reconcile(d, True)` as a real `def`, and `refresh_hooks_for_dir(d)` as `_reconcile(d, False)`. Add `refreshed_events` to `ConfigDirResult` (default `[]`). Outline:

```python
def _same_hook(hook: dict, desired: dict) -> bool:
    return hook.get("command") == desired["command"] and hook.get("args") == desired.get("args")


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
    # 6. for event, matcher in HOOK_EVENTS, skipping events in local_owned:
    #    walk entries/hooks; first owned hook: rewrite if not _same_hook
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
            try:
                retry_pending_hook_op(state_dir)
                hooks_install.ensure_current(state_dir)
            except (OSError, PermissionError):
                pass
```

with `from tokitty import hooks_install` at the top (keep the existing `retry_pending_hook_op` import). If the retry raises, the refresh is skipped for this launch; that is fine.

- [ ] **Step 7:** `install_hooks()` CLI prints `refreshed hooks for ...` when `result.refreshed_events` is non-empty. Run the full suite. Commit: `git commit -m "Rewrite out-of-date tokitty hooks in place and refresh them at startup"`.

---

### Task 4: Frozen guards

**Goal:** A frozen build writes no launcher file, and neither autostart nor exe-hook registration happens from a macOS App Translocation path.

**Files:**
- Create: `tokitty/frozen.py`
- Modify: `tokitty/autostart.py`, `tokitty/hooks_install.py`, `tokitty/__main__.py` (`toggle_autostart`)
- Test: `tests/test_frozen.py`, `tests/test_autostart.py`, `tests/test_hooks_install.py`

**Acceptance Criteria:**
- [ ] `frozen.is_translocated(executable=None)` is true iff the path contains `/AppTranslocation/`. `frozen.AppTranslocatedError(OSError)` carries `frozen.MOVE_TO_APPLICATIONS`, the text: "Tokitty is running from a temporary copy macOS makes of downloaded apps. Move Tokitty to your Applications folder, open it from there, and try again."
- [ ] `autostart.ensure_current(..., frozen=True)` never writes `autostart_launcher.pyw`; with a translocated executable it returns False without touching the backend.
- [ ] `autostart.write_launcher_and_register(state_dir, backend, *, frozen=None, executable=None)` skips the launcher when frozen, raises `AppTranslocatedError` (backend untouched) when frozen and translocated, otherwise registers `resolve_launch_command(state_dir, frozen=frozen, executable=executable)`. `install_autostart` already prints `OSError`s; its message must be the translocation text.
- [ ] `run_gui`'s `toggle_autostart` catches `AppTranslocatedError` and shows `messagebox.showwarning("Start at login", str(exc))`, leaving the checkbox state as read from the backend.
- [ ] In `hooks_install._reconcile`, when the desired hook is exec form and `is_translocated(sys.executable)`: return `ConfigDirResult(config_dir, False, MOVE_TO_APPLICATIONS)` before writing anything (no copy, no settings write). The Accounts dialog already shows `outcome.message` for a failed op.
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

Plus in `tests/test_hooks_install.py`: frozen with a translocated `sys.executable` (and `sys.platform` patched to `darwin`) makes `install_hooks_for_dir` return `ok=False` with the message and leaves the home dir without `settings.json` or `tokitty/`. And a frozen WSL-home install (platform `win32`, UNC dir) is not refused even if the executable were translocated, because that row is python3; use a tmp_path-based UNC-free equivalent by patching `_build_command` only if the UNC path cannot be created on the test OS.

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
    return "/AppTranslocation/" in executable
```

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

(use whatever the Tk root variable is called in `run_gui`). In `hooks_install._reconcile`, after computing `desired`: `if "args" in desired and is_translocated(): return ConfigDirResult(config_dir, False, MOVE_TO_APPLICATIONS)`.

- [ ] **Step 3:** full suite. Commit: `git commit -m "Skip the launcher file when frozen and refuse to register from App Translocation"`.

---

### Task 5: Windowed error handling and --self-check

**Goal:** The frozen GUI never shows the bootloader's exception dialog, and `--self-check` verifies a bundle's contents.

**Files:**
- Modify: `tokitty/frozen.py`, `tokitty/__main__.py` (`main`), `freeze/gui_entry.py`
- Test: `tests/test_frozen.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `frozen.run_gui_entry(main_fn, state_dir_fn=get_state_dir) -> int` returns `main_fn()`'s result; lets `SystemExit` through; on any other `Exception` appends a UTC timestamp and the full traceback to `<state_dir>/crash.log` and returns 1. A failure to write the log is swallowed.
- [ ] `freeze/gui_entry.py` is `sys.exit(run_gui_entry(main))`.
- [ ] `tokitty --self-check` runs `frozen.self_check()`: checks `tkinter` (import, `tkinter.Tcl().eval("info patchlevel")`), `PIL` (import, `Image.new("RGBA", (1, 1))`), `pystray` (import), prices (`pricing.build_table(json.loads(pricing.PACKAGED_PRICES.read_text(...)))` with at least one model), `hook_writer.py` beside `hooks_install.__file__`, and when `sys.frozen` also that `hooks_install.hook_runner_path(sys.executable, sys.platform)` exists. It prints one JSON object (each check `{"ok": bool, "detail": str}`, plus `frozen`, `executable`, `build_id` from `TOKITTY_BUILD_ID`) to stdout when stdout is not None, and returns 0 only if every check passed.
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
import sys

from tokitty.__main__ import main
from tokitty.frozen import run_gui_entry

sys.exit(run_gui_entry(main))
```

- [ ] **Step 3:** rebuild on ext4 (Task 1 Step 7 command) and run `HOME=<scratch> XDG_CONFIG_HOME=<scratch>/xdg xvfb-run -a /tmp/tokitty-freeze/dist/tokitty/tokitty --self-check`; expect exit 0 and every check ok, `frozen: true`. Full suite. Commit: `git commit -m "Log frozen GUI crashes to crash.log and add a bundle self-check"`.

---

### Task 6: Release workflow and artifact verification

**Goal:** `.github/workflows/release.yml` builds four artifacts, tests only the extracted copies, gates the hook median at 100 ms, and on a `v*` tag attaches them to a draft release.

**Files:**
- Create: `freeze/verify_artifact.py`, `.github/workflows/release.yml`
- Test: local run of `verify_artifact.py` against an extracted Linux tarball

**Acceptance Criteria:**
- [ ] `freeze/verify_artifact.py --app-dir <extracted> --work <scratch> --report <json> [--gate-ms 100]` (stdlib only, no `tokitty` import) does, in order, with scratch env only (`LOCALAPPDATA=<work>/localappdata` on Windows; `HOME=<work>/home`, `XDG_CONFIG_HOME=<work>/xdg` elsewhere) and a 60 s timeout on every child:
  1. Locates the GUI exe (`Tokitty.exe` / `Tokitty.app/Contents/MacOS/Tokitty` / `tokitty`) and `tokitty-hook[.exe]` beside it; fails if either is missing.
  2. Runs `<gui> --self-check`; requires exit 0 and all checks ok.
  3. Writes `accounts.json` into the state dir computed the way `tokitty/paths.py` does for that env (`<LOCALAPPDATA>/Tokitty`, `<HOME>/Library/Application Support/Tokitty`, `<XDG_CONFIG_HOME>/tokitty`) naming `<work>/claude-home`.
  4. Runs `<gui> --install-hooks`; requires exit 0; `claude-home/tokitty/hook_writer.py` byte-identical to the repo's `tokitty/hook_writer.py`; all 7 `HOOK_EVENTS` present, each with an exec-form hook whose `command` resolves to the extracted `tokitty-hook` and exists.
  5. Times the registered hook argv (`[command] + args`) exactly as the spike did: 1 cold run (fresh session id, expect `seq` 1), then 20 warm runs on one session id asserting exit 0, empty stdout, `seq == i`; payload `{"session_id", "hook_event_name": "PreToolUse", "tool_name": "Bash"}`; `time.perf_counter` around `subprocess.run`. Logs every value; fails if the median exceeds `--gate-ms`.
  6. Runs every registered hook with its own event name, checking the state file (and that `SessionEnd` removes it).
  7. Autostart: `<gui> --install-autostart`, then asserts the registration points at the extracted GUI exe (Linux: `Exec=` line of `<XDG_CONFIG_HOME>/autostart/tokitty.desktop`; macOS: `ProgramArguments == [gui]` in `<HOME>/Library/LaunchAgents/com.nickwolf.tokitty.plist`; Windows: `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Tokitty` equals the quoted exe path), that no `autostart_launcher.pyw` exists in the state dir, then `--uninstall-autostart`. On Windows this step runs only when `CI=true` and is skipped with a logged reason otherwise, because the Run key cannot be pointed at a scratch location.
  8. Writes the JSON report (sizes, every timing, cold, median, min, max, pass/fail per step) and exits nonzero on any failure.
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

### Task 7: macOS Keychain job

**Goal:** A `workflow_dispatch`-only job that tests spec Q6's prediction (no Keychain prompt per release) against a synthetic keychain.

**Files:**
- Create: `freeze/keychain_check.py`
- Modify: `.github/workflows/release.yml` (new job `keychain`)

**Acceptance Criteria:**
- [ ] Job `keychain` runs on `macos-latest` only when `github.event_name == 'workflow_dispatch'`, independent of the build matrix.
- [ ] Builds the spec twice from the same checkout with `TOKITTY_BUILD_ID=A` and `B` into `dist-a`/`dist-b` (separate `--workpath`), logs `codesign -dv --verbose=4` for both `Contents/MacOS/Tokitty` binaries, and fails if their CDHashes are equal (the test would prove nothing).
- [ ] `keychain_check.py` follows spec Q6 steps 2 to 6 exactly: throwaway `tk.keychain-db` (password `ci`), unlocked, first in the user search list; three items under service `Claude Code-credentials`, one at a time (`trusted` with `-T /usr/bin/security`, `negative` with `-T ''`, `binary-only` with `-T <build A binary>`), each holding fake credentials JSON in the shape `tokitty/credentials.py` parses, with `expiresAt` in the past (epoch ms); `security dump-keychain -a tk.keychain-db` logged before each run; A and B each run with `--debug-print` under a scratch `HOME` holding no `accounts.json`, no `~/.claude/.credentials.json`, and with every credentials-path override env var the code reads (the implementer greps `credentials.py` and `__main__.py` for `os.environ`) removed from the child env; 30 s external timeout, timeout recorded as "would prompt".
- [ ] Classifies each run from `--debug-print` output: `status: stale_token` plus a `credentials source:` line naming the Keychain is a pass-through read; `status: keychain_denied` or timeout is "would prompt"; anything else is a harness error. Verdict table printed and written to `$GITHUB_STEP_SUMMARY`. Exit nonzero if `negative` passes (broken instrument) or on any harness error. A disproved prediction (B denied where A passed, or `binary-only` passes for A) is reported as DISPROVED with exit 0 so the evidence is kept; the controller reads the verdict, not the job colour.
- [ ] The keychain is deleted and the search list restored in a `finally`.
- [ ] Before writing, the implementer confirms the exact `security add-generic-password` account name `keychain.py` looks up (it passes `account=None` today, so the item's account attribute must not matter; verify in `_base_command`) and the fake credentials schema from `credentials.py`, and cites both in the report.

**Verify:** `python3 -m py_compile freeze/keychain_check.py`; actionlint clean. It can only truly run on the macOS runner during the dry run.

**Steps:**

- [ ] **Step 1:** read `tokitty/keychain.py`, `tokitty/credentials.py` (`resolve_credentials_source`, the expiry check at `credentials.py:192`), and `debug_print` in `__main__.py:70-100`. Write `freeze/keychain_check.py` with `--build-a`, `--build-b`, `--work` args, stdlib only.
- [ ] **Step 2:** add the job to `release.yml`. Commit `git commit -m "Add a macOS job that checks Keychain access across two builds"`.

---

### Task 8: README

**Goal:** Install instructions for the downloaded app, per OS, with the decided notes.

**Files:**
- Modify: `README.md`

**Acceptance Criteria:**
- [ ] An "Install" section first: download from GitHub Releases, archive names per OS and architecture (Apple Silicon takes `macos-arm64`, Intel Macs `macos-x86_64`), unzip anywhere, run `Tokitty.exe` / `Tokitty.app` / `tokitty`.
- [ ] First launch per OS: Windows SmartScreen (More info, Run anyway); macOS 15: open once, then System Settings, Privacy & Security, Open Anyway, or `xattr -dr com.apple.quarantine Tokitty.app`; Linux: nothing, tray is X11 (xorg) only in the downloaded build.
- [ ] The three notes, in plain words: hooks from the downloaded app need Claude Code 2.1.139 or newer; restart open Claude Code sessions after updating or moving Tokitty (they keep the old hook command, and log hook errors if the old folder is gone); on macOS, move Tokitty to Applications before turning on Start at login or adding an account.
- [ ] A Claude Code home inside WSL keeps using WSL's `python3` for its hook, even with the Windows download.
- [ ] The existing `python -m tokitty` / `pythonw.exe -m tokitty` instructions move under a "Running from source" heading presented as the developer path. Nothing else in the README changes.
- [ ] Playbook rules: no em-dashes, no hard-wrapped paragraphs, no bold lead-ins on every item. `grep -n '—' README.md` returns nothing new.

**Verify:** `git diff README.md` reviewed by the controller against the playbook; `grep -c '—' README.md` unchanged from before.

**Steps:**

- [ ] **Step 1:** read `README.md` in full and `/mnt/c/Tools/docs/conventions/public_writing_playbook.md`. Edit only the sections named above. Commit `git commit -m "Document installing the downloaded app"`.

---

### Task 9: Manual gate on Windows (Nick)

Not a subagent task. After the dry run is green, the controller hands Nick exact steps: download the Windows dry-run artifact, extract to a new folder, launch `Tokitty.exe`, add an account in the dialog, confirm the WSL home still gets the `python3` hook (`settings.json` under `\\wsl.localhost\...`), confirm the cat reacts in a restarted Claude Code session, toggle Start at login, reboot, confirm it launches from the new folder.

---

## Execution order and review gates

Tasks run in order 1 to 8, one Sonnet subagent each, spec review then code-quality review, commit between tasks, full suite after each. Task 1 Step 7 is a hard gate: if `_internal` is not shared, stop and report before Task 2. After Task 8 the controller asks Nick before pushing the branch and before triggering `release.yml` via `workflow_dispatch`, then reads the dry-run logs (hook median per OS against 100 ms, keychain verdict), then hands Nick Task 9.
