"""The ground shadow's geometry: pure functions, no toolkit."""
from tokitty import shadow, sprites, theme
from tokitty.shadow import CORE_ALPHA, RIM_ALPHA, ground_row, footprint, shadow_for

S = sprites.SCALE


def _grid(*rows, width=28, height=26):
    """Rows pinned to the bottom of an empty 28x26 grid, padded to width."""
    pad = ["." * width] * (height - len(rows))
    return pad + [r.ljust(width, ".") for r in rows]


def _visible(s):
    """The alpha run without the transparent corner cells."""
    return list(s.cells[0][1:-1])


def test_ground_row_and_footprint_ignore_accents():
    g = _grid("..zzz", "!h?...", "...ooo", "..oooooo")
    assert ground_row(g) == 25
    assert footprint(g) == (2, 7)
    # accents alone below the body do not count
    low = _grid("..zz!", "...ooo", ".z")
    assert ground_row(low) == 24 and footprint(low) == (3, 5)


def test_footprint_spans_the_two_lowest_body_rows():
    g = _grid("oooooooo", "..oo", "...pp")
    assert footprint(g) == (2, 4)


def test_empty_grid_has_no_shadow():
    empty = _grid()
    assert ground_row(empty) is None and footprint(empty) is None
    assert shadow_for(empty, empty, (0, 0), "inside", False) is None
    assert shadow_for(_grid("..zz!"), _grid("..zz!"), (0, 0), "inside", False) is None


def test_sitting_cat_at_rest_is_centred_under_the_paws_on_the_floor():
    g = sprites.get_frames("content")[0]
    first, last = footprint(g)
    s = shadow_for(g, g, (0, 0), "inside", False)
    assert s.y == 116 - S  # two rows straddling the floor line at 116
    assert len(s.cells) == 2 and s.cells[0] == s.cells[1]
    row = s.cells[0]
    assert row[0] == 0 and row[-1] == 0
    assert row[1] == RIM_ALPHA and row[-2] == RIM_ALPHA
    assert set(row[2:-2]) == {CORE_ALPHA} and len(row[2:-2]) == last - first + 1
    # one rim cell beyond each side of the footprint
    left = theme.placement("inside").origin[0] + (first - 1) * S
    assert s.x + S == left
    assert s.x + len(row) * S - S == theme.placement("inside").origin[0] + (last + 2) * S


def test_lift_narrows_two_cells_per_two_rows_and_fades():
    g = sprites.get_frames("content")[0]
    rest = shadow_for(g, g, (0, 0), "inside", False)
    widths = {}
    for rows in (1, 2, 3, 4):
        s = shadow_for(g, g, (0, -rows), "inside", False)
        widths[rows] = len(_visible(s))
        assert s.y == rest.y  # still on the floor
    base = len(_visible(rest))
    assert widths == {1: base, 2: base - 2, 3: base - 2, 4: base - 4}
    one = shadow_for(g, g, (0, -1), "inside", False)
    assert _visible(one)[0] == round(RIM_ALPHA * 0.85)
    assert _visible(one)[1] == round(CORE_ALPHA * 0.85)
    # centred: the left and right trims are equal
    two = shadow_for(g, g, (0, -2), "inside", False)
    assert two.x - rest.x == S
    assert len(two.cells[0]) == len(rest.cells[0]) - 2


def test_lift_clamps_width_and_alpha():
    g = sprites.get_frames("content")[0]
    base = len(_visible(shadow_for(g, g, (0, 0), "inside", False)))
    s = shadow_for(g, g, (0, -12), "inside", False)
    visible = _visible(s)
    assert len(visible) >= round(shadow.MIN_SCALE * base)
    assert (base - len(visible)) % 2 == 0
    assert visible[0] == round(RIM_ALPHA * shadow.MIN_SCALE)
    assert visible[1] == round(CORE_ALPHA * shadow.MIN_SCALE)


def test_feet_above_the_card_top_edge_land_on_it():
    g = sprites.get_frames("content")[0]
    s = shadow_for(g, g, (0, -40), "inside", False)
    assert s.y == -S  # straddles y = 0
    # still dark enough to see, at the alpha floor
    assert max(s.cells[0]) == round(CORE_ALPHA * shadow.MIN_SCALE)


def test_on_top_cat_rests_on_the_top_edge_and_drops_to_the_floor():
    g = sprites.get_frames("content")[0]
    assert shadow_for(g, g, (0, 0), "on_top", False).y == -S
    down = shadow_for(g, g, (0, 29), "on_top", False)  # feet on y = 116
    assert down.y == 116 - S
    assert max(down.cells[0]) == CORE_ALPHA
    # a cat in the air above the floor shrinks
    air = shadow_for(g, g, (0, 20), "on_top", False)
    assert air.y == 116 - S and len(air.cells[0]) < len(down.cells[0])


def test_floor_is_where_the_rest_frame_stands():
    rest = _grid("..ooo", "..ooo")
    shifted = [r for r in rest[1:]] + ["." * 28]  # on screen, one row higher
    s = shadow_for(rest, shifted, (0, 0), "inside", False)
    assert s.y == 116 - S  # the floor from the rest frame, not the shifted one
    assert max(s.cells[0]) < CORE_ALPHA  # one row of lift fades it


def test_shadow_follows_dx_and_mirroring_flips_it():
    g = sprites.get_frames("content")[0]
    first, last = footprint(g)
    plain = shadow_for(g, g, (0, 0), "inside", False)
    assert shadow_for(g, g, (3, 0), "inside", False).x == plain.x + 3 * S
    right = shadow_for(g, g, (0, 0), "inside_right", True)
    origin_x = theme.placement("inside_right").origin[0]
    mirrored_first = 27 - last
    assert right.x == origin_x + (mirrored_first - 2) * S
    assert right.y == plain.y and right.cells == plain.cells
    # dx is negated for a mirrored cat
    assert shadow_for(g, g, (3, 0), "inside_right", True).x == right.x - 3 * S
