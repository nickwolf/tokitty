import hashlib
import itertools
import json
import random
import sys
import threading

import pytest

from tokitty import sprites

from tokitty.sprites import (
    ALERT_TEMPLATE,
    ALL_STATES,
    ANIMATIONS,
    BASE_PALETTE,
    COLORWAYS,
    FLOPPED_TEMPLATE,
    LEGACY_COAT_MAP,
    MIN_FRAME_MS,
    PALETTE,
    PATTERNS,
    REGION_CHARS,
    SCALE,
    SITTING_TEMPLATE,
    Animation,
    Frame,
    Track,
    check_animation,
    compose,
    frame_ms,
    get_animation,
    get_frames,
    get_tracks,
    get_palette,
    single_track,
    resolve_palette,
)


def test_all_states_have_at_least_two_frames():
    for state in ALL_STATES:
        assert len(get_frames(state)) >= 2


def test_all_frame_rows_are_equal_length_within_a_frame():
    for state in ALL_STATES:
        for frame in get_frames(state):
            row_lengths = {len(row) for row in frame}
            assert len(row_lengths) == 1, f"{state} has mismatched row widths"


def test_all_frames_for_a_state_share_dimensions():
    for state in ALL_STATES:
        frames = get_frames(state)
        shapes = {(len(frame), len(frame[0])) for frame in frames}
        assert len(shapes) == 1, f"{state} frames differ in overall shape"


def test_every_character_used_is_in_the_palette():
    for state in ALL_STATES:
        for frame in get_frames(state):
            for row in frame:
                for ch in row:
                    assert ch in PALETTE, f"{state} uses undefined character {ch!r}"


def test_unknown_state_raises_key_error():
    with pytest.raises(KeyError):
        get_frames("nonexistent-mood")


CAT_CANVAS_SIZE = 112  # mirrors ui.py; sprites must fit it


def test_new_grid_dimensions():
    for template in (SITTING_TEMPLATE, ALERT_TEMPLATE, FLOPPED_TEMPLATE):
        assert len(template) == 26
        assert all(len(row) == 28 for row in template)


def test_sprite_fits_cat_canvas():
    for state in ALL_STATES:
        frame = get_frames(state)[0]
        assert len(frame[0]) * SCALE <= CAT_CANVAS_SIZE
        assert len(frame) * SCALE <= CAT_CANVAS_SIZE


def test_placeholders_appear_exactly_once_per_template():
    for template, placeholders in (
        (SITTING_TEMPLATE, "LRA"),
        (ALERT_TEMPLATE, "LRA"),
        (FLOPPED_TEMPLATE, "L"),
    ):
        joined = "".join(template)
        for ch in placeholders:
            assert joined.count(ch) == 1, f"{ch} appears {joined.count(ch)} times"
    # the flopped tail-sweep regions: every marker (unique and shared)
    # must be present or a pose silently loses segments
    joined = "".join(FLOPPED_TEMPLATE)
    for ch in "12345":
        assert joined.count(ch) >= 2, f"tail sweep {ch} too small"


def test_pattern_region_is_used():
    joined = "".join(SITTING_TEMPLATE + ALERT_TEMPLATE + FLOPPED_TEMPLATE)
    assert "c" in joined, "templates must use the patch region so coats can differ"


def test_get_palette_default_matches_module_palette():
    assert get_palette() == PALETTE


def test_get_palette_unknown_coat_raises_key_error():
    with pytest.raises(KeyError):
        get_palette("nonexistent-coat")


def test_palette_covers_pattern_char():
    assert PALETTE["c"] == PALETTE["o"]  # invisible on the default coat


def test_ground_line_is_not_coat_colored():
    # The ground char is defined in BASE_PALETTE, and no colorway x pattern
    # combination ever recolors it -- it's not one of the region chars.
    assert "G" in BASE_PALETTE
    assert "G" not in REGION_CHARS
    for colorway in COLORWAYS:
        for pattern in PATTERNS:
            assert resolve_palette(colorway, pattern)["G"] == BASE_PALETTE["G"]


# Frozen snapshot of the five legacy coats' region colors (o,O,s,c,p),
# captured so this proof survives the bundled coat presets being deleted.
_LEGACY_REGIONS = {
    "orange_tabby": {"o": "#e8823c", "O": "#c26a2c", "s": "#a8541f", "c": "#e8823c", "p": "#f6b8c8"},
    "gray_tabby":   {"o": "#a4aec2", "O": "#818ba0", "s": "#5f6879", "c": "#a4aec2", "p": "#e3a9ba"},
    "black":        {"o": "#4a4653", "O": "#38343f", "s": "#575263", "c": "#4a4653", "p": "#a8798c"},
    "white":        {"o": "#f1ebdf", "O": "#c4bcae", "s": "#ded6c6", "c": "#f1ebdf", "p": "#f6b8c8"},
    "calico":       {"o": "#f1ebdf", "O": "#c4bcae", "s": "#453a33", "c": "#e8823c", "p": "#f6b8c8"},
}


