"""Tests for the Codex side of tokitty/hooks_install.py: the hooks.json
target, the Codex command shapes, ownership and reconcile.

SAFETY: every test operates on tmp_path fixtures only. Never touch a real
~/.codex.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tokitty import hooks_install as hi
from tokitty import runner_link

EXE_NAME = "tokitty.exe" if sys.platform == "win32" else "tokitty"
RUNNER_NAME = "tokitty-hook.exe" if sys.platform == "win32" else "tokitty-hook"
_real_build = hi._build_command
WIN_RUNNER = r"C:\Users\nick\AppData\Local\Tokitty\current\tokitty-hook.exe"
LINUX_RUNNER = "/home/nick/.config/tokitty/current/tokitty-hook"

EVENT_NAMES = [
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
    "Stop", "SubagentStop", "Interrupt", "SessionEnd",
]
REWARN = "Codex will ask you to approve Tokitty's hooks again."


def _fake_release(root):
    root.mkdir(parents=True)
    (root / EXE_NAME).write_text("gui", encoding="utf-8")
    (root / RUNNER_NAME).write_text("hook", encoding="utf-8")
    return root / EXE_NAME


def _unlink_current_link(state_dir):
    link = state_dir / "current"
    if runner_link._is_link(str(link)):
        (os.rmdir if sys.platform == "win32" else os.unlink)(link)


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A fake frozen build whose state dir is under tmp_path. Returns the
    state dir; the link it makes is removed afterwards."""
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    yield state_dir
    _unlink_current_link(state_dir)


