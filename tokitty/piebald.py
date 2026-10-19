"""Piebald (white spotting) for the #211 looks: which cells of each drawing turn white.

A region char maps to one tone per pattern, but tuxedo, bicolor, harlequin and
cap & saddle each paint their own white areas across the regions. So every
drawn cat carries a piebald class per cell: a bit set of the looks that paint
that cell white. sprites.py folds the class into the cell's char (see
sprites.piebald_char), and a pattern turns a class white if its bit is set.

Each drawing is described by a few named areas, and every look is the same
formula over them, so the looks stay consistent from pose to pose:

  tuxedo     BIB | BELLY
  bicolor    everything but CAP and SIDES, plus NOTCH and LEGS_WHITE
  harlequin  everything but EARS and SPOTS (rounded)
  cap&saddle everything but SOFT_CAP and SADDLE

Tail and paws aren't classed: every look draws the tail shade, the paws white.
"""
from typing import Dict, FrozenSet, Iterable, List, Sequence, Tuple

Cell = Tuple[int, int]

TUXEDO, BICOLOR, HARLEQUIN, CAP_SADDLE = 1, 2, 4, 8
# The class of a leg drawn by code rather than taken from a template:
# front legs are white on all but the tuxedo, hind legs keep bicolor's coat.
FRONT_LEG = BICOLOR | HARLEQUIN | CAP_SADDLE
HIND_LEG = HARLEQUIN | CAP_SADDLE

ROWS, COLS = 26, 28


def box(r0: int, r1: int, c0: int, c1: int) -> FrozenSet[Cell]:
    return frozenset((r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1))


def spans(spec: Dict[int, Sequence[Tuple[int, int]]]) -> FrozenSet[Cell]:
    """{row: [(first col, last col), ...]}."""
    return frozenset((r, c) for r, runs in spec.items() for a, b in runs for c in range(a, b + 1))


def front(spec: Dict[int, Tuple[int, int]]) -> FrozenSet[Cell]:
    """One run per row: a rounded white front."""
    return spans({r: [ab] for r, ab in spec.items()})


def outside(spec: Dict[int, Tuple[int, int]], r0: int, r1: int) -> FrozenSet[Cell]:
    """Rows r0..r1 except each row's run in spec: the sides around a front."""
    return frozenset((r, c) for r in range(r0, r1 + 1) for c in range(COLS)
                     if r not in spec or not spec[r][0] <= c <= spec[r][1])


def round_spots(spots: Iterable[Tuple[int, int, int, int]]) -> FrozenSet[Cell]:
    """Harlequin spots as discs: corners trimmed off spots four rows tall."""
    out = set()
    for r0, r1, c0, c1 in spots:
        cells = set(box(r0, r1, c0, c1))
        if r1 - r0 >= 3 and c1 - c0 >= 2:
            cells -= {(r0, c0), (r0, c1), (r1, c0), (r1, c1)}
        out |= cells
    return frozenset(out)


def _drawing(EARS=frozenset(), CAP=frozenset(), SOFT_CAP=frozenset(), NOTCH=frozenset(),
             SIDES=frozenset(), LEGS_WHITE=frozenset(), SADDLE=frozenset(), SPOTS=(),
             BIB=frozenset(), BELLY=frozenset()) -> List[List[int]]:
    allc = box(0, ROWS - 1, 0, COLS - 1)
    white = {
        TUXEDO: BIB | BELLY,
        BICOLOR: (allc - CAP - SIDES) | NOTCH | (LEGS_WHITE - SIDES),
        HARLEQUIN: allc - EARS - round_spots(SPOTS),
        CAP_SADDLE: allc - SOFT_CAP - SADDLE,
    }
    return [[sum(bit for bit, cells in white.items() if (r, c) in cells) for c in range(COLS)]
            for r in range(ROWS)]


def _shift(cells: Iterable[Cell], dr: int = 0, dc: int = 0, keep=lambda r, c: True) -> FrozenSet[Cell]:
    return frozenset((r + dr, c + dc) for r, c in cells if keep(r, c))


