"""Tests for tokitty/hooks_install.py: --install-hooks / --uninstall-hooks.

SAFETY: every test operates on tmp_path fixtures only. Never touch the
real ~/.claude or ~/.claude-work.
"""
import json
import os
import sys
from pathlib import Path

import pytest

from tokitty import hooks_install as hi
from tokitty import runner_link
from tokitty.accounts import Account
from tokitty.frozen import MOVE_TO_APPLICATIONS
from tokitty.hooks_install import (
    ConfigDirResult,
    apply_account_mutation,
    clear_pending_hook_op,
    load_pending_hook_op,
    retry_pending_hook_op,
    save_pending_hook_op,
)

EXE_NAME = "tokitty.exe" if sys.platform == "win32" else "tokitty"
RUNNER_NAME = "tokitty-hook.exe" if sys.platform == "win32" else "tokitty-hook"


def _fake_release(root):
    """A host-native fake frozen build: exe + tokitty-hook side by side.
    Never a real directory outside tmp_path -- release lives under the
    caller's own tmp_path."""
    root.mkdir(parents=True)
    (root / EXE_NAME).write_text("gui", encoding="utf-8")
    (root / RUNNER_NAME).write_text("hook", encoding="utf-8")
    return root / EXE_NAME


def _unlink_current_link(state_dir):
    link = state_dir / "current"
    if runner_link._is_link(str(link)):
        (os.rmdir if sys.platform == "win32" else os.unlink)(link)


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


def test_build_command_trailing_slash_posix_home_matches_no_slash():
    trailing = hi._build_command("/home/n/.claude/", frozen=False, platform="linux")
    bare = hi._build_command("/home/n/.claude", frozen=False, platform="linux")
    assert trailing == bare
    assert trailing["command"] == (
        'python3 "/home/n/.claude/tokitty/hook_writer.py" '
        '--sessions-dir "/home/n/.claude/tokitty/sessions"'
    )


def test_build_command_trailing_backslash_windows_home_matches_no_slash():
    trailing = hi._build_command("C:\\Users\\n\\.claude\\", frozen=False, platform="win32")
    bare = hi._build_command("C:\\Users\\n\\.claude", frozen=False, platform="win32")
    assert trailing == bare


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
    # A fake release under tmp_path with host-native names, sys.executable
    # patched to it -- so this never links to a real directory (Task 3
    # ruling: no test may link outside its own tmp_path).
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"
    try:
        result = hi.install_hooks_for_dir(str(home))
        assert result.ok
        data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
        hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
        assert hook["args"][0] == "--sessions-dir"
        assert Path(hook["command"]).name in ("tokitty-hook", "tokitty-hook.exe")
        # Registered against the stable link path, not the release-specific
        # bundled path -- this is the string Codex hashes.
        assert hook["command"] == str(state_dir / "current" / RUNNER_NAME)
        assert runner_link._is_link(str(state_dir / "current"))
    finally:
        _unlink_current_link(state_dir)


def test_install_maps_translocated_error_to_move_to_applications(tmp_path, monkeypatch):
    exe = _fake_release(tmp_path / "release" / "AppTranslocation" / "x" / "rel")
    state_dir = tmp_path / "state"
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"

    result = hi.install_hooks_for_dir(str(home))

    assert not result.ok
    assert result.message == MOVE_TO_APPLICATIONS
    assert not (home / "settings.json").exists()
    assert not (home / "tokitty" / "hook_writer.py").exists()
    assert not state_dir.exists()


def test_install_leaves_stable_registration_untouched_on_link_failure(tmp_path, monkeypatch):
    # A release with no bundled tokitty-hook: ensure_runner_link raises
    # FileNotFoundError (an OSError), which must abort the install rather
    # than fall back to writing a release-specific command -- a transient
    # failure must never rewrite a stable registration.
    bare = tmp_path / "release-bare"
    bare.mkdir(parents=True)
    exe = bare / EXE_NAME
    exe.write_text("gui", encoding="utf-8")
    state_dir = tmp_path / "state"
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"

    result = hi.install_hooks_for_dir(str(home))

    assert not result.ok
    assert not (home / "settings.json").exists()
    assert not (home / "tokitty" / "hook_writer.py").exists()


