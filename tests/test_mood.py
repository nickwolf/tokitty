from datetime import datetime, timedelta, timezone

import pytest

from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.mood import (
    compute_capped_substate,
    compute_mood,
    detect_activate,
    is_capped,
    select_binding_capped_limit,
)


@pytest.mark.parametrize(
    "session_pct,weekly_pct,expected_mood,expected_tag",
    [
        (0, 0, "sleeping", "5h"),
        (24.9, 0, "sleeping", "5h"),
        (25.0, 0, "content", "5h"),
        (49.9, 0, "content", "5h"),
        (50.0, 0, "interested", "5h"),
        (74.9, 0, "interested", "5h"),
        (75.0, 0, "alert", "5h"),
        (89.9, 0, "alert", "5h"),
        (90.0, 0, "panicked", "5h"),
        (99.9, 0, "panicked", "5h"),
        (10, 60, "interested", "7d"),
    ],
)
def test_compute_mood_thresholds(session_pct, weekly_pct, expected_mood, expected_tag):
    mood, tag = compute_mood(session_pct, weekly_pct)

    assert mood == expected_mood
    assert tag == expected_tag


def _limit(kind="session", percent=100.0, severity="normal", is_active=True, resets_at=None):
    if resets_at is None:
        resets_at = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
    return LimitInfo(kind=kind, percent=percent, severity=severity, resets_at=resets_at, is_active=is_active)


def test_is_capped_true_at_100_percent():
    assert is_capped(_limit(percent=100.0)) is True


def test_is_capped_false_below_100_percent():
    assert is_capped(_limit(percent=99.9)) is False


def test_is_capped_true_for_blocked_severity_regardless_of_percent():
    assert is_capped(_limit(percent=10.0, severity="exceeded")) is True


def test_select_binding_capped_limit_picks_soonest_reset():
    soon = _limit(kind="session", resets_at=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc))
    later = _limit(kind="weekly_all", resets_at=datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc))

    result = select_binding_capped_limit([later, soon])

    assert result is soon


def test_select_binding_capped_limit_ignores_uncapped_and_inactive():
    uncapped = _limit(percent=10.0)
    inactive = _limit(is_active=False)

    result = select_binding_capped_limit([uncapped, inactive])

    assert result is None


def test_compute_capped_substate_flopped_when_far_from_reset():
    limit = _limit(kind="session")
    now = limit.resets_at - timedelta(hours=1)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "flopped"
    assert state.driving_tag == "5h"


def test_compute_capped_substate_stirring_within_stir_window():
    limit = _limit(kind="session")
    now = limit.resets_at - timedelta(minutes=10)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "stirring"


def test_compute_capped_substate_waking_within_wake_window():
    limit = _limit(kind="session")
    now = limit.resets_at - timedelta(minutes=1)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "waking"


def test_compute_capped_substate_uses_weekly_window_for_weekly_kinds():
    limit = _limit(kind="weekly_all")
    now = limit.resets_at - timedelta(hours=1)

    state = compute_capped_substate(limit, now=now)

    assert state.substate == "stirring"
    assert state.driving_tag == "7d"


def _snapshot(limits):
    return UsageSnapshot(session_pct=0.0, session_resets_at=None, weekly_pct=0.0, weekly_resets_at=None, limits=limits)


def test_detect_activate_true_when_capped_clears():
    previous = _snapshot([_limit(percent=100.0)])
    current = _snapshot([_limit(percent=0.0, is_active=False)])

    assert detect_activate(previous, current) is True


def test_detect_activate_false_when_still_capped():
    capped_limit = _limit(percent=100.0)
    previous = _snapshot([capped_limit])
    current = _snapshot([capped_limit])

    assert detect_activate(previous, current) is False


def test_detect_activate_false_when_no_previous_snapshot():
    current = _snapshot([])

    assert detect_activate(None, current) is False
