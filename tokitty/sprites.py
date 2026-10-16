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

Animation model (the standard for every state): an Animation is a template
plus independent TRACKS ("main", "tail", "eyes", "ears", ...). A track is a
list of Frames, each with a substitution dict and a hold in ms (a (lo, hi)
range is a random hold drawn each time the frame is shown), and its own
loop_from (None plays once and holds the last frame). Each track runs on its
own clock; the grid on screen is the template with the substitutions of
every track's current frame merged. Tracks must use disjoint substitution
keys, so composition order never matters, and at most one track may swap the
template (Frame.template). check_animation enforces the rules, and the tests
run it over the whole table. A state with one track named "main" behaves as
the old linear frame list did.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

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
    # change with coat, so these live in BASE_PALETTE, not the coat presets.
    "g": "#9aa0ac",  # laptop chassis
    "b": "#7fd8f0",  # laptop screen glow, dim
    "B": "#c8f2ff",  # laptop screen glow, bright (typing pulse)
    "f": "#ff3fa4",  # flag cloth -- hot magenta, distinct from every accent
    "d": "#c9a15a",  # flag pole -- light wood, reads against dark backdrops
    "G": "#5a4632",  # ground line -- fixed earth tone, never varies with coat
}

# --- colorway x pattern model (replaces the old bundled coat presets) ---
# A colorway is a tone palette; a pattern maps each coat-driven char to a
# tone token ("coat"/"shade"/"mark"/"light"/"white") or a literal #rrggbb.
COLORWAYS: Dict[str, Dict[str, str]] = {
    "orange": {"coat": "#e8823c", "shade": "#c26a2c", "mark": "#a8541f", "light": "#f7e0c0", "ear": "#f6b8c8"},
    "gray":   {"coat": "#a4aec2", "shade": "#818ba0", "mark": "#5f6879", "light": "#e2e7f0", "ear": "#e3a9ba"},
    "black":  {"coat": "#4a4653", "shade": "#38343f", "mark": "#575263", "light": "#cfccd9", "ear": "#a8798c"},
    "white":  {"coat": "#f1ebdf", "shade": "#c4bcae", "mark": "#ded6c6", "light": "#f8f4ec", "ear": "#f6b8c8"},
    "cream":  {"coat": "#e9d9bd", "shade": "#cdb894", "mark": "#a98d63", "light": "#f7efdd", "ear": "#f0c4cf"},
    "brown":  {"coat": "#8a5a3c", "shade": "#6d452d", "mark": "#4f3020", "light": "#d9c4ab", "ear": "#d69aa6"},
}

# Which template chars a pattern controls: the base fill/shade plus the body
# regions -- m paws, x points (outer ears + face mask), y tail, u belly, and
# Y the tail's alternate bands (#43). Closed set: every PATTERNS entry must
# define exactly these keys. Y takes y's tone in every pattern but ringed, so
# a banded tail looks exactly like a plain one.
REGION_CHARS: Tuple[str, ...] = ("o", "O", "s", "c", "m", "x", "y", "u", "Y")

PATTERNS: Dict[str, Dict[str, str]] = {
    "solid":       {"o": "coat", "O": "shade", "s": "coat", "c": "coat",
                    "m": "coat", "x": "coat", "y": "shade", "u": "coat", "Y": "shade"},
    "tabby":       {"o": "coat", "O": "shade", "s": "mark", "c": "coat",
                    "m": "white", "x": "coat", "y": "shade", "u": "white", "Y": "shade"},
    "bicolor":     {"o": "coat", "O": "shade", "s": "coat", "c": "white",
                    "m": "white", "x": "coat", "y": "shade", "u": "white", "Y": "shade"},
    "tabby_white": {"o": "coat", "O": "shade", "s": "mark", "c": "white",
                    "m": "white", "x": "coat", "y": "shade", "u": "white", "Y": "shade"},
    "calico":      {"o": "coat", "O": "shade", "s": "#453a33", "c": "#e8823c",
                    "m": "white", "x": "coat", "y": "shade", "u": "white", "Y": "shade"},
    # Silhouette patterns -- these use the new regions to change the cat's
    # read, not just tint it. colorpoint/van pale the body toward the
    # colorway's `light` tone, so they only sing on light colorways.
    "tuxedo":      {"o": "coat", "O": "shade", "s": "coat", "c": "white",
                    "m": "white", "x": "coat", "y": "shade", "u": "white", "Y": "shade"},
    "socks":       {"o": "coat", "O": "shade", "s": "coat", "c": "coat",
                    "m": "white", "x": "coat", "y": "shade", "u": "coat", "Y": "shade"},
    "colorpoint":  {"o": "light", "O": "light", "s": "light", "c": "light",
                    "m": "mark", "x": "mark", "y": "mark", "u": "light", "Y": "mark"},
    "van":         {"o": "white", "O": "shade", "s": "white", "c": "white",
                    "m": "white", "x": "coat", "y": "coat", "u": "white", "Y": "coat"},
}

# Old bundled coat names -> (colorway, pattern). Legacy black/white carried a
# subtle tone-on-tone sheen (a mark-tone stripe), so they map to +tabby, not
# +solid, to reproduce that exactly; users can switch to solid for a flat look.
LEGACY_COAT_MAP: Dict[str, Tuple[str, str]] = {
    "orange_tabby": ("orange", "tabby"),
    "gray_tabby":   ("gray", "tabby"),
    "black":        ("black", "tabby"),
    "white":        ("white", "tabby"),
    "calico":       ("white", "calico"),
}


def resolve_palette(colorway: str = "orange", pattern: str = "tabby") -> Dict[str, str]:
    """Full char->color map for one colorway x pattern. The colorway supplies
    the tone slots; the pattern maps each coat-driven char to a tone token or a
    literal hex. Every other char is BASE_PALETTE unchanged."""
    cw = COLORWAYS[colorway]
    tones = {
        "coat": cw["coat"], "shade": cw["shade"], "mark": cw["mark"],
        "light": cw["light"], "white": BASE_PALETTE["w"],
    }
    merged = dict(BASE_PALETTE)
    merged["p"] = cw["ear"]
    for char, src in PATTERNS[pattern].items():
        merged[char] = tones.get(src, src)  # tone token -> tone, else literal hex
    merged[LID_CHAR] = lid_tone(merged["o"])
    return merged


