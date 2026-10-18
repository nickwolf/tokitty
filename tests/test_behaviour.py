from dataclasses import replace

import pytest

from tokitty import behaviour
from tokitty.behaviour import Drives, drives


def test_none_is_the_plain_cat_that_plays():
    assert drives(None) == replace(Drives(), play=0.3)
    assert Drives().trip == 1.0 and Drives().nap == 0.0 and Drives().pace == 1.0
    assert Drives().play == 0.0


@pytest.mark.parametrize("hour, band", [
    (0, "night"), (5, "night"), (6, "morning"), (9, "morning"), (10, "day"),
    (17, "day"), (18, "evening"), (22, "evening"), (23, "night"),
])
def test_band_edges(hour, band):
    expected = {
        "night": Drives(trip=0.3, nap=3.0, stay=1.0, play=0.1, pace=1.6),
        "morning": Drives(trip=1.5, nap=0.2, stay=0.0, play=0.4, pace=0.7),
        "day": Drives(trip=1.0, nap=0.5, stay=0.0, play=0.3, pace=1.0),
        "evening": Drives(trip=0.8, nap=1.0, stay=0.5, play=0.3, pace=1.2),
    }[band]
    assert drives(hour) == expected


def test_every_hour_has_stops_and_a_pace_in_bounds():
    for hour in range(24):
        d = drives(hour)
        assert d.stops == (1, 3)
        assert behaviour.PACE_MIN <= d.pace <= behaviour.PACE_MAX


def test_pace_is_clamped(monkeypatch):
    monkeypatch.setattr(behaviour, "_BANDS", ((0, replace(Drives(), pace=9.0)),))
    assert drives(4).pace == behaviour.PACE_MAX
    monkeypatch.setattr(behaviour, "_BANDS", ((0, replace(Drives(), pace=0.01)),))
    assert drives(4).pace == behaviour.PACE_MIN


def test_balanced_is_the_band_unchanged_at_every_hour():
    for hour in [None, *range(24)]:
        assert drives(hour, "balanced") == drives(hour)


def test_unknown_personality_is_balanced():
    assert drives(12, "grumpy") == drives(12, "balanced")


@pytest.mark.parametrize("name, mult, stops", [
    ("lazy", dict(trip=.5, nap=2.5, stay=1.5, play=.5, pace=1.4), (1, 2)),
    ("playful", dict(trip=1.2, nap=.6, stay=.5, play=3.0, pace=.8), (1, 3)),
    ("curious", dict(trip=1.6, nap=.6, stay=.5, play=1.0, pace=.9), (2, 4)),
])
def test_personality_scales_the_evening_band(name, mult, stops):
    base = Drives(trip=0.8, nap=1.0, stay=0.5, play=0.3, pace=1.2)
    got = drives(20, name)
    for field, factor in mult.items():
        assert getattr(got, field) == pytest.approx(getattr(base, field) * factor)
    assert got.stops == stops


def test_lazy_at_night_paces_inside_the_clamp():
    assert drives(2, "lazy").pace == pytest.approx(1.6 * 1.4)


def test_every_hour_and_personality_keeps_pace_in_bounds():
    for hour in [None, *range(24)]:
        for name in behaviour.PERSONALITIES:
            assert behaviour.PACE_MIN <= drives(hour, name).pace <= behaviour.PACE_MAX


def test_clamp_applies_after_the_personality(monkeypatch):
    monkeypatch.setattr(behaviour, "_BANDS", ((0, replace(Drives(), pace=2.5)),))
    assert drives(4, "lazy").pace == behaviour.PACE_MAX
    monkeypatch.setattr(behaviour, "_BANDS", ((0, replace(Drives(), pace=0.5)),))
    assert drives(4, "playful").pace == behaviour.PACE_MIN
