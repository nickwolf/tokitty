"""resolve_first_run_action is the only gate run_gui consults.

The first block below pins the behaviour the old should_auto_open boolean
had, expressed through the function that replaced it, so widening the
gate for transcript-only installs cannot quietly change what a
subscription install sees.
"""
from tokitty.startup import (
    ACTION_ACCOUNTS,
    ACTION_USAGE_SETUP,
    ONBOARDING_MODELS_AUTOSELECT,
    resolve_first_run_action,
    should_auto_select_models,
)


def _opens_accounts(**kwargs):
    return resolve_first_run_action(**kwargs) == ACTION_ACCOUNTS


def test_no_auto_open_when_accounts_file_exists():
    assert _opens_accounts(
        accounts_state="valid_non_empty", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is False


def test_no_auto_open_when_accounts_file_malformed():
    assert _opens_accounts(
        accounts_state="malformed", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is False


def test_auto_open_when_absent_and_wsl_finds_two():
    assert _opens_accounts(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is True


def test_no_auto_open_when_absent_but_wsl_finds_only_one():
    assert _opens_accounts(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=1,
    ) is False


def test_no_auto_open_when_env_override_wins_first():
    assert _opens_accounts(
        accounts_state="absent", env_override_set=True,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=3,
    ) is False


def test_no_auto_open_when_home_relative_wins_first():
    assert _opens_accounts(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=True, keychain_available=False,
        platform="win32", wsl_match_count=3,
    ) is False


def test_no_auto_open_when_keychain_wins_on_darwin():
    assert _opens_accounts(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=True,
        platform="darwin", wsl_match_count=0,
    ) is False


# --- first-run action --------------------------------------------------

def _action(**overrides):
    kwargs = dict(
        accounts_state="absent",
        env_override_set=False,
        home_relative_exists=False,
        keychain_available=False,
        platform="win32",
        wsl_match_count=0,
    )
    kwargs.update(overrides)
    return resolve_first_run_action(**kwargs)


def test_multi_install_still_opens_accounts():
    assert _action(wsl_match_count=2) == ACTION_ACCOUNTS


def test_transcripts_without_credentials_open_usage_setup():
    """The user who previously got nothing: no credentials anywhere, but
    a full billing ledger on disk."""
    assert _action(transcripts_found=True) == ACTION_USAGE_SETUP


def test_no_credentials_and_no_transcripts_opens_nothing():
    assert _action() is None


def test_transcripts_do_not_override_an_existing_accounts_file():
    assert _action(accounts_state="valid_non_empty", transcripts_found=True) is None


def test_transcripts_do_not_override_a_resolvable_credential_source():
    assert _action(env_override_set=True, transcripts_found=True) is None
    assert _action(home_relative_exists=True, transcripts_found=True) is None
    assert _action(platform="darwin", keychain_available=True, transcripts_found=True) is None


def test_multi_install_wins_over_usage_setup():
    assert _action(wsl_match_count=2, transcripts_found=True) == ACTION_ACCOUNTS


# --- onboarding auto-select --------------------------------------------

def _autoselect(**overrides):
    kwargs = dict(
        poll_status="credentials_unreachable",
        scan_status="ok",
        has_records=True,
        onboarding_version=0,
    )
    kwargs.update(overrides)
    return should_auto_select_models(**kwargs)


def test_api_key_user_with_records_is_switched_once():
    assert _autoselect() is True
    assert _autoselect(onboarding_version=ONBOARDING_MODELS_AUTOSELECT) is False


def test_resting_look_never_triggers_a_switch():
    """A subscriber's token expires about an hour after Claude Code last
    ran, so this is their normal overnight state, not a broken account."""
    assert _autoselect(poll_status="stale_token") is False


def test_transient_and_recoverable_states_never_trigger_a_switch():
    assert _autoselect(poll_status="api_error") is False
    assert _autoselect(poll_status="keychain_denied") is False
    assert _autoselect(poll_status="ambiguous_credentials") is False
    assert _autoselect(poll_status="ok") is False


def test_an_unreadable_scan_is_not_evidence():
    assert _autoselect(scan_status="unavailable") is False
    assert _autoselect(scan_status=None) is False


def test_no_records_means_nothing_to_switch_to():
    assert _autoselect(has_records=False) is False


def test_a_partial_scan_with_records_still_counts():
    assert _autoselect(scan_status="partial") is True