# --- sitting (and everything drawn from it: working, the sleeping and
# content loops, the hop frames) -----------------------------------------
_S_EARS = box(0, 4, 0, 27)
_S_CAP = box(0, 7, 0, 27) | spans({8: [(0, 10), (16, 27)]})
_S_SOFT_CAP = box(0, 6, 0, 27) | spans({7: [(0, 11), (15, 27)], 8: [(0, 10), (16, 27)], 9: [(0, 8), (18, 27)]})
_S_NOTCH = spans({5: [(13, 13)], 6: [(13, 13)], 7: [(12, 14)]})
_S_SADDLE = spans({17: [(17, 20)], 18: [(16, 21)], 19: [(15, 21)], 20: [(15, 21)],
                   21: [(15, 21)], 22: [(16, 21)], 23: [(17, 20)]})
_S_TUX = front({13: (12, 14), 14: (11, 15), 15: (10, 16), 16: (10, 16), 17: (9, 17), 18: (9, 17),
                19: (9, 17), 20: (9, 17), 21: (9, 17), 22: (10, 16), 23: (10, 16)})
_S_BI = {14: (10, 16), 15: (9, 17), 16: (8, 18), 17: (7, 19), 18: (7, 19), 19: (7, 19),
         20: (7, 19), 21: (8, 18), 22: (8, 18), 23: (8, 18)}
_S_SPOTS = [(15, 18, 6, 9), (19, 22, 15, 18), (5, 6, 15, 18)]

SITTING = _drawing(EARS=_S_EARS, CAP=_S_CAP, SOFT_CAP=_S_SOFT_CAP, NOTCH=_S_NOTCH,
                   SIDES=outside(_S_BI, 14, 23), SADDLE=_S_SADDLE, SPOTS=_S_SPOTS, BIB=_S_TUX)


# --- alert and question: the sitting head a row higher, the same body ----
def _head_up(cells: Iterable[Cell]) -> FrozenSet[Cell]:
    cells = list(cells)
    return _shift(cells, -1, keep=lambda r, c: 1 <= r <= 13) | _shift(cells, keep=lambda r, c: r > 12)


ALERT = _drawing(EARS=_head_up(_S_EARS), CAP=_head_up(_S_CAP), SOFT_CAP=_head_up(_S_SOFT_CAP),
                 NOTCH=_head_up(_S_NOTCH), SIDES=_head_up(outside(_S_BI, 14, 23)),
                 SADDLE=_head_up(_S_SADDLE), BIB=_head_up(_S_TUX),
                 SPOTS=[(15, 18, 6, 9), (19, 22, 15, 18), (4, 5, 15, 18)])


# --- the loaf: the sitting head five rows lower, a wide low body ---------
def _head_down(cells: Iterable[Cell]) -> FrozenSet[Cell]:
    return _shift(cells, 5, keep=lambda r, c: r <= 13)


LOAF = _drawing(
    EARS=_head_down(_S_EARS), CAP=_head_down(_S_CAP), SOFT_CAP=_head_down(_S_SOFT_CAP),
    NOTCH=_head_down(_S_NOTCH),
    SIDES=outside({15: (7, 19), 16: (7, 19), 17: (8, 18), 18: (8, 18), 19: (8, 18),
                   20: (7, 19), 21: (6, 20)}, 15, 21),
    # anchored to the right flank, so it reads as a saddle and not a spot
    SADDLE=spans({15: [(21, 27)], 16: [(20, 27)], 17: [(19, 27)], 18: [(19, 27)], 19: [(19, 27)],
                  20: [(20, 27)], 21: [(21, 27)]}),
    SPOTS=[(15, 18, 3, 6), (17, 20, 17, 20), (10, 11, 15, 18)],
    BIB=front({18: (11, 15), 19: (10, 16), 20: (9, 17), 21: (9, 17)}),
)

