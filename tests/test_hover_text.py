from datetime import datetime, timedelta, timezone

import pytest

from tokitty.burn import PACE_UNKNOWN, PACE_WONT_CAP, Pace
from tokitty.display import format_reset_day, format_reset_time
from tokitty.hover import (
    LimitHover,
    cat_hover_text,
    cat_phase,
    format_elapsed,
    LimitsHover,
    limit_hover_text,
    limits_popup_text,
    poll_problem,
)
from tokitty.pose import ACTIVITY_SPRITE_PLACEHOLDERS
from tokitty.sprites import ALL_STATES

NOW = datetime(2030, 1, 2, 9, 0, tzinfo=timezone.utc)
UNKNOWN = Pace(PACE_UNKNOWN)


def row(kind="session", pct=42.0, resets_in=timedelta(hours=2, minutes=14), capped=False, pace=UNKNOWN):
    return LimitHover("Session" if kind == "session" else "Week", kind, pct,
                      None if resets_in is None else NOW + resets_in, capped, pace)


def test_limits_popup_adds_the_credits_row_to_either_bar():
    hover = LimitsHover(row(), row("weekly"), credits="Extra usage: $3.62 / $20.00")
    for key in ("session", "weekly"):
        lines = limits_popup_text(hover, key, NOW).split("\n")
        assert lines[-1] == "Extra usage: $3.62 / $20.00"
        assert lines[:-1] == limit_hover_text(getattr(hover, key), NOW).split("\n")


def test_limits_popup_names_the_failed_poll_and_the_last_good_one():
    last_good = datetime.now(timezone.utc) - timedelta(minutes=7)
    hover = LimitsHover(row(), row("weekly"), problem="rate limited (429)", last_good_at=last_good)
    lines = limits_popup_text(hover, "weekly", datetime.now(timezone.utc)).split("\n")
    assert lines[-2:] == ["Poll failed: rate limited (429)",
                          f"Last good poll: {format_reset_time(last_good)}"]


def test_limits_popup_adds_the_weekday_to_a_last_good_poll_from_another_day():
    last_good = NOW - timedelta(days=1)
    hover = LimitsHover(row(), row("weekly"), problem="no network", last_good_at=last_good)
    assert limits_popup_text(hover, "session", NOW).endswith(
        f"Last good poll: {last_good.astimezone().strftime('%a')} {format_reset_time(last_good)}")


def test_limits_popup_without_numbers_is_just_the_reason():
    hover = LimitsHover(None, None, problem="no network")
    assert limits_popup_text(hover, "session", NOW) == "Poll failed: no network"


def test_limits_popup_is_quiet_about_polls_that_worked():
    text = limits_popup_text(LimitsHover(row(), row("weekly")), "session", NOW)
    assert "Poll failed" not in text and "Last good poll" not in text


@pytest.mark.parametrize("status, message, expected", [
    (429, "HTTP 429 from usage endpoint", "rate limited (429)"),
    (503, "HTTP 503 from usage endpoint", "server error (503)"),
    (403, "HTTP 403 from usage endpoint", "HTTP error (403)"),
    (None, "Network error reaching usage endpoint: [Errno -2] Name or service not known", "no network"),
    (None, "Network error reaching usage endpoint: timed out", "timed out"),
    (None, "timed out", "timed out"),
    (None, "Usage endpoint returned invalid JSON: Expecting value", "unreadable response"),
    (None, "something else", "unexpected error"),
    (None, None, "unexpected error"),
])
def test_poll_problem_names_the_cause(status, message, expected):
    assert poll_problem(status, message) == expected


def test_limits_popup_has_no_credits_row_without_credits_data():
    hover = LimitsHover(row(), row("weekly"))
    assert limits_popup_text(hover, "session", NOW) == limit_hover_text(row(), NOW)


