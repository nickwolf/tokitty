#!/usr/bin/env python3
"""Stdlib-only verifier for an extracted Tokitty release artifact (#48).

Runs the frozen GUI and hook executables exactly as a real install would:
from an extracted onedir build, against a scratch state dir, through a
scratch Claude Code home. Never imports the tokitty package -- every claim
this makes about the bundle is checked from the outside, against files on
disk and subprocess output, the same way CI or a user would see it.

Usage:
  verify_artifact.py --app-dir <extracted-dir> --work <scratch-dir> \\
      --report <report.json> [--gate-ms 100] [--overhead-gate-ms N] \\
      [--repo-root <repo>]

--app-dir is the extraction root: the directory that directly contains the
GUI executable on Windows and Linux (dist/Tokitty, dist/tokitty), or the
directory containing Tokitty.app on macOS. --repo-root defaults to the
parent of this script's own directory (freeze/), used only to byte-compare
the bundled hook_writer.py against the repo's copy.

--overhead-gate-ms replaces the absolute --gate-ms gate with a
baseline-relative one (median of the paired frozen - baseline differences
<= overhead_gate_ms), for runners where only the overhead over a same-runner
plain-Python hook is meaningful, not the absolute number. Nothing here
checks the platform or OS name to decide which gate applies -- the caller
(release.yml) passes the flag only for the matrix entries it wants it on.

Steps, in order (stops at the first failure, always writes the report):
  1. locate the GUI and hook executables beside each other
  2. `<gui> --self-check` with no QT_QPA_PLATFORM: qt and qt_root must pass and
     the report must say "toolkits": ["qt"] (Qt is the only toolkit, #142)
  2b. bundle_contents: the licence files, the font, the LGPL packages as
      source, the Qt trim and the platform plugin (#115), nothing from Tcl/Tk,
      Pillow, pystray, pyobjc or python-xlib, and on Linux the xcb libraries
  3. fail-closed accounts.json preflight, before anything can write hooks
  4. `<gui> --install-hooks`, checked against the repo and the state dir
  4b. a second simulated release, extracted separately, repoints `current`
  5. hook_timing: one interleaved round (#158, #160). A cold run of the
     registered PreToolUse hook argv and of a copy of the repo's own
     tokitty/hook_writer.py (the baseline, run with sys.executable), then 30
     warm pairs that each run both, alternating which goes first. Runner
     slowdowns hit both sides of a pair, and the per-pair difference
     (frozen - baseline) cancels them. Reports the frozen side plus the
     paired overhead. Measurement only (no gate is judged here).
  5b. hook_timing_baseline: reports the baseline side of the same round
      (no separate timing loop). diff_ms is the median of the paired
      differences. Informational, like 5.
  5c. hook_timing_gate: judges the round. Absolute gate (default):
      frozen median <= gate_ms. Overhead gate (--overhead-gate-ms given):
      the median of the paired differences <= overhead_gate_ms; the baseline
      is required for it, so a baseline that failed to measure fails it
      closed. If the first round misses the gate, one more full round is
      measured on fresh session ids and judged again; the gate passes if the
      retry passes. A measurement error (a hook crashed, a bad seq) fails
      at once with no retry. Records which gate applied, its threshold, the
      deciding round's numbers, and a summary of every round.
  6. run every registered hook by its own event name
  7. autostart install/uninstall round trip
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

HOOK_EVENTS = [
    ("UserPromptSubmit", ""),
    ("PreToolUse", ""),
    ("PostToolUse", ""),
    ("Notification", "permission_prompt"),
    ("Stop", ""),
    ("SubagentStop", ""),
    ("SessionEnd", ""),
]

HOOK_RUNNER_NAME = "tokitty-hook"
CHILD_TIMEOUT = 60

# The platform plugin each OS must ship (file stem), and the library names
# that mean QtDBus is present (binding, Windows/Linux library, framework binary).
QT_PLATFORM_PLUGINS = {"win32": "qwindows", "darwin": "libqcocoa", "linux": "libqxcb"}
QT_DBUS_STEMS = ("QtDBus", "Qt6DBus", "libQt6DBus")
LGPL_SOURCE_FILES = ("PySide6/__init__.py", "shiboken6/__init__.py")

# The Stream Dock key font and its notice, under tokitty/ in the bundle.
FONT_FILES = ("tokitty/streamdock/fonts/Aileron-Regular.ttf", "tokitty/streamdock/fonts/NOTICE.txt")

# The xcb libraries the Linux build must carry in _internal/ (the build runner
# installs them and PyInstaller collects them), so a runner change cannot
# silently drop one and leave a user to install it (#142 decision 6).
LINUX_XCB_LIBS = (
    "libxcb-cursor.so.0", "libxcb-shape.so.0", "libxcb-icccm.so.4", "libxcb-keysyms.so.1",
    "libxcb-xkb.so.1", "libxkbcommon-x11.so.0",
    # Qt links glib; PyInstaller collects it only if the build host has it, and a
    # bare desktop-less system does not (found in the #142 container check).
    "libglib-2.0.so.0",
)

# Names that must not appear anywhere in the app (Qt is the only toolkit).
# Matched against each file or folder name by forbidden_names().
FORBIDDEN_EXACT = frozenset(("tkinter", "AppKit", "Foundation", "tcl", "tk", "_tcl_data", "_tk_data", "tcl86t.dll", "tk86t.dll", "PIL", "pillow.libs", "pystray", "objc", "Xlib"))
FORBIDDEN_PREFIXES = ("libtcl", "libtk", "_tkinter", "pyobjc_")

# Only present on Windows Python 3.8+; guarded so this stays importable on
# POSIX, where stat has neither the tag nor st_reparse_tag.
_MOUNT_POINT_TAG = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)

_DESKTOP_RESERVED_CHARS = set(" \t\n\"'\\><~|&;$*?#()`")

WINDOWS_RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
MAC_LAUNCH_AGENT_PLIST = "com.nickwolf.tokitty.plist"


# ---------------------------------------------------------------------------
# small helpers mirroring tokitty's own logic (duplicated on purpose: this
# script must never import tokitty, so any bundle-breaking regression in the
# real code would otherwise go unnoticed).
# ---------------------------------------------------------------------------


def _is_link(path) -> bool:
    """True for a symlink, or on Windows specifically a directory junction
    (not any reparse point). False for a path that doesn't exist. Mirrors
    tokitty.runner_link._is_link."""
    path = str(path)
    if sys.platform == "win32":
        try:
            st = os.lstat(path)
        except OSError:
            return False
        return getattr(st, "st_reparse_tag", None) == _MOUNT_POINT_TAG
    return os.path.islink(path)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _stable_runner_path(state_dir, platform: str) -> str:
    """Mirrors tokitty.hooks_install.stable_runner_path."""
    if platform == "win32":
        return str(PureWindowsPath(str(state_dir)) / "current" / (HOOK_RUNNER_NAME + ".exe"))
    return str(PurePosixPath(str(state_dir)) / "current" / HOOK_RUNNER_NAME)


def _quote_desktop_arg(arg: str) -> str:
    """Mirrors tokitty.autostart._quote_desktop_arg."""
    if not any(ch in _DESKTOP_RESERVED_CHARS for ch in arg):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _render_exec_line(command) -> str:
    return " ".join(_quote_desktop_arg(a) for a in command)


def _sessions_dir_from_argv(argv):
    for i, arg in enumerate(argv):
        if arg == "--sessions-dir" and i + 1 < len(argv):
            return argv[i + 1]
    raise ValueError(f"--sessions-dir not found in {argv!r}")


def _hook_argv(settings_data, event, matcher, expected_command, sessions_dir_expected):
    """Picks the hook entry that matches exactly: matcher, command == the
    stable runner path --install-hooks was proved to write, and args == the
    expected --sessions-dir pair. Never "the first command hook" -- a stray
    extra entry with the right type but wrong matcher/command/args must not
    get run in its place."""
    entries = settings_data["hooks"][event]
    expected_args = ["--sessions-dir", sessions_dir_expected]
    for entry in entries:
        if entry.get("matcher") != matcher:
            continue
        for h in entry.get("hooks", []):
            if h.get("type") != "command":
                continue
            if h.get("command") != expected_command:
                continue
            if h.get("args") != expected_args:
                continue
            return [h["command"]] + list(h.get("args", []))
    raise KeyError(
        f"no validated command hook for {event} (matcher={matcher!r}, "
        f"command={expected_command!r}, args={expected_args!r}): {entries!r}"
    )


def _release_b_realpath_check(ctx):
    """Steps 5 and 6 run through the link at release-b: confirm the
    stable command path still resolves inside release-b before either
    step trusts it, rather than assuming step_second_release's repoint
    held."""
    command = ctx["expected_command"]
    release_b_dir = ctx.get("release_b_dir")
    if release_b_dir is None:
        return "release_b_dir not set on ctx; step_second_release must run first"
    real = os.path.realpath(command)
    release_b_real = os.path.realpath(str(release_b_dir))
    if not _is_relative_to(Path(real), Path(release_b_real)):
        return f"{command} resolves to {real}, expected under release-b {release_b_real}"
    return None


def _dir_size(path: Path) -> int:
    """Apparent size of every file directly under path, matching `du -sb`:
    os.lstat (not getsize/stat) so a symlink beside its own target -- as
    PyInstaller's onedir layout has for several shared libraries -- counts
    the link's own tiny size once, rather than the target's size twice."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.lstat(fp).st_size
            except OSError:
                pass
    return total


