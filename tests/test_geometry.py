from tokitty.geometry import clamp_position


def test_clamp_position_leaves_in_bounds_position_unchanged():
    assert clamp_position(100, 100, 300, 110, 1920, 1080) == (100, 100)


def test_clamp_position_resets_offscreen_negative_position():
    x, y = clamp_position(-500, 100, 300, 110, 1920, 1080)
    assert 0 <= x <= 1920 - 300
    assert 0 <= y <= 1080 - 110


def test_clamp_position_resets_position_beyond_screen_bounds():
    x, y = clamp_position(5000, 5000, 300, 110, 1920, 1080)
    assert x == 1920 - 300 - 24
    assert y == 1080 - 110 - 24


def test_clamp_position_handles_window_larger_than_screen():
    assert clamp_position(100, 100, 5000, 5000, 1920, 1080) == (0, 0)


def test_saved_bottom_edge_position_clamped_for_taller_card():
    # A position saved by the 128px single-pane card, restored by the
    # 256px dual-pane card, must be pulled up so the card stays on screen.
    screen_w, screen_h = 1920, 1080
    saved_x, saved_y = 1596, 1080 - 128 - 24  # v1 default bottom-right
    x, y = clamp_position(saved_x, saved_y, 300, 256, screen_w, screen_h)
    assert y + 256 <= screen_h
    assert x == saved_x
