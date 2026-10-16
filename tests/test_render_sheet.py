import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from render_sheet import render_sheet
from tokitty import sprites
from tokitty.sprites import ALL_STATES, get_tracks


def _png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def _geometry(scale, states=ALL_STATES, wrap=8):
    """Expected sheet size: per frame a sprite cell plus the duration strip,
    wrapped onto continuation rows, one block of rows per track."""
    gap = 4
    counts = [len(t.frames) for s in states for t in get_tracks(s)]
    cell_w = 28 * scale
    cell_h = 26 * scale + 2 + 4 + 4
    per_row = min(wrap, max(counts))
    rows = sum(-(-n // per_row) for n in counts)
    return gap + per_row * (cell_w + gap), gap + rows * (cell_h + gap)


@pytest.fixture
def plain(monkeypatch):
    """A one-track, two-frame state at the old 800 ms, independent of the art."""
    monkeypatch.setitem(sprites.ANIMATIONS, "plain", sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({"L": "e", "R": "e", "A": "n"}), sprites.Frame({"L": "k", "R": "k", "A": "n"})]))
    # and a one-track, four-frame one
    monkeypatch.setitem(sprites.ANIMATIONS, "four", sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({"L": c, "R": "e", "A": "n"}) for c in "ekz!"]))


def test_sheet_is_valid_png_with_expected_dimensions(tmp_path):
    out = tmp_path / "sheet.png"
    order = render_sheet(out, scale=2)
    assert order == [s if len(get_tracks(s)) == 1 else f"{s}/{t.name}"
                     for s in ALL_STATES for t in get_tracks(s)]
    assert _png_size(out) == _geometry(2)


def test_sheet_wraps_long_states_onto_continuation_rows(tmp_path, plain):
    out = tmp_path / "sheet.png"
    render_sheet(out, scale=2, states=["four", "plain"], wrap=3)
    assert _png_size(out) == _geometry(2, ["four", "plain"], wrap=3)
    # four has 4 frames: 2 rows at wrap 3, plain 1 row
    assert _geometry(2, ["four", "plain"], wrap=3)[1] == 4 + 3 * (52 + 10 + 4)


def test_state_selection_keeps_the_requested_order(tmp_path, plain):
    out = tmp_path / "sheet.png"
    assert render_sheet(out, scale=1, states=["four", "plain"]) == ["four", "plain"]


def _pixel(path, x, y):
    data = path.read_bytes()
    idat_start = data.find(b"IDAT") + 4
    idat_len = struct.unpack(">I", data[idat_start - 8:idat_start - 4])[0]
    raw = zlib.decompress(data[idat_start:idat_start + idat_len])
    w, _ = _png_size(path)
    off = y * (1 + w * 3) + 1 + x * 3
    return tuple(raw[off:off + 3])


def test_duration_strip_and_loop_marker(tmp_path, monkeypatch):
    from tokitty import sprites

    anim = sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({"L": "e", "R": "e", "A": "n"}, ms=500),
         sprites.Frame({"L": "k", "R": "k", "A": "n"}, ms=(250, 1000))],
        loop_from=1)
    monkeypatch.setitem(sprites.ANIMATIONS, "synth", anim)
    out = tmp_path / "sheet.png"
    render_sheet(out, scale=1, states=["synth"])
    cell_w, sprite_h, gap = 28, 26, 4
    strip_y = gap + sprite_h + 2
    solid, light, mark, bg = (render_sheet.__globals__[k] for k in
                              ("STRIP_SOLID", "STRIP_LIGHT", "STRIP_MARK", "BG"))
    rgb = lambda c: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))  # noqa: E731
    # full scale is the longest hold (1000 ms), so 500 ms is half a cell wide
    x0 = gap
    assert _pixel(out, x0 + 13, strip_y) == rgb(solid)
    assert _pixel(out, x0 + 14, strip_y) == rgb(bg)
    # the ranged frame: lo (250 ms = 7 px) solid, then the lighter tail to hi (28 px)
    x1 = gap + cell_w + gap
    assert _pixel(out, x1 + 6, strip_y) == rgb(solid)
    assert _pixel(out, x1 + 7, strip_y) == rgb(light)
    assert _pixel(out, x1 + 27, strip_y) == rgb(light)
    # the marker sits under the loop_from frame only
    mark_y = strip_y + 4
    assert _pixel(out, x1 + 1, mark_y) == rgb(mark)
    assert _pixel(out, x0 + 1, mark_y) == rgb(bg)


def test_legend_line(plain):
    from render_sheet import legend

    assert legend(["plain"]) == ["plain: 800 800 (loop from 0)"]
    assert legend(["four"]) == ["four: 800 800 800 800 (loop from 0)"]


def _two_track(monkeypatch):
    from tokitty import sprites

    anim = sprites.Animation(sprites.SITTING_TEMPLATE, (
        sprites.Track("eyes", (sprites.Frame({"L": "e", "R": "e"}, ms=(100, 400)),
                               sprites.Frame({"L": "k", "R": "k"}, ms=60)), loop_from=None),
        sprites.Track("tail", (sprites.Frame({"A": "n"}, ms=500), sprites.Frame({"A": "z"}, ms=500),
                               sprites.Frame({"A": "!"}, ms=500)), loop_from=1),
    ))
    monkeypatch.setitem(sprites.ANIMATIONS, "synth", anim)


def test_multi_track_state_gets_one_row_per_track(tmp_path, monkeypatch, plain):
    from render_sheet import legend

    _two_track(monkeypatch)
    out = tmp_path / "sheet.png"
    labels = render_sheet(out, scale=2, states=["synth", "plain"])
    assert labels == ["synth/eyes", "synth/tail", "plain"]
    assert legend(["synth", "plain"]) == [
        "synth/eyes: 100-400 60 (play once)",
        "synth/tail: 500 500 500 (loop from 1)",
        "plain: 800 800 (loop from 0)",
    ]
    assert _png_size(out)[1] == 4 + 3 * (52 + 10 + 4)


def test_gif_per_state_uses_real_durations(tmp_path, plain):
    PIL = pytest.importorskip("PIL.Image")
    from render_sheet import write_gifs

    paths = write_gifs(tmp_path, ["four"], scale=1, seconds=3.2)
    assert [p.name for p in paths] == ["four.gif"]
    with PIL.open(paths[0]) as gif:
        assert gif.n_frames == 4 and gif.info["duration"] == 800 and gif.info["loop"] == 0


def test_gif_playback_is_seeded(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    from render_sheet import write_gif

    _two_track(monkeypatch)
    sizes = [write_gif(tmp_path / f"{n}.gif", "synth", scale=1, seed=seed, seconds=6)
             for n, seed in (("a", 1), ("b", 1), ("c", 2))]
    assert sizes[0] == sizes[1]
    assert (tmp_path / "a.gif").read_bytes() == (tmp_path / "b.gif").read_bytes()



def test_idat_decompresses_to_rgb_scanlines(tmp_path):
    out = tmp_path / "sheet.png"
    render_sheet(out, scale=1)
    data = out.read_bytes()
    idat_start = data.find(b"IDAT") + 4
    idat_len = struct.unpack(">I", data[idat_start - 8:idat_start - 4])[0]
    raw = zlib.decompress(data[idat_start:idat_start + idat_len])
    w, h = _png_size(out)
    assert len(raw) == h * (1 + w * 3)


def test_render_grid_writes_png(tmp_path):
    from scripts.render_sheet import render_grid
    out = tmp_path / "grid.png"
    labels = render_grid(out, scale=2)
    assert out.is_file() and out.stat().st_size > 0
    assert labels  # non-empty legend
