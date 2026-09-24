"""Tests for tokitty/hooks_install.py: --install-hooks / --uninstall-hooks.

SAFETY: every test operates on tmp_path fixtures only. Never touch the
real ~/.claude or ~/.claude-work.
"""
import json
from pathlib import Path

import pytest

from tokitty import hooks_install as hi
from tokitty.accounts import Account
from tokitty.hooks_install import (
    ConfigDirResult,
    apply_account_mutation,
    clear_pending_hook_op,
    load_pending_hook_op,
    retry_pending_hook_op,
    save_pending_hook_op,
)


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------

def test_wsl_native_path_from_wsl_localhost_unc():
    p = r"\\wsl.localhost\Ubuntu\home\nick\.claude"
    assert hi._wsl_native_path(p) == "/home/nick/.claude"


def test_wsl_native_path_from_wsl_dollar_unc():
    p = r"\\wsl$\Ubuntu\home\nick\.claude"
    assert hi._wsl_native_path(p) == "/home/nick/.claude"


def test_wsl_native_path_posix_passthrough():
    p = "/home/nick/.claude"
    assert hi._wsl_native_path(p) == "/home/nick/.claude"


def test_windows_local_path_detected():
    assert hi._is_windows_local_path(r"C:\Users\nick\.claude")
    assert not hi._is_windows_local_path("/home/nick/.claude")
    assert not hi._is_windows_local_path(r"\\wsl.localhost\Ubuntu\home\nick\.claude")


def test_build_command_posix_uses_python3():
    handler = hi._build_command("/home/nick/.claude")
    assert handler["type"] == "command"
    assert handler["command"] == (
        'python3 "/home/nick/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/nick/.claude/tokitty/sessions"'
    )


def test_build_command_wsl_unc_maps_to_native():
    handler = hi._build_command(r"\\wsl.localhost\Ubuntu\home\nick\.claude")
    assert handler["command"] == (
        'python3 "/home/nick/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/nick/.claude/tokitty/sessions"'
    )


def test_build_command_windows_local_uses_python():
    handler = hi._build_command(r"C:\Users\nick\.claude")
    assert handler["command"].startswith('python "C:\\Users\\nick\\.claude/tokitty/hook_writer.py"')


def test_build_command_quotes_spaced_path():
    handler = hi._build_command("/home/nick 2/.claude")
    assert handler["command"] == (
        'python3 "/home/nick 2/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/nick 2/.claude/tokitty/sessions"'
    )


def test_build_command_strips_trailing_slash_from_home():
    with_slash = hi._build_command("/home/nick/.claude/")
    without_slash = hi._build_command("/home/nick/.claude")
    assert with_slash == without_slash


def test_build_command_strips_trailing_backslash_from_windows_home():
    with_slash = hi._build_command("C:\\Users\\nick\\.claude\\")
    without_slash = hi._build_command("C:\\Users\\nick\\.claude")
    assert with_slash == without_slash


# ---------------------------------------------------------------------------
# HookTarget / _hook_target
# ---------------------------------------------------------------------------

def test_hook_target_for_claude_and_default():
    target = hi._hook_target("claude")
    assert target.settings_file == "settings.json"
    assert target.local_settings_file == "settings.local.json"
    assert target.events == tuple(hi.HOOK_EVENTS)
    assert hi._hook_target(None) == target


def test_hook_target_raises_for_unknown_provider():
    with pytest.raises(ValueError):
        hi._hook_target("codex")


# ---------------------------------------------------------------------------
# _is_owned_hook (exact ownership matcher)
# ---------------------------------------------------------------------------

def test_is_owned_hook_accepts_current_quoted_form():
    config_dir = "/home/nick/.claude"
    hook = hi._build_command(config_dir)
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_accepts_historical_unquoted_form():
    config_dir = "/home/nick/.claude"
    hook = {
        "type": "command",
        "command": "python3 /home/nick/.claude/tokitty/hook_writer.py "
        "--sessions-dir /home/nick/.claude/tokitty/sessions",
    }
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_accepts_windows_local_home_as_built():
    # _build_command never rewrites the drive-letter home to forward
    # slashes, so this is a mix of backslashes and forward slashes.
    config_dir = r"C:\Users\nick\.claude"
    hook = hi._build_command(config_dir)
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_accepts_windows_local_home_all_backslashes():
    config_dir = r"C:\Users\nick\.claude"
    hook = {
        "type": "command",
        "command": (
            r'python "C:\Users\nick\.claude\tokitty\hook_writer.py" '
            r'--sessions-dir "C:\Users\nick\.claude\tokitty\sessions"'
        ),
    }
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_case_folds_drive_letter_home():
    written_home = r"C:\Users\Nick\.claude"
    hook = hi._build_command(written_home)
    account_home = r"c:\users\nick\.claude"
    assert hi._is_owned_hook(hook, account_home)