def _scratch_env(work: Path) -> dict:
    env = dict(os.environ)
    if sys.platform == "win32":
        env["LOCALAPPDATA"] = str(work / "localappdata")
        env["USERPROFILE"] = str(work / "home")
    else:
        env["HOME"] = str(work / "home")
        env["XDG_CONFIG_HOME"] = str(work / "xdg")
    return env


def _work_dir_unusable_reason(work: Path):
    """None if `work` is safe to adopt as a fresh scratch root: it does not
    exist yet, or it exists as a plain, empty directory. Anything else
    (a file, a link/junction, a non-empty directory) is refused, so no step
    can ever follow, overwrite, or delete state a previous run -- or a real
    install -- left at that path."""
    if not os.path.lexists(str(work)):
        return None
    if _is_link(work):
        return f"--work {work} exists and is a link/junction, refusing to reuse it"
    if not work.is_dir():
        return f"--work {work} exists and is not a directory"
    if any(work.iterdir()):
        return f"--work {work} exists and is not empty"
    return None


def run_child(argv, env, input_bytes=None, timeout=CHILD_TIMEOUT):
    """subprocess.run wrapper with a uniform (proc, error) return so every
    step can handle a timeout or launch failure the same way. subprocess.run
    already kills the child and waits for it before raising TimeoutExpired,
    so no separate kill is needed here."""
    try:
        proc = subprocess.run(argv, input=input_bytes, capture_output=True, env=env, timeout=timeout)
        return proc, None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s: {argv!r}"
    except OSError as exc:
        return None, f"failed to launch {argv!r}: {exc}"


def _remove_current_link(state_dir) -> None:
    """Removes the `current` link only -- never shutil.rmtree, which on
    Windows would follow a junction and could delete the real release
    directory it points at."""
    link = Path(state_dir) / "current"
    if not os.path.lexists(str(link)):
        return
    if not _is_link(link):
        return
    if sys.platform == "win32":
        os.rmdir(str(link))
    else:
        os.unlink(str(link))


def _locate_binaries(app_dir: Path):
    plat = sys.platform
    if plat == "win32":
        gui = app_dir / "Tokitty.exe"
        hook = app_dir / "tokitty-hook.exe"
    elif plat == "darwin":
        macos_dir = app_dir / "Tokitty.app" / "Contents" / "MacOS"
        gui = macos_dir / "Tokitty"
        hook = macos_dir / "tokitty-hook"
    else:
        gui = app_dir / "tokitty"
        hook = app_dir / "tokitty-hook"
    return gui, hook