@pytest.mark.parametrize("legacy_name, regions", list(_LEGACY_REGIONS.items()))
def test_resolve_palette_reproduces_legacy_byte_identical(legacy_name, regions):
    colorway, pattern = LEGACY_COAT_MAP[legacy_name]
    palette = resolve_palette(colorway, pattern)
    for char, color in regions.items():          # region chars match legacy exactly
        assert palette[char] == color, (legacy_name, char)
    for char, color in BASE_PALETTE.items():      # every other char is untouched BASE_PALETTE
        if char in ("o", "O", "s", "c", "p"):
            continue
        assert palette[char] == color, (legacy_name, char)


def test_every_pattern_covers_region_chars():
    for name, pat in PATTERNS.items():
        assert set(pat.keys()) == set(REGION_CHARS), name


def test_colorways_define_all_tone_slots():
    for name, cw in COLORWAYS.items():
        assert set(cw.keys()) == {"coat", "shade", "mark", "light", "ear"}, name
        for k, v in cw.items():
            assert v.startswith("#") and len(v) == 7, (name, k)


def test_black_colorway_body_lighter_than_outline():
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.299 * r + 0.587 * g + 0.114 * b
    assert lum(COLORWAYS["black"]["coat"]) > lum(BASE_PALETTE["k"]) + 15


def test_every_state_char_defined_for_every_look():
    for colorway in COLORWAYS:
        for pattern in PATTERNS:
            palette = resolve_palette(colorway, pattern)
            for state in ALL_STATES:
                for frame in get_frames(state):
                    for row in frame:
                        for ch in row:
                            assert ch in palette, (colorway, pattern, state, ch)


def test_new_regions_present_and_covered():
    for ch in ("m", "x", "y", "u"):
        assert ch in REGION_CHARS
    # every pattern defines every region (closed set)
    for name, pat in PATTERNS.items():
        assert set(pat.keys()) == set(REGION_CHARS), name
    # the new chars actually appear in the base template art
    for name, template in (("sitting", SITTING_TEMPLATE), ("alert", ALERT_TEMPLATE),
                           ("flopped", FLOPPED_TEMPLATE)):
        joined = "".join(template)
        for ch in ("m", "x", "y", "u"):
            assert ch in joined, (name, ch)


def test_inner_ear_pink_preserved_and_symmetric():
    """Points (x) must not eat the inner-ear pink cells. Carving x over a p
    cell de-pinks one ear asymmetrically, and no palette-level test catches
    it -- p is colorway-driven, so only the template shape reveals it."""
    for name, template in (("sitting", SITTING_TEMPLATE), ("alert", ALERT_TEMPLATE),
                           ("flopped", FLOPPED_TEMPLATE)):
        cols_by_row = {}
        for r, row in enumerate(template):
            for c, ch in enumerate(row):
                if ch == "p":
                    cols_by_row.setdefault(r, []).append(c)
        assert cols_by_row, name
        for r, cols in cols_by_row.items():
            assert len(cols) == 2, (name, r, cols)


def test_lying_tail_sweep_paints_only_tail_chars():
    """The sweeping tail must paint the tail chars so it takes the pattern's
    tail tones; painting it "o" leaves a two-tone tail under van/colorpoint.
    Rows 11-15, columns 23-27 hold nothing but the tail tip of the belly-down
    pose (flopped now lies on its back)."""
    for state in ("stirring",):
        for frame in get_frames(state):
            tip = {frame[r][c] for r in range(11, 16) for c in range(23, 28)}
            assert tip <= {".", "y", "Y"}, (state, tip)


def test_transition_names_resolve_and_bad_transitions_are_rejected(monkeypatch):
    from tokitty.sprites import TRANSITIONS, _check_transitions, transition_name

    once = single_track(SITTING_TEMPLATE, [Frame({"L": "e", "R": "e", "A": "n"}, ms=100)], None)
    monkeypatch.setitem(TRANSITIONS, ("content", "sleeping"), once)
    assert get_animation(transition_name("content", "sleeping")) is once
    with pytest.raises(KeyError):
        get_animation(transition_name("sleeping", "content"))
    monkeypatch.setitem(TRANSITIONS, ("content", "nope"), once)
    with pytest.raises(ValueError):
        _check_transitions()
    monkeypatch.delitem(TRANSITIONS, ("content", "nope"))
    looping = single_track(SITTING_TEMPLATE, [Frame({"L": "e", "R": "e", "A": "n"}, ms=100)], 0)
    monkeypatch.setitem(TRANSITIONS, ("content", "sleeping"), looping)
    with pytest.raises(ValueError):
        _check_transitions()


