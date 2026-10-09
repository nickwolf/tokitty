"""The app icon (#100): one master drawn from the cat sprite, written as
.icns for Tokitty.app and .ico for Tokitty.exe."""
import shutil
import subprocess

import pytest

Image = pytest.importorskip("PIL.Image")

from tokitty import app_icon  # noqa: E402


def test_master_is_1024_square_with_transparent_corners():
    img = app_icon.render(1024)
    assert img.size == (1024, 1024)
    assert img.mode == "RGBA"
    for xy in [(0, 0), (1023, 0), (0, 1023), (1023, 1023)]:
        assert img.getpixel(xy)[3] == 0


def test_tile_is_the_card_background():
    img = app_icon.render(1024)
    # Inside the rounded tile, above the cat.
    assert img.getpixel((512, 130)) == (0x1C, 0x1C, 0x22, 255)


def test_the_cat_is_drawn_in_the_middle():
    img = app_icon.render(1024)
    middle = img.crop((312, 312, 712, 712))
    colors = {px for px in middle.get_flattened_data() if px[3] == 255}
    assert len(colors) > 3


@pytest.mark.skipif(shutil.which("iconutil") is None, reason="iconutil is macOS only")
def test_icns_carries_the_full_size_set(tmp_path):
    path = tmp_path / "Tokitty.icns"
    app_icon.write_icns(path)
    # Pillow's reader skips the small 1x images, so unpack with iconutil.
    iconset = tmp_path / "out.iconset"
    subprocess.run(["iconutil", "-c", "iconset", "-o", str(iconset), str(path)], check=True)
    names = {p.name for p in iconset.iterdir()}
    for points in (16, 32, 128, 256, 512):
        assert f"icon_{points}x{points}.png" in names
        assert f"icon_{points}x{points}@2x.png" in names
    with Image.open(iconset / "icon_512x512@2x.png") as largest:
        assert largest.size == (1024, 1024)


def test_ico_goes_up_to_256(tmp_path):
    path = tmp_path / "Tokitty.ico"
    app_icon.write_ico(path)
    with Image.open(path) as ico:
        assert ico.format == "ICO"
        assert (256, 256) in ico.info["sizes"]
        assert (16, 16) in ico.info["sizes"]
