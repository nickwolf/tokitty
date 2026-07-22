"""Curated appearance randomization: pick a colorway and a pattern from the
existing preset keys. Never rolls free-form hex overrides -- curated presets
always look intentional. rng is injectable for deterministic tests."""
from __future__ import annotations

import random
from typing import Optional, Sequence, Tuple


def random_look(colorways: Sequence[str], patterns: Sequence[str],
                rng: Optional[random.Random] = None) -> Tuple[str, str]:
    r = rng or random
    return r.choice(list(colorways)), r.choice(list(patterns))
