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
    "f": "#ff3fa4",  # flag cloth -- hot magenta, distinct from every accent
    "d": "#c9a15a",  # flag pole -- light wood, reads against dark backdrops
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


# Sitting-at-laptop pose -- SITTING_TEMPLATE with an open laptop in
# three-quarter profile stamped IN FRONT of the cat (the stamps
# deliberately occlude the cat's left flank and lower chest). The
# screen is a wide slanted slab -- landscape glow area, tilted back
# away from the cat -- and the keyboard deck is much wider than the
# screen is tall, so the silhouette reads laptop, not phone. "S" is
# the glow (dim/bright per frame); "P" cells on the deck are where the
# paws land for the typing frame.
WORKING_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    # screen slab: top bezel, glow rows slanting toward the hinge
    (14, 0): "g", (14, 1): "g", (14, 2): "g", (14, 3): "g", (14, 4): "g",
    (14, 5): "g", (14, 6): "g", (14, 7): "g",
    (15, 0): "g", (15, 1): "S", (15, 2): "S", (15, 3): "S", (15, 4): "S",
    (15, 5): "S", (15, 6): "S", (15, 7): "g",
    (16, 0): "g", (16, 1): "S", (16, 2): "S", (16, 3): "S", (16, 4): "S",
    (16, 5): "S", (16, 6): "S", (16, 7): "g",
    (17, 1): "g", (17, 2): "S", (17, 3): "S", (17, 4): "S", (17, 5): "S",
    (17, 6): "S", (17, 7): "S", (17, 8): "g",
    (18, 1): "g", (18, 2): "S", (18, 3): "S", (18, 4): "S", (18, 5): "S",
    (18, 6): "S", (18, 7): "S", (18, 8): "g",
    (19, 2): "g", (19, 3): "S", (19, 4): "S", (19, 5): "S", (19, 6): "S",
    (19, 7): "S", (19, 8): "S", (19, 9): "g",
    (20, 2): "g", (20, 3): "g", (20, 4): "g", (20, 5): "g", (20, 6): "g",
    (20, 7): "g", (20, 8): "g", (20, 9): "g",
    # hinge + wide keyboard deck running under the cat's front paws
    (21, 3): "g", (21, 4): "g", (21, 5): "g", (21, 6): "g", (21, 7): "g",
    (21, 8): "g", (21, 9): "g", (21, 10): "g", (21, 11): "g", (21, 12): "g",
    (21, 13): "g", (21, 14): "g", (21, 15): "g", (21, 16): "g",
    (22, 2): "g", (22, 3): "g", (22, 4): "g", (22, 5): "g", (22, 6): "g",
    (22, 7): "g", (22, 8): "g", (22, 9): "P", (22, 10): "P", (22, 11): "P",
    (22, 12): "P", (22, 13): "g", (22, 14): "g", (22, 15): "g", (22, 16): "g",
    # base front edge
    (23, 3): "g", (23, 4): "g", (23, 5): "g", (23, 6): "g", (23, 7): "g",
    (23, 8): "g", (23, 9): "g", (23, 10): "g", (23, 11): "g", (23, 12): "g",
    (23, 13): "g", (23, 14): "g", (23, 15): "g",
})

# Contemplative pose -- SITTING_TEMPLATE with a small thought-dot accent
# floating above the head. "T" toggles on/off with the nose accent.
THINKING_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    (0, 13): "T", (0, 14): "T",
    (1, 13): "T", (1, 14): "T",
})

