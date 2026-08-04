import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.burn import BurnTracker, MIN_SPAN_SECONDS, WINDOW_SECONDS, Projection, Sample

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


def _tracker_with(points, **snapshot_kwargs):
    """points = [(offset_seconds, session_pct, weekly_pct), ...]"""
    tracker = BurnTracker()
    for offset, session_pct, weekly_pct in points:
        tracker.add(_snapshot(offset_seconds=offset, session_pct=session_pct,
                              weekly_pct=weekly_pct, **snapshot_kwargs))
    return tracker


def test_project_returns_none_with_a_single_sample():
    tracker = _tracker_with([(0, 10.0, 5.0)])
    assert tracker.project(NOW) is None


def test_project_returns_none_before_the_minimum_span():
    tracker = _tracker_with([(0, 10.0, 5.0), (60, 20.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=60)) is None


def test_project_extrapolates_the_session_cap_from_the_measured_rate():
    # 10% -> 40% over 600s = 0.05 %/s. 60% remains -> 1200s past the last
    # sample, i.e. 30 minutes after NOW+600.
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 40.0, 5.0)])
    projection = tracker.project(NOW + timedelta(seconds=600))
    assert projection == Projection(
        kind="session", caps_at=NOW + timedelta(seconds=1800)
    )


def test_project_returns_none_when_the_rate_is_flat():
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 10.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_usage_went_down():
    tracker = _tracker_with([(0, 40.0, 5.0), (600, 10.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_the_cap_lands_after_the_reset():
    # 10% -> 11% over 600s is slow enough to coast past SESSION_RESET (+3h).
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 11.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_already_capped():
    capped = LimitInfo(kind="session", percent=100.0, severity="exceeded",
                       resets_at=SESSION_RESET, is_active=True)
    tracker = BurnTracker()
    tracker.add(_snapshot(offset_seconds=0, session_pct=10.0))
    tracker.add(_snapshot(offset_seconds=600, session_pct=40.0, limits=[capped]))
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_the_projected_cap_is_already_past():
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 40.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=99999)) is None


def test_project_picks_the_nearer_of_two_live_projections():
    # BOTH limits project a real cap before their own reset, so this
    # exercises the min() and not just one candidate being suppressed:
    #   session 10 -> 30 over 600s = 0.0333 %/s, 70 left -> caps at +2700s
    #   weekly  90 -> 95 over 600s = 0.00833 %/s, 5 left -> caps at +1200s
    tracker = _tracker_with([(0, 10.0, 90.0), (600, 30.0, 95.0)])
    projection = tracker.project(NOW + timedelta(seconds=600))
    assert projection == Projection(
        kind="weekly", caps_at=NOW + timedelta(seconds=1200)
    )


def test_project_ignores_samples_from_before_a_window_reset():
    """A changed resets_at means the limit reset; spanning that boundary
    would read as a large negative burn and suppress a real projection.

    All three samples are inside WINDOW_SECONDS (600) of the newest, so
    the pre-reset sample is excluded by the resets_at walk-back and NOT
    merely aged out by the window -- otherwise this passes for the wrong
    reason.
    """
    tracker = BurnTracker()
    old_reset = SESSION_RESET - timedelta(hours=5)
    tracker.add(_snapshot(offset_seconds=0, session_pct=80.0,
                          session_resets_at=old_reset))
    tracker.add(_snapshot(offset_seconds=100, session_pct=5.0))
    tracker.add(_snapshot(offset_seconds=600, session_pct=55.0))
    assert len(tracker.samples) == 3
    projection = tracker.project(NOW + timedelta(seconds=600))
    # Measured across the two post-reset samples only: 5 -> 55 over 500s
    # = 0.1 %/s, 45 left -> caps 450s after the newest sample.
    assert projection == Projection(
        kind="session", caps_at=NOW + timedelta(seconds=1050)
    )


def test_project_returns_none_when_resets_at_is_missing():
    """A work account with no session window reports session_resets_at
    None -- without it there is no way to know a cap precedes the reset."""
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 40.0, 5.0)],
                            session_resets_at=None, weekly_resets_at=None)
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_decays_when_polls_stop_succeeding_and_add_is_no_longer_called():
    """Regression guard for finding 1: the window is the decay time even
    when tick() stops calling add() because polls are failing. project()
    must filter by its own `now`, not rely on add()'s trim, which freezes
    once add() stops being called."""
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 40.0, 5.0)])
    live_now = NOW + timedelta(seconds=600)
    projection = tracker.project(live_now)
    assert projection == Projection(kind="session", caps_at=NOW + timedelta(seconds=1800))

    stale_now = live_now + timedelta(seconds=WINDOW_SECONDS + 1)
    assert tracker.project(stale_now) is None


