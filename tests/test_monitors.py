import sys

import pytest

from tokitty import monitors


@pytest.mark.skipif(sys.platform == "win32", reason="the no-op path is for everything else")
def test_monitor_lookups_are_none_off_windows():
    assert monitors.primary_work_area() is None
    assert monitors.work_area_for(0, 0, 300, 256) is None


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 monitor lookup")
def test_primary_work_area_is_a_real_rectangle_at_the_origin():
    area = monitors.primary_work_area()
    assert area is not None
    left, top, right, bottom = area
    # The primary monitor owns the virtual-desktop origin; its work area
    # can only start later than that, where a left or top taskbar sits.
    assert 0 <= left < right
    assert 0 <= top < bottom


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 monitor lookup")
def test_a_rectangle_on_no_monitor_resolves_to_the_nearest_one():
    # Far off any plausible desktop: stands in for a saved position on a
    # monitor that has since been unplugged.
    area = monitors.work_area_for(10**6, 10**6, 300, 256)
    assert area is not None
    left, top, right, bottom = area
    assert left < right and top < bottom


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 monitor lookup")
def test_the_primary_work_area_contains_a_rectangle_placed_inside_it():
    left, top, right, bottom = monitors.primary_work_area()
    assert monitors.work_area_for(left + 10, top + 10, 100, 100) == (left, top, right, bottom)