def _pyproject_version(repo_root: Path):
    match = re.search(r'^version\s*=\s*"([^"]+)"',
                      (repo_root / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def bundle_version_problem(plist_data: dict, build_id, pyproject_version):
    """Why a macOS bundle's Info.plist version is wrong, or None (#98). Both
    version keys must equal pyproject.toml, and a release tag must equal it
    too. A dry run's build ID isn't a version, so only pyproject applies."""
    expected = {"CFBundleShortVersionString": pyproject_version, "CFBundleVersion": pyproject_version}
    actual = {key: plist_data.get(key) for key in expected}
    if actual != expected:
        return f"Info.plist has {actual}, pyproject.toml says {pyproject_version}"
    if re.fullmatch(r"v\d+\.\d+\.\d+", build_id or "") and build_id != f"v{pyproject_version}":
        return f"tag {build_id} but pyproject.toml says {pyproject_version}"
    return None


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


def step_locate_binaries(ctx):
    gui, hook = _locate_binaries(ctx["app_dir"])
    missing = [str(p) for p in (gui, hook) if not p.is_file()]
    if missing:
        return {"ok": False, "detail": f"missing executable(s): {', '.join(missing)}"}
    ctx["gui"] = gui
    ctx["hook"] = hook
    ctx["release_dir"] = gui.parent
    return {"ok": True, "detail": {"gui": str(gui), "hook": str(hook), "release_dir": str(gui.parent)}}


def _self_check_report(gui, env):
    """Runs `<gui> --self-check` and applies the rules every run shares: exit 0,
    a JSON report with checks, tk_root present, nothing failed, a state_dir.
    Returns (report, None) or (None, why not)."""
    proc, err = run_child([str(gui), "--self-check"], env)
    if err:
        return None, err
    if proc.returncode != 0:
        # A failed entry exits 1 with the report on stdout and nothing on
        # stderr, so name the failed entries rather than only the exit code.
        try:
            report = json.loads(proc.stdout.decode("utf-8").strip())
            failed = {k: v for k, v in report["checks"].items() if not v.get("ok")}
        except Exception:  # noqa: BLE001 - no report: fall back to the raw output
            failed = None
        if failed is not None:
            return None, f"exit {proc.returncode}, failing self-check(s): {failed}"
        return None, f"exit {proc.returncode}, stderr={proc.stderr!r}, stdout={proc.stdout[-2000:]!r}"
    try:
        data = json.loads(proc.stdout.decode("utf-8").strip())
    except Exception as exc:
        return None, f"could not parse self-check JSON ({exc}); stdout={proc.stdout!r}"
    checks = data.get("checks")
    if not isinstance(checks, dict) or not checks:
        return None, f"self-check report has no checks: {data!r}"
    if "tk_root" not in checks:
        return None, f"self-check report missing tk_root check: {data!r}"
    failed = {k: v for k, v in checks.items() if not v.get("ok")}
    if failed:
        return None, f"failing self-check(s): {failed}"
    if not data.get("state_dir"):
        return None, f"self-check report missing state_dir: {data!r}"
    return data, None


def step_self_check(ctx):
    # TOKITTY_SELF_CHECK_TK=1 makes self-check also open a real Qt window
    # (checks qt_root and its alias tk_root), not just import Qt: a bundle
    # whose platform plugin can't load must fail here, not slip through as a
    # pass. On Linux CI runs this under xvfb-run, which wraps this whole
    # process so DISPLAY is already set for this child. Windows and macOS
    # hosted runners provide a real GUI session directly. The platform is
    # Qt's own choice, not whatever the runner exported.
    env = dict(ctx["env"])
    env["TOKITTY_SELF_CHECK_TK"] = "1"
    env.pop("QT_QPA_PLATFORM", None)
    env.pop("WAYLAND_DISPLAY", None)
    data, problem = _self_check_report(ctx["gui"], env)
    if problem:
        return {"ok": False, "detail": problem}
    ctx["state_dir"] = Path(data["state_dir"])
    ctx["self_check_report"] = data
    ctx["toolkits"] = data.get("toolkits")
    # The report's own declaration is checked rather than trusted: a build
    # that still carried Tk would say so here.
    if ctx["toolkits"] != ["qt"]:
        return {"ok": False, "detail": f"the bundle declares toolkits {ctx['toolkits']}, expected ['qt']"}
    expected = ctx.get("expect_toolkits")
    if expected is not None and expected != ["qt"]:
        return {"ok": False, "detail": f"--expect-toolkits {expected} given; Qt is the only toolkit"}
    checks = data["checks"]
    missing = [name for name in ("qt", "qt_root") if name not in checks]
    if missing:
        return {"ok": False, "detail": f"self-check report missing {missing}: {data!r}"}
    ctx["qt_version"] = checks["qt"].get("detail")
    return {"ok": True, "detail": data}


def _load_bundle_data(repo_root: Path):
    """freeze/bundle_data.py, loaded by path: it is the one list of what the
    trim drops, shared with the spec, and does not import tokitty."""
    path = repo_root / "freeze" / "bundle_data.py"
    spec = importlib.util.spec_from_file_location("bundle_data_for_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle_roots(app_dir: Path, plat: str):
    """(label, folder) pairs that can hold PySide6 and the LGPL source
    packages: _internal on Windows and Linux, and on macOS either of
    Contents/Frameworks and Contents/Resources (PyInstaller puts them in one
    and links the other)."""
    if plat == "darwin":
        contents = app_dir / "Tokitty.app" / "Contents"
        return [("Contents/Frameworks", contents / "Frameworks"), ("Contents/Resources", contents / "Resources")]
    return [("_internal", app_dir / "_internal")]


def licenses_dir(app_dir: Path, plat: str) -> Path:
    if plat == "darwin":
        return app_dir / "Tokitty.app" / "Contents" / "Resources" / "licenses"
    return app_dir / "licenses"


def dangling_links(root: Path, limit: int = 20):
    """Symlinks under `root` whose target does not exist (a trimmed library
    leaves one behind), up to `limit`."""
    found = []
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(folder, name)
            if os.path.islink(path) and not os.path.exists(path):
                found.append(os.path.relpath(path, root))
                if len(found) >= limit:
                    return found
    return found


def pyside6_files(base: Path):
    """Every file and symlink under base/PySide6 as (path relative to base with
    forward slashes, absolute path), the form qt_entry_dropped judges."""
    top = base / "PySide6"
    found = []
    for folder, dirs, files in os.walk(top):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(folder, d))]:
            path = Path(folder) / name
            found.append((path.relative_to(base).as_posix(), path))
    return found


def trim_problems(bundle_data, roots, plat: str):
    """Problems with the Qt trim across the folders that hold PySide6: a file
    the trim drops is present, the platform plugin is missing, or QtDBus is
    missing (required on macOS and Linux, where QtGui links it; on Windows it
    is only reported). Returns (problems, info)."""
    problems, info = [], {}
    plugin_stem = QT_PLATFORM_PLUGINS[plat if plat in ("win32", "darwin") else "linux"]
    plugin_found = dbus_found = False
    walked = []
    for label, base in roots:
        if not (base / "PySide6").is_dir():
            continue
        walked.append(label)
        for rel, _path in pyside6_files(base):
            if bundle_data.qt_entry_dropped(rel):
                problems.append(f"{label}/{rel} is one the trim drops")
            parts = rel.split("/")
            name = parts[-1]
            stem = name.split(".", 1)[0]
            if stem == plugin_stem and "platforms" in parts[:-1]:
                plugin_found = True
            if stem in QT_DBUS_STEMS:
                dbus_found = True
    info["pyside6_in"] = walked
    if not walked:
        problems.append("no PySide6 folder in " + ", ".join(label for label, _ in roots))
    if not plugin_found:
        problems.append(f"the platform plugin {plugin_stem} is missing from PySide6/**/platforms")
    info["qt_dbus"] = dbus_found
    if not dbus_found and plat != "win32":
        problems.append("QtDBus is missing (QtGui links it on macOS and Linux)")
    return problems, info


def forbidden_names(root: Path, limit: int = 20):
    """Paths under `root` named for Tcl/Tk, _tkinter, Pillow, pystray, pyobjc or
    python-xlib, relative to `root`, up to `limit`. Does not follow symlinks."""
    found = []
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            if name in FORBIDDEN_EXACT or name.startswith(FORBIDDEN_PREFIXES):
                found.append(os.path.relpath(os.path.join(folder, name), root))
                if len(found) >= limit:
                    return found
    return found


def step_bundle_contents(ctx):
    plat = sys.platform
    app_dir = ctx["app_dir"]
    bundle_data = _load_bundle_data(ctx["repo_root"])
    roots = bundle_roots(app_dir, plat)
    problems, detail = [], {}

    licenses = licenses_dir(app_dir, plat)
    absent = [name for name in bundle_data.LICENSE_FILES if not (licenses / name).is_file()]
    if absent:
        problems.append(f"{licenses} is missing {absent}")
    notice = licenses / "Qt-NOTICE.txt"
    qt_version = ctx.get("qt_version")
    if qt_version and notice.is_file():
        if qt_version not in notice.read_text(encoding="utf-8", errors="replace"):
            problems.append(f"Qt-NOTICE.txt does not name Qt {qt_version}, the version the bundle reports")
        detail["notice_names_qt"] = qt_version

    # PySide6 and shiboken6 are LGPL-3.0, so they ship as source files a user
    # can replace, not inside the PyInstaller archive.
    source_in = {}
    for rel in LGPL_SOURCE_FILES:
        hits = [label for label, base in roots if (base / rel).is_file()]
        if hits:
            source_in[rel] = hits[0]
        else:
            problems.append(f"{rel} is not a plain file under {', '.join(label for label, _ in roots)}")
    detail["lgpl_source_in"] = source_in

    forbidden = forbidden_names(app_dir)
    if forbidden:
        problems.append(f"the bundle carries what Qt-only builds leave out: {forbidden}")

    if plat.startswith("linux"):
        internal = roots[0][1]
        absent_libs = [name for name in LINUX_XCB_LIBS if not os.path.lexists(internal / name)]
        if absent_libs:
            problems.append(f"_internal is missing the xcb libraries {absent_libs}")

    # The Stream Dock font sits under tokitty/ in _internal on Windows and
    # Linux and in either macOS root; the renderer refuses to draw without it.
    for rel in FONT_FILES:
        if not any((base / rel).is_file() for _label, base in roots):
            problems.append(f"{rel} is missing from {', '.join(label for label, _ in roots)}")

    if plat.startswith("linux") and os.environ.get("TOKITTY_QT_TRIM", "1") != "0":
        internal = roots[0][1]
        stray = sorted(name for name in bundle_data.GTK_THEME_LIBS if os.path.lexists(internal / name))
        if stray:
            problems.append(f"the GTK 3 theme libraries are still in _internal: {stray}")

    # A dispatch with the trim off (TOKITTY_QT_TRIM=0, for measuring) keeps
    # what the trim would drop on purpose; everything else is still checked.
    if os.environ.get("TOKITTY_QT_TRIM", "1") == "0":
        detail["trim_check"] = "skipped: TOKITTY_QT_TRIM=0"
    else:
        trim, info = trim_problems(bundle_data, roots, plat)
        problems += trim
        detail.update(info)

    dangling = dangling_links(app_dir)
    if dangling:
        problems.append(f"dangling symlinks: {dangling}")

    if problems:
        detail["problems"] = problems
    return {"ok": not problems, "detail": detail}


def step_preflight_accounts(ctx):
    # Resolve before the containment check: an unresolved state_dir could
    # contain ".." or a symlink component that makes a naive prefix
    # comparison pass for a path that isn't really under `work`.
    state_dir = ctx["state_dir"].resolve()
    work = ctx["work"].resolve()
    if not (state_dir == work or _is_relative_to(state_dir, work)):
        return {"ok": False, "detail": f"state_dir {state_dir} does not sit under work {work}"}

    claude_home = work / "claude-home"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        accounts_path = state_dir / "accounts.json"
        payload = {"accounts": [{"name": "verify", "config_dir": str(claude_home), "provider": "claude"}]}
        accounts_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        read_back = json.loads(accounts_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "detail": f"accounts.json round trip failed: {exc}"}

    accounts = read_back.get("accounts") if isinstance(read_back, dict) else None
    if not (isinstance(accounts, list) and len(accounts) == 1 and accounts[0].get("config_dir") == str(claude_home)):
        return {"ok": False, "detail": f"accounts.json does not name only {claude_home}: {read_back!r}"}

    # Only now is it proved that state_dir resolves under work: commit the
    # resolved path and set the flag that lets main()'s cleanup touch
    # `<state_dir>/current`. Before this point ctx["state_dir"] came
    # straight from the frozen exe's self-check report and must never be
    # used to remove anything -- that report is not trusted input.
    ctx["state_dir"] = state_dir
    ctx["state_dir_verified"] = True
    ctx["claude_home"] = claude_home
    return {"ok": True, "detail": {"accounts_json": str(accounts_path), "claude_home": str(claude_home)}}


def _check_settings_hooks(settings_data, expected_command, sessions_dir_expected):
    hooks = settings_data.get("hooks")
    if not isinstance(hooks, dict):
        return f"settings.json has no hooks object: {settings_data!r}"
    for event, matcher in HOOK_EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list) or not entries:
            return f"hooks.{event} missing or empty"
        found = None
        for entry in entries:
            if entry.get("matcher") != matcher:
                continue
            for h in entry.get("hooks", []):
                if h.get("type") == "command" and h.get("command") == expected_command:
                    found = h
        if found is None:
            return f"hooks.{event} has no exec-form entry (matcher={matcher!r}, command={expected_command!r}): {entries!r}"
        if found.get("args") != ["--sessions-dir", sessions_dir_expected]:
            return f"hooks.{event} args {found.get('args')!r} != expected sessions dir {sessions_dir_expected!r}"
    return None


