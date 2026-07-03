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
