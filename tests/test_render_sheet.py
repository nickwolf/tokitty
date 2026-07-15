import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from render_sheet import render_sheet
from tokitty.sprites import ALL_STATES, get_frames


def _png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_sheet_is_valid_png_with_expected_dimensions(tmp_path):
    out = tmp_path / "sheet.png"
    order = render_sheet(out, scale=2)
    assert order == list(ALL_STATES)
    w, h = _png_size(out)
    gap = 4
    max_frames = max(len(get_frames(s)) for s in ALL_STATES)
    cell_w = max(len(f[0][0]) for f in (get_frames(s) for s in ALL_STATES)) * 2
    cell_h = max(len(f[0]) for f in (get_frames(s) for s in ALL_STATES)) * 2
    expected_w = gap + max_frames * (cell_w + gap)
    expected_h = gap + len(ALL_STATES) * (cell_h + gap)
    assert (w, h) == (expected_w, expected_h)


def test_idat_decompresses_to_rgb_scanlines(tmp_path):
    out = tmp_path / "sheet.png"
    render_sheet(out, scale=1)
    data = out.read_bytes()
    idat_start = data.find(b"IDAT") + 4
    idat_len = struct.unpack(">I", data[idat_start - 8:idat_start - 4])[0]
    raw = zlib.decompress(data[idat_start:idat_start + idat_len])
    w, h = _png_size(out)
    assert len(raw) == h * (1 + w * 3)
