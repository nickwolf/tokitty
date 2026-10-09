"""The app icon (#100): the content cat on a rounded card-coloured tile.

One master is drawn here and written as .icns for Tokitty.app and .ico for
Tokitty.exe by freeze/tokitty.spec at build time, so the icon always matches
the sprites in the build. A source run on macOS also uses it for the Dock
tile, which would otherwise be the Python rocket. Needs Pillow, which ships
everywhere the tray does; the hook executable never imports this.
"""
from __future__ import annotations

from tokitty.sprite_raster import raster_rgba
from tokitty.sprites import get_frames, resolve_palette

CARD_BG = (0x1C, 0x1C, 0x22, 255)  # ui.py card background

# Apple's macOS icon grid on a 1024 canvas: an 824 tile inset 100 px, with
# corners of about 185 px. The cat fills about 70% of the tile.
TILE_INSET = 100 / 1024
TILE_RADIUS = 185 / 1024
CAT_FILL = 0.7

# The iconset iconutil expects: each point size at 1x and 2x.
ICONSET_POINTS = [16, 32, 128, 256, 512]
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _cat():
    """The content frame cropped to the cat, as an RGBA image at 1 px per
    sprite pixel."""
    from PIL import Image

    width, height, data = raster_rgba(get_frames("content")[0], resolve_palette("orange", "tabby"), 1)
    cat = Image.frombytes("RGBA", (width, height), data)
    return cat.crop(cat.getbbox())


def render(size: int = 1024):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inset = round(size * TILE_INSET)
    ImageDraw.Draw(img).rounded_rectangle(
        (inset, inset, size - 1 - inset, size - 1 - inset), radius=round(size * TILE_RADIUS), fill=CARD_BG)

    cat = _cat()
    # Whole-number scaling keeps the pixel art square.
    scale = max(1, int((size - 2 * inset) * CAT_FILL) // max(cat.size))
    cat = cat.resize((cat.width * scale, cat.height * scale), Image.NEAREST)
    img.alpha_composite(cat, ((size - cat.width) // 2, (size - cat.height) // 2))
    return img


def write_icns(path) -> None:
    """macOS only: built by iconutil from a full iconset, since Pillow's own
    ICNS writer leaves out the 16 and 32 point 1x images Finder lists use."""
    import subprocess
    import tempfile
    from pathlib import Path

    from PIL import Image

    master = render(1024)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Tokitty.iconset"
        iconset.mkdir()
        for points in ICONSET_POINTS:
            for factor, suffix in ((1, ""), (2, "@2x")):
                pixels = points * factor
                master.resize((pixels, pixels), Image.LANCZOS).save(
                    iconset / f"icon_{points}x{points}{suffix}.png")
        subprocess.run(["iconutil", "-c", "icns", "-o", str(path), str(iconset)], check=True)


def write_ico(path) -> None:
    render(256).save(path, format="ICO", sizes=ICO_SIZES)


def photo(master, size: int = 256):
    """A Tk PhotoImage of the icon, for root.iconphoto on a source run."""
    import io
    import tkinter as tk

    buffer = io.BytesIO()
    render(size).save(buffer, format="PNG")
    return tk.PhotoImage(master=master, data=buffer.getvalue(), format="png")