def test_install_twice_from_different_releases_is_byte_identical_and_link_moves(tmp_path, monkeypatch):
    """hooks_install reconciliation across releases (install_hooks_for_dir
    is idempotent once every event is already installed; a future
    refresh_hooks_for_dir will share this same repoint-then-register
    path). settings.json must be byte-identical after the second call,
    and the link must end at the second release."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"

    a = _fake_release(tmp_path / "rel-a")
    monkeypatch.setattr(hi.sys, "executable", str(a))
    try:
        first = hi.install_hooks_for_dir(str(home))
        assert first.ok
        settings_bytes_first = (home / "settings.json").read_bytes()

        b = _fake_release(tmp_path / "rel-b")
        monkeypatch.setattr(hi.sys, "executable", str(b))
        second = hi.install_hooks_for_dir(str(home))
        assert second.ok
        settings_bytes_second = (home / "settings.json").read_bytes()

        assert settings_bytes_first == settings_bytes_second
        assert os.path.realpath(state_dir / "current") == os.path.realpath(b.parent)
    finally:
        _unlink_current_link(state_dir)


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


def test_is_owned_hook_rejects_unbalanced_quote():
    # The whitespace fallback exists only for the historical unquoted
    # shape, which never had quotes at all. A command with a stray quote
    # is not that shape, even though stripping quotes per token happens
    # to make it line up.
    hook = {
        "type": "command",
        "command": 'python3 "/h/.claude/tokitty/hook_writer.py '
        '--sessions-dir /h/.claude/tokitty/sessions',
    }
    assert not hi._is_owned_hook(hook, "/h/.claude")


def test_is_owned_hook_rejects_trailing_slash_on_script_token():
    # A quoted command with a trailing slash on the script path is not the
    # command tokitty writes, even though the home itself normalises a
    # trailing separator away.
    hook = {
        "type": "command",
        "command": 'python3 "/h/.claude/tokitty/hook_writer.py/" '
        '--sessions-dir "/h/.claude/tokitty/sessions"',
    }
    assert not hi._is_owned_hook(hook, "/h/.claude")


# ---------------------------------------------------------------------------
# _is_owned_hook -- exec form (spec Q2a, Task 4)
# ---------------------------------------------------------------------------

def test_is_owned_hook_accepts_exec_form_posix():
    config_dir = "/home/nick/.claude"
    hook = {
        "type": "command",
        "command": "/home/nick/.config/tokitty/current/tokitty-hook",
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions"],
    }
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_accepts_exec_form_windows_mixed_slashes_and_case():
    # The command's containing directory is never checked, only the
    # basename -- and the sessions arg case-folds because the home starts
    # with a drive letter, even though its casing differs from config_dir.
    config_dir = r"C:\Users\Nick\.claude"
    hook = {
        "type": "command",
        "command": r"c:\ProgramData\Tokitty\current\Tokitty-Hook.EXE",
        "args": ["--sessions-dir", "C:/Users/nick/.claude/tokitty/sessions"],
    }
    assert hi._is_owned_hook(hook, config_dir)


def test_is_owned_hook_rejects_exec_form_other_home():
    hook = {
        "type": "command",
        "command": "/x/current/tokitty-hook",
        "args": ["--sessions-dir", "/other/.claude/tokitty/sessions"],
    }
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_rejects_exec_form_extra_args():
    hook = {
        "type": "command",
        "command": "/x/tokitty-hook",
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions", "--extra"],
    }
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_rejects_exec_form_wrong_basename():
    hook = {
        "type": "command",
        "command": "/x/not-tokitty-hook",
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions"],
    }
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_rejects_exec_form_wrong_type():
    hook = {
        "type": "prompt",
        "command": "/x/tokitty-hook",
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions"],
    }
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


def test_is_owned_hook_exec_form_never_matches_interpreter_string_branch():
    # A handler with args is checked only against the exec shape, even
    # when its command string happens to look like a python invocation.
    hook = {
        "type": "command",
        "command": "python3 /home/nick/.claude/tokitty/hook_writer.py",
        "args": ["--sessions-dir", "/home/nick/.claude/tokitty/sessions"],
    }
    assert not hi._is_owned_hook(hook, "/home/nick/.claude")


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


def test_uninstall_removes_exec_form_handler_keeps_user_handler_in_shared_entry(tmp_path):
    # Task 4: uninstall's handler-level removal already only touches owned
    # handlers (Task 3); this just proves that now covers the exec form
    # too, now that _is_owned_hook recognises it.
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    exec_handler = {
        "type": "command",
        "command": str(tmp_path / "state" / "current" / RUNNER_NAME),
        "args": ["--sessions-dir", str(config_dir) + "/tokitty/sessions"],
    }
    existing = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "some-other-tool"},
                        exec_handler,
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
# _reconcile_claude / refresh_hooks_for_dir / ensure_current (Task 4)
# ---------------------------------------------------------------------------

def test_refresh_never_adds_missing_events(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    owned = hi._build_command(str(config_dir))
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [dict(owned)]}]}}
    (config_dir / "settings.json").write_text(json.dumps(existing))

    result = hi.refresh_hooks_for_dir(str(config_dir))

    assert result.ok
    assert result.installed_events == []
    data = json.loads((config_dir / "settings.json").read_text())
    assert set(data["hooks"].keys()) == {"Stop"}


def test_refresh_on_never_installed_home_writes_nothing(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    result = hi.refresh_hooks_for_dir(str(config_dir))

    assert result.ok
    assert result.message == "nothing to refresh"
    assert not (config_dir / "settings.json").exists()
    assert not (config_dir / "tokitty").exists()


def test_refresh_identical_is_a_no_op_no_backup(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    before = (config_dir / "settings.json").read_bytes()

    result = hi.refresh_hooks_for_dir(str(config_dir))

    assert result.ok
    assert result.installed_events == []
    assert result.refreshed_events == []
    after = (config_dir / "settings.json").read_bytes()
    assert after == before
    assert list(config_dir.glob("settings.json.tokitty-backup-*")) == []


def test_refresh_leaves_equivalent_spelling_python_handler_byte_identical(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    historical = (
        f"python3 {config_dir}/tokitty/hook_writer.py "
        f"--sessions-dir {config_dir}/tokitty/sessions"
    )
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": historical}]}]}}
    (config_dir / "settings.json").write_text(json.dumps(existing))
    before = (config_dir / "settings.json").read_bytes()

    result = hi.refresh_hooks_for_dir(str(config_dir))

    assert result.ok
    assert result.refreshed_events == []
    after = (config_dir / "settings.json").read_bytes()
    assert after == before
    assert list(config_dir.glob("settings.json.tokitty-backup-*")) == []


def test_install_collapses_duplicate_owned_handlers_to_one(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    handler = hi._build_command(str(config_dir))
    existing = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [dict(handler)]},
                {"matcher": "", "hooks": [dict(handler)]},
            ]
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))

    result = hi.install_hooks_for_dir(str(config_dir))

    assert result.ok
    assert result.refreshed_events == ["Stop"]
    data = json.loads((config_dir / "settings.json").read_text())
    assert data["hooks"]["Stop"] == [{"matcher": "", "hooks": [dict(handler)]}]


def test_install_rewrites_owned_handler_in_place_keeping_timeout_and_user_handler(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    stale_handler = {
        "type": "command",
        "command": str(tmp_path / "old-release" / RUNNER_NAME),
        "args": ["--sessions-dir", f"{config_dir}/tokitty/sessions"],
        "timeout": 30,
    }
    existing = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "user-tool"},
                        stale_handler,
                    ],
                }
            ]
        }
    }
    (config_dir / "settings.json").write_text(json.dumps(existing))
    try:
        result = hi.install_hooks_for_dir(str(config_dir))
    finally:
        _unlink_current_link(state_dir)

    assert result.ok
    assert "PreToolUse" in result.refreshed_events
    data = json.loads((config_dir / "settings.json").read_text())
    entry = data["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0] == {"type": "command", "command": "user-tool"}
    rewritten = entry["hooks"][1]
    assert rewritten["timeout"] == 30
    assert rewritten["command"] == str(state_dir / "current" / RUNNER_NAME)
    assert rewritten["args"] == ["--sessions-dir", f"{config_dir}/tokitty/sessions"]


def test_refresh_rewrites_python_string_handler_to_stable_exec_form(tmp_path, monkeypatch):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    first = hi.install_hooks_for_dir(str(config_dir))
    assert first.ok
    assert "args" not in json.loads((config_dir / "settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]

    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    try:
        second = hi.refresh_hooks_for_dir(str(config_dir))
    finally:
        _unlink_current_link(state_dir)

    assert second.ok
    assert "PreToolUse" in second.refreshed_events
    data = json.loads((config_dir / "settings.json").read_text())
    hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["command"] == str(state_dir / "current" / RUNNER_NAME)
    assert hook["args"] == ["--sessions-dir", f"{config_dir}/tokitty/sessions"]


def test_refresh_rewrites_old_absolute_release_exec_path_to_stable(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    old_absolute = {
        "type": "command",
        "command": str(tmp_path / "old-release" / RUNNER_NAME),
        "args": ["--sessions-dir", f"{config_dir}/tokitty/sessions"],
    }
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [old_absolute]}]}}
    (config_dir / "settings.json").write_text(json.dumps(existing))

    try:
        result = hi.refresh_hooks_for_dir(str(config_dir))
    finally:
        _unlink_current_link(state_dir)

    assert result.ok
    assert result.refreshed_events == ["Stop"]
    data = json.loads((config_dir / "settings.json").read_text())
    hook = data["hooks"]["Stop"][0]["hooks"][0]
    assert hook["command"] == str(state_dir / "current" / RUNNER_NAME)


def test_install_skips_stale_local_entry_and_removes_main_duplicate(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    # An equivalent-spelling local handler is never "stale" by design (see
    # the byte-identical test above), so staleness here comes from local
    # owning the event with an exec-form handler while the current build
    # is non-frozen -- "desired" is the python string, a real difference.
    stale_local = {
        "type": "command",
        "command": str(tmp_path / "state" / "current" / RUNNER_NAME),
        "args": ["--sessions-dir", f"{config_dir}/tokitty/sessions"],
    }
    local = {"hooks": {"Stop": [{"matcher": "", "hooks": [stale_local]}]}}
    (config_dir / "settings.local.json").write_text(json.dumps(local))
    main_duplicate = hi._build_command(str(config_dir))
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [dict(main_duplicate)]}]}}
    (config_dir / "settings.json").write_text(json.dumps(existing))

    result = hi.install_hooks_for_dir(str(config_dir))

    assert result.ok
    assert "Stop" in result.refreshed_events
    # Exact match, not a substring check: a SubagentStop-only note would
    # also satisfy "Stop" in result.note (Task 4 review coverage gap).
    assert result.note == "a locally-owned hook differs from what Tokitty would write for: Stop"
    data = json.loads((config_dir / "settings.json").read_text())
    assert "Stop" not in data["hooks"]
    local_after = json.loads((config_dir / "settings.local.json").read_text())
    assert local_after == local


def test_uninstall_then_ensure_current_stays_uninstalled(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    hi.install_hooks_for_dir(str(config_dir))
    hi.uninstall_hooks_for_dir(str(config_dir))
    before = (config_dir / "settings.json").read_bytes()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # ensure_current reads its accounts explicitly from accounts.json now
    # (Task 4 review finding 2): it never falls back to get_config_dirs's
    # default-dir resolution, so this can no longer be driven by
    # monkeypatching get_config_dirs -- a real accounts.json is required.
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"config_dir": str(config_dir), "provider": "claude"}]})
    )

    results = hi.ensure_current(state_dir)

    assert len(results) == 1
    assert results[0].ok
    after = (config_dir / "settings.json").read_bytes()
    assert after == before


def test_refresh_across_two_releases_is_byte_identical_and_link_moves(tmp_path, monkeypatch):
    """Replaces Task 3's stand-in (test_install_twice_from_different_
    releases_is_byte_identical_and_link_moves, still above, unmodified):
    the second call here is refresh_hooks_for_dir, not another install."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"

    a = _fake_release(tmp_path / "rel-a")
    monkeypatch.setattr(hi.sys, "executable", str(a))
    try:
        first = hi.install_hooks_for_dir(str(home))
        assert first.ok
        before = (home / "settings.json").read_bytes()

        b = _fake_release(tmp_path / "rel-b")
        monkeypatch.setattr(hi.sys, "executable", str(b))
        second = hi.refresh_hooks_for_dir(str(home))
        assert second.ok

        after = (home / "settings.json").read_bytes()
        assert after == before
        assert os.path.realpath(state_dir / "current") == os.path.realpath(b.parent)
    finally:
        _unlink_current_link(state_dir)