def _warning_lines(proc) -> list:
    """Every stderr line containing "warning:", lowercase-matched. A
    fallback-path warning (link fallback, spec Q2a addendum) is never
    silent, so its absence is exactly what proves --install-hooks took
    the stable <state dir>/current path instead of falling back to a
    release-specific one."""
    text = proc.stderr.decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if "warning:" in line.lower()]


def step_install_hooks(ctx):
    proc, err = run_child([str(ctx["gui"]), "--install-hooks"], ctx["env"])
    if err:
        return {"ok": False, "detail": err}
    if proc.returncode != 0:
        return {"ok": False, "detail": f"--install-hooks exit {proc.returncode}, stderr={proc.stderr!r}"}
    warnings = _warning_lines(proc)
    if warnings:
        return {"ok": False, "detail": f"--install-hooks printed warning(s), stable path not exercised: {warnings}"}

    claude_home = ctx["claude_home"]
    hook_writer_dest = claude_home / "tokitty" / "hook_writer.py"
    hook_writer_src = ctx["repo_root"] / "tokitty" / "hook_writer.py"
    if not hook_writer_dest.is_file():
        return {"ok": False, "detail": f"{hook_writer_dest} missing after --install-hooks"}
    if not hook_writer_src.is_file():
        return {"ok": False, "detail": f"repo hook_writer.py not found at {hook_writer_src}"}
    if hook_writer_dest.read_bytes() != hook_writer_src.read_bytes():
        return {"ok": False, "detail": f"{hook_writer_dest} is not byte-identical to {hook_writer_src}"}

    settings_path = claude_home / "settings.json"
    if not settings_path.is_file():
        return {"ok": False, "detail": f"{settings_path} missing after --install-hooks"}
    settings_bytes = settings_path.read_bytes()
    try:
        settings_data = json.loads(settings_bytes.decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "detail": f"settings.json did not parse: {exc}"}

    expected_command = _stable_runner_path(ctx["state_dir"], sys.platform)
    sessions_dir_expected = f"{claude_home}/tokitty/sessions"
    err_msg = _check_settings_hooks(settings_data, expected_command, sessions_dir_expected)
    if err_msg:
        return {"ok": False, "detail": err_msg}

    current_link = ctx["state_dir"] / "current"
    if not os.path.lexists(str(current_link)):
        return {"ok": False, "detail": f"{current_link} does not exist"}
    if not _is_link(current_link):
        return {"ok": False, "detail": f"{current_link} is not a link (symlink/junction)"}
    link_real = os.path.realpath(str(current_link))
    release_real = os.path.realpath(str(ctx["release_dir"]))
    if link_real != release_real:
        return {"ok": False, "detail": f"current -> {link_real}, expected release dir {release_real}"}
    if not Path(expected_command).exists():
        return {"ok": False, "detail": f"{expected_command} does not exist through the link"}

    ctx["settings_bytes_after_4"] = settings_bytes
    ctx["settings_data"] = settings_data
    ctx["expected_command"] = expected_command
    ctx["sessions_dir_expected"] = sessions_dir_expected
    return {
        "ok": True,
        "detail": {
            "settings_path": str(settings_path),
            "hook_writer_bytes": len(hook_writer_dest.read_bytes()),
            "current_link_target": link_real,
            "command": expected_command,
        },
    }


