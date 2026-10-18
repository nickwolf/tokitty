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
    play: float = 0.0               # reserved, not read yet
    pace: float = 1.0               # scales every rest span (>1 is slower)
    stops: Tuple[int, int] = (1, 3)  # stops per trip, as a randint range


# (first hour, drives) per band of local time, each running to the next start.
_BANDS = (
    (6, Drives(trip=1.5, nap=0.2, stay=0.0, pace=0.7)),    # morning
    (10, Drives(trip=1.0, nap=0.5, stay=0.0, pace=1.0)),   # day
    (18, Drives(trip=0.8, nap=1.0, stay=0.5, pace=1.2)),   # evening
    (23, Drives(trip=0.3, nap=3.0, stay=1.0, pace=1.6)),   # night, until 6
)


def drives(hour: Optional[int]) -> Drives:
    """The drives for a local hour (0-23); None, the setting being off, is the
    plain cat."""
    if hour is None:
        return Drives()
    row = _BANDS[-1][1]                # before 6 is still the night before
    for start, band in _BANDS:
        if hour >= start:
            row = band
    return replace(row, pace=min(PACE_MAX, max(PACE_MIN, row.pace)))
