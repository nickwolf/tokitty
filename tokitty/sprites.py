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

import functools
import math
import random
import re
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
    "q": "#3c6fe8",  # the question bubble's "?" -- blue, apart from confused's yellow
    "h": "#f2d675",  # activate/happy sparkle accent
    # Prop colors (task 8 activity sprites). Non-coat: props must not
    # change with coat, so these live in BASE_PALETTE, not the coat presets.
    "g": "#9aa0ac",  # laptop chassis
    "b": "#7fd8f0",  # laptop screen glow, dim
    "B": "#c8f2ff",  # laptop screen glow, bright (typing pulse)
    "f": "#ff3fa4",  # flag cloth -- hot magenta, distinct from every accent
    "d": "#c9a15a",  # flag pole -- light wood, reads against dark backdrops
    "G": "#5a4632",  # ground line -- fixed earth tone, never varies with coat
    "W": "#e8fbff",  # white lit by the laptop screen in the dark (working)
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
    "blue":     {"coat": "#6c7d9c", "shade": "#56657f", "mark": "#414c61", "light": "#d8dfea", "ear": "#d9a0b4"},
    "lilac":    {"coat": "#b9a6b8", "shade": "#9a8699", "mark": "#7a6779", "light": "#ece4ec", "ear": "#f0b3c6"},
    "cinnamon": {"coat": "#b3693f", "shade": "#965530", "mark": "#743f22", "light": "#efd2bb", "ear": "#eba7b2"},
    "silver":   {"coat": "#c9cdd2", "shade": "#a3a8b0", "mark": "#4d525a", "light": "#eef0f2", "ear": "#efb2c0"},
    "golden":   {"coat": "#d8a447", "shade": "#b8862f", "mark": "#94661c", "light": "#f6e4bd", "ear": "#f2b0a8"},
    "auburn":   {"coat": "#b5482a", "shade": "#933a22", "mark": "#6e2a18", "light": "#f3d6c4", "ear": "#eaa3ae"},
    "seal":     {"coat": "#4a3226", "shade": "#3a271d", "mark": "#2a1b14", "light": "#efe2cc", "ear": "#c99aa0"},
    "midnight": {"coat": "#3d4558", "shade": "#30374a", "mark": "#232a3a", "light": "#e4e8f0", "ear": "#c7a0b0"},
}

# User-made colorways (colorways.json), installed at startup and after every
# edit by colorways.apply. Ids are "custom-<N>", so they can never collide with
# a built-in name. Randomize draws from the built-in table above, never from
# this registry.
TONE_KEYS: Tuple[str, ...] = ("coat", "shade", "mark", "light", "ear")
_CUSTOM_ID_RE = re.compile(r"custom-[0-9]+")
_custom_colorways: Dict[str, Dict[str, str]] = {}
_custom_generation = 0


def is_custom_id(name: object) -> bool:
    """True for a well-formed custom id ("custom-<digits>"), registered or not."""
    return isinstance(name, str) and _CUSTOM_ID_RE.fullmatch(name) is not None


def set_custom_colorways(mapping: Dict[str, Dict[str, str]]) -> None:
    """Replace the custom table (id -> tones; extra keys such as a display
    name are ignored; entries missing a tone are skipped) and drop every
    palette resolved from the old one. Bumps custom_colorway_generation so
    caches keyed outside this module can tell the tones changed."""
    global _custom_generation
    table: Dict[str, Dict[str, str]] = {}
    for cid, tones in mapping.items():
        if is_custom_id(cid) and all(isinstance(tones.get(k), str) for k in TONE_KEYS):
            table[cid] = {k: tones[k] for k in TONE_KEYS}
    _custom_colorways.clear()
    _custom_colorways.update(table)
    _custom_generation += 1
    _resolved_palette.cache_clear()


def custom_colorway_generation() -> int:
    """Changes every time set_custom_colorways runs; a cache key component."""
    return _custom_generation


def colorway_tones(name: str) -> Dict[str, str]:
    """A colorway's five tones: built-in, else custom, else orange's, so a
    dangling id (a deleted or not-yet-loaded custom) never raises."""
    return COLORWAYS.get(name) or _custom_colorways.get(name) or COLORWAYS["orange"]


def colorway_names() -> List[str]:
    """Built-ins first, then customs in id order."""
    customs = sorted(_custom_colorways, key=lambda cid: int(cid.split("-", 1)[1]))
    return list(COLORWAYS) + customs


def is_colorway(name: object) -> bool:
    """True when `name` is a built-in or a currently registered custom."""
    return isinstance(name, str) and (name in COLORWAYS or name in _custom_colorways)


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


# The tail, a third axis beside colorway and pattern (#43): "plain" keeps the
# pattern's own tail; a ringed style paints the tail's two bands (y, Y) in a
# pair of the colorway's tones, on any pattern.
TAIL_STYLES: Dict[str, Optional[Tuple[str, str]]] = {
    "plain": None,
    "ringed": ("coat", "mark"),
    "ringed_light": ("light", "mark"),
    "ringed_shade": ("shade", "mark"),
    "ringed_pale": ("light", "shade"),
}


def resolve_palette(colorway: str = "orange", pattern: str = "tabby",
                    tail: str = "plain") -> Dict[str, str]:
    """Full char->color map for one colorway x pattern x tail. The colorway
    supplies the tone slots; the pattern maps each coat-driven char to a tone
    token or a literal hex; a ringed tail overrides the two tail bands. Every
    other char is BASE_PALETTE unchanged. Pure, so computed once per input;
    each call returns a fresh dict the caller may mutate."""
    return dict(_resolved_palette(colorway, pattern, tail))


@functools.lru_cache(maxsize=None)
def _resolved_palette(colorway: str, pattern: str, tail: str) -> Dict[str, str]:
    cw = colorway_tones(colorway)
    tones = {
        "coat": cw["coat"], "shade": cw["shade"], "mark": cw["mark"],
        "light": cw["light"], "white": BASE_PALETTE["w"],
    }
    merged = dict(BASE_PALETTE)
    merged["p"] = cw["ear"]
    for char, src in PATTERNS[pattern].items():
        merged[char] = tones.get(src, src)  # tone token -> tone, else literal hex
    rings = TAIL_STYLES[tail]
    if rings is not None:
        merged["y"], merged["Y"] = tones[rings[0]], tones[rings[1]]
    return derive_tones(merged)


def derive_tones(palette: Dict[str, str]) -> Dict[str, str]:
    """Fill in the tones derived from a palette's own colours (the lid, the
    screen glow, the dark-room twins), so a coat override can re-derive them."""
    palette[LID_CHAR] = lid_tone(palette["o"])
    palette[GLOW_CHAR] = glow_tone(palette["o"])
    for char, dark in DIM_CHARS.items():
        palette[dark] = dim_tone(palette[char])
    return palette


# Shut eyes are drawn in the fur colour darkened, not the outline black,
# which read as harsh on every coat. Derived from "o", so a coat override
# has to re-derive it (customize.effective_palette does).
LID_CHAR = "-"
LID_DARKEN = 0.5


# Dark-room twins (working): every coat and white char has a dimmed copy,
# so the whole cat can sit in shadow while the screen lights bands of it.
DIM_CHARS: Dict[str, str] = {"o": "(", "O": ")", "x": "[", "s": "]", "c": "{", "m": "}",
                             "u": "<", "w": ">", "y": "^", "Y": "_", "p": ";", "n": ":", "g": "|"}
DIM_FACTOR = 0.5


@functools.lru_cache(maxsize=None)
def dim_tone(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(v * DIM_FACTOR) for v in (r, g, b))


GLOW_CHAR = "~"
GLOW_LIGHT = "#c8f2ff"  # the bright screen glow, BASE_PALETTE "B"
GLOW_MIX = 0.55