# --- lying on its side: frontal head on the left, body running right -----
_F_CAP = box(0, 10, 0, 15) | spans({11: [(0, 4), (12, 15)]})
_F_SOFT_CAP = box(0, 9, 0, 15) | spans({10: [(0, 6), (10, 15)], 11: [(0, 4), (12, 15)], 12: [(0, 3), (13, 15)]})
_F_NOTCH = spans({8: [(8, 8)], 9: [(8, 8)], 10: [(7, 9)]})
FLOPPED = _drawing(
    EARS=box(0, 5, 0, 15), CAP=_F_CAP, SOFT_CAP=_F_SOFT_CAP, NOTCH=_F_NOTCH,
    SIDES=spans({14: [(12, 27)], 15: [(12, 27)], 16: [(12, 27)], 17: [(13, 27)], 18: [(15, 27)],
                 19: [(18, 27)], 20: [(19, 27)], 21: [(19, 27)], 22: [(20, 27)], 23: [(22, 27)]}),
    SADDLE=spans({14: [(19, 22)], 15: [(18, 23)], 16: [(18, 25)], 17: [(18, 25)],
                  18: [(18, 25)], 19: [(19, 25)], 20: [(20, 25)]}),
    SPOTS=[(6, 7, 9, 12), (15, 18, 14, 17), (19, 22, 21, 24)],
    BIB=front({14: (6, 10), 15: (5, 11), 16: (5, 11), 17: (6, 12), 18: (9, 15)}),
    BELLY=front({19: (11, 18), 20: (11, 18), 21: (11, 17), 22: (12, 17)}),
)

# --- on its back (flopped): the head turned, crown at the left edge ------
FLOPPED_BACK = _drawing(
    EARS=box(0, 25, 0, 2),
    CAP=box(0, 25, 0, 5) | frozenset((r, 6) for r in range(ROWS) if r <= 13 or r >= 19),
    SOFT_CAP=(box(0, 25, 0, 4) | frozenset((r, 5) for r in range(ROWS) if r <= 14 or r >= 18)
              | frozenset((r, 6) for r in range(ROWS) if r <= 13 or r >= 19)
              | frozenset((r, 7) for r in range(ROWS) if r <= 11 or r >= 21)),
    NOTCH=frozenset({(16, 3), (16, 4), (15, 5), (16, 5), (17, 5)}),
    SIDES=(spans({16: [(19, 27)], 17: [(12, 27)]}) | box(18, 22, 10, 27)
           | spans({7: [(22, 27)], 8: [(22, 27)], 9: [(22, 27)]}) | box(10, 15, 21, 27)),
    SADDLE=spans({14: [(21, 23)], 15: [(20, 24)], 16: [(19, 25)], 17: [(19, 25)],
                  18: [(19, 25)], 19: [(19, 25)], 20: [(19, 25)], 21: [(20, 25)]}),
    SPOTS=[(18, 20, 3, 4), (17, 20, 13, 16), (9, 12, 21, 24)],
    BIB=front({12: (10, 11), 13: (9, 11), 14: (9, 11), 15: (9, 11), 16: (9, 11), 17: (9, 11), 18: (10, 11)}),
    BELLY=front({12: (12, 20), 13: (11, 21), 14: (11, 21), 15: (11, 21), 16: (12, 20)}),
)


# --- jump_up / jump_down: sitting head, a narrow stretched body ----------
def _head_cols(cells: Iterable[Cell]) -> FrozenSet[Cell]:
    """Head areas stop at the head's columns, or they would paint the raised arms."""
    return frozenset((r, c) for r, c in cells if 5 <= c <= 21)


# The arms thrown up beside the head on the launch (front legs).
JUMP_ARMS = spans({6: [(2, 4), (22, 23)], 7: [(2, 4), (22, 23)], 8: [(3, 6), (21, 22)], 9: [(3, 5), (21, 22)],
                   10: [(4, 6), (20, 21)], 11: [(4, 6), (20, 21)], 12: [(5, 6), (20, 20)],
                   13: [(5, 6), (20, 20)], 14: [(6, 6)]})
