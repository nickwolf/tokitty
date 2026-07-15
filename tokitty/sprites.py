"""Pixel-art data for the cat: palette, pose templates, and per-mood frames.

Three 28x26 pose templates (sitting calm, sitting alert/upright, and
lying down) are each composed with small per-state substitution dicts
to produce concrete frames. Sitting/alert placeholders (L, R, A) each
appear exactly once per template; the flopped template also carries
tail-sweep region markers (1-5, several cells each) that frames paint
or blank wholesale. Every substitution maps one character to one
character, so every produced frame is structurally valid (equal
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

SCALE = 4  # device pixels per sprite pixel when rendered on the Canvas

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
    "............................",
    "........o.........o.........",
    ".......ooo...o...ooo........",
    "......ooooooooooooooo.......",
    "......oopoosssssoopoo.......",
    ".....ooocccoooooooooOO......",
    ".......occsssosssoOO........",
    ".......oooooooooooOO........",
    "......ooooLoooooRooOO.......",
    ".......oooooowooooOO........",
    ".......oooowwAwwooOO........",
    ".......ooowwwwwwwoOO........",
    "........ooowwwwwoOO.........",
    ".........oooowooOO..........",
    "........ooooooooooo..OO.....",
    ".......ooooooooooooo..OO....",
    "......ooooooooooooooo..OO...",
    "......osssooowooooooo..OO...",
    "......ooooowwwwwooooo...OO..",
    ".....osssowwwwwwwcccoo..OO..",
    "......oooowwwwwwwccco...OO..",
    "......ooowwwwwwwwwcco..OO...",
    "......oooowwwwwwwoooo.OO....",
    ".......ooowwwwwwwooo.OO.....",
    "........oowwwwwwwOO.........",
    ".........oooooooOO..........",
]

# Sitting, alert pose -- ears sharply perked, upright posture. Same
# placeholder cells as SITTING_TEMPLATE.
ALERT_TEMPLATE: List[str] = [
    "........o.........o.........",
    "........o....o....o.........",
    ".......opooooooooopo........",
    "......oopoosssssoopoo.......",
    "......oocccoooooooooo.......",
    ".......occsssosssoOO........",
    ".......oooooooooooOO........",
    "......ooooLoooooRooOO.......",
    ".......oooooowooooOO........",
    ".......oooowwAwwooOO........",
    ".......ooowwwwwwwoOO........",
    "........ooowwwwwoOO.........",
    "..........ooowoOO...........",
    "..........oooooOO...........",
    ".........ooooooooo...OO.....",
    "........ooooooooooo...OO....",
    ".......ooooooooooooo...OO...",
    ".......sssooowoooooo...OO...",
    ".......oooowwwwwoooo....OO..",
    "......sssowwwwwwwccco...OO..",
    ".......ooowwwwwwwccc....OO..",
    ".......oowwwwwwwwwcc...OO...",
    ".......ooowwwwwwwooo..OO....",
    "........oowwwwwwwoo..OO.....",
    ".........OwwwwwwwO..........",
    "..........oooooOO...........",
]

# Lying down, for the capped/wake sequence. L is the visible (near)
# eye; 1-5 mark the three tail-sweep poses (see FLOPPED_FRAME_SPECS).
FLOPPED_TEMPLATE: List[str] = [
    "............................",
    "............................",
    "............................",
    "....oo......oo..............",
    "...oopo...opoo..............",
    "...oopooooopoo..............",
    "...ooooooooooo..............",
    "...oooosssoooo..............",
    "..ooooooooooooo.............",
    "..oossooooossoo.............",
    "..ooooooooooooo.............",
    "..oooLooookkooo.............",
    "..oooooonoooooo........1..23",
    "..oooowwwwwoooo........11223",
    "...ooowwwwwooooossoooo..1453",
    "....ooowwwooooooooossoo.1453",
    ".....oOOOOOoooooooooooss.4oo",
    "......OOOOOooooooooOoooo..oo",
    "...wwoooooooooooooOooooo..oo",
    "..wwooooooooowwwwwOoccooooo.",
    "...........owwwwwwOccccooo..",
    "..wwoooooooowwwwwwwOoccoo...",
    "..wwoooooooowwwwwwwoooooooww",
    "........................ooww",
    "............................",
    "............................",
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

# The flopped tail wags through three 2px-thick positions. Template
# markers: "1"/"2"/"3" = cells unique to sweep pose 1/2/3; "4" = cells
# shared by poses 1+2; "5" = shared by poses 2+3. Each frame paints its
# pose's cells with coat color and blanks the rest.
_TAIL_POSE_1 = {"1": "o", "4": "o", "2": ".", "5": ".", "3": "."}
_TAIL_POSE_2 = {"2": "o", "4": "o", "5": "o", "1": ".", "3": "."}
_TAIL_POSE_3 = {"3": "o", "5": "o", "1": ".", "2": ".", "4": "."}

FLOPPED_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    # pose 2 repeats after pose 3 so the wag ping-pongs smoothly
    # (left, up, right, up, left, ...) instead of snapping right-to-left.
    "flopped": [
        {"L": "k", **_TAIL_POSE_1},
        {"L": "k", **_TAIL_POSE_2},
        {"L": "k", **_TAIL_POSE_3},
        {"L": "k", **_TAIL_POSE_2},
    ],
    "stirring": [
        {"L": "k", **_TAIL_POSE_2},
        {"L": "e", **_TAIL_POSE_3},
    ],
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