def test_silhouette_patterns_present_and_covered():
    for name in ("tuxedo", "socks", "colorpoint", "van"):
        assert name in PATTERNS
        assert set(PATTERNS[name].keys()) == set(REGION_CHARS), name
    for name in ("cream", "brown"):
        assert name in COLORWAYS
    # colorpoint pales the body and darkens the extremities
    cp = PATTERNS["colorpoint"]
    assert cp["o"] == "light" and cp["x"] == "mark" and cp["m"] == "mark"
    # van: white body, colored ears + tail
    van = PATTERNS["van"]
    assert van["o"] == "white" and van["x"] == "coat" and van["y"] == "coat"


# ---- animation timing and tracks (#125) -------------------------------------

def _frames(*chars, ms=800):
    ms = ms if isinstance(ms, (list, tuple)) and len(ms) == len(chars) else [ms] * len(chars)
    return [Frame({"A": c}, ms=m) for c, m in zip(chars, ms)]


def test_all_states_follows_the_animation_table():
    assert ALL_STATES == tuple(ANIMATIONS)
    assert len(ALL_STATES) == 18 and ALL_STATES[0] == "sleeping" and ALL_STATES[-1] == "done_hop"


def test_the_table_passes_the_track_rules():
    for anim in ANIMATIONS.values():
        check_animation(anim)


def test_every_hold_is_valid():
    for state, anim in ANIMATIONS.items():
        for track in anim.tracks:
            for frame in track.frames:
                ends = (frame.ms,) if isinstance(frame.ms, int) else tuple(frame.ms)
                if len(ends) == 2:
                    assert ends[0] <= ends[1], state
                for ms in ends:
                    assert ms >= MIN_FRAME_MS and ms % 10 == 0, (state, ms)


def test_loop_from_is_none_or_a_valid_index():
    for state, anim in ANIMATIONS.items():
        for track in anim.tracks:
            assert track.loop_from is None or 0 <= track.loop_from < len(track.frames), state


def test_frame_templates_match_the_animation_template_dimensions():
    for state, anim in ANIMATIONS.items():
        for track in anim.tracks:
            for frame in track.frames:
                tmpl = frame.template if frame.template is not None else anim.template
                assert len(tmpl) == len(anim.template), state
                assert all(len(row) == len(anim.template[0]) for row in tmpl), state


def test_frame_zero_of_every_state_has_all_tracks_at_rest():
    # Icons and previews draw get_frames(state)[0]: every track at frame 0.
    for state in ALL_STATES:
        assert get_frames(state)[0] is sprites.compose(state, [0] * len(get_tracks(state))), state


def test_check_animation_rejects_rule_breaks():
    tmpl = ["A.", ".."]
    other = ["AA", ".."]
    bad = {
        "no tracks": Animation(tmpl, ()),
        "dup names": Animation(tmpl, (Track("a", tuple(_frames("x"))), Track("a", tuple(_frames("y"))))),
        "short hold": single_track(tmpl, _frames("x", "y", ms=30)),
        "odd hold": single_track(tmpl, _frames("x", "y", ms=125)),
        "lo > hi": single_track(tmpl, _frames("x", "y", ms=[(200, 100), 100])),
        "loop_from out of range": single_track(tmpl, _frames("x", "y"), loop_from=2),
        "shared keys": Animation(tmpl, (Track("a", tuple(_frames("x"))), Track("b", tuple(_frames("y"))))),
        "two template tracks": Animation(tmpl, (
            Track("a", (Frame({"A": "x"}, template=other),)),
            Track("b", (Frame({"B": "x"}, template=other),)))),
        "template size": single_track(tmpl, [Frame({"A": "x"}, template=["A"])]),
    }
    for name, anim in bad.items():
        with pytest.raises(ValueError):
            check_animation(anim)
        assert name
    good = Animation(tmpl, (Track("a", tuple(_frames("x"))),
                            Track("b", (Frame({"B": "y"}), Frame({"B": "z"}), ))))
    check_animation(good)


