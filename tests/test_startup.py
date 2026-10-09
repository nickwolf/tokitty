"""should_auto_select_models, the one startup decision left in startup.py.
The first-run decision is covered by test_first_run.py."""
from tokitty.startup import ONBOARDING_MODELS_AUTOSELECT, should_auto_select_models


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