JUMP_LAUNCH = _drawing(
    EARS=_head_cols(_S_EARS), CAP=_head_cols(_S_CAP), SOFT_CAP=_head_cols(_S_SOFT_CAP), NOTCH=_S_NOTCH,
    SIDES=outside({14: (8, 18), 15: (9, 17), 16: (10, 16), 17: (10, 17), 18: (10, 17), 19: (10, 17),
                   20: (10, 17), 21: (11, 16)}, 14, 21) - JUMP_ARMS,
    LEGS_WHITE=box(22, 25, 0, 27) | JUMP_ARMS,
    SADDLE=spans({17: [(16, 19)], 18: [(15, 20)], 19: [(15, 20)], 20: [(15, 20)], 21: [(16, 18)]}),
    SPOTS=[(15, 18, 8, 11), (18, 21, 14, 17), (5, 6, 15, 18)],
    BIB=front({13: (12, 14), 14: (11, 15), 15: (10, 16), 16: (10, 16), 17: (10, 16), 18: (10, 16),
               19: (10, 16), 20: (10, 16), 21: (11, 15)}),
)
JUMP_FALL = _drawing(
    EARS=_head_cols(_S_EARS), CAP=_head_cols(_S_CAP), SOFT_CAP=_head_cols(_S_SOFT_CAP), NOTCH=_S_NOTCH,
    SIDES=outside({14: (8, 18), 15: (9, 17), 16: (9, 17), 17: (9, 17), 18: (9, 17),
                   19: (10, 16), 20: (10, 16)}, 14, 20),
    LEGS_WHITE=box(21, 25, 0, 27),
    SADDLE=spans({16: [(17, 21)], 17: [(16, 21)], 18: [(16, 20)], 19: [(16, 19)], 20: [(17, 19)]}),
    SPOTS=[(15, 18, 8, 11), (17, 20, 14, 17), (5, 6, 15, 18)],
    BIB=front({13: (12, 14), 14: (11, 15), 15: (10, 16), 16: (10, 16), 17: (10, 16),
               18: (10, 16), 19: (10, 16), 20: (11, 15)}),
)

# --- walking, side on and facing right (legs are classed where they're
# drawn: FRONT_LEG / HIND_LEG) --------------------------------------------
WALK = _drawing(
    EARS=box(0, 5, 0, 27),
    CAP=box(0, 7, 0, 27) | spans({8: [(0, 22)], 9: [(0, 19)], 10: [(0, 19)], 11: [(0, 19)]}),
    SOFT_CAP=box(0, 6, 0, 27) | spans({7: [(0, 23)], 8: [(0, 22)], 9: [(0, 19)], 10: [(0, 17)]}),
    NOTCH=frozenset({(6, 25), (7, 24), (7, 25), (7, 26)}),
    SIDES=spans({12: [(0, 17)], 13: [(0, 16)], 14: [(0, 16)], 15: [(0, 16)], 16: [(0, 15)],
                 17: [(0, 6)], 18: [(0, 6)]}) | box(19, 24, 0, 10),
    LEGS_WHITE=box(19, 25, 11, 27),
    SADDLE=spans({12: [(5, 8)], 13: [(4, 9)], 14: [(4, 10)], 15: [(4, 10)], 16: [(4, 9)], 17: [(4, 7)]}),
    SPOTS=[(12, 15, 7, 10), (13, 16, 12, 15), (6, 7, 19, 21)],
    BIB=front({12: (20, 21), 13: (18, 22), 14: (18, 21), 15: (18, 21), 16: (17, 21)}),
    BELLY=front({17: (8, 18), 18: (8, 17)}),
)


# --- the stretch bow: the side-flop head five rows down, rump up ---------
def _down5(cells: Iterable[Cell]) -> FrozenSet[Cell]:
    return frozenset((r + 5, c) for r, c in cells if c <= 13 and r + 5 <= 20)


STRETCH_BOW = _drawing(
    EARS=_down5(box(0, 5, 0, 15)), CAP=_down5(_F_CAP), SOFT_CAP=_down5(_F_SOFT_CAP), NOTCH=_down5(_F_NOTCH),
    SIDES=box(6, 15, 14, 27) | box(16, 22, 21, 27),
    SADDLE=spans({6: [(20, 23)], 7: [(19, 24)], 8: [(18, 25)], 9: [(18, 25)], 10: [(19, 25)],
                  11: [(20, 25)], 12: [(21, 25)]}),
    SPOTS=[(8, 11, 15, 18), (12, 15, 19, 22), (11, 12, 9, 12)],
    BIB=front({19: (6, 10), 20: (5, 12), 21: (6, 13), 22: (7, 13)}),
    BELLY=front({17: (16, 18), 18: (15, 17), 19: (13, 16)}),
)