# Flag-waving pose -- ALERT_TEMPLATE (upright, attentive) with a thick
# pole down the right edge and a LARGE solid pennant. "8" is the pole
# (always on, two cells wide so it reads at real size); "6" is the
# raised pennant (rows 0-4, flying high off the pole top) and "7" the
# dropped pennant (rows 5-9, sagging down the pole) -- the two never
# share cells, so the whole flag visibly snaps between positions.
PERMISSION_TEMPLATE: List[str] = _overlay(ALERT_TEMPLATE, {
    # pole: tall, unbroken, two cells thick
    (0, 25): "8", (0, 26): "8", (1, 25): "8", (1, 26): "8",
    (2, 25): "8", (2, 26): "8", (3, 25): "8", (3, 26): "8",
    (4, 25): "8", (4, 26): "8", (5, 25): "8", (5, 26): "8",
    (6, 25): "8", (6, 26): "8", (7, 25): "8", (7, 26): "8",
    (8, 25): "8", (8, 26): "8", (9, 25): "8", (9, 26): "8",
    (10, 25): "8", (10, 26): "8", (11, 25): "8", (11, 26): "8",
    (12, 25): "8", (12, 26): "8", (13, 25): "8", (13, 26): "8",
    # raised pennant (flies high and wide off the pole top)
    (0, 19): "6", (0, 20): "6", (0, 21): "6", (0, 22): "6", (0, 23): "6",
    (0, 24): "6",
    (1, 19): "6", (1, 20): "6", (1, 21): "6", (1, 22): "6", (1, 23): "6",
    (1, 24): "6",
    (2, 20): "6", (2, 21): "6", (2, 22): "6", (2, 23): "6", (2, 24): "6",
    (3, 21): "6", (3, 22): "6", (3, 23): "6", (3, 24): "6",
    (4, 22): "6", (4, 23): "6", (4, 24): "6",
    # dropped pennant (sags down the pole, widening as it falls)
    (5, 22): "7", (5, 23): "7", (5, 24): "7",
    (6, 21): "7", (6, 22): "7", (6, 23): "7", (6, 24): "7",
    (7, 21): "7", (7, 22): "7", (7, 23): "7", (7, 24): "7",
    (8, 20): "7", (8, 21): "7", (8, 22): "7", (8, 23): "7", (8, 24): "7",
    (9, 20): "7", (9, 21): "7", (9, 22): "7", (9, 23): "7", (9, 24): "7",
})

# Happy completion hop -- SITTING_TEMPLATE with a persistent ground
# line (literal "s" cells outside the cat plus "2" markers under it)
# and the cat's bottom THREE rows marked for lift-off: "9" (white
# cells), "0" (coat cells) and "1" (shaded cells) all vanish in the
# airborne frame, while the "2" cells become ground color -- leaving a
# clear ground-clearance gap between the lifted cat and the unbroken
# ground line. "M" motion-dash clusters only show mid-air.
DONE_HOP_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    # rows 23-24: cat bottom, blanked when airborne
    (23, 7): "0", (23, 8): "0", (23, 9): "0",
    (23, 10): "9", (23, 11): "9", (23, 12): "9", (23, 13): "9",
    (23, 14): "9", (23, 15): "9", (23, 16): "9",
    (23, 17): "0", (23, 18): "0", (23, 19): "0",
    (23, 21): "1", (23, 22): "1",
    (24, 8): "0", (24, 9): "0",
    (24, 10): "9", (24, 11): "9", (24, 12): "9", (24, 13): "9",
    (24, 14): "9", (24, 15): "9", (24, 16): "9",
    (24, 17): "1", (24, 18): "1",
    # row 25: cat rim when grounded, becomes ground line when airborne
    (25, 9): "2", (25, 10): "2", (25, 11): "2", (25, 12): "2",
    (25, 13): "2", (25, 14): "2", (25, 15): "2", (25, 16): "2",
    (25, 17): "2",
    # persistent ground line flanking the cat (both frames)
    (25, 3): "s", (25, 4): "s", (25, 5): "s", (25, 6): "s", (25, 7): "s",
    (25, 8): "s",
    (25, 18): "s", (25, 19): "s", (25, 20): "s", (25, 21): "s",
    (25, 22): "s", (25, 23): "s", (25, 24): "s",
    # motion dashes: flanks plus the clearance gap under the paws
    (20, 3): "M", (20, 4): "M", (21, 3): "M", (21, 4): "M",
    (20, 22): "M", (20, 23): "M", (21, 21): "M", (21, 22): "M",
    (24, 4): "M", (24, 5): "M", (24, 20): "M", (24, 21): "M",
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
        # grounded: full cat, ground line only at the flanks
        {"L": "e", "R": "e", "A": "h",
         "9": "w", "0": "o", "1": "O", "2": "o", "M": "."},
        # airborne: bottom rows lift away, ground line runs unbroken,
        # motion dashes flare in the gap and at the flanks
        {"L": "e", "R": "e", "A": "h",
         "9": ".", "0": ".", "1": ".", "2": "s", "M": "h"},
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