# Shut eyes are drawn in the fur colour darkened, not the outline black,
# which read as harsh on every coat. Derived from "o", so a coat override
# has to re-derive it (customize.effective_palette does).
LID_CHAR = "-"
LID_DARKEN = 0.5


def lid_tone(fur: str) -> str:
    r, g, b = (int(fur[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(v * (1 - LID_DARKEN)) for v in (r, g, b))


def get_palette(coat: str = "orange_tabby") -> Dict[str, str]:
    """Legacy shim: a bundled coat name -> resolve_palette. New code calls
    resolve_palette(colorway, pattern) directly. Kept so scripts and any other
    legacy callers keep working until they migrate."""
    return resolve_palette(*LEGACY_COAT_MAP[coat])


PALETTE: Dict[str, str] = resolve_palette("orange", "tabby")

# Sitting, calm pose -- ears relaxed. L/R are the eye cells, A is a
# single accent cell (nose by default, repainted per state).
SITTING_TEMPLATE: List[str] = [
    "............................",
    "........x.........x.........",
    ".......xxx...o...xxx........",
    "......xxxxoooooooxxxx.......",
    "......xxpxxsssssxxpxx.......",
    ".....ooocccoooooooooOO......",
    ".......occsssosssoOO........",
    ".......oooooooooooOO........",
    "......ooooLxxxxxRooOO.......",
    ".......ooooxxwxxooOO........",
    ".......ooxxwwAwwxxOO........",
    ".......oxxwwwwwwwxOO........",
    "........oxxwwwwwxOO.........",
    ".........ooxxwxxOO..........",
    "........ooooooooooo..YY.....",
    ".......ooooooooooooo..YY....",
    "......ooooooooooooooo..yy...",
    "......osssooowooooooo..yy...",
    "......ooooouuuuuooooo...YY..",
    ".....osssouuuuuuucccoo..YY..",
    "......oooouuuuuuuccco...yy..",
    "......ooouuuuuuuuucco..yy...",
    "......oooouuuuuuuooooYYY....",
    ".......ooouuuuuuuoooYYY.....",
    "........oommmmmmmOO.........",
    ".........oooooooOO..........",
]

# Sitting, alert pose -- ears sharply perked, upright posture. Same
# placeholder cells as SITTING_TEMPLATE.
ALERT_TEMPLATE: List[str] = [
    "........x.........x.........",
    "........x....o....x.........",
    ".......xpooooooooopx........",
    "......xxpxxsssssxxpxx.......",
    "......xxcccoooooooxxo.......",
    ".......occsssosssoOO........",
    ".......oooooooooooOO........",
    "......ooooLxxxxxRooOO.......",
    ".......ooooxxwxxooOO........",
    ".......ooxxwwAwwxxOO........",
    ".......oxxwwwwwwwxOO........",
    "........oxxwwwwwxOO.........",
    "..........ooowoOO...........",
    "..........oooooOO...........",
    ".........ooooooooo...YY.....",
    "........ooooooooooo...YY....",
    ".......ooooooooooooo...yy...",
    ".......sssooowoooooo...yy...",
    ".......oooouuuuuoooo....YY..",
    "......sssouuuuuuuccco...YY..",
    ".......ooouuuuuuuccc....yy..",
    ".......oouuuuuuuuucc...yy...",
    ".......ooouuuuuuuoooYYY.....",
    "........oouuuuuuuooYYY......",
    ".........OmmmmmmmO..........",
    "..........oooooOO...........",
]

# Lying down, for the capped/wake sequence. L is the visible (near)
# eye; 1-5 mark the three tail-sweep poses (see the flopped animation).
FLOPPED_TEMPLATE: List[str] = [
    "............................",
    "............................",
    "............................",
    "....xx......xx..............",
    "...xxpx...xpxx..............",
    "...xxpooooopxx..............",
    "...ooooooooooo..............",
    "...oooosssoooo..............",
    "..ooooooooooooo.............",
    "..oossooooossoo.............",
    "..ooooooooooooo.............",
    "..oooLooookkooo.............",
    "..oooxxxnxxxooo........1..23",
    "..ooxxwwwwwxxoo........11223",
    "...oxxwwwwwxxooossoooo..1453",
    "....oxxwwwxxooooooossoo.1453",
    ".....oOOOOOoooooooooooss.4yy",
    "......OOOOOooooooooOoooo..yy",
    "...mmoooooooooooooOooooo.yyy",
    "..mmooooooooouuuuuOoccoooyy.",
    "...........ouuuuuuOccccooy..",
    "..mmoooooooouuuuuuuOoccoo...",
    "..mmoooooooouuuuuuuooooooomm",
    "........................oomm",
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

# Timing. Every frame carries its own hold so art passes can mix fast frames
# (blinks) with slow ones (breathing, long random holds between blinks).
DEFAULT_FRAME_MS = 800  # what every frame held when one global timer drove all states
MIN_FRAME_MS = 40       # shortest hold a frame may ask for
FRAME_MS_STEP = 10      # holds are whole multiples of this

Duration = Union[int, Tuple[int, int]]


@dataclass(frozen=True)
class Frame:
    subs: Mapping[str, str]
    # (lo, hi) is a random hold in [lo, hi], chosen each time the frame is shown.
    ms: Duration = DEFAULT_FRAME_MS
    # Overrides the animation's template for this frame only.
    template: Optional[List[str]] = None


@dataclass(frozen=True)
class Track:
    """One independent strand of an animation ("main", "tail", "eyes", ...),
    with its own clock, holds and loop."""
    name: str
    frames: Tuple[Frame, ...]
    # After the last frame, continue at this index. None plays once and holds
    # the last frame.
    loop_from: Optional[int] = 0


@dataclass(frozen=True)
class Animation:
    template: List[str]
    tracks: Tuple[Track, ...]


def _round_step(ms: float) -> int:
    return int((ms + FRAME_MS_STEP / 2) // FRAME_MS_STEP) * FRAME_MS_STEP


def frame_ms(frame: Frame, rng: Optional[random.Random] = None) -> int:
    """How long to hold a frame. A fixed hold is itself; a (lo, hi) range is
    drawn from rng (rounded to the step, so it stays inside the range), or
    its midpoint when there is no rng."""
    ms = frame.ms
    if isinstance(ms, int):
        return ms
    lo, hi = ms
    if rng is None:
        return _round_step((lo + hi) / 2)
    return min(max(_round_step(rng.randint(lo, hi)), lo), hi)


def frame_ms_max(frame: Frame) -> int:
    """The longest a frame can be held."""
    return frame.ms if isinstance(frame.ms, int) else frame.ms[1]


def single_track(template: List[str], frames: Sequence[Frame], loop_from: Optional[int] = 0,
                 name: str = "main") -> Animation:
    """An animation with one track, the shape every state had before tracks."""
    return Animation(template, (Track(name, tuple(frames), loop_from),))


def _anim(template: List[str], specs: List[Dict[str, str]]) -> Animation:
    return single_track(template, [Frame(subs) for subs in specs])


def check_animation(anim: Animation) -> None:
    """Raise ValueError if an animation breaks the track rules: unique track
    names, holds that are multiples of the step and at least MIN_FRAME_MS
    (lo <= hi for ranges), a valid loop_from per track, template overrides
    with the animation template's dimensions on at most one track, and
    substitution keys that no two tracks share (so the order tracks are
    composed in can never matter)."""
    if not anim.tracks:
        raise ValueError("animation has no tracks")
    names = [t.name for t in anim.tracks]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate track names {names}")
    dims = (len(anim.template), len(anim.template[0]))
    owners: Dict[str, str] = {}
    template_tracks = []
    for track in anim.tracks:
        if not track.frames:
            raise ValueError(f"track {track.name!r} has no frames")
        if track.loop_from is not None and not 0 <= track.loop_from < len(track.frames):
            raise ValueError(f"track {track.name!r}: loop_from {track.loop_from} out of range")
        for frame in track.frames:
            ends = (frame.ms,) if isinstance(frame.ms, int) else tuple(frame.ms)
            if len(ends) == 2 and ends[0] > ends[1]:
                raise ValueError(f"track {track.name!r}: range {frame.ms} has lo > hi")
            for ms in ends:
                if ms < MIN_FRAME_MS or ms % FRAME_MS_STEP:
                    raise ValueError(f"track {track.name!r}: bad hold {ms}")
            for key in frame.subs:
                if owners.setdefault(key, track.name) != track.name:
                    raise ValueError(
                        f"key {key!r} used by tracks {owners[key]!r} and {track.name!r}")
            if frame.template is not None:
                if (len(frame.template), len(frame.template[0])) != dims:
                    raise ValueError(f"track {track.name!r}: template size differs")
                if track.name not in template_tracks:
                    template_tracks.append(track.name)
    if len(template_tracks) > 1:
        raise ValueError(f"tracks {template_tracks} all override the template")


# The flopped tail wags through three 2px-thick positions. Template
# markers: "1"/"2"/"3" = cells unique to sweep pose 1/2/3; "4" = cells
# shared by poses 1+2; "5" = shared by poses 2+3. Each frame paints its
# pose's cells with the tail region char "y" -- so the swept tail takes the
# pattern's tail tone, matching the resting curl -- and blanks the rest.
_TAIL_POSE_1 = {"1": "y", "4": "y", "2": ".", "5": ".", "3": "."}
_TAIL_POSE_2 = {"2": "y", "4": "y", "5": "y", "1": ".", "3": "."}
_TAIL_POSE_3 = {"3": "y", "5": "y", "1": ".", "2": ".", "4": "."}


# Chars _pose_cells may use as markers: any printable ASCII the template and
# palette don't already use.
_MARKER_CHARS = "".join(chr(c) for c in range(33, 127) if chr(c) not in "\"'\\")


def _pose_cells(template: List[str], poses: Sequence[Dict[Tuple[int, int], str]]
                ) -> Tuple[List[str], List[Dict[str, str]]]:
    """Stamp alternative poses of one body part (a tail tip, an ear) into a
    template, for a track to switch between. Each pose maps (row, col) to the
    char it paints there. Every cell any pose touches becomes a marker char
    (one the template and palette don't use yet), one per distinct "what each
    pose paints here" pattern, and
    pose i's subs paint its own cells and blank the rest. Returns the marked
    template and one subs dict per pose."""
    used = set("".join(template)) | set(BASE_PALETTE) | set(REGION_CHARS) | {"p", LID_CHAR}
    pool = [ch for ch in _MARKER_CHARS if ch not in used]
    cells = sorted({cell for pose in poses for cell in pose})
    # A pose that leaves a cell unpainted blanks it, so the cells may only
    # hold the part being replaced, never another part of the cat.
    part = {ch for pose in poses for ch in pose.values()} | {".", "L", "R"}
    clash = [cell for cell in cells if template[cell[0]][cell[1]] not in part]
    if clash:
        raise ValueError(f"pose cells {clash} would erase template pixels")
    patterns = sorted({tuple(pose.get(cell, ".") for pose in poses) for cell in cells})
    if len(patterns) > len(pool):
        raise ValueError(f"{len(patterns)} cell patterns, only {len(pool)} marker chars left")
    marker = dict(zip(patterns, pool))
    marked = _overlay(template, {cell: marker[tuple(pose.get(cell, ".") for pose in poses)]
                                 for cell in cells})
    subs = [{marker[pat]: pat[i] for pat in patterns} for i in range(len(poses))]
    return marked, subs


def _cells(char: str, *runs: Tuple[int, int, int]) -> Dict[Tuple[int, int], str]:
    """(row, first col, last col) runs painted with one char."""
    return {(r, c): char for r, c0, c1 in runs for c in range(c0, c1 + 1)}


def _tail_band(row: int) -> str:
    """The tail char for a row: bands two rows deep alternate y and Y, the
    same rule the sitting and alert templates' tails are drawn with, so a
    moving tip stays in step with the curl it joins."""
    return "Y" if (row // 2) % 2 else "y"


def _tail(*runs: Tuple[int, int, int]) -> Dict[Tuple[int, int], str]:
    """Tail cells as (row, first col, last col) runs, banded by row."""
    return {(r, c): _tail_band(r) for r, c0, c1 in runs for c in range(c0, c1 + 1)}


# The sitting cat's tail tip (rows 13-17; the curl below row 17 never moves).
# Neutral is the resting curl and frame 0, so the icons keep it.
_SIT_TAIL_NEUTRAL = _tail((14, 21, 22), (15, 22, 23), (16, 23, 24), (17, 23, 24))
_SIT_TAIL_IN = _tail((13, 20, 21), (14, 21, 22), (15, 22, 23), (16, 23, 24), (17, 23, 24))
_SIT_TAIL_OUT = _tail((14, 22, 23), (15, 23, 24), (16, 23, 24), (17, 23, 24))
_SIT_TAIL_UP = _tail((14, 24, 25), (15, 24, 25), (16, 24, 25), (17, 24, 25))
_SIT_TAIL_HIGH = _tail(*((r, 24, 25) for r in range(12, 18)))
# Right ear: the tip pixel twitches one cell outward.
_SIT_EAR_REST = _cells("x", (1, 18, 18))
_SIT_EAR_FLICK = _cells("x", (1, 19, 19))

# The sitting cat's eyes. Each eye is one pixel on row 8 between the fur and
# the mask, so a pose repaints the cells around both eyes: _SIT_EYE_CELLS is
# the face with no eyes, and each pose stamps over it. A single dark pixel
# reads as an open dark eye, so shut eyes are a 3px line in the lid tone
# (LID_CHAR), and the happy
# squint of a slow blink is an arch.
_SIT_EYE_CELLS = {(8, 9): "o", (8, 10): "o", (8, 11): "x", (8, 15): "x", (8, 16): "o", (8, 17): "o",
                  (9, 9): "o", (9, 10): "o", (9, 11): "x", (9, 15): "x", (9, 16): "o", (9, 17): "o"}


def _sit_eyes(*cells: Tuple[int, int], char: str = "e") -> Dict[Tuple[int, int], str]:
    return {**_SIT_EYE_CELLS, **{cell: char for cell in cells}}


_SIT_EYES_OPEN = _sit_eyes((8, 10), (8, 16))
_SIT_EYES_LINE = _sit_eyes((8, 9), (8, 10), (8, 11), (8, 15), (8, 16), (8, 17), char=LID_CHAR)
_SIT_EYES_ARCH = _sit_eyes((9, 9), (8, 10), (9, 11), (9, 15), (8, 16), (9, 17), char=LID_CHAR)
_SIT_EYES_LEFT = {**_sit_eyes((8, 9), (8, 15)), (8, 10): "x", (8, 16): "o"}
_SIT_EYES_RIGHT = {**_sit_eyes((8, 11), (8, 17)), (8, 16): "x"}

_CONTENT_TEMPLATE, _CONTENT_TAIL = _pose_cells(
    SITTING_TEMPLATE, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_IN, _SIT_TAIL_OUT, _SIT_TAIL_UP])
_CONTENT_TEMPLATE, _CONTENT_EAR = _pose_cells(
    _CONTENT_TEMPLATE, [_SIT_EAR_REST, _SIT_EAR_FLICK])
_CONTENT_TEMPLATE, _CONTENT_EYES = _pose_cells(
    _CONTENT_TEMPLATE, [_SIT_EYES_OPEN, _SIT_EYES_LINE, _SIT_EYES_ARCH])
_TAIL_NEUTRAL, _TAIL_IN, _TAIL_OUT, _TAIL_UP = _CONTENT_TAIL
_EAR_REST, _EAR_FLICK = _CONTENT_EAR
_NOSE = {"A": "n"}
_EYES_OPEN, _EYES_SHUT, _EYES_ARCH = ({**eyes, **_NOSE} for eyes in _CONTENT_EYES)

CONTENT_ANIMATION = Animation(_CONTENT_TEMPLATE, (
    # Blinks at random gaps, with a quick double blink and a slow blink
    # ("I trust you") in the cycle so it never reads as a metronome.
    Track("eyes", (
        Frame(_EYES_OPEN, (2500, 6000)),
        Frame(_EYES_SHUT, 150),
        Frame(_EYES_OPEN, (3000, 7000)),
        Frame(_EYES_SHUT, 140),
        Frame(_EYES_OPEN, 120),
        Frame(_EYES_SHUT, 140),
        Frame(_EYES_OPEN, (2500, 6000)),
        Frame(_EYES_ARCH, 700),
    )),
    # A lazy sway: up and out, back through neutral, a curl in.
    Track("tail", (
        Frame(_TAIL_NEUTRAL, (800, 2000)),
        Frame(_TAIL_OUT, 220),
        Frame(_TAIL_UP, (600, 1100)),
        Frame(_TAIL_OUT, 220),
        Frame(_TAIL_NEUTRAL, (700, 1600)),
        Frame(_TAIL_IN, (500, 900)),
    )),
    Track("ears", (
        Frame(_EAR_REST, (8000, 20000)),
        Frame(_EAR_FLICK, 100),
        Frame(_EAR_REST, 120),
        Frame(_EAR_FLICK, 100),
    )),
))


# interested: eyes glance left and right (the 1px eye moves a cell, the
# fur or mask fills in behind it), ears perk one at a time, the tail tip
# twitches.
_SIT_EAR_PERK_LEFT = {**_SIT_EAR_REST, **_cells("x", (0, 8, 8))}
_SIT_EAR_PERK_RIGHT = {**_SIT_EAR_REST, **_cells("x", (0, 18, 18))}

_INTERESTED_TEMPLATE, (_INT_TAIL_NEUTRAL, _INT_TAIL_OUT, _INT_TAIL_UP) = _pose_cells(
    SITTING_TEMPLATE, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_OUT, _SIT_TAIL_UP])
_INTERESTED_TEMPLATE, (_INT_AHEAD, _INT_LEFT, _INT_RIGHT, _INT_SHUT) = _pose_cells(
    _INTERESTED_TEMPLATE, [_SIT_EYES_OPEN, _SIT_EYES_LEFT, _SIT_EYES_RIGHT, _SIT_EYES_LINE])
_INTERESTED_TEMPLATE, (_INT_EAR_REST, _INT_EAR_FLICK, _INT_PERK_L, _INT_PERK_R) = _pose_cells(
    _INTERESTED_TEMPLATE, [_SIT_EAR_REST, _SIT_EAR_FLICK, _SIT_EAR_PERK_LEFT, _SIT_EAR_PERK_RIGHT])
INTERESTED_ANIMATION = Animation(_INTERESTED_TEMPLATE, (
    Track("eyes", (
        Frame({**_INT_AHEAD, **_NOSE}, (1500, 3000)),
        Frame({**_INT_LEFT, **_NOSE}, (700, 1400)),
        Frame({**_INT_AHEAD, **_NOSE}, 250),
        Frame({**_INT_RIGHT, **_NOSE}, (700, 1400)),
        Frame({**_INT_AHEAD, **_NOSE}, (1200, 2500)),
        Frame({**_INT_SHUT, **_NOSE}, 140),
    )),
    Track("tail", (
        Frame(_INT_TAIL_NEUTRAL, (1500, 3000)),
        Frame(_INT_TAIL_OUT, 120),
        Frame(_INT_TAIL_NEUTRAL, 120),
        Frame(_INT_TAIL_OUT, 120),
        Frame(_INT_TAIL_NEUTRAL, (1000, 2000)),
        Frame(_INT_TAIL_UP, 160),
        Frame(_INT_TAIL_OUT, 160),
    )),
    Track("ears", (
        Frame(_INT_EAR_REST, (1500, 3500)),
        Frame(_INT_PERK_L, (600, 1200)),
        Frame(_INT_EAR_REST, (800, 1500)),
        Frame(_INT_PERK_R, (600, 1200)),
        Frame(_INT_EAR_REST, (2000, 4000)),
        Frame(_INT_EAR_FLICK, 100),
        Frame(_INT_EAR_REST, 120),
        Frame(_INT_EAR_FLICK, 100),
    )),
))


def _sink_head(template: List[str], neck_row: int = 13, width: int = 21) -> List[str]:
    """The head (rows above neck_row, columns below width) dropped one row
    onto the chest, the sleeping cat's exhale. The columns to the right (the
    tail tip, the Zs) stay where they are."""
    rows = [list(r) for r in template]
    for r in range(neck_row, 0, -1):
        rows[r][:width] = rows[r - 1][:width]
    rows[0][:width] = ["."] * width
    return ["".join(r) for r in rows]


# sleeping: eyes shut but for the odd peek, the head rising and sinking with each breath, Zs
# drifting up off the head, now and then an ear twitch or a lazy tail swish.
_ZZZ_BIG = _cells("z", (0, 22, 26), (1, 25, 25), (2, 24, 24), (3, 23, 23), (4, 22, 26))
_ZZZ_SMALL = _cells("z", (5, 22, 25), (6, 24, 24), (7, 23, 23), (8, 22, 25))
_ZZZ_DOT = _cells("z", (9, 22, 22))

_SLEEP_TEMPLATE, (_SLEEP_TAIL_NEUTRAL, _SLEEP_TAIL_IN, _SLEEP_TAIL_OUT, _SLEEP_TAIL_UP,
                  _SLEEP_TAIL_HIGH) = _pose_cells(
    SITTING_TEMPLATE, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_IN, _SIT_TAIL_OUT, _SIT_TAIL_UP, _SIT_TAIL_HIGH])
_SLEEP_TEMPLATE, (_SLEEP_EAR_REST, _SLEEP_EAR_FLICK) = _pose_cells(
    _SLEEP_TEMPLATE, [_SIT_EAR_REST, _SIT_EAR_FLICK])
# Now and then one eye peeks open, looks left and right ("anyone there?
# no?") and shuts again.
_SLEEP_PEEK = {**_SIT_EYES_LINE, (8, 15): "x", (8, 16): "e", (8, 17): "o"}
_SLEEP_PEEK_LEFT = {**_SIT_EYES_LINE, (8, 15): "e", (8, 16): "o", (8, 17): "o"}
_SLEEP_PEEK_RIGHT = {**_SIT_EYES_LINE, (8, 15): "x", (8, 16): "x", (8, 17): "e"}
_SLEEP_TEMPLATE, (_SLEEP_SHUT, _SLEEP_OPEN, _SLEEP_LOOK_L, _SLEEP_LOOK_R) = _pose_cells(
    _SLEEP_TEMPLATE, [_SIT_EYES_LINE, _SLEEP_PEEK, _SLEEP_PEEK_LEFT, _SLEEP_PEEK_RIGHT])
_SLEEP_TEMPLATE, (_Z_BIG, _Z_NONE, _Z_DOT, _Z_SMALL) = _pose_cells(
    _SLEEP_TEMPLATE, [_ZZZ_BIG, {}, _ZZZ_DOT, _ZZZ_SMALL])
_SLEEP_EXHALE_TEMPLATE = _sink_head(_SLEEP_TEMPLATE)

SLEEPING_ANIMATION = Animation(_SLEEP_TEMPLATE, (
    Track("eyes", (
        Frame({**_SLEEP_SHUT, **_NOSE}, (20000, 45000)),
        Frame({**_SLEEP_OPEN, **_NOSE}, 600),
        Frame({**_SLEEP_LOOK_L, **_NOSE}, 450),
        Frame({**_SLEEP_LOOK_R, **_NOSE}, 450),
        Frame({**_SLEEP_OPEN, **_NOSE}, 300),
    )),
    Track("breath", (
        Frame({}, (1700, 2000)),
        Frame({}, (2000, 2500), template=_SLEEP_EXHALE_TEMPLATE),
    )),
    Track("zzz", (
        Frame(_Z_BIG, 800),
        Frame(_Z_NONE, (1200, 2200)),
        Frame(_Z_DOT, 500),
        Frame(_Z_SMALL, 600),
    )),
    Track("ears", (
        Frame(_SLEEP_EAR_REST, (15000, 40000)),
        Frame(_SLEEP_EAR_FLICK, 100),
        Frame(_SLEEP_EAR_REST, 140),
        Frame(_SLEEP_EAR_FLICK, 100),
    )),
    # A slow, lazy swish: the tail lifts high, hangs there, comes back down
    # and settles into a curl.
    Track("tail", (
        Frame(_SLEEP_TAIL_NEUTRAL, (6000, 15000)),
        Frame(_SLEEP_TAIL_OUT, 300),
        Frame(_SLEEP_TAIL_UP, 300),
        Frame(_SLEEP_TAIL_HIGH, (800, 1400)),
        Frame(_SLEEP_TAIL_UP, 300),
        Frame(_SLEEP_TAIL_OUT, 300),
        Frame(_SLEEP_TAIL_NEUTRAL, (500, 900)),
        Frame(_SLEEP_TAIL_IN, (1200, 2500)),
    )),
))


# ---- the alert family: upright, ears up -----------------------------------
# The alert face is the sitting face one row higher, and the alert tail tip
# (rows 12-17) matches the sitting one, so the sitting poses are reused.


def _shift(pose: Dict[Tuple[int, int], str], dr: int = 0, dc: int = 0) -> Dict[Tuple[int, int], str]:
    return {(r + dr, c + dc): ch for (r, c), ch in pose.items()}


def _glyph(char: str, top: int, left: int, *rows: str) -> Dict[Tuple[int, int], str]:
    """A small picture ("#" = painted) at (top, left), painted with char."""
    return {(top + r, left + c): char
            for r, row in enumerate(rows) for c, cell in enumerate(row) if cell == "#"}


def _shift_template(template: List[str], dc: int) -> List[str]:
    """The whole grid moved dc columns (the edge columns must be empty)."""
    if dc > 0:
        return ["." * dc + row[:-dc] for row in template]
    return [row[-dc:] + "." * -dc for row in template]


_ALERT_EYES_OPEN = _shift(_SIT_EYES_OPEN, -1)
_ALERT_EYES_LINE = _shift(_SIT_EYES_LINE, -1)
_ALERT_EYES_ARCH = _shift(_SIT_EYES_ARCH, -1)
# One eye shut, the other open: left shut, then right shut.
_ALERT_WINK_L = {**_ALERT_EYES_OPEN, (7, 9): LID_CHAR, (7, 10): LID_CHAR, (7, 11): LID_CHAR}
_ALERT_WINK_R = {**_ALERT_EYES_OPEN, (7, 15): LID_CHAR, (7, 16): LID_CHAR, (7, 17): LID_CHAR}
_ALERT_PEEK_R = {**_ALERT_EYES_LINE, (7, 15): "x", (7, 16): "e", (7, 17): "o"}
# The waking yawn: eyes squeezed, mouth open under the nose.
_YAWN_MOUTH = {(10, 12): LID_CHAR, (10, 13): "n", (10, 14): LID_CHAR, (11, 13): LID_CHAR}
_MOUTH_SHUT = {(10, 12): "w", (10, 13): "w", (10, 14): "w", (11, 13): "w"}
_ALERT_EAR_REST = _cells("x", (0, 18, 18))
_ALERT_EAR_FLICK = _cells("x", (0, 19, 19))
_ALERT_EARS_REST = _cells("x", (0, 8, 8), (0, 18, 18))
_ALERT_EARS_SHAKE = _cells("x", (0, 7, 7), (0, 19, 19))
_ALERT_EAR_DROOP = {(0, 8): ".", (1, 7): "x", (0, 18): "x"}
_ALERT_EAR_UP = {(0, 8): "x", (0, 18): "x"}
_BANG = ("##", "##", "##", "##", "..", "##")
_BANG_LOW = _glyph("!", 2, 23, *_BANG)
_BANG_HIGH = _glyph("!", 0, 23, *_BANG)
_BANG_MID = _glyph("!", 1, 23, *_BANG)
_QUESTION = ("###", "..#", ".##", ".#.", "...", ".#.")
_QUESTION_UP = _glyph("?", 0, 22, *_QUESTION)
_QUESTION_DOWN = _glyph("?", 1, 22, *_QUESTION)


def _alert_base(eye_poses, pool_tail=None):
    """ALERT_TEMPLATE with the tail tip and eye cells turned into markers."""
    tmpl, tail = _pose_cells(ALERT_TEMPLATE, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_IN, _SIT_TAIL_OUT,
                                              _SIT_TAIL_UP, _SIT_TAIL_HIGH])
    tmpl, eyes = _pose_cells(tmpl, eye_poses)
    return tmpl, tail, eyes


# alert: wide eyes with a rare quick blink, a red "!" that pops up beside
# the head (overshoot, settle) every few seconds, a fast tail-tip flick.
_AL_TMPL, (_AL_T_NEUTRAL, _AL_T_IN, _AL_T_OUT, _AL_T_UP, _AL_T_HIGH), (_AL_OPEN, _AL_LINE) = \
    _alert_base([_ALERT_EYES_OPEN, _ALERT_EYES_LINE])
_AL_TMPL, (_AL_BANG_NONE, _AL_BANG_LOW, _AL_BANG_HIGH, _AL_BANG_MID) = _pose_cells(
    _AL_TMPL, [{}, _BANG_LOW, _BANG_HIGH, _BANG_MID])
_AL_TMPL, (_AL_EAR_REST, _AL_EAR_FLICK) = _pose_cells(
    _AL_TMPL, [_ALERT_EAR_REST, _ALERT_EAR_FLICK])

ALERT_ANIMATION = Animation(_AL_TMPL, (
    Track("eyes", (
        Frame({**_AL_OPEN, **_NOSE}, (2000, 5000)),
        Frame({**_AL_LINE, **_NOSE}, 120),
    )),
    Track("bang", (
        Frame(_AL_BANG_MID, (1200, 2000)),
        Frame(_AL_BANG_NONE, (1500, 3000)),
        Frame(_AL_BANG_LOW, 80),
        Frame(_AL_BANG_HIGH, 100),
    )),
    Track("tail", (
        Frame(_AL_T_NEUTRAL, (1200, 2500)),
        Frame(_AL_T_OUT, 70),
        Frame(_AL_T_UP, 90),
        Frame(_AL_T_OUT, 70),
        Frame(_AL_T_NEUTRAL, (600, 1200)),
        Frame(_AL_T_OUT, 70),
        Frame(_AL_T_NEUTRAL, 70),
    )),
    Track("ears", (
        Frame(_AL_EAR_REST, (4000, 9000)),
        Frame(_AL_EAR_FLICK, 90),
        Frame(_AL_EAR_REST, 110),
        Frame(_AL_EAR_FLICK, 90),
    )),
))

# panicked: the whole cat shakes in bursts, the tail puffs up into a bottle
# brush, the "!" flashes.
_PUFF_TAIL = _tail((11, 23, 24), (12, 22, 25), (13, 22, 26), (14, 21, 26), (15, 22, 26),
                   (16, 22, 26), (17, 21, 26), (18, 22, 26), (19, 22, 25), (20, 21, 25),
                   (21, 21, 24), (22, 20, 23), (23, 19, 22))
_PANIC_BASE = _overlay(
    [row.replace("y", ".").replace("Y", ".") for row in ALERT_TEMPLATE], _PUFF_TAIL)
_PN_TMPL, (_PN_OPEN,) = _pose_cells(_PANIC_BASE, [_ALERT_EYES_OPEN])
_PN_TMPL, (_PN_BANG_ON, _PN_BANG_OFF) = _pose_cells(_PN_TMPL, [_BANG_HIGH, {}])

PANICKED_ANIMATION = Animation(_PN_TMPL, (
    Track("face", (Frame({**_PN_OPEN, **_NOSE}),)),
    Track("shake", (
        Frame({}, (400, 700)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, -1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, 1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, -1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, 1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, -1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, 1)),
    )),
    Track("bang", (
        Frame(_PN_BANG_ON, 300),
        Frame(_PN_BANG_OFF, 150),
    )),
))