@functools.lru_cache(maxsize=None)
def glow_tone(fur: str) -> str:
    """Fur lit by the laptop screen: the fur colour pulled toward the glow."""
    a = [int(fur[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(GLOW_LIGHT[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * GLOW_MIX) for x, y in zip(a, b))


@functools.lru_cache(maxsize=None)
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
    "........oxwwwwwwwxO.........",
    ".........ooxwwwxOO..........",
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
    "........oxwwwwwwwxO.........",
    ".........ooxwwwxOO..........",
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
    "...mmoooooooooooooOooooo.YYY",
    "..mmooooooooouuuuuOoccoooYY.",
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
    # Moves the whole sprite (dx, dy) cells from its spot while this frame is
    # up, dy < 0 being up: motion the 28x26 grid cannot hold, like a hop's
    # height or a jump onto the card's top edge. Facing right; a mirrored cat
    # moves by -dx. Icons, previews and keys draw the grid only.
    offset: Tuple[int, int] = (0, 0)


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
    with the animation template's dimensions on at most one track, offsets
    on at most one track, and substitution keys that no two tracks share (so
    the order tracks are composed in can never matter)."""
    if not anim.tracks:
        raise ValueError("animation has no tracks")
    names = [t.name for t in anim.tracks]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate track names {names}")
    dims = (len(anim.template), len(anim.template[0]))
    owners: Dict[str, str] = {}
    template_tracks = []
    offset_tracks = []
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
            if frame.offset != (0, 0) and track.name not in offset_tracks:
                offset_tracks.append(track.name)
    if len(template_tracks) > 1:
        raise ValueError(f"tracks {template_tracks} all override the template")
    if len(offset_tracks) > 1:
        raise ValueError(f"tracks {offset_tracks} all move the sprite")


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
    used = (set("".join(template)) | set(BASE_PALETTE) | set(REGION_CHARS)
            | {"p", LID_CHAR, GLOW_CHAR} | set(DIM_CHARS.values()))
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


def _sink_head(template: List[str], neck_row: int = 13, width: int = 21, keep: str = "") -> List[str]:
    """The head (rows above neck_row, columns below width) dropped one row
    onto the chest, the sleeping cat's exhale. The columns to the right (the
    tail tip, the Zs) stay where they are, and so do cells holding a `keep`
    marker (a tail pose that reaches into the head's columns)."""
    rows = [list(r) for r in template]
    for r in range(neck_row, 0, -1):
        for c in range(width):
            if template[r][c] not in keep:
                above = template[r - 1][c]
                rows[r][c] = "." if above in keep else above
    for c in range(width):
        if template[0][c] not in keep:
            rows[0][c] = "."
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
_SLEEP_EXHALE_TEMPLATE = _sink_head(_SLEEP_TEMPLATE, keep="".join(
    {ch for pose in (_SLEEP_TAIL_NEUTRAL, _SLEEP_TAIL_IN, _SLEEP_TAIL_OUT, _SLEEP_TAIL_UP, _SLEEP_TAIL_HIGH)
     for ch in pose}))

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

# panicked: the whole cat shakes in bursts, each opening with a startle hop
# two rows up that settles into smaller bounces, the "!" flashes, and on the
# way in the tail fluffs up (plain, bristled tip, full puff) and stays puffed.
_ALERT_TAIL_PLAIN = _tail((14, 21, 22), (15, 22, 23), (16, 23, 24), (17, 23, 24), (18, 24, 25),
                          (19, 24, 25), (20, 24, 25), (21, 23, 24), (22, 20, 22), (23, 19, 21))
_ALERT_TAIL_BRISTLE = _tail((12, 21, 21), (13, 20, 23), (14, 21, 23), (15, 22, 24), (16, 23, 25),
                            (17, 23, 24), (18, 24, 25), (19, 24, 25), (20, 24, 25), (21, 23, 24),
                            (22, 20, 22), (23, 19, 21))
# One cell wider than the plain curve, with fur spikes on alternate rows.
_ALERT_TAIL_PUFF = _tail((13, 21, 21), (14, 21, 23), (15, 22, 24), (16, 22, 25), (17, 23, 25),
                         (18, 23, 26), (19, 24, 26), (20, 23, 26), (21, 23, 25), (22, 20, 24),
                         (23, 19, 22))
_PANIC_BASE = [row.replace("y", ".").replace("Y", ".") for row in ALERT_TEMPLATE]
_PN_TMPL, (_PN_TAIL_PLAIN, _PN_TAIL_BRISTLE, _PN_TAIL_PUFF) = _pose_cells(
    _PANIC_BASE, [_ALERT_TAIL_PLAIN, _ALERT_TAIL_BRISTLE, _ALERT_TAIL_PUFF])
_PN_TMPL, (_PN_OPEN,) = _pose_cells(_PN_TMPL, [_ALERT_EYES_OPEN])
_PN_TMPL, (_PN_BANG_ON, _PN_BANG_OFF) = _pose_cells(_PN_TMPL, [_BANG_HIGH, {}])

PANICKED_ANIMATION = Animation(_PN_TMPL, (
    Track("face", (Frame({**_PN_OPEN, **_NOSE}),)),
    Track("shake", (
        Frame({}, (400, 700)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, -1), offset=(0, -2)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, 1), offset=(0, -1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, -1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, 1), offset=(0, -1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, -1)),
        Frame({}, 70, template=_shift_template(_PN_TMPL, 1)),
    )),
    Track("bang", (
        Frame(_PN_BANG_ON, 300),
        Frame(_PN_BANG_OFF, 150),
    )),
    Track("tail", (
        Frame(_PN_TAIL_PLAIN, 120),
        Frame(_PN_TAIL_BRISTLE, 120),
        Frame(_PN_TAIL_PUFF),
    ), loop_from=None),
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

# tired (#129): the alert cat worn out, its account near its cap while
# another has room. Heavy lids over the eyes, both ears sagging out to the
# sides, a slow tail, now and then a yawn, and every so often it nods off,
# the head sinking a row with the eyes shut, and jerks awake wide-eyed.
_TIRED_EYES_HEAVY = {**_ALERT_EYES_LINE, (8, 10): "e", (8, 16): "e"}
_TIRED_EARS_SAG = {(0, 8): ".", (1, 8): ".", (1, 7): "x", (2, 6): "x",
                   (0, 18): ".", (1, 18): ".", (1, 19): "x", (2, 20): "x"}
_TIRED_EAR_LIFT = {(0, 8): ".", (1, 8): ".", (1, 7): "x", (2, 6): "x",
                   (0, 18): "x", (1, 18): "x"}

_TI_TMPL, (_TI_T_NEUTRAL, _TI_T_IN, _TI_T_OUT, _TI_T_UP, _TI_T_HIGH), \
    (_TI_HEAVY, _TI_SHUT, _TI_OPEN, _TI_YAWN) = _alert_base(
        [{**_TIRED_EYES_HEAVY, **_MOUTH_SHUT}, {**_ALERT_EYES_LINE, **_MOUTH_SHUT},
         {**_ALERT_EYES_OPEN, **_MOUTH_SHUT}, {**_ALERT_EYES_ARCH, **_YAWN_MOUTH}])
_TI_TMPL, (_TI_EARS_SAG, _TI_EAR_LIFT) = _pose_cells(
    _TI_TMPL, [_TIRED_EARS_SAG, _TIRED_EAR_LIFT])
_TI_TMPL = [row.replace("A", "n") for row in _TI_TMPL]
_TI_NOD = _sink_head(_TI_TMPL)

TIRED_ANIMATION = Animation(_TI_TMPL, (
    # The nod lives here: it moves the eyes, so it shares their track.
    Track("eyes", (
        Frame(_TI_HEAVY, (2500, 5000)),
        Frame(_TI_SHUT, 300),
        Frame(_TI_HEAVY, (2000, 4000)),
        Frame(_TI_YAWN, 1100),
        Frame(_TI_HEAVY, (3000, 6000)),
        Frame(_TI_SHUT, 600),
        Frame(_TI_SHUT, 900, template=_TI_NOD),
        Frame(_TI_OPEN, 350),
        Frame(_TI_HEAVY, (1500, 3000)),
    )),
    Track("ears", (
        Frame(_TI_EARS_SAG, (6000, 12000)),
        Frame(_TI_EAR_LIFT, 250),
    )),
    Track("tail", (
        Frame(_TI_T_NEUTRAL, (3500, 7000)),
        Frame(_TI_T_OUT, 400),
        Frame(_TI_T_UP, (1200, 2000)),
        Frame(_TI_T_OUT, 400),
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


# ---- lying down: flopped and stirring ----------------------------------------
# The flopped template's old 1-5 sweep markers and its eyes are cleared, and
# the tail tip, eyes, ears and back become tracks. The tip sweeps through five
# strokes rooted at (16, 25), banded by row like the other tails.
_FLOP_BASE = _overlay(
    [row.translate(str.maketrans("12345", ".....")) for row in FLOPPED_TEMPLATE],
    {(11, 5): "o", (11, 10): "o", (11, 11): "o"})
_FLOP_TIP_A = _tail((16, 25, 25), (15, 24, 25), (14, 23, 24), (13, 22, 23), (12, 21, 22))
_FLOP_TIP_B = _tail((16, 25, 25), (15, 24, 25), (14, 24, 25), (13, 23, 24), (12, 23, 23))
_FLOP_TIP_C = _tail((16, 25, 25), (15, 25, 26), (14, 25, 26), (13, 24, 25), (12, 24, 25))
_FLOP_TIP_D = _tail((16, 25, 25), (15, 25, 26), (14, 25, 26), (13, 25, 26), (12, 26, 26))
_FLOP_TIP_E = _tail((15, 26, 27), (14, 26, 27), (13, 27, 27), (12, 27, 27))
# Eyes sit symmetric about the face: cols 5-6 and 10-11 on row 11.
_FLOP_EYES_SHUT = {(11, 5): LID_CHAR, (11, 6): LID_CHAR, (11, 10): LID_CHAR, (11, 11): LID_CHAR}
_FLOP_EYES_OPEN = {(11, 5): "e", (11, 6): "o", (11, 10): "o", (11, 11): "e"}
_FLOP_EARS_UP = {**_cells("x", (3, 12, 13)), (2, 5): "x", (2, 12): "x"}
_FLOP_EARS_DOWN = _cells("x", (3, 12, 13))


def _lift_head(template: List[str], top: int = 3, bottom: int = 14, width: int = 15) -> List[str]:
    """Rows top..bottom of the first `width` columns moved up one row, the
    bottom row kept as well, so the head rises off the paws on a longer neck."""
    rows = [list(r) for r in template]
    for r in range(top - 1, bottom):
        rows[r][:width] = rows[r + 1][:width]
    return ["".join(r) for r in rows]


# flopped: capped and spent, lolling on its back with its paws in the air,
# head rolled over onto the floor, tongue out. The belly rises and falls, a
# limp front paw droops, the tail tip curls, and now and then one eye checks
# the clock. Rolling over into stirring is the (flopped, stirring) transition.
FLOPPED_BACK_TEMPLATE: List[str] = [
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "................mm..........",
    "...........mm...mmoo....mm..",
    "...........mmOO...oo...OOm..",
    ".........mmooOO...oo...OO...",
    "....ooo..mmooOO...ooo.OO....",
    ".xxooooo...ooOO..oooo.OO....",
    "xxpoooRoo..ooOO..ooooOO..yy.",
    ".xxoooRoxoooouuuuuooooOoo.yy",
    "..osoooxwxouuuuuuuuuoooOo.YY",
    "..osoooxwwouuuuuuuuuuoooo.YY",
    "..osoooAnwoooooooooooooOo.yy",
    "..osoooxwwosoosoosooooOoo.yy",
    "..osoooxwxosoosoosooooOoo.YY",
    ".xxoooLoxOoooooooooooOooo.YY",
    "xxpOOOLOOoooooooooooOooooOyy",
    ".xxOOOOOOOOOOooooooOOOOOOyy.",
    "....OOO....OOOOOOOOOOOO.....",
    "............................",
    "............................",
    "............................",
]
# The head lies rolled 90 degrees, ears on the floor to the left, so each
# eye is a two-cell vertical bar: R on rows 12-13, L on rows 19-20, col 6.
_BACK_EYES_SHUT = {(12, 6): LID_CHAR, (13, 6): LID_CHAR, (19, 6): LID_CHAR, (20, 6): LID_CHAR}
_BACK_EYES_PEEK = {**_BACK_EYES_SHUT, (12, 6): "o", (13, 6): "e"}
# The belly band rises a row between the leg pairs on each breath in.
_BACK_EXHALE = {(12, 15): ".", (12, 16): "."}
_BACK_INHALE = {(12, 15): "u", (12, 16): "u"}
# The near front paw droops a row from its limp wrist.
_BACK_PAW_REST = _cells("m", (9, 9, 10), (10, 9, 10))
_BACK_PAW_DROOP = _cells("m", (10, 9, 10), (11, 9, 10))
# The tail rises up the right side of the rump, a column clear of the body so
# it never merges into the outline, to a small S at the top no higher than the
# raised paws. It swishes by sweeping the top in over the hip and out
# straight up the edge.
_BACK_TAIL_MID = _tail((13, 26, 27), (12, 26, 27), (11, 25, 26), (10, 25, 26), (9, 26, 27),
                       (8, 26, 27), (7, 26, 27), (6, 25, 26))
_BACK_TAIL_IN = _tail((13, 26, 27), (12, 25, 26), (11, 24, 25), (10, 24, 25), (9, 25, 26))
_BACK_TAIL_OUT = _tail((13, 26, 27), *((r, 26, 27) for r in range(8, 13)), (7, 27, 27), (6, 27, 27))

_FL_TMPL, (_FL_SHUT, _FL_PEEK) = _pose_cells(FLOPPED_BACK_TEMPLATE, [_BACK_EYES_SHUT, _BACK_EYES_PEEK])
_FL_TMPL, (_FL_EXHALE, _FL_INHALE) = _pose_cells(_FL_TMPL, [_BACK_EXHALE, _BACK_INHALE])
_FL_TMPL, (_FL_PAW_REST, _FL_PAW_DROOP) = _pose_cells(_FL_TMPL, [_BACK_PAW_REST, _BACK_PAW_DROOP])
_FL_TMPL, (_FL_TAIL_MID, _FL_TAIL_IN, _FL_TAIL_OUT) = _pose_cells(
    _FL_TMPL, [_BACK_TAIL_MID, _BACK_TAIL_IN, _BACK_TAIL_OUT])
# Out cold, not asleep: now and then a single small Z drifts up off the head,
# sparser than sleeping's and never the big one.
_FL_TMPL, (_FL_Z_NONE, _FL_Z_DOT, _FL_Z_LOW, _FL_Z_HIGH) = _pose_cells(_FL_TMPL, [
    {}, _cells("z", (9, 7, 7)), _shift(_ZZZ_SMALL, 0, -19), _shift(_ZZZ_SMALL, -2, -18)])

FLOPPED_ANIMATION = Animation(_FL_TMPL, (
    Track("eyes", (
        Frame({**_FL_SHUT, **_NOSE}, (15000, 30000)),
        Frame({**_FL_PEEK, **_NOSE}, (900, 1500)),
    )),
    Track("breath", (
        Frame(_FL_EXHALE, (2300, 2700)),
        Frame(_FL_INHALE, (1800, 2200)),
    )),
    Track("paw", (
        Frame(_FL_PAW_REST, (5000, 12000)),
        Frame(_FL_PAW_DROOP, 160),
        Frame(_FL_PAW_REST, 140),
        Frame(_FL_PAW_DROOP, 160),
    )),
    Track("tail", (
        Frame(_FL_TAIL_MID, (1500, 4000)),
        Frame(_FL_TAIL_IN, 220), Frame(_FL_TAIL_MID, 160), Frame(_FL_TAIL_OUT, 260),
        Frame(_FL_TAIL_MID, 160), Frame(_FL_TAIL_IN, 220), Frame(_FL_TAIL_MID, 160),
        Frame(_FL_TAIL_OUT, 260),
    )),
    Track("zzz", (
        Frame(_FL_Z_NONE, (6000, 12000)),
        Frame(_FL_Z_DOT, 500),
        Frame(_FL_Z_LOW, 700),
        Frame(_FL_Z_HIGH, 800),
    )),
))

# stirring: the reset is close. The head lifts off the paws now and then,
# groggy blinks, perked ears, a quicker tail.
_ST_TMPL, (_ST_A, _ST_B, _ST_C, _ST_D, _ST_E) = _pose_cells(
    _FLOP_BASE, [_FLOP_TIP_A, _FLOP_TIP_B, _FLOP_TIP_C, _FLOP_TIP_D, _FLOP_TIP_E])
_ST_TMPL, (_ST_SHUT, _ST_OPEN) = _pose_cells(_ST_TMPL, [_FLOP_EYES_SHUT, _FLOP_EYES_OPEN])
_ST_TMPL, (_ST_EARS_DOWN, _ST_EARS_UP) = _pose_cells(_ST_TMPL, [_FLOP_EARS_DOWN, _FLOP_EARS_UP])

STIRRING_ANIMATION = Animation(_ST_TMPL, (
    Track("eyes", (
        Frame({**_ST_SHUT, **_NOSE}, (800, 1500)),
        Frame({**_ST_OPEN, **_NOSE}, (1500, 3000)),
        Frame({**_ST_SHUT, **_NOSE}, 160),
        Frame({**_ST_OPEN, **_NOSE}, (1000, 2000)),
    )),
    Track("head", (
        Frame({}, (2000, 4000)),
        Frame({}, (1200, 2500), template=_lift_head(_ST_TMPL, top=2)),
    )),
    Track("tail", (
        Frame(_ST_B, (300, 700)),
        Frame(_ST_C, 90),
        Frame(_ST_D, 80),
        Frame(_ST_E, (200, 400)),
        Frame(_ST_D, 80),
        Frame(_ST_C, 90),
        Frame(_ST_B, 100),
        Frame(_ST_A, (200, 500)),
    )),
    Track("ears", (
        Frame(_ST_EARS_DOWN, (2000, 4000)),
        Frame(_ST_EARS_UP, (1500, 3000)),
    )),
))


# ---- props: working, thinking, permission ------------------------------------
# working, in a dark room: paws typing in bursts on the keyboard, bands of
# screen light scrolling up the face, a lean in to read, blinks, a slow
# tail sway and the odd ear twitch.
_PAWS_DOWN = _cells("m", (22, 9, 12))
_PAWS_LEFT_UP = {**_PAWS_DOWN, (22, 9): "g", (22, 10): "g"}
_PAWS_RIGHT_UP = {**_PAWS_DOWN, (22, 11): "g", (22, 12): "g"}
# The laptop faces the cat, so we see the back of its lid (a glowing logo in
# the middle). The room is dark: the whole cat is drawn in its dim twins,
# its eyes still bright, and two lines of screen light scroll up the face,
# chin to brow, as what's on the screen moves.


def _dim(pose: Dict[Tuple[int, int], str]) -> Dict[Tuple[int, int], str]:
    return {cell: DIM_CHARS.get(ch, ch) for cell, ch in pose.items()}


_WK_BASE = [row.replace("S", "g") for row in WORKING_TEMPLATE]
_WK_BASE = _overlay(_WK_BASE, {(17, 5): "B", (22, 9): "g", (22, 10): "g", (22, 11): "g", (22, 12): "g"})
_WK_BASE = ["".join(DIM_CHARS.get(ch, ch) for ch in row) for row in _WK_BASE]
_WRK_TMPL = _WK_BASE
_EYE_CELLS = set(_SIT_EYE_CELLS)
_FACE_LIGHT = {"o": GLOW_CHAR, "x": GLOW_CHAR, "w": "W", "s": "s", "c": "c", "p": "p", "n": "n"}


def _light_band(rows) -> Dict[Tuple[int, int], str]:
    cells = {}
    for r in range(3, 14):
        for c in range(5, 21):
            ch = WORKING_TEMPLATE[r][c]
            if ch in _FACE_LIGHT and (r, c) not in _EYE_CELLS:
                cells[(r, c)] = _FACE_LIGHT[ch] if r in rows else DIM_CHARS[ch]
    return cells


_LIGHT_BANDS = [_light_band(())] + [_light_band((13 - k, 10 - k)) for k in range(6)]
_WRK_TMPL, (_WRK_PAWS_DOWN, _WRK_PAWS_L, _WRK_PAWS_R) = _pose_cells(
    _WRK_TMPL, [_dim(_PAWS_DOWN), _dim(_PAWS_LEFT_UP), _dim(_PAWS_RIGHT_UP)])
_WRK_TMPL, _WRK_LIGHT = _pose_cells(_WRK_TMPL, _LIGHT_BANDS)
_WRK_TMPL, (_WRK_OPEN, _WRK_LINE) = _pose_cells(_WRK_TMPL, [_dim(_SIT_EYES_OPEN), _dim(_SIT_EYES_LINE)])
_WRK_TMPL, (_WRK_T_NEUTRAL, _WRK_T_OUT, _WRK_T_UP) = _pose_cells(
    _WRK_TMPL, [_dim(_SIT_TAIL_NEUTRAL), _dim(_SIT_TAIL_OUT), _dim(_SIT_TAIL_UP)])
_WRK_TMPL, (_WRK_EAR_REST, _WRK_EAR_FLICK) = _pose_cells(
    _WRK_TMPL, [_dim(_SIT_EAR_REST), _dim(_SIT_EAR_FLICK)])

WORKING_ANIMATION = Animation(_WRK_TMPL, (
    Track("paws", (
        Frame(_WRK_PAWS_DOWN, (400, 1200)),
        Frame(_WRK_PAWS_L, 90), Frame(_WRK_PAWS_R, 90), Frame(_WRK_PAWS_L, 90),
        Frame(_WRK_PAWS_R, 90), Frame(_WRK_PAWS_L, 90), Frame(_WRK_PAWS_R, 90),
    )),
    Track("glow", (
        Frame(_WRK_LIGHT[0], (400, 1200)),
        *(Frame(band, 120) for band in _WRK_LIGHT[1:]),
    )),
    Track("eyes", (
        Frame({**_WRK_OPEN, "A": ":"}, (2000, 5000)),
        Frame({**_WRK_LINE, "A": ":"}, 140),
    )),
    Track("lean", (
        Frame({}, (10000, 20000)),
        Frame({}, (1000, 1600), template=_sink_head(_WRK_TMPL)),
    )),
    Track("tail", (
        Frame(_WRK_T_NEUTRAL, (1500, 3000)),
        Frame(_WRK_T_OUT, 300),
        Frame(_WRK_T_UP, (500, 900)),
        Frame(_WRK_T_OUT, 300),
    )),
    Track("ears", (
        Frame(_WRK_EAR_REST, (8000, 20000)),
        Frame(_WRK_EAR_FLICK, 100),
        Frame(_WRK_EAR_REST, 120),
        Frame(_WRK_EAR_FLICK, 100),
    )),
))

# thinking: a thought bubble builds from a dot to a full bubble with "..."
# in it, holds, pops and clears; the eyes glance up toward it and the tail
# tip taps.
# A classic thought bubble up and to the left of the head: a scalloped white
# cloud with yellow dots in it, and a trail of a medium and a small circle
# down to the head.
_BUBBLE_DOT = _glyph("w", 6, 5, "#")
_BUBBLE_TRAIL = {**_BUBBLE_DOT, **_glyph("w", 4, 3, "##", "##")}
_BUBBLE_SMALL = {**_BUBBLE_TRAIL, **_glyph("w", 1, 1, ".##.", "####", ".##.")}
_BUBBLE_FULL = {**_BUBBLE_TRAIL, **_glyph("w", 0, 0, ".##.##.", "#######", "#######", ".#####."),
                (2, 1): "?", (2, 3): "?", (2, 5): "?"}
_BUBBLE_POP = _cells("w", (0, 0, 0), (0, 6, 6), (3, 0, 0), (2, 6, 6), (5, 3, 3))

_TH_TMPL, (_TH_NONE, _TH_DOT, _TH_TRAIL, _TH_SMALL, _TH_FULL, _TH_POP) = _pose_cells(
    SITTING_TEMPLATE, [{}, _BUBBLE_DOT, _BUBBLE_TRAIL, _BUBBLE_SMALL, _BUBBLE_FULL, _BUBBLE_POP])
_TH_TMPL, (_TH_OPEN, _TH_RIGHT, _TH_LINE) = _pose_cells(
    _TH_TMPL, [_SIT_EYES_OPEN, _SIT_EYES_LEFT, _SIT_EYES_LINE])
_TH_TMPL, (_TH_T_NEUTRAL, _TH_T_OUT) = _pose_cells(_TH_TMPL, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_OUT])

THINKING_ANIMATION = Animation(_TH_TMPL, (
    # Frame 0 is the full bubble, so the previews show the cat mid-thought.
    Track("bubble", (
        Frame(_TH_FULL, (1200, 2000)),
        Frame(_TH_POP, 150),
        Frame(_TH_NONE, (300, 700)),
        Frame(_TH_DOT, 220),
        Frame(_TH_TRAIL, 220),
        Frame(_TH_SMALL, 260),
    )),
    Track("eyes", (
        Frame({**_TH_OPEN, **_NOSE}, (1500, 3000)),
        Frame({**_TH_RIGHT, **_NOSE}, (1000, 2000)),
        Frame({**_TH_OPEN, **_NOSE}, 300),
        Frame({**_TH_LINE, **_NOSE}, 140),
    )),
    Track("tail", (
        Frame(_TH_T_NEUTRAL, (2000, 4000)),
        Frame(_TH_T_OUT, 120), Frame(_TH_T_NEUTRAL, 120), Frame(_TH_T_OUT, 120),
        Frame(_TH_T_NEUTRAL, 120), Frame(_TH_T_OUT, 120),
    )),
))

# permission: the pennant ripples on a straight pole,
# eyes wide with a rare blink.
# The pole is held straight: it ends just below the paw instead of jogging
# across the tail, which gets its cells back from the alert template.
_PM_BASE = _overlay(
    [row.replace("6", ".").replace("7", ".").replace("8", "d") for row in PERMISSION_TEMPLATE],
    {(r, c): ALERT_TEMPLATE[r][c] for r, c in ((14, 25), (15, 24), (16, 24), (17, 23), (18, 23), (19, 22))})
# The pennant ripples: a wave runs along its top edge out to the tip.
_FLAG_WAVES = [
    _cells("f", (0, 19, 24), (1, 19, 24), (2, 20, 24), (3, 21, 24), (4, 22, 24)),
    _cells("f", (0, 20, 24), (1, 19, 24), (2, 20, 24), (3, 21, 24), (4, 22, 24)),
    _cells("f", (0, 22, 24), (1, 20, 24), (2, 20, 24), (3, 21, 24), (4, 22, 24)),
]
_PM_TMPL, (_PM_W0, _PM_W1, _PM_W2) = _pose_cells(_PM_BASE, _FLAG_WAVES)
_PM_TMPL, (_PM_OPEN, _PM_LINE) = _pose_cells(_PM_TMPL, [_ALERT_EYES_OPEN, _ALERT_EYES_LINE])
_BANG_NOSE = {"A": "!"}

PERMISSION_ANIMATION = Animation(_PM_TMPL, (
    Track("flag", (Frame(_PM_W0, 250), Frame(_PM_W1, 250), Frame(_PM_W2, 250), Frame(_PM_W1, 250))),
    Track("eyes", (
        Frame({**_PM_OPEN, **_BANG_NOSE}, (3000, 6000)),
        Frame({**_PM_LINE, **_BANG_NOSE}, 140),
    )),
))

# permission_urgent (#174): the prompt has waited 30 s. The pennant whips
# twice as fast, the brows come down (frame 0, so a key or preview reads
# impatient too), the tail lashes and a hind foot beside the body taps,
# heel down, toes up.
_SCOWL_CELLS = {(6, 9): "o", (6, 10): "o", (6, 16): "o", (6, 17): "o"}
_SCOWL = {**_SCOWL_CELLS, (6, 9): LID_CHAR, (6, 10): LID_CHAR, (7, 11): LID_CHAR,
          (7, 15): LID_CHAR, (6, 16): LID_CHAR, (6, 17): LID_CHAR}
_PU_EYES_OPEN = {**_ALERT_EYES_OPEN, **_SCOWL}
_PU_EYES_LINE = {**_ALERT_EYES_LINE, **_SCOWL}
_HAUNCH = _cells("o", (23, 6, 7), (24, 5, 8))
_FOOT_DOWN = {**_HAUNCH, **_cells("o", (25, 5, 9)), **_cells("m", (25, 3, 4))}
_FOOT_UP = {**_HAUNCH, **_cells("o", (25, 6, 9)), **_cells("m", (24, 3, 4))}

_PU_TMPL, (_PU_W0, _PU_W1, _PU_W2) = _pose_cells(_PM_BASE, _FLAG_WAVES)
_PU_TMPL, (_PU_OPEN, _PU_LINE) = _pose_cells(_PU_TMPL, [_PU_EYES_OPEN, _PU_EYES_LINE])
_PU_TMPL, (_PU_FOOT_DOWN, _PU_FOOT_UP) = _pose_cells(_PU_TMPL, [_FOOT_DOWN, _FOOT_UP])
_PU_TMPL, (_PU_T_NEUTRAL, _PU_T_OUT, _PU_T_UP) = _pose_cells(
    _PU_TMPL, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_OUT, _SIT_TAIL_UP])

PERMISSION_URGENT_ANIMATION = Animation(_PU_TMPL, (
    Track("flag", (Frame(_PU_W0, 120), Frame(_PU_W1, 120), Frame(_PU_W2, 120), Frame(_PU_W1, 120))),
    Track("eyes", (
        Frame({**_PU_OPEN, **_BANG_NOSE}, (2500, 4500)),
        Frame({**_PU_LINE, **_BANG_NOSE}, 120),
    )),
    Track("foot", (
        Frame(_PU_FOOT_DOWN, (350, 600)),
        Frame(_PU_FOOT_UP, 110),
        Frame(_PU_FOOT_DOWN, 110),
        Frame(_PU_FOOT_UP, 110),
    )),
    Track("tail", (
        Frame(_PU_T_NEUTRAL, (700, 1200)),
        Frame(_PU_T_OUT, 80),
        Frame(_PU_T_UP, 100),
        Frame(_PU_T_OUT, 80),
    )),
))

# permission_frantic (#174): still waiting after 3 minutes. Everything from
# urgent, faster, plus a big red "!" flashing on the free side of the head
# and an open-mouthed meow now and then.
_BIG_BANG = ("###", "###", "###", "###", "###", "...", "###")
_PF_TMPL, (_PF_W0, _PF_W1, _PF_W2) = _pose_cells(_PM_BASE, _FLAG_WAVES)
_PF_TMPL, (_PF_OPEN, _PF_LINE) = _pose_cells(_PF_TMPL, [_PU_EYES_OPEN, _PU_EYES_LINE])
_PF_TMPL, (_PF_MOUTH_SHUT, _PF_MOUTH_MEOW) = _pose_cells(_PF_TMPL, [_MOUTH_SHUT, _YAWN_MOUTH])
_PF_TMPL, (_PF_FOOT_DOWN, _PF_FOOT_UP) = _pose_cells(_PF_TMPL, [_FOOT_DOWN, _FOOT_UP])
_PF_TMPL, (_PF_T_NEUTRAL, _PF_T_OUT, _PF_T_UP) = _pose_cells(
    _PF_TMPL, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_OUT, _SIT_TAIL_UP])
_PF_TMPL, (_PF_BANG_HIGH, _PF_BANG_LOW, _PF_BANG_NONE) = _pose_cells(
    _PF_TMPL, [_glyph("!", 1, 1, *_BIG_BANG), _glyph("!", 2, 1, *_BIG_BANG), {}])

PERMISSION_FRANTIC_ANIMATION = Animation(_PF_TMPL, (
    Track("flag", (Frame(_PF_W0, 80), Frame(_PF_W1, 80), Frame(_PF_W2, 80), Frame(_PF_W1, 80))),
    Track("bang", (
        Frame(_PF_BANG_HIGH, 350),
        Frame(_PF_BANG_NONE, 150),
        Frame(_PF_BANG_LOW, 70),
    )),
    Track("eyes", (
        Frame({**_PF_OPEN, **_BANG_NOSE}, (2500, 4500)),
        Frame({**_PF_LINE, **_BANG_NOSE}, 120),
    )),
    Track("mouth", (
        Frame(_PF_MOUTH_SHUT, (1500, 2500)),
        Frame(_PF_MOUTH_MEOW, 500),
    )),
    Track("foot", (Frame(_PF_FOOT_DOWN, 90), Frame(_PF_FOOT_UP, 90))),
    Track("tail", (
        Frame(_PF_T_NEUTRAL, (300, 600)),
        Frame(_PF_T_OUT, 70),
        Frame(_PF_T_UP, 80),
        Frame(_PF_T_OUT, 70),
    )),
))


# question: Claude Code is asking something. The cat sits up with its head
# tilted and a speech bubble with a big "?" bobs beside it, apart from the
# permission flag and from confused's bare yellow "?". The head is the alert
# head drawn tilted (left ear up, right ear down), the body the alert body.
QUESTION_TEMPLATE: List[str] = [row.ljust(28, ".") for row in (
    "..........x..........",
    ".........xpx.....o...",
    "........xxpooooooooxx",
    "........xxpxxssssxxpx",
    ".........occcoooosxpx",
    "........ooosssoooooxx",
    ".......oooooooooossoO",
    "........oooexxxxxoooO",
    "........oooxxxwxxxeoO",
    "........ooxxwwAwwxxoO",
    "........oxxwwwwwwwxoO",
    ".........oxwwwwwwwxOO",
    "..........oxxwwwxxOO.",
    "...........oooooOOO..",
)] + ALERT_TEMPLATE[14:]

_ASK_BUBBLE = (".#####.", "#######", "#######", "#######", "#######", "#######", "#######",
               "#######", ".#####.", ".#.....", "#......")
_ASK_MARK = ("###", "..#", ".##", ".#.", "...", ".#.")


def _ask_bubble(top: int) -> Dict[Tuple[int, int], str]:
    return {**_glyph("w", top, 21, *_ASK_BUBBLE), **_glyph("q", top + 1, 23, *_ASK_MARK)}


# The tilted eyes: the left one on row 7, the right one a row lower.
_QU_EYE_CELLS = {(7, 10): "o", (7, 11): "o", (7, 12): "x", (8, 17): "x", (8, 18): "o", (8, 19): "o"}
_QU_EYES_OPEN = {**_QU_EYE_CELLS, (7, 11): "e", (8, 18): "e"}
_QU_EYES_LINE = {**_QU_EYE_CELLS, **{cell: LID_CHAR for cell in _QU_EYE_CELLS}}
# A glance up at the bubble.
_QU_EYES_LOOK = {**_QU_EYE_CELLS, (7, 12): "e", (8, 18): "x", (8, 19): "e"}

_QU_TMPL, (_QU_T_NEUTRAL, _QU_T_OUT, _QU_T_UP) = _pose_cells(
    QUESTION_TEMPLATE, [_SIT_TAIL_NEUTRAL, _SIT_TAIL_OUT, _SIT_TAIL_UP])
_QU_TMPL, (_QU_OPEN, _QU_LINE, _QU_LOOK) = _pose_cells(
    _QU_TMPL, [_QU_EYES_OPEN, _QU_EYES_LINE, _QU_EYES_LOOK])
_QU_TMPL, (_QU_EAR_REST, _QU_EAR_FLICK) = _pose_cells(
    _QU_TMPL, [_cells("x", (0, 10, 10)), _cells("x", (0, 9, 9))])
_QU_TMPL, (_QU_ASK_UP, _QU_ASK_DOWN) = _pose_cells(_QU_TMPL, [_ask_bubble(0), _ask_bubble(1)])

QUESTION_ANIMATION = Animation(_QU_TMPL, (
    Track("bubble", (Frame(_QU_ASK_UP, 450), Frame(_QU_ASK_DOWN, 450))),
    Track("eyes", (
        Frame({**_QU_OPEN, **_NOSE}, (2000, 4000)),
        Frame({**_QU_LOOK, **_NOSE}, (900, 1500)),
        Frame({**_QU_OPEN, **_NOSE}, (1500, 3000)),
        Frame({**_QU_LINE, **_NOSE}, 140),
    )),
    Track("ears", (
        Frame(_QU_EAR_REST, (3000, 7000)),
        Frame(_QU_EAR_FLICK, 90),
        Frame(_QU_EAR_REST, 110),
        Frame(_QU_EAR_FLICK, 90),
    )),
    Track("tail", (
        Frame(_QU_T_NEUTRAL, (1500, 3000)),
        Frame(_QU_T_OUT, 200),
        Frame(_QU_T_UP, (700, 1200)),
        Frame(_QU_T_OUT, 200),
    )),
))


# done_hop: the happy hop when a task finishes, played once. A butt wiggle
# before the jump, a launch, a hang three rows up with the paws tucked and
# sparkles, a squash on landing and a proud settle with squinting eyes. The
# height is a sprite offset, so the whole cat leaves the grid's bottom and
# the ground shadow (#137) shows how high it went.
# activity.DONE_HOP_S keeps the state up long enough for all of it.
_HOP_SPARKS = _cells("h", (3, 2, 2), (6, 1, 1), (2, 24, 24), (5, 26, 26))


def _hop_frame(pose: str = "sit", eyes: str = "open", dx: int = 0,
               sparks: bool = False) -> List[str]:
    grid = _overlay(SITTING_TEMPLATE, _SIT_EYES_ARCH if eyes == "arch" else _SIT_EYES_OPEN)
    grid = [row.replace("A", "n") for row in grid]
    if pose == "crouch":
        grid = _sink_head(grid)
    if dx:
        grid = _shift_template(grid, dx)
    if pose == "tucked":
        # paws pulled up under the body: the two lowest rows go
        bottom = max(r for r, row in enumerate(grid) if row.strip("."))
        grid = _overlay(grid, {(r, c): "." for r in (bottom - 1, bottom) for c in range(28)})
    if sparks:
        grid = _overlay(grid, _HOP_SPARKS)
    return grid


def _up(rows: int) -> Tuple[int, int]:
    return (0, -rows)


DONE_HOP_ANIMATION = single_track(_hop_frame(), [
    Frame({}, 200, template=_hop_frame()),
    Frame({}, 150, template=_hop_frame(pose="crouch")),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=-1)),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=1)),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=-1)),
    Frame({}, 120, template=_hop_frame(pose="crouch")),
    Frame({}, 70, template=_hop_frame(), offset=_up(1)),
    Frame({}, 70, template=_hop_frame(), offset=_up(2)),
    Frame({}, 420, template=_hop_frame(pose="tucked", eyes="arch", sparks=True), offset=_up(3)),
    Frame({}, 70, template=_hop_frame(), offset=_up(2)),
    Frame({}, 70, template=_hop_frame(), offset=_up(1)),
    Frame({}, 160, template=_hop_frame(pose="crouch")),
    Frame({}, 400, template=_hop_frame(eyes="arch")),
], loop_from=None)

