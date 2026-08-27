import subprocess

import pytest

from tokitty.credentials import AmbiguousCredentialsError, CredentialsError
from tokitty.wsl_probe import (
    find_all_wsl_credentials,
    find_wsl_credentials,
    list_running_distros,
    list_wsl_distros,
    read_wsl_credentials,
    wsl_config_dir_from_credentials,
    wsl_sessions_dir_from_credentials,
    _CHECK_SCRIPT,
)


class FakeCompletedProcess:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_list_wsl_distros_parses_utf16_output():
    def fake_run(cmd, **kwargs):
        assert cmd == ["wsl.exe", "-l", "-q"]
        return FakeCompletedProcess(stdout="Ubuntu\r\ndocker-desktop\r\n".encode("utf-16-le"))

    distros = list_wsl_distros(run=fake_run)

    assert distros == ["Ubuntu", "docker-desktop"]


def test_find_wsl_credentials_returns_single_match():
    def fake_run(cmd, **kwargs):
        if cmd == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))
        if cmd[:4] == ["wsl.exe", "-d", "Ubuntu", "--exec"]:
            return FakeCompletedProcess(stdout=b"/home/u/.claude/.credentials.json\n")
        raise AssertionError(f"unexpected command: {cmd}")

    distro, path = find_wsl_credentials(run=fake_run)

    assert distro == "Ubuntu"
    assert path == "/home/u/.claude/.credentials.json"


def test_find_wsl_credentials_raises_when_multiple_distros_match():
    def fake_run(cmd, **kwargs):
        if cmd == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\r\nDebian\r\n".encode("utf-16-le"))
        return FakeCompletedProcess(stdout=b"/home/someone/.claude/.credentials.json\n")

    with pytest.raises(AmbiguousCredentialsError):
        find_wsl_credentials(run=fake_run)


def test_find_wsl_credentials_raises_when_none_found():
    def fake_run(cmd, **kwargs):
        if cmd == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))
        return FakeCompletedProcess(stdout=b"")

    with pytest.raises(CredentialsError):
        find_wsl_credentials(run=fake_run)


def test_read_wsl_credentials_returns_file_contents():
    def fake_run(cmd, **kwargs):
        assert cmd == ["wsl.exe", "-d", "Ubuntu", "--", "cat", "/home/u/.claude/.credentials.json"]
        return FakeCompletedProcess(stdout=b'{"claudeAiOauth": {}}')

    contents = read_wsl_credentials("Ubuntu", "/home/u/.claude/.credentials.json", run=fake_run)

    assert contents == '{"claudeAiOauth": {}}'


# Every wsl.exe invocation must suppress its console window -- otherwise
# every poll flashes a visible terminal window on Windows (pythonw.exe has
# no console of its own, but wsl.exe is a console app that opens one by
# default when spawned without CREATE_NO_WINDOW).
EXPECTED_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_list_wsl_distros_suppresses_console_window():
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))

    list_wsl_distros(run=fake_run)

    assert captured.get("creationflags") == EXPECTED_CREATIONFLAGS


def test_credentials_paths_in_distro_suppresses_console_window():
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))
        captured.update(kwargs)
        return FakeCompletedProcess(stdout=b"/home/u/.claude/.credentials.json\n")

    find_wsl_credentials(run=fake_run)

    assert captured.get("creationflags") == EXPECTED_CREATIONFLAGS


def test_read_wsl_credentials_suppresses_console_window():
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return FakeCompletedProcess(stdout=b'{"claudeAiOauth": {}}')

    read_wsl_credentials("Ubuntu", "/home/u/.claude/.credentials.json", run=fake_run)

    assert captured.get("creationflags") == EXPECTED_CREATIONFLAGS


def test_list_running_distros_parses_utf16_output():
    def fake_run(cmd, **kwargs):
        assert cmd == ["wsl.exe", "--list", "--running", "--quiet"]
        return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))

    distros = list_running_distros(run=fake_run)

    assert distros == ["Ubuntu"]


def test_list_running_distros_returns_empty_when_none_running():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout="".encode("utf-16-le"))

    assert list_running_distros(run=fake_run) == []


def test_list_running_distros_returns_empty_on_error_instead_of_raising():
    def fake_run(cmd, **kwargs):
        raise OSError("wsl.exe not found")

    assert list_running_distros(run=fake_run) == []


def test_list_running_distros_suppresses_console_window():
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return FakeCompletedProcess(stdout="Ubuntu\r\n".encode("utf-16-le"))

    list_running_distros(run=fake_run)

    assert captured.get("creationflags") == EXPECTED_CREATIONFLAGS


def test_wsl_config_dir_from_credentials_dot_claude():
    result = wsl_config_dir_from_credentials("Ubuntu", "/home/nick/.claude/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude"


def test_wsl_config_dir_from_credentials_dot_claude_work():
    result = wsl_config_dir_from_credentials("Ubuntu", "/home/nick/.claude-work/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude-work"


def test_wsl_sessions_dir_from_credentials_dot_claude():
    result = wsl_sessions_dir_from_credentials("Ubuntu", "/home/nick/.claude/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude\\tokitty\\sessions"


def test_wsl_sessions_dir_from_credentials_dot_claude_work():
    result = wsl_sessions_dir_from_credentials("Ubuntu", "/home/nick/.claude-work/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude-work\\tokitty\\sessions"


def test_wsl_sessions_dir_from_credentials_derives_username_from_path():
    sessions_dir = wsl_sessions_dir_from_credentials("Ubuntu", "/home/u/.claude/.credentials.json")

    assert sessions_dir == "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude\\tokitty\\sessions"


def test_wsl_sessions_dir_from_credentials_handles_other_usernames():
    sessions_dir = wsl_sessions_dir_from_credentials("Debian", "/home/someone-else/.claude/.credentials.json")

    assert sessions_dir == "\\\\wsl.localhost\\Debian\\home\\someone-else\\.claude\\tokitty\\sessions"


def test_wsl_config_dir_from_credentials_derives_username_from_path():
    config_dir = wsl_config_dir_from_credentials("Ubuntu", "/home/u/.claude/.credentials.json")

    assert config_dir == "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude"


def test_wsl_config_dir_from_credentials_handles_other_usernames():
    config_dir = wsl_config_dir_from_credentials("Debian", "/home/someone-else/.claude/.credentials.json")

    assert config_dir == "\\\\wsl.localhost\\Debian\\home\\someone-else\\.claude"


def test_find_all_wsl_credentials_empty():
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))
        return FakeCompletedProcess(stdout=b"")

    assert find_all_wsl_credentials(run=fake_run) == []


def test_find_all_wsl_credentials_returns_every_match_across_distros():
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\nDebian\n".encode("utf-16-le"))
        distro = cmd[2]
        if distro == "Ubuntu":
            out = "/home/nick/.claude/.credentials.json\n/home/nick/.claude-work/.credentials.json\n"
        else:
            out = "/home/dana/.claude/.credentials.json\n"
        return FakeCompletedProcess(stdout=out.encode("utf-8"))

    matches = find_all_wsl_credentials(run=fake_run)
    assert set(matches) == {
        ("Ubuntu", "/home/nick/.claude/.credentials.json"),
        ("Ubuntu", "/home/nick/.claude-work/.credentials.json"),
        ("Debian", "/home/dana/.claude/.credentials.json"),
    }


def test_check_script_globs_dot_claude_star():
    assert "/home/*/.claude*/.credentials.json" in _CHECK_SCRIPT