# confused: a yellow "?" bobbing above the head, one ear drooped, the eyes
# shutting one at a time.
_CF_TMPL, (_CF_T_NEUTRAL, _CF_T_IN, _CF_T_OUT, _CF_T_UP, _CF_T_HIGH), \
    (_CF_OPEN, _CF_WINK_L, _CF_WINK_R) = _alert_base([_ALERT_EYES_OPEN, _ALERT_WINK_L, _ALERT_WINK_R])
_CF_TMPL, (_CF_Q_UP, _CF_Q_DOWN) = _pose_cells(_CF_TMPL, [_QUESTION_UP, _QUESTION_DOWN])
_CF_TMPL, (_CF_EAR_DROOP, _CF_EAR_UP) = _pose_cells(
    _CF_TMPL, [_ALERT_EAR_DROOP, _ALERT_EAR_UP])

CONFUSED_ANIMATION = Animation(_CF_TMPL, (
    Track("eyes", (
        Frame({**_CF_OPEN, **_NOSE}, (1200, 2500)),
        Frame({**_CF_WINK_L, **_NOSE}, (400, 700)),
        Frame({**_CF_OPEN, **_NOSE}, (800, 1500)),
        Frame({**_CF_WINK_R, **_NOSE}, (400, 700)),
    )),
    Track("question", (
        Frame(_CF_Q_UP, 500),
        Frame(_CF_Q_DOWN, 500),
    )),
    Track("ears", (
        Frame(_CF_EAR_DROOP, (3000, 6000)),
        Frame(_CF_EAR_UP, (600, 1200)),
    )),
    Track("tail", (
        Frame(_CF_T_NEUTRAL, (2000, 4000)),
        Frame(_CF_T_IN, (800, 1500)),
    )),
))

