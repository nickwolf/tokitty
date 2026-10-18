from dataclasses import replace

import pytest

from tokitty import behaviour
from tokitty.behaviour import Drives, drives


def test_none_is_the_plain_cat():
    assert drives(None) == Drives()
    assert Drives().trip == 1.0 and Drives().nap == 0.0 and Drives().pace == 1.0


@pytest.mark.parametrize("hour, band", [
    (0, "night"), (5, "night"), (6, "morning"), (9, "morning"), (10, "day"),
    (17, "day"), (18, "evening"), (22, "evening"), (23, "night"),
])
def test_band_edges(hour, band):
    expected = {
        "night": Drives(trip=0.3, nap=3.0, stay=1.0, pace=1.6),
        "morning": Drives(trip=1.5, nap=0.2, stay=0.0, pace=0.7),
        "day": Drives(trip=1.0, nap=0.5, stay=0.0, pace=1.0),
        "evening": Drives(trip=0.8, nap=1.0, stay=0.5, pace=1.2),
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