def test_fallback_with_no_stable_hook_registers_bundled_path_with_warning(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    monkeypatch.setattr(
        runner_link, "ensure_runner_link",
        lambda *a, **k: (_ for _ in ()).throw(OSError("timed out waiting for the lock")),
    )
    home = tmp_path / "home"

    result = hi.install_hooks_for_dir(str(home))

    assert result.ok
    assert result.warning is not None
    assert "could not set up its stable hook path" in result.warning
    assert "timed out waiting for the lock" in result.warning
    data = json.loads((home / "settings.json").read_text())
    hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["command"] == hi.hook_runner_path(os.path.realpath(str(exe)), sys.platform)
    assert not runner_link._is_link(str(state_dir / "current"))


def test_fallback_with_existing_stable_hook_leaves_it_and_warns(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"

    try:
        first = hi.install_hooks_for_dir(str(home))
        assert first.ok
        before = (home / "settings.json").read_bytes()

        monkeypatch.setattr(
            runner_link, "ensure_runner_link",
            lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
        )
        second = hi.refresh_hooks_for_dir(str(home))
    finally:
        _unlink_current_link(state_dir)

    assert second.ok
    assert second.warning is not None
    assert "boom" in second.warning
    after = (home / "settings.json").read_bytes()
    assert after == before


def test_fallback_new_event_never_uses_a_broken_stable_path(tmp_path, monkeypatch):
    """Codex review finding: the fallback decision is per event, not once
    per home -- an existing owned handler for one event must not make a
    *new* event (added in this same call) trust a link that isn't
    working right now."""
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"

    try:
        first = hi.install_hooks_for_dir(str(home))
        assert first.ok

        monkeypatch.setattr(
            runner_link, "ensure_runner_link",
            lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
        )
        # Remove one event's entry so install_hooks_for_dir has to add a
        # brand new handler for it while every other event already has
        # the (now-unreachable) stable path registered.
        data = json.loads((home / "settings.json").read_text())
        del data["hooks"]["SessionEnd"]
        (home / "settings.json").write_text(json.dumps(data))

        second = hi.install_hooks_for_dir(str(home))
    finally:
        _unlink_current_link(state_dir)

    assert second.ok
    assert "SessionEnd" in second.installed_events
    data = json.loads((home / "settings.json").read_text())
    new_hook = data["hooks"]["SessionEnd"][0]["hooks"][0]
    assert new_hook["command"] == hi.hook_runner_path(os.path.realpath(str(exe)), sys.platform)
    # The other events already had the stable path and are left alone.
    other_hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
    assert other_hook["command"] == str(state_dir / "current" / RUNNER_NAME)


def _write_accounts_json(state_dir: Path, config_dir, provider="claude") -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"config_dir": str(config_dir), "provider": provider}]})
    )