# waking: groggy eyes (shut, one opens, both, drooping shut again) and now
# and then a yawn with the eyes squeezed into the arch; the ears shake.
_WK_TMPL, (_WK_T_NEUTRAL, _WK_T_IN, _WK_T_OUT, _WK_T_UP, _WK_T_HIGH), \
    (_WK_LINE, _WK_PEEK, _WK_OPEN, _WK_ARCH) = _alert_base(
        [{**_ALERT_EYES_LINE, **_MOUTH_SHUT}, {**_ALERT_PEEK_R, **_MOUTH_SHUT},
         {**_ALERT_EYES_OPEN, **_MOUTH_SHUT}, {**_ALERT_EYES_ARCH, **_YAWN_MOUTH}])
_WK_TMPL, (_WK_EARS_REST, _WK_EARS_SHAKE) = _pose_cells(
    _WK_TMPL, [_ALERT_EARS_REST, _ALERT_EARS_SHAKE])

WAKING_ANIMATION = Animation(_WK_TMPL, (
    Track("eyes", (
        Frame({**_WK_OPEN, **_NOSE}, (1500, 3000)),
        Frame({**_WK_LINE, **_NOSE}, (700, 1200)),
        Frame({**_WK_PEEK, **_NOSE}, (500, 900)),
        Frame({**_WK_OPEN, **_NOSE}, (1500, 3000)),
        Frame({**_WK_ARCH, **_NOSE}, (700, 1000)),
        Frame({**_WK_OPEN, **_NOSE}, (1200, 2500)),
        Frame({**_WK_LINE, **_NOSE}, 300),
    )),
    Track("ears", (
        Frame(_WK_EARS_REST, (3000, 6000)),
        Frame(_WK_EARS_SHAKE, 90),
        Frame(_WK_EARS_REST, 90),
        Frame(_WK_EARS_SHAKE, 90),
    )),
    Track("tail", (
        Frame(_WK_T_NEUTRAL, (2500, 5000)),
        Frame(_WK_T_OUT, 250),
        Frame(_WK_T_UP, (600, 1000)),
        Frame(_WK_T_OUT, 250),
    )),
))