ANIMATIONS: Dict[str, Animation] = {
    "sleeping": SLEEPING_ANIMATION,
    "content": CONTENT_ANIMATION,
    "interested": INTERESTED_ANIMATION,
    "alert": ALERT_ANIMATION,
    "tired": TIRED_ANIMATION,
    "panicked": PANICKED_ANIMATION,
    "confused": CONFUSED_ANIMATION,
    "waking": WAKING_ANIMATION,
    "activate": ACTIVATE_ANIMATION,
    # pose 2 repeats after pose 3 so the wag ping-pongs smoothly
    # (left, up, right, up, left, ...) instead of snapping right-to-left.
    "flopped": FLOPPED_ANIMATION,
    "stirring": STIRRING_ANIMATION,
    "working": WORKING_ANIMATION,
    "thinking": THINKING_ANIMATION,
    "permission": PERMISSION_ANIMATION,
    "permission_urgent": PERMISSION_URGENT_ANIMATION,
    "permission_frantic": PERMISSION_FRANTIC_ANIMATION,
    "question": QUESTION_ANIMATION,
    "done_hop": DONE_HOP_ANIMATION,
}

ALL_STATES = tuple(ANIMATIONS)

# One-off animations played when the sprite state changes from one state to
# another (#134), before the new state's own loop starts. Keyed by
# (from, to); a pair with no entry is a plain cut. Every track plays once
# (loop_from None) and the transition ends when its longest track does.
TRANSITION_SEP = ">"
# A transition keyed (ANY_STATE, to) plays on a change into `to` from any
# state that has no transition of its own into it.
ANY_STATE = "*"


# The flopped cat rolls over into stirring: an eye opens, the paws tuck in,
# a curled half-roll seen from its back, then it lands on stirring's frame 0
# so the hand-off to stirring's own loop is seamless.
_ROLL_CURL: List[str] = [
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    ".......xx..xx...............",
    "......xpxxxpx...............",
    ".....ooooooooooo............",
    "....ooosssooosssoo..........",
    "...oooooooooooooooo.........",
    "...oosssoooosssooooo........",
    "...oooooooooooooooooo.......",
    "...oossooooossoooooooo......",
    "....oooooooooooooooooo......",
    ".....OOOOOOOOOOOOOOOOyy.....",
    "......OOOOOOOOOOOOOOYYY.....",
    "........mm.mm.....YYY.......",
    "............................",
    "............................",
    "............................",
]
TRANSITIONS: Dict[Tuple[str, str], Animation] = {}

# A state's own look in one cat spot (theme.SPOTS), keyed by (state, spot):
# the cat lying along the card's top edge where it would sit inside. Goes by
# "state@spot"; a state with no entry for a spot plays as usual there.
# TRANSITIONS may name a variant on either side.
SPOT_SEP = "@"
SPOT_VARIANTS: Dict[Tuple[str, str], Animation] = {}

# Moves from one spot to another (#116), played once by the wandering (#22):
# the jumps between the card and its top edge, the walk along it. Each starts
# at the spot it leaves, and its last frame's offset is where it lands
# relative to that spot. Goes by "move:name".
MOVE_PREFIX = "move:"
MOVES: Dict[str, Animation] = {}


def transition_name(from_state: str, to_state: str) -> str:
    """The name a transition goes by wherever a state name is expected
    (compose, get_frames, render_sheet): "from>to"."""
    return f"{from_state}{TRANSITION_SEP}{to_state}"


def transition_for(from_state: str, to_state: str) -> Optional[Tuple[str, str]]:
    """The TRANSITIONS key that plays on a change from from_state to to_state:
    the pair's own entry, else an ANY_STATE entry into to_state (never on a
    restart of the same state), else None for a plain cut."""
    if (from_state, to_state) in TRANSITIONS:
        return (from_state, to_state)
    if from_state != to_state and (ANY_STATE, to_state) in TRANSITIONS:
        return (ANY_STATE, to_state)
    return None


def spot_state(state: str, spot: str) -> str:
    """The name to play `state` under in `spot`: its variant's "state@spot"
    when SPOT_VARIANTS has one, else the state itself."""
    return f"{state}{SPOT_SEP}{spot}" if (state, spot) in SPOT_VARIANTS else state


def move_name(move: str) -> str:
    return f"{MOVE_PREFIX}{move}"


def _plays_once(anim: Animation) -> bool:
    return all(track.loop_from is None for track in anim.tracks)


def _check_transitions() -> None:
    for (state, _spot), anim in SPOT_VARIANTS.items():
        if state not in ANIMATIONS:
            raise ValueError(f"spot variant names an unknown state {state!r}")
        check_animation(anim)
    known = set(ANIMATIONS) | {spot_state(*key) for key in SPOT_VARIANTS}
    for (from_state, to_state), anim in TRANSITIONS.items():
        if from_state not in known | {ANY_STATE} or to_state not in known:
            raise ValueError(f"transition {from_state}->{to_state} names an unknown state")
        check_animation(anim)
        if not _plays_once(anim):
            raise ValueError(f"transition {from_state}->{to_state} must play once")
    for move, anim in MOVES.items():
        check_animation(anim)
        if not _plays_once(anim):
            raise ValueError(f"move {move!r} must play once")



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
    """The track table for a mood/sub-state name, a spot variant
    ("state@spot"), a move ("move:name"), or a transition by its
    transition_name ("from>to")."""
    if state in ANIMATIONS:
        return ANIMATIONS[state]
    if state.startswith(MOVE_PREFIX) and state[len(MOVE_PREFIX):] in MOVES:
        return MOVES[state[len(MOVE_PREFIX):]]
    if TRANSITION_SEP in state:
        pair = tuple(state.split(TRANSITION_SEP, 1))
        if pair in TRANSITIONS:
            return TRANSITIONS[pair]
    elif SPOT_SEP in state:
        key = tuple(state.split(SPOT_SEP, 1))
        if key in SPOT_VARIANTS:
            return SPOT_VARIANTS[key]
    raise KeyError(f"Unknown sprite state: {state!r}")


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


@functools.lru_cache(maxsize=None)
def offsets(state: str) -> Tuple[Tuple[int, int], ...]:
    """Every offset any frame of the animation moves the sprite by, (0, 0)
    included: where it can draw the cat, which the click mask must cover."""
    found = {(0, 0)} | {f.offset for t in get_animation(state).tracks for f in t.frames}
    return tuple(sorted(found))


def grid_size(state: str) -> Tuple[int, int]:
    """The animation's grid in cells, (columns, rows): 28 x 26 but for a move
    whose props reach past the cat (knock_off's glass, mouse's mouse), which
    draws the cat in the grid's top-left corner. Mirrored, the extra columns
    reach to the cat's left instead, and the cat stays where it was."""
    template = get_animation(state).template
    return len(template[0]), len(template)


def compose_offset(state: str, positions: Sequence[int]) -> Tuple[int, int]:
    """Where the sprite is moved, in cells, with every track at the given
    frame position: the offset of the one track that moves it, else (0, 0)."""
    for track, pos in zip(get_animation(state).tracks, positions):
        offset = track.frames[pos].offset
        if offset != (0, 0):
            return offset
    return (0, 0)


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


def _flopped_to_stirring() -> Animation:
    on_back = list(get_frames("flopped")[0])
    peek = _overlay(on_back, {(12, 6): "o", (13, 6): "e"})
    # paws tucked in: the legs fold down onto the chest, both eyes open
    tucked = _overlay(peek, {**{(r, c): "." for r in range(6, 13) for c in range(9, 26)
                                if on_back[r][c] not in "yY"},
                             (12, 11): "m", (12, 12): "m", (12, 18): "m", (12, 19): "m",
                             (19, 6): "o", (20, 6): "e"})
    landed = list(get_frames("stirring")[0])
    return single_track(on_back, [
        Frame({}, 250, template=peek),
        Frame({}, 120, template=tucked),
        Frame({}, 140, template=_ROLL_CURL),
        Frame({}, 100, template=landed),
    ], loop_from=None)


# Lying on the card's top edge (#116): a loaf, the sitting head dropped onto
# a low wide body with the front paws tucked under the chest, the tail hanging
# down over the front of the card. It replaces content and sleeping when the
# cat's spot is on_top. Same placeholders as the sitting template (L, R, A).
LOAF_TEMPLATE: List[str] = [
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "........x.........x.........",
    ".......xxx...o...xxx........",
    "......xxxxoooooooxxxx.......",
    "......xxpxxsssssxxpxx.......",
    ".....ooocccoooooooooOO......",
    ".......occsssosssoOO........",
    "......ooooooooooooooO.......",
    ".....oooooLxxxxxRoooooO.....",
    "....oooooooxxwxxoooooooO....",
    "...ooooooxxwwAwwxxooooOO....",
    "...sssooxxwwwwwwwxoooooOO...",
    "...ooooooxwwwwwwwxoooooOO...",
    "...sssoooooxwwwxoooooooOO...",
    "...ooooooouuuuuuuoccoooOO...",
    "...oooooouuuuuuuuucccooOO...",
    "....oooommmmooommmmoooOO....",
    "............................",
    "............................",
    "............................",
    "............................",
]
_LOAF_HEAD_DROP = 5  # rows the sitting head sits lower on the loaf
# The loaf's paws are on row 21; it is drawn this much lower than a sitting
# cat on the edge, so the paws rest on the card's top edge and the tail
# hangs over the card.
_LOAF_DROP = (0, len(LOAF_TEMPLATE) - 1 - 21)


def _loaf_tail(tip: str) -> Dict[Tuple[int, int], str]:
    tips = {"left": ((23, 24, 25), (24, 23, 24), (25, 23, 23)),
            "mid": ((23, 25, 26), (24, 25, 26), (25, 25, 25)),
            "right": ((23, 26, 27), (24, 26, 27), (25, 27, 27))}
    return _tail((19, 25, 25), (20, 25, 26), (21, 25, 26), (22, 25, 26), *tips[tip])


# The flanks fill out by a pixel on each breath in.
_LOAF_INHALE = {**_cells("o", *((r, 2, 2) for r in range(16, 21))),
                **_cells("O", *((r, 25, 25) for r in range(15, 19)))}

_LF_TMPL, (_LF_TAIL_MID, _LF_TAIL_LEFT, _LF_TAIL_RIGHT) = _pose_cells(
    LOAF_TEMPLATE, [_loaf_tail("mid"), _loaf_tail("left"), _loaf_tail("right")])
_LF_TMPL, (_LF_EXHALE, _LF_INHALE) = _pose_cells(_LF_TMPL, [{}, _LOAF_INHALE])
_LF_TMPL, (_LF_EAR_REST, _LF_EAR_FLICK) = _pose_cells(
    _LF_TMPL, [_shift(_SIT_EAR_REST, _LOAF_HEAD_DROP), _shift(_SIT_EAR_FLICK, _LOAF_HEAD_DROP)])
_LF_TMPL, (_LF_OPEN, _LF_SHUT, _LF_ARCH, _LF_PEEK, _LF_LOOK_L, _LF_LOOK_R) = _pose_cells(
    _LF_TMPL, [_shift(eyes, _LOAF_HEAD_DROP) for eyes in (
        _SIT_EYES_OPEN, _SIT_EYES_LINE, _SIT_EYES_ARCH,
        _SLEEP_PEEK, _SLEEP_PEEK_LEFT, _SLEEP_PEEK_RIGHT)])
_LF_TMPL, (_LF_Z_BIG, _LF_Z_NONE, _LF_Z_DOT, _LF_Z_SMALL) = _pose_cells(
    _LF_TMPL, [_ZZZ_BIG, {}, _ZZZ_DOT, _ZZZ_SMALL])
_LF_TMPL = [row.replace("A", "n") for row in _LF_TMPL]

# The breath track carries the drop, so every frame of it sits on the edge.
LOAF_CONTENT_ANIMATION = Animation(_LF_TMPL, (
    Track("eyes", (
        Frame(_LF_OPEN, (2500, 6000)),
        Frame(_LF_SHUT, 150),
        Frame(_LF_OPEN, (3000, 7000)),
        Frame(_LF_SHUT, 140),
        Frame(_LF_OPEN, 120),
        Frame(_LF_SHUT, 140),
        Frame(_LF_OPEN, (2500, 6000)),
        Frame(_LF_ARCH, 900),
    )),
    # The tip swings over the card like a lazy pendulum.
    Track("tail", (
        Frame(_LF_TAIL_MID, (1200, 2600)),
        Frame(_LF_TAIL_RIGHT, (600, 900)),
        Frame(_LF_TAIL_MID, (500, 700)),
        Frame(_LF_TAIL_LEFT, (600, 900)),
    )),
    Track("breath", (
        Frame(_LF_EXHALE, (2500, 3500), offset=_LOAF_DROP),
        Frame(_LF_INHALE, (1800, 2400), offset=_LOAF_DROP),
    )),
    Track("ears", (
        Frame(_LF_EAR_REST, (8000, 20000)),
        Frame(_LF_EAR_FLICK, 100),
        Frame(_LF_EAR_REST, 120),
        Frame(_LF_EAR_FLICK, 100),
    )),
    Track("zzz", (Frame(_LF_Z_NONE, 1000),), None),
))

LOAF_SLEEPING_ANIMATION = Animation(_LF_TMPL, (
    Track("eyes", (
        Frame(_LF_SHUT, (20000, 45000)),
        Frame(_LF_PEEK, 600),
        Frame(_LF_LOOK_L, 450),
        Frame(_LF_LOOK_R, 450),
        Frame(_LF_PEEK, 300),
    )),
    Track("breath", (
        Frame(_LF_EXHALE, (2000, 2500), offset=_LOAF_DROP),
        Frame(_LF_INHALE, (1700, 2000), offset=_LOAF_DROP),
    )),
    Track("zzz", (
        Frame(_LF_Z_BIG, 800),
        Frame(_LF_Z_NONE, (1200, 2200)),
        Frame(_LF_Z_DOT, 500),
        Frame(_LF_Z_SMALL, 600),
    )),
    Track("ears", (
        Frame(_LF_EAR_REST, (15000, 40000)),
        Frame(_LF_EAR_FLICK, 100),
        Frame(_LF_EAR_REST, 140),
        Frame(_LF_EAR_FLICK, 100),
    )),
    Track("tail", (
        Frame(_LF_TAIL_MID, (6000, 15000)),
        Frame(_LF_TAIL_RIGHT, 500),
        Frame(_LF_TAIL_MID, 400),
        Frame(_LF_TAIL_LEFT, 500),
        Frame(_LF_TAIL_MID, (500, 900)),
    )),
))

SPOT_VARIANTS[("content", "on_top")] = LOAF_CONTENT_ANIMATION
SPOT_VARIANTS[("sleeping", "on_top")] = LOAF_SLEEPING_ANIMATION


# alert on the card's top edge (#127, #135's watch): the alert cat sitting on
# the edge with its head bowed over the bars below, eyes a row low, and its
# tail hanging down over the front of the card like the loaf's. The body sits
# _PERCH_DROP rows higher under the head, and the whole sprite is drawn that
# much lower, so the paws still rest on the edge.
_PERCH_DROP = 3


def _perch_template() -> List[str]:
    head = ALERT_TEMPLATE[:13]
    body = [row.replace("y", ".").replace("Y", ".") for row in ALERT_TEMPLATE[14:]]
    blank = ["." * 28]
    rows = [list(row) for row in blank * (14 - _PERCH_DROP) + body + blank * _PERCH_DROP]
    for r, line in enumerate(head):
        for c, ch in enumerate(line):
            if ch != ".":
                rows[r][c] = ch
    return ["".join(row) for row in rows]


def _perch_tail(tip: str) -> Dict[Tuple[int, int], str]:
    tips = {"left": ((23, 20, 21), (24, 19, 20), (25, 19, 19)),
            "mid": ((23, 21, 22), (24, 21, 22), (25, 21, 21)),
            "right": ((23, 22, 23), (24, 22, 23), (25, 23, 23))}
    return _tail((19, 20, 21), (20, 21, 22), (21, 21, 22), (22, 21, 22), *tips[tip])


_PERCH_EYES_DOWN = {**_ALERT_EYES_OPEN, (7, 10): "o", (7, 16): "o", (8, 10): "e", (8, 16): "e"}
_PERCH_EYES_DOWN_R = {**_ALERT_EYES_OPEN, (7, 10): "o", (7, 16): "o", (8, 11): "e", (8, 17): "e"}
_PERCH_EYES_DOWN_L = {**_ALERT_EYES_OPEN, (7, 10): "o", (7, 16): "o", (8, 9): "e", (8, 15): "e"}
_PERCH_EYES_SHUT = {**_ALERT_EYES_OPEN, (7, 10): "o", (7, 16): "o",
                    **_cells(LID_CHAR, (8, 9, 11), (8, 15, 17))}

_PC_TMPL, (_PC_TAIL_MID, _PC_TAIL_LEFT, _PC_TAIL_RIGHT) = _pose_cells(
    _perch_template(), [_perch_tail("mid"), _perch_tail("left"), _perch_tail("right")])
_PC_TMPL, (_PC_DOWN, _PC_DOWN_R, _PC_DOWN_L, _PC_SHUT) = _pose_cells(
    _PC_TMPL, [_PERCH_EYES_DOWN, _PERCH_EYES_DOWN_R, _PERCH_EYES_DOWN_L, _PERCH_EYES_SHUT])
_PC_TMPL, (_PC_BANG_NONE, _PC_BANG_LOW, _PC_BANG_HIGH, _PC_BANG_MID) = _pose_cells(
    _PC_TMPL, [{}, _BANG_LOW, _BANG_HIGH, _BANG_MID])
_PC_TMPL, (_PC_EAR_REST, _PC_EAR_FLICK) = _pose_cells(
    _PC_TMPL, [_ALERT_EAR_REST, _ALERT_EAR_FLICK])
_PC_TMPL = [row.replace("A", "n") for row in _PC_TMPL]

ALERT_PERCH_ANIMATION = Animation(_PC_TMPL, (
    Track("eyes", (
        Frame(_PC_DOWN, (2000, 4500)),
        Frame(_PC_SHUT, 120),
        Frame(_PC_DOWN, (1500, 3000)),
        Frame(_PC_DOWN_R, (700, 1400)),
        Frame(_PC_DOWN, (1500, 3500)),
        Frame(_PC_DOWN_L, (500, 900)),
    )),
    Track("bang", (
        Frame(_PC_BANG_MID, (1200, 2000)),
        Frame(_PC_BANG_NONE, (1500, 3000)),
        Frame(_PC_BANG_LOW, 80),
        Frame(_PC_BANG_HIGH, 100),
    )),
    # The tip twitches the way alert's flicks, not the loaf's lazy swing.
    Track("tail", (
        Frame(_PC_TAIL_MID, (1000, 2200)),
        Frame(_PC_TAIL_RIGHT, 90),
        Frame(_PC_TAIL_MID, 80),
        Frame(_PC_TAIL_RIGHT, 90),
        Frame(_PC_TAIL_MID, (600, 1200)),
        Frame(_PC_TAIL_LEFT, 110),
    )),
    Track("ears", (
        Frame(_PC_EAR_REST, (4000, 9000)),
        Frame(_PC_EAR_FLICK, 90),
        Frame(_PC_EAR_REST, 110),
        Frame(_PC_EAR_FLICK, 90),
    )),
    Track("perch", (Frame({}, 1000, offset=(0, _PERCH_DROP)),), None),
))
SPOT_VARIANTS[("alert", "on_top")] = ALERT_PERCH_ANIMATION
# The variants drawn lying down, which the wanderer reaches with lie_down and
# leaves with sit_up. Every other variant is drawn sitting.
LYING_VARIANTS = frozenset({("content", "on_top"), ("sleeping", "on_top")})
# The moves that end lying in the loaf rather than sitting up as they began.
LYING_MOVES = frozenset({"lie_down", "bat_down_roll"})