def test_project_walk_back_respects_the_clock_filter_not_just_add_times_trim():
    """The trap in finding 1: filtering in project() but leaving the
    walk-back over the raw internal buffer still reaches an aged-out
    sample, inflating elapsed and deflating the measured rate.

    Three samples span 900s (more than WINDOW_SECONDS=600), but none of
    them get dropped by add()'s own trim (each is within 600s of the
    newest at the time it was added). Only project()'s own clock filter
    -- using a `now` later than the newest sample -- excludes the oldest.
    """
    tracker = _tracker_with([(350, 45.0, 5.0), (600, 50.0, 5.0), (900, 65.0, 5.0)])
    assert len(tracker.samples) == 3  # add()'s trim never aged any of these out

    now = NOW + timedelta(seconds=1200)  # cutoff = NOW+600: ages out the offset-350 sample
    projection = tracker.project(now)
    # Windowed rate (offset 600 -> 900 only): (65-50)/300 = 0.05 %/s,
    # 35 remaining -> caps 700s after the newest sample (NOW+900) = NOW+1600.
    # Including the aged-out offset-350 sample instead gives (65-45)/550 =
    # 0.0364 %/s -- a measurably lower rate -- and a different caps_at.
    assert projection == Projection(kind="session", caps_at=NOW + timedelta(seconds=1600))


def test_min_span_seconds_is_five_minutes():
    assert MIN_SPAN_SECONDS == 300


def test_window_seconds_is_ten_minutes():
    """The window is the decay time -- how long a stale projection lingers
    after you stop. Changing this is a product decision, not a tuning knob."""
    assert WINDOW_SECONDS == 600


def test_project_walk_back_survives_per_poll_jitter_in_resets_at():
    """Regression guard: the usage endpoint derives resets_at from its own
    per-request clock, so an unchanged window reports a slightly different
    sub-second resets_at on every poll (observed live: 21:00:00.104399,
    then 21:00:00.936780). Exact equality in the walk-back read every poll
    as a new window, collapsed `oldest` to `newest`, pinned elapsed at 0.0,
    and suppressed every projection forever. This MUST fail against the
    pre-fix exact-equality comparison.
    """
    tracker = BurnTracker()
    tracker.add(_snapshot(
        offset_seconds=0, session_pct=10.0, weekly_pct=5.0,
        session_resets_at=SESSION_RESET + timedelta(microseconds=104399),
        weekly_resets_at=WEEKLY_RESET + timedelta(microseconds=104426),
    ))
    tracker.add(_snapshot(
        offset_seconds=300, session_pct=25.0, weekly_pct=5.0,
        session_resets_at=SESSION_RESET + timedelta(microseconds=936780),
        weekly_resets_at=WEEKLY_RESET + timedelta(microseconds=936805),
    ))
    tracker.add(_snapshot(
        offset_seconds=600, session_pct=40.0, weekly_pct=5.0,
        session_resets_at=SESSION_RESET + timedelta(microseconds=51234),
        weekly_resets_at=WEEKLY_RESET + timedelta(microseconds=51260),
    ))
    projection = tracker.project(NOW + timedelta(seconds=600))
    # Walk-back now spans all three samples: 10 -> 40 over 600s = 0.05 %/s,
    # 60 remaining -> caps 1200s after the newest sample (NOW+600) = NOW+1800.
    # Weekly stays flat at 5.0 across all samples, so it yields no candidate.
    assert projection == Projection(
        kind="session", caps_at=NOW + timedelta(seconds=1800)
    )


def test_project_still_detects_a_real_reset_despite_jitter_tolerance():
    """Proves the jitter tolerance did not defeat the walk-back's actual
    purpose: a genuine window reset (resets_at moves by the whole window
    length) must still break the walk-back, even though both post-reset
    samples carry their own sub-second jitter.
    """
    tracker = BurnTracker()
    old_reset = SESSION_RESET - timedelta(hours=5)
    tracker.add(_snapshot(offset_seconds=0, session_pct=80.0,
                          session_resets_at=old_reset))
    tracker.add(_snapshot(
        offset_seconds=100, session_pct=5.0,
        session_resets_at=SESSION_RESET + timedelta(microseconds=104399),
    ))
    tracker.add(_snapshot(
        offset_seconds=600, session_pct=55.0,
        session_resets_at=SESSION_RESET + timedelta(microseconds=936780),
    ))
    assert len(tracker.samples) == 3
    projection = tracker.project(NOW + timedelta(seconds=600))
    # Measured across the two post-reset samples only: 5 -> 55 over 500s
    # = 0.1 %/s, 45 left -> caps 450s after the newest sample.
    assert projection == Projection(
        kind="session", caps_at=NOW + timedelta(seconds=1050)
    )