@pytest.fixture
def two_tracks(monkeypatch):
    """Track a swaps A, track b swaps B; frame 2 of a equals frame 1 of a's
    twin content-wise only through dedup of identical compositions."""
    tmpl = ["AB", ".."]
    anim = Animation(tmpl, (
        Track("a", (Frame({"A": "1"}, ms=100), Frame({"A": "2"}, ms=100), Frame({"A": "3"}, ms=100))),
        Track("b", (Frame({"B": "x"}, ms=350), Frame({"B": "y"}, ms=350))),
    ))
    monkeypatch.setitem(ANIMATIONS, "two", anim)
    return anim


def test_get_frames_single_track_is_the_frame_list(monkeypatch):
    frames = [Frame({"L": c, "R": "e", "A": "n"}, ms=100) for c in "ekz!"]
    monkeypatch.setitem(ANIMATIONS, "four", single_track(SITTING_TEMPLATE, frames))
    assert [g[8] for g in get_frames("four")] == [
        SITTING_TEMPLATE[8].replace("L", c).replace("R", "e") for c in "ekz!"]


def test_get_frames_enumeration_order(two_tracks):
    assert [tuple(g) for g in get_frames("two")] == [
        ("1x", ".."),                       # all tracks at 0
        ("2x", ".."), ("3x", ".."),        # track a frames 1.., b at 0
        ("1y", ".."),                       # track b frame 1, a at 0
    ]
    from tokitty.sprites import enumeration_positions
    assert enumeration_positions("two") == [(0, 0), (1, 0), (2, 0), (0, 1)]


def test_composition_merges_every_tracks_current_frame(two_tracks):
    assert compose("two", (2, 1)) == ["3y", ".."]


def test_composed_grids_are_interned_by_content(monkeypatch):
    tmpl = ["AB", ".."]
    anim = Animation(tmpl, (
        Track("a", (Frame({"A": "1"}), Frame({"A": "1"}))),  # a second frame identical to the first
        Track("b", (Frame({"B": "x"}), Frame({"B": "y"}))),
    ))
    monkeypatch.setitem(ANIMATIONS, "dup", anim)
    frames = get_frames("dup")
    assert len(frames) == 3  # nothing is dropped
    assert frames[1] is frames[0]  # but equal content is one object
    assert compose("dup", (1, 0)) is frames[0]
    assert get_frames("dup") is get_frames("dup")


def test_known_grids_survives_a_compose_on_another_thread(monkeypatch):
    # The Stream Dock server composes key images on its own thread while the
    # widget scans the cache in known_grids.
    monkeypatch.setattr(sprites, "_COMPOSED", {})
    monkeypatch.setattr(sprites, "_GRIDS", {})
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        tracks = sprites.get_tracks("sleeping")
        combos = list(itertools.product(*[range(len(t.frames)) for t in tracks]))
        done = threading.Event()

        def writer():
            for pos in combos:
                compose("sleeping", pos)
            done.set()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            while not done.is_set():
                sprites.known_grids("sleeping")
        finally:
            thread.join()
    finally:
        sys.setswitchinterval(interval)


def test_template_override_comes_from_the_one_track_that_has_it(monkeypatch):
    base, hop = ["A.", ".."], [".A", ".."]
    anim = Animation(base, (
        Track("body", (Frame({}), Frame({}, template=hop))),
        Track("eyes", (Frame({"A": "o"}), Frame({"A": "c"}))),
    ))
    # keys overlap between tracks only through the template char; subs are disjoint
    monkeypatch.setitem(ANIMATIONS, "ovr", anim)
    assert compose("ovr", (0, 0)) == ["o.", ".."]
    assert compose("ovr", (1, 1)) == [".c", ".."]


def test_get_animation_unknown_state_raises():
    with pytest.raises(KeyError):
        get_animation("nonexistent-mood")
    with pytest.raises(KeyError):
        get_frames("nonexistent-mood")


def test_get_frames_returns_the_same_grids_every_call():
    for state in ALL_STATES:
        a, b = get_frames(state), get_frames(state)
        assert a is b and all(x is y for x, y in zip(a, b))


def test_frame_ms_resolution():
    assert frame_ms(Frame({}, ms=120)) == 120
    assert frame_ms(Frame({}, ms=(40, 100))) == 70  # no rng: the midpoint
    assert frame_ms(Frame({}, ms=(40, 90))) == 70   # 65 rounds to a multiple of 10
    rng = random.Random(7)
    drawn = {frame_ms(Frame({}, ms=(40, 200)), rng) for _ in range(200)}
    assert drawn <= set(range(40, 201, 10)) and len(drawn) > 3
    assert frame_ms(Frame({}, ms=(50, 50)), rng) == 50