# activate: yellow sparkles burst out from the cat in rings and fade.
_SPARK_INNER = [(4, 4), (11, 3), (19, 4), (2, 22), (9, 23), (24, 23)]
_SPARK_OUTER = [(1, 1), (10, 0), (21, 1), (0, 25), (8, 26), (25, 25)]


def _sparks(points, plus: bool) -> Dict[Tuple[int, int], str]:
    cells = {}
    for r, c in points:
        cells[(r, c)] = "h"
        if plus:
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if 0 <= r + dr < 26 and 0 <= c + dc < 28:
                    cells[(r + dr, c + dc)] = "h"
    return cells


_AC_TMPL, (_AC_T_NEUTRAL, _AC_T_IN, _AC_T_OUT, _AC_T_UP, _AC_T_HIGH), (_AC_OPEN, _AC_ARCH) = \
    _alert_base([_ALERT_EYES_OPEN, _ALERT_EYES_ARCH])
_AC_TMPL, (_AC_S_INNER_DOT, _AC_S_INNER_PLUS, _AC_S_OUTER_PLUS, _AC_S_OUTER_DOT, _AC_S_NONE) = \
    _pose_cells(_AC_TMPL, [_sparks(_SPARK_INNER, False), _sparks(_SPARK_INNER, True),
                           _sparks(_SPARK_OUTER, True), _sparks(_SPARK_OUTER, False), {}])

