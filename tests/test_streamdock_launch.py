import pytest

from tokitty.accounts import Account
from tokitty.streamdock.launch import build_command, find_preset, launch

PROFILE = r"C:\Users\u"
PERSONAL = Account("personal", r"\\wsl.localhost\Ubuntu\home\u\.claude")
WORK = Account("work", r"\\wsl.localhost\Ubuntu\home\u\.claude-work")
CODEX = Account("codex", r"C:\Users\u\.codex", provider="codex")
NATIVE = Account("native", r"C:\Users\u\.claude")
NATIVE_OTHER = Account("other", r"C:\Users\u\.claude-alt")


def wsl(**kw):
    p = {"name": "t", "account_index": 0, "env": "wsl", "distro": "Ubuntu", "cwd": "/home/u/repo"}
    p.update(kw)
    return p


def native(**kw):
    p = {"name": "n", "account_index": 0, "env": "native", "cwd": r"C:\src"}
    p.update(kw)
    return p


HEAD = ["wt.exe", "-w", "0", "new-tab", "wsl.exe", "-d", "Ubuntu", "--cd", "/home/u/repo", "--"]


def test_wsl_default_account_sets_nothing():
    assert build_command(wsl(), PERSONAL) == HEAD + ["bash", "-lic", "claude"]


def test_wsl_root_default():
    acct = Account("r", r"\\wsl.localhost\Ubuntu\root\.claude")
    assert build_command(wsl(), acct) == HEAD + ["bash", "-lic", "claude"]


def test_wsl_non_default_sets_variable():
    assert build_command(wsl(), WORK) == HEAD + [
        "env", "CLAUDE_CONFIG_DIR=/home/u/.claude-work", "bash", "-lic", "claude"]


def test_wsl_dollar_form_and_distro_case():
    acct = Account("p", r"\\wsl$\ubuntu\home\u\.claude")
    assert build_command(wsl(), acct) == HEAD + ["bash", "-lic", "claude"]


def test_wsl_distro_mismatch_raises():
    with pytest.raises(ValueError):
        build_command(wsl(distro="Debian"), PERSONAL)


def test_wsl_preset_with_native_account_raises():
    with pytest.raises(ValueError):
        build_command(wsl(), NATIVE)


def test_native_default_account():
    assert build_command(native(), NATIVE, userprofile=PROFILE) == [
        "wt.exe", "-w", "0", "new-tab", "-d", r"C:\src", "claude"]


def test_native_default_compares_case_and_slashes():
    acct = Account("n", "c:/users/U/.claude/")
    assert build_command(native(), acct, userprofile=PROFILE)[-1] == "claude"


def test_native_non_default_account():
    assert build_command(native(), NATIVE_OTHER, userprofile=PROFILE) == [
        "wt.exe", "-w", "0", "new-tab", "-d", r"C:\src", "cmd.exe", "/k",
        r'set "CLAUDE_CONFIG_DIR=C:\Users\u\.claude-alt" && claude']


def test_native_preset_with_wsl_account_raises():
    with pytest.raises(ValueError):
        build_command(native(), PERSONAL, userprofile=PROFILE)


def test_codex_account_raises():
    with pytest.raises(ValueError):
        build_command(native(), CODEX, userprofile=PROFILE)


@pytest.mark.parametrize("bad", [";", '"'])
def test_forbidden_characters_rejected(bad):
    with pytest.raises(ValueError):
        build_command(wsl(cwd=f"/a{bad}b"), PERSONAL)
    with pytest.raises(ValueError):
        build_command(wsl(distro=f"U{bad}"), PERSONAL)
    with pytest.raises(ValueError):
        build_command(native(cwd=f"C:\\a{bad}b"), NATIVE, userprofile=PROFILE)
    with pytest.raises(ValueError):
        build_command(wsl(), Account("w", rf"\\wsl.localhost\Ubuntu\home\u\a{bad}b"))
    with pytest.raises(ValueError):
        build_command(native(), Account("n", rf"C:\a{bad}b"), userprofile=PROFILE)


def test_launch_passes_list_without_shell():
    calls = []
    launch(wsl(), PERSONAL, popen_fn=lambda *a, **k: calls.append((a, k)))
    (args, kwargs), = calls
    assert args == (HEAD + ["bash", "-lic", "claude"],)
    assert "shell" not in kwargs
    assert kwargs["close_fds"] is True


def test_launch_does_not_swallow_validation_errors():
    with pytest.raises(ValueError):
        launch(wsl(distro="Debian"), PERSONAL, popen_fn=lambda *a, **k: pytest.fail("started"))


def test_find_preset():
    a, b = wsl(name="a"), wsl(name="b")
    assert find_preset([a, b], "b") is b
    assert find_preset([a, b], "c") is None
    assert find_preset([], "a") is None