# jump_up / jump_down: the jumps between the card (inside) and its top edge
# (on_top), 29 rows apart. jump_up wiggles like done_hop, launches with its
# front paws thrown up, overshoots the edge by three rows (HOP_HEADROOM) with
# its paws tucked, and drops onto it. jump_down peeks over the edge first, then
# falls with its front paws reaching for the ground and its tail streaming up.
_JUMP_ROWS = 29  # theme: on_top sits SPRITE_H + 12 px above inside
_JUMP_HEAD = [row.replace("A", "n") for row in _overlay(SITTING_TEMPLATE, _SIT_EYES_OPEN)[:14]]

_JUMP_LAUNCH_BODY = [
    "........ooooooooooo.........",
    "........ooooooooooO.........",
    "........ossooowoooO.........",
    "........oooouuuoooO.........",
    "........sssuuuuucccO........",
    "........ooouuuuuccOY........",
    "........ooouuuuuuoOYY.......",
    ".........oouuuuuoOO.YY......",
    ".........ooo...ooO...yy.....",
    ".........ooo...ooO...yy.....",
    ".........mmm...mmm....YY....",
    "......................YY....",
]

# Each foreleg is three cells wide, stepping out a column every two rows from
# the shoulder; the right one mirrors the left with its outer edge shaded.
_JUMP_ARM = ((14, 7), (13, 6), (12, 6), (11, 5), (10, 5), (9, 4), (8, 4), (7, 3), (6, 3))
_JUMP_ARMS = {**{(r, c + dc): "o" for r, c in _JUMP_ARM for dc in (-1, 0, 1)},
              **{(r, 27 - c + dc): "o" for r, c in _JUMP_ARM for dc in (-2, -1)},
              **{(r, 27 - c): "O" for r, c in _JUMP_ARM},
              **_cells("m", (4, 2, 3), (5, 2, 3), (4, 23, 24), (5, 23, 24))}
_JUMP_LAUNCH: List[str] = _overlay(_JUMP_HEAD + _JUMP_LAUNCH_BODY, _JUMP_ARMS)

_JUMP_FALL_BODY = [
    "........ooooooooooo.........",
    ".......ooooooooooooO........",
    ".......ossooowooooooO.......",
    ".......oooouuuuuoooooO......",
    ".......ssouuuuuuucccO.......",
    "........oouuuuuuuccO........",
    "........ooouuuuuuoO.........",
    "........oo.......oO.........",
    "........oo.......oO.........",
    "........oo.......oO.........",
    "........mm.......mm.........",
    "............................",
]
# The ears swept flat (their tips gone) and the tail rising from the rump
# behind the right flank.
_JUMP_FALL: List[str] = _overlay(
    _JUMP_HEAD[:1] + ["." * 28] + _JUMP_HEAD[2:] + _JUMP_FALL_BODY,
    {(r, c + dc): band for r, c, band in (
        (19, 19, "Y"), (18, 20, "Y"), (17, 21, "y"), (16, 21, "y"), (15, 21, "Y"),
        (14, 22, "Y"), (13, 22, "y"), (12, 23, "y"), (11, 23, "Y"), (10, 23, "Y"))
     for dc in (0, 1)})

# Looking down over the edge: the head lowered three rows onto the chest, the
# eyes a row low under dark lids.
_JUMP_PEEK_DROP = 3
_JUMP_PEEK_EYES = {(8, 9): "o", (8, 10): "x", (8, 11): "o", (8, 15): "o", (8, 16): "x",
                   (8, 17): "o", (9, 10): "e", (9, 16): "e"}


def _jump_peek() -> List[str]:
    head = _overlay(SITTING_TEMPLATE, _JUMP_PEEK_EYES)[:14]
    rows = [list(row) for row in SITTING_TEMPLATE]
    for r in range(_JUMP_PEEK_DROP):
        rows[r][:22] = ["."] * 22
    for r in range(13, -1, -1):
        for c in range(22):
            if head[r][c] != ".":
                rows[r + _JUMP_PEEK_DROP][c] = head[r][c]
            elif r + _JUMP_PEEK_DROP < 14:
                rows[r + _JUMP_PEEK_DROP][c] = "."
    return ["".join(row).replace("A", "n") for row in rows]


_JUMP_PEEK = _jump_peek()


def _down(rows: int) -> Tuple[int, int]:
    return (0, rows)


MOVES["jump_up"] = single_track(_hop_frame(), [
    Frame({}, 200, template=_hop_frame()),
    Frame({}, 150, template=_hop_frame(pose="crouch")),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=-1)),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=1)),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=-1)),
    Frame({}, 120, template=_hop_frame(pose="crouch")),
    Frame({}, 60, template=_JUMP_LAUNCH, offset=_up(10)),
    Frame({}, 60, template=_JUMP_LAUNCH, offset=_up(24)),
    Frame({}, 140, template=_hop_frame(pose="tucked"), offset=_up(_JUMP_ROWS + 3)),
    Frame({}, 70, template=_JUMP_FALL, offset=_up(_JUMP_ROWS + 1)),
    Frame({}, 150, template=_hop_frame(pose="crouch"), offset=_up(_JUMP_ROWS)),
    Frame({}, 400, template=_hop_frame(), offset=_up(_JUMP_ROWS)),
], loop_from=None)

MOVES["jump_down"] = single_track(_hop_frame(), [
    Frame({}, 200, template=_hop_frame()),
    Frame({}, 700, template=_JUMP_PEEK),
    Frame({}, 150, template=_hop_frame(pose="crouch")),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=-1)),
    Frame({}, 110, template=_hop_frame(pose="crouch", dx=1)),
    Frame({}, 100, template=_hop_frame(pose="crouch")),
    Frame({}, 70, template=_JUMP_LAUNCH, offset=_up(2)),
    Frame({}, 60, template=_JUMP_FALL, offset=_down(4)),
    Frame({}, 50, template=_JUMP_FALL, offset=_down(13)),
    Frame({}, 50, template=_JUMP_FALL, offset=_down(24)),
    Frame({}, 160, template=_hop_frame(pose="crouch"), offset=_down(_JUMP_ROWS)),
    Frame({}, 400, template=_hop_frame(), offset=_down(_JUMP_ROWS)),
], loop_from=None)

# walk_start / walk / walk_stop: walking along the card's top edge, in side
# profile facing right (a mirrored cat walks left). walk_start turns the
# sitting cat side on, walk is one stride of four frames that covers
# _WALK_STRIDE cells and is played back to back for longer walks, and
# walk_stop sits back down facing out.
_WALK_STRIDE = 4

_WALK_BODY = [
    "............................",
    "............................",
    "............................",
    "...................x....x...",
    "..................xpx..xpx..",
    "..................xppooopx..",
    ".................oosssooooo.",
    ".................ooooooooooo",
    "................oooooooeoxxo",
    "................oooooooxwwwn",
    "................ooooooxwwwww",
    ".................Oooooxwwwww",
    ".....ooosssoooooOOooooxwww..",
    "....oossssooossooooOOOOOO...",
    "....ossssooossoooooccO......",
    "....oooooooooooooooccO......",
    "....osssoooooooooocccO......",
    "....Oooouuuuuuuuuuuo........",
    ".....OOuuuuuuuuuuuO.........",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
    "............................",
]

# Foot columns (near front, far front, near hind, far hind) for each stride
# pose: a contact pose with the legs spread, a passing pose with them under
# the body, then the same with the other pair leading. The far legs are shaded.
_WALK_FEET = {
    "stand": (17, 15, 6, 4),
    "contact": (19, 15, 4, 8),
    "pass": (17, 16, 6, 7),
    "contact2": (15, 19, 8, 4),
    "pass2": (16, 17, 7, 6),
}
_WALK_TAILS = {
    "up": ((13, 4), (12, 3), (11, 2), (10, 2), (9, 2), (8, 2), (7, 3), (6, 4)),
    "sway": ((13, 4), (12, 3), (11, 3), (10, 2), (9, 2), (8, 1), (7, 1), (6, 2)),
}


def _walk_frame(feet: str = "stand", tail: str = "up") -> List[str]:
    rows = [list(row) for row in _WALK_BODY]
    near_front, far_front, near_hind, far_hind = _WALK_FEET[feet]
    # far legs first, so the near ones cover them where they cross
    for hip, foot, fur in ((16, far_front, "O"), (5, far_hind, "O"),
                           (17, near_front, "o"), (6, near_hind, "o")):
        # two cells wide from under the body (row 19) to the paw on row 25
        for i, r in enumerate(range(19, 25)):
            c = round(hip + (foot - hip) * i / 5)
            rows[r][c] = rows[r][c + 1] = fur
        rows[25][foot] = rows[25][foot + 1] = "m"
    for i, (r, c) in enumerate(_WALK_TAILS[tail]):
        rows[r][c] = rows[r][c + 1] = "Y" if i // 2 % 2 == 0 else "y"
    return ["".join(row) for row in rows]


_WALK_STAND = _walk_frame()

MOVES["walk_start"] = single_track(_hop_frame(), [
    Frame({}, 150, template=_hop_frame()),
    Frame({}, 120, template=_hop_frame(pose="crouch")),
    Frame({}, 200, template=_WALK_STAND),
], loop_from=None)

MOVES["walk"] = single_track(_WALK_STAND, [
    Frame({}, 130, template=_walk_frame(feet, tail), offset=(step, 0))
    for step, (feet, tail) in enumerate(
        (("contact", "up"), ("pass", "sway"), ("contact2", "up"), ("pass2", "sway")), 1)
], loop_from=None)

MOVES["walk_stop"] = single_track(_WALK_STAND, [
    Frame({}, 150, template=_WALK_STAND),
    Frame({}, 120, template=_hop_frame(pose="crouch")),
    Frame({}, 300, template=_hop_frame()),
], loop_from=None)

# lie_down / sit_up: between sitting on the top edge and the content loaf
# (content@on_top), so a cat that wanders up settles into the loaf and sits up
# again before it walks or jumps down. Two in-between frames in loaf
# coordinates (row 21 on the edge, drawn _LOAF_DROP lower): the body widening
# from the sitting cat's to the loaf's, the head coming down to meet it.
_SETTLE_HALF = {12: (7, 19), 13: (6, 20), 14: (6, 21), 15: (5, 21), 16: (5, 22),
                17: (4, 22), 18: (4, 22), 19: (4, 22), 20: (4, 22), 21: (5, 21)}
_SETTLE_LOAF = {12: (6, 20), 13: (5, 22), 14: (4, 23), 15: (3, 23), 16: (3, 24),
                17: (3, 24), 18: (3, 24), 19: (3, 24), 20: (3, 24), 21: (4, 23)}


def _settle_frame(head_down: int, body: Mapping[int, Tuple[int, int]],
                  paws: Sequence[int], tail_tip: str = "mid") -> List[str]:
    """The sitting head head_down rows below the loaf grid's top (the loaf
    itself uses _LOAF_HEAD_DROP) over a body spanning `body`'s columns per
    row, the paws on row 21 and the loaf's tail over the edge."""
    rows = [["."] * 28 for _ in range(len(LOAF_TEMPLATE))]
    for r, (first, last) in body.items():
        rows[r][first:last + 1] = ["o"] * (last - first + 1)
        rows[r][last] = "O"
        if r >= 15:
            rows[r][last - 1] = "O"
    for r in (16, 18):  # the haunch stripes, as on the sitting cat's flank
        first = body[r][0]
        rows[r][first:first + 3] = ["s"] * 3
    for r, cols in ((19, (18, 19)), (20, (18, 19, 20))):
        for c in cols:
            rows[r][c] = "c"
    for r in range(1, 14):
        for c, ch in enumerate(SITTING_TEMPLATE[r]):
            if ch == "." or r + head_down > 21:
                continue
            # the head's shaded edge only against the air, as on the loaf
            rows[r + head_down][c] = "o" if ch == "O" and rows[r + head_down][c] != "." else ch
    # a bib from below the chin to the paws, no more than four rows deep
    for r in range(max(14 + head_down, 17), 21):
        first, last = (10, 16) if r < 19 else (9, 17)
        rows[r][first:last + 1] = ["u"] * (last - first + 1)
    for c in paws:
        rows[21][c] = "m"
    grid = _overlay(["".join(row) for row in rows], _loaf_tail(tail_tip))
    # a body narrower than the loaf's: bridge the gap to the tail's root
    right = max(body[r][1] for r in (19, 20))
    grid = _overlay(grid, {(r, c): "Y" for r in (19, 20) for c in range(right + 1, 25)})
    grid = _overlay(grid, _shift(_SIT_EYES_OPEN, head_down))
    return [row.replace("A", "n") for row in grid]


_SETTLE_LOWER = _settle_frame(0, _SETTLE_HALF, (8, 9, 10, 16, 17, 18), "left")
_SETTLE_SINK = _settle_frame(3, _SETTLE_LOAF, (8, 9, 10, 11, 15, 16, 17, 18))
# the content loaf's frame 0, so lie_down hands off to it and sit_up leaves it seamlessly
_LOAF_REST = compose(spot_state("content", "on_top"),
                     [0] * len(SPOT_VARIANTS[("content", "on_top")].tracks))

MOVES["lie_down"] = single_track(_hop_frame(), [
    Frame({}, 150, template=_hop_frame()),
    Frame({}, 140, template=_hop_frame(pose="crouch")),
    Frame({}, 160, template=_SETTLE_LOWER, offset=_LOAF_DROP),
    Frame({}, 160, template=_SETTLE_SINK, offset=_LOAF_DROP),
    Frame({}, 300, template=_LOAF_REST, offset=_LOAF_DROP),
], loop_from=None)

MOVES["sit_up"] = single_track(_hop_frame(), [
    Frame({}, 150, template=_LOAF_REST, offset=_LOAF_DROP),
    Frame({}, 120, template=_SETTLE_SINK, offset=_LOAF_DROP),
    Frame({}, 120, template=_SETTLE_LOWER, offset=_LOAF_DROP),
    Frame({}, 140, template=_hop_frame(pose="crouch")),
    Frame({}, 250, template=_hop_frame()),
], loop_from=None)

# State changes (#134). Each lands on the target's own frame 0, so the
# hand-off to its loop is seamless.


def _at(state: str, **positions: int) -> List[str]:
    """A state's composed grid with the named tracks at those frames, the rest at 0."""
    return compose(state, [positions.get(track.name, 0) for track in get_tracks(state)])


def _laptop(dc: int = 0, lid_top: int = 14) -> Dict[Tuple[int, int], str]:
    """The working laptop lit (the room not yet dark), moved dc columns with
    whatever leaves the grid dropped, its lid folded down to row lid_top: 14
    is open, 20 is shut."""
    cells = {}
    for r in range(lid_top, 24):
        for c, ch in enumerate(WORKING_TEMPLATE[r]):
            if ch in "gSP" and 0 <= c + dc < 28:
                cells[(r, c + dc)] = "g"
    if lid_top <= 17 and 0 <= 5 + dc < 28:
        cells[(17, 5 + dc)] = "B"  # the logo on the back of the lid
    return cells


def _thinking_to_working() -> Animation:
    """The thought pops, the cat pulls the laptop over from the left, and the
    room goes dark around the screen."""
    clear = _at("thinking", bubble=2)
    return single_track(_at("thinking"), [
        Frame({}, 150, template=_at("thinking")),
        Frame({}, 120, template=_at("thinking", bubble=1)),
        Frame({}, 90, template=_overlay(clear, _laptop(-6))),
        Frame({}, 90, template=_overlay(clear, _laptop(-3))),
        Frame({}, 160, template=_overlay(clear, {**_laptop(), **_PAWS_DOWN})),
        Frame({}, 100, template=_at("working")),
    ], loop_from=None)


def _settle_at_keys() -> Animation:
    """Into working from anywhere without its own entry: the cat sits up,
    pulls the laptop over from the left, and the room goes dark."""
    clear = _at("thinking", bubble=2)
    return single_track(clear, [
        Frame({}, 180, template=clear),
        Frame({}, 90, template=_overlay(clear, _laptop(-9))),
        Frame({}, 90, template=_overlay(clear, _laptop(-6))),
        Frame({}, 90, template=_overlay(clear, _laptop(-3))),
        Frame({}, 160, template=_overlay(clear, {**_laptop(), **_PAWS_DOWN})),
        Frame({}, 100, template=_at("working")),
    ], loop_from=None)


def _working_to_thinking() -> Animation:
    """thinking>working run back: the lights come up, the paws lift, the
    laptop slides off to the left, and a thought bubbles up dot by dot."""
    clear = _at("thinking", bubble=2)
    return single_track(_at("working"), [
        Frame({}, 150, template=_at("working")),
        Frame({}, 140, template=_overlay(clear, {**_laptop(), **_PAWS_DOWN})),
        Frame({}, 120, template=_overlay(clear, _laptop())),
        Frame({}, 90, template=_overlay(clear, _laptop(-3))),
        Frame({}, 90, template=_overlay(clear, _laptop(-6))),
        Frame({}, 90, template=_overlay(clear, _laptop(-9))),
        Frame({}, 260, template=clear),
        Frame({}, 200, template=_at("thinking", bubble=3)),
        Frame({}, 200, template=_at("thinking", bubble=4)),
        Frame({}, 240, template=_at("thinking", bubble=5)),
        Frame({}, 100, template=_at("thinking")),
    ], loop_from=None)


def _rise(base: List[str], target: List[str], drop: int) -> List[str]:
    """`base` with what `target` adds to it moved `drop` rows down and drawn
    behind it, as a prop comes up from behind the body."""
    patches = {}
    for r, row in enumerate(target):
        for c, ch in enumerate(row):
            if ch != base[r][c] and r + drop < len(base) and base[r + drop][c] == ".":
                patches[(r + drop, c)] = ch
    return _overlay(base, patches)


def _notice() -> List[Frame]:
    """The lead-in to a prompt from anywhere: the cat sits up and its ears
    go up, eyes wide (alert with its mark hidden)."""
    return [
        Frame({}, 140, template=_at("thinking", bubble=2)),
        Frame({}, 200, template=_at("alert", bang=1)),
    ]


def _raise_pennant() -> Animation:
    """Into permission from anywhere: the cat notices, its paw shoots up with
    the pole, and the pennant unfurls from the pole outward."""
    flag = _at("permission")

    def furled(from_col: int) -> List[str]:
        return [
            "".join("." if ch == "f" and c < from_col else ch for c, ch in enumerate(row))
            for row in flag
        ]

    return single_track(_at("alert", bang=1), _notice() + [
        Frame({}, 140, template=furled(28)),
        Frame({}, 80, template=furled(24)),
        Frame({}, 80, template=furled(21)),
        Frame({}, 100, template=flag),
    ], loop_from=None)


def _pop_question() -> Animation:
    """Into question from anywhere: the cat notices, tilts its head, and the
    bubble pops up beside it."""
    start = _at("alert", bang=1)
    ask = _at("question")
    tilt = _overlay(ask, {cell: "." for cell in _ask_bubble(0)})
    return single_track(start, _notice() + [
        Frame({}, 180, template=tilt),
        Frame({}, 80, template=_rise(tilt, ask, 4)),
        Frame({}, 80, template=_rise(tilt, ask, 2)),
        Frame({}, 100, template=ask),
    ], loop_from=None)


def _lean(grid: List[str], cols: int) -> List[str]:
    """The grid sheared left by up to `cols` (right when negative) at the top
    row, nothing at the bottom: the cat tipping over."""
    rows = []
    for r, row in enumerate(grid):
        dc = round(cols * (len(grid) - 1 - r) / (len(grid) - 1))
        rows.append(row[dc:] + "." * dc if dc >= 0 else "." * -dc + row[:dc])
    return rows


def _drop(grid: List[str], rows: int) -> List[str]:
    """The grid moved `rows` down, its bottom rows cut off by the ground."""
    return ["." * len(grid[0])] * rows + grid[:len(grid) - rows]


def _panicked_to_flopped() -> Animation:
    """The cap hits mid-panic: the alarm drains out of the cat, its eyes go
    heavy, it wobbles, tips over and rolls onto its back (flopped>stirring's
    roll run backward), and its legs flop up."""
    spent = [row.replace("!", ".") for row in _at("panicked")]
    spent = _overlay(spent, {(7, 10): LID_CHAR, (7, 16): LID_CHAR})
    roll = get_frames(transition_name("flopped", "stirring"))
    return single_track(_at("panicked"), [
        Frame({}, 120, template=_at("panicked")),
        Frame({}, 300, template=spent),
        Frame({}, 140, template=_lean(spent, -1)),
        Frame({}, 120, template=_lean(spent, 3)),
        Frame({}, 80, template=_lean(spent, 7)),
        Frame({}, 70, template=_lean(_drop(spent, 2), 9)),
        Frame({}, 140, template=_ROLL_CURL),
        Frame({}, 160, template=roll[1]),
        Frame({}, 100, template=_at("flopped")),
    ], loop_from=None)


# The play bow of a cat stretching awake: head down between front paws
# pushed out along the ground, rump and tail up, eyes squeezed shut.
_STRETCH_BOW: List[str] = [
    "............................",
    "..........................y.",
    ".........................yy.",
    ".........................Y..",
    "........................YY..",
    "........................Y...",
    "...................ooooYY...",
    ".................ooooooooo..",
    "....xx......xx..oossoooooo..",
    "...xxpx...xpxxooooooosooOo..",
    "...xxpooooopxxooosooooooOo..",
    "...oooooooooooooooooooooOo..",
    "...oooosssoooooooooossoOOo..",
    "..oooooooooooooooooooooOo...",
    "..oossooooossooooooooooOo...",
    "..ooooooooooooooooooOooOo...",
    "..ooo--ooo--oooooooOo.ooo...",
    "..oooxxxnxxxooooooOo..ooo...",
    "..ooxxwwwwwxxooooOo...ooo...",
    "...oxxwwwwwxxooooo....ooo...",
    "....oxxwwwxxoooo......ooo...",
    "mmooooooooooooo.......ooo...",
    "mmooooooooooooo......mmoo...",
    "............................",
    "............................",
    "............................",
]