ACTIVATE_ANIMATION = Animation(_AC_TMPL, (
    Track("eyes", (
        Frame({**_AC_OPEN, **_NOSE}, (1500, 2500)),
        Frame({**_AC_ARCH, **_NOSE}, (500, 800)),
    )),
    Track("sparks", (
        Frame(_AC_S_INNER_PLUS, 160),
        Frame(_AC_S_OUTER_PLUS, 160),
        Frame(_AC_S_OUTER_DOT, 140),
        Frame(_AC_S_NONE, (300, 600)),
        Frame(_AC_S_INNER_DOT, 120),
    )),
    Track("tail", (
        Frame(_AC_T_NEUTRAL, (500, 900)),
        Frame(_AC_T_UP, 200),
        Frame(_AC_T_HIGH, (400, 700)),
        Frame(_AC_T_UP, 200),
    )),
))

ANIMATIONS: Dict[str, Animation] = {
    "sleeping": SLEEPING_ANIMATION,
    "content": CONTENT_ANIMATION,
    "interested": INTERESTED_ANIMATION,
    "alert": ALERT_ANIMATION,
    "panicked": PANICKED_ANIMATION,
    "confused": CONFUSED_ANIMATION,
    "waking": WAKING_ANIMATION,
    "activate": ACTIVATE_ANIMATION,
    # pose 2 repeats after pose 3 so the wag ping-pongs smoothly
    # (left, up, right, up, left, ...) instead of snapping right-to-left.
    "flopped": _anim(FLOPPED_TEMPLATE, [
        {"L": "k", **_TAIL_POSE_1},
        {"L": "k", **_TAIL_POSE_2},
        {"L": "k", **_TAIL_POSE_3},
        {"L": "k", **_TAIL_POSE_2},
    ]),
    "stirring": _anim(FLOPPED_TEMPLATE, [
        {"L": "k", **_TAIL_POSE_2},
        {"L": "e", **_TAIL_POSE_3},
    ]),
    "working": _anim(WORKING_TEMPLATE, [
        {"L": "e", "R": "e", "A": "n", "S": "b", "P": "g"},
        # typing pulse: lidded eyes, looking down at the bright screen
        {"L": "k", "R": "k", "A": "n", "S": "B", "P": "w"},
    ]),
    "thinking": _anim(THINKING_TEMPLATE, [
        {"L": "e", "R": "e", "A": "n", "t": "?", "T": "."},
        # bubble grows, one eye squints, nose flashes the accent
        {"L": "e", "R": "k", "A": "?", "t": "?", "T": "?"},
    ]),
    "permission": _anim(PERMISSION_TEMPLATE, [
        {"L": "e", "R": "e", "A": "!", "8": "d", "6": "f", "7": "."},
        {"L": "e", "R": "e", "A": "!", "8": "d", "6": ".", "7": "f"},
    ]),
    # done_hop is the only state whose frames use different base grids
    # (whole-cat translate): each frame carries its own template.
    "done_hop": single_track(DONE_HOP_GROUNDED_TEMPLATE, [
        Frame({"L": "e", "R": "e", "A": "h"}, template=DONE_HOP_GROUNDED_TEMPLATE),
        Frame({"L": "e", "R": "e", "A": "h"}, template=DONE_HOP_AIRBORNE_TEMPLATE),  # airborne
    ]),
}

