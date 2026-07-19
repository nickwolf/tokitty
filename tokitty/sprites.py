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
    "G": "#5a4632",  # ground line -- fixed earth tone, never varies with coat
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
    # Cool blue-gray, pushed bluer/lighter than the laptop chassis
    # ("g" #9aa0ac) so cat and prop never merge in the working pose.
    "gray_tabby": {
        "o": "#a4aec2",  # coat
        "O": "#818ba0",  # coat shading
        "s": "#5f6879",  # tabby stripe -- clearly darker, stripes read
        "c": "#a4aec2",  # patch region matches coat
        "p": "#e3a9ba",  # inner ear, slightly cooled pink
    },
    # Dark charcoal with a violet cast -- visibly lighter than the
    # k outline (#2b1a12) so edges and closed-eye lines stay readable.
    "black": {
        "o": "#4a4653",  # coat
        "O": "#38343f",  # coat shading
        "s": "#575263",  # tone-on-tone sheen stripe (subtle)
        "c": "#4a4653",  # patch region matches coat
        "p": "#a8798c",  # inner ear, muted rose against dark fur
    },
    # Warm off-white; shading carries the silhouette on the dark card
    # (muzzle "w" #fff6ec melting into the coat is the point).
    "white": {
        "o": "#f1ebdf",  # coat
        "O": "#c4bcae",  # coat shading -- strong enough to hold the pose
        "s": "#ded6c6",  # tone-on-tone shade (subtle)
        "c": "#f1ebdf",  # patch region matches coat
        "p": "#f6b8c8",  # inner ear
    },
    # Classic tricolor: white-ish base, orange patches on "c", and the
    # stripe region repurposed as near-black patches for the third color.
    "calico": {
        "o": "#f1ebdf",  # white base coat
        "O": "#c4bcae",  # base shading
        "s": "#453a33",  # dark patches (warm near-black, above outline)
        "c": "#e8823c",  # orange patches (matches the tabby's coat)
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
    # (one column of margin at the left edge, like every other row)
    (14, 1): "g", (14, 2): "g", (14, 3): "g", (14, 4): "g", (14, 5): "g",
    (14, 6): "g", (14, 7): "g", (14, 8): "g",
    (15, 1): "g", (15, 2): "S", (15, 3): "S", (15, 4): "S", (15, 5): "S",
    (15, 6): "S", (15, 7): "S", (15, 8): "g",
    (16, 1): "g", (16, 2): "S", (16, 3): "S", (16, 4): "S", (16, 5): "S",
    (16, 6): "S", (16, 7): "S", (16, 8): "g",
    (17, 2): "g", (17, 3): "S", (17, 4): "S", (17, 5): "S", (17, 6): "S",
    (17, 7): "S", (17, 8): "S", (17, 9): "g",
    (18, 2): "g", (18, 3): "S", (18, 4): "S", (18, 5): "S", (18, 6): "S",
    (18, 7): "S", (18, 8): "S", (18, 9): "g",
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

# Contemplative pose -- SITTING_TEMPLATE with a thought bubble above
# the head. "t" is the small seed dot (lit in BOTH frames, so neither
# frame is ever pixel-identical to idle); "T" cells extend it into a
# larger bubble plus an ascending trail dot in the second frame.
THINKING_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    # seed dot, always on
    (1, 13): "t", (1, 14): "t",
    # bubble growth + trail dot toward the head, frame 2 only
    # (avoids (2,13), an existing head-tuft pixel)
    (0, 14): "T", (0, 15): "T", (0, 16): "T",
    (1, 15): "T", (1, 16): "T",
    (2, 14): "T", (2, 15): "T", (2, 16): "T",
    (2, 12): "T",
})