def test_ensure_current_turns_oserror_into_failed_result(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    state_dir = tmp_path / "state"
    _write_accounts_json(state_dir, config_dir)

    def raising_refresh(cd, provider):
        raise OSError("disk on fire")

    results = hi.ensure_current(state_dir, refresh_fn=raising_refresh)

    assert len(results) == 1
    assert results[0].ok is False
    assert "disk on fire" in results[0].message


def test_ensure_current_turns_generic_exception_into_failed_result(tmp_path):
    """Task 4 review finding 3: a non-OSError exception (e.g. the
    AttributeError a malformed settings.json used to raise) must not
    abort the rest of ensure_current's accounts, only this one's result."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    state_dir = tmp_path / "state"
    _write_accounts_json(state_dir, config_dir)

    def raising_refresh(cd, provider):
        raise ValueError("not an OSError at all")

    results = hi.ensure_current(state_dir, refresh_fn=raising_refresh)

    assert len(results) == 1
    assert results[0].ok is False
    assert "not an OSError at all" in results[0].message


def test_ensure_current_looks_up_default_refresh_fn_at_call_time(tmp_path, monkeypatch):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    state_dir = tmp_path / "state"
    _write_accounts_json(state_dir, config_dir)

    calls = []

    def fake_refresh(cd, provider):
        calls.append((cd, provider))
        return hi.ConfigDirResult(cd, True, "spied")

    monkeypatch.setattr(hi, "refresh_hooks_for_dir", fake_refresh)

    results = hi.ensure_current(state_dir)

    assert calls == [(str(config_dir), "claude")]
    assert results[0].message == "spied"


def test_ensure_current_skips_default_dir_and_wsl_probe_without_accounts_json(tmp_path, monkeypatch):
    """Task 4 review finding 2: with no accounts.json, ensure_current must
    do nothing at all -- specifically, it must never call
    _default_config_dir() (whose non-Windows branch is harmless, but
    whose Windows branch shells into every WSL distro on every launch)."""

    def boom():
        raise AssertionError("_default_config_dir must not be called by ensure_current")

    monkeypatch.setattr(hi, "_default_config_dir", boom)

    results = hi.ensure_current(tmp_path / "state")

    assert results == []


def test_ensure_current_skips_when_accounts_json_lists_no_hook_accounts(tmp_path, monkeypatch):
    def boom():
        raise AssertionError("_default_config_dir must not be called by ensure_current")

    monkeypatch.setattr(hi, "_default_config_dir", boom)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text(json.dumps({"accounts": []}))

    results = hi.ensure_current(state_dir)

    assert results == []


# ---------------------------------------------------------------------------
# Task 4 review fix round 1
# ---------------------------------------------------------------------------

def test_refresh_leaves_exec_form_handler_alone_when_not_frozen(tmp_path):
    """Finding 1: a source (non-frozen) launch's own startup refresh must
    never demote a frozen install's exec-form handler back to the
    interpreter-string shape -- doing so would flip-flop the registered
    command (and the Codex hash) with every frozen launch's own refresh,
    which converts it back up."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    exec_handler = {
        "type": "command",
        "command": str(tmp_path / "state" / "current" / RUNNER_NAME),
        "args": ["--sessions-dir", f"{config_dir}/tokitty/sessions"],
    }
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [exec_handler]}]}}
    (config_dir / "settings.json").write_text(json.dumps(existing))
    before = (config_dir / "settings.json").read_bytes()

    result = hi.refresh_hooks_for_dir(str(config_dir))

    assert result.ok
    assert result.refreshed_events == []
    after = (config_dir / "settings.json").read_bytes()
    assert after == before
    assert list(config_dir.glob("settings.json.tokitty-backup-*")) == []


