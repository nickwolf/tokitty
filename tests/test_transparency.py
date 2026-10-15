import pytest

from tokitty.transparency import (
    DEFAULT_LEVEL, LEVELS, MIN_LEVEL, alpha_for, clamp_level, effective_level,
)


def test_levels_descend_from_opaque_to_the_floor():
    assert LEVELS == (100, 90, 80, 70, 60, 50)
    assert MIN_LEVEL == 50


def test_the_floor_is_well_above_invisible():
    # A fully transparent card could only be recovered through the tray icon,
    # which the user may have turned off.
    assert MIN_LEVEL >= 50


@pytest.mark.parametrize("value", [100, 90, 50])
def test_supported_levels_pass_through(value):
    assert clamp_level(value) == value


@pytest.mark.parametrize("value,expected", [(55, 50), (95, 90), (0, 50), (1000, 100), (74, 70)])
def test_unsupported_levels_snap_to_the_nearest(value, expected):
    assert clamp_level(value) == expected


@pytest.mark.parametrize("value", [None, "80", True, False, object()])
def test_unusable_values_fall_back_to_the_default(value):
    assert clamp_level(value) == DEFAULT_LEVEL


def test_alpha_is_the_level_over_one_hundred():
    assert alpha_for(60) == 0.6
    assert alpha_for(100) == 1.0


def test_accent_forces_full_opacity():
    assert effective_level(50, accented=True) == 100
    assert effective_level(50, accented=False) == 50
