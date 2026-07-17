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
    # Prop colors (task 8 activity sprites). Non-coat: props must not
    # change with coat, so these live in BASE_PALETTE, not COATS.
    "g": "#9aa0ac",  # laptop chassis
    "b": "#7fd8f0",  # laptop screen glow, dim
    "B": "#c8f2ff",  # laptop screen glow, bright (typing pulse)
    "f": "#e0479e",  # flag cloth -- distinct from every existing accent
    "d": "#8a6a3c",  # flag pole
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

def _overlay(template: List[str], patches: Dict[tuple, str]) -> List[str]:
    """Copy `template`, stamping single characters at (row, col) coords.

    Used to derive new pose templates (props alongside the established
    cat) from the existing SITTING/ALERT templates without hand-retyping
    all 26 rows -- the props land in cells that are transparent ('.') in
    the source template, so the cat silhouette is untouched.
    """
    rows = [list(r) for r in template]
    for (r, c), ch in patches.items():
        rows[r][c] = ch
    return ["".join(r) for r in rows]


# Sitting-at-laptop pose -- SITTING_TEMPLATE with a laptop stamped into
# the free space beside the cat's near paw. "S" cells are the screen
# glow, "P" cells the keys/paw that animate for the typing frame.
WORKING_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    (12, 0): "g", (12, 1): "g", (12, 2): "g", (12, 3): "g", (12, 4): "g",
    (13, 0): "g", (13, 1): "S", (13, 2): "S", (13, 3): "S", (13, 4): "g",
    (14, 0): "g", (14, 1): "S", (14, 2): "S", (14, 3): "S", (14, 4): "g",
    (15, 0): "g", (15, 1): "g", (15, 2): "g", (15, 3): "g", (15, 4): "g",
    (16, 0): "g", (16, 1): "g", (16, 2): "g", (16, 3): "g", (16, 4): "g", (16, 5): "g",
    (17, 0): "g", (17, 1): "P", (17, 2): "P", (17, 3): "g", (17, 4): "P", (17, 5): "g",
    (18, 0): "g", (18, 1): "g", (18, 2): "g", (18, 3): "g", (18, 4): "g", (18, 5): "g",
})

# Contemplative pose -- SITTING_TEMPLATE with a small thought-dot accent
# floating above the head. "T" toggles on/off with the nose accent.
THINKING_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    (0, 13): "T", (0, 14): "T",
    (1, 13): "T", (1, 14): "T",
})

# Flag-waving pose -- ALERT_TEMPLATE (upright, attentive) with a pole and
# waving cloth stamped above/right of the head. "8" is the pole (always
# on); "6"/"7" are cloth cells unique to the up/down wave positions, the
# same unique-region trick FLOPPED_TEMPLATE uses for its tail sweep.
PERMISSION_TEMPLATE: List[str] = _overlay(ALERT_TEMPLATE, {
    (2, 25): "8", (3, 25): "8", (4, 25): "8", (5, 25): "8",
    (6, 25): "8", (7, 25): "8", (8, 25): "8", (9, 25): "8",
    (0, 22): "6", (0, 23): "6", (0, 24): "6", (0, 25): "6",
    (1, 24): "6", (1, 25): "6",
    (1, 23): "7", (1, 24): "7",
    (2, 23): "7", (2, 24): "7", (2, 25): "7",
})

# Happy completion hop -- SITTING_TEMPLATE with the paw-tip rows marked
# for lift-off ("9": present when grounded, blanked when airborne) and
# motion-sparkle clusters ("M") that only show mid-air, on both flanks.
DONE_HOP_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    (24, 10): "9", (24, 11): "9", (24, 12): "9", (24, 13): "9",
    (24, 14): "9", (24, 15): "9", (24, 16): "9",
    (25, 10): "9", (25, 11): "9", (25, 12): "9", (25, 13): "9",
    (25, 14): "9", (25, 15): "9",
    (20, 3): "M", (20, 4): "M", (21, 3): "M", (21, 4): "M",
    (20, 22): "M", (20, 23): "M", (21, 21): "M", (21, 22): "M",
})

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

WORKING_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "working": [
        {"L": "e", "R": "e", "A": "n", "S": "b", "P": "g"},
        {"L": "e", "R": "k", "A": "n", "S": "B", "P": "w"},
    ],
}

THINKING_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "thinking": [
        {"L": "e", "R": "e", "A": "n", "T": "."},
        {"L": "e", "R": "e", "A": "?", "T": "?"},
    ],
}

PERMISSION_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "permission": [
        {"L": "e", "R": "e", "A": "!", "8": "d", "6": "f", "7": "."},
        {"L": "e", "R": "e", "A": "!", "8": "d", "6": ".", "7": "f"},
    ],
}

DONE_HOP_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "done_hop": [
        {"L": "e", "R": "e", "A": "h", "9": "w", "M": "."},
        {"L": "e", "R": "e", "A": "h", "9": ".", "M": "h"},
    ],
}

ALL_STATES = (
    tuple(SITTING_FRAME_SPECS.keys())
    + tuple(ALERT_FRAME_SPECS.keys())
    + tuple(FLOPPED_FRAME_SPECS.keys())
    + tuple(WORKING_FRAME_SPECS.keys())
    + tuple(THINKING_FRAME_SPECS.keys())
    + tuple(PERMISSION_FRAME_SPECS.keys())
    + tuple(DONE_HOP_FRAME_SPECS.keys())
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
    elif state in WORKING_FRAME_SPECS:
        template, specs = WORKING_TEMPLATE, WORKING_FRAME_SPECS[state]
    elif state in THINKING_FRAME_SPECS:
        template, specs = THINKING_TEMPLATE, THINKING_FRAME_SPECS[state]
    elif state in PERMISSION_FRAME_SPECS:
        template, specs = PERMISSION_TEMPLATE, PERMISSION_FRAME_SPECS[state]
    elif state in DONE_HOP_FRAME_SPECS:
        template, specs = DONE_HOP_TEMPLATE, DONE_HOP_FRAME_SPECS[state]
    else:
        raise KeyError(f"Unknown sprite state: {state!r}")

    return [_apply(template, subs) for subs in specs]
