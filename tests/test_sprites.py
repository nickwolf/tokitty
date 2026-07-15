import pytest

from tokitty.sprites import ALL_STATES, COATS, PALETTE, get_frames, get_palette


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


from tokitty.sprites import (
    ALERT_TEMPLATE, FLOPPED_TEMPLATE, SCALE, SITTING_TEMPLATE,
)

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


def test_every_coat_defines_exactly_the_coat_keys():
    for name, coat in COATS.items():
        assert set(coat) == {"o", "O", "s", "c", "p"}, name


def test_palette_covers_pattern_char():
    assert PALETTE["c"] == PALETTE["o"]  # invisible on the default coat
