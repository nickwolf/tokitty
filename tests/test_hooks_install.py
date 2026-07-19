"""Tests for tokitty/hooks_install.py: --install-hooks / --uninstall-hooks.

SAFETY: every test operates on tmp_path fixtures only. Never touch the
real ~/.claude or ~/.claude-work.
"""
import json
from pathlib import Path

import pytest

from tokitty import hooks_install as hi


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
    cmd = hi._build_command("/home/nick/.claude")
    assert cmd == (
        'python3 "/home/nick/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/nick/.claude/tokitty/sessions"'
    )


def test_build_command_wsl_unc_maps_to_native():
    cmd = hi._build_command(r"\\wsl.localhost\Ubuntu\home\nick\.claude")
    assert cmd == (
        'python3 "/home/nick/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/nick/.claude/tokitty/sessions"'
    )


def test_build_command_windows_local_uses_python():
    cmd = hi._build_command(r"C:\Users\nick\.claude")
    assert cmd.startswith('python "C:\\Users\\nick\\.claude/tokitty/hook_writer.py"')


def test_build_command_quotes_spaced_path():
    cmd = hi._build_command("/home/nick 2/.claude")
    assert cmd == (
        'python3 "/home/nick 2/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/nick 2/.claude/tokitty/sessions"'
    )


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
    assert dirs == [str(fake_home / ".claude")]


def test_get_config_dirs_reads_accounts_json(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"config_dir": "/a/.claude"}, {"config_dir": "/b/.claude-work"}]})
    )
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    dirs = hi.get_config_dirs()
    assert dirs == ["/a/.claude", "/b/.claude-work"]


def test_get_config_dirs_falls_back_on_malformed_accounts_json(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text("not json")
    monkeypatch.setattr(hi, "get_state_dir", lambda: state_dir)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    dirs = hi.get_config_dirs()
    assert dirs == [str(fake_home / ".claude")]


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
    assert any(hi._is_tokitty_entry(e) for e in pretool)


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
    local = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "python3 x/tokitty/hook_writer.py"}]}
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


def test_uninstall_leaves_settings_local_alone_but_reports(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    local = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "python3 x/tokitty/hook_writer.py"}]}
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
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [str(config_dir)])
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
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [str(good), str(bad)])
    rc = hi.install_hooks()
    assert rc == 1
    assert (good / "tokitty" / "hook_writer.py").exists()


def test_uninstall_hooks_returns_0_on_success(monkeypatch, tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [str(config_dir)])
    rc = hi.uninstall_hooks()
    assert rc == 0


def test_multiple_config_dirs_get_independent_sessions_dirs(monkeypatch, tmp_path):
    dir_a = tmp_path / "a" / ".claude"
    dir_b = tmp_path / "b" / ".claude-work"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [str(dir_a), str(dir_b)])
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