# --- the roll curl, seen from behind -------------------------------------
ROLL_CURL = _drawing(
    EARS=box(0, 12, 0, 27), CAP=box(0, 19, 0, 27), SOFT_CAP=box(0, 14, 0, 27), SIDES=box(0, 19, 0, 27),
    SADDLE=spans({15: [(16, 18)], 16: [(15, 19)], 17: [(14, 20)], 18: [(14, 21)], 19: [(15, 21)]}),
    SPOTS=[(15, 18, 5, 8), (16, 19, 10, 13), (13, 14, 12, 15)],
)



# --- scratch: stretched out low, facing right, the walking cat's head ------
SCRATCH = _drawing(
    SIDES=box(8, 17, 0, 19) | box(17, 24, 0, 7),
    SADDLE=spans({8: [(5, 8)], 9: [(3, 9)], 10: [(2, 9)], 11: [(2, 9)], 12: [(2, 8)], 13: [(2, 6)]}),
    SPOTS=[(9, 12, 5, 8), (12, 15, 10, 13)],
    BIB=front({19: (15, 19), 20: (15, 19), 21: (16, 19), 22: (17, 19)}),
)

# --- up on the hind legs facing left (scratch_stats, bat): chest and belly
# down the left edge, the forelegs classed where they're drawn --------------
_UP_FRONT = {10: (8, 10), 11: (7, 9), 12: (7, 9), 13: (7, 9), 14: (8, 10), 15: (8, 10), 16: (8, 10),
             17: (8, 10), 18: (8, 10), 19: (8, 10), 20: (8, 10), 21: (9, 11)}
SCRATCH_UPRIGHT = _drawing(
    SIDES=outside(_UP_FRONT, 3, 22),
    SADDLE=spans({15: [(15, 17)], 16: [(14, 18)], 17: [(14, 18)], 18: [(14, 18)], 19: [(14, 19)],
                  20: [(15, 19)], 21: [(15, 18)]}),
    SPOTS=[(12, 15, 12, 15), (17, 20, 13, 16)],
    BIB=front(_UP_FRONT),
)

# --- the sitting cat from behind: the front's white is out of sight, so
# tuxedo and bicolor are all coat; harlequin's spots and the saddle are the
# sitting cat's, mirrored ---------------------------------------------------
BACK_VIEW = _drawing(
    EARS=box(0, 4, 0, 27), CAP=box(0, 25, 0, 27), SIDES=box(0, 25, 0, 27),
    SOFT_CAP=box(0, 10, 0, 27),
    SADDLE=spans({17: [(6, 9)], 18: [(5, 10)], 19: [(5, 11)], 20: [(5, 11)], 21: [(5, 11)],
                  22: [(5, 10)], 23: [(6, 9)]}),
    SPOTS=[(15, 18, 17, 20), (19, 22, 8, 11), (5, 6, 8, 11)],
)