def step_second_release(ctx):
    release_b_root = ctx["work"] / "release-b"
    if os.path.lexists(str(release_b_root)):
        # The startup guard means work started empty, so reaching this with
        # release-b already present means something outside this run put it
        # there. Never rmtree a link/junction (it would follow it), and
        # never delete anything this step did not itself create this run.
        if _is_link(release_b_root):
            return {"ok": False, "detail": f"{release_b_root} already exists as a link, refusing to remove it"}
        if not ctx.get("release_b_created"):
            return {"ok": False, "detail": f"{release_b_root} already exists and was not created by this run"}
        shutil.rmtree(release_b_root)
    shutil.copytree(ctx["app_dir"], release_b_root, symlinks=True)
    ctx["release_b_created"] = True

    gui2, hook2 = _locate_binaries(release_b_root)
    missing = [str(p) for p in (gui2, hook2) if not p.is_file()]
    if missing:
        return {"ok": False, "detail": f"second release copy missing: {', '.join(missing)}"}
    if sys.platform != "win32":
        # shutil.copytree/copy2 preserves mode bits, but make the intent
        # explicit rather than depend on that.
        os.chmod(gui2, os.stat(gui2).st_mode | 0o111)
        os.chmod(hook2, os.stat(hook2).st_mode | 0o111)

    proc, err = run_child([str(gui2), "--install-hooks"], ctx["env"])
    if err:
        return {"ok": False, "detail": err}
    if proc.returncode != 0:
        return {"ok": False, "detail": f"--install-hooks (release-b) exit {proc.returncode}, stderr={proc.stderr!r}"}
    warnings = _warning_lines(proc)
    if warnings:
        return {"ok": False, "detail": f"--install-hooks (release-b) printed warning(s), stable path not exercised: {warnings}"}

    settings_path = ctx["claude_home"] / "settings.json"
    settings_bytes_2 = settings_path.read_bytes()
    if settings_bytes_2 != ctx["settings_bytes_after_4"]:
        return {"ok": False, "detail": "settings.json changed after the second --install-hooks run"}

    current_link = ctx["state_dir"] / "current"
    link_real = os.path.realpath(str(current_link))
    release2_real = os.path.realpath(str(gui2.parent))
    if link_real != release2_real:
        return {"ok": False, "detail": f"current -> {link_real}, expected release-b dir {release2_real}"}

    # Steps 5 and 6 run through the link at release-b.
    ctx["gui"] = gui2
    ctx["hook"] = hook2
    ctx["release_dir"] = gui2.parent
    ctx["release_b_dir"] = release_b_root
    return {"ok": True, "detail": {"release_b_dir": str(release_b_root), "current_link_target": link_real}}


# Warm (frozen, baseline) pairs per round.
PAIRED_WARM_RUNS = 30


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _side_stats(cold_s, timings):
    ordered = sorted(timings)
    median = _median(timings)
    return {
        "cold_s": cold_s,
        "timings_s": list(timings),
        "min_s": ordered[0],
        "median_s": median,
        "max_s": ordered[-1],
        "median_ms": median * 1000,
    }


def _checked_run(argv, env, session_id, state_path: Path, expected_seq):
    """One hook invocation with the per-run checks every timed hook run gets:
    exit 0, empty stdout, the state file exists, its seq == expected_seq.
    Returns (elapsed_s, error); error is None when every check held."""
    payload = json.dumps(
        {"session_id": session_id, "hook_event_name": "PreToolUse", "tool_name": "Bash"}
    ).encode("utf-8")
    t0 = time.perf_counter()
    proc, err = run_child(argv, env, input_bytes=payload)
    elapsed = time.perf_counter() - t0
    if err:
        return elapsed, err
    if proc.returncode != 0 or proc.stdout != b"":
        return elapsed, f"exit={proc.returncode} stdout={proc.stdout!r}"
    if not state_path.is_file():
        return elapsed, "state file missing"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    if data.get("seq") != expected_seq:
        return elapsed, f"seq {data.get('seq')} != {expected_seq}"
    return elapsed, None


def _time_hook_pair(frozen_argv, frozen_sessions: Path, baseline_argv, baseline_sessions: Path, env, suffix=""):
    """One interleaved round (#158, #160): a cold run of each hook (frozen
    first, then baseline), then PAIRED_WARM_RUNS warm iterations that each
    run both, alternating which goes first (even iteration: frozen then
    baseline; odd: baseline then frozen). Runner slowdowns therefore land on
    both sides of a pair instead of on whichever block happened to be
    running, and the per-pair difference (frozen - baseline) cancels them.
    Each side keeps its own sessions dir and a label ("verify"/"baseline" plus
    `suffix`, so a retry round gets fresh session ids and a clean seq), and
    gets _checked_run's per-run checks. Returns a round dict:
    error / failed_side (None when clean), frozen and baseline side details
    (full stats only for a side that finished all its runs, otherwise just
    the cold_s and timings_s collected so far, plus the error on the failing
    side), paired_overhead_ms, overhead_median_ms (None unless the round was
    clean), and order (one "FB"/"BF" per warm iteration actually started)."""
    sides = {
        "frozen": {"argv": frozen_argv, "dir": frozen_sessions, "label": f"verify{suffix}"},
        "baseline": {"argv": baseline_argv, "dir": baseline_sessions, "label": f"baseline{suffix}"},
    }
    for side in sides.values():
        side["dir"].mkdir(parents=True, exist_ok=True)
        side["cold_state"] = side["dir"] / f"{side['label']}-cold.json"
        side["warm_state"] = side["dir"] / f"{side['label']}-warm.json"
        for state in (side["cold_state"], side["warm_state"]):
            if state.exists():
                state.unlink()
        side["cold_s"] = None
        side["timings"] = []
        side["error"] = None

    overheads_ms = []
    order = []
    failed_side = None
    error = None

    def _finish():
        round_ = {"error": error, "failed_side": failed_side, "order": order, "paired_overhead_ms": overheads_ms}
        for name, side in sides.items():
            complete = side["error"] is None and side["cold_s"] is not None and len(side["timings"]) == PAIRED_WARM_RUNS
            if complete and error is None:
                detail = _side_stats(side["cold_s"], side["timings"])
            else:
                detail = {"cold_s": side["cold_s"], "timings_s": list(side["timings"])}
                if side["error"]:
                    detail["error"] = side["error"]
            round_[name] = detail
        round_["overhead_median_ms"] = _median(overheads_ms) if overheads_ms and error is None else None
        return round_

    for name in ("frozen", "baseline"):
        side = sides[name]
        elapsed, err = _checked_run(side["argv"], env, f"{side['label']}-cold", side["cold_state"], 1)
        side["cold_s"] = elapsed
        if err:
            side["error"] = f"cold run: {err}"
            failed_side, error = name, f"{name} cold run: {err}"
            return _finish()

    for k in range(PAIRED_WARM_RUNS):
        names = ("frozen", "baseline") if k % 2 == 0 else ("baseline", "frozen")
        order.append("FB" if k % 2 == 0 else "BF")
        elapsed_by_side = {}
        for name in names:
            side = sides[name]
            elapsed, err = _checked_run(side["argv"], env, f"{side['label']}-warm", side["warm_state"], k + 1)
            side["timings"].append(elapsed)
            elapsed_by_side[name] = elapsed
            if err:
                side["error"] = f"warm run {k + 1}: {err}"
                failed_side, error = name, f"{name} warm run {k + 1}: {err}"
                return _finish()
        overheads_ms.append((elapsed_by_side["frozen"] - elapsed_by_side["baseline"]) * 1000)
    return _finish()


def _measure_round(ctx, suffix=""):
    setup = ctx["timing_setup"]
    return _time_hook_pair(
        setup["frozen_argv"],
        setup["frozen_sessions"],
        setup["baseline_argv"],
        setup["baseline_sessions"],
        ctx["env"],
        suffix,
    )