def test_install_still_converts_exec_form_to_python_when_not_frozen(tmp_path):
    """Finding 1's other half: an explicit install (add_missing=True) keeps
    today's behaviour and still performs the demotion -- only the
    automatic startup refresh gained the new guard."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    exec_handler = {
        "type": "command",
        "command": str(tmp_path / "state" / "current" / RUNNER_NAME),
        "args": ["--sessions-dir", f"{config_dir}/tokitty/sessions"],
    }
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [exec_handler]}]}}
    (config_dir / "settings.json").write_text(json.dumps(existing))

    result = hi.install_hooks_for_dir(str(config_dir))

    assert result.ok
    assert "Stop" in result.refreshed_events
    data = json.loads((config_dir / "settings.json").read_text())
    hook = data["hooks"]["Stop"][0]["hooks"][0]
    assert "args" not in hook


def test_install_aborts_cleanly_on_non_dict_settings_json_root(tmp_path):
    """Finding 3: settings.json parses fine but its root isn't an object."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps([]))
    original = (config_dir / "settings.json").read_text()

    result = hi.install_hooks_for_dir(str(config_dir))

    assert not result.ok
    assert (config_dir / "settings.json").read_text() == original
    assert not (config_dir / "tokitty").exists()


def test_install_aborts_cleanly_on_null_hooks_key(tmp_path):
    """Finding 3: {"hooks": null} used to reach a bare AttributeError."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps({"hooks": None}))
    original = (config_dir / "settings.json").read_text()

    result = hi.install_hooks_for_dir(str(config_dir))

    assert not result.ok
    assert (config_dir / "settings.json").read_text() == original


def test_refresh_also_aborts_on_non_dict_settings_json_root(tmp_path):
    """Finding 3 is a shape problem, not a parse problem -- finding 9's
    quiet-skip leniency for refresh is for unparseable JSON only, so this
    must still abort loudly during a refresh too."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps([]))

    result = hi.refresh_hooks_for_dir(str(config_dir))

    assert not result.ok


