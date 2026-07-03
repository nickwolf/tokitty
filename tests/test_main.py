from datetime import datetime, timedelta, timezone

from tokitty.__main__ import _display_state_for, _next_last_good
from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.poller import PollResult

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


def _limit(kind="session", percent=100.0, severity="normal", is_active=True, resets_at=None):
    return LimitInfo(kind=kind, percent=percent, severity=severity, resets_at=resets_at, is_active=is_active)


def _snapshot(session_pct=40.0, weekly_pct=20.0, limits=None, session_resets_at=None, weekly_resets_at=None):
    return UsageSnapshot(
        session_pct=session_pct,
        session_resets_at=session_resets_at or (NOW + timedelta(hours=3)),
        weekly_pct=weekly_pct,
        weekly_resets_at=weekly_resets_at or (NOW + timedelta(days=3)),
        limits=limits or [],
    )


def _ok(snapshot):
    return PollResult(status="ok", snapshot=snapshot, message=None, fetched_at=NOW)


def _error(status="stale_token"):
    return PollResult(status=status, snapshot=None, message="access token expired", fetched_at=NOW)


def test_ok_result_with_no_previous_uses_live_data():
    display = _display_state_for(_ok(_snapshot(session_pct=56.0, weekly_pct=50.0)), previous=None, now=NOW)

    assert display["session_pct"] == 56.0
    assert display["weekly_pct"] == 50.0
    assert display["hint_text"] is None
    assert display["dimmed"] is False


def test_ok_result_still_detects_activate_against_last_good_snapshot():
    capped_limit = _limit(kind="session", resets_at=NOW + timedelta(minutes=5))
    capped_snapshot = _snapshot(session_pct=100.0, limits=[capped_limit])
    cleared_snapshot = _snapshot(session_pct=0.0, limits=[])

    previous = _ok(capped_snapshot)
    display = _display_state_for(_ok(cleared_snapshot), previous=previous, now=NOW)

    assert display["state"] == "activate"


def test_non_ok_with_no_good_snapshot_shows_blocking_fallback():
    display = _display_state_for(_error("stale_token"), previous=None, now=NOW)

    assert display["state"] == "confused"
    assert display["session_reset_text"] == "—"
    assert display["dimmed"] is True
    assert display["hint_text"]


def test_non_ok_with_cached_uncapped_snapshot_reuses_cached_data_silently():
    previous = _ok(_snapshot(session_pct=56.0, weekly_pct=50.0))

    display = _display_state_for(_error("stale_token"), previous=previous, now=NOW)

    assert display["session_pct"] == 56.0
    assert display["weekly_pct"] == 50.0
    assert display["hint_text"] is None
    assert display["dimmed"] is False


def test_non_ok_with_cached_capped_snapshot_keeps_counting_down_silently():
    capped_limit = _limit(kind="session", resets_at=NOW + timedelta(minutes=30))
    previous = _ok(_snapshot(session_pct=100.0, limits=[capped_limit]))

    later = NOW + timedelta(minutes=10)  # token went stale mid-countdown
    display = _display_state_for(_error("stale_token"), previous=previous, now=later)

    assert display["hint_text"] is None
    assert display["dimmed"] is False
    assert "20m" in display["session_reset_text"]  # still ticks down using the live clock


def test_non_ok_with_cached_capped_snapshot_overdue_shows_small_warning():
    capped_limit = _limit(kind="session", resets_at=NOW + timedelta(minutes=5))
    previous = _ok(_snapshot(session_pct=100.0, limits=[capped_limit]))

    later = NOW + timedelta(minutes=20)  # well past the cached reset time
    display = _display_state_for(_error("stale_token"), previous=previous, now=later)

    assert display["hint_text"] is not None
    assert display["dimmed"] is True
    assert display["session_pct"] == 100.0  # still shows cached data, not blanked to "-"


def test_next_last_good_keeps_previous_on_error():
    good = _ok(_snapshot())
    bad = _error("stale_token")

    assert _next_last_good(bad, good) is good


def test_next_last_good_replaces_on_new_success():
    good = _ok(_snapshot())
    newer = _ok(_snapshot(session_pct=5.0))

    assert _next_last_good(newer, good) is newer


def test_next_last_good_stays_none_until_first_success():
    bad = _error("stale_token")

    assert _next_last_good(bad, None) is None