def step_hook_timing(ctx):
    """Times the registered PreToolUse hook argv against a copy of the repo's
    own tokitty/hook_writer.py (run with sys.executable, the runner's
    setup-python interpreter) as one interleaved round: a cold run of each,
    then 30 warm pairs in alternating order (#158, #160; see
    _time_hook_pair). This step reports the frozen side in the same fields
    as before (cold_s, timings_s, min_s, median_s, max_s, median_ms, argv)
    plus the paired data (paired_overhead_ms, overhead_median_ms, order);
    step_hook_timing_baseline reports the other side of the same round.
    Measurement only: no gate is judged here (#48 dry run 2, Nick's overhead
    gate decision); that happens in step_hook_timing_gate."""
    realpath_err = _release_b_realpath_check(ctx)
    if realpath_err:
        return {"ok": False, "detail": realpath_err}
    argv = _hook_argv(ctx["settings_data"], "PreToolUse", "", ctx["expected_command"], ctx["sessions_dir_expected"])
    sessions_dir = Path(_sessions_dir_from_argv(argv))

    hook_writer_src = ctx["repo_root"] / "tokitty" / "hook_writer.py"
    if not hook_writer_src.is_file():
        return {"ok": False, "detail": f"repo hook_writer.py not found at {hook_writer_src}"}
    baseline_dir = ctx["work"] / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    hook_writer_copy = baseline_dir / "hook_writer.py"
    shutil.copy2(hook_writer_src, hook_writer_copy)
    baseline_sessions = baseline_dir / "sessions"
    ctx["timing_setup"] = {
        "frozen_argv": argv,
        "frozen_sessions": sessions_dir,
        "baseline_argv": [sys.executable, str(hook_writer_copy), "--sessions-dir", str(baseline_sessions)],
        "baseline_sessions": baseline_sessions,
    }

    rnd = _measure_round(ctx)
    ctx["timing_round"] = rnd
    detail = dict(rnd["frozen"])
    detail["argv"] = argv
    detail["paired_overhead_ms"] = rnd["paired_overhead_ms"]
    detail["overhead_median_ms"] = rnd["overhead_median_ms"]
    detail["order"] = rnd["order"]
    detail["order_note"] = "alternating per warm pair: FB = frozen then baseline, BF = baseline then frozen"
    if rnd["error"]:
        detail["round_error"] = rnd["error"]
    return {"ok": "median_ms" in rnd["frozen"], "detail": detail}


def step_hook_timing_baseline(ctx):
    """Informational only (#48 Task 1, Nick's baseline decision). Does not
    measure anything itself any more: the baseline (a copy of the repo's own
    tokitty/hook_writer.py run with sys.executable) is timed inside
    step_hook_timing's interleaved round, and this reports that side in its
    existing fields (cold_s, timings_s, min_s, median_s, max_s, median_ms,
    argv, sys_executable, frozen_timings_s, frozen_median_ms). diff_ms is
    now the median of the paired (frozen - baseline) differences, not the
    difference of the medians (diff_kind says so). `ok` reports only whether
    the baseline side measured cleanly; main()'s report["ok"] excludes this
    step by name (INFORMATIONAL_STEPS), and step_hook_timing_gate decides
    whether a baseline failure matters."""
    rnd = ctx.get("timing_round")
    if not rnd:
        return {"ok": False, "detail": "hook_timing produced no round to report the baseline from"}
    detail = dict(rnd["baseline"])
    detail["argv"] = ctx["timing_setup"]["baseline_argv"]
    detail["sys_executable"] = sys.executable
    detail["frozen_timings_s"] = rnd["frozen"].get("timings_s")
    detail["frozen_median_ms"] = rnd["frozen"].get("median_ms")
    detail["diff_ms"] = rnd["overhead_median_ms"]
    detail["diff_kind"] = "median of paired differences"
    return {"ok": "median_ms" in rnd["baseline"], "detail": detail}


def _judge_round(rnd, overhead_gate_ms, gate_ms):
    """Judges one round. Returns (ok, detail, measurement_error). A round
    that did not measure cleanly (hook crashed, bad seq, either side) is a
    measurement error: it fails closed here and is never retried."""
    gate_kind = "overhead" if overhead_gate_ms is not None else "absolute"
    detail = {"gate_kind": gate_kind}
    if rnd["error"] or rnd["overhead_median_ms"] is None:
        detail["error"] = f"measurement error: {rnd['error'] or 'round produced no paired data'}"
        return False, detail, True

    frozen_median_ms = rnd["frozen"]["median_ms"]
    baseline_median_ms = rnd["baseline"]["median_ms"]
    overhead_median_ms = rnd["overhead_median_ms"]
    detail.update(
        frozen_median_ms=frozen_median_ms,
        baseline_median_ms=baseline_median_ms,
        overhead_median_ms=overhead_median_ms,
    )
    if overhead_gate_ms is not None:
        detail["overhead_gate_ms"] = overhead_gate_ms
        detail["threshold_ms"] = overhead_gate_ms
        ok = overhead_median_ms <= overhead_gate_ms
        if not ok:
            detail["error"] = (
                f"median paired overhead {overhead_median_ms:.3f}ms exceeds overhead gate {overhead_gate_ms}ms "
                f"(frozen median {frozen_median_ms:.3f}ms, baseline median {baseline_median_ms:.3f}ms)"
            )
    else:
        detail["gate_ms"] = gate_ms
        detail["threshold_ms"] = gate_ms
        ok = frozen_median_ms <= gate_ms
        if not ok:
            detail["error"] = f"median {frozen_median_ms:.3f}ms exceeds gate {gate_ms}ms"
    return ok, detail, False


def step_hook_timing_gate(ctx):
    """Judges the interleaved round step_hook_timing measured (#48 dry run 2,
    Nick's overhead gate decision; interleaving and retry from #158, #160:
    runner slowdowns used to land on one of two back-to-back blocks and move
    the old difference of medians by tens of ms).

    Two gates, chosen by whether --overhead-gate-ms was given (ctx
    "overhead_gate_ms"), never by platform or OS name:
      - absolute (default): frozen median <= gate_ms.
      - overhead (--overhead-gate-ms N): the median of the per-pair
        (frozen - baseline) differences <= N. The baseline is required for
        this gate: a baseline that failed to measure fails it closed rather
        than silently passing.
    Retry: if the first round misses the gate (a real verdict, not a
    measurement error), ONE more full round is measured on fresh session ids
    ("verify-r2", "baseline-r2") and judged the same way; the gate passes if
    the retry passes. A measurement error (hook crashed, bad seq) fails
    immediately with no retry. The detail carries the deciding round's
    gate_kind, threshold_ms, frozen_median_ms, baseline_median_ms,
    overhead_median_ms and error at top level, plus `rounds`, one summary per
    round measured."""
    overhead_gate_ms = ctx.get("overhead_gate_ms")
    gate_ms = ctx["gate_ms"]
    rnd = ctx.get("timing_round")
    if not rnd:
        return {
            "ok": False,
            "detail": {
                "gate_kind": "overhead" if overhead_gate_ms is not None else "absolute",
                "error": "hook_timing did not produce a round to judge",
            },
        }

    def _summary(number, ok, detail):
        keys = ("overhead_median_ms", "frozen_median_ms", "baseline_median_ms", "error")
        return {"round": number, "ok": ok, **{k: detail.get(k) for k in keys}}

    ok, detail, measurement_error = _judge_round(rnd, overhead_gate_ms, gate_ms)
    rounds = [_summary(1, ok, detail)]
    if not ok and not measurement_error:
        rnd2 = _measure_round(ctx, "-r2")
        ctx["timing_round_2"] = rnd2
        ok, detail, _ = _judge_round(rnd2, overhead_gate_ms, gate_ms)
        rounds.append(_summary(2, ok, detail))
    detail["rounds"] = rounds
    return {"ok": ok, "detail": detail}