def test_ensure_current_survives_a_shape_error_via_the_real_reconcile(tmp_path):
    """Finding 3, end to end: ensure_current must not let one account's
    AttributeError-turned-abort take down the whole pass."""
    good = tmp_path / "good" / ".claude"
    good.mkdir(parents=True)
    bad = tmp_path / "bad" / ".claude"
    bad.mkdir(parents=True)
    (bad / "settings.json").write_text(json.dumps({"hooks": None}))
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {"config_dir": str(good), "provider": "claude"},
                    {"config_dir": str(bad), "provider": "claude"},
                ]
            }
        )
    )
    hi.install_hooks_for_dir(str(good))

    results = hi.ensure_current(state_dir)

    by_dir = {r.config_dir: r for r in results}
    assert by_dir[str(good)].ok
    assert not by_dir[str(bad)].ok


def test_handler_needs_rewrite_normalizes_drive_letter_case_in_command():
    """Finding 5, drive-letter case: an equivalently-spelled stable command
    (only its drive letter and directory casing differ) is not a rewrite."""
    old = {
        "type": "command",
        "command": r"c:\users\nick\appdata\local\tokitty\current\tokitty-hook.exe",
        "args": ["--sessions-dir", r"C:\Users\Nick\.claude/tokitty/sessions"],
    }
    desired = {
        "type": "command",
        "command": r"C:\Users\Nick\AppData\Local\Tokitty\current\tokitty-hook.exe",
        "args": ["--sessions-dir", r"C:\Users\Nick\.claude/tokitty/sessions"],
    }
    assert not hi._handler_needs_rewrite(old, desired)


