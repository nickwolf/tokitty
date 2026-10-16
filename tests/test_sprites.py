import hashlib
import json
import random

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
    for frames in (get_frames("done_hop"),):
        for frame in frames:
            bottom_rows = frame[-3:]
            joined = "".join("".join(r) for r in bottom_rows)
            assert "G" in joined  # ground exists
    # the ground char is defined in BASE_PALETTE, and no colorway x pattern
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
    Rows 11-15, columns 23-27 hold nothing but the tail tip."""
    for state in ("flopped", "stirring"):
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
    assert len(ALL_STATES) == 14 and ALL_STATES[0] == "sleeping" and ALL_STATES[-1] == "done_hop"


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


def test_art_is_pinned():
    # Pins the art byte for byte, so a change to any frame is deliberate.
    # The #125 art passes change pixels on purpose: update this hash with them.
    frames = {s: get_frames(s) for s in ALL_STATES}
    digest = hashlib.sha256(json.dumps(frames).encode()).hexdigest()
    assert digest == "a0473043aeffdb42576f8a585459025124358d61391901d62737ba0d7f916cd7"


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
                assert {k: v for k, v in palette.items() if k not in "yY"} == \
                    {k: v for k, v in plain.items() if k not in "yY"}