@pytest.fixture
def home(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    return codex_home


def _install(home, **kw):
    return hi.install_hooks_for_dir(str(home), "codex", **kw)


def _refresh(home):
    return hi.refresh_hooks_for_dir(str(home), "codex")


def _hooks(home):
    return json.loads((home / "hooks.json").read_text(encoding="utf-8"))


def _sessions(home):
    return f"{home}/tokitty/sessions"


def _source_command(home, interpreter=None):
    if interpreter is None:
        # A drive-letter home (tmp_path on Windows) gets python, as in _build_command.
        interpreter = "python" if hi._is_windows_local_path(str(home)) else "python3"
    return f'{interpreter} "{home}/tokitty/hook_writer.py" --sessions-dir "{_sessions(home)}"'


def _runner_command(home, runner):
    if sys.platform == "win32":
        # The frozen command a native Windows home gets: unquoted, for PowerShell.
        return hi._build_command(
            str(home), frozen=True, runner=runner, platform="win32", provider="codex"
        )["command"]
    return f'"{runner}" --sessions-dir "{_sessions(home)}"'


def _quoted_runner_command(home, runner):
    return f'"{runner}" --sessions-dir "{_sessions(home)}"'


def _write_hooks(home, hooks):
    (home / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


def _owned(command, config_dir="/home/nick/.codex", **extra):
    hook = {"type": "command", "command": command, **extra}
    return hi._is_owned_hook(hook, config_dir, "codex")


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------

def test_codex_hook_target():
    target = hi._hook_target("codex")
    assert target.settings_file == "hooks.json"
    assert target.local_settings_file is None
    assert target.events == hi.CODEX_EVENTS
    assert [e for e, _m in hi.CODEX_EVENTS] == EVENT_NAMES
    assert all(m is None for _e, m in hi.CODEX_EVENTS)
    assert target.timeouts == (("Interrupt", 3), ("SessionEnd", 3))
    assert target.exec_form is False


def test_claude_hook_target_keeps_matchers_and_exec_form():
    target = hi._hook_target("claude")
    assert target.timeouts == ()
    assert target.exec_form is True
    assert ("Notification", "permission_prompt") in target.events


def test_codex_paths_native_and_wsl():
    fs, visible = hi.codex_paths(r"C:\Users\u\.codex")
    assert visible == "C:\\Users\\u\\.codex/hooks.json"
    fs, visible = hi.codex_paths(r"\\wsl.localhost\Ubuntu\home\u\.codex")
    assert visible == "/home/u/.codex/hooks.json"
    assert fs.endswith("hooks.json")


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def test_codex_command_source_linux():
    hook = hi._build_command("/home/nick/.codex", frozen=False, platform="linux", provider="codex")
    assert hook == {"type": "command", "command": _source_command("/home/nick/.codex")}


def test_codex_command_source_drive_letter_home_uses_python():
    hook = hi._build_command(r"C:\Users\nick\.codex", frozen=False, platform="win32", provider="codex")
    assert hook["command"].startswith('python "C:\\Users\\nick\\.codex/tokitty/hook_writer.py"')
    assert "args" not in hook


def test_codex_command_frozen_windows_native_home_is_unquoted_forward_slashes():
    hook = hi._build_command(
        r"C:\Users\nick\.codex", frozen=True, runner=WIN_RUNNER, platform="win32", provider="codex"
    )
    assert hook == {
        "type": "command",
        "command": (
            "C:/Users/nick/AppData/Local/Tokitty/current/tokitty-hook.exe"
            " --sessions-dir C:/Users/nick/.codex/tokitty/sessions"
        ),
    }


def test_codex_command_frozen_windows_spaced_runner_dir_gets_its_directory_short_pathed(monkeypatch):
    seen = []

    def short(path):
        seen.append(path)
        return r"C:\PROGRA~1\Tokitty"

    monkeypatch.setattr(hi, "_win_short_path", short)
    hook = hi._build_command(
        r"C:\Users\nick\.codex", frozen=True,
        runner=r"C:\Program Files\Tokitty\tokitty-hook.exe", platform="win32", provider="codex",
    )
    assert seen == ["C:/Program Files/Tokitty"]
    assert hook["command"] == (
        "C:/PROGRA~1/Tokitty/tokitty-hook.exe --sessions-dir C:/Users/nick/.codex/tokitty/sessions"
    )


def test_codex_command_frozen_windows_spaced_home_gets_the_sessions_short_pathed(monkeypatch):
    seen = []

    def short(path):
        seen.append(path)
        return r"C:\Users\NICKWO~1\.codex"

    monkeypatch.setattr(hi, "_win_short_path", short)
    hook = hi._build_command(
        r"C:\Users\nick wolf\.codex", frozen=True, runner=WIN_RUNNER, platform="win32", provider="codex"
    )
    assert seen == [r"C:\Users\nick wolf\.codex"]
    assert hook["command"] == (
        "C:/Users/nick/AppData/Local/Tokitty/current/tokitty-hook.exe"
        " --sessions-dir C:/Users/NICKWO~1/.codex/tokitty/sessions"
    )


def test_codex_command_frozen_windows_without_short_names_falls_back_to_the_call_operator(monkeypatch):
    monkeypatch.setattr(hi, "_win_short_path", lambda path: None)
    runner = r"C:\Program Files\Tokitty\tokitty-hook.exe"
    hook = hi._build_command(
        r"C:\Users\nick\.codex", frozen=True, runner=runner, platform="win32", provider="codex"
    )
    assert hook["command"] == f'& "{runner}" --sessions-dir "C:\\Users\\nick\\.codex/tokitty/sessions"'


def test_codex_command_frozen_windows_wsl_home_keeps_python3():
    hook = hi._build_command(
        r"\\wsl.localhost\Ubuntu\home\nick\.codex",
        frozen=True, runner=WIN_RUNNER, platform="win32", provider="codex",
    )
    assert hook == {"type": "command", "command": _source_command("/home/nick/.codex")}


def test_codex_command_frozen_linux_quotes_stable_path():
    hook = hi._build_command(
        "/home/nick/.codex", frozen=True, runner=LINUX_RUNNER, platform="linux", provider="codex"
    )
    assert hook == {
        "type": "command",
        "command": f'"{LINUX_RUNNER}" --sessions-dir "/home/nick/.codex/tokitty/sessions"',
    }


def test_codex_command_default_runner_is_stable_path(tmp_path, monkeypatch):
    monkeypatch.setattr(hi, "state_dir_path", lambda: tmp_path)
    hook = hi._build_command("/home/nick/.codex", frozen=True, platform="linux", provider="codex")
    assert hook["command"].startswith(f'"{tmp_path / "current" / "tokitty-hook"}" --sessions-dir ')


def test_codex_command_runner_with_space_is_one_quoted_token():
    runner = "/home/nick 2/.config/tokitty/current/tokitty-hook"
    hook = hi._build_command("/home/nick/.codex", frozen=True, runner=runner, platform="linux", provider="codex")
    import shlex
    assert shlex.split(hook["command"])[0] == runner
    assert hook["command"].startswith(f'"{runner}" ')


def _powershells():
    names = ["powershell.exe", "pwsh"]
    return [found for found in (shutil.which(name) for name in names) if found]


@pytest.mark.skipif(sys.platform != "win32", reason="cmd /C and PowerShell are the Windows Codex launchers")
def test_codex_command_survives_cmd_c_and_powershell_with_a_spaced_runner_dir(tmp_path):
    runner_dir = tmp_path / "run ner"
    runner_dir.mkdir()
    seen = tmp_path / "seen.txt"
    runner = runner_dir / "tokitty-hook.cmd"
    runner.write_text(
        f'@echo off\r\necho %~1> "{seen}"\r\necho %~2>> "{seen}"\r\n', encoding="utf-8"
    )
    config_dir = str(tmp_path / ".codex")
    (tmp_path / ".codex").mkdir()
    if hi._win_short_path(str(runner_dir)) is None:
        pytest.skip("8.3 short names are disabled on this volume")
    command = hi._build_command(
        config_dir, frozen=True, runner=str(runner), platform="win32", provider="codex"
    )["command"]
    assert not command.startswith(('"', "&"))
    expected_sessions = command.split(" --sessions-dir ")[1]

    # A str (not a list) so the command line reaches cmd untouched, the way
    # Codex builds it: cmd /C "<command>".
    runs = [f'cmd /C "{command}"']
    # A list, as Codex's Command::arg builds it: list2cmdline escapes inner quotes like Rust does.
    runs += [[ps, "-NoProfile", "-Command", command] for ps in _powershells()]
    for run in runs:
        seen.unlink(missing_ok=True)
        subprocess.run(run, check=True)
        lines = seen.read_text(encoding="utf-8").splitlines()
        assert lines[0].strip() == "--sessions-dir"
        assert lines[1].strip() == expected_sessions


@pytest.mark.skipif(sys.platform != "win32", reason="cmd /C and PowerShell are the Windows Codex launchers")
def test_codex_command_without_spaces_runs_unquoted_under_cmd_and_powershell(tmp_path):
    seen = tmp_path / "seen.txt"
    runner = tmp_path / "tokitty-hook.cmd"
    runner.write_text(
        f'@echo off\r\necho %~1> "{seen}"\r\necho %~2>> "{seen}"\r\n', encoding="utf-8"
    )
    config_dir = str(tmp_path / ".codex")
    command = hi._build_command(
        config_dir, frozen=True, runner=str(runner), platform="win32", provider="codex"
    )["command"]
    if command.startswith("&"):
        pytest.skip("tmp_path is not shell-safe and short names are unavailable")
    assert not command.startswith('"')

    runs = [f'cmd /C "{command}"']
    runs += [[ps, "-NoProfile", "-Command", command] for ps in _powershells()]
    for run in runs:
        seen.unlink(missing_ok=True)
        subprocess.run(run, check=True)
        lines = seen.read_text(encoding="utf-8").splitlines()
        assert lines[0].strip() == "--sessions-dir"
        assert lines[1].strip() == command.split(" --sessions-dir ")[1]


def test_claude_command_unchanged_by_provider_default():
    assert hi._build_command("/home/n/.claude", frozen=True, runner="/r/tokitty-hook", platform="linux") == {
        "type": "command",
        "command": "/r/tokitty-hook",
        "args": ["--sessions-dir", "/home/n/.claude/tokitty/sessions"],
    }


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

def test_codex_owns_quoted_source_shape():
    assert _owned(_source_command("/home/nick/.codex"))
    assert _owned(_source_command("/home/nick/.codex", "python"))


def test_codex_owns_unquoted_source_shape():
    assert _owned(
        "python3 /home/nick/.codex/tokitty/hook_writer.py --sessions-dir /home/nick/.codex/tokitty/sessions"
    )


def test_codex_owns_unquoted_drive_letter_source_shape():
    assert _owned(
        r"python C:\Users\nick\.codex/tokitty/hook_writer.py --sessions-dir C:\Users\nick\.codex/tokitty/sessions",
        config_dir=r"C:\Users\nick\.codex",
    )


def test_codex_owns_runner_at_stable_fallback_and_old_release_paths():
    for runner in (
        "/home/nick/.config/tokitty/current/tokitty-hook",
        "/opt/Tokitty/tokitty-hook",
        "/old/release 3/tokitty-hook",
    ):
        assert _owned(_runner_command("/home/nick/.codex", runner))
    assert _owned(_runner_command("/home/nick/.codex", r"C:\Tokitty\TOKITTY-HOOK.EXE"))


def test_codex_owns_the_unquoted_windows_form():
    command = (
        "C:/Users/nick/AppData/Local/Tokitty/current/tokitty-hook.exe"
        " --sessions-dir C:/Users/nick/.codex/tokitty/sessions"
    )
    assert _owned(command, r"C:\Users\nick\.codex")


def test_codex_owns_the_call_operator_form():
    command = (
        '& "C:\\Program Files\\Tokitty\\tokitty-hook.exe"'
        ' --sessions-dir "C:\\Users\\nick\\.codex/tokitty/sessions"'
    )
    assert _owned(command, r"C:\Users\nick\.codex")
    assert not _owned(command.replace("tokitty-hook.exe", "other.exe"), r"C:\Users\nick\.codex")


def test_codex_still_owns_the_old_quoted_windows_form():
    command = f'"{WIN_RUNNER}" --sessions-dir "C:\\Users\\nick\\.codex/tokitty/sessions"'
    assert _owned(command, r"C:\Users\nick\.codex")


def test_codex_owns_the_short_path_sessions_form(monkeypatch):
    monkeypatch.setattr(hi, "_win_short_path", lambda path: r"C:\Users\NICKWO~1\.codex")
    command = (
        "C:/Users/nick/AppData/Local/Tokitty/current/tokitty-hook.exe"
        " --sessions-dir C:/Users/NICKWO~1/.codex/tokitty/sessions"
    )
    assert _owned(command, r"C:\Users\nick wolf\.codex")
    assert not _owned(command.replace("NICKWO~1", "OTHERU~1"), r"C:\Users\nick wolf\.codex")


def test_codex_does_not_own_hook_with_args_key():
    assert not _owned(
        f'"{LINUX_RUNNER}" --sessions-dir "/home/nick/.codex/tokitty/sessions"', args=["x"]
    )


def test_codex_does_not_own_the_unquoted_windows_form_with_args_or_another_home_or_user_command():
    home = r"C:\Users\nick\.codex"
    command = "C:/Users/nick/AppData/Local/Tokitty/current/tokitty-hook.exe --sessions-dir C:/Users/nick/.codex/tokitty/sessions"
    assert not _owned(command, home, args=["x"])
    assert not _owned(command, r"C:\Users\other\.codex")
    assert not _owned(
        "C:/tools/tokitty-notify.exe --sessions-dir C:/Users/nick/.codex/tokitty/sessions", home
    )


def test_codex_does_not_own_another_home():
    assert not _owned(_source_command("/home/other/.codex"))
    assert not _owned(_runner_command("/home/other/.codex", LINUX_RUNNER))


def test_codex_does_not_own_user_command_mentioning_tokitty():
    assert not _owned("/usr/local/bin/tokitty-notify --sessions-dir /home/nick/.codex/tokitty/sessions extra")
    assert not _owned('"/home/me/tokitty/run.sh" --sessions-dir "/home/nick/.codex/tokitty/sessions"')
    assert not _owned("echo tokitty")


def test_codex_does_not_own_unbalanced_quote_or_wrong_flag():
    assert not _owned('"/r/tokitty-hook --sessions-dir "/home/nick/.codex/tokitty/sessions"')
    assert not _owned('"/r/tokitty-hook" --other "/home/nick/.codex/tokitty/sessions"')


def test_codex_does_not_own_the_claude_exec_form():
    hook = {
        "type": "command",
        "command": LINUX_RUNNER,
        "args": ["--sessions-dir", "/home/nick/.codex/tokitty/sessions"],
    }
    assert not hi._is_owned_hook(hook, "/home/nick/.codex", "codex")


def test_claude_ownership_unchanged_for_exec_form():
    hook = {
        "type": "command",
        "command": LINUX_RUNNER,
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions"],
    }
    assert hi._is_owned_hook(hook, "/home/nick/.claude", "claude")


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def test_install_into_fresh_codex_home_writes_hooks_json_only(home):
    result = _install(home)

    assert result.ok
    assert sorted(result.installed_events) == sorted(EVENT_NAMES)
    assert result.warning is None
    data = _hooks(home)
    assert list(data) == ["hooks"]
    assert list(data["hooks"]) == EVENT_NAMES
    for event in EVENT_NAMES:
        groups = data["hooks"][event]
        assert len(groups) == 1
        assert list(groups[0]) == ["hooks"]
        (handler,) = groups[0]["hooks"]
        assert handler["type"] == "command"
        assert handler["command"] == _source_command(home)
        assert "args" not in handler
        if event in ("Interrupt", "SessionEnd"):
            assert handler["timeout"] == 3
        else:
            assert "timeout" not in handler
    assert (home / "tokitty" / "hook_writer.py").is_file()
    assert not (home / "settings.json").exists()
    assert not (home / "config.toml").exists()


def test_install_leaves_config_toml_byte_identical(home):
    toml = home / "config.toml"
    toml.write_bytes(b'model = "x"\r\n[hooks.state.\'/a:stop:0:0\']\r\ntrusted_hash = "sha256:1"\r\n')
    before = toml.read_bytes()
    _install(home)
    _refresh(home)
    hi.uninstall_hooks_for_dir(str(home), "codex")
    assert toml.read_bytes() == before


def test_install_appends_after_user_groups_and_leaves_them_byte_identical(home):
    user_start = {"hooks": [{"type": "command", "command": "start.sh"}]}
    user_pre = {"matcher": "Bash", "hooks": [{"type": "command", "command": "pre.sh", "timeout": 9}]}
    _write_hooks(home, {"SessionStart": [user_start], "PreToolUse": [user_pre]})

    result = _install(home)

    assert result.ok
    data = _hooks(home)["hooks"]
    assert data["SessionStart"] == [user_start]
    assert data["PreToolUse"][0] == user_pre
    assert len(data["PreToolUse"]) == 2
    assert data["PreToolUse"][1] == {"hooks": [{"type": "command", "command": _source_command(home)}]}
    assert result.warning is None


def test_install_aborts_on_non_object_hooks_key(home):
    (home / "hooks.json").write_text(json.dumps({"hooks": []}), encoding="utf-8")
    before = (home / "hooks.json").read_bytes()

    result = _install(home)

    assert not result.ok
    assert (home / "hooks.json").read_bytes() == before
    assert not (home / "tokitty").exists()


def test_second_install_is_a_noop_that_writes_nothing(home):
    _install(home)
    before = (home / "hooks.json").read_bytes()
    backups = sorted(p.name for p in home.glob("hooks.json.tokitty-backup-*"))

    result = _install(home)

    assert result.ok
    assert result.installed_events == []
    assert result.refreshed_events == []
    assert result.warning is None
    assert (home / "hooks.json").read_bytes() == before
    assert sorted(p.name for p in home.glob("hooks.json.tokitty-backup-*")) == backups


def test_frozen_install_writes_quoted_stable_runner_string(home, frozen):
    result = _install(home)

    assert result.ok
    assert result.warning is None
    stable = str(frozen / "current" / RUNNER_NAME)
    handler = _hooks(home)["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler == {"type": "command", "command": _runner_command(home, stable)}
    assert runner_link._is_link(str(frozen / "current"))


def test_frozen_install_with_failed_link_writes_release_runner_and_warns(home, frozen, monkeypatch):
    monkeypatch.setattr(
        runner_link, "ensure_runner_link",
        lambda *a, **k: (_ for _ in ()).throw(OSError("timed out waiting for the lock")),
    )

    result = _install(home)

    assert result.ok
    assert result.warning == hi.LINK_FALLBACK_WARNING.format(reason="timed out waiting for the lock")
    bundled = hi.hook_runner_path(os.path.realpath(sys.executable), sys.platform)
    handler = _hooks(home)["hooks"]["Stop"][0]["hooks"][0]
    assert handler["command"] == _runner_command(home, bundled)


def test_frozen_install_without_bundled_runner_aborts(home, tmp_path, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / EXE_NAME).write_text("gui", encoding="utf-8")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(bare / EXE_NAME))
    monkeypatch.setattr(hi, "state_dir_path", lambda: tmp_path / "state")

    result = _install(home)

    assert not result.ok
    assert not (home / "hooks.json").exists()


# ---------------------------------------------------------------------------
# Refresh and rewrite
# ---------------------------------------------------------------------------

def test_refresh_source_to_frozen_rewrites_in_place_and_warns(home, frozen, monkeypatch):
    user = {"hooks": [{"type": "command", "command": "pre.sh"}]}
    _write_hooks(home, {"PreToolUse": [user]})
    monkeypatch.setattr(hi.sys, "frozen", False, raising=False)
    _install(home)
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)

    result = _refresh(home)

    assert result.ok
    assert sorted(result.refreshed_events) == sorted(EVENT_NAMES)
    assert result.warning == REWARN
    stable = str(frozen / "current" / RUNNER_NAME)
    pre = _hooks(home)["hooks"]["PreToolUse"]
    assert pre[0] == user
    assert pre[1] == {"hooks": [{"type": "command", "command": _runner_command(home, stable)}]}
    session_end = _hooks(home)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert session_end["timeout"] == 3


def test_refresh_rewrites_an_old_quoted_windows_handler_to_the_unquoted_form(home, frozen, monkeypatch):
    stable = str(frozen / "current" / RUNNER_NAME)
    _install(home)
    quoted = _quoted_runner_command(home, stable)
    data = _hooks(home)
    for groups in data["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = quoted
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    seen = []
    monkeypatch.setattr(hi, "_record_codex_trust", lambda config_dir, changes: seen.append(sorted(changes)))
    monkeypatch.setattr(
        hi, "_build_command",
        lambda config_dir, **kw: _real_build(config_dir, **{**kw, "platform": "win32"}),
    )

    result = _refresh(home)

    assert result.ok
    assert sorted(result.refreshed_events) == sorted(EVENT_NAMES)
    assert result.warning == REWARN
    handler = _hooks(home)["hooks"]["Stop"][0]["hooks"][0]
    assert not handler["command"].lstrip().startswith('"')
    assert handler["command"] == f"{stable.replace(chr(92), '/')} --sessions-dir {_sessions(home).replace(chr(92), '/')}"
    assert hi._codex_owned_parts(handler["command"], str(home)) is not None
    (changes,) = seen
    assert len(changes) == 8


def test_refresh_moved_stable_path_with_same_basename_rewrites_and_warns(home, frozen):
    _install(home)
    data = _hooks(home)
    for groups in data["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = _runner_command(home, "/old/elsewhere/current/" + RUNNER_NAME)
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    result = _refresh(home)

    assert result.ok
    assert result.warning == REWARN
    stable = str(frozen / "current" / RUNNER_NAME)
    handler = _hooks(home)["hooks"]["Stop"][0]["hooks"][0]
    assert handler["command"] == _runner_command(home, stable)


def test_refresh_identical_is_a_noop_without_warning(home, frozen):
    _install(home)
    before = (home / "hooks.json").read_bytes()
    backups = sorted(p.name for p in home.glob("hooks.json.tokitty-backup-*"))

    result = _refresh(home)

    assert result.ok
    assert result.refreshed_events == []
    assert result.warning is None
    assert (home / "hooks.json").read_bytes() == before
    assert sorted(p.name for p in home.glob("hooks.json.tokitty-backup-*")) == backups


def test_refresh_equivalent_python_spelling_is_not_a_rewrite(home):
    _install(home)
    data = _hooks(home)
    for groups in data["hooks"].values():
        for handler in (g["hooks"][0] for g in groups):
            handler["command"] = (
                f'python {home}/tokitty/hook_writer.py --sessions-dir {_sessions(home)}'
            )
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    before = (home / "hooks.json").read_bytes()

    result = _refresh(home)

    assert result.refreshed_events == []
    assert result.warning is None
    assert (home / "hooks.json").read_bytes() == before


def test_refresh_from_a_source_launch_does_not_demote_a_frozen_handler(home, frozen, monkeypatch):
    _install(home)
    before = (home / "hooks.json").read_bytes()
    monkeypatch.setattr(hi.sys, "frozen", False, raising=False)

    result = _refresh(home)

    assert result.ok
    assert result.refreshed_events == []
    assert result.warning is None
    assert (home / "hooks.json").read_bytes() == before


def test_explicit_install_from_a_source_launch_does_demote_a_frozen_handler(home, frozen, monkeypatch):
    _install(home)
    monkeypatch.setattr(hi.sys, "frozen", False, raising=False)

    result = _install(home)

    assert result.ok
    assert result.warning == REWARN
    handler = _hooks(home)["hooks"]["Stop"][0]["hooks"][0]
    assert handler["command"] == _source_command(home)


def test_refresh_restores_a_missing_timeout_on_session_end_and_warns(home):
    _install(home)
    data = _hooks(home)
    del data["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"]
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    result = _refresh(home)

    assert result.ok
    assert result.refreshed_events == ["SessionEnd"]
    assert result.warning == REWARN
    assert _hooks(home)["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 3


def test_refresh_repairs_timeout_on_a_frozen_handler_from_a_source_launch(home, frozen, monkeypatch):
    _install(home)
    stable = str(frozen / "current" / RUNNER_NAME)
    data = _hooks(home)
    del data["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"]
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(hi.sys, "frozen", False, raising=False)

    result = _refresh(home)

    assert result.ok
    assert result.refreshed_events == ["SessionEnd"]
    assert result.warning == REWARN
    handler = _hooks(home)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert handler == {"type": "command", "command": _runner_command(home, stable), "timeout": 3}


def test_refresh_removes_a_timeout_from_an_event_that_has_none(home):
    _install(home)
    data = _hooks(home)
    data["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 3
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    result = _refresh(home)

    assert result.refreshed_events == ["Stop"]
    assert "timeout" not in _hooks(home)["hooks"]["Stop"][0]["hooks"][0]


def test_rewrite_keeps_a_user_added_key_on_tokittys_handler(home, frozen, monkeypatch):
    monkeypatch.setattr(hi.sys, "frozen", False, raising=False)
    _install(home)
    data = _hooks(home)
    data["hooks"]["Stop"][0]["hooks"][0]["statusMessage"] = "hi"
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)

    _refresh(home)

    handler = _hooks(home)["hooks"]["Stop"][0]["hooks"][0]
    assert handler["statusMessage"] == "hi"
    assert handler["command"].startswith("C:/" if sys.platform == "win32" else '"')


def test_refresh_never_adds_a_missing_event(home):
    _install(home)
    data = _hooks(home)
    del data["hooks"]["Interrupt"]
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    result = _refresh(home)

    assert result.installed_events == []
    assert "Interrupt" not in _hooks(home)["hooks"]


def test_refresh_on_a_never_installed_home_writes_nothing(home):
    result = _refresh(home)

    assert result.ok
    assert not (home / "hooks.json").exists()
    assert not (home / "tokitty").exists()


def test_duplicate_collapse_keeping_the_stable_handler_counts_as_a_move(home, frozen, monkeypatch):
    stable = str(frozen / "current" / RUNNER_NAME)
    old = _runner_command(home, "/old/release/" + RUNNER_NAME)
    _write_hooks(home, {
        "PreToolUse": [
            {"hooks": [{"type": "command", "command": old}]},
            {"hooks": [{"type": "command", "command": _runner_command(home, stable)}]},
        ],
    })
    recorded = []
    monkeypatch.setattr(hi, "_record_codex_trust", lambda config_dir, changes: recorded.append(list(changes)))

    result = _refresh(home)

    assert result.ok
    assert result.refreshed_events == ["PreToolUse"]
    assert result.warning == REWARN
    groups = _hooks(home)["hooks"]["PreToolUse"]
    assert groups == [{"hooks": [{"type": "command", "command": _runner_command(home, stable)}]}]
    assert recorded == [[("PreToolUse", 0, 0)]]


def test_duplicate_collapse_keeping_the_first_handler_in_place_does_not_warn(home, frozen):
    stable = str(frozen / "current" / RUNNER_NAME)
    command = _runner_command(home, stable)
    _write_hooks(home, {
        "PreToolUse": [
            {"hooks": [{"type": "command", "command": command}]},
            {"hooks": [{"type": "command", "command": _runner_command(home, "/old/" + RUNNER_NAME)}]},
        ],
    })

    result = _refresh(home)

    assert result.ok
    assert result.warning is None
    assert _hooks(home)["hooks"]["PreToolUse"] == [{"hooks": [{"type": "command", "command": command}]}]


def test_trust_hook_point_gets_final_positions_for_added_and_rewritten_handlers(home, monkeypatch):
    user = {"hooks": [{"type": "command", "command": "pre.sh"}]}
    _write_hooks(home, {"PreToolUse": [user]})
    seen = []
    monkeypatch.setattr(hi, "_record_codex_trust", lambda config_dir, changes: seen.append((config_dir, sorted(changes))))

    _install(home)

    (config_dir, changes), = seen
    assert config_dir == str(home)
    assert ("PreToolUse", 1, 0) in changes
    assert ("Stop", 0, 0) in changes
    assert len(changes) == 8

    seen.clear()
    _refresh(home)
    assert seen == []


def test_trust_hook_point_runs_before_hooks_json_is_written(home, monkeypatch):
    def boom(config_dir, changes):
        assert not (home / "hooks.json").exists()
        raise RuntimeError("stop")

    monkeypatch.setattr(hi, "_record_codex_trust", boom)
    with pytest.raises(RuntimeError):
        _install(home)
    assert not (home / "hooks.json").exists()


def test_claude_reconcile_never_calls_the_trust_hook(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(hi, "_record_codex_trust", lambda *a: called.append(a))
    claude = tmp_path / ".claude"
    claude.mkdir()

    hi.install_hooks_for_dir(str(claude))

    assert called == []


def test_link_warning_comes_before_the_reapproval_sentence(home, frozen, monkeypatch):
    _install(home)
    monkeypatch.setattr(
        runner_link, "ensure_runner_link", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    data = _hooks(home)
    del data["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"]
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    result = _refresh(home)

    assert result.warning == hi.LINK_FALLBACK_WARNING.format(reason="boom") + " " + REWARN


def test_fallback_keeps_an_existing_stable_handler(home, frozen, monkeypatch):
    _install(home)
    before = (home / "hooks.json").read_bytes()
    monkeypatch.setattr(
        runner_link, "ensure_runner_link", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )

    result = _refresh(home)

    assert result.ok
    assert result.warning == hi.LINK_FALLBACK_WARNING.format(reason="boom")
    assert (home / "hooks.json").read_bytes() == before


def test_uninstall_then_ensure_current_leaves_the_home_uninstalled(home, tmp_path, monkeypatch):
    _install(home)
    hi.uninstall_hooks_for_dir(str(home), "codex")
    before = (home / "hooks.json").read_bytes()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"config_dir": str(home), "provider": "codex"}]})
    )
    # Codex has no activity capability until a later task, so let it through
    # provider_has_hooks here: this drives the real reconcile.
    monkeypatch.setattr(hi, "provider_has_hooks", lambda kind: True)

    results = hi.ensure_current(state_dir)

    assert len(results) == 1
    assert results[0].ok
    assert (home / "hooks.json").read_bytes() == before
    assert not any(_hooks(home)["hooks"].values())


def test_uninstall_removes_only_owned_handlers_from_codex_hooks(home):
    user_in_group = {"type": "command", "command": "mine.sh"}
    _write_hooks(home, {"Stop": [{"hooks": [user_in_group, {"type": "command", "command": _source_command(home)}]}]})

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.ok
    assert _hooks(home)["hooks"]["Stop"] == [{"hooks": [user_in_group]}]


# ---------------------------------------------------------------------------
# Shift warning (a user handler's position is its trust key)
# ---------------------------------------------------------------------------

SHIFT = "Codex will ask you to approve these hooks again: {}."


def _tokitty(home):
    return {"type": "command", "command": _source_command(home)}


def _user(name):
    return {"type": "command", "command": name}


def test_uninstall_warns_when_a_user_group_after_tokittys_shifts(home):
    _write_hooks(home, {"Stop": [{"hooks": [_tokitty(home)]}, {"hooks": [_user("mine.sh")]}]})

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.ok
    assert result.warning == SHIFT.format("Stop")
    assert _hooks(home)["hooks"]["Stop"] == [{"hooks": [_user("mine.sh")]}]


def test_uninstall_warns_when_a_user_handler_after_tokittys_in_its_group_shifts(home):
    _write_hooks(home, {"Stop": [{"hooks": [_tokitty(home), _user("mine.sh")]}]})

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.warning == SHIFT.format("Stop")
    assert _hooks(home)["hooks"]["Stop"] == [{"hooks": [_user("mine.sh")]}]


def test_uninstall_does_not_warn_when_the_user_group_is_before_tokittys(home):
    _write_hooks(home, {"Stop": [{"hooks": [_user("mine.sh")]}, {"hooks": [_tokitty(home)]}]})

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.ok
    assert result.warning is None
    assert _hooks(home)["hooks"]["Stop"] == [{"hooks": [_user("mine.sh")]}]


def test_uninstall_keeps_a_user_handler_inside_tokittys_group(home):
    _write_hooks(home, {"Stop": [{"matcher": "x", "hooks": [_user("mine.sh"), _tokitty(home)]}]})

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.warning is None
    assert _hooks(home)["hooks"]["Stop"] == [{"matcher": "x", "hooks": [_user("mine.sh")]}]


def test_uninstall_names_every_shifted_event_in_event_order(home):
    _write_hooks(home, {
        "SessionEnd": [{"hooks": [_tokitty(home)]}, {"hooks": [_user("b.sh")]}],
        "PreToolUse": [{"hooks": [_tokitty(home)]}, {"hooks": [_user("a.sh")]}],
        "Stop": [{"hooks": [_user("c.sh")]}, {"hooks": [_tokitty(home)]}],
    })

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.warning == SHIFT.format("PreToolUse, SessionEnd")


def test_claude_uninstall_never_gets_the_shift_warning(tmp_path):
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    assert hi.install_hooks_for_dir(str(claude_home)).ok
    settings = claude_home / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["Stop"].append({"hooks": [_user("mine.sh")]})
    settings.write_text(json.dumps(data), encoding="utf-8")

    result = hi.uninstall_hooks_for_dir(str(claude_home))

    assert result.ok
    assert result.warning is None
    assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]["Stop"] == [{"hooks": [_user("mine.sh")]}]


