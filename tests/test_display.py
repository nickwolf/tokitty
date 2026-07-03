import re
from datetime import datetime, timezone

from tokitty.display import bar_color, format_countdown, format_reset_day, format_reset_time


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
