import pytest

from tokitty.credentials import AmbiguousCredentialsError, CredentialsError
from tokitty.wsl_probe import find_wsl_credentials, list_wsl_distros, read_wsl_credentials


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
        if cmd[:4] == ["wsl.exe", "-d", "Ubuntu", "--"]:
            return FakeCompletedProcess(stdout=b"/home/cptsmidge/.claude/.credentials.json\n")
        raise AssertionError(f"unexpected command: {cmd}")

    distro, path = find_wsl_credentials(run=fake_run)

    assert distro == "Ubuntu"
    assert path == "/home/cptsmidge/.claude/.credentials.json"


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
        assert cmd == ["wsl.exe", "-d", "Ubuntu", "--", "cat", "/home/cptsmidge/.claude/.credentials.json"]
        return FakeCompletedProcess(stdout=b'{"claudeAiOauth": {}}')

    contents = read_wsl_credentials("Ubuntu", "/home/cptsmidge/.claude/.credentials.json", run=fake_run)

    assert contents == '{"claudeAiOauth": {}}'