def test_jumps_land_on_the_spot_they_head_for_within_the_headroom():
    from tokitty import theme
    from tokitty.sprites import MOVES

    rise = (theme.placement("inside").origin[1] - theme.placement("on_top").origin[1]) // SCALE
    for move, sign in (("jump_up", -1), ("jump_down", 1)):
        frames = MOVES[move].tracks[0].frames
        assert frames[0].offset == (0, 0)
        assert frames[-1].offset == (0, sign * rise), move
        assert frames[-1].template == frames[0].template, move  # lands sitting, as it left
    # the overshoot above the edge stays inside the room kept clear over the card
    top = min(f.offset[1] for f in MOVES["jump_up"].tracks[0].frames)
    assert -top - rise <= theme.HOP_HEADROOM // SCALE


def test_state_hops_stay_inside_the_room_above_the_cat():
    # A state, or a change between states, plays in any spot, so its highest
    # hop has to fit both inside the card (above the inside cat's canvas) and in
    # the headroom kept over an on_top cat. Moves are #22's to place and are
    # checked on their own.
    from tokitty import theme
    from tokitty.sprites import SPOT_VARIANTS, TRANSITIONS

    inside_room = theme.placement("inside").origin[1] // SCALE
    anims = list(ANIMATIONS.values()) + list(SPOT_VARIANTS.values()) + list(TRANSITIONS.values())
    for anim in anims:
        for track in anim.tracks:
            for frame in track.frames:
                rise = -frame.offset[1]
                assert rise <= min(inside_room, theme.HOP_HEADROOM // SCALE), (track.name, frame.offset)


def test_state_transitions_land_on_the_target_frame_0():
    # the target's loop starts on frame 0, so a transition that ends on it hands
    # off without a visible jump
    from tokitty.sprites import TRANSITIONS, transition_name

    for (from_state, to_state), anim in TRANSITIONS.items():
        assert get_frames(transition_name(from_state, to_state))[-1] == get_frames(to_state)[0], \
            (from_state, to_state)


def test_walk_pieces_chain_and_keep_the_paws_on_the_edge():
    from tokitty.shadow import ground_row
    from tokitty.sprites import MOVES, _WALK_STRIDE

    start, walk, stop = (MOVES[m].tracks[0].frames for m in ("walk_start", "walk", "walk_stop"))
    # turning and sitting happen in place; the stride moves a cell a frame, flat
    assert all(f.offset == (0, 0) for f in start + stop)
    assert [f.offset for f in walk] == [(i, 0) for i in range(1, _WALK_STRIDE + 1)]
    # walk_start ends in the standing pose walk_stop starts from, and walk is drawn
    # from that same pose, so start, strides and stop join without a jump
    assert start[-1].template == stop[0].template == MOVES["walk"].template
    assert start[0].template == stop[-1].template  # sits as it sat
    # every frame stands on the same row as the sitting cat, so it walks on the edge
    sitting = ground_row(start[0].template)
    assert all(ground_row(f.template) == sitting for f in start + walk + stop)


def test_lie_down_and_sit_up_join_sitting_and_the_loaf_on_the_edge():
    from tokitty.shadow import ground_row
    from tokitty.sprites import MOVES, compose_offset, enumeration_positions, move_name, spot_state

    loaf = spot_state("content", "on_top")
    loaf_offset = compose_offset(loaf, enumeration_positions(loaf)[0])
    sitting = get_frames("content")[0]
    for move, first, last in (("lie_down", sitting, get_frames(loaf)[0]),
                              ("sit_up", get_frames(loaf)[0], sitting)):
        frames = MOVES[move].tracks[0].frames
        grids = get_frames(move_name(move))
        assert grids[0] == first and grids[-1] == last, move
        ends = {frames[0].offset, frames[-1].offset}
        assert ends == {(0, 0), loaf_offset}, move
        # the paws stay on the edge the whole way down and up
        edge = ground_row(sitting)
        assert all(ground_row(g) + f.offset[1] == edge for g, f in zip(grids, frames)), move


def _touching(cell, cells, diagonal=True):
    r, c = cell
    return any((r + dr, c + dc) in cells for dr in (-1, 0, 1) for dc in (-1, 0, 1)
               if (dr or dc) and (diagonal or not (dr and dc)))


def test_every_tail_stays_joined_to_the_cat():
    # Tail cells (the Y/y bands) reach the body through each other, so no
    # frame leaves a piece of tail floating beside the cat. Stepped diagonals
    # count as joined, the way the curled tails are drawn.
    from tokitty.sprites import (
        MOVES, SPOT_VARIANTS, TRANSITIONS, move_name, spot_state, transition_name,
    )

    names = (list(ALL_STATES) + [spot_state(*key) for key in SPOT_VARIANTS]
             + [move_name(m) for m in MOVES] + [transition_name(*pair) for pair in TRANSITIONS])
    for name in names:
        for i, grid in enumerate(get_frames(name)):
            cells = {(r, c): ch for r, row in enumerate(grid) for c, ch in enumerate(row) if ch != "."}
            tail = {cell for cell, ch in cells.items() if ch in "Yy"}
            body = set(cells) - tail
            joined = {cell for cell in tail if _touching(cell, body)}
            grew = True
            while grew:
                more = {cell for cell in tail - joined if _touching(cell, joined)}
                joined |= more
                grew = bool(more)
            assert joined == tail, (name, i, sorted(tail - joined))


def test_a_template_override_keeps_every_other_tracks_pose():
    # A frame that swaps the template (a breath, a nod, a lifted head) may move
    # another track's pose with the head, but never drop cells of it: each pose
    # still changes as many cells as it does on the base template. Working's
    # screen light stays with the screen, so a lean hides part of a band.
    from tokitty.sprites import (
        MOVES, SPOT_VARIANTS, TRANSITIONS, move_name, spot_state, transition_name,
    )

    def changed(a, b):
        return sum(x != y for ra, rb in zip(a, b) for x, y in zip(ra, rb))

    allowed = {("working", "lean", "glow")}
    names = (list(ALL_STATES) + [spot_state(*key) for key in SPOT_VARIANTS]
             + [move_name(m) for m in MOVES] + [transition_name(*pair) for pair in TRANSITIONS])
    for name in names:
        tracks = get_animation(name).tracks
        for t, track in enumerate(tracks):
            for f, frame in enumerate(track.frames):
                if frame.template is None:
                    continue
                for o, other in enumerate(tracks):
                    if o == t or (name, track.name, other.name) in allowed:
                        continue
                    if any(g.template is not None for g in other.frames):
                        continue
                    for j in range(1, len(other.frames)):
                        base, over = [0] * len(tracks), [0] * len(tracks)
                        over[t] = f
                        expected = changed(compose(name, base), compose(name, base[:o] + [j] + base[o + 1:]))
                        got = changed(compose(name, over), compose(name, over[:o] + [j] + over[o + 1:]))
                        assert got == expected, (name, track.name, f, other.name, j)


def _placed(grid, offset):
    """The drawn cells of a grid, moved by the frame's offset."""
    dx, dy = offset
    return {(r + dy, c + dx) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch != "."}


_NOT_PLAY_MOVES = ("jump_up", "jump_down")
_ENDS_ELSEWHERE = _NOT_PLAY_MOVES + ("walk_start", "walk", "walk_stop", "lie_down", "sit_up")


def test_every_move_stays_within_the_room_over_the_cat():
    from tokitty import theme
    from tokitty.sprites import MOVES, move_name

    room = theme.HOP_HEADROOM // SCALE
    for move in MOVES:
        if move in _NOT_PLAY_MOVES:
            continue
        frames = MOVES[move].tracks[0].frames
        for i, (grid, frame) in enumerate(zip(get_frames(move_name(move)), frames)):
            top = min(r for r, c in _placed(grid, (0, 0)))
            assert top + frame.offset[1] >= -room, (move, i)


def test_every_move_starts_and_ends_where_the_cat_sits():
    from tokitty.sprites import LYING_MOVES, MOVES, compose_offset, enumeration_positions, move_name, spot_state

    sit = _placed(sprites._hop_frame(), (0, 0))
    loaf = spot_state("content", "on_top")
    loaf_cells = _placed(get_frames(loaf)[0], compose_offset(loaf, enumeration_positions(loaf)[0]))
    for move in MOVES:
        if move in _ENDS_ELSEWHERE:
            continue
        frames = MOVES[move].tracks[0].frames
        grids = get_frames(move_name(move))
        assert _placed(grids[0], frames[0].offset) == sit, move
        want = loaf_cells if move in LYING_MOVES else sit
        assert _placed(grids[-1], frames[-1].offset) == want, move


def _pieces(grid):
    """How many separate pieces the body (not the tail) is drawn in."""
    cells = {(r, c) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch not in ".yY"}
    count = 0
    while cells:
        count += 1
        stack = [cells.pop()]
        while stack:
            r, c = stack.pop()
            for near in ((r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)):
                if near in cells:
                    cells.remove(near)
                    stack.append(near)
    return count


def test_the_face_on_rolls_stay_on_the_floor_and_the_body_moves_smoothly():
    # raised paws once wrapped to the bottom row through negative indexes,
    # which split the body in two and lifted the cat ten rows
    from tokitty.sprites import MOVES, move_name

    for move in ("roll", "tail_catch_roll", "bat_down_roll"):
        frames = MOVES[move].tracks[0].frames
        # bat_down_roll's paw hangs over the edge on purpose; its roll comes
        # after. A tail may hang over (the loaf's does), so only the body counts.
        hung = [i for i, f in enumerate(frames) if f.offset[1] == sprites._HANG_DROP[1]]
        first = hung[-1] + 1 if move == "bat_down_roll" else 0
        centres = []
        for i, (grid, frame) in enumerate(zip(get_frames(move_name(move)), frames)):
            dx, dy = frame.offset
            body = [r for r, row in enumerate(grid) if any(ch not in ".yY" for ch in row)]
            if i >= first:
                assert max(body) + dy <= 25, (move, i)
                assert _pieces(grid) == 1, (move, i)
            cols = [c for row in grid for c, ch in enumerate(row) if ch not in ".yY"]
            centres.append((min(cols) + max(cols)) / 2 + dx)
        for i, (a, b) in enumerate(zip(centres, centres[1:])):
            assert abs(b - a) <= 2.5, (move, i)


def test_the_card_the_props_ride_on_mirrors_the_themes_and_the_mirror_reaches_its_edge():
    from tokitty import theme

    assert sprites.KO_CARD == (theme.CARD_WIDTH, theme.CARD_HEIGHT)
    assert sprites.KO_CAT_X == theme.placement("on_top").origin[0] == theme.placement("inside").origin[0]
    wide = sprites.grid_size("move:mouse")[0]
    assert theme.placement("inside_right").origin[0] - (wide - 28) * SCALE == 0


def test_only_the_prop_moves_have_a_grid_bigger_than_the_cat():
    from tokitty.sprites import MOVES, SPOT_VARIANTS, TRANSITIONS, move_name, spot_state, transition_name

    props = {"knock_off", "mouse", "wand"}
    plain = (list(ALL_STATES) + [spot_state(*key) for key in SPOT_VARIANTS]
             + [transition_name(*pair) for pair in TRANSITIONS]
             + [move_name(m) for m in MOVES if m not in props])
    for name in plain:
        assert sprites.grid_size(name) == (28, 26), name
    for move in props:
        assert sprites.grid_size(move_name(move)) != (28, 26), move


def test_art_is_pinned():
    # Pins the art byte for byte, so a change to any frame is deliberate.
    # The #125 art passes change pixels on purpose: update this hash with them.
    from tokitty.sprites import (
        MOVES, SPOT_VARIANTS, TRANSITIONS, compose_offset, enumeration_positions, move_name,
        spot_state, transition_name,
    )

    names = (list(ALL_STATES) + [spot_state(*key) for key in SPOT_VARIANTS]
             + [move_name(m) for m in MOVES] + [transition_name(*pair) for pair in TRANSITIONS])
    frames = {n: [get_frames(n), [compose_offset(n, pos) for pos in enumeration_positions(n)]]
              for n in names}
    digest = hashlib.sha256(json.dumps(frames).encode()).hexdigest()
    assert digest == "d2eb62e23f1f1e2dd7d7c7aebae6a42f30df9b74a577359671adf5e33ff8b1fb"


def test_shut_eyes_use_the_fur_darkened_not_the_outline():
    from tokitty.sprites import LID_CHAR, lid_tone

    assert lid_tone("#e8823c") == "#74411e"
    for colorway in COLORWAYS:
        for pattern in PATTERNS:
            palette = resolve_palette(colorway, pattern)
            assert palette[LID_CHAR] == lid_tone(palette["o"]), (colorway, pattern)
    assert LID_CHAR not in BASE_PALETTE and LID_CHAR not in REGION_CHARS


def test_plain_tail_is_the_patterns_own_and_ringed_paints_the_bands():
    from tokitty.sprites import TAIL_STYLES

    assert TAIL_STYLES["ringed"] == ("coat", "mark")  # the default ring pair
    for colorway, tones in COLORWAYS.items():
        for pattern in PATTERNS:
            assert resolve_palette(colorway, pattern, "plain") == resolve_palette(colorway, pattern)
            for style, pair in TAIL_STYLES.items():
                if pair is None:
                    continue
                palette = resolve_palette(colorway, pattern, style)
                assert (palette["y"], palette["Y"]) == (tones[pair[0]], tones[pair[1]])
                plain = resolve_palette(colorway, pattern)
                tail_chars = {"y", "Y", sprites.DIM_CHARS["y"], sprites.DIM_CHARS["Y"]}
                assert {k: v for k, v in palette.items() if k not in tail_chars} == \
                    {k: v for k, v in plain.items() if k not in tail_chars}


def test_dark_room_and_glow_tones_derive_from_each_look():
    from tokitty.sprites import DIM_CHARS, GLOW_CHAR, dim_tone, glow_tone

    assert dim_tone("#e8823c") == "#74411e"
    for colorway in COLORWAYS:
        for pattern in PATTERNS:
            palette = resolve_palette(colorway, pattern)
            assert palette[GLOW_CHAR] == glow_tone(palette["o"])
            for char, dark in DIM_CHARS.items():
                assert palette[dark] == dim_tone(palette[char]), (colorway, pattern, char)
    assert not set(DIM_CHARS.values()) & (set(BASE_PALETTE) | set(REGION_CHARS))


def test_resolve_palette_returns_independent_equal_dicts():
    first, second = resolve_palette("gray", "calico", "ringed"), resolve_palette("gray", "calico", "ringed")
    assert first == second and first is not second
    first["o"] = "#010203"
    assert resolve_palette("gray", "calico", "ringed") == second
    assert resolve_palette("gray", "calico") != second


def test_offsets_live_on_one_track_and_compose():
    from tokitty.sprites import Animation, Track, check_animation, compose_offset

    hop = Track("body", (Frame({}, ms=100), Frame({}, ms=100, offset=(0, -3))), None)
    anim = Animation(SITTING_TEMPLATE, (hop, Track("eyes", (Frame({"L": "e"}, ms=100),), None)))
    check_animation(anim)
    clash = Animation(SITTING_TEMPLATE, (hop, Track("tail", (Frame({}, ms=100, offset=(1, 0)),), None)))
    with pytest.raises(ValueError):
        check_animation(clash)
    for state in ALL_STATES:
        assert compose_offset(state, [0] * len(get_animation(state).tracks)) == (0, 0)


def test_spot_variants_and_moves_resolve_and_are_checked(monkeypatch):
    from tokitty import sprites
    from tokitty.sprites import (
        MOVES, SPOT_VARIANTS, TRANSITIONS, _check_transitions, compose_offset, move_name,
        spot_state, transition_name,
    )

    once = single_track(SITTING_TEMPLATE, [Frame({"L": "e", "R": "e", "A": "n"}, ms=100,
                                                 offset=(0, -29))], None)
    looping = single_track(SITTING_TEMPLATE, [Frame({"L": "e", "R": "e", "A": "n"}, ms=100)], 0)
    assert spot_state("panicked", "on_top") == "panicked"
    monkeypatch.setitem(SPOT_VARIANTS, ("panicked", "on_top"), looping)
    assert spot_state("panicked", "on_top") == "panicked@on_top"
    assert get_animation("panicked@on_top") is looping
    with pytest.raises(KeyError):
        get_animation("confused@on_top")
    # a transition may name a variant on either side
    monkeypatch.setitem(TRANSITIONS, ("alert@on_top", "content"), once)
    assert get_animation(transition_name("alert@on_top", "content")) is once
    _check_transitions()
    monkeypatch.setitem(MOVES, "jump_up", once)
    assert get_animation(move_name("jump_up")) is once
    assert compose_offset(move_name("jump_up"), [0]) == (0, -29)
    _check_transitions()
    monkeypatch.setitem(MOVES, "walk", looping)
    with pytest.raises(ValueError):
        _check_transitions()
    monkeypatch.delitem(MOVES, "walk")
    monkeypatch.setitem(SPOT_VARIANTS, ("nope", "on_top"), looping)
    with pytest.raises(ValueError):
        _check_transitions()
    assert sprites.SPOT_SEP not in "".join(ALL_STATES)


def test_every_colorway_routes_its_tones_through_every_pattern():
    """The cat pixel refs draw one colorway (#156), so this pins the rest: each
    region char takes the tone its pattern names, the ears take the colorway's
    ear tone, and every other char is BASE_PALETTE's."""
    for colorway, tones in COLORWAYS.items():
        tone_of = dict(tones, white=BASE_PALETTE["w"])
        for pattern, regions in PATTERNS.items():
            palette = resolve_palette(colorway, pattern)
            for char, src in regions.items():
                assert palette[char] == tone_of.get(src, src), (colorway, pattern, char)
            assert palette["p"] == tones["ear"], (colorway, pattern)
            for char, value in BASE_PALETTE.items():
                if char not in regions and char != "p":
                    assert palette[char] == value, (colorway, pattern, char)
