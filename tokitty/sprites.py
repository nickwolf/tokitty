"""Pixel-art data for the cat: palette, pose templates, and per-mood frames.

Three 15x13 pose templates (sitting calm, sitting alert/upright, and
lying down) are each composed with small per-state substitution dicts
to produce concrete frames. Placeholder letters (L, R, A, T) each
appear exactly once per template, so "replace every occurrence of this
character" is equivalent to "replace this one designated cell" -- this
guarantees every produced frame is structurally valid (equal
dimensions, only palette-defined characters) by construction rather
than by hand-aligning ~20 independent grids.

The templates themselves were generated once from simple geometric
shapes (ellipses/triangles stamped onto a grid) to avoid hand-counting
errors, then hand-tuned and baked in here as plain static data --
exactly the format a future session can edit directly, per the design
spec's roadmap for further art passes.
"""
from __future__ import annotations

from typing import Dict, List

SCALE = 7  # device pixels per sprite pixel when rendered on the Canvas

# Non-coat colors: shared by every coat preset.
BASE_PALETTE: Dict[str, str] = {
    ".": "",  # transparent -- not drawn
    "k": "#2b1a12",  # outline / closed-eye line
    "w": "#fff6ec",  # muzzle / belly / paws
    "e": "#3fae5c",  # eye, open
    "n": "#d6748c",  # nose (default accent)
    "z": "#8fd0e8",  # sleep accent
    "!": "#e6483c",  # alert/panic accent
    "?": "#e8c23c",  # confused accent
    "h": "#f2d675",  # activate/happy sparkle accent
}

# Coat presets: each defines the same region keys. "c" is the patch
# region -- distinct cells in the grids that only some coats color
# differently (calico patches); on the default orange tabby it matches
# the coat so the pattern plumbing is invisible until Phase 4 uses it.
COATS: Dict[str, Dict[str, str]] = {
    "orange_tabby": {
        "o": "#e8823c",  # coat
        "O": "#c26a2c",  # coat shading
        "s": "#a8541f",  # tabby stripe
        "c": "#e8823c",  # patch region (matches coat on this preset)
        "p": "#f6b8c8",  # inner ear
    },
}


def get_palette(coat: str = "orange_tabby") -> Dict[str, str]:
    """Full character->color mapping for one coat preset."""
    merged = dict(BASE_PALETTE)
    merged.update(COATS[coat])
    return merged


PALETTE: Dict[str, str] = get_palette()

# Sitting, calm pose -- ears relaxed. L/R are the eye cells, A is a
# single accent cell (nose by default, repainted per state).
SITTING_TEMPLATE: List[str] = [
    "...............",
    "....oo.s..oo...",
    "...oooooooooo..",
    "....ooooooo....",
    "....oLoooRo....",
    "....oowwwoo....",
    "....owwAwwo..Oo",
    ".....wwwwwOO.o.",
    "....soowoosOOo.",
    "...ooowwwoooOo.",
    "...oowwwwwooOo.",
    "...oowwwwwooOo.",
    "....owwwwwoO...",
]

# Sitting, alert pose -- ears sharply perked, upright posture. Same
# placeholder cells as SITTING_TEMPLATE.
ALERT_TEMPLATE: List[str] = [
    "....oo....oo...",
    "....oo.s..oo...",
    "...oooooooooo..",
    "....oLoooRo....",
    "....ooooooo....",
    "....oowwwoo....",
    ".....wwAww...o.",
    ".....owwwoOO.o.",
    "....sooooosOOo.",
    "...ooowwwoooO..",
    "...oowwwwwooO..",
    "...oowwwwwooO..",
    "....owwwwwoO...",
]

# Lying down, for the capped/wake sequence. L is the visible (near)
# eye, T is the tail-tip cell.
FLOPPED_TEMPLATE: List[str] = [
    "...............",
    "...............",
    "...............",
    "...............",
    "...............",
    ".opoo....s.....",
    ".oooosooOOOO...",
    "ooLooooOOOOOOo.",
    "owwwoooOOOOOOoT",
    ".wwwwwwwwwwwwT.",
    ".....wwwwwwwo..",
    ".....ww.www..o.",
    "...............",
]

SITTING_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "sleeping": [{"L": "k", "R": "k", "A": "z"}, {"L": "k", "R": "k", "A": "n"}],
    "content": [{"L": "e", "R": "e", "A": "n"}, {"L": "k", "R": "k", "A": "n"}],
    "interested": [{"L": "e", "R": "e", "A": "n"}, {"L": "e", "R": "k", "A": "n"}],
}

ALERT_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "alert": [{"L": "e", "R": "e", "A": "n"}, {"L": "e", "R": "e", "A": "!"}],
    "panicked": [{"L": "e", "R": "e", "A": "!"}, {"L": "e", "R": "e", "A": "n"}],
    "confused": [{"L": "e", "R": "k", "A": "?"}, {"L": "k", "R": "e", "A": "?"}],
    "waking": [{"L": "e", "R": "k", "A": "n"}, {"L": "k", "R": "e", "A": "n"}],
    "activate": [{"L": "e", "R": "e", "A": "h"}, {"L": "e", "R": "e", "A": "n"}],
}

FLOPPED_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "flopped": [{"L": "k", "T": "k"}, {"L": "k", "T": "O"}],
    "stirring": [{"L": "k", "T": "O"}, {"L": "e", "T": "k"}],
}

ALL_STATES = (
    tuple(SITTING_FRAME_SPECS.keys())
    + tuple(ALERT_FRAME_SPECS.keys())
    + tuple(FLOPPED_FRAME_SPECS.keys())
)


def _apply(template: List[str], subs: Dict[str, str]) -> List[str]:
    return ["".join(subs.get(ch, ch) for ch in row) for row in template]


def get_frames(state: str) -> List[List[str]]:
    """Return the list of frame grids (each a list of equal-length row
    strings) for the given mood/sub-state name."""
    if state in SITTING_FRAME_SPECS:
        template, specs = SITTING_TEMPLATE, SITTING_FRAME_SPECS[state]
    elif state in ALERT_FRAME_SPECS:
        template, specs = ALERT_TEMPLATE, ALERT_FRAME_SPECS[state]
    elif state in FLOPPED_FRAME_SPECS:
        template, specs = FLOPPED_TEMPLATE, FLOPPED_FRAME_SPECS[state]
    else:
        raise KeyError(f"Unknown sprite state: {state!r}")

    return [_apply(template, subs) for subs in specs]