def test_refresh_leaves_doubled_slash_stable_spelling_byte_identical(tmp_path, monkeypatch):
    """Finding 5, doubled slash, healthy link: the handler already spells
    the (working) stable path with a doubled slash -- an equivalent
    spelling, so _handler_needs_rewrite's normalized command compare must
    not treat it as a rewrite."""
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"
    home.mkdir()
    doubled_stable = str(state_dir / "current") + "//" + RUNNER_NAME
    handler = {
        "type": "command",
        "command": doubled_stable,
        "args": ["--sessions-dir", f"{home}/tokitty/sessions"],
    }
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [handler]}]}}
    (home / "settings.json").write_text(json.dumps(existing))
    before = (home / "settings.json").read_bytes()

    try:
        result = hi.refresh_hooks_for_dir(str(home))
    finally:
        _unlink_current_link(state_dir)

    assert result.ok
    assert result.refreshed_events == []
    after = (home / "settings.json").read_bytes()
    assert after == before


def test_fallback_recognizes_doubled_slash_stable_spelling_as_already_stable(tmp_path, monkeypatch):
    """Finding 5, doubled slash, failing link: the existing handler's
    command is a differently-spelled but equivalent stable path --
    desired_for's "already the stable path" check must recognise it via
    _normalize_token_path, not exact string equality, or it gets rewritten
    to a release path for no real reason."""
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"
    home.mkdir()
    doubled_stable = str(state_dir / "current") + "//" + RUNNER_NAME
    handler = {
        "type": "command",
        "command": doubled_stable,
        "args": ["--sessions-dir", f"{home}/tokitty/sessions"],
    }
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [handler]}]}}
    (home / "settings.json").write_text(json.dumps(existing))
    before = (home / "settings.json").read_bytes()

    monkeypatch.setattr(
        runner_link, "ensure_runner_link",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )

    result = hi.refresh_hooks_for_dir(str(home))

    assert result.ok
    after = (home / "settings.json").read_bytes()
    assert after == before