ALL_STATES = tuple(ANIMATIONS)

for _anim_state in ANIMATIONS.values():
    check_animation(_anim_state)
del _anim_state


def _apply(template: List[str], subs: Mapping[str, str]) -> List[str]:
    return ["".join(subs.get(ch, ch) for ch in row) for row in template]


# Composed grids are cached per (state, track positions) and interned by
# content, so equal grids are one list object for the process and callers
# (the widget's image cache) can key on its identity. The cache entries
# remember their Animation so a replaced table entry (tests) rebuilds.
_GRIDS: Dict[tuple, List[str]] = {}
_COMPOSED: Dict[Tuple[str, Tuple[int, ...]], Tuple[Animation, List[str]]] = {}
_ENUMERATIONS: Dict[str, Tuple[Animation, List[Tuple[int, ...]], List[List[str]]]] = {}


def get_animation(state: str) -> Animation:
    """The track table for a mood/sub-state name."""
    try:
        return ANIMATIONS[state]
    except KeyError:
        raise KeyError(f"Unknown sprite state: {state!r}") from None


def get_tracks(state: str) -> Tuple[Track, ...]:
    """The tracks (names, frames with holds, loop_from) of a state."""
    return get_animation(state).tracks


def compose(state: str, positions: Sequence[int]) -> List[str]:
    """The grid with every track at the given frame position: the template
    (or the one frame template override) with all tracks' substitutions."""
    anim = get_animation(state)
    key = (state, tuple(positions))
    cached = _COMPOSED.get(key)
    if cached is not None and cached[0] is anim:
        return cached[1]
    template, subs = anim.template, {}
    for track, pos in zip(anim.tracks, positions):
        frame = track.frames[pos]
        subs.update(frame.subs)
        if frame.template is not None:
            template = frame.template
    rows = _apply(template, subs)
    grid = _GRIDS.setdefault(tuple(rows), rows)
    _COMPOSED[key] = (anim, grid)
    return grid