def test_refresh_collapsing_a_duplicate_ahead_of_a_user_group_warns(home, frozen):
    stable = str(frozen / "current" / RUNNER_NAME)
    _write_hooks(home, {
        "Stop": [
            {"hooks": [{"type": "command", "command": _runner_command(home, "/old/" + RUNNER_NAME)}]},
            {"hooks": [_user("mine.sh")]},
            {"hooks": [{"type": "command", "command": _runner_command(home, stable)}]},
        ],
    })

    result = _refresh(home)

    assert result.ok
    # The kept stable handler moved (reapproval), and the user group shifted up.
    assert result.warning == f"{REWARN} {SHIFT.format('Stop')}"
    assert _hooks(home)["hooks"]["Stop"] == [
        {"hooks": [_user("mine.sh")]},
        {"hooks": [{"type": "command", "command": _runner_command(home, stable)}]},
    ]


def test_refresh_collapse_puts_the_link_warning_first(home, frozen, monkeypatch):
    def boom(state_dir):
        raise OSError("locked")

    monkeypatch.setattr(runner_link, "ensure_runner_link", boom)
    release = hi.hook_runner_path(os.path.realpath(hi.sys.executable), hi.sys.platform)
    _write_hooks(home, {
        "Stop": [
            {"hooks": [{"type": "command", "command": _runner_command(home, release)}]},
            {"hooks": [{"type": "command", "command": _runner_command(home, release)}]},
            {"hooks": [_user("mine.sh")]},
        ],
    })

    result = _refresh(home)

    assert result.warning.startswith(hi.LINK_FALLBACK_WARNING.format(reason="locked"))
    assert result.warning.endswith(SHIFT.format("Stop"))


