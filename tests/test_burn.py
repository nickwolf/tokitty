import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.burn import BurnTracker, Sample

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
SESSION_RESET = NOW + timedelta(hours=3)
WEEKLY_RESET = NOW + timedelta(days=4)


def _snapshot(offset_seconds=0, session_pct=10.0, weekly_pct=5.0,
              session_resets_at=SESSION_RESET, weekly_resets_at=WEEKLY_RESET,
              limits=()):
    return UsageSnapshot(
        session_pct=session_pct,
        session_resets_at=session_resets_at,
        weekly_pct=weekly_pct,
        weekly_resets_at=weekly_resets_at,
        limits=list(limits),
        fetched_at=NOW + timedelta(seconds=offset_seconds),
    )


def test_add_records_a_sample():
    tracker = BurnTracker()
    tracker.add(_snapshot(session_pct=12.0))
    assert len(tracker.samples) == 1
    assert tracker.samples[0].session_pct == 12.0


def test_add_ignores_a_repeated_snapshot_with_the_same_fetched_at():
    """The render path re-uses the cached snapshot object every 500ms on a
    failed poll -- ingesting it repeatedly would fake a flat burn rate."""
    tracker = BurnTracker()
    snapshot = _snapshot()
    for _ in range(50):
        tracker.add(snapshot)
    assert len(tracker.samples) == 1


def test_add_ignores_an_out_of_order_snapshot():
    tracker = BurnTracker()
    tracker.add(_snapshot(offset_seconds=300))
    tracker.add(_snapshot(offset_seconds=120))
    assert len(tracker.samples) == 1


def test_add_drops_samples_older_than_the_window():
    tracker = BurnTracker(window_seconds=600)
    tracker.add(_snapshot(offset_seconds=0))
    tracker.add(_snapshot(offset_seconds=300))
    tracker.add(_snapshot(offset_seconds=900))
    assert [s.fetched_at for s in tracker.samples] == [
        NOW + timedelta(seconds=300),
        NOW + timedelta(seconds=900),
    ]


def test_add_records_whether_the_account_is_currently_capped():
    tracker = BurnTracker()
    capped = LimitInfo(kind="session", percent=100.0, severity="exceeded",
                       resets_at=SESSION_RESET, is_active=True)
    tracker.add(_snapshot(limits=[capped]))
    assert tracker.samples[0].capped is True


def test_samples_is_a_copy_not_the_internal_list():
    tracker = BurnTracker()
    tracker.add(_snapshot())
    tracker.samples.clear()
    assert len(tracker.samples) == 1


def test_sample_is_frozen():
    tracker = BurnTracker()
    tracker.add(_snapshot())
    sample = tracker.samples[0]
    assert isinstance(sample, Sample)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.session_pct = 99.0