def test_is_owned_hook_accepts_doubled_slash():
    hook = {
        "type": "command",
        "command": 'python3 "/h/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/h/.claude//tokitty/sessions"',
    }
    assert hi._is_owned_hook(hook, "/h/.claude")


def test_is_owned_hook_wsl_unc_home_matches_posix_written_hook():
    config_dir = r"\\wsl.localhost\Ubuntu\home\nick\.claude"
    hook = {
        "type": "command",
        "command": 'python3 "/home/nick/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/nick/.claude/tokitty/sessions"',
    }
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_rejects_unrelated_python_script():
    hook = {"type": "command", "command": "python3 /opt/tokitty/myhook.py"}
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_rejects_non_python_interpreter():
    hook = {"type": "command", "command": "bash ~/tokitty-scripts/run.sh"}
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_rejects_other_home():
    config_dir = "/home/nick/.claude"
    hook = hi._build_command(config_dir)
    assert not hi._is_owned_hook(hook, "/other-home")


def test_is_owned_hook_rejects_prompt_type():
    hook = {
        "type": "prompt",
        "command": "python3 /home/nick/.claude/tokitty/hook_writer.py "
        "--sessions-dir /home/nick/.claude/tokitty/sessions",
    }
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_rejects_partial_lookalike_missing_sessions_flag():
    # The old fixture shape used before ownership was exact: mentions
    # tokitty, but is not the command tokitty actually writes.
    hook = {"type": "command", "command": "python3 x/tokitty/hook_writer.py"}
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_accepts_historical_unquoted_drive_letter_form():
    # Written by 9bab1b3 for a drive-letter home: unquoted, so shlex's
    # posix parser reads the backslashes as escapes and mangles the path.
    config_dir = r"C:\Users\nick\.claude"
    hook = {
        "type": "command",
        "command": r"python C:\Users\nick\.claude/tokitty/hook_writer.py "
        r"--sessions-dir C:\Users\nick\.claude/tokitty/sessions",
    }
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_rejects_historical_unquoted_form_for_a_different_drive_letter_home():
    hook = {
        "type": "command",
        "command": r"python C:\Users\nick\.claude/tokitty/hook_writer.py "
        r"--sessions-dir C:\Users\nick\.claude/tokitty/sessions",
    }
    assert not hi._is_owned_hook(hook, r"D:\Users\nick\.claude")


# ---------------------------------------------------------------------------
# get_config_dirs
# ---------------------------------------------------------------------------

def test_get_config_dirs_defaults_to_home_claude(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    dirs = hi.get_config_dirs()
    assert dirs == [(str(fake_home / ".claude"), "claude")]


def test_get_config_dirs_reads_accounts_json(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"config_dir": "/a/.claude"}, {"config_dir": "/b/.claude-work"}]})
    )
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    dirs = hi.get_config_dirs()
    assert dirs == [("/a/.claude", "claude"), ("/b/.claude-work", "claude")]


def test_get_config_dirs_falls_back_on_malformed_accounts_json(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text("not json")
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    dirs = hi.get_config_dirs()
    assert dirs == [(str(fake_home / ".claude"), "claude")]


def test_get_config_dirs_accepts_explicit_state_dir(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"config_dir": "/a/.claude"}]})
    )
    assert hi.get_config_dirs(state_dir) == [("/a/.claude", "claude")]


# ---------------------------------------------------------------------------
# _default_config_dir (win32 WSL resolution)
# ---------------------------------------------------------------------------