def test_shifted_events_tells_identical_user_handlers_apart():
    first, second = _user("same.sh"), _user("same.sh")
    tokitty = {"type": "command", "command": _source_command(Path("/home/nick/.codex"))}
    before = {"Stop": [{"hooks": [tokitty, first]}, {"hooks": [second]}]}

    # Dropping Tokitty's handler moves `first` to (0, 0) and leaves `second`
    # at (1, 0) in a list that keeps both: only `first` shifted.
    after = {"Stop": [{"hooks": [first]}, {"hooks": [second]}]}
    assert hi._shifted_events(before, after, "/home/nick/.codex", "codex") == ["Stop"]

    # Swapping which of two equal handlers occupies a slot is a shift by
    # identity even though the values compare equal.
    swapped = {"Stop": [{"hooks": [tokitty, second]}, {"hooks": [first]}]}
    assert hi._shifted_events(before, swapped, "/home/nick/.codex", "codex") == ["Stop"]

    same = {"Stop": [{"hooks": [tokitty, first]}, {"hooks": [second]}]}
    assert hi._shifted_events(before, same, "/home/nick/.codex", "codex") == []


def test_approved_owned_duplicates_collapsed_onto_index_zero_read_as_needing_approval(home, frozen):
    from tokitty import codex_trust

    stable = str(frozen / "current" / RUNNER_NAME)
    old = _runner_command(home, "/old/release/" + RUNNER_NAME)
    _write_hooks(home, {
        "PreToolUse": [
            {"hooks": [{"type": "command", "command": old}]},
            {"hooks": [{"type": "command", "command": _runner_command(home, stable)}]},
        ],
    })
    config_toml = codex_trust.config_toml_path(str(home))
    prefix = hi.codex_paths(str(home))[1]
    config_toml.write_text(
        f"[hooks.state.'{prefix}:pre_tool_use:0:0']\ntrusted_hash = \"sha256:old-first\"\n"
        f"[hooks.state.'{prefix}:pre_tool_use:1:0']\ntrusted_hash = \"sha256:stable\"\n",
        encoding="utf-8",
    )

    result = _refresh(home)

    assert result.warning == REWARN
    assert _hooks(home)["hooks"]["PreToolUse"] == [
        {"hooks": [{"type": "command", "command": _runner_command(home, stable)}]}
    ]
    # Codex still holds the first handler's hash at index 0, and the record
    # captured that same hash, so the kept handler is not approved yet.
    assert codex_trust.codex_hook_status(str(home), frozen) == codex_trust.NEEDS_APPROVAL