def test_session_popup_has_the_reset_time_and_countdown():
    text = limit_hover_text(row(), NOW)
    assert text.split("\n") == [
        "Session 42%",
        f"Resets {format_reset_time(NOW + timedelta(hours=2, minutes=14))} (in 2h 14m)",
    ]


def test_week_popup_names_the_day_and_counts_days():
    resets = NOW + timedelta(days=2, hours=3, minutes=5)
    text = limit_hover_text(row("weekly", 81.0, resets - NOW), NOW)
    assert text.split("\n") == [
        "Week 81%",
        f"Resets {format_reset_day(resets)}, {format_reset_time(resets)} (in 2d 3h)",
    ]


def test_a_cap_pace_line_has_the_time_and_for_the_week_the_weekday():
    caps_at = NOW + timedelta(hours=1)
    session = limit_hover_text(row(pace=Pace("caps", caps_at)), NOW).split("\n")
    assert session[-1] == f"At this pace: caps ~{format_reset_time(caps_at)}"
    week = limit_hover_text(row("weekly", pace=Pace("caps", caps_at)), NOW).split("\n")
    assert week[-1] == f"At this pace: caps ~{caps_at.astimezone().strftime('%a')} {format_reset_time(caps_at)}"


def test_a_slow_burn_says_it_wont_cap():
    assert limit_hover_text(row(pace=Pace(PACE_WONT_CAP)), NOW).endswith(
        "At this pace: won't cap before reset")


def test_no_pace_line_while_warming_up():
    assert len(limit_hover_text(row(pace=UNKNOWN), NOW).split("\n")) == 2


def test_a_capped_limit_shows_the_countdown_and_no_pace():
    text = limit_hover_text(
        row(pct=100.0, resets_in=timedelta(hours=1, minutes=2), capped=True, pace=Pace(PACE_WONT_CAP)), NOW)
    assert text.split("\n") == ["Session 100%", "Resets in 1h 02m"]


def test_an_unknown_reset_leaves_the_line_out():
    text = limit_hover_text(row(resets_in=None), NOW)
    assert text == "Session 42%"


def test_a_reset_already_due_drops_the_countdown():
    text = limit_hover_text(row(resets_in=timedelta(seconds=-5)), NOW)
    assert text.split("\n")[1].startswith("Resets ") and "(in" not in text


@pytest.mark.parametrize("seconds, expected", [(0, "0s"), (45, "45s"), (60, "1m"), (119, "1m"),
                                                (3600, "1h 00m"), (3900, "1h 05m")])
def test_format_elapsed(seconds, expected):
    assert format_elapsed(seconds) == expected


def test_cat_phrases_for_the_activity_states():
    assert cat_hover_text("working", "Bash", 125) == "Working: Bash, 2m"
    assert cat_hover_text("working", "", 3) == "Working, 3s"
    assert cat_hover_text("permission", "", 45) == "Waiting for permission, 45s"
    assert cat_hover_text("permission_frantic", "", 200) == "Waiting for permission, 3m"
    assert cat_hover_text("question", "", 60) == "Asking a question, 1m"
    assert cat_hover_text("thinking", "", 90) == "Thinking"


def test_a_fresh_phase_has_no_elapsed_time():
    assert cat_hover_text("working", "Bash", 0.4) == "Working: Bash"
    assert cat_hover_text("working", "Bash", None) == "Working: Bash"


def test_cat_phrases_for_the_moods():
    assert cat_hover_text("sleeping", "", 500) == "Sleeping"
    assert cat_hover_text("content", "", 500) == "Content"


def test_every_sprite_state_has_a_phrase_and_the_escalations_share_a_phase():
    for state in ALL_STATES:
        text = cat_hover_text(state, "", None)
        assert text and "_" not in text, state
    assert set(ACTIVITY_SPRITE_PLACEHOLDERS.values()) <= set(ALL_STATES)
    assert cat_phase("permission_urgent") == cat_phase("permission") == cat_phase("permission_frantic")
    assert cat_phase("working") != cat_phase("thinking")