def _enumeration(state: str):
    anim = get_animation(state)
    cached = _ENUMERATIONS.get(state)
    if cached is None or cached[0] is not anim:
        zero = [0] * len(anim.tracks)
        positions = [tuple(zero)]
        for t, track in enumerate(anim.tracks):
            for i in range(1, len(track.frames)):
                positions.append(tuple(i if k == t else 0 for k in range(len(zero))))
        cached = _ENUMERATIONS[state] = (
            anim, positions, [compose(state, pos) for pos in positions])
    return cached


def known_grids(state: str) -> List[List[str]]:
    """Every grid of a state composed so far, enumeration entries included:
    what an image cache must keep for a state that is on screen."""
    anim = get_animation(state)
    grids = list(_enumeration(state)[2])
    grids += [g for (st, _), (a, g) in _COMPOSED.items() if st == state and a is anim]
    return grids


def enumeration_positions(state: str) -> List[Tuple[int, ...]]:
    """The track positions behind each get_frames() entry, in the same order."""
    return _enumeration(state)[1]


def get_frames(state: str) -> List[List[str]]:
    """Return the list of frame grids (each a list of equal-length row
    strings) for the given mood/sub-state name. One track: its frames in
    order. Several: every track at frame 0 first, then for each track in
    order each of its frames from 1 with the other tracks at frame 0 (equal
    grids stay at their positions as the same object). Entry 0 is always
    "everything at frame 0", which the icons and previews use."""
    return _enumeration(state)[2]