def test_install_aborts_on_a_bare_event_map_and_leaves_it_alone(home):
    """Codex only accepts "description" and "hooks" at the top level; a bare
    event map (as some installers write) makes it ignore the whole file."""
    bare = {"SessionStart": [{"hooks": [{"type": "command", "command": "node gsd.js"}]}]}
    (home / "hooks.json").write_text(json.dumps(bare), encoding="utf-8")
    before = (home / "hooks.json").read_bytes()

    result = _install(home)

    assert not result.ok
    assert "SessionStart" in result.message and '"hooks"' in result.message
    assert (home / "hooks.json").read_bytes() == before


def test_install_accepts_a_description_key(home):
    _write_hooks(home, {})
    data = _hooks(home)
    data["description"] = "mine"
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    result = _install(home)

    assert result.ok
    assert _hooks(home)["description"] == "mine"


def test_refresh_on_a_never_installed_bare_event_map_stays_quiet(home):
    bare = {"SessionStart": [{"hooks": [{"type": "command", "command": "node gsd.js"}]}]}
    (home / "hooks.json").write_text(json.dumps(bare), encoding="utf-8")

    result = _refresh(home)

    assert result.ok


def test_refresh_aborts_when_a_stray_key_appears_beside_tokittys_hooks(home):
    _install(home)
    data = _hooks(home)
    data["SessionStart"] = []
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    before = (home / "hooks.json").read_bytes()

    result = _refresh(home)

    assert not result.ok
    assert (home / "hooks.json").read_bytes() == before