# Flag-waving pose -- ALERT_TEMPLATE (upright, attentive) with a thick
# pole down the right edge and a LARGE solid pennant. "8" is the pole
# (always on, two cells wide so it reads at real size); "6" is the
# raised pennant (rows 0-4, flying high off the pole top) and "7" the
# dropped pennant (rows 5-9, sagging down the pole) -- the two never
# share cells, so the whole flag visibly snaps between positions.
PERMISSION_TEMPLATE: List[str] = _overlay(ALERT_TEMPLATE, {
    # pole: tall, unbroken, two cells thick, held by the cat -- the
    # lower shaft slants in toward the flank and a coat-colored arm
    # (literal "o" cells, rows 11-12) grips it, so it reads "cat
    # holding flag", not "flag planted nearby"
    (0, 25): "8", (0, 26): "8", (1, 25): "8", (1, 26): "8",
    (2, 25): "8", (2, 26): "8", (3, 25): "8", (3, 26): "8",
    (4, 25): "8", (4, 26): "8", (5, 25): "8", (5, 26): "8",
    (6, 25): "8", (6, 26): "8", (7, 25): "8", (7, 26): "8",
    (8, 25): "8", (8, 26): "8", (9, 25): "8", (9, 26): "8",
    (10, 25): "8", (10, 26): "8", (11, 25): "8", (11, 26): "8",
    (12, 25): "8", (12, 26): "8", (13, 25): "8", (13, 26): "8",
    # lower shaft: single-width, stepping inward to the cat's side
    (14, 25): "8", (15, 24): "8", (16, 24): "8",
    (17, 23): "8", (18, 23): "8", (19, 22): "8",
    # arm + paw gripping the pole
    (11, 22): "o", (11, 23): "o", (11, 24): "o",
    (12, 17): "o", (12, 18): "o", (12, 19): "o", (12, 20): "o",
    (12, 21): "o", (12, 22): "o", (12, 23): "o", (12, 24): "o",
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

def _shift_up(template: List[str], n: int) -> List[str]:
    """Translate a template up by `n` rows, backfilling blank rows.

    The top `n` source rows fall off the grid; callers pick shifts whose
    lost rows are blank or near-blank (SITTING loses only two 1px ear
    tips at n=2, which reads as hop squash rather than damage).
    """
    blank = "." * len(template[0])
    return template[n:] + [blank] * n


# Happy completion hop -- two templates, one per frame, so the WHOLE
# cat translates upward when airborne instead of just losing its feet.
# Both share a persistent ground line (literal "G") at the bottom row;
# the airborne frame is the sitting cat shifted up two rows, leaving a
# real clearance gap above the unbroken ground, with bright motion
# dashes (literal "B") in the gap under the paws. "G" is a dedicated
# non-coat char (BASE_PALETTE) so the earth never recolors with the coat.
DONE_HOP_GROUNDED_TEMPLATE: List[str] = _overlay(SITTING_TEMPLATE, {
    # ground line flanking the seated cat
    (25, 3): "G", (25, 4): "G", (25, 5): "G", (25, 6): "G", (25, 7): "G",
    (25, 8): "G",
    (25, 18): "G", (25, 19): "G", (25, 20): "G", (25, 21): "G",
    (25, 22): "G", (25, 23): "G", (25, 24): "G",
})

DONE_HOP_AIRBORNE_TEMPLATE: List[str] = _overlay(
    _shift_up(SITTING_TEMPLATE, 2), {
        # unbroken ground line: the cat now ends at row 23, so rows
        # 24-25 read as clear air between paws and ground
        (25, 3): "G", (25, 4): "G", (25, 5): "G", (25, 6): "G",
        (25, 7): "G", (25, 8): "G", (25, 9): "G", (25, 10): "G",
        (25, 11): "G", (25, 12): "G", (25, 13): "G", (25, 14): "G",
        (25, 15): "G", (25, 16): "G", (25, 17): "G", (25, 18): "G",
        (25, 19): "G", (25, 20): "G", (25, 21): "G", (25, 22): "G",
        (25, 23): "G", (25, 24): "G",
        # bright motion dashes in the clearance gap under the paws
        (24, 9): "B", (24, 10): "B", (24, 15): "B", (24, 16): "B",
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
        # typing pulse: lidded eyes, looking down at the bright screen
        {"L": "k", "R": "k", "A": "n", "S": "B", "P": "w"},
    ],
}

THINKING_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "thinking": [
        {"L": "e", "R": "e", "A": "n", "t": "?", "T": "."},
        # bubble grows, one eye squints, nose flashes the accent
        {"L": "e", "R": "k", "A": "?", "t": "?", "T": "?"},
    ],
}

PERMISSION_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "permission": [
        {"L": "e", "R": "e", "A": "!", "8": "d", "6": "f", "7": "."},
        {"L": "e", "R": "e", "A": "!", "8": "d", "6": ".", "7": "f"},
    ],
}

# done_hop pairs a template WITH each frame's substitutions -- the only
# state whose two frames use different base grids (whole-cat translate).
DONE_HOP_FRAME_SPECS: Dict[str, List[Dict[str, str]]] = {
    "done_hop": [
        {"L": "e", "R": "e", "A": "h"},  # grounded
        {"L": "e", "R": "e", "A": "h"},  # airborne (shifted template)
    ],
}

DONE_HOP_FRAME_TEMPLATES: List[List[str]] = [
    DONE_HOP_GROUNDED_TEMPLATE,
    DONE_HOP_AIRBORNE_TEMPLATE,
]

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
        # per-frame templates: the airborne frame translates the cat
        return [
            _apply(tmpl, subs)
            for tmpl, subs in zip(
                DONE_HOP_FRAME_TEMPLATES, DONE_HOP_FRAME_SPECS[state]
            )
        ]
    else:
        raise KeyError(f"Unknown sprite state: {state!r}")

    return [_apply(template, subs) for subs in specs]