def test_default_config_dir_non_win32_is_home_claude(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(hi.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    assert hi._default_config_dir() == str(fake_home / ".claude")


def test_default_config_dir_win32_resolves_wsl_unc(monkeypatch, tmp_path):
    monkeypatch.setattr(hi.sys, "platform", "win32")

    def fake_find_wsl_credentials(run=None):
        return "Ubuntu", "/home/nick/.claude/.credentials.json"

    import tokitty.wsl_probe as wsl_probe
    monkeypatch.setattr(wsl_probe, "find_wsl_credentials", fake_find_wsl_credentials)

    assert hi._default_config_dir() == r"\\wsl.localhost\Ubuntu\home\nick\.claude"


def test_default_config_dir_win32_falls_back_when_wsl_resolution_fails(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(hi.sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    def raising_find_wsl_credentials(run=None):
        raise RuntimeError("no wsl.exe")

    import tokitty.wsl_probe as wsl_probe
    monkeypatch.setattr(wsl_probe, "find_wsl_credentials", raising_find_wsl_credentials)

    assert hi._default_config_dir() == str(fake_home / ".claude")


# ---------------------------------------------------------------------------
# install_hooks_for_dir
# ---------------------------------------------------------------------------

def test_install_creates_missing_settings_json(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    result = hi.install_hooks_for_dir(str(config_dir))
    assert result.ok
    data = json.loads((config_dir / "settings.json").read_text())
    assert set(data["hooks"].keys()) == {e for e, _ in hi.HOOK_EVENTS}
    assert (config_dir / "tokitty" / "hook_writer.py").exists()


def test_install_copies_hook_writer_verbatim(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    copied = (config_dir / "tokitty" / "hook_writer.py").read_text()
    original = hi._HOOK_WRITER_SOURCE.read_text()
    assert copied == original


def test_install_registers_all_events_with_correct_matchers(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    data = json.loads((config_dir / "settings.json").read_text())
    for event, matcher in hi.HOOK_EVENTS:
        entries = data["hooks"][event]
        assert len(entries) == 1
        assert entries[0]["matcher"] == matcher
        cmd = entries[0]["hooks"][0]["command"]
        assert "tokitty" in cmd
        assert "hook_writer.py" in cmd
        assert "--sessions-dir" in cmd


def test_install_is_additive_preserves_existing_hooks(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    existing = {
        "otherKey": "preserved",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]}
            ]
        },
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    hi.install_hooks_for_dir(str(config_dir))
    data = json.loads((config_dir / "settings.json").read_text())
    assert data["otherKey"] == "preserved"
    pretool = data["hooks"]["PreToolUse"]
    assert len(pretool) == 2
    assert {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]} in pretool
    assert any(hi._is_tokitty_entry(e, str(config_dir)) for e in pretool)


def test_install_writes_timestamped_backup(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps({"foo": "bar"}))
    hi.install_hooks_for_dir(str(config_dir))
    backups = list(config_dir.glob("settings.json.tokitty-backup-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == {"foo": "bar"}


def test_backup_uniquifies_on_same_second_collision(monkeypatch, tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    settings_path = config_dir / "settings.json"
    settings_path.write_text(json.dumps({"foo": "bar"}))
    monkeypatch.setattr(hi.time, "strftime", lambda fmt: "20260101-000000")

    hi._backup(settings_path)
    settings_path.write_text(json.dumps({"foo": "baz"}))
    hi._backup(settings_path)

    backups = sorted(config_dir.glob("settings.json.tokitty-backup-*"))
    assert len(backups) == 2
    contents = {b.read_text() for b in backups}
    assert json.dumps({"foo": "bar"}) in contents
    assert json.dumps({"foo": "baz"}) in contents


def test_install_no_backup_when_settings_missing(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    backups = list(config_dir.glob("settings.json.tokitty-backup-*"))
    assert backups == []


def test_install_idempotent_running_twice_yields_identical_file(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    first = (config_dir / "settings.json").read_text()
    hi.install_hooks_for_dir(str(config_dir))
    second = (config_dir / "settings.json").read_text()
    assert first == second
    data = json.loads(second)
    for event, _ in hi.HOOK_EVENTS:
        assert len(data["hooks"][event]) == 1


def test_install_skips_event_already_marked_in_settings_local(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    owned_command = hi._build_command(str(config_dir))["command"]
    local = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": owned_command}]}
            ]
        }
    }
    (config_dir / "settings.local.json").write_text(json.dumps(local))
    result = hi.install_hooks_for_dir(str(config_dir))
    assert result.ok
    data = json.loads((config_dir / "settings.json").read_text())
    assert "Stop" not in data["hooks"]
    assert "PreToolUse" in data["hooks"]


def test_install_aborts_on_corrupt_settings_json_touches_nothing(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{not valid json")
    original = (config_dir / "settings.json").read_text()
    result = hi.install_hooks_for_dir(str(config_dir))
    assert not result.ok
    assert (config_dir / "settings.json").read_text() == original
    assert not (config_dir / "tokitty").exists()
    assert list(config_dir.glob("settings.json.tokitty-backup-*")) == []


def test_install_aborts_cleanly_on_non_dict_hooks_key(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps({"hooks": "not-a-dict"}))
    original = (config_dir / "settings.json").read_text()
    result = hi.install_hooks_for_dir(str(config_dir))
    assert not result.ok
    assert (config_dir / "settings.json").read_text() == original
    assert list(config_dir.glob("settings.json.tokitty-backup-*")) == []


def test_install_aborts_cleanly_on_non_list_event_value(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": "not-a-list"}})
    )
    original = (config_dir / "settings.json").read_text()
    result = hi.install_hooks_for_dir(str(config_dir))
    assert not result.ok
    assert (config_dir / "settings.json").read_text() == original
    assert list(config_dir.glob("settings.json.tokitty-backup-*")) == []


def test_install_aborts_on_corrupt_settings_local_json(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.local.json").write_text("{not valid json")
    result = hi.install_hooks_for_dir(str(config_dir))
    assert not result.ok
    assert not (config_dir / "settings.json").exists()


# ---------------------------------------------------------------------------
# uninstall_hooks_for_dir
# ---------------------------------------------------------------------------

def test_uninstall_removes_exactly_marker_entries(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert result.ok
    data = json.loads((config_dir / "settings.json").read_text())
    assert data.get("hooks", {}) == {}


def test_uninstall_preserves_non_tokitty_entries(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]}
            ]
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    hi.install_hooks_for_dir(str(config_dir))
    hi.uninstall_hooks_for_dir(str(config_dir))
    data = json.loads((config_dir / "settings.json").read_text())
    assert data["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]}
    ]


def test_uninstall_keeps_user_handler_in_a_shared_entry(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    owned_handler = hi._build_command(str(config_dir))
    existing = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "some-other-tool"},
                        dict(owned_handler),
                    ],
                }
            ]
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert result.ok
    data = json.loads((config_dir / "settings.json").read_text())
    assert data["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]}
    ]
    assert result.installed_events == ["PreToolUse"]


def test_uninstall_drops_an_entry_left_with_no_handlers(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    owned_handler = hi._build_command(str(config_dir))
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "", "hooks": [dict(owned_handler)]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]},
            ]
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert result.ok
    data = json.loads((config_dir / "settings.json").read_text())
    assert data["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]}
    ]
    assert result.installed_events == ["PreToolUse"]


def test_uninstall_drops_an_event_left_with_no_entries(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    owned_handler = hi._build_command(str(config_dir))
    existing = {
        "hooks": {
            "Stop": [{"matcher": "", "hooks": [dict(owned_handler)]}],
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert result.ok
    data = json.loads((config_dir / "settings.json").read_text())
    assert "Stop" not in data["hooks"]
    assert result.installed_events == ["Stop"]


def test_uninstall_keeps_the_position_of_user_entries_around_tokittys(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    owned_handler = hi._build_command(str(config_dir))
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Before", "hooks": [{"type": "command", "command": "before-tool"}]},
                {"matcher": "", "hooks": [dict(owned_handler)]},
                {"matcher": "After", "hooks": [{"type": "command", "command": "after-tool"}]},
            ]
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    hi.uninstall_hooks_for_dir(str(config_dir))
    data = json.loads((config_dir / "settings.json").read_text())
    assert data["hooks"]["PreToolUse"] == [
        {"matcher": "Before", "hooks": [{"type": "command", "command": "before-tool"}]},
        {"matcher": "After", "hooks": [{"type": "command", "command": "after-tool"}]},
    ]


def test_uninstall_writes_nothing_and_no_backup_when_nothing_owned(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]}
            ]
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    original = (config_dir / "settings.json").read_text()
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert result.ok
    assert result.installed_events == []
    assert (config_dir / "settings.json").read_text() == original
    assert list(config_dir.glob("settings.json.tokitty-backup-*")) == []


def test_uninstall_leaves_settings_local_alone_but_reports(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    owned_command = hi._build_command(str(config_dir))["command"]
    local = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": owned_command}]}
            ]
        }
    }
    (config_dir / "settings.local.json").write_text(json.dumps(local))
    local_before = (config_dir / "settings.local.json").read_text()
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert result.ok
    assert "settings.local.json" in result.message
    assert (config_dir / "settings.local.json").read_text() == local_before


def test_uninstall_writes_backup(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    # remove any install-time backup so we can isolate the uninstall backup
    for b in config_dir.glob("settings.json.tokitty-backup-*"):
        b.unlink()
    hi.uninstall_hooks_for_dir(str(config_dir))
    backups = list(config_dir.glob("settings.json.tokitty-backup-*"))
    assert len(backups) == 1


def test_uninstall_leaves_hook_writer_copy_and_sessions_dir(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    sessions_dir = config_dir / "tokitty" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "abc.json").write_text("{}")
    hi.uninstall_hooks_for_dir(str(config_dir))
    assert (config_dir / "tokitty" / "hook_writer.py").exists()
    assert (sessions_dir / "abc.json").exists()


def test_uninstall_aborts_on_corrupt_settings_json(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{not valid json")
    original = (config_dir / "settings.json").read_text()
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert not result.ok
    assert (config_dir / "settings.json").read_text() == original


def test_uninstall_no_op_when_nothing_installed(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps({"hooks": {}}))
    result = hi.uninstall_hooks_for_dir(str(config_dir))
    assert result.ok
    assert result.installed_events == []


# ---------------------------------------------------------------------------
# Top-level install_hooks / uninstall_hooks (exit codes, multi-dir)
# ---------------------------------------------------------------------------

def test_install_hooks_returns_0_on_success(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    rc = hi.install_hooks()
    assert rc == 0
    out = capsys.readouterr().out
    assert "restart running Claude Code sessions" in out


def test_install_hooks_returns_1_if_any_dir_fails(monkeypatch, tmp_path):
    good = tmp_path / "good" / ".claude"
    good.mkdir(parents=True)
    bad = tmp_path / "bad" / ".claude"
    bad.mkdir(parents=True)
    (bad / "settings.json").write_text("{not valid json")
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(good), "claude"), (str(bad), "claude")])
    rc = hi.install_hooks()
    assert rc == 1
    assert (good / "tokitty" / "hook_writer.py").exists()


def test_uninstall_hooks_returns_0_on_success(monkeypatch, tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    rc = hi.uninstall_hooks()
    assert rc == 0


def test_install_hooks_passes_provider_to_install_fn(monkeypatch, tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    calls = []

    def fake_install(cd, provider):
        calls.append((cd, provider))
        return ConfigDirResult(cd, True, "installed")

    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    monkeypatch.setattr(hi, "install_hooks_for_dir", fake_install)
    hi.install_hooks()
    assert calls == [(str(config_dir), "claude")]


def test_uninstall_hooks_passes_provider_to_uninstall_fn(monkeypatch, tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    calls = []

    def fake_uninstall(cd, provider):
        calls.append((cd, provider))
        return ConfigDirResult(cd, True, "uninstalled")

    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    monkeypatch.setattr(hi, "uninstall_hooks_for_dir", fake_uninstall)
    hi.uninstall_hooks()
    assert calls == [(str(config_dir), "claude")]


def test_multiple_config_dirs_get_independent_sessions_dirs(monkeypatch, tmp_path):
    dir_a = tmp_path / "a" / ".claude"
    dir_b = tmp_path / "b" / ".claude-work"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(dir_a), "claude"), (str(dir_b), "claude")])
    hi.install_hooks()
    data_a = json.loads((dir_a / "settings.json").read_text())
    data_b = json.loads((dir_b / "settings.json").read_text())
    cmd_a = data_a["hooks"]["Stop"][0]["hooks"][0]["command"]
    cmd_b = data_b["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert str(dir_a) in cmd_a
    assert str(dir_b) in cmd_b
    assert cmd_a != cmd_b


def test_local_config_path_translates_unc_on_posix(monkeypatch):
    from tokitty import hooks_install

    monkeypatch.setattr(hooks_install.sys, "platform", "linux")
    assert (
        hooks_install._local_config_path("\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude")
        == "/home/u/.claude"
    )
    assert hooks_install._local_config_path("/home/u/.claude") == "/home/u/.claude"


def test_local_config_path_keeps_unc_on_windows(monkeypatch):
    from tokitty import hooks_install

    monkeypatch.setattr(hooks_install.sys, "platform", "win32")
    unc = "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude"
    assert hooks_install._local_config_path(unc) == unc


# ---------------------------------------------------------------------------
# _write_settings atomicity
# ---------------------------------------------------------------------------

def test_write_settings_uses_tmp_file_and_replace(tmp_path, monkeypatch):
    import os
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("tokitty.hooks_install.os.replace", spy_replace)
    path = tmp_path / "settings.json"
    hi._write_settings(path, {"a": 1})
    assert len(calls) == 1
    assert calls[0][0].endswith("settings.json.tmp")
    assert calls[0][1].endswith("settings.json")
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_write_settings_failed_write_does_not_truncate_original(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"original": true}\n', encoding="utf-8")

    def raising_write_text(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", raising_write_text)
    try:
        hi._write_settings(path, {"new": True})
    except OSError:
        pass
    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == {"original": True}


# ---------------------------------------------------------------------------
# Pending hook op journal + apply_account_mutation / retry_pending_hook_op
# ---------------------------------------------------------------------------

def test_pending_hook_op_round_trip(tmp_path):
    save_pending_hook_op(tmp_path, "install", "/home/u/.claude")
    assert load_pending_hook_op(tmp_path) == {"op": "install", "config_dir": "/home/u/.claude"}
    clear_pending_hook_op(tmp_path)
    assert load_pending_hook_op(tmp_path) is None


def test_load_pending_hook_op_missing_file_returns_none(tmp_path):
    assert load_pending_hook_op(tmp_path) is None


def test_apply_account_mutation_writes_accounts_before_pending_op_before_hook(tmp_path):
    order = []
    accounts = [Account(name="a", config_dir="/home/u/.claude")]

    def fake_save_accounts(state_dir, accts):
        order.append("save_accounts")

    def fake_install(config_dir, provider):
        order.append("hook_call")
        return ConfigDirResult(config_dir, True, "installed")

    import tokitty.hooks_install as hi

    class _Spy:
        def __call__(self, state_dir, accts):
            fake_save_accounts(state_dir, accts)

    original_save_pending = hi.save_pending_hook_op

    def spy_save_pending(state_dir, op, config_dir, provider=None):
        order.append("save_pending")
        return original_save_pending(state_dir, op, config_dir, provider)

    import tokitty.accounts as accounts_mod
    monkeypatched_save_accounts = accounts_mod.save_accounts

    def spy_save_accounts(state_dir, accts):
        order.append("save_accounts")
        return monkeypatched_save_accounts(state_dir, accts)

    hi.save_accounts = spy_save_accounts
    hi.save_pending_hook_op = spy_save_pending
    try:
        apply_account_mutation(tmp_path, accounts, "install", "/home/u/.claude", install_fn=fake_install)
    finally:
        hi.save_accounts = monkeypatched_save_accounts
        hi.save_pending_hook_op = original_save_pending

    assert order == ["save_accounts", "save_pending", "hook_call"]


def test_apply_account_mutation_clears_pending_op_on_success(tmp_path):
    accounts = [Account(name="a", config_dir="/home/u/.claude")]
    apply_account_mutation(
        tmp_path, accounts, "install", "/home/u/.claude",
        install_fn=lambda cd, provider: ConfigDirResult(cd, True, "installed"),
    )
    assert load_pending_hook_op(tmp_path) is None


def test_apply_account_mutation_leaves_pending_op_on_ok_false(tmp_path):
    accounts = [Account(name="a", config_dir="/home/u/.claude")]
    apply_account_mutation(
        tmp_path, accounts, "install", "/home/u/.claude",
        install_fn=lambda cd, provider: ConfigDirResult(cd, False, "aborted"),
    )
    assert load_pending_hook_op(tmp_path) == {"op": "install", "config_dir": "/home/u/.claude", "provider": "claude"}


def test_apply_account_mutation_leaves_pending_op_on_raised_exception(tmp_path):
    accounts = [Account(name="a", config_dir="/home/u/.claude")]

    def raising_install(config_dir, provider):
        raise OSError("disk full")

    try:
        apply_account_mutation(tmp_path, accounts, "install", "/home/u/.claude", install_fn=raising_install)
    except OSError:
        pass
    assert load_pending_hook_op(tmp_path) == {"op": "install", "config_dir": "/home/u/.claude", "provider": "claude"}


def test_apply_account_mutation_passes_provider_to_install_fn(tmp_path):
    accounts = [Account(name="a", config_dir="/home/u/.claude")]
    calls = []

    def fake_install(config_dir, provider):
        calls.append((config_dir, provider))
        return ConfigDirResult(config_dir, True, "installed")

    apply_account_mutation(
        tmp_path, accounts, "install", "/home/u/.claude",
        install_fn=fake_install, provider="claude",
    )
    assert calls == [("/home/u/.claude", "claude")]


def test_retry_pending_hook_op_clears_on_success(tmp_path):
    save_pending_hook_op(tmp_path, "remove", "/home/u/.claude")
    result = retry_pending_hook_op(
        tmp_path, uninstall_fn=lambda cd, provider: ConfigDirResult(cd, True, "uninstalled")
    )
    assert result.ok
    assert load_pending_hook_op(tmp_path) is None


def test_retry_pending_hook_op_returns_none_when_nothing_pending(tmp_path):
    assert retry_pending_hook_op(tmp_path) is None


def test_retry_pending_hook_op_passes_recorded_provider_to_fn(tmp_path):
    save_pending_hook_op(tmp_path, "install", "/home/u/.claude", "claude")
    calls = []

    def fake_install(config_dir, provider):
        calls.append((config_dir, provider))
        return ConfigDirResult(config_dir, True, "installed")

    retry_pending_hook_op(tmp_path, install_fn=fake_install, uninstall_fn=_forbidden)
    assert calls == [("/home/u/.claude", "claude")]


def test_retry_resolves_provider_from_matching_account_for_legacy_record(tmp_path):
    _write_accounts(tmp_path, [{"name": "a", "config_dir": "/home/u/.claude-work", "provider": "claude"}])
    save_pending_hook_op(tmp_path, "install", "/home/u/.claude-work")  # legacy: no provider recorded
    calls = []

    def fake_install(config_dir, provider):
        calls.append((config_dir, provider))
        return ConfigDirResult(config_dir, True, "installed")

    retry_pending_hook_op(tmp_path, install_fn=fake_install, uninstall_fn=_forbidden)
    assert calls == [("/home/u/.claude-work", "claude")]


# ---------------------------------------------------------------------------
# Providers without hooks (Codex)
# ---------------------------------------------------------------------------

def _write_accounts(state_dir, entries):
    (state_dir / "accounts.json").write_text(json.dumps({"accounts": entries}), encoding="utf-8")


def _codex_home(tmp_path):
    home = tmp_path / ".codex"
    (home / "sessions").mkdir(parents=True)
    return home


def _assert_untouched(home):
    assert sorted(p.name for p in home.iterdir()) == ["sessions"]


def test_provider_has_hooks_follows_the_activity_capability():
    assert hi.provider_has_hooks("claude")
    assert hi.provider_has_hooks(None)
    assert not hi.provider_has_hooks("codex")
    assert not hi.provider_has_hooks("gemini")


def test_get_config_dirs_skips_a_codex_account(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_accounts(state_dir, [
        {"config_dir": "/a/.claude"},
        {"config_dir": "/b/.codex", "provider": "codex"},
    ])
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    assert hi.get_config_dirs() == [("/a/.claude", "claude")]


def test_get_config_dirs_with_only_codex_accounts_is_empty(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_accounts(state_dir, [{"config_dir": "/b/.codex", "provider": "codex"}])
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    assert hi.get_config_dirs() == []


def test_install_hooks_leaves_a_codex_home_untouched(monkeypatch, tmp_path, capsys):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    home = _codex_home(tmp_path)
    _write_accounts(state_dir, [{"config_dir": str(home), "provider": "codex"}])
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    assert hi.install_hooks() == 0
    assert hi.uninstall_hooks() == 0
    _assert_untouched(home)
    assert "nothing to install" in capsys.readouterr().out


def _forbidden(config_dir, provider):
    raise AssertionError(f"hook function called for {config_dir}")


def test_adding_a_codex_account_never_calls_install_fn(tmp_path):
    from tokitty.accounts import load_accounts

    home = _codex_home(tmp_path)
    accounts = [Account(name="c", config_dir=str(home), provider="codex")]
    result = apply_account_mutation(
        tmp_path, accounts, "install", str(home),
        install_fn=_forbidden, uninstall_fn=_forbidden, provider="codex",
    )
    assert result.ok
    assert [a.provider for a in load_accounts(tmp_path)] == ["codex"]
    assert load_pending_hook_op(tmp_path) is None
    _assert_untouched(home)


def test_removing_a_codex_account_never_calls_uninstall_fn(tmp_path):
    from tokitty.accounts import load_accounts

    home = _codex_home(tmp_path)
    remaining = [Account(name="a", config_dir="/home/u/.claude")]
    result = apply_account_mutation(
        tmp_path, remaining, "remove", str(home),
        install_fn=_forbidden, uninstall_fn=_forbidden, provider="codex",
    )
    assert result.ok
    assert [a.name for a in load_accounts(tmp_path)] == ["a"]
    assert load_pending_hook_op(tmp_path) is None
    _assert_untouched(home)


def test_retry_clears_a_pending_op_for_a_codex_account(tmp_path):
    home = _codex_home(tmp_path)
    _write_accounts(tmp_path, [{"name": "c", "config_dir": str(home), "provider": "codex"}])
    save_pending_hook_op(tmp_path, "install", str(home))
    assert retry_pending_hook_op(tmp_path, install_fn=_forbidden, uninstall_fn=_forbidden) is None
    assert load_pending_hook_op(tmp_path) is None
    _assert_untouched(home)


def test_retry_clears_a_pending_remove_for_a_codex_home_no_longer_listed(tmp_path):
    home = _codex_home(tmp_path)
    _write_accounts(tmp_path, [{"name": "a", "config_dir": "/home/u/.claude"}])
    save_pending_hook_op(tmp_path, "remove", str(home))
    assert retry_pending_hook_op(tmp_path, install_fn=_forbidden, uninstall_fn=_forbidden) is None
    assert load_pending_hook_op(tmp_path) is None


def test_retry_still_replays_a_pending_op_for_a_removed_claude_dir(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    (claude / "sessions").mkdir()
    save_pending_hook_op(tmp_path, "remove", str(claude))
    calls = []

    def fake_uninstall(config_dir, provider):
        calls.append(config_dir)
        return ConfigDirResult(config_dir, True, "uninstalled")

    retry_pending_hook_op(tmp_path, install_fn=_forbidden, uninstall_fn=fake_uninstall)
    assert calls == [str(claude)]


def test_new_pending_ops_record_their_provider(tmp_path):
    apply_account_mutation(
        tmp_path, [], "remove", "/home/u/.claude",
        uninstall_fn=lambda d, provider: ConfigDirResult(d, False, "failed"),
    )
    assert load_pending_hook_op(tmp_path)["provider"] == "claude"


def test_retry_trusts_a_recorded_claude_provider_over_the_dir_shape(tmp_path):
    # A Claude dir with hooks but, right now, no credentials or projects/
    # looks like a Codex home. The recorded provider must win.
    claude = tmp_path / ".claude"
    (claude / "sessions").mkdir(parents=True)
    save_pending_hook_op(tmp_path, "remove", str(claude), "claude")
    calls = []

    def fake_uninstall(config_dir, provider):
        calls.append(config_dir)
        return ConfigDirResult(config_dir, True, "uninstalled")

    retry_pending_hook_op(tmp_path, install_fn=_forbidden, uninstall_fn=fake_uninstall)
    assert calls == [str(claude)]


def test_retry_clears_a_recorded_codex_provider(tmp_path):
    home = _codex_home(tmp_path)
    save_pending_hook_op(tmp_path, "install", str(home), "codex")
    assert retry_pending_hook_op(tmp_path, install_fn=_forbidden, uninstall_fn=_forbidden) is None
    assert load_pending_hook_op(tmp_path) is None


# ---------------------------------------------------------------------------
# A warning on a successful result
# ---------------------------------------------------------------------------

def test_config_dir_result_warning_defaults_to_none():
    result = ConfigDirResult("cd", True, "installed")
    assert result.warning is None


def test_config_dir_result_stores_a_warning():
    result = ConfigDirResult("cd", True, "installed", warning="fallback used")
    assert result.warning == "fallback used"


def test_install_hooks_prints_warning_to_stderr_for_ok_result(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    def fake_install(cd, provider):
        return ConfigDirResult(cd, True, "installed", warning="fallback used")

    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    monkeypatch.setattr(hi, "install_hooks_for_dir", fake_install)
    rc = hi.install_hooks()
    assert rc == 0
    err = capsys.readouterr().err
    assert f"{config_dir}: warning: fallback used" in err


def test_install_hooks_prints_no_warning_line_without_one(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    def fake_install(cd, provider):
        return ConfigDirResult(cd, True, "installed")

    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    monkeypatch.setattr(hi, "install_hooks_for_dir", fake_install)
    rc = hi.install_hooks()
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" not in err


def test_uninstall_hooks_prints_warning_to_stderr_for_ok_result(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    def fake_uninstall(cd, provider):
        return ConfigDirResult(cd, True, "uninstalled", warning="fallback used")

    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    monkeypatch.setattr(hi, "uninstall_hooks_for_dir", fake_uninstall)
    rc = hi.uninstall_hooks()
    assert rc == 0
    err = capsys.readouterr().err
    assert f"{config_dir}: warning: fallback used" in err


def test_uninstall_hooks_prints_no_warning_line_without_one(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    def fake_uninstall(cd, provider):
        return ConfigDirResult(cd, True, "uninstalled")

    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    monkeypatch.setattr(hi, "uninstall_hooks_for_dir", fake_uninstall)
    rc = hi.uninstall_hooks()
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" not in err