def _stirring_to_waking() -> Animation:
    """The cat stretches awake: from lying it pushes its front paws out into
    a play bow, rump and tail up, then sits up into waking."""
    return single_track(_at("stirring"), [
        Frame({}, 200, template=_at("stirring")),
        Frame({}, 160, template=_drop(_STRETCH_BOW, 1)),
        Frame({}, 600, template=_STRETCH_BOW),
        Frame({}, 140, template=_at("stirring")),
        Frame({}, 110, template=_drop(_at("waking"), 3)),
        Frame({}, 100, template=_drop(_at("waking"), 1)),
        Frame({}, 100, template=_at("waking")),
    ], loop_from=None)


# scratch (#124): the cat stretches out low facing the way it walks, rump and
# tail up, and rakes whatever it stands on (the card's top edge, or the card
# inside) with its front paws in turn, bright claw marks where they drag.
# The walking cat's head, lowered to between the shoulders.
_SCRATCH_BODY = {8: (4, 9), 9: (3, 11), 10: (2, 12), 11: (2, 13), 12: (2, 14),
                 13: (2, 15), 14: (2, 16), 15: (2, 17), 16: (2, 18), 17: (2, 18),
                 18: (2, 18), 19: (3, 19), 20: (3, 19), 21: (3, 19), 22: (12, 19)}
_SCRATCH_TAIL = ((8, 3), (7, 2), (6, 2), (5, 2), (4, 2), (3, 3), (2, 4), (1, 5))


def _scratch_frame(near: int = 0, far: int = 0) -> List[str]:
    """The reach with the near (front row 23-24) and far (row 25) forepaws
    pulled back `near` / `far` cells from full stretch, claw marks ahead of a
    pulled paw."""
    rows = [["."] * 28 for _ in range(26)]
    for r, (first, last) in _SCRATCH_BODY.items():
        if r >= 17:  # under the hips only the haunch and the chest are body
            belly = 7 + 2 * (r - 17)
            for c in range(first, last + 1):
                if c < 7 or c >= belly:
                    rows[r][c] = "o"
        else:
            rows[r][first:last + 1] = ["o"] * (last - first + 1)
    for r, c in ((9, 6), (9, 7), (10, 9), (10, 10), (11, 5), (11, 6), (12, 11), (12, 12)):
        rows[r][c] = "s"
    # hind legs: the far one shaded behind the near one
    for r in range(17, 25):
        rows[r][6] = rows[r][7] = "O"
        rows[r][3] = rows[r][4] = "o"
    rows[25][3] = rows[25][4] = "m"
    rows[25][6] = rows[25][7] = "m"
    # the head, from the walking cat, eight rows lower and two columns back
    for r in range(3, 13):
        for c in range(16, 28):
            ch = _WALK_STAND[r][c]
            if ch != "." and c - 2 >= 0:
                rows[r + 9][c - 2] = ch
    # forelegs out along the ground: far (shaded) on row 25, near on rows 23-24
    far_end = 25 - far
    for c in range(14, far_end):
        rows[25][c] = "O"
    rows[25][far_end] = rows[25][far_end + 1] = "m"
    near_end = 23 - near
    for r in (23, 24):
        for c in range(13, near_end):
            rows[r][c] = "o"
    rows[24][near_end] = rows[24][near_end + 1] = "m"
    rows[23][near_end] = "o"
    if near:
        for c in range(near_end + 3, min(28, near_end + 3 + near), 2):
            rows[24][c] = "B"
    if far:
        for c in range(far_end + 3, min(28, far_end + 3 + far), 2):
            rows[25][c] = "B"
    for i, (r, c) in enumerate(_SCRATCH_TAIL):
        rows[r][c] = rows[r][c + 1] = "Y" if i // 2 % 2 == 0 else "y"
    return ["".join(row) for row in rows]


_SCRATCH_REACH = _scratch_frame()


MOVES["scratch"] = single_track(_hop_frame(), [
    Frame({}, 150, template=_hop_frame()),
    Frame({}, 120, template=_hop_frame(pose="crouch")),
    Frame({}, 140, template=_WALK_STAND),
    Frame({}, 400, template=_SCRATCH_REACH),
    Frame({}, 110, template=_scratch_frame(near=4)),
    Frame({}, 110, template=_SCRATCH_REACH),
    Frame({}, 110, template=_scratch_frame(far=4)),
    Frame({}, 110, template=_SCRATCH_REACH),
    Frame({}, 110, template=_scratch_frame(near=4)),
    Frame({}, 110, template=_SCRATCH_REACH),
    Frame({}, 110, template=_scratch_frame(far=4)),
    Frame({}, 300, template=_SCRATCH_REACH),
    Frame({}, 140, template=_WALK_STAND),
    Frame({}, 120, template=_hop_frame(pose="crouch")),
    Frame({}, 250, template=_hop_frame()),
], loop_from=None)


# scratch_stats / scratch_edge (#124): a cat inside the card steps over to the
# stats column, or to the card's own near side, rears up and claws that edge,
# front paws dragging down in turn. One drawing facing left, turned around for
# the stats; inside_right mirrors the whole move.
_SCRATCH_UP_BODY = {8: (9, 15), 9: (8, 15), 10: (8, 15), 11: (7, 15), 12: (7, 16),
                    13: (7, 16), 14: (8, 16), 15: (8, 17), 16: (8, 17), 17: (8, 18),
                    18: (8, 18), 19: (8, 19), 20: (8, 19), 21: (9, 18), 22: (10, 17)}
_SCRATCH_UP_TAIL = ((21, 17), (22, 18), (23, 19), (23, 21), (23, 23), (22, 24),
                    (21, 25), (20, 25))
_SCRATCH_UP_STRIPES = ((11, 13), (11, 14), (13, 14), (13, 15), (15, 15), (15, 16),
                       (17, 16), (17, 17))


def _scratch_upright(near: int = 0, far: int = 0,
                     paws: Optional[Sequence[Tuple[int, int, str]]] = None) -> List[str]:
    """Up on the hind legs facing left, forepaws on the grid's left edge: the
    near paw at rows 11-12 and the far one at 14-15, each dragged `near` /
    `far` rows down with claw marks above it. `paws` instead puts the far
    and then the near paw's top-left cell at (row, col), drawn behind the
    head ("o" near, "O" far), with no claw marks; a paw left out is not drawn."""
    rows = [["."] * 28 for _ in range(26)]

    def put(r: int, c: int, ch: str, behind: bool = False) -> None:
        if 0 <= r < 26 and 0 <= c < 28 and not (behind and rows[r][c] != "."):
            rows[r][c] = ch

    for i, (r, c) in enumerate(_SCRATCH_UP_TAIL):
        ch = "Y" if i // 2 % 2 == 0 else "y"
        put(r, c, ch)
        put(r, c + 1, ch)
    for r, (first, last) in _SCRATCH_UP_BODY.items():
        for c in range(first, last + 1):
            put(r, c, "o")
        put(r, last, "O")
    for r in range(12, 21):  # the belly, on the side toward the card's edge
        for c in range(_SCRATCH_UP_BODY[r][0], _SCRATCH_UP_BODY[r][0] + 3):
            put(r, c, "u")
    for r, c in _SCRATCH_UP_STRIPES:
        put(r, c, "s")
    for r in (23, 24):
        for c, ch in ((9, "o"), (10, "o"), (15, "O"), (16, "O")):
            put(r, c, ch)
    for c in (8, 9, 10, 15, 16, 17):
        put(25, c, "m")
    # the walking cat's head, turned to face the edge and raised
    for r in range(3, 13):
        for c in range(16, 28):
            if _WALK_STAND[r][c] != ".":
                put(r - 3, 31 - c, _WALK_STAND[r][c])
    # forelegs out to the edge, the far (shaded) one under the near one
    behind = paws is not None
    if paws is None:
        paws = ((14 + far, 0, "O"), (11 + near, 0, "o"))
    for (shoulder_r, shoulder_c), (paw_r, paw_c, ch) in zip(((15, 10), (13, 9)), paws):
        for c in range(paw_c + 2, shoulder_c + 1):
            r = round(paw_r + (shoulder_r - paw_r) * (c - paw_c - 2) / (shoulder_c - paw_c - 2))
            put(r, c, ch, behind)
            put(r + 1, c, ch, behind)
        for r in (paw_r, paw_r + 1):
            put(r, paw_c, "m")
            put(r, paw_c + 1, "m")
    if behind:
        return ["".join(row) for row in rows]
    for r in range(11, 11 + near, 2):
        put(r, 0, "B")
    for r in range(14, 14 + far, 2):
        put(r, 0, "B")
    return ["".join(row) for row in rows]


def _facing_left(grid: List[str]) -> List[str]:
    return [row[::-1] for row in grid]


def _upright_scratch(toward_stats: bool) -> Animation:
    """Walk to a vertical edge, rear up and claw it in four strokes, walk
    back. Toward the stats: three steps right put the forepaws (columns 26-27)
    flush with the stats column's left edge, STATS_X (132 = 8 + 3 * 4 + 28 * 4).
    Otherwise two steps left put them on the card's own side (0 = 8 - 2 * 4)."""
    turn = (lambda g: g) if toward_stats else _facing_left
    back = _facing_left if toward_stats else (lambda g: g)
    up = (lambda g: _facing_left(g)) if toward_stats else (lambda g: g)
    sign = 1 if toward_stats else -1
    steps = (("contact", "up"), ("pass", "sway"), ("contact2", "up"))[:3 if toward_stats else 2]
    at = (sign * len(steps), 0)
    reach = up(_scratch_upright())
    frames = [
        Frame({}, 150, template=_hop_frame()),
        Frame({}, 120, template=_hop_frame(pose="crouch")),
        Frame({}, 140, template=turn(_WALK_STAND)),
    ]
    frames += [Frame({}, 130, template=turn(_walk_frame(feet, tail)), offset=(sign * i, 0))
               for i, (feet, tail) in enumerate(steps, 1)]
    frames += [Frame({}, 120, template=_drop(reach, 2), offset=at),
               Frame({}, 350, template=reach, offset=at)]
    for paw in ("near", "far", "near", "far"):
        frames += [Frame({}, 110, template=up(_scratch_upright(**{paw: 3})), offset=at),
                   Frame({}, 110, template=reach, offset=at)]
    frames[-1] = Frame({}, 300, template=reach, offset=at)
    frames.append(Frame({}, 120, template=_drop(reach, 2), offset=at))
    frames += [Frame({}, 130, template=back(_walk_frame(feet, tail)), offset=(sign * i, 0))
               for i, (feet, tail) in reversed(list(enumerate(steps[:-1], 1)))]
    frames += [Frame({}, 140, template=back(_WALK_STAND)),
               Frame({}, 120, template=_hop_frame(pose="crouch")),
               Frame({}, 250, template=_hop_frame())]
    return single_track(_hop_frame(), frames, loop_from=None)


MOVES["scratch_stats"] = _upright_scratch(toward_stats=True)
MOVES["scratch_edge"] = _upright_scratch(toward_stats=False)


# bat (#127, #135): a bar ticked up, so the cat steps over to the stats as
# for scratch_stats, rears up and swats the session bar's end twice (it sits
# at rows 5-6 of the cat's grid in either card design), then walks back. The
# swatting paw is drawn by hand: cocked up by the ear, flung out level with
# the bar beside the face, pulled back to the chin in between. Facing left.
def _bat_arm(paw: Tuple[int, int], arm: Sequence[Tuple[int, int]]) -> Dict[Tuple[int, int], str]:
    r, c = paw
    return {**{cell: "o" for cell in arm}, (r, c): "m", (r, c + 1): "m", (r + 1, c): "m",
            (r + 1, c + 1): "m"}


_BAT_COCK = _bat_arm((0, 3), ((2, 3), (2, 4), (3, 4)))
_BAT_SWAT = _bat_arm((5, 0), ((5, 2), (6, 2), (6, 3), (7, 2), (7, 3), (8, 2), (8, 3), (9, 3),
                              (9, 4), (10, 4), (10, 5), (10, 6), (10, 7), (11, 5), (11, 6)))
_BAT_CHIN = _bat_arm((9, 2), ((10, 4), (10, 5), (10, 6), (10, 7), (11, 5), (11, 6)))


def _bat_upright(arm: Dict[Tuple[int, int], str]) -> List[str]:
    """Up on the hind legs facing left, the far foreleg hidden behind the
    body and the near one at `arm`."""
    return _overlay(_scratch_upright(paws=()), arm)


def _bat() -> Animation:
    at = (3, 0)
    steps = (("contact", "up"), ("pass", "sway"), ("contact2", "up"))
    chin, cock, swat = (_facing_left(_bat_upright(a)) for a in (_BAT_CHIN, _BAT_COCK, _BAT_SWAT))
    frames = [
        Frame({}, 120, template=_hop_frame()),
        Frame({}, 100, template=_hop_frame(pose="crouch")),
        Frame({}, 120, template=_WALK_STAND),
    ]
    frames += [Frame({}, 110, template=_walk_frame(feet, tail), offset=(i, 0))
               for i, (feet, tail) in enumerate(steps, 1)]
    frames += [Frame({}, 100, template=_drop(chin, 2), offset=at),
               Frame({}, 160, template=chin, offset=at),
               Frame({}, 180, template=cock, offset=at),
               Frame({}, 60, template=swat, offset=at),
               Frame({}, 200, template=swat, offset=at),
               Frame({}, 100, template=chin, offset=at),
               Frame({}, 140, template=cock, offset=at),
               Frame({}, 60, template=swat, offset=at),
               Frame({}, 300, template=swat, offset=at),
               Frame({}, 140, template=chin, offset=at),
               Frame({}, 100, template=_drop(chin, 2), offset=at)]
    frames += [Frame({}, 110, template=_facing_left(_walk_frame(feet, tail)), offset=(i, 0))
               for i, (feet, tail) in reversed(list(enumerate(steps[:-1], 1)))]
    frames += [Frame({}, 120, template=_facing_left(_WALK_STAND)),
               Frame({}, 100, template=_hop_frame(pose="crouch")),
               Frame({}, 200, template=_hop_frame())]
    return single_track(_hop_frame(), frames, loop_from=None)


MOVES["bat"] = _bat()


# pounce (#127, #135): a session window reset without capping. The cat turns
# side on toward the stats, drops into a hunting crouch, wiggles its rear,
# springs forward in an arc and lands front paws first, four cells on (its
# paws at most flush with the stats column), then trots back. Facing right.
_POUNCE_REACH = 4


def _side_frame(legs: Sequence[Tuple[int, int, int, int, str]],
                tail: Sequence[Tuple[int, int]], drop: int = 0, rear: int = 0,
                head: int = 0) -> List[str]:
    """The walking body side on, `drop` rows lower, its rear half (columns
    before 12) raised `rear` rows and its head (columns from 16, rows 3-11)
    lowered `head` rows; each leg runs two cells wide from (hip row, hip col)
    to its paw at (paw row, paw col); `tail` lists the tail's cells from the
    rump out, two wide."""
    rows = [["."] * 28 for _ in range(26)]
    # the raised rear is painted over the rear where it was, so the haunches
    # bunch up rather than tearing away from the back
    for lift in sorted({0, rear}):
        for r, line in enumerate(_WALK_BODY):
            for c, ch in enumerate(line):
                if ch == "." or (lift and c >= 12):
                    continue
                dr = drop - lift + (head if c >= 16 and r <= 11 else 0)
                if 0 <= r + dr < 26:
                    rows[r + dr][c] = ch
    for hip_r, hip_c, paw_r, paw_c, fur in legs:
        n = max(abs(paw_r - hip_r), abs(paw_c - hip_c), 1)
        for i in range(n):
            r = round(hip_r + (paw_r - hip_r) * i / n)
            c = round(hip_c + (paw_c - hip_c) * i / n)
            for cc in (c, c + 1):
                if 0 <= r < 26 and 0 <= cc < 28 and rows[r][cc] in ".Oo":
                    rows[r][cc] = fur
        for cc in (paw_c, paw_c + 1):
            if 0 <= paw_r < 26 and 0 <= cc < 28:
                rows[paw_r][cc] = "m"
    for i, (r, c) in enumerate(tail):
        for cc in (c, c + 1):
            if 0 <= r < 26 and 0 <= cc < 28 and rows[r][cc] == ".":
                rows[r][cc] = "Y" if i // 2 % 2 == 0 else "y"
    return ["".join(row) for row in rows]


# crouched low, the forepaws tucked under the chest and the haunches folded,
# the tail straight back along the ground with the tip flicking up
def _pounce_crouch(rear: int = 0, tip: int = 0) -> List[str]:
    return _side_frame(
        legs=((22, 15, 25, 16, "O"), (22, 17, 25, 19, "o"),
              (23 - rear, 5, 25, 5, "O"), (23 - rear, 7, 25, 8, "o")),
        tail=((21 - rear, 3), (21 - rear, 2), (22 - rear, 1), (22 - rear, 0))
        + (((21 - rear, 0), (20 - rear, 0)) if tip else ()),
        drop=5, rear=rear, head=1)


_POUNCE_LEAP = _side_frame(  # stretched out, forepaws reaching ahead, hind legs trailing
    legs=((18, 15, 20, 23, "O"), (18, 17, 19, 25, "o"),
          (18, 5, 22, 0, "O"), (18, 7, 21, 1, "o")),
    tail=((12, 4), (12, 3), (12, 2), (11, 1), (11, 0)))
_POUNCE_LAND = _side_frame(  # forepaws down first, the rear still up
    legs=((21, 15, 25, 17, "O"), (21, 17, 25, 19, "o"),
          (19, 5, 23, 3, "O"), (19, 7, 22, 5, "o")),
    tail=((15, 4), (14, 3), (13, 2), (12, 2), (11, 2)), drop=3, rear=2)


def _pounce() -> Animation:
    feet = (("contact", "up"), ("pass", "sway"), ("contact2", "up"), ("pass2", "sway"))
    far = (_POUNCE_REACH, 0)
    frames = [
        Frame({}, 120, template=_hop_frame()),
        Frame({}, 100, template=_hop_frame(pose="crouch")),
        Frame({}, 160, template=_WALK_STAND),
        Frame({}, 400, template=_pounce_crouch()),
    ]
    for _ in range(3):  # the wiggle
        frames += [Frame({}, 90, template=_pounce_crouch(rear=2, tip=1)),
                   Frame({}, 90, template=_pounce_crouch())]
    frames += [
        Frame({}, 250, template=_pounce_crouch(tip=1)),
        Frame({}, 60, template=_POUNCE_LEAP, offset=(1, -2)),
        Frame({}, 70, template=_POUNCE_LEAP, offset=(2, -4)),
        Frame({}, 60, template=_POUNCE_LEAP, offset=(3, -3)),
        Frame({}, 90, template=_POUNCE_LAND, offset=far),
        Frame({}, 300, template=_pounce_crouch(), offset=far),
        Frame({}, 250, template=_WALK_STAND, offset=far),
        Frame({}, 120, template=_facing_left(_WALK_STAND), offset=far),
    ]
    frames += [Frame({}, 110, template=_facing_left(_walk_frame(*feet[(_POUNCE_REACH - i) % 4])),
                     offset=(i, 0))
               for i in range(_POUNCE_REACH - 1, -1, -1)]
    frames += [Frame({}, 120, template=_facing_left(_WALK_STAND)),
               Frame({}, 100, template=_hop_frame(pose="crouch")),
               Frame({}, 200, template=_hop_frame())]
    return single_track(_hop_frame(), frames, loop_from=None)


MOVES["pounce"] = _pounce()


# bat_down (#127, #135): bat for a cat on the card's top edge. It walks along
# the edge until it is over the session bar, lies down there and hangs its
# right forepaw over the edge, eyes down, swats at the bar twice and walks
# back. The loaf is drawn _HANG_LIFT rows higher in the grid and that much
# lower on the card, so the body stays where the loaf lies and the grid has
# room below the edge for the paw: hanging, it reaches the bar's top edge
# (card y 31 in either design).
_HANG_LIFT = 4
_HANG_WALK = 16  # cells along the edge: puts the paw (cols 16-17) at card x 136
_HANG_DROP = (0, _LOAF_DROP[1] + _HANG_LIFT)


def _hang_frame(leg: Sequence[Tuple[int, int]]) -> List[str]:
    """The loaf on the edge looking down, the right forepaw over the edge:
    `leg` lists the (row, left col) of each row of the two-wide foreleg below
    the paws' row, and the paw takes the two rows after the last."""
    grid = _shift_up(_LOAF_REST, _HANG_LIFT)
    paws = 21 - _HANG_LIFT
    cells = {(paws, 15): "o", (paws, 16): "o", (paws, 17): "O", (paws, 18): "o",
             (9, 10): "o", (9, 16): "o", (10, 10): "e", (10, 16): "e"}
    for r, c in leg:
        cells[(r, c)] = "o"
        cells[(r, c + 1)] = "O"
    r, c = leg[-1]
    for pr in (r + 1, r + 2):
        cells[(pr, c)] = cells[(pr, c + 1)] = "m"
    return _overlay(grid, cells)


_HANG_REACH = _hang_frame([(r, 16) for r in range(18, 24)])
_HANG_COCK = _hang_frame([(r, 16) for r in range(18, 20)])
_HANG_SWIPE = _hang_frame([(18, 16), (19, 16), (20, 16), (21, 17), (22, 17), (23, 18)])
_HANG_LOOK = _overlay(_shift_up(_LOAF_REST, _HANG_LIFT),
                      {(9, 10): "o", (9, 16): "o", (10, 10): "e", (10, 16): "e"})


def _on_edge(grid: List[str]) -> Tuple[int, int]:
    """The drop that rests a lying grid's lowest body row (not its tail) on
    the card's top edge, where a sitting cat's row 25 rests."""
    bottom = max(r for r, row in enumerate(grid) if any(ch not in ".yY" for ch in row))
    return (0, len(grid) - 1 - bottom)