# ---------------------------------------------------------------------------
# Automatic approvals reviewer: no PermissionRequest hook
# ---------------------------------------------------------------------------

GUARDIAN = 'approvals_reviewer = "guardian_subagent"\n'


def _guardian(home):
    (home / "config.toml").write_text(GUARDIAN, encoding="utf-8")


def _trust_record(home):
    from tokitty import codex_trust

    data = json.loads((codex_trust.record_path(hi.state_dir_path())).read_text(encoding="utf-8"))
    return data[codex_trust.home_key(str(home))]["keys"]


def test_install_into_a_guardian_home_skips_permission_request(home):
    _guardian(home)

    result = _install(home)

    assert result.ok
    assert sorted(_hooks(home)["hooks"]) == sorted(n for n in EVENT_NAMES if n != "PermissionRequest")
    assert len(result.installed_events) == 7


def test_install_into_a_normal_home_still_writes_eight(home):
    (home / "config.toml").write_text('approvals_reviewer = "user"\n', encoding="utf-8")
    _install(home)
    assert sorted(_hooks(home)["hooks"]) == sorted(EVENT_NAMES)


def test_refresh_removes_tokittys_permission_request_once_guardian(home):
    from tokitty import codex_trust

    _install(home)
    before = _hooks(home)["hooks"]
    _guardian(home)
    config_before = (home / "config.toml").read_bytes()

    result = _refresh(home)

    assert result.ok
    assert result.removed_events == ["PermissionRequest"]
    assert result.refreshed_events == []
    assert result.warning is None
    after = _hooks(home)["hooks"]
    assert "PermissionRequest" not in after
    assert {k: v for k, v in before.items() if k != "PermissionRequest"} == after
    keys = _trust_record(home)
    assert codex_trust.trust_key(str(home), "PermissionRequest", 0, 0) not in keys
    assert len(keys) == 7
    assert (home / "config.toml").read_bytes() == config_before