def step_hook_events(ctx):
    settings_data = ctx["settings_data"]
    realpath_err = _release_b_realpath_check(ctx)
    if realpath_err:
        return {"ok": False, "detail": realpath_err}
    expected_command = ctx["expected_command"]
    sessions_dir_expected = ctx["sessions_dir_expected"]
    results = {}
    for event, matcher in HOOK_EVENTS:
        argv = _hook_argv(settings_data, event, matcher, expected_command, sessions_dir_expected)
        sessions_dir = Path(_sessions_dir_from_argv(argv))
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_id = f"verify-event-{event.lower()}"
        state_file = sessions_dir / f"{session_id}.json"
        if state_file.exists():
            state_file.unlink()

        if event == "SessionEnd":
            create_argv = _hook_argv(settings_data, "PreToolUse", "", expected_command, sessions_dir_expected)
            payload = json.dumps(
                {"session_id": session_id, "hook_event_name": "PreToolUse", "tool_name": "Bash"}
            ).encode("utf-8")
            proc, err = run_child(create_argv, ctx["env"], input_bytes=payload)
            if err:
                return {"ok": False, "detail": f"{event}: setup run: {err}"}
            if proc.returncode != 0 or proc.stdout != b"":
                return {"ok": False, "detail": f"{event}: setup run exit={proc.returncode} stdout={proc.stdout!r}"}
            if not state_file.is_file():
                return {"ok": False, "detail": f"{event}: setup run did not create the state file"}

            payload = json.dumps({"session_id": session_id, "hook_event_name": "SessionEnd"}).encode("utf-8")
            proc, err = run_child(argv, ctx["env"], input_bytes=payload)
            if err:
                return {"ok": False, "detail": f"{event}: {err}"}
            if proc.returncode != 0 or proc.stdout != b"":
                return {"ok": False, "detail": f"{event}: exit={proc.returncode} stdout={proc.stdout!r}"}
            if state_file.exists():
                return {"ok": False, "detail": f"{event}: state file still present after SessionEnd"}
            results[event] = "removed ok"
            continue

        payload = json.dumps(
            {"session_id": session_id, "hook_event_name": event, "tool_name": "Bash"}
        ).encode("utf-8")
        proc, err = run_child(argv, ctx["env"], input_bytes=payload)
        if err:
            return {"ok": False, "detail": f"{event}: {err}"}
        if proc.returncode != 0 or proc.stdout != b"":
            return {"ok": False, "detail": f"{event}: exit={proc.returncode} stdout={proc.stdout!r}"}
        if not state_file.is_file():
            return {"ok": False, "detail": f"{event}: state file not written"}
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if data.get("event") != event or data.get("seq") != 1:
            return {"ok": False, "detail": f"{event}: unexpected state {data!r}"}
        results[event] = "written ok"
    return {"ok": True, "detail": results}


class _RegQueryFailed(RuntimeError):
    """A `reg query` call did not cleanly resolve to "value present" or the
    well-known "value absent" case. Raised instead of returning a fake
    absent result, so a genuine query failure (bad exit for some other
    reason, a timeout, a launch error) always aborts rather than being
    read as "nothing to restore"."""