# --- the rolling loaf frames (sprites._ROLL_DIAGONALS and friends), turned
# by RotSprite rather than drawn: each cell sampled from LOAF through the
# frame's fitted rotation. One hex digit per body cell, the class bits. ----
ROLL_DIAGONALS: Dict[int, List[str]] = {
    45: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "........0000.4..............",
        "........000044..............",
        "........00.44..4464.........",
        ".......00000444464444.......",
        ".....00000004446444444......",
        ".....0004004446ec444444.....",
        "....0006444...eee444444.....",
        ".00000446eeeeeee0004444.....",
        ".0000444eeee..eea00444......",
        ".00.4444ee......aaaae.......",
        ".004444..e.....effa.........",
        "..44444.e......fff..........",
        "..4.444.ee....fff...........",
        "....446eee...ffff...........",
        "....46ceeeeeffff............",
        "....68c8ceeeff..............",
        "....88888ceff...............",
        "....c888ccee................",
        ".....888ccce................",
        "......cccce.................",
        ".......ccc..................",
    ],
    135: [
        "............................",
        "............................",
        "............................",
        "............................",
        ".........444................",
        "........44444...............",
        ".......444444e..............",
        ".......444444a..............",
        "......4444400aa.............",
        "......6444400aff............",
        "......464ce0aaffff..........",
        "......446eeee.effff.........",
        ".......446eee...fff.........",
        "....44.444.e.....fff........",
        ".....44444.e......fff.......",
        "....004004.ee.....eefee.....",
        "....00.0004ee.....eeeeee....",
        "....0000004eeee.eeeeccccc...",
        "....00000446ee.eeeecccccc...",
        ".......0006444...eee888cc...",
        "........0004444446ec888c....",
        "........000044444468888.....",
        "..........00044444468c......",
        "...........00.44............",
        "...........000044...........",
        "...........0000.............",
    ],
    225: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "...................ccc......",
        "..................ecccc.....",
        ".................eccc888....",
        ".................eecc888c...",
        "................ffec88888...",
        "...............ffeeec8c86...",
        ".............ffffeeeeec64...",
        "............ffff...eee644...",
        "............fff....ee.444.4.",
        "...........fff......e.44444.",
        "..........affe.....e..444400",
        "........eaaaa......ee4444.00",
        ".......44400aee..eeee4440000",
        "......4444000eeeeeee64400000",
        "......444444eee...4446000...",
        "......444444ce6444004000....",
        ".......44444464440000000....",
        "........44446444400000......",
        "..........4644..44.00.......",
        "...............440000.......",
        "...............4.0000.......",
    ],
    315: [
        "..............0000..........",
        "............440000..........",
        ".............44.00..........",
        ".......c86444444000.........",
        "......888864444440000.......",
        ".....c888ce6444444000.......",
        "....cc888eee...4446000......",
        "....cccccceeee.ee64400000...",
        "....ccccceeee.eeee4000000...",
        ".....eeeeee.....ee4000.00...",
        "......eefee.....ee.400400...",
        "........fff......e.44444....",
        ".........fff.....e.444.44...",
        "..........fff...eee644......",
        "..........ffffe.eeee64......",
        "...........ffffaa0ec464.....",
        ".............ffa0044446.....",
        "..............aa0044444.....",
        "...............a444444......",
        "...............e444444......",
        "................44444.......",
        ".................444........",
        "............................",
        "............................",
        "............................",
        "............................",
    ],
}
ROLL_OVER: Dict[int, List[str]] = {
    -45: [
        "..............0000..........",
        "............440000..........",
        ".............44.00..........",
        ".......c86444444000.........",
        "......888864444440000.......",
        ".....c888ce6444444000.......",
        "....cc888eeee4.4446000......",
        "....cccccceeeeeee64400000...",
        "....ccccceeeeeeeee4000000...",
        ".....eeeeee.....ee4000.00...",
        "......eefee.....ee.400400...",
        "........fff.....ee.44444....",
        ".........fff.....ee444.44...",
        "..........fff...eee644......",
        "..........ffffe.eeee644.....",
        "...........ffffaa0ec464.....",
        ".............ffa0044446.....",
        "..............aa0044444.....",
        "...............a444444......",
        "...............e444444......",
        "................44444.......",
        ".................444........",
        "............................",
        "............................",
        "............................",
        "............................",
    ],
    -135: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "...................ccc......",
        "..................ecccc.....",
        ".................eccc888....",
        ".................eecc888c...",
        "................ffec88888...",
        "...............ffeeec8c86...",
        ".............ffffeeeeec64...",
        "............ffff...eee644...",
        "............fff....eee444.4.",
        "...........fff.....ee444444.",
        "..........affe.....ee.444400",
        "........eaaaa......ee4444.00",
        ".......44400aee.eeeee4440000",
        "......4444000eeeeeee64400000",
        "......444444eeee..4446000...",
        "......444444ce6444004000....",
        ".......44444464440000000....",
        "........44446444400000......",
        "..........4644..44.00.......",
        "...............440000.......",
        "...............4.0000.......",
    ],
    -165: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        ".....................eecc...",
        "....................fecccc..",
        "..............fff.ffeccccc..",
        "..............fffffeecc888..",
        "......44eeafffffffeeeec8888.",
        ".....4444aaaaf......eee888c.",
        "....0444400aae......eeec88c.",
        ".....444400ae.......ee6666..",
        ".....444440eee.....eee444...",
        ".....444444eeeee..ee.4444...",
        "......4444c66eeeeee44444....",
        ".......6666444.4eee4444444..",
        "........444444404464444.00..",
        "...........440000460000000..",
        "...........44000000000000...",
        "..........440.000000...00...",
        "...........00000..0.........",
        "............000.............",
        ".............00.............",
    ],
    -195: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "....04444...................",
        "....4444ee..................",
        "....44444aaf..ff............",
        "....444400aafffff...........",
        "...4444400aafffffffffeec....",
        "....44440ae.e...ffffeeccc...",
        "....444ceee......eeeecccc...",
        "....6666eee.......eecc8cc...",
        ".....44666ee......eec888c...",
        "......4444.ee...eeeee8888...",
        ".......4444eeeeeee6eec888...",
        ".....4440004eeee.446666c....",
        ".....00400004644444444......",
        ".....00.0004664444444.......",
        ".....00000000044444.........",
        "......00..0000000.444.......",
        ".......0...00.000000........",
        "................000.........",
        "................00..........",
    ],
}
TC_HUG_ROLL: Dict[int, List[str]] = {
    -45: [
        "............................",
        "............................",
        "............................",
        "............................",
        "..............0000..........",
        "............440000..........",
        ".............44.00..........",
        ".......c86444444000.........",
        "......888864444440000.......",
        ".....c888ce6444444000.......",
        "....cc888eeee4.4446000......",
        "....cccccceeeeeee64400000...",
        "....ccccceeeeeeeee4000000...",
        ".....eeeeee.....ee4000.00...",
        "......eefee.....ee.400400...",
        ".......ffff.....ee.44444....",
        "........f........ee444.44...",
        ".........f......eee644......",
        "..........f...e.eeee644.....",
        "...........f...aa0ec464.....",
        "............f...0044446.....",
        ".............f...044444.....",
        "..............e...4444......",
        "...............e...444......",
        "................4...4.......",
        ".................4..........",
    ],
    -135: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "................ccc.........",
        "...............ecccc........",
        "..............eecc888.......",
        ".............feecc888c......",
        "............fffecc8888......",
        "...........f.feeecec86......",
        "..........f..feeeeee64......",
        ".........f......eee644......",
        "........f.......eee444.4....",
        ".......f........ee444444....",
        "......e...e.....ee.444400...",
        ".....e...a......ee4444.00...",
        "....4...0aee.eeeee4440000...",
        "...4...000eeeeeee64400000...",
        "......444eeee..4446000......",
        ".....4444ce6444004000.......",
        "....44444464440000000.......",
        ".....44446444400000.........",
        ".......4644..44.00..........",
        "............440000..........",
        "............4.0000..........",
    ],
    -165: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "....................eecc....",
        ".................fffecccc...",
        ".............ffffffeecccc...",
        ".........ffff...ffeecc88c...",
        ".....44ee.......feeeec8888..",
        "....44.............eee888c..",
        "..........aae......eeec88c..",
        "......4400ae.......eee666...",
        "....444440eee.....eee644....",
        "....44444eeeeee..ee.4444....",
        ".....4444c66eeeeee44444.....",
        "......4666644.4eee4444444...",
        ".......444444444664444.00...",
        "..........440000464000000...",
        "..........44000000000000....",
        ".........440.000000...00....",
        "..........00000..0..........",
        "...........000..............",
        "............00..............",
    ],
    -195: [
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "............................",
        "......444...................",
        "........4eff................",
        "............ffff............",
        "....4444........ffff........",
        "...4444400aa......fffeec....",
        "....44440ae.f....ffeeeccc...",
        "....444ceee......eeeccccc...",
        "....6666eee.......eecc8cc...",
        ".....4446eee......eec888c...",
        "......4444.ee...eeeee8888...",
        ".......4444eeeeeee6ec8888...",
        ".....4440004eeee.446666c....",
        ".....00000004644444444......",
        ".....00.0004644444444.......",
        ".....00000000004444.........",
        "......00..0000000.444.......",
        ".......0...00.000000........",
        "................000.........",
        "................00..........",
    ],
}