def test_install_also_removes_it_once_guardian(home):
    _install(home)
    _guardian(home)
    result = _install(home)
    assert result.ok and result.warning is None
    assert "PermissionRequest" not in _hooks(home)["hooks"]


def test_refresh_never_adds_permission_request_in_a_normal_home(home):
    _install(home)
    data = _hooks(home)
    del data["hooks"]["PermissionRequest"]
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    _refresh(home)
    assert "PermissionRequest" not in _hooks(home)["hooks"]


def test_removal_warns_when_a_user_group_after_tokittys_shifts(home):
    _install(home)
    data = _hooks(home)
    data["hooks"]["PermissionRequest"].append({"hooks": [_user("mine.sh")]})
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    _guardian(home)

    result = _refresh(home)

    assert result.warning == SHIFT.format("PermissionRequest")
    assert _hooks(home)["hooks"]["PermissionRequest"] == [{"hooks": [_user("mine.sh")]}]


def test_a_users_own_permission_request_hook_is_left_alone(home):
    _write_hooks(home, {"PermissionRequest": [{"hooks": [_user("mine.sh")]}]})
    _guardian(home)

    result = _install(home)

    assert result.ok
    assert _hooks(home)["hooks"]["PermissionRequest"] == [{"hooks": [_user("mine.sh")]}]
    assert result.warning is None