# The loaf rolling, at 45, 135, 225 and 315 degrees counter-clockwise, with
# the happy squint: turned from an 8x Scale2x enlargement and sampled back
# down (RotSprite), for the in-between frames of bat_down_roll's roll.
_ROLL_DIAGONALS: Dict[int, List[str]] = {
    45: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "........xxxx.O..............",
        "........xxxxOO..............",
        "........oxpoO..oOOO.........",
        ".......ooxooOOoooOOOO.......",
        ".....ooosooooooooooOOOYyy...",
        ".....oosoosoooooooooOOOyyY..",
        "....oosooso---oooooooOO.YYY.",
        ".xxxxsooooxxoxxoooooooO..Yy.",
        ".xxxxcosoxxxwwxxooocoo....yy",
        ".xxpccsoxxwwwwwwoocco.......",
        ".xxocco--xwnwwwooucm........",
        "..ooooo-owwwwwwuuum.........",
        "..o.ooo-xxwwwwuuum..........",
        "....ooooxxwwwuuuo...........",
        "....ooooxxoouuuo............",
        "....oooooooouum.............",
        "....oosoooooum..............",
        "....osoooooom...............",
        ".....oosoooo................",
        "......soooo.................",
        ".......ooo..................",
    ],
    135: [
        "..........Yyy...............",
        ".........YYY................",
        "........yyY.................",
        "........yy..................",
        "........YOOO................",
        "........OOOoo...............",
        ".......OOOoooo..............",
        ".......OOoooccm.............",
        "......OOoooooccm............",
        "......OOoooooouum...........",
        "......Ooooooooouuo..........",
        "......ooooooxwouuuo.........",
        ".......ooooxxwwwuuum........",
        "....OO.Ooo-xwwwwwuuum.......",
        ".....OOOoo-owwwwwwuuum......",
        "....xxoooo-xxwnwwwooooo.....",
        "....xxpoosoxxwwwwwoooooo....",
        "....xxxxoosoxxxwxxxoooooo...",
        "....xxoosoooox-oxxxoooooo...",
        ".......oosooso---ooooosoo...",
        "........oosoosoooooosoos....",
        "........oooscccooooooso.....",
        "..........oxxccooooooo......",
        "...........xxpoo............",
        "...........xxxxoo...........",
        "...........xxxx.............",
    ],
    225: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "...................ooo......",
        "..................oooos.....",
        ".................oooosoo....",
        "................mooooooso...",
        "...............muooooosoo...",
        "..............muuoooooooo...",
        ".............ouuuooxxoooo...",
        "............ouuuwwwxxoooo...",
        "...........muuuwwwwxx-ooo.o.",
        "..........muuuwwwwwwo-ooooo.",
        ".........mcuoowwwnwx--occoxx",
        "........occoowwwwwwxxosccpxx",
        ".yy....oocoooxxwwxxxosocxxxx",
        "..yY..Ooooooooxxoxxoooosxxxx",
        "..YYY.OOooooooo---osoosoo...",
        "...YyyOOOooooooooosoosoo....",
        "....yyYOOOoooooooooosooo....",
        "........OOOOoooOOooxoo......",
        "..........OOOo..Oopxo.......",
        "...............OOxxxx.......",
        "...............O.xxxx.......",
    ],
    315: [
        "..............xxxx..........",
        "............ooxxxx..........",
        ".............oopxx..........",
        ".......oooooooccxxo.........",
        "......osoooooocccsooo.......",
        ".....soosoooooosoosoo.......",
        "....oosooooo---osoosoo......",
        "....ooooooxxxo-xoooosooxx...",
        "....ooooooxxxwxxxosooxxxx...",
        ".....oooooowwwwwxxosoopxx...",
        "......ooooowwwnwxx-ooooxx...",
        ".......muuuwwwwwwo-ooOOO....",
        "........muuuwwwwwx-ooO.OO...",
        ".........muuuwwwxxoooo......",
        "..........ouuuowxooooo......",
        "...........ouuooooooooO.....",
        "............muuooooooOO.....",
        ".............mccoooooOO.....",
        "..............mccoooOO......",
        "...............ooooOOO......",
        "................ooOOO.......",
        ".................OOOY.......",
        "...................yy.......",
        "..................Yyy.......",
        ".................YYY........",
        "................yyY.........",
    ],
}


def _turn_ccw(grid: List[str]) -> List[str]:
    """The drawing turned a quarter counter-clockwise about its own centre
    column, at the top of a blank grid of the same size."""
    cells = [(r, c) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch != "."]
    top, bottom = min(r for r, _ in cells), max(r for r, _ in cells)
    left, right = min(c for _, c in cells), max(c for _, c in cells)
    h, w = bottom - top + 1, right - left + 1
    first = (left + right + 1) // 2 - h // 2
    rows = [["."] * len(grid[0]) for _ in grid]
    for r, c in cells:
        # (r, c) in the box -> row (w - 1 - (c - left)), col (r - top)
        rows[w - 1 - (c - left)][first + r - top] = grid[r][c]
    return ["".join(row) for row in rows]


def _body_mid(grid: List[str]) -> float:
    """The centre column of the drawing's body, not its tail."""
    cols = [c for row in grid for c, ch in enumerate(row) if ch not in ".yY"]
    return (min(cols) + max(cols)) / 2


def _rolled(grid: List[str], mid: float) -> Tuple[int, int]:
    """The offset that puts a rolling frame's body centred on column `mid`
    (halves rounding the same way every time) and resting on the ground, so a
    roll moves evenly however each turned frame sits in its grid."""
    return (math.floor(mid - _body_mid(grid) + 0.5), _on_edge(grid)[1])


def _bat_down(roll_home: bool = False) -> Animation:
    """bat_down; with `roll_home` the cat rolls back along the edge instead of
    walking: one roll over facing us, the loaf turning an eighth at a time,
    and it stays lying in the loaf where it started (LYING_MOVES)."""
    feet = (("contact", "up"), ("pass", "sway"), ("contact2", "up"), ("pass2", "sway"))
    at = (_HANG_WALK, 0)
    lying = (_HANG_WALK, _LOAF_DROP[1])
    hang = (_HANG_WALK, _HANG_DROP[1])
    frames = [
        Frame({}, 120, template=_hop_frame()),
        Frame({}, 100, template=_hop_frame(pose="crouch")),
        Frame({}, 120, template=_WALK_STAND),
    ]
    frames += [Frame({}, 100, template=_walk_frame(*feet[(i - 1) % 4]), offset=(i, 0))
               for i in range(1, _HANG_WALK + 1)]
    frames += [Frame({}, 120, template=_WALK_STAND, offset=at),
               Frame({}, 120, template=_hop_frame(pose="crouch"), offset=at),
               Frame({}, 140, template=_SETTLE_LOWER, offset=lying),
               Frame({}, 140, template=_SETTLE_SINK, offset=lying),
               Frame({}, 200, template=_LOAF_REST, offset=lying),
               Frame({}, 300, template=_HANG_LOOK, offset=hang),
               Frame({}, 150, template=_HANG_COCK, offset=hang),
               Frame({}, 350, template=_HANG_REACH, offset=hang),
               Frame({}, 140, template=_HANG_COCK, offset=hang),
               Frame({}, 60, template=_HANG_SWIPE, offset=hang),
               Frame({}, 200, template=_HANG_SWIPE, offset=hang),
               Frame({}, 120, template=_HANG_COCK, offset=hang),
               Frame({}, 60, template=_HANG_SWIPE, offset=hang),
               Frame({}, 300, template=_HANG_SWIPE, offset=hang),
               Frame({}, 140, template=_HANG_COCK, offset=hang),
               Frame({}, 200, template=_HANG_LOOK, offset=hang)]
    if roll_home:
        # eyes squeezed into the happy arch while it rolls
        squint = compose(spot_state("content", "on_top"), [7, 0, 0, 0, 0])
        frames.append(Frame({}, 250, template=_LOAF_REST, offset=lying))
        quarter = squint
        home = _body_mid(squint)
        for eighth in range(1, 8):
            if eighth % 2:
                grid = _ROLL_DIAGONALS[45 * eighth]
            else:
                grid = quarter = _turn_ccw(quarter)
            frames.append(Frame({}, 70, template=grid,
                                offset=_rolled(grid, home + _HANG_WALK - 2 * eighth)))
        # it stays down: the move ends in the loaf, as lie_down does
        frames += [Frame({}, 110, template=squint, offset=_LOAF_DROP),
                   Frame({}, 500, template=_LOAF_REST, offset=_LOAF_DROP)]
        return single_track(_hop_frame(), frames, loop_from=None)
    frames += [Frame({}, 150, template=_LOAF_REST, offset=lying),
               Frame({}, 120, template=_SETTLE_SINK, offset=lying),
               Frame({}, 120, template=_SETTLE_LOWER, offset=lying),
               Frame({}, 120, template=_hop_frame(pose="crouch"), offset=at),
               Frame({}, 120, template=_facing_left(_WALK_STAND), offset=at)]
    frames += [Frame({}, 100, template=_facing_left(_walk_frame(*feet[(_HANG_WALK - i) % 4])),
                     offset=(i, 0))
               for i in range(_HANG_WALK - 1, -1, -1)]
    frames += [Frame({}, 120, template=_facing_left(_WALK_STAND)),
               Frame({}, 100, template=_hop_frame(pose="crouch")),
               Frame({}, 200, template=_hop_frame())]
    return single_track(_hop_frame(), frames, loop_from=None)


MOVES["bat_down"] = _bat_down()
MOVES["bat_down_roll"] = _bat_down(roll_home=True)


# roll (#127 play): the cat lies down, rolls over facing us onto its back
# toward the stats side (a cell per eighth of a turn, so four cells at most),
# wriggles there with its paws batting the air, and rolls back the way it
# came to sit where it started.
# The loaf rolling over clockwise onto its back (#127 roll), at 45 and 135
# degrees and rocking 15 degrees either side of belly-up: turned from an 8x
# Scale2x enlargement and sampled back down, like _ROLL_DIAGONALS.
_ROLL_OVER: Dict[int, List[str]] = {
    -45: [
        "..............xxxx..........",
        "............ooxxxx..........",
        ".............oopxx..........",
        ".......oooooooccxxo.........",
        "......osoooooocccsooo.......",
        ".....soosoooooosoosoo.......",
        "....oosoooooooeosoosoo......",
        "....ooooooxxooxxoooosooxx...",
        "....ooooooxxxxxxxosooxxxx...",
        ".....oooooowwwwwxxosoopxx...",
        "......ooooowwwnwxxeooooxx...",
        ".......muuuwwwwwxxeooOOO....",
        "........muuuwwwwwxoooO.OO...",
        ".........muuuwwwxxoooo......",
        "..........ouuuowxoooooo.....",
        "...........ouuooooooooO.....",
        "............muuooooooOO.....",
        ".............mccoooooOO.....",
        "..............mccoooOO......",
        "...............ooooOOO......",
        "................ooOOO.......",
        ".................OOOY.......",
        "...................yy.......",
        "..................Yyy.......",
        ".................YYY........",
        "................yyY.........",
    ],
    -135: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "...................ooo......",
        "..................oooos.....",
        ".................oooosoo....",
        "................mooooooso...",
        "...............muooooosoo...",
        "..............muuoooooooo...",
        ".............ouuuooxxoooo...",
        "............ouuuwwwxxoooo...",
        "...........muuuwwwwxooooo.o.",
        "..........muuuwwwwwxooooooo.",
        ".........mcuoowwwnwxxeoccoxx",
        "........occoowwwwwwxxosccpxx",
        ".yy....oocoooxxwxxxxosocxxxx",
        "..yY..Ooooooooxxxxxoooosxxxx",
        "..YYY.OOooooooooeeosoosoo...",
        "...YyyOOOooooooooosoosoo....",
        "....yyYOOOoooooooooosooo....",
        "........OOOOoooOOooxoo......",
        "..........OOOo..Oopxo.......",
        "...............OOxxxx.......",
        "...............O.xxxx.......",
    ],
    -165: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        ".....................oooo...",
        "..y...............mmoooooo..",
        "..y...........ooomuuoooooo..",
        "..YY......mmmmuuuuuoooosss..",
        "..YY..Oooocuuuuuuuoooooooos.",
        "..Yy.OOocccoouwwwwwwxooosso.",
        "..yyOOoooocooowwwwwwxxooooo.",
        "....YOOoooooxwwwwwwwxooooo..",
        ".....OOoooooxxwwnwwxooooo...",
        ".....OOooooooxxxwwxxeoooo...",
        "......OOoooooooxxxxooooo....",
        ".......Oooooooeoooossccooo..",
        "........ooooooossooooccpxx..",
        "...........OOOooooossxxxxx..",
        "...........Ooooosssoooxxx...",
        "..........OOxpxxoooo...xx...",
        "...........xxxxo..o.........",
        "............xxx.............",
        ".............xx.............",
    ],
    -195: [
        "............................",
        "............................",
        "............................",
        "....y.......................",
        "...YY.......................",
        "...YY.......................",
        "...yy.......................",
        "...yOOOoo...................",
        "...YOOoooomm................",
        "....OOoocccummoo............",
        "....OOoooccuuuuuommm........",
        "...OOooooooouuuuuuuuoooo....",
        "....OOooooowxwwwuuuoooooo...",
        "....OOooooxwwwwwwoooooooo...",
        "....Ooooooxwwwwwwwoooosoo...",
        ".....ooooooxwwnwwwxxoooss...",
        "......Ooooexxwwwxxxooosoo...",
        ".......Ooooxxxxxoooooooss...",
        ".....OOOOosooooxeooooooo....",
        ".....xxoooossosooooooo......",
        ".....xxpxxsoooosccooo.......",
        ".....xxxxoosssoccco.........",
        "......xx..oooosxxpooo.......",
        ".......x...oo.ooxxxx........",
        "................xxx.........",
        "................xx..........",
    ],
}


def _paw_up(grid: List[str], left: bool) -> List[str]:
    """Belly up: one of the paws (the top row of "m") raised two rows on its
    leg."""
    top = min(r for r, row in enumerate(grid) if "m" in row)
    cols = [c for c, ch in enumerate(grid[top]) if ch == "m"]
    runs = [[cols[0]]]
    for c in cols[1:]:
        if c == runs[-1][-1] + 1:
            runs[-1].append(c)
        else:
            runs.append([c])
    run = runs[0] if left else runs[-1]
    mid = run[len(run) // 2 - 1:len(run) // 2 + 1]
    cells = {(top, c): "o" for c in run}
    cells.update({(top - 1, c): "o" for c in mid})
    cells.update({(top - 2, c): "m" for c in mid})
    cells.update({(top - 3, c): "m" for c in mid})
    return _overlay(grid, cells)


def _no_tail(grid: List[str]) -> List[str]:
    return [row.replace("y", ".").replace("Y", ".") for row in grid]


def _roll() -> Animation:
    """Lying down the tail tucks out of sight: inside, the loaf's hanging tail
    would reach past the card's bottom, and turned it waves about."""
    loaf, sink, lower = (_no_tail(g) for g in (_LOAF_REST, _SETTLE_SINK, _SETTLE_LOWER))
    # belly up, low enough in the grid that a paw can be raised above it
    back = _drop(_turn_ccw(_turn_ccw(loaf)), 6)
    over = [_no_tail(_ROLL_OVER[-45]), _turn_ccw(back), _no_tail(_ROLL_OVER[-135]), back]
    rock = {d: _no_tail(_ROLL_OVER[d]) for d in (-165, -195)}
    home = _body_mid(loaf)

    def at(grid: List[str], dx: int) -> Tuple[int, int]:
        return _rolled(grid, home + dx)

    frames = [
        Frame({}, 150, template=_hop_frame()),
        Frame({}, 140, template=_hop_frame(pose="crouch")),
        Frame({}, 160, template=lower, offset=_LOAF_DROP),
        Frame({}, 160, template=sink, offset=_LOAF_DROP),
        Frame({}, 400, template=loaf, offset=_LOAF_DROP),
    ]
    frames += [Frame({}, 80, template=g, offset=at(g, i)) for i, g in enumerate(over[:3], 1)]
    frames.append(Frame({}, 300, template=back, offset=at(back, 4)))
    for _ in range(2):  # the wriggle, paws batting the air
        for g, ms in ((rock[-165], 130), (back, 110), (rock[-195], 130), (back, 110),
                      (_paw_up(back, True), 100), (_paw_up(back, False), 100),
                      (_paw_up(back, True), 100), (back, 150)):
            frames.append(Frame({}, ms, template=g, offset=at(g, 4)))
    frames += [Frame({}, 80, template=g, offset=at(g, i))
               for i, g in reversed(list(enumerate(over[:3], 1)))]
    frames += [Frame({}, 300, template=loaf, offset=_LOAF_DROP),
               Frame({}, 120, template=sink, offset=_LOAF_DROP),
               Frame({}, 120, template=lower, offset=_LOAF_DROP),
               Frame({}, 140, template=_hop_frame(pose="crouch")),
               Frame({}, 250, template=_hop_frame())]
    return single_track(_hop_frame(), frames, loop_from=None)


MOVES["roll"] = _roll()


# tail_catch (#127 play): sitting, the cat's eyes follow its tail tip as it
# twitches, it swats at it twice and misses as the tip flicks away, then
# catches it in both forepaws in front of its chest, nibbles it with a few bobs
# of the head, and lets go: the tail springs back to its curl. Front on, so it
# plays in every spot.
_TC_SWAT = {(15, 20): "o", (15, 21): "o", (16, 21): "o",
            (14, 22): "m", (14, 23): "m", (15, 22): "m", (15, 23): "m",
            (24, 14): "o", (24, 15): "o", (24, 16): "o"}
# the tail drawn from the rump across the belly up to the chin, both
# forepaws holding it there
_TC_HELD = {**_tail((21, 19, 20), (20, 18, 19), (19, 17, 18), (18, 16, 17), (17, 15, 16),
                    (16, 14, 15), (15, 13, 14)),
            (16, 11): "m", (16, 12): "m", (17, 11): "m", (17, 12): "m",
            (17, 17): "m", (17, 18): "m", (18, 18): "m", (18, 19): "m",
            **{(24, c): "o" for c in range(10, 17)},
            **{(r, c): "." for r in range(18, 22) for c in range(22, 26)
               if SITTING_TEMPLATE[r][c] in "yY"}}


def _tail_catch_frame(tail: Optional[Dict[Tuple[int, int], str]] = None,
                      eyes: Optional[Dict[Tuple[int, int], str]] = None,
                      extra: Optional[Dict[Tuple[int, int], str]] = None,
                      ear: bool = False, nibble: bool = False) -> List[str]:
    """The sitting cat with its tail tip (rows 12-17 of the curl) in pose
    `tail`, or held in front of it when `tail` is None and `extra` holds it."""
    base = _overlay(SITTING_TEMPLATE, {**_SIT_EYES_OPEN, **(eyes or {})})
    base = [row.replace("A", "n") for row in base]
    clear = {(r, c): "." for r in range(12, 18) for c in range(19, 28) if base[r][c] in "yY"}
    grid = _overlay(base, {**clear, **(tail or {}), **(extra or {}),
                           **(_SIT_EAR_FLICK if ear else {})})
    if ear:
        grid = _overlay(grid, {(1, 18): "."})
    return _sink_head(grid) if nibble else grid


def _tail_catch() -> Animation:
    look, shut, arch = _SIT_EYES_RIGHT, _SIT_EYES_LINE, _SIT_EYES_ARCH
    sit = _tail_catch_frame(_SIT_TAIL_NEUTRAL)
    held = _tail_catch_frame(eyes=_SIT_EYES_OPEN, extra=_TC_HELD)
    frames = [
        Frame({}, 300, template=sit),
        Frame({}, 260, template=_tail_catch_frame(_SIT_TAIL_UP, look)),
        Frame({}, 200, template=_tail_catch_frame(_SIT_TAIL_NEUTRAL, look, ear=True)),
        Frame({}, 160, template=_tail_catch_frame(_SIT_TAIL_IN, look)),
        Frame({}, 200, template=_tail_catch_frame(_SIT_TAIL_UP, look, ear=True)),
        Frame({}, 110, template=_tail_catch_frame(_SIT_TAIL_HIGH, look, _TC_SWAT)),
        Frame({}, 160, template=_tail_catch_frame(_SIT_TAIL_OUT, look)),
        Frame({}, 220, template=_tail_catch_frame(_SIT_TAIL_IN, look)),
        Frame({}, 110, template=_tail_catch_frame(_SIT_TAIL_HIGH, look, _TC_SWAT)),
        Frame({}, 120, template=_tail_catch_frame(_SIT_TAIL_NEUTRAL, look)),
        Frame({}, 350, template=held),
    ]
    for _ in range(3):  # the nibble
        frames += [Frame({}, 170, template=_tail_catch_frame(eyes=shut, extra=_TC_HELD, nibble=True)),
                   Frame({}, 140, template=_tail_catch_frame(eyes=arch, extra=_TC_HELD))]
    frames += [
        Frame({}, 300, template=_tail_catch_frame(eyes=arch, extra=_TC_HELD)),
        Frame({}, 140, template=_tail_catch_frame(_SIT_TAIL_OUT)),
        Frame({}, 140, template=_tail_catch_frame(_SIT_TAIL_UP)),
        Frame({}, 300, template=sit),
    ]
    return single_track(sit, frames, loop_from=None)


MOVES["tail_catch"] = _tail_catch()


# tail_catch_roll (#127 play): tail_catch, but with the tail caught the cat
# hugs it, lies down and rolls over onto its back with it facing us (roll's
# roll), wrestles it there, rolls back and sits up to let it go.
# The loaf hugging its tail: the tail comes round from the right flank across
# the front, both forepaws off the ground and clutching it.
_TC_HUG_LOAF = _overlay(_no_tail(_LOAF_REST), {
    **{(21, c): "o" for c in range(8, 19) if _LOAF_REST[21][c] == "m"},
    **_tail((18, 25, 25), (19, 12, 25), (20, 11, 24)),
    (19, 14): "m", (19, 15): "m", (20, 14): "m", (20, 15): "m",
    (19, 19): "m", (19, 20): "m", (20, 19): "m", (20, 20): "m",
})

# _TC_HUG_LOAF rolling over clockwise, at 45 and 135 degrees and rocking 15
# degrees either side of belly-up, made like _ROLL_OVER.
_TC_HUG_ROLL: Dict[int, List[str]] = {
    -45: [
        "............................",
        "............................",
        "............................",
        "............................",
        "..............xxxx..........",
        "............ooxxxx..........",
        ".............oopxx..........",
        ".......oooooooccxxo.........",
        "......osoooooocccsooo.......",
        ".....soosoooooosoosoo.......",
        "....oosoooooooeosoosoo......",
        "....ooooooxxooxxoooosooxx...",
        "....ooooooxxxxxxxosooxxxx...",
        ".....oooooowwwwwxxosoopxx...",
        "......ooooowwwnwxxeooooxx...",
        ".......ouuuwwwwwxxeooOOO....",
        "........oyyYwwwwwxoooO.OO...",
        ".........oyymwwwxxoooo......",
        "..........ommmowxoooooo.....",
        "...........omyYoooooooO.....",
        "............oyyYoooooOO.....",
        ".............oyymooooOO.....",
        "..............ommYooOO......",
        "...............oyyYOOO......",
        "................oyyYO.......",
        ".................OyyY.......",
    ],
    -135: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "................ooo.........",
        "...............oooos........",
        "..............oooosoo.......",
        ".............oooooooso......",
        "............ouooooosoo......",
        "...........oyuoooooooo......",
        "..........oyyuooxxoooo......",
        ".........omyYwwwxxoooo......",
        "........ommmwwwwxooooo.o....",
        ".......oyymwwwwwxooooooo....",
        "......oyyYowwwnwxxeoccoxx...",
        ".....omyYowwwwwwxxosccpxx...",
        "....oymmooxxwxxxxosocxxxx...",
        "...OyyYooooxxxxxoooosxxxx...",
        "...yyYoooooooeeosoosoo......",
        "...yYOooooooooosoosoo.......",
        "...YOOOoooooooooosooo.......",
        ".....OOOOoooOOooxoo.........",
        ".......OOOo..Oopxo..........",
        "............OOxxxx..........",
        "............O.xxxx..........",
    ],
    -165: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "....................oooo....",
        ".................oooooooo...",
        ".............oooouuoooooo...",
        ".........oooomyyuuoooosss...",
        ".....OoooyyymmYYuoooooooos..",
        "....OOymmYYYmwwwwwwxooosso..",
        "....yyYYmmooowwwwwwxxooooo..",
        "...YYYoooooxwwwwwwwxooooo...",
        "...YOOoooooxxwwnwwxooooo....",
        "....OOooooooxxxwwxxeoooo....",
        ".....OOoooooooxxxxooooo.....",
        "......Oooooooeoooossccooo...",
        ".......ooooooossooooccpxx...",
        "..........OOOooooossxxxxx...",
        "..........Ooooosssoooxxx....",
        ".........OOxpxxoooo...xx....",
        "..........xxxxo..o..........",
        "...........xxx..............",
        "............xx..............",
    ],
    -195: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "......OOo...................",
        "....Yyyyoooo................",
        "...YYYYYmmyyoooo............",
        "....OOoommYYymmyoooo........",
        "...OOoooooooYmmYyyuuoooo....",
        "....OOooooowxwwwYuuoooooo...",
        "....OOooooxwwwwwwoooooooo...",
        "....Ooooooxwwwwwwwoooosoo...",
        ".....ooooooxwwnwwwxxoooss...",
        "......Ooooexxwwwxxxooosoo...",
        ".......Ooooxxxxxoooooooss...",
        ".....OOOOosooooxeooooooo....",
        ".....xxoooossosooooooo......",
        ".....xxpxxsoooosccooo.......",
        ".....xxxxoosssoccco.........",
        "......xx..oooosxxpooo.......",
        ".......x...oo.ooxxxx........",
        "................xxx.........",
        "................xx..........",
    ],
}


