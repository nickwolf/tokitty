#!/usr/bin/env python3
"""Stdlib-only verifier for an extracted Tokitty release artifact (#48).

Runs the frozen GUI and hook executables exactly as a real install would:
from an extracted onedir build, against a scratch state dir, through a
scratch Claude Code home. Never imports the tokitty package -- every claim
this makes about the bundle is checked from the outside, against files on
disk and subprocess output, the same way CI or a user would see it.

Usage:
  verify_artifact.py --app-dir <extracted-dir> --work <scratch-dir> \\
      --report <report.json> [--gate-ms 100] [--repo-root <repo>]

--app-dir is the extraction root: the directory that directly contains the
GUI executable on Windows and Linux (dist/Tokitty, dist/tokitty), or the
directory containing Tokitty.app on macOS. --repo-root defaults to the
parent of this script's own directory (freeze/), used only to byte-compare
the bundled hook_writer.py against the repo's copy.

Steps, in order (stops at the first failure, always writes the report):
  1. locate the GUI and hook executables beside each other
  2. `<gui> --self-check`
  3. fail-closed accounts.json preflight, before anything can write hooks
  4. `<gui> --install-hooks`, checked against the repo and the state dir
  4b. a second simulated release, extracted separately, repoints `current`
  5. time the registered PreToolUse hook argv (cold + 20 warm runs)
  6. run every registered hook by its own event name
  7. autostart install/uninstall round trip
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
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


def step_self_check(ctx):
    proc, err = run_child([str(ctx["gui"]), "--self-check"], ctx["env"])
    if err:
        return {"ok": False, "detail": err}
    if proc.returncode != 0:
        return {"ok": False, "detail": f"exit {proc.returncode}, stderr={proc.stderr!r}"}
    try:
        data = json.loads(proc.stdout.decode("utf-8").strip())
    except Exception as exc:
        return {"ok": False, "detail": f"could not parse self-check JSON ({exc}); stdout={proc.stdout!r}"}
    checks = data.get("checks")
    if not isinstance(checks, dict) or not checks:
        return {"ok": False, "detail": f"self-check report has no checks: {data!r}"}
    failed = {k: v for k, v in checks.items() if not v.get("ok")}
    if failed:
        return {"ok": False, "detail": f"failing self-check(s): {failed}"}
    state_dir = data.get("state_dir")
    if not state_dir:
        return {"ok": False, "detail": f"self-check report missing state_dir: {data!r}"}
    ctx["state_dir"] = Path(state_dir)
    ctx["self_check_report"] = data
    return {"ok": True, "detail": data}


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


def step_hook_timing(ctx):
    gate_ms = ctx["gate_ms"]
    realpath_err = _release_b_realpath_check(ctx)
    if realpath_err:
        return {"ok": False, "detail": realpath_err}
    argv = _hook_argv(ctx["settings_data"], "PreToolUse", "", ctx["expected_command"], ctx["sessions_dir_expected"])
    sessions_dir = Path(_sessions_dir_from_argv(argv))
    sessions_dir.mkdir(parents=True, exist_ok=True)

    def _payload(session_id):
        return json.dumps(
            {"session_id": session_id, "hook_event_name": "PreToolUse", "tool_name": "Bash"}
        ).encode("utf-8")

    cold_id = "verify-cold"
    cold_state = sessions_dir / f"{cold_id}.json"
    if cold_state.exists():
        cold_state.unlink()
    t0 = time.perf_counter()
    proc, err = run_child(argv, ctx["env"], input_bytes=_payload(cold_id))
    cold_s = time.perf_counter() - t0
    if err:
        return {"ok": False, "detail": f"cold run: {err}"}
    if proc.returncode != 0 or proc.stdout != b"":
        return {"ok": False, "detail": f"cold run: exit={proc.returncode} stdout={proc.stdout!r}"}
    if not cold_state.is_file():
        return {"ok": False, "detail": "cold run: state file missing"}
    cold_data = json.loads(cold_state.read_text(encoding="utf-8"))
    if cold_data.get("seq") != 1:
        return {"ok": False, "detail": f"cold run: seq {cold_data.get('seq')} != 1"}

    warm_id = "verify-warm"
    warm_state = sessions_dir / f"{warm_id}.json"
    if warm_state.exists():
        warm_state.unlink()

    timings = []
    for i in range(1, 21):
        t0 = time.perf_counter()
        proc, err = run_child(argv, ctx["env"], input_bytes=_payload(warm_id))
        elapsed = time.perf_counter() - t0
        # Record before judging: a failing run's timing is still evidence,
        # and a fix-round-1 report must show every value gathered so far.
        timings.append(elapsed)
        if err:
            return {"ok": False, "detail": {"error": f"warm run {i}: {err}", "timings_s": timings}}
        if proc.returncode != 0 or proc.stdout != b"":
            return {
                "ok": False,
                "detail": {
                    "error": f"warm run {i}: exit={proc.returncode} stdout={proc.stdout!r}",
                    "timings_s": timings,
                },
            }
        if not warm_state.is_file():
            return {"ok": False, "detail": {"error": f"warm run {i}: state file missing", "timings_s": timings}}
        data = json.loads(warm_state.read_text(encoding="utf-8"))
        if data.get("seq") != i:
            return {
                "ok": False,
                "detail": {"error": f"warm run {i}: seq {data.get('seq')} != {i}", "timings_s": timings},
            }

    sorted_t = sorted(timings)
    n = len(sorted_t)
    median = sorted_t[n // 2] if n % 2 else (sorted_t[n // 2 - 1] + sorted_t[n // 2]) / 2
    median_ms = median * 1000
    ok = median_ms <= gate_ms
    detail = {
        "argv": argv,
        "cold_s": cold_s,
        "timings_s": timings,
        "min_s": sorted_t[0],
        "median_s": median,
        "max_s": sorted_t[-1],
        "median_ms": median_ms,
        "gate_ms": gate_ms,
    }
    if not ok:
        detail["error"] = f"median {median_ms:.3f}ms exceeds gate {gate_ms}ms"
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


STEPS = [
    ("locate_binaries", step_locate_binaries),
    ("self_check", step_self_check),
    ("preflight_accounts", step_preflight_accounts),
    ("install_hooks", step_install_hooks),
    ("second_release", step_second_release),
    ("hook_timing", step_hook_timing),
    ("hook_events", step_hook_events),
    ("autostart", step_autostart),
]


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
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    work = Path(args.work).resolve()
    report_path = Path(args.report)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parent.parent

    unusable_reason = _work_dir_unusable_reason(work)
    if unusable_reason:
        print(f"[verify_artifact] refusing to run: {unusable_reason}", file=sys.stderr)
        write_report(
            report_path,
            {
                "app_dir": str(app_dir),
                "work": str(work),
                "repo_root": str(repo_root),
                "gate_ms": args.gate_ms,
                "platform": sys.platform,
                "steps": {},
                "ok": False,
                "error": unusable_reason,
            },
        )
        return 1

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
    }

    report = {
        "app_dir": str(app_dir),
        "work": str(work),
        "repo_root": str(repo_root),
        "gate_ms": args.gate_ms,
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

    report["ok"] = bool(report["steps"]) and all(s.get("ok") for s in report["steps"].values())
    write_report(report_path, report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