def test_fallback_duplicate_handlers_prefers_the_stable_one_as_primary(tmp_path, monkeypatch):
    """Finding 6: with the link failing and two owned handlers for one
    event -- an old release path and the current stable path -- the
    stable one must be kept (and the old one dropped), never the other
    way around."""
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"
    home.mkdir()
    stable_path = str(state_dir / "current" / RUNNER_NAME)
    old_release_handler = {
        "type": "command",
        "command": str(tmp_path / "old-release" / RUNNER_NAME),
        "args": ["--sessions-dir", f"{home}/tokitty/sessions"],
    }
    stable_handler = {
        "type": "command",
        "command": stable_path,
        "args": ["--sessions-dir", f"{home}/tokitty/sessions"],
    }
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [old_release_handler, stable_handler]}]}}
    (home / "settings.json").write_text(json.dumps(existing))

    monkeypatch.setattr(
        runner_link, "ensure_runner_link",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )

    result = hi.refresh_hooks_for_dir(str(home))

    assert result.ok
    data = json.loads((home / "settings.json").read_text())
    hooks = data["hooks"]["Stop"][0]["hooks"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == stable_path


def test_fallback_never_prefers_this_releases_own_bundled_duplicate_over_stable(tmp_path, monkeypatch):
    """Round 2 finding 1: choose_primary used to return the first owned
    handler that already matched what fallback mode would write today,
    before it had looked at every position for a stable-path one. A
    handler already sitting at *this release's own* bundled path needs no
    rewrite, so when it is listed before the stable-path handler for the
    same event, the old per-position early return picked it as primary and
    deleted the stable duplicate -- backwards from the rule that a
    stable-path hook is never dropped in favour of a release path. Listing
    the bundled handler first is what exposes the bug."""
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"
    home.mkdir()

    bundled_path = hi.hook_runner_path(os.path.realpath(str(exe)), sys.platform)
    stable_path = str(state_dir / "current" / RUNNER_NAME)
    sessions_args = ["--sessions-dir", f"{home}/tokitty/sessions"]
    bundled_handler = {"type": "command", "command": bundled_path, "args": list(sessions_args)}
    stable_handler = {"type": "command", "command": stable_path, "args": list(sessions_args)}
    existing = {"hooks": {"Stop": [{"matcher": "", "hooks": [bundled_handler, stable_handler]}]}}
    (home / "settings.json").write_text(json.dumps(existing))

    monkeypatch.setattr(
        runner_link, "ensure_runner_link",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )

    result = hi.refresh_hooks_for_dir(str(home))

    assert result.ok
    data = json.loads((home / "settings.json").read_text())
    hooks = data["hooks"]["Stop"][0]["hooks"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == stable_path


def test_fallback_missing_bundled_runner_message_names_the_reason(tmp_path, monkeypatch):
    """Finding 8: a missing bundled tokitty-hook used to surface only a
    bare path as the whole result message."""
    bare = tmp_path / "release-bare"
    bare.mkdir(parents=True)
    exe = bare / EXE_NAME
    exe.write_text("gui", encoding="utf-8")
    state_dir = tmp_path / "state"
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / "home"

    result = hi.install_hooks_for_dir(str(home))

    assert not result.ok
    assert result.message == "this copy of Tokitty has no tokitty-hook next to it"


def test_refresh_quietly_skips_a_settings_json_that_wont_parse(tmp_path):
    """Finding 9: a home Tokitty never touched whose settings.json just
    happens not to parse must not warn on every launch."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{not valid json")

    result = hi.refresh_hooks_for_dir(str(config_dir))

    assert result.ok
    assert result.warning is None
    assert result.message.startswith("skipped, could not parse")


def test_install_still_aborts_loudly_on_the_same_unparseable_settings_json(tmp_path):
    """Finding 9's contrast: an explicit install still fails loudly."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{not valid json")

    result = hi.install_hooks_for_dir(str(config_dir))

    assert not result.ok
    assert result.message.startswith("aborted, could not parse")


def test_fallback_real_directory_at_current_registers_bundled_path_with_warning(tmp_path, monkeypatch):
    """Test gap named in the review: no reconcile-level test exercised the
    real-directory-at-current outcome (runner_link's own note path, not
    an exception) through _reconcile_claude."""
    state_dir = tmp_path / "state"
    exe = _fake_release(tmp_path / "release")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(exe))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    (state_dir / "current").mkdir(parents=True)
    (state_dir / "current" / "keep.txt").write_text("mine", encoding="utf-8")
    home = tmp_path / "home"

    result = hi.install_hooks_for_dir(str(home))

    assert result.ok
    assert result.warning is not None
    data = json.loads((home / "settings.json").read_text())
    hook = data["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["command"] == hi.hook_runner_path(os.path.realpath(str(exe)), sys.platform)
    assert (state_dir / "current" / "keep.txt").read_text(encoding="utf-8") == "mine"


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


def test_retry_clears_a_legacy_pending_install_with_no_claude_evidence(tmp_path):
    # A legacy record (no provider) whose dir matches no account and has
    # neither Codex's rollout dirs nor any Claude marker must not be
    # replayed as an install: that would create a Claude settings.json in
    # a directory nothing here can identify. Fail closed instead.
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    save_pending_hook_op(tmp_path, "install", str(orphan))
    assert retry_pending_hook_op(tmp_path, install_fn=_forbidden, uninstall_fn=_forbidden) is None
    assert load_pending_hook_op(tmp_path) is None
    assert list(orphan.iterdir()) == []


def test_retry_replays_a_legacy_pending_install_with_claude_settings_json(tmp_path):
    # The other side of the fix above: affirmative Claude evidence (here,
    # an existing settings.json) still earns the replay.
    claude = tmp_path / "orphan"
    claude.mkdir()
    (claude / "settings.json").write_text("{}", encoding="utf-8")
    save_pending_hook_op(tmp_path, "install", str(claude))
    calls = []

    def fake_install(config_dir, provider):
        calls.append((config_dir, provider))
        return ConfigDirResult(config_dir, True, "installed")

    retry_pending_hook_op(tmp_path, install_fn=fake_install, uninstall_fn=_forbidden)
    assert calls == [(str(claude), "claude")]


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


def test_install_hooks_prints_note_to_stdout_for_ok_result(monkeypatch, tmp_path, capsys):
    """Replaces the brittle '"; " in message' detection with a dedicated
    ConfigDirResult.note field the CLI prints directly."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    def fake_install(cd, provider):
        return ConfigDirResult(cd, True, "installed and refreshed", note="a stale local entry for: Stop")

    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(config_dir), "claude")])
    monkeypatch.setattr(hi, "install_hooks_for_dir", fake_install)
    rc = hi.install_hooks()
    assert rc == 0
    out = capsys.readouterr().out
    assert f"{config_dir}: a stale local entry for: Stop" in out


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
