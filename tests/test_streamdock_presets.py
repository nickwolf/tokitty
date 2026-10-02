from tokitty.accounts import Account
from tokitty.streamdock.presets import check_preset, claude_accounts, preset_for

NATIVE = Account("native-1", "C:\\Users\\u\\.claude")
WSL = Account("wsl-1", "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude")
WSL_OLD = Account("wsl-2", "\\\\wsl$\\Debian\\home\\u\\.claude")
CODEX = Account("cx", "C:\\Users\\u\\.codex", provider="codex")
ALL = [NATIVE, WSL, WSL_OLD, CODEX]


def test_preset_for_native():
    assert preset_for(" web ", NATIVE, " C:\\src ", ALL) == {
        "name": "web", "account": "native-1", "account_index": 0, "env": "native", "cwd": "C:\\src"}


def test_preset_for_wsl_localhost():
    assert preset_for("w", WSL, "~/repo", ALL) == {
        "name": "w", "account": "wsl-1", "account_index": 1, "env": "wsl", "distro": "Ubuntu", "cwd": "~/repo"}


def test_preset_for_wsl_dollar_form():
    p = preset_for("w", WSL_OLD, "/mnt/c/Tools", ALL)
    assert (p["env"], p["distro"], p["account_index"]) == ("wsl", "Debian", 2)


def test_claude_accounts_skips_codex_and_none():
    assert claude_accounts([NATIVE, None, CODEX, WSL]) == [(0, NATIVE), (3, WSL)]


def test_check_ok():
    assert check_preset(preset_for("a", NATIVE, "C:\\src", ALL), NATIVE) is None
    assert check_preset(preset_for("a", WSL, "~/repo", ALL), WSL) is None


def test_check_blank_and_long_name():
    assert "Name" in check_preset(preset_for("  ", NATIVE, "C:\\src", ALL), NATIVE)
    assert "Name" in check_preset(preset_for("x" * 41, NATIVE, "C:\\src", ALL), NATIVE)


def test_check_blank_folder():
    assert check_preset(preset_for("a", NATIVE, "  ", ALL), NATIVE) == "Folder is required"


def test_check_duplicate_name():
    other = preset_for("a", NATIVE, "C:\\x", ALL)
    assert "already" in check_preset(preset_for("a", NATIVE, "C:\\src", ALL), NATIVE, [other])


def test_check_forbidden_character():
    assert "semicolon" in check_preset(preset_for("a", NATIVE, "C:\\a;b", ALL), NATIVE)


def test_check_env_mismatch():
    wsl_preset = preset_for("a", WSL, "~/r", ALL)
    assert check_preset(wsl_preset, NATIVE) == "Account is not in WSL"
    assert check_preset(wsl_preset, WSL_OLD) == "Account is in a different WSL distro"
    assert check_preset(preset_for("a", NATIVE, "C:\\s", ALL), WSL) == "Account is in WSL, preset is native"


def test_check_codex_account():
    assert check_preset(preset_for("a", NATIVE, "C:\\s", ALL), CODEX) == "Account is not a Claude account"


def test_account_label():
    from tokitty.streamdock.presets import account_label

    assert account_label(1, WSL) == f"Cat 2 ({WSL.config_dir})"
    assert account_label(1, WSL, "Work") == f"Work ({WSL.config_dir})"


def test_save_presets_keeps_other_settings(tmp_path):
    from tokitty.settings import Settings, load_settings, save_settings
    from tokitty.streamdock.presets import save_presets

    save_settings(tmp_path, Settings(tray_enabled=False, streamdock_port=4000, streamdock_token="a" * 32))
    preset = {"name": "p", "account": "work", "account_index": 1, "env": "wsl", "distro": "Ubuntu", "cwd": "/w"}
    assert save_presets(tmp_path, [preset]) == [preset]
    loaded = load_settings(tmp_path)
    assert loaded.streamdock_presets == [preset]
    assert (loaded.tray_enabled, loaded.streamdock_port, loaded.streamdock_token) == (False, 4000, "a" * 32)


def test_save_hidden_accounts_keeps_other_settings(tmp_path):
    from tokitty.settings import Settings, load_settings, save_settings
    from tokitty.streamdock.presets import save_hidden_accounts

    preset = {"name": "p", "account_index": 0, "env": "native", "cwd": "C:\\a"}
    save_settings(tmp_path, Settings(tray_enabled=False, streamdock_presets=[preset]))
    assert save_hidden_accounts(tmp_path, ["work", "", "work", "codex"]) == ["work", "codex"]
    loaded = load_settings(tmp_path)
    assert loaded.streamdock_hidden_accounts == ["work", "codex"]
    assert (loaded.tray_enabled, loaded.streamdock_presets) == (False, [preset])
    assert save_hidden_accounts(tmp_path, []) == []
