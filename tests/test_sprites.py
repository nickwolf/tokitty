import pytest

from tokitty.sprites import (
    ALERT_TEMPLATE,
    ALL_STATES,
    BASE_PALETTE,
    COLORWAYS,
    FLOPPED_TEMPLATE,
    LEGACY_COAT_MAP,
    PALETTE,
    PATTERNS,
    REGION_CHARS,
    SCALE,
    SITTING_TEMPLATE,
    get_frames,
    get_palette,
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
