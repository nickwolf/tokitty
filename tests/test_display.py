import pytest
import re
from datetime import datetime, timezone

from tokitty.display import (
    bar_color,
    format_countdown,
    format_time_left,
    format_projection,
    format_reset_day,
    format_reset_time,
    resolve_status_text,
)


def test_format_countdown_shows_hours_minutes_seconds():
    assert format_countdown(3723) == "1h 02m 03s"


def test_format_countdown_shows_minutes_seconds_under_an_hour():
    assert format_countdown(125) == "2m 05s"


def test_format_countdown_shows_seconds_only_under_a_minute():
    assert format_countdown(45) == "45s"


def test_format_countdown_floors_negative_to_zero():
    assert format_countdown(-10) == "0s"


def test_bar_color_green_below_50():
    assert bar_color(10) == "#4caf6b"


def test_bar_color_amber_between_50_and_80():
    assert bar_color(60) == "#e0a838"


def test_bar_color_red_at_80_and_above():
    assert bar_color(80) == "#e05252"


def test_format_reset_time_has_no_leading_zero_hour():
    dt = datetime(2026, 7, 3, 1, 29, tzinfo=timezone.utc)
    result = format_reset_time(dt)
    assert ("AM" in result) or ("PM" in result)
    assert not result.startswith("0")


def test_format_reset_day_format_is_weekday_month_day():
    dt = datetime(2026, 7, 6, 23, 59, tzinfo=timezone.utc)
    result = format_reset_day(dt)
    assert re.match(r"^[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2}$", result)


def test_format_projection_session_shows_time_only():
    caps_at = datetime(2026, 8, 4, 18, 20, tzinfo=timezone.utc)
    text = format_projection("session", caps_at)
    local = caps_at.astimezone()
    hour_12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    assert text == f"session caps ~{hour_12}:{local.minute:02d} {period}"


def test_format_projection_weekly_shows_weekday_and_time():
    caps_at = datetime(2026, 8, 5, 23, 40, tzinfo=timezone.utc)
    text = format_projection("weekly", caps_at)
    local = caps_at.astimezone()
    hour_12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    assert text == f"week caps ~{local.strftime('%a')} {hour_12}:{local.minute:02d} {period}"


def test_format_projection_weekly_omits_the_month_and_day():
    """Month/day pushes the line past the 160px wrap ceiling."""
    caps_at = datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)
    text = format_projection("weekly", caps_at)
    assert "Aug" not in text


def test_format_projection_stays_within_the_character_budget():
    """A crude proxy for the 160px wrap ceiling that runs headlessly.
    The real check is the Windows manual gate."""
    worst = format_projection("weekly", datetime(2026, 8, 5, 23, 40, tzinfo=timezone.utc))
    assert len(worst) <= 24


def test_resolve_status_text_prefers_the_hint():
    assert resolve_status_text("API hiccup", "$1.00 / $5.00", "session caps ~6:20 PM") == "API hiccup"


def test_resolve_status_text_falls_back_to_credits():
    assert resolve_status_text(None, "$1.00 / $5.00", "session caps ~6:20 PM") == "$1.00 / $5.00"


def test_resolve_status_text_falls_back_to_the_projection():
    assert resolve_status_text(None, None, "session caps ~6:20 PM") == "session caps ~6:20 PM"


def test_resolve_status_text_is_empty_when_nothing_applies():
    assert resolve_status_text(None, None, None) == ""


def test_resolve_status_text_treats_empty_strings_as_absent():
    assert resolve_status_text("", "", "session caps ~6:20 PM") == "session caps ~6:20 PM"


def test_reset_phrase_reads_for_each_reset_form():
    from tokitty.display import reset_phrase
    assert reset_phrase("6:10 PM") == "resets 6:10 PM"
    assert reset_phrase("Mon Oct 20") == "resets Mon Oct 20"
    assert reset_phrase("2h 14m 05s") == "resets in 2h 14m 05s"
    assert reset_phrase("14m 05s") == "resets in 14m 05s"
    assert reset_phrase("9s") == "resets in 9s"
    assert reset_phrase("—") == ""
    assert reset_phrase("") == ""


@pytest.mark.parametrize("seconds, expected", [
    (30, "30s"), (0, "0s"), (-5, "0s"), (59.9, "59s"),
    (60, "1m"), (45 * 60 + 59, "45m"),
    (3600, "1h 00m"), (2 * 3600 + 14 * 60 + 59, "2h 14m"),
    (86400, "1d 0h"), (2 * 86400 + 3 * 3600 + 1800, "2d 3h"),
])
def test_format_time_left_keeps_the_two_coarsest_units(seconds, expected):
    assert format_time_left(seconds) == expected


def test_resolve_status_text_orders_the_handoff_text():
    assert resolve_status_text(None, "$1", "proj", None, "Codex has room") == "Codex has room"
    assert resolve_status_text(None, "$1", "proj", "as of 4:12 PM", "Codex has room") == "as of 4:12 PM"
    assert resolve_status_text("hint", None, None, "as of 4:12 PM", "Codex has room") == "hint"
    assert resolve_status_text(None, "$1", "proj", None, None) == "$1"
