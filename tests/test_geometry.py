from tokitty.geometry import clamp_position, clamp_to_area, default_position


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


def test_clamp_to_area_keeps_a_position_on_a_monitor_right_of_the_primary():
    # The #60 case: x is past the primary's width, which is only
    # out of bounds if you measure against the primary.
    area = (1920, 0, 1920 + 2560, 1440)
    assert clamp_to_area(3000, 400, 300, 256, area) == (3000, 400)


def test_clamp_to_area_keeps_a_position_on_a_monitor_left_of_the_primary():
    area = (-1920, 0, 0, 1080)
    assert clamp_to_area(-1500, 200, 300, 256, area) == (-1500, 200)


def test_clamp_to_area_falls_back_inside_the_area_not_the_origin():
    area = (1920, 0, 1920 + 2560, 1400)
    assert clamp_to_area(1920 + 2500, 100, 300, 256, area) == (1920 + 2560 - 300 - 24, 1400 - 256 - 24)


def test_clamp_to_area_honours_a_work_area_that_excludes_the_taskbar():
    # 48px taskbar at the bottom: a position overlapping it is pulled out.
    area = (0, 0, 1920, 1032)
    x, y = clamp_to_area(100, 1000, 300, 256, area)
    assert y + 256 <= 1032


def test_clamp_to_area_matches_clamp_position_at_the_origin():
    area = (0, 0, 1920, 1080)
    for x, y in [(100, 100), (-500, 100), (5000, 5000)]:
        assert clamp_to_area(x, y, 300, 110, area) == clamp_position(x, y, 300, 110, 1920, 1080)


def test_default_position_is_bottom_right_of_the_work_area():
    assert default_position(300, 256, (0, 0, 1920, 1032)) == (1920 - 300 - 24, 1032 - 256 - 24)
    assert default_position(300, 256, (1920, 0, 4480, 1440)) == (4480 - 300 - 24, 1440 - 256 - 24)


def test_default_position_never_leaves_the_area_for_an_oversized_card():
    assert default_position(5000, 5000, (1920, 100, 3840, 1180)) == (1920, 100)
