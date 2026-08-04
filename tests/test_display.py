import re
from datetime import datetime, timezone

from tokitty.display import (
    bar_color,
    format_countdown,
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