def test_a_forget_keys_failure_aborts_before_hooks_json_is_written(home, monkeypatch):
    from tokitty import codex_trust

    _install(home)
    _guardian(home)
    raw = (home / "hooks.json").read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(codex_trust, "forget_keys", boom)
    result = _refresh(home)

    assert not result.ok
    assert (home / "hooks.json").read_bytes() == raw


def test_hook_status_for_a_seven_handler_guardian_home(home):
    from tokitty import codex_trust as ct

    _guardian(home)
    _install(home)
    state = hi.state_dir_path()
    assert ct.codex_hook_status(str(home), state) == ct.NEEDS_APPROVAL

    lines = [GUARDIAN]
    for event, _m in hi.CODEX_EVENTS:
        if event != "PermissionRequest":
            lines.append(f"[hooks.state.'{ct.trust_key(str(home), event, 0, 0)}']")
            lines.append(f'trusted_hash = "sha256:{event}"')
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ct.codex_hook_status(str(home), state) == ct.APPROVED


def test_install_hooks_cli_says_removed_for_the_guardian_permission_hook(home, monkeypatch, capsys):
    _install(home)
    _guardian(home)
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(home), "codex")])

    assert hi.install_hooks() == 0

    out = capsys.readouterr().out
    assert "removed hooks for PermissionRequest (Codex's automatic reviewer answers approvals)" in out
    assert "refreshed hooks for PermissionRequest" not in out
