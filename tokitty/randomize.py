"""Curated appearance randomization: pick a colorway, a pattern and a tail
style from the existing preset keys. Never rolls free-form hex overrides -- curated presets
always look intentional. rng is injectable for deterministic tests."""
from __future__ import annotations

import random
from typing import Optional, Sequence, Tuple

from tokitty import sprites
from tokitty.behaviour import PERSONALITIES

PLAIN_TAIL_WEIGHT = 0.6  # most real cats; the rest is split across the ringed styles


def random_look(colorways: Sequence[str], patterns: Sequence[str],
                rng: Optional[random.Random] = None) -> Tuple[str, str]:
    r = rng or random
    return r.choice(list(colorways)), r.choice(list(patterns))


def random_tail(rng: Optional[random.Random] = None) -> str:
    r = rng or random
    ringed = [name for name in sprites.TAIL_STYLES if name != "plain"]
    if not ringed or r.random() < PLAIN_TAIL_WEIGHT:
        return "plain"
    return r.choice(ringed)


def random_personality(rng: Optional[random.Random] = None) -> str:
    r = rng or random
    return r.choice(list(PERSONALITIES))
