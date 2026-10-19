"""Position conversion, loading, saving and placing: plain data, no Qt."""
import json

import pytest

from tokitty.qt import position
from tokitty.qt.position import ScreenInfo


def screen(nx, ny, nw, nh, ratio, lx=None, ly=None):
    lx = nx if lx is None else lx
    ly = ny if ly is None else ly
    return ScreenInfo((nx, ny, nw, nh), (lx, ly, round(nw / ratio), round(nh / ratio)), ratio)


# 2560 px primary at 150%, a 100% monitor at physical x=2560 (Qt keeps its corner).
HIGH_THEN_LOW = [screen(0, 0, 2560, 1440, 1.5), screen(2560, 0, 1920, 1080, 1.0)]
# the reverse order: a 100% primary, then a 150% monitor
LOW_THEN_HIGH = [screen(0, 0, 1920, 1080, 1.0), screen(1920, 0, 2560, 1440, 1.5)]


def test_macos_is_identity():
    assert position.from_tk(2700, 40, HIGH_THEN_LOW, 1.5, "darwin") == (2700, 40)


def test_primary_point_is_divided_by_the_system_ratio():
    assert position.from_tk(300, 150, HIGH_THEN_LOW, 1.5, "win32") == (200, 100)


def test_secondary_point_converts_relative_to_its_screen():
    # one division of the whole point would give 1800, which is on no screen
    assert position.from_tk(2700, 30, HIGH_THEN_LOW, 1.5, "win32") == (2560 + 93, 20)


def test_mixed_dpi_reverse_order():
    # system ratio 1.0: the primary is 100%, so its points are unchanged
    assert position.from_tk(100, 50, LOW_THEN_HIGH, 1.0, "win32") == (100, 50)
    assert position.from_tk(2000, 90, LOW_THEN_HIGH, 1.0, "linux") == (2000, 90)


def test_secondary_with_a_logical_offset():
    screens = [screen(0, 0, 3000, 1500, 1.5), screen(3000, 0, 1500, 1000, 1.5, lx=2000)]
    assert position.from_tk(3300, 0, screens, 1.5, "win32") == (2200, 0)


def test_point_on_no_screen_is_unchanged():
    assert position.from_tk(9000, 9000, HIGH_THEN_LOW, 1.5, "win32") == (9000, 9000)


def write(tmp_path, data):
    path = tmp_path / "position.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def load(path, platform="win32"):
    return position.load(path, HIGH_THEN_LOW, 1.5, platform)


def test_logical_file_is_used_as_is(tmp_path):
    assert load(write(tmp_path, {"x": 2700, "y": 30, "unit": "logical"})) == (2700, 30)


def test_tk_file_is_converted_on_windows_and_not_on_macos(tmp_path):
    path = write(tmp_path, {"x": 300, "y": 150})
    assert load(path, "win32") == (200, 100)
    assert load(path, "darwin") == (300, 150)


@pytest.mark.parametrize("data", [
    "not json", "[1, 2]", {}, {"x": 1}, {"x": "1", "y": 2}, {"x": True, "y": False},
    {"x": 1.5, "y": 2}, {"x": None, "y": 3}, {"x": 1, "y": 2, "unit": "furlongs"},
])
def test_garbage_loads_as_none(tmp_path, data):
    assert load(write(tmp_path, data)) is None


def test_missing_file_loads_as_none(tmp_path):
    assert load(tmp_path / "nope.json") is None


def test_save_is_atomic_and_logical(tmp_path):
    path = tmp_path / "position.json"
    assert position.save(path, -5, 40)
    assert json.loads(path.read_text()) == {"x": -5, "y": 40, "unit": "logical"}
    assert not (tmp_path / "position.json.tmp").exists()
    assert load(path) == (-5, 40)


def test_save_failure_is_swallowed(tmp_path):
    assert position.save(tmp_path / "missing-dir" / "p.json", 1, 2) is False


AREA = (0, 0, 1000, 800)


def test_place_puts_the_first_card_where_saved_minus_margins():
    assert position.place((300, 200), (300, 128), (112, 0, 0, 0), AREA) == (188, 200)


def test_place_clamps_the_grid_not_the_window():
    # card at x=0 is on screen; the 112 px margin hangs off the left edge
    assert position.place((0, 100), (300, 128), (112, 0, 0, 0), AREA) == (-112, 100)


def test_place_pulls_an_offscreen_grid_back_on():
    x, y = position.place((5000, 5000), (300, 128), (0, 104, 0, 0), AREA)
    assert 0 <= x and x + 300 <= 1000 and 0 <= y + 104 and y + 104 + 128 <= 800


def test_place_default_is_the_bottom_right_corner():
    from tokitty.geometry import default_position

    cx, cy = default_position(300, 128, AREA)
    assert position.place(None, (300, 128), (112, 104, 0, 0), AREA) == (cx - 112, cy - 104)


def test_a_margin_change_keeps_the_card_in_place():
    a = position.place((400, 300), (300, 128), (0, 0, 0, 0), AREA)
    b = position.place((400, 300), (300, 128), (112, 0, 0, 0), AREA)
    assert (b[0] + 112, b[1]) == a


def test_the_headroom_change_keeps_a_saved_on_top_card_in_place():
    from tokitty import theme as T

    old_top = T.SPRITE_H  # the margin before HOP_HEADROOM
    new_top = T.layout_window(["on_top"]).margins[1]
    assert new_top == old_top + T.HOP_HEADROOM
    # position.json holds the first card, so the window follows the margin
    old = position.place((400, 300), (300, 128), (0, old_top, 0, 0), AREA)
    new = position.place((400, 300), (300, 128), (0, new_top, 0, 0), AREA)
    assert (old[0], old[1] + old_top) == (new[0], new[1] + new_top) == (400, 300)
