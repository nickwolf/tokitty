"""What the cat is inclined to do right now (#138): a small set of drives
the wandering reads when it decides what the next rest at home leads to.

Pure data and tables, no toolkit and no clock: the caller passes the hour.
Drives() is the cat as it was before any of this existed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Tuple

# Bounds on `pace`, the idle-CPU budget: a longer rest is cheaper, a shorter
# one wakes the app more often.
PACE_MIN = 0.5
PACE_MAX = 3.0


@dataclass(frozen=True)
class Drives:
    trip: float = 1.0               # weight of a stroll along the top edge
    nap: float = 0.0                # weight of dozing off where it is
    stay: float = 0.0               # weight of simply resting on
    play: float = 0.0               # weight of a scratch (#124)
    bat: float = 0.0                # chance a bar ticking up draws a swat (#135)
    pounce: float = 0.0             # chance a session reset draws a pounce
    watch: float = 0.0              # chance an alert sends the cat to the bar
    pace: float = 1.0               # scales every rest span (>1 is slower)
    stops: Tuple[int, int] = (1, 3)  # stops per trip, as a randint range


# The chances for the bar reactions (#135), the same in every band.
_BARS = {"bat": 0.5, "pounce": 1.0, "watch": 0.5}

# (first hour, drives) per band of local time, each running to the next start.
_BANDS = (
    (6, Drives(trip=1.5, nap=0.2, stay=0.0, play=0.4, pace=0.7, **_BARS)),    # morning
    (10, Drives(trip=1.0, nap=0.5, stay=0.0, play=0.3, pace=1.0, **_BARS)),   # day
    (18, Drives(trip=0.8, nap=1.0, stay=0.5, play=0.3, pace=1.2, **_BARS)),   # evening
    (23, Drives(trip=0.3, nap=3.0, stay=1.0, play=0.1, pace=1.6, **_BARS)),   # night, until 6
)
# With the clock off: the plain cat, but it plays. Drives() itself keeps play
# at 0, the behaviour from before any of this existed.
_NO_CLOCK = replace(Drives(), play=0.3, **_BARS)

PERSONALITIES = ("balanced", "lazy", "playful", "curious")

# Per personality, what the band's drives are multiplied by; `stops` replaces.
_PERSONALITY = {
    "balanced": Drives(trip=1.0, nap=1.0, stay=1.0, play=1.0, pace=1.0, stops=(1, 3),
                       bat=1.0, pounce=1.0, watch=1.0),
    "lazy":     Drives(trip=0.5, nap=2.5, stay=1.5, play=0.5, pace=1.4, stops=(1, 2),
                       bat=0.5, pounce=1.0, watch=0.5),
    "playful":  Drives(trip=1.2, nap=0.6, stay=0.5, play=3.0, pace=0.8, stops=(1, 3),
                       bat=2.0, pounce=1.0, watch=1.0),
    "curious":  Drives(trip=1.6, nap=0.6, stay=0.5, play=1.0, pace=0.9, stops=(2, 4),
                       bat=2.0, pounce=1.0, watch=2.0),
}


def _chance(p: float) -> float:
    return min(1.0, max(0.0, p))


def drives(hour: Optional[int], personality: str = "balanced") -> Drives:
    """The drives for a local hour (0-23) as a personality sets them; None for
    the hour, the setting being off, is the plain cat's baseline (which plays).
    An unknown personality is balanced."""
    if hour is None:
        row = _NO_CLOCK
    else:
        row = _BANDS[-1][1]            # before 6 is still the night before
        for start, band in _BANDS:
            if hour >= start:
                row = band
    mult = _PERSONALITY.get(personality, _PERSONALITY["balanced"])
    return Drives(
        trip=row.trip * mult.trip, nap=row.nap * mult.nap, stay=row.stay * mult.stay,
        play=row.play * mult.play,
        bat=_chance(row.bat * mult.bat), pounce=_chance(row.pounce * mult.pounce),
        watch=_chance(row.watch * mult.watch),
        pace=min(PACE_MAX, max(PACE_MIN, row.pace * mult.pace)),
        stops=mult.stops,
    )