def _reg_read_tokitty():
    """(value, present) on a clean query. present=False only for reg.exe's
    documented "unable to find the specified registry key or value" exit;
    anything else raises _RegQueryFailed. Only ever exercised on
    windows-latest in CI, never in the Linux rehearsal."""
    try:
        proc = subprocess.run(
            ["reg", "query", WINDOWS_RUN_KEY, "/v", "Tokitty"],
            capture_output=True,
            text=True,
            timeout=CHILD_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise _RegQueryFailed(f"reg query timed out after {CHILD_TIMEOUT}s") from exc
    except OSError as exc:
        raise _RegQueryFailed(f"reg query failed to launch: {exc}") from exc

    if proc.returncode != 0:
        if "unable to find" in proc.stderr.lower():
            return None, False
        raise _RegQueryFailed(
            f"reg query exit {proc.returncode}, stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("Tokitty"):
            parts = line.split(None, 2)
            if len(parts) == 3:
                return parts[2], True
    raise _RegQueryFailed(f"reg query exit 0 but no Tokitty value line found: {proc.stdout!r}")


def _reg_restore_and_verify(saved_value, had_value):
    """Restores the Run key to its pre-test state, then re-queries to
    confirm the restore actually took. Returns an error string on any
    write failure, timeout, or mismatch; None on a verified restore. Never
    swallowed into a bare finally -- the caller must fail the step (and so
    the whole run) on a non-None return."""
    try:
        if had_value:
            proc = subprocess.run(
                ["reg", "add", WINDOWS_RUN_KEY, "/v", "Tokitty", "/t", "REG_SZ", "/d", saved_value, "/f"],
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT,
            )
        else:
            proc = subprocess.run(
                ["reg", "delete", WINDOWS_RUN_KEY, "/v", "Tokitty", "/f"],
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT,
            )
    except subprocess.TimeoutExpired as exc:
        return f"reg restore timed out after {CHILD_TIMEOUT}s: {exc}"
    except OSError as exc:
        return f"reg restore failed to launch: {exc}"

    if proc.returncode != 0:
        # When there was no prior value, --uninstall-autostart already
        # removed it during the round trip, so `reg delete` here finds
        # nothing to delete: that is the saved state, not a failure. The
        # post-restore re-query below still has the final say on whether
        # the value is actually absent.
        already_absent = not had_value and "unable to find" in proc.stderr.lower()
        if not already_absent:
            return f"reg restore exit {proc.returncode}: {proc.stderr!r}"

    try:
        value_after, present_after = _reg_read_tokitty()
    except _RegQueryFailed as exc:
        return f"post-restore verification query failed: {exc}"

    if had_value:
        if not present_after or value_after != saved_value:
            return f"expected {saved_value!r} restored, found present={present_after} value={value_after!r}"
    else:
        if present_after:
            return f"expected value absent after restore, found {value_after!r}"
    return None


def _autostart_round_trip(gui, env, state_dir, plat):
    proc, err = run_child([gui, "--install-autostart"], env)
    if err:
        return {"ok": False, "detail": err}
    if proc.returncode != 0:
        return {"ok": False, "detail": f"--install-autostart exit {proc.returncode}: {proc.stderr!r}"}

    launcher = state_dir / "autostart_launcher.pyw"
    if launcher.exists():
        return {"ok": False, "detail": f"{launcher} should not exist for a frozen build"}

    desktop_path = None
    plist_path = None
    if plat.startswith("linux"):
        desktop_path = Path(env["XDG_CONFIG_HOME"]) / "autostart" / "tokitty.desktop"
        if not desktop_path.is_file():
            return {"ok": False, "detail": f"{desktop_path} not written"}
        expected = f"Exec={_render_exec_line([gui])}"
        lines = desktop_path.read_text(encoding="utf-8").splitlines()
        if expected not in lines:
            return {"ok": False, "detail": f"{desktop_path} Exec line {lines!r} missing {expected!r}"}
    elif plat == "darwin":
        plist_path = Path(env["HOME"]) / "Library" / "LaunchAgents" / MAC_LAUNCH_AGENT_PLIST
        if not plist_path.is_file():
            return {"ok": False, "detail": f"{plist_path} not written"}
        data = plistlib.loads(plist_path.read_bytes())
        if data.get("ProgramArguments") != [gui]:
            return {"ok": False, "detail": f"ProgramArguments {data.get('ProgramArguments')!r} != [{gui!r}]"}
    elif plat == "win32":
        try:
            value, _present = _reg_read_tokitty()
        except _RegQueryFailed as exc:
            return {"ok": False, "detail": f"post-install reg query failed: {exc}"}
        expected = subprocess.list2cmdline([gui])
        if value != expected:
            return {"ok": False, "detail": f"registry value {value!r} != {expected!r}"}
    else:
        return {"ok": False, "detail": f"unsupported platform for autostart: {plat}"}

    proc, err = run_child([gui, "--uninstall-autostart"], env)
    if err:
        return {"ok": False, "detail": err}
    if proc.returncode != 0:
        return {"ok": False, "detail": f"--uninstall-autostart exit {proc.returncode}: {proc.stderr!r}"}

    if plat.startswith("linux") and desktop_path.exists():
        return {"ok": False, "detail": f"{desktop_path} still present after uninstall"}
    if plat == "darwin" and plist_path.exists():
        return {"ok": False, "detail": f"{plist_path} still present after uninstall"}
    if plat == "win32":
        try:
            _value, present = _reg_read_tokitty()
        except _RegQueryFailed as exc:
            return {"ok": False, "detail": f"post-uninstall reg query failed: {exc}"}
        if present:
            return {"ok": False, "detail": "registry value still present after uninstall"}

    return {"ok": True, "detail": "install/uninstall autostart round trip ok"}


def step_autostart(ctx):
    plat = sys.platform
    gui = str(ctx["gui"])
    env = ctx["env"]
    state_dir = ctx["state_dir"]

    if plat == "win32" and os.environ.get("CI") != "true":
        return {"ok": True, "detail": "skipped: HKCU Run key has no scratch location outside CI"}

    saved_value, had_value = (None, False)
    if plat == "win32":
        # A failed pre-check must abort before --install-autostart runs at
        # all: without a trustworthy saved_value/had_value, a later restore
        # could not tell "put it back" from "leave it alone".
        try:
            saved_value, had_value = _reg_read_tokitty()
        except _RegQueryFailed as exc:
            return {"ok": False, "detail": f"pre-check reg query failed, aborting before any write: {exc}"}

    result = {"ok": False, "detail": "autostart step did not complete"}
    try:
        result = _autostart_round_trip(gui, env, state_dir, plat)
    finally:
        if plat == "win32":
            restore_err = _reg_restore_and_verify(saved_value, had_value)
            if restore_err:
                prior = result.get("detail") if isinstance(result, dict) else result
                result = {"ok": False, "detail": f"{prior}; restore failed: {restore_err}"}
    return result


def step_bundle_version(ctx):
    if sys.platform != "darwin":
        return {"ok": True, "detail": "not a macOS bundle"}
    plist_path = ctx["gui"].parent.parent / "Info.plist"
    try:
        data = plistlib.loads(plist_path.read_bytes())
    except Exception as exc:
        return {"ok": False, "detail": f"could not read {plist_path}: {exc}"}
    if data.get("LSUIElement") is not True:
        return {"ok": False, "detail": f"Info.plist LSUIElement is {data.get('LSUIElement')!r}, expected True (agent app)"}
    build_id = (ctx.get("self_check_report") or {}).get("build_id")
    problem = bundle_version_problem(data, build_id, _pyproject_version(ctx["repo_root"]))
    if problem:
        return {"ok": False, "detail": problem}
    return {"ok": True, "detail": {"version": data["CFBundleShortVersionString"], "build_id": build_id}}


STEPS = [
    ("locate_binaries", step_locate_binaries),
    ("self_check", step_self_check),
    ("bundle_contents", step_bundle_contents),
    ("bundle_version", step_bundle_version),
    ("preflight_accounts", step_preflight_accounts),
    ("install_hooks", step_install_hooks),
    ("second_release", step_second_release),
    ("hook_timing", step_hook_timing),
    ("hook_timing_baseline", step_hook_timing_baseline),
    ("hook_timing_gate", step_hook_timing_gate),
    ("hook_events", step_hook_events),
    ("autostart", step_autostart),
]

# Steps whose own ok/fail never counts toward report["ok"] and never stops
# the run on its own (#48 dry run 2): hook_timing and hook_timing_baseline
# are both pure measurements (two views of one interleaved round), run
# unconditionally in that order; only hook_timing_gate, which judges the
# round, decides whether the hook-timing concern passes or fails the
# overall run.
INFORMATIONAL_STEPS = {"hook_timing", "hook_timing_baseline"}


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    os.replace(tmp_path, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--gate-ms", type=float, default=100.0)
    parser.add_argument(
        "--overhead-gate-ms",
        type=float,
        default=None,
        help="replaces --gate-ms with frozen_median_ms <= baseline_median_ms + this value",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument(
        "--expect-toolkits",
        default=None,
        help="kept for the workflow; the only accepted value is qt (the report must say toolkits == [qt] regardless)",
    )
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    # Check the unresolved --work path for being a link/junction before
    # resolving it: .resolve() follows a symlink straight through to its
    # target, so checking afterward would test the target, not the
    # argument a caller actually passed, and silently let a symlinked
    # --work through unrefused.
    work_arg = Path(args.work)
    report_path = Path(args.report)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parent.parent

    unusable_reason = _work_dir_unusable_reason(work_arg)
    if unusable_reason:
        print(f"[verify_artifact] refusing to run: {unusable_reason}", file=sys.stderr)
        write_report(
            report_path,
            {
                "app_dir": str(app_dir),
                "work": str(work_arg),
                "repo_root": str(repo_root),
                "gate_ms": args.gate_ms,
                "overhead_gate_ms": args.overhead_gate_ms,
                "platform": sys.platform,
                "steps": {},
                "ok": False,
                "error": unusable_reason,
            },
        )
        return 1

    work = work_arg.resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = _scratch_env(work)
    for sub in ("home", "xdg", "localappdata"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    ctx = {
        "app_dir": app_dir,
        "work": work,
        "env": env,
        "repo_root": repo_root,
        "gate_ms": args.gate_ms,
        "overhead_gate_ms": args.overhead_gate_ms,
        "expect_toolkits": (
            sorted({t.strip() for t in args.expect_toolkits.split(",") if t.strip()}) if args.expect_toolkits else None
        ),
    }

    report = {
        "app_dir": str(app_dir),
        "work": str(work),
        "repo_root": str(repo_root),
        "gate_ms": args.gate_ms,
        "overhead_gate_ms": args.overhead_gate_ms,
        "platform": sys.platform,
        "steps": {},
    }

    try:
        for name, fn in STEPS:
            print(f"[verify_artifact] running step: {name}", file=sys.stderr)
            try:
                result = fn(ctx)
            except Exception as exc:  # noqa: BLE001 - a step's own bug must still fail closed
                result = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            report["steps"][name] = result
            status = "ok" if result.get("ok") else "FAILED"
            print(f"[verify_artifact] step {name}: {status}", file=sys.stderr)

            if name in INFORMATIONAL_STEPS:
                continue

            if not result.get("ok"):
                break
    finally:
        # Only touch `<state_dir>/current` once step_preflight_accounts has
        # actually proved state_dir resolves under work (P0): before that,
        # ctx["state_dir"] is unverified input straight from the frozen
        # exe's self-check report, and removing a link there could destroy
        # a real install's current release symlink.
        if ctx.get("state_dir_verified"):
            state_dir = ctx.get("state_dir")
            try:
                _remove_current_link(state_dir)
            except OSError as exc:
                report.setdefault("cleanup_errors", []).append(str(exc))

    sizes = {"app_dir_bytes": _dir_size(app_dir)}
    if ctx.get("release_b_dir"):
        sizes["release_b_dir_bytes"] = _dir_size(ctx["release_b_dir"])
    report["sizes"] = sizes

    report["ok"] = bool(report["steps"]) and all(
        s.get("ok") for name, s in report["steps"].items() if name not in INFORMATIONAL_STEPS
    )
    write_report(report_path, report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
