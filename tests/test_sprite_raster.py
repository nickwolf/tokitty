from tokitty.sprite_raster import raster_frame, raster_rgba


def test_raster_frame_fills_bg_and_scales():
    frame = ["o."]
    palette = {"o": "#010203", ".": ""}
    bg = b"\x00\x00\x00"
    grid = raster_frame(frame, palette, scale=2, bg=bg)
    assert len(grid) == 2          # 1 row * scale 2
    assert len(grid[0]) == 4       # 2 cols * scale 2
    assert grid[0][0] == b"\x01\x02\x03"   # painted cell
    assert grid[0][2] == bg                # "." -> bg
    assert grid[1][3] == bg


def test_raster_rgba_transparent_empties():
    frame = ["o."]
    palette = {"o": "#010203", ".": ""}
    width, height, raw = raster_rgba(frame, palette, scale=1)
    assert (width, height) == (2, 1)
    assert raw == bytes((1, 2, 3, 255)) + bytes((0, 0, 0, 0))
