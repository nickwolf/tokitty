from tokitty.startup import should_auto_open


def test_no_auto_open_when_accounts_file_exists():
    assert should_auto_open(
        accounts_state="valid_non_empty", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is False


def test_no_auto_open_when_accounts_file_malformed():
    assert should_auto_open(
        accounts_state="malformed", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is False


def test_auto_open_when_absent_and_wsl_finds_two():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is True


def test_no_auto_open_when_absent_but_wsl_finds_only_one():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=1,
    ) is False


def test_no_auto_open_when_env_override_wins_first():
    assert should_auto_open(
        accounts_state="absent", env_override_set=True,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=3,
    ) is False


def test_no_auto_open_when_home_relative_wins_first():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=True, keychain_available=False,
        platform="win32", wsl_match_count=3,
    ) is False


def test_no_auto_open_when_keychain_wins_on_darwin():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=True,
        platform="darwin", wsl_match_count=0,
    ) is False
