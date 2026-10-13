"""tray_icon.py: the sprite choice the Tk and Qt trays share."""
from tokitty import tray
from tokitty.sprite_raster import raster_rgba
from tokitty.sprites import get_frames, resolve_palette
from tokitty.tray_icon import TRAY_ICON_SCALE, tray_raster


def test_tray_raster_is_the_content_frame_at_scale_two():
    expected = raster_rgba(get_frames("content")[0], resolve_palette("gray", "solid"), 2)
    assert TRAY_ICON_SCALE == 2
    assert tray_raster("gray", "solid") == expected


def test_default_image_factory_centres_the_raster_on_a_square():
    width, height, raw = tray_raster("orange", "tabby")
    side = max(width, height)

    img = tray._default_image_factory("orange", "tabby")

    assert img.mode == "RGBA"
    assert img.size == (side, side)
    left, top = (side - width) // 2, (side - height) // 2
    assert img.crop((left, top, left + width, top + height)).tobytes() == raw


def test_tray_still_exposes_the_scale():
    assert tray.TRAY_ICON_SCALE == 2
