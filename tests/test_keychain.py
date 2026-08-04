import subprocess

import pytest

from tokitty.credentials import CredentialsError, KeychainAccessError
from tokitty.keychain import (
    KEYCHAIN_SERVICE,
    keychain_item_exists,
    read_keychain_secret,
)


class FakeCompletedProcess:
    def __init__(self, stdout: bytes = b"", returncode: int = 0, stderr: bytes = b""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_read_keychain_secret_returns_stdout():
    def fake_run(cmd, **kwargs):
        assert cmd == [
            "security", "find-generic-password", "-s", "Claude Code-credentials", "-w",
        ]
        return FakeCompletedProcess(stdout=b'{"claudeAiOauth": {}}\n')

    assert read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run) == '{"claudeAiOauth": {}}'


def test_read_keychain_secret_includes_account_when_given():
    def fake_run(cmd, **kwargs):
        assert cmd == [
            "security", "find-generic-password",
            "-s", "Claude Code-credentials", "-a", "someuser", "-w",
        ]
        return FakeCompletedProcess(stdout=b"{}")

    read_keychain_secret(KEYCHAIN_SERVICE, account="someuser", run=fake_run)


def test_read_keychain_secret_raises_credentials_error_on_not_found():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=44, stderr=b"could not be found")

    with pytest.raises(CredentialsError) as excinfo:
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)
    # Exit 44 is transient-if-it-happens-during-a-load, so it must NOT be the
    # sticky subclass -- see CredentialLoader in Task 4.
    assert not isinstance(excinfo.value, KeychainAccessError)


def test_read_keychain_secret_raises_keychain_access_error_on_denial():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=128, stderr=b"User canceled")

    with pytest.raises(KeychainAccessError):
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)


def test_read_keychain_secret_wraps_os_error():
    def fake_run(cmd, **kwargs):
        raise OSError("no such binary")

    with pytest.raises(CredentialsError):
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)


def test_read_keychain_secret_wraps_timeout():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="security", timeout=10)

    with pytest.raises(CredentialsError):
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)


# The -w flag is what asks for the *secret*, which is what raises a macOS
# dialog. Resolution runs on every poll (~120s), so the existence probe must
# never include it. This is a correctness assertion, not a style one.
def test_keychain_item_exists_never_requests_the_secret():
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeCompletedProcess(stdout=b'svce="Claude Code-credentials"')

    keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run)

    assert "-w" not in seen["cmd"]
    assert seen["cmd"] == [
        "security", "find-generic-password", "-s", "Claude Code-credentials",
    ]


def test_keychain_item_exists_true_on_success():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=0)

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is True


def test_keychain_item_exists_false_when_not_found():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=44)

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is False


# Existence is not in question on an unexpected exit code -- only access to the
# secret is. Returning False here would route the user to the "can't find
# credentials" hint, which is the wrong remedy.
def test_keychain_item_exists_true_on_unexpected_exit_code():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=128)

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is True


def test_keychain_item_exists_false_when_security_is_missing():
    def fake_run(cmd, **kwargs):
        raise OSError("no such binary")

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is False


def test_keychain_calls_pass_a_timeout():
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return FakeCompletedProcess(stdout=b"{}")

    read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)

    assert seen["timeout"] == 10
    assert seen["capture_output"] is True
    assert seen["check"] is False
    # creationflags is Windows-only and raises ValueError on POSIX.
    assert "creationflags" not in seen