def _tail_catch_roll() -> Animation:
    """tail_catch up to the catch, then the hug, the roll and back."""
    catch = list(_tail_catch().tracks[0].frames[:11])
    held = catch[-1].template
    hug = _TC_HUG_LOAF
    back = _drop(_turn_ccw(_turn_ccw(hug)), 6)
    over = [_TC_HUG_ROLL[-45], _turn_ccw(back), _TC_HUG_ROLL[-135]]
    home = _body_mid(hug)

    def at(grid: List[str], dx: int) -> Tuple[int, int]:
        # the hugged tail is part of the bundle, so it rests on the floor too
        bottom = max(r for r, row in enumerate(grid) if row.strip("."))
        return (_rolled(grid, home + dx)[0], len(grid) - 1 - bottom)

    frames = catch + [
        Frame({}, 140, template=_sink_head(held)),
        Frame({}, 300, template=hug, offset=_LOAF_DROP),
    ]
    frames += [Frame({}, 80, template=g, offset=at(g, i)) for i, g in enumerate(over, 1)]
    frames.append(Frame({}, 250, template=back, offset=at(back, 4)))
    for _ in range(3):  # the wrestle
        for g, ms in ((_TC_HUG_ROLL[-165], 110), (back, 90), (_TC_HUG_ROLL[-195], 110), (back, 90)):
            frames.append(Frame({}, ms, template=g, offset=at(g, 4)))
    frames.append(Frame({}, 250, template=back, offset=at(back, 4)))
    frames += [Frame({}, 80, template=g, offset=at(g, i)) for i, g in reversed(list(enumerate(over, 1)))]
    frames += [
        Frame({}, 300, template=hug, offset=_LOAF_DROP),
        Frame({}, 140, template=_sink_head(held)),
        Frame({}, 250, template=held),
        Frame({}, 140, template=_tail_catch_frame(_SIT_TAIL_OUT)),
        Frame({}, 140, template=_tail_catch_frame(_SIT_TAIL_UP)),
        Frame({}, 300, template=_tail_catch_frame(_SIT_TAIL_NEUTRAL)),
    ]
    return single_track(catch[0].template, frames, loop_from=None)


MOVES["tail_catch_roll"] = _tail_catch_roll()


# fly (#127 play): a fly buzzes in from the stats side, loops over the cat's
# head, down the far side and back round. The cat's eyes follow it, an ear
# flicks, it chatters, then swats, missing, faster and faster from side to
# side until a two-pawed grab with a hop, and the fly escapes straight up. A grey body ("g") with a wing pixel in the screen glow
# ("B") that flickers each frame. Sitting front on, so it plays in every spot.
_FLY_PATH = ((4, 27), (3, 26), (2, 25), (1, 24), (0, 22), (0, 19), (0, 16), (0, 13), (0, 10),
             (1, 7), (2, 5), (4, 4), (6, 3), (8, 4), (7, 5), (5, 4), (3, 5), (1, 7), (0, 10),
             (0, 14), (1, 17), (2, 21), (4, 22), (6, 23))
# a forepaw flung up beside the head: the arm two wide, angled out from the
# shoulder and shaded on its outer side so it stands off the face
_FLY_ARM_R = ((13, 19), (12, 20), (11, 20), (10, 21), (9, 21))
_FLY_SWAT_R = {**{(r, c): "o" for r, c in _FLY_ARM_R}, **{(r, c + 1): "O" for r, c in _FLY_ARM_R},
               (7, 22): "m", (7, 23): "m", (8, 22): "m", (8, 23): "m",
               (24, 14): "o", (24, 15): "o", (24, 16): "o"}
_FLY_SWAT_L = {**{(r, 26 - c): "o" for r, c in _FLY_ARM_R},
               **{(r, 25 - c): "O" for r, c in _FLY_ARM_R},
               (7, 3): "m", (7, 4): "m", (8, 3): "m", (8, 4): "m",
               (24, 10): "o", (24, 11): "o", (24, 12): "o"}
_FLY_SWAT_BOTH = {**_FLY_SWAT_R, **_FLY_SWAT_L}
# After the loop the swats come faster and faster, the fly darting from side
# to side over the head, until a two-pawed grab with a hop and it escapes
# straight up: (fly, swat, hold ms, hop rows).
_FLY_FIGHT = (
    ((5, 24), _FLY_SWAT_R, 170, 0), ((3, 25), _FLY_SWAT_R, 120, 0), ((3, 25), None, 120, 0),
    ((1, 19), None, 70, 0), ((0, 13), None, 70, 0), ((2, 6), None, 70, 0),
    ((5, 3), _FLY_SWAT_L, 120, 0), ((3, 2), _FLY_SWAT_L, 90, 0), ((1, 6), None, 60, 0),
    ((0, 12), None, 60, 0), ((2, 21), None, 60, 0), ((4, 24), _FLY_SWAT_R, 80, 0),
    ((1, 22), None, 50, 0), ((0, 9), _FLY_SWAT_L, 60, 0), ((1, 17), _FLY_SWAT_R, 60, 0),
    ((0, 7), _FLY_SWAT_L, 60, 1), ((1, 19), _FLY_SWAT_R, 60, 1), ((0, 13), _FLY_SWAT_BOTH, 90, 2),
    (None, _FLY_SWAT_BOTH, 90, 1),
)
_CHATTER = {(11, 13): LID_CHAR}


def _fly_frame(fly: Optional[Tuple[int, int]], i: int,
               eyes: Dict[Tuple[int, int], str], extra: Optional[Dict] = None) -> List[str]:
    grid = _overlay(SITTING_TEMPLATE, {**eyes, **(extra or {})})
    grid = [row.replace("A", "n") for row in grid]
    if fly is not None:
        r, c = fly
        wing = (r - 1, c + (1 if i % 2 else -1)) if r else (r, c + (1 if i % 2 else -1))
        cells = {(r, c): "g"}
        if 0 <= wing[0] < 26 and 0 <= wing[1] < 28:
            cells[wing] = "B"
        grid = _overlay(grid, {cell: ch for cell, ch in cells.items()
                               if grid[cell[0]][cell[1]] == "."})
    return grid


def _fly() -> Animation:
    sit = _fly_frame(None, 0, _SIT_EYES_OPEN)
    frames = [Frame({}, 250, template=sit)]
    for i, (r, c) in enumerate(_FLY_PATH):
        eyes = _SIT_EYES_RIGHT if c >= 19 else _SIT_EYES_LEFT if c <= 7 else _SIT_EYES_OPEN
        extra = {}
        if i in (4, 5, 17, 18):
            extra.update(_SIT_EAR_FLICK)
        if i in (9, 11, 13, 15):  # chattering at it
            extra.update(_CHATTER)
        frames.append(Frame({}, 100, template=_fly_frame((r, c), i, eyes, extra)))
    for i, (fly, swat, ms, hop) in enumerate(_FLY_FIGHT):
        c = 14 if fly is None else fly[1]
        eyes = _SIT_EYES_RIGHT if c >= 19 else _SIT_EYES_LEFT if c <= 7 else _SIT_EYES_OPEN
        frames.append(Frame({}, ms, template=_fly_frame(fly, i, eyes, swat), offset=(0, -hop)))
    frames += [Frame({}, 400, template=_fly_frame(None, 0, _SIT_EYES_OPEN)),
               Frame({}, 160, template=_fly_frame(None, 0, _SIT_EYES_LINE)),
               Frame({}, 400, template=sit)]
    return single_track(sit, frames, loop_from=None)


MOVES["fly"] = _fly()



# yarn (#127 play): a ball of yarn (the flag's magenta, wound with pink lines
# that turn as it rolls, a thread trailing) rolls in along the floor from the
# card's edge and stops in front of the cat. It looks down at it, bats it from
# paw to paw faster and faster, then winds up and swipes it back off the
# card's edge, and watches it go. Inside only: inside_right mirrors it.
_YARN_BALL = ((".ff.", "fnff", "ffnf", ".ff."), (".ff.", "ffnf", "fnff", ".ff."))
_YARN_TOP = 22  # the ball's top row: it sits on the floor (rows 22-25)
_YARN_L = {(24, 10): "o", (24, 11): "o", (24, 12): "o",
           (24, 6): "m", (24, 7): "m", (25, 6): "m", (25, 7): "m"}
_YARN_R = {(24, 14): "o", (24, 15): "o", (24, 16): "o",
           (24, 19): "m", (24, 20): "m", (25, 19): "m", (25, 20): "m"}
_YARN_R_UP = {(24, 14): "o", (24, 15): "o", (24, 16): "o", (22, 18): "o", (23, 18): "o",
              (20, 17): "m", (20, 18): "m", (21, 17): "m", (21, 18): "m"}
_YARN_L_UP = {(24, 10): "o", (24, 11): "o", (24, 12): "o", (22, 8): "o", (23, 8): "o",
              (20, 8): "m", (20, 9): "m", (21, 8): "m", (21, 9): "m"}
_SIT_EYES_DOWN = {**_SIT_EYES_OPEN, (8, 10): "o", (8, 16): "o", (9, 10): "e", (9, 16): "e"}


def _yarn_frame(col: Optional[int], spin: int, eyes: Dict[Tuple[int, int], str],
                paw: Optional[Dict] = None, sink: bool = False) -> List[str]:
    """The sitting cat with the ball's left edge at `col` (off the grid's left
    edge when negative, gone when None). Its thread lies along the floor from
    the card's edge, where the ball came in, to the ball, in front of the cat."""
    grid = _overlay(SITTING_TEMPLATE, {**eyes, **(paw or {})})
    grid = [row.replace("A", "n") for row in grid]
    if sink:
        grid = _sink_head(grid)
    if col is None:
        return grid
    cells = {}
    for r, line in enumerate(_YARN_BALL[spin % 2]):
        for c, ch in enumerate(line):
            if ch != "." and 0 <= col + c < 28:
                cells[(_YARN_TOP + r, col + c)] = ch
    for c in range(0, col + 1):  # up to the ball's rounded bottom corner
        # in front of the cat like the ball, but under a paw slapping down
        if grid[25][c] != "m" and (25, c) not in cells:
            cells[(25, c)] = "f"
    return _overlay(grid, cells)


def _yarn() -> Animation:
    sit = _yarn_frame(None, 0, _SIT_EYES_OPEN)
    left, right, down = _SIT_EYES_LEFT, _SIT_EYES_RIGHT, _SIT_EYES_DOWN

    def look(col: int) -> Dict[Tuple[int, int], str]:
        return left if col < 8 else right if col > 13 else down

    frames = [Frame({}, 250, template=sit)]
    # rolling in from the edge, slowing to a stop in front of the paws
    for i, (col, ms) in enumerate(((-3, 90), (-1, 90), (1, 100), (3, 110), (5, 130), (7, 170),
                                   (8, 250))):
        frames.append(Frame({}, ms, template=_yarn_frame(col, i, look(col))))
    frames.append(Frame({}, 450, template=_yarn_frame(8, 0, down, sink=True)))
    # paw to paw, faster each time: (slapping paw, the ball's path, hold per step)
    rallies = ((_YARN_L, (10, 12, 14, 15), 110), (_YARN_R, (13, 11, 9, 8), 100),
               (_YARN_L, (11, 14, 15), 80), (_YARN_R, (12, 9, 8), 70),
               (_YARN_L, (12, 15), 60))
    spin = 0
    for paw, path, ms in rallies:
        start = path[0] - 2 if paw is _YARN_L else path[0] + 2
        lift = _YARN_L_UP if paw is _YARN_L else _YARN_R_UP
        frames += [Frame({}, ms, template=_yarn_frame(start, spin, look(start), lift)),
                   Frame({}, 90, template=_yarn_frame(start, spin, look(start), paw))]
        for col in path:
            spin += 1
            frames.append(Frame({}, ms, template=_yarn_frame(col, spin, look(col))))
        frames.append(Frame({}, 150, template=_yarn_frame(path[-1], spin, look(path[-1]))))
    # the wind-up and the big swipe off the card's edge
    frames += [Frame({}, 300, template=_yarn_frame(15, spin, right, _YARN_R_UP)),
               Frame({}, 80, template=_yarn_frame(15, spin, right, _YARN_R))]
    for col in (11, 7, 3, -1, -5):
        spin += 1
        frames.append(Frame({}, 60, template=_yarn_frame(col, spin, left)))
    frames += [Frame({}, 50, template=_yarn_frame(-9, 0, left)),
               Frame({}, 600, template=_yarn_frame(None, 0, left, _SIT_EAR_FLICK)),
               Frame({}, 160, template=_yarn_frame(None, 0, _SIT_EYES_LINE)),
               Frame({}, 400, template=sit)]
    return single_track(sit, frames, loop_from=None)


MOVES["yarn"] = _yarn()


# knock_off (#127 play): on the card's top edge. A glass of water is slid along
# the edge from the card's far end, behind the cat, to a stop on its other
# side. The cat eyes it, looks at us, pats it twice (it slides to the end),
# looks at us again, then nudges it: the glass tips, splashes and tumbles down
# the card's face. It peers down after it, looks back at us and blinks slowly.
# on_top only, and never mirrored (see grid_size).
# open at the top so the shape alone says glass, water in its lower half
_GLASS = ("g..g", "g..g", "gbbg", "gbBg", "gggg")
_GLASS_TIPPED = ("ggggg", "..bBg", "..bbg", "ggggg")  # turned on its side, mouth to the left
_GLASS_TOP = 21  # standing on the edge: rows 21-25
_KO_ARM = {(24, 10): "o", (24, 11): "o", (24, 12): "o"}  # the left forepaw is off the ground
_KO_PAW_UP = {**_KO_ARM, (17, 2): "m", (17, 3): "m", (18, 2): "m", (18, 3): "m",
              (17, 4): "o", (17, 5): "o", (18, 4): "o", (18, 5): "o"}
_KO_PAW_PAT = {**_KO_ARM, (19, 2): "m", (19, 3): "m", (20, 2): "m", (20, 3): "m",
               (19, 4): "o", (20, 4): "o", (20, 5): "o"}
_KO_PAW_PUSH = {**_KO_ARM, (21, 4): "m", (21, 5): "m", (22, 4): "m", (22, 5): "m"}
_KO_SPLASH = ({(21, 0): "b", (20, 1): "B"}, {(19, 0): "b", (18, 1): "b", (20, 2): "B"})


# knock_off's grid reaches from the cat's spot on the top edge (x 8) to the
# card's right end and down to its bottom, so the glass can be slid in from the
# far end and fall down the card's face. The cat sits in its top-left corner.
# theme imports this module, so the card's size and the cat's x on the edge
# are repeated here (a test keeps them equal to theme's).
KO_CARD = (300, 128)
KO_CAT_X = 8
_KO_COLS = (KO_CARD[0] - KO_CAT_X) // SCALE
_KO_ROWS = 26 + KO_CARD[1] // SCALE
# the glass emptied by the tip, tumbling: on its side, upside down, on its
# other side, upright
_GLASS_TUMBLE = (("ggggg", "....g", "....g", "ggggg"), ("gggg", "g..g", "g..g", "g..g", "g..g"),
                 ("ggggg", "g....", "g....", "ggggg"), ("g..g", "g..g", "g..g", "g..g", "gggg"))


def _knock_off_frame(glass: Optional[Tuple[int, int, Tuple[str, ...]]],
                     eyes: Dict[Tuple[int, int], str], paw: Optional[Dict] = None,
                     ear: bool = False, sink: bool = False,
                     drops: Sequence[Tuple[int, int]] = ()) -> List[str]:
    """The sitting cat in the corner of the big grid, the glass's top-left at
    (row, col) in `glass`'s shape (behind the cat where they cross) and water
    drops at `drops`."""
    cat = _overlay(SITTING_TEMPLATE, {**eyes, **(_SIT_EAR_FLICK if ear else {})})
    cat = [row.replace("A", "n") for row in cat]
    if ear:
        cat = _overlay(cat, {(1, 18): "."})
    if sink:
        cat = _sink_head(cat)
    grid = [row + "." * (_KO_COLS - 28) for row in cat] + ["." * _KO_COLS] * (_KO_ROWS - 26)
    cells = {(r, c): "b" for r, c in drops if 0 <= r < _KO_ROWS and 0 <= c < _KO_COLS}
    if glass is not None:
        top, left, shape = glass
        for r, line in enumerate(shape):
            for c, ch in enumerate(line):
                if ch != "." and 0 <= top + r < _KO_ROWS and 0 <= left + c < _KO_COLS \
                        and grid[top + r][left + c] == ".":
                    cells[(top + r, left + c)] = ch
    return _overlay(grid, {**cells, **(paw or {})})


def _knock_off() -> Animation:
    left, right, at_us, down = _SIT_EYES_LEFT, _SIT_EYES_RIGHT, _SIT_EYES_OPEN, _SIT_EYES_DOWN
    standing = (_GLASS_TOP, 1, _GLASS)
    slid = (_GLASS_TOP, 0, _GLASS)
    sit = _knock_off_frame(None, at_us)
    frames = [Frame({}, 300, template=sit)]
    # slid along the edge from the card's far end like a drink down a bar,
    # behind the cat, to a stop on its other side
    far = _KO_COLS - 4
    for col, ms in ((far, 40), (far - 5, 40), (far - 10, 40), (far - 15, 40), (far - 20, 40),
                    (far - 25, 40), (far - 30, 40), (far - 35, 40), (far - 40, 50),
                    (far - 45, 50), (24, 50), (20, 50), (16, 60), (12, 60), (8, 70), (5, 80),
                    (3, 100), (2, 130), (1, 160)):
        eyes = right if col > 18 else left if col < 6 else at_us
        frames.append(Frame({}, ms, template=_knock_off_frame((_GLASS_TOP, col, _GLASS), eyes)))
    frames += [
        Frame({}, 90, template=_knock_off_frame((_GLASS_TOP, 2, _GLASS), left)),
        Frame({}, 400, template=_knock_off_frame(standing, left)),
        Frame({}, 400, template=_knock_off_frame(standing, at_us, ear=True)),
        Frame({}, 300, template=_knock_off_frame(standing, left)),
        Frame({}, 180, template=_knock_off_frame(standing, left, _KO_PAW_UP)),
        Frame({}, 140, template=_knock_off_frame(standing, left, _KO_PAW_PAT)),
        Frame({}, 200, template=_knock_off_frame(standing, left, _KO_PAW_UP)),
        Frame({}, 140, template=_knock_off_frame(slid, left, _KO_PAW_PAT)),
        Frame({}, 250, template=_knock_off_frame(slid, left)),
        Frame({}, 700, template=_knock_off_frame(slid, at_us)),
        Frame({}, 300, template=_knock_off_frame(slid, left)),
        Frame({}, 140, template=_knock_off_frame(slid, left, _KO_PAW_PUSH)),
        Frame({}, 100, template=_knock_off_frame((22, 0, _GLASS_TIPPED), left,
                                                 {**_KO_PAW_PUSH, **_KO_SPLASH[0]})),
    ]
    # down the card's face, tumbling and speeding up, the water falling with it
    for i, (top, ms) in enumerate(((25, 70), (28, 60), (32, 60), (37, 50), (43, 50), (50, 50),
                                   (58, 40))):
        shape = _GLASS_TUMBLE[i % 4]
        drops = ((top - 3, 1), (top - 2, 4), (top - 6, 2)) if i < 4 else ()
        frames.append(Frame({}, ms, template=_knock_off_frame((top, 0, shape), down, drops=drops)))
    frames += [
        Frame({}, 500, template=_knock_off_frame(None, down, sink=True)),
        Frame({}, 500, template=_knock_off_frame(None, at_us)),
        Frame({}, 700, template=_knock_off_frame(None, _SIT_EYES_ARCH)),
        Frame({}, 400, template=sit),
    ]
    return single_track(sit, frames, loop_from=None)


