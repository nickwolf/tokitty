"""Tests for the Codex side of tokitty/hooks_install.py: the hooks.json
target, the Codex command shapes, ownership and reconcile.

SAFETY: every test operates on tmp_path fixtures only. Never touch a real
~/.codex.
"""
import json
import os
import subprocess
import sys

import pytest

from tokitty import hooks_install as hi
from tokitty import runner_link

EXE_NAME = "tokitty.exe" if sys.platform == "win32" else "tokitty"
RUNNER_NAME = "tokitty-hook.exe" if sys.platform == "win32" else "tokitty-hook"
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


def _source_command(home, interpreter="python3"):
    return f'{interpreter} "{home}/tokitty/hook_writer.py" --sessions-dir "{_sessions(home)}"'


def _runner_command(home, runner):
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


def test_codex_command_frozen_windows_native_home_quotes_stable_exe():
    hook = hi._build_command(
        r"C:\Users\nick\.codex", frozen=True, runner=WIN_RUNNER, platform="win32", provider="codex"
    )
    assert hook == {
        "type": "command",
        "command": f'"{WIN_RUNNER}" --sessions-dir "C:\\Users\\nick\\.codex/tokitty/sessions"',
    }


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


@pytest.mark.skipif(sys.platform != "win32", reason="cmd /C is the Windows Codex launcher")
def test_codex_command_survives_cmd_c_with_a_spaced_runner_path(tmp_path):
    runner_dir = tmp_path / "run ner"
    runner_dir.mkdir()
    seen = tmp_path / "seen.txt"
    runner = runner_dir / "tokitty hook.cmd"
    runner.write_text(
        f'@echo off\r\necho %~1> "{seen}"\r\necho %~2>> "{seen}"\r\n', encoding="utf-8"
    )
    config_dir = str(tmp_path / ".codex")
    command = hi._build_command(
        config_dir, frozen=True, runner=str(runner), platform="win32", provider="codex"
    )["command"]

    # A str (not a list) so the command line reaches cmd untouched, the way
    # Codex builds it: cmd /C "<command>".
    subprocess.run(f'cmd /C "{command}"', check=True)

    lines = seen.read_text(encoding="utf-8").splitlines()
    assert lines[0].strip() == "--sessions-dir"
    assert lines[1].strip() == f"{config_dir}/tokitty/sessions"


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


def test_codex_does_not_own_hook_with_args_key():
    assert not _owned(
        f'"{LINUX_RUNNER}" --sessions-dir "/home/nick/.codex/tokitty/sessions"', args=["x"]
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
    assert handler["command"].startswith('"')


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
