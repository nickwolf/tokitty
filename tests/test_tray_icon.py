"""tray_icon.py: the sprite choice the Qt tray draws."""
from tokitty.sprite_raster import raster_rgba
from tokitty.sprites import get_frames, resolve_palette
from tokitty.tray_icon import TRAY_ICON_SCALE, tray_raster


def test_tray_raster_is_the_content_frame_at_scale_two():
    expected = raster_rgba(get_frames("content")[0], resolve_palette("gray", "solid"), 2)
    assert TRAY_ICON_SCALE == 2
    assert tray_raster("gray", "solid") == expected