MOVES["knock_off"] = _knock_off()


# wand (#127 play): a cat wand. A feather toy on a string comes down from
# above (the window's top on the edge, the card's top inside: _WAND_ROOM rows
# over the cat), bobs and sways by the cat's head, gets swatted from either
# side, dangles in front of its face, and is yanked up just as the cat
# stretches both paws over its head to grab it, then pulled away out of
# sight. The grid has _WAND_ROOM extra rows on top, the cat sits that much
# lower in it and every frame is drawn that much higher. First-row cards only.
_WAND_ROOM = 3
_WAND_TOY = (".h.", "fqf", "fqf", ".f.")  # the knot the string is tied to, the feathers
_SIT_EYES_UP = {**_SIT_EYES_OPEN, (8, 10): "o", (8, 16): "o", (7, 10): "e", (7, 16): "e"}
# both forepaws stretched up past the ears, meeting over the head
_WAND_REACH = {**{(r, 6): "o" for r in range(2, 14)}, **{(r, 5): "O" for r in range(3, 13)},
               **{(r, 20): "o" for r in range(2, 14)}, **{(r, 21): "O" for r in range(3, 13)},
               (1, 7): "o", (1, 8): "o", (0, 9): "o", (1, 19): "o", (1, 18): "o", (0, 17): "o",
               (-1, 10): "m", (-1, 11): "m", (0, 10): "m", (0, 11): "m",
               (-1, 15): "m", (-1, 16): "m", (0, 15): "m", (0, 16): "m",
               **{(24, c): "o" for c in range(10, 17)}}


def _wand_frame(toy: Optional[Tuple[int, int]], anchor: int,
                eyes: Dict[Tuple[int, int], str], extra: Optional[Dict] = None,
                sink: bool = False) -> List[str]:
    """The sitting cat (`extra` painted over it, rows above it negative) in
    the wand's grid, the toy's top-left at `toy` in the cat's rows and
    columns, and the string from the top of the grid at column `anchor` down
    to the toy's knot, behind the cat."""
    cat = [row.replace("A", "n") for row in _overlay(SITTING_TEMPLATE, eyes)]
    if sink:
        cat = _sink_head(cat)
    grid = [list(row) for row in ["." * 28] * _WAND_ROOM + cat]
    for (r, c), ch in (extra or {}).items():
        if 0 <= r + _WAND_ROOM < len(grid) and 0 <= c < 28:
            grid[r + _WAND_ROOM][c] = ch
    if toy is not None:
        top, left = toy
        knot = (top + _WAND_ROOM, left + 1)
        n = max(knot[0], abs(knot[1] - anchor), 1)
        for i in range(n):
            r = round(knot[0] * i / n)
            c = round(anchor + (knot[1] - anchor) * i / n)
            if 0 <= r < len(grid) and 0 <= c < 28 and grid[r][c] == ".":
                grid[r][c] = "g"
        for r, line in enumerate(_WAND_TOY):
            for c, ch in enumerate(line):
                rr, cc = top + _WAND_ROOM + r, left + c
                if ch != "." and 0 <= rr < len(grid) and 0 <= cc < 28:
                    grid[rr][cc] = ch
    return ["".join(row) for row in grid]


def _wand() -> Animation:
    up, right, left, at_us = _SIT_EYES_UP, _SIT_EYES_RIGHT, _SIT_EYES_LEFT, _SIT_EYES_OPEN
    lift = (0, -_WAND_ROOM)
    steps = [
        (None, 22, at_us, None, 300),
        ((-7, 21), 22, up, None, 90), ((-4, 21), 22, up, None, 90),
        ((-1, 21), 22, right, _SIT_EAR_FLICK, 100), ((2, 21), 22, right, None, 120),
        ((4, 21), 22, right, None, 300), ((5, 21), 23, right, None, 160),
        ((4, 21), 22, right, None, 160), ((3, 22), 23, right, None, 200),
        ((5, 21), 22, right, None, 250),
        ((5, 22), 22, right, _FLY_SWAT_R, 100),          # swat
        ((1, 23), 23, up, _FLY_SWAT_R, 90),
        ((0, 20), 19, up, None, 110), ((-2, 15), 15, up, None, 120),   # over the head
        ((-1, 9), 10, up, None, 120), ((2, 4), 6, left, None, 140),
        ((4, 3), 5, left, None, 300),
        ((5, 2), 4, left, _FLY_SWAT_L, 100),            # swat
        ((1, 1), 3, up, _FLY_SWAT_L, 90),
        ((3, 6), 9, up, None, 120), ((7, 11), 12, at_us, None, 150),   # the tease
        ((9, 12), 13, at_us, None, 600),
    ]
    frames = [Frame({}, ms, template=_wand_frame(toy, anchor, eyes, extra), offset=lift)
              for toy, anchor, eyes, extra, ms in steps]
    frames.insert(-1, Frame({}, 200, template=_wand_frame((8, 12), 13, at_us, sink=True),
                            offset=lift))
    frames += [
        Frame({}, 80, template=_wand_frame((3, 12), 13, up), offset=lift),     # yanked
        Frame({}, 300, template=_wand_frame((-3, 12), 13, up, _WAND_REACH), offset=lift),
        Frame({}, 80, template=_wand_frame((-7, 12), 13, up, _WAND_REACH), offset=lift),
        Frame({}, 500, template=_wand_frame(None, 13, up), offset=lift),
        Frame({}, 300, template=_wand_frame(None, 13, up, _SIT_EAR_FLICK), offset=lift),
        Frame({}, 400, template=_wand_frame(None, 13, at_us), offset=lift),
    ]
    return single_track(frames[0].template, frames, loop_from=None)


MOVES["wand"] = _wand()


# mouse (#127 play): a mouse runs laps round the cat, faster each lap, and the
# cat turns on the spot in a low crouch to keep facing it: front on while it
# passes the paws, side on (pounce's crouch) at the sides, its back to us while
# the mouse is behind it, where the cat hides it. Then the mouse bolts off
# across the card to its far end, and the cat sits up dizzy, its head
# wobbling. Every spot.
_MOUSE = ("...ng.", "nggggk", "..g.g.")  # running right: ear, eye at the nose, tail behind


def _back_view() -> List[str]:
    """The sitting cat from behind: the face and belly furred over, a stripe
    or two across the head and back, outer ears, paws hidden, and the tail
    lying along the ground out to the right."""
    rows = [list(row) for row in SITTING_TEMPLATE]
    for r in range(26):
        for c in range(28):
            ch = rows[r][c]
            if ch in "yY":
                rows[r][c] = "."
            elif ch == "p":
                rows[r][c] = "x"
            elif ch in "xwnALRucm" and not (r <= 3 and ch == "x"):
                rows[r][c] = "o"
    stripes = {6: range(9, 18), 9: range(9, 18), 16: range(8, 19), 19: range(7, 20),
               22: range(8, 19)}
    for r, cols in stripes.items():
        for c in cols:
            if rows[r][c] == "o" and (c % 3) != 0:
                rows[r][c] = "s"
    for r, c0, c1 in ((24, 14, 22), (23, 22, 23), (22, 23, 24)):
        for c in range(c0, c1 + 1):
            if rows[r][c] in ".oO":
                rows[r][c] = _tail_band(r)
    return ["".join(row) for row in rows]


_BACK_VIEW = _back_view()


def _mouse_frame(cat: List[str], col: Optional[int], right: bool, behind: bool) -> List[str]:
    """`cat` with the mouse's left edge at `col` on the floor, running right
    or left; behind the cat it only shows where the cat doesn't."""
    if col is None:
        return cat
    shape = _MOUSE if right else [row[::-1] for row in _MOUSE]
    cells = {}
    for r, line in enumerate(shape):
        for c, ch in enumerate(line):
            rr, cc = 23 + r, col + c
            if ch != "." and 0 <= cc < len(cat[0]) and not (behind and cat[rr][cc] != "."):
                cells[(rr, cc)] = ch
    return _overlay(cat, cells)


def _crouch_front(eyes: Dict[Tuple[int, int], str]) -> List[str]:
    return _sink_head([row.replace("A", "n") for row in _overlay(SITTING_TEMPLATE, eyes)])


def _mouse() -> Animation:
    """Laps round the cat, then the mouse bolts across the bottom of the card
    (under the stats) and out the far end, so the grid is card wide like
    knock_off's; mirrored for inside_right it runs the other way."""
    def wide(grid: List[str]) -> List[str]:
        return [row + "." * (_KO_COLS - 28) for row in grid]

    sit = wide(_hop_frame())
    side_r = wide(_pounce_crouch())
    side_l = wide(_facing_left(_pounce_crouch()))
    back = wide(_BACK_VIEW)

    def front(eyes: Dict[Tuple[int, int], str]) -> List[str]:
        return wide(_crouch_front(eyes))

    # one lap: (mouse col, running right, behind the cat, the cat's view)
    lap = [(c, True, False, "front") for c in (2, 6, 10, 14)]
    lap += [(c, True, False, "right") for c in (18, 21, 22)]
    lap += [(c, False, True, "back") for c in (20, 16, 12, 8, 4)]
    lap += [(c, False, False, "left") for c in (1, 0)]
    views = {"right": side_r, "left": side_l, "back": back}
    frames = [Frame({}, 250, template=sit),
              Frame({}, 300, template=_mouse_frame(front(_SIT_EYES_LEFT), -3, True, False))]
    for ms in (110, 80, 60):
        for col, right, behind, view in lap:
            if view == "front":
                cat = front(_SIT_EYES_RIGHT if col > 8 else _SIT_EYES_DOWN)
            else:
                cat = views[view]
            frames.append(Frame({}, ms, template=_mouse_frame(cat, col, right, behind)))
    # it bolts across the front and the bottom of the card, out the far end
    for col in (3, 8, 13, 19):
        frames.append(Frame({}, 50, template=_mouse_frame(front(_SIT_EYES_RIGHT), col, True, False)))
    for col in range(26, _KO_COLS + 1, 7):
        frames.append(Frame({}, 50, template=_mouse_frame(side_r, col, True, False)))
    frames.append(Frame({}, 400, template=side_r))
    # dizzy: the head wobbles side to side, slowing, then settles
    dizzy = wide(_overlay(_hop_frame(), _SIT_EYES_LINE))
    for dc, ms in ((1, 120), (-1, 120), (1, 160), (-1, 180), (1, 220), (0, 260)):
        frames.append(Frame({}, ms, template=_shift_head(dizzy, dc)))
    frames += [Frame({}, 200, template=wide(_overlay(_hop_frame(), _SIT_EYES_ARCH))),
               Frame({}, 400, template=sit)]
    return single_track(sit, frames, loop_from=None)


def _shift_head(grid: List[str], dc: int, neck_row: int = 13, width: int = 24) -> List[str]:
    """The head (rows down to neck_row, the columns before `width`, wide
    enough to hold all of it) moved dc columns."""
    if not dc:
        return grid
    rows = [list(row) for row in grid]
    for r in range(neck_row + 1):
        head = rows[r][:width]
        moved = (["."] * dc + head[:-dc]) if dc > 0 else (head[-dc:] + ["."] * -dc)
        rows[r][:width] = moved
    return ["".join(row) for row in rows]


MOVES["mouse"] = _mouse()


def _activate_settles(to_state: str) -> Animation:
    """Out of activate into the mood after it: the sparks drift out and fade,
    one last happy squint, and the cat settles into `to_state`."""
    return single_track(_at("activate"), [
        Frame({}, 160, template=_at("activate", sparks=1)),
        Frame({}, 160, template=_at("activate", sparks=2)),
        Frame({}, 300, template=_at("activate", sparks=3, eyes=1)),
        Frame({}, 100, template=_at(to_state)),
    ], loop_from=None)


def _activate_dozes_off() -> Animation:
    """Out of activate into sleeping, taking its time: the sparks fade, the
    ears relax, two slow drowsy blinks, one eye lingers open, then the head
    nods onto the chest and the first Z drifts up."""
    settle = list(_activate_settles("content").tracks[0].frames[:-1])
    asleep = _at("sleeping", zzz=1)
    return single_track(_at("activate"), settle + [
        Frame({}, 600, template=_at("content")),
        Frame({}, 450, template=_at("content", eyes=1)),
        Frame({}, 700, template=_at("content")),
        Frame({}, 800, template=_at("content", eyes=1)),
        Frame({}, 300, template=_at("content")),
        Frame({}, 900, template=_at("sleeping", eyes=1, zzz=1)),
        Frame({}, 700, template=asleep),
        Frame({}, 600, template=_sink_head(asleep)),
        Frame({}, 500, template=_at("sleeping", zzz=2)),
        Frame({}, 100, template=_at("sleeping")),
    ], loop_from=None)


# The moods activate settles into at once (sleeping dozes off slowly,
# panicked has its own startle).
_ACTIVATE_SETTLES_INTO = ("content", "interested", "alert")


def _interested_to_alert() -> Animation:
    """An ear perks, the cat sits up tall, and the "!" pops up beside it."""
    return single_track(_at("interested"), [
        Frame({}, 120, template=_at("interested")),
        Frame({}, 220, template=_at("interested", ears=1)),
        Frame({}, 180, template=_at("alert", bang=1)),
        Frame({}, 80, template=_at("alert", bang=2)),
        Frame({}, 100, template=_at("alert", bang=3)),
        Frame({}, 100, template=_at("alert")),
    ], loop_from=None)


def _alert_to_interested() -> Animation:
    """The "!" goes, the cat lets out a sigh (its head sinks a row) and
    settles back down."""
    calm = _at("alert", bang=1)
    return single_track(_at("alert"), [
        Frame({}, 120, template=_at("alert")),
        Frame({}, 300, template=calm),
        Frame({}, 400, template=_sink_head(calm, neck_row=14)),
        Frame({}, 100, template=_at("interested")),
    ], loop_from=None)


def _alert_to_tired() -> Animation:
    """The "!" goes, a long blink, the lids come back down only halfway, the
    ears sag and the cat lets out a sigh."""
    return single_track(_at("alert"), [
        Frame({}, 120, template=_at("alert")),
        Frame({}, 250, template=_at("alert", bang=1)),
        Frame({}, 300, template=_at("alert", bang=1, eyes=1)),
        Frame({}, 300, template=_at("tired", ears=1)),
        Frame({}, 450, template=_sink_head(_at("tired"), neck_row=14)),
        Frame({}, 100, template=_at("tired")),
    ], loop_from=None)


def _tired_to_alert() -> Animation:
    """The eyes snap open, the cat shakes its ears up and the "!" pops."""
    return single_track(_at("tired"), [
        Frame({}, 120, template=_at("tired")),
        Frame({}, 150, template=_at("tired", eyes=7)),
        Frame({}, 90, template=_at("waking", ears=1)),
        Frame({}, 90, template=_at("waking")),
        Frame({}, 90, template=_at("waking", ears=1)),
        Frame({}, 80, template=_at("alert", bang=2)),
        Frame({}, 100, template=_at("alert", bang=3)),
        Frame({}, 100, template=_at("alert")),
    ], loop_from=None)


def _tired_to_interested() -> Animation:
    """Relief: the eyes open, the ears come up and the cat sighs (its head
    sinks a row) before settling."""
    return single_track(_at("tired"), [
        Frame({}, 120, template=_at("tired")),
        Frame({}, 250, template=_at("tired", eyes=7)),
        Frame({}, 400, template=_sink_head(_at("alert", bang=1), neck_row=14)),
        Frame({}, 100, template=_at("interested")),
    ], loop_from=None)


def _interested_to_tired() -> Animation:
    """An ear perks and the cat sits up tall as into alert, but there is no
    "!": a long blink, and the lids and ears come back down."""
    return single_track(_at("interested"), [
        Frame({}, 120, template=_at("interested")),
        Frame({}, 220, template=_at("interested", ears=1)),
        Frame({}, 250, template=_at("alert", bang=1)),
        Frame({}, 300, template=_at("alert", bang=1, eyes=1)),
        Frame({}, 300, template=_at("tired", ears=1)),
        Frame({}, 100, template=_at("tired")),
    ], loop_from=None)


def _double_take() -> Animation:
    """Into confused from anywhere: a squint one way, then the other, the
    ear comes up, and the "?" floats up beside the cat."""
    puzzled = _at("confused")
    blank = [row.replace("?", ".") for row in puzzled]
    return single_track(blank, [
        Frame({}, 180, template=blank),
        Frame({}, 160, template=[row.replace("?", ".") for row in _at("confused", eyes=1)]),
        Frame({}, 120, template=blank),
        Frame({}, 160, template=[row.replace("?", ".") for row in _at("confused", eyes=3)]),
        Frame({}, 200, template=[row.replace("?", ".") for row in _at("confused", ears=1)]),
        Frame({}, 90, template=_rise(blank, puzzled, 4)),
        Frame({}, 90, template=_rise(blank, puzzled, 2)),
        Frame({}, 100, template=puzzled),
    ], loop_from=None)


def _working_to_done_hop() -> Animation:
    """The lights come up as the lid swings shut, and the laptop slides away."""
    sit = _hop_frame()
    return single_track(_at("working"), [
        Frame({}, 150, template=_at("working")),
        Frame({}, 110, template=_overlay(sit, _laptop(lid_top=17))),
        Frame({}, 130, template=_overlay(sit, _laptop(lid_top=20))),
        Frame({}, 90, template=_overlay(sit, _laptop(-4, lid_top=20))),
        Frame({}, 90, template=_overlay(sit, _laptop(-8, lid_top=20))),
        Frame({}, 100, template=get_frames("done_hop")[0]),
    ], loop_from=None)


def _sleeping_to_interested() -> Animation:
    """One eye peeks open, the head comes up, and the ears perk."""
    return single_track(_at("sleeping"), [
        Frame({}, 200, template=_at("sleeping")),
        Frame({}, 350, template=_at("sleeping", eyes=1, zzz=1)),
        Frame({}, 200, template=_sink_head(_hop_frame())),
        Frame({}, 120, template=_hop_frame()),
        Frame({}, 100, template=_at("interested")),
    ], loop_from=None)


def _startle() -> Animation:
    """Into panicked from anywhere: the cat leaps three rows with its paws
    flung up, drops and lands shaking."""
    return single_track(_JUMP_LAUNCH, [
        Frame({}, 70, template=_JUMP_LAUNCH, offset=_up(2)),
        Frame({}, 140, template=_JUMP_LAUNCH, offset=_up(3)),
        Frame({}, 70, template=_JUMP_FALL, offset=_up(1)),
        Frame({}, 80, template=_at("panicked")),
    ], loop_from=None)


def _permission_to_urgent() -> Animation:
    """The wait wears thin: the brows lower flat, then pinch into the scowl
    as the hind leg slides out, and the first tap lands on urgent's frame 0."""
    flat = {(6, 9): LID_CHAR, (6, 10): LID_CHAR, (6, 16): LID_CHAR, (6, 17): LID_CHAR}
    leg = {**_cells("o", (23, 7, 7), (24, 6, 8), (25, 6, 9)), (25, 5): "m"}
    return single_track(_at("permission"), [
        Frame({}, 200, template=_at("permission")),
        Frame({}, 160, template=_overlay(_at("permission"), flat)),
        Frame({}, 120, template=_overlay(_at("permission"), {**_SCOWL, **leg})),
        Frame({}, 100, template=_at("permission_urgent")),
    ], loop_from=None)


TRANSITIONS[("permission", "permission_urgent")] = _permission_to_urgent()
TRANSITIONS[("thinking", "working")] = _thinking_to_working()
TRANSITIONS[("working", "thinking")] = _working_to_thinking()
TRANSITIONS[(ANY_STATE, "working")] = _settle_at_keys()
TRANSITIONS[(ANY_STATE, "permission")] = _raise_pennant()
TRANSITIONS[(ANY_STATE, "question")] = _pop_question()
TRANSITIONS[("working", "done_hop")] = _working_to_done_hop()
TRANSITIONS[("sleeping", "interested")] = _sleeping_to_interested()
TRANSITIONS[(ANY_STATE, "panicked")] = _startle()
TRANSITIONS[("flopped", "stirring")] = _flopped_to_stirring()
TRANSITIONS[("panicked", "flopped")] = _panicked_to_flopped()
TRANSITIONS[("stirring", "waking")] = _stirring_to_waking()
for _mood in _ACTIVATE_SETTLES_INTO:
    TRANSITIONS[("activate", _mood)] = _activate_settles(_mood)
del _mood
TRANSITIONS[("activate", "sleeping")] = _activate_dozes_off()
TRANSITIONS[("interested", "alert")] = _interested_to_alert()
TRANSITIONS[("alert", "interested")] = _alert_to_interested()
TRANSITIONS[("alert", "tired")] = _alert_to_tired()
TRANSITIONS[("tired", "alert")] = _tired_to_alert()
TRANSITIONS[("tired", "interested")] = _tired_to_interested()
TRANSITIONS[("interested", "tired")] = _interested_to_tired()
TRANSITIONS[(ANY_STATE, "confused")] = _double_take()
_check_transitions()
