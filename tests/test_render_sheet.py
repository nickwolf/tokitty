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
    # the cell also holds every frame's offset (done_hop rises three rows)
    offsets = [f.offset for s in states for t in get_tracks(s) for f in t.frames] + [(0, 0)]
    xs, ys = [o[0] for o in offsets], [o[1] for o in offsets]
    cell_w = (28 + max(xs) - min(xs)) * scale
    cell_h = (26 + max(ys) - min(ys)) * scale + 2 + 4 + 4
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


@pytest.fixture
def transition(monkeypatch):
    for name in ("sa", "sb"):
        monkeypatch.setitem(sprites.ANIMATIONS, name, sprites.single_track(
            sprites.SITTING_TEMPLATE,
            [sprites.Frame({"A": c}, ms=100) for c in "nz"]))
    monkeypatch.setitem(sprites.TRANSITIONS, ("sa", "sb"), sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({"L": c}, ms=200) for c in "ek!"], loop_from=None))


def test_sheet_and_legend_accept_a_transition_name(tmp_path, transition):
    from render_sheet import legend

    out = tmp_path / "sheet.png"
    assert render_sheet(out, scale=1, states=["sa>sb"]) == ["sa>sb"]
    assert legend(["sa>sb"]) == ["sa>sb: 200 200 200 (play once)"]


def test_transition_gif_is_named_without_the_separator(tmp_path, transition):
    PIL = pytest.importorskip("PIL.Image")
    from render_sheet import write_gifs

    paths = write_gifs(tmp_path, ["sa>sb"], scale=1, seconds=1.0)
    assert [p.name for p in paths] == ["sa-to-sb.gif"] and ">" not in paths[0].name
    with PIL.open(paths[0]) as gif:
        # 3 transition frames of 200 ms, then the target alternating every 100 ms
        durations = []
        for i in range(gif.n_frames):
            gif.seek(i)
            durations.append(gif.info["duration"])
    assert sum(durations) == 1000 and durations[:3] == [200, 200, 200]


def test_render_media_writes_a_gif_per_transition(tmp_path, transition, monkeypatch):
    pytest.importorskip("PIL.Image")
    import render_media

    monkeypatch.setattr(render_media, "ALL_STATES", [])
    monkeypatch.setattr(render_media, "SPOT_VARIANTS", {})
    monkeypatch.setattr(render_media, "MOVES", {})
    render_media.write_anims(tmp_path, 1)
    # one file per TRANSITIONS entry: the synthetic one and the real ones
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        f"anim-{render_media.gif_name(sprites.transition_name(*pair))}" for pair in sprites.TRANSITIONS)
    assert (tmp_path / "anim-sa-to-sb.gif").exists()


@pytest.fixture
def hop(monkeypatch):
    """A play-once move: down-left, then up three and right two, on one grid."""
    monkeypatch.setitem(sprites.MOVES, "hop", sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({}, ms=100, offset=(-1, 1)), sprites.Frame({}, ms=100, offset=(2, -3))],
        loop_from=None))
    return sprites.move_name("hop")


def test_sheet_cell_grows_to_hold_every_offset(tmp_path, hop):
    out = tmp_path / "sheet.png"
    assert render_sheet(out, scale=2, states=[hop]) == [hop]
    # 3 cells wider (-1..2) and 4 taller (-3..1) than the plain 28x26 cell
    assert _png_size(out) == (4 + 2 * (31 * 2 + 4), 4 + (30 * 2 + 2 + 4 + 4) + 4)


def test_sheet_draws_each_frame_at_its_offset(tmp_path, hop):
    out = tmp_path / "sheet.png"
    render_sheet(out, scale=1, states=[hop])
    palette = sprites.resolve_palette("orange", "tabby")
    grid = sprites.get_frames(hop)[0]
    r, c = next((r, c) for r, row in enumerate(grid) for c, ch in enumerate(row) if palette.get(ch))
    color = tuple(int(palette[grid[r][c]][i:i + 2], 16) for i in (1, 3, 5))
    cell_w = 28 + 3
    # canvas origin is (-1, -3) cells from the unmoved sprite
    assert _pixel(out, 4 + 1 + (-1) + c, 4 + 3 + 1 + r) == color
    assert _pixel(out, 4 + cell_w + 4 + 1 + 2 + c, 4 + 3 - 3 + r) == color


def test_state_without_offsets_keeps_the_old_cell(tmp_path, plain):
    out = tmp_path / "sheet.png"
    render_sheet(out, scale=2, states=["plain"])
    assert _png_size(out) == _geometry(2, ["plain"])


def test_gif_canvas_holds_the_offsets(tmp_path, hop):
    PIL = pytest.importorskip("PIL.Image")
    from render_sheet import gif_name, write_gifs

    assert gif_name(hop) == "move-hop.gif"
    paths = write_gifs(tmp_path, [hop], scale=1, seconds=1.0)
    with PIL.open(paths[0]) as gif:
        assert gif.size == (31, 30) and gif.n_frames == 2


def test_cli_accepts_variant_and_move_names(tmp_path, monkeypatch, hop):
    import render_sheet as rs

    monkeypatch.setitem(sprites.SPOT_VARIANTS, ("plain", "on_top"), sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"A": "n"})]))
    monkeypatch.setitem(sprites.ANIMATIONS, "plain", sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"A": "n"})]))
    out = tmp_path / "sheet.png"
    for name in ("plain@on_top", hop):
        monkeypatch.setattr(sys, "argv", ["render_sheet", "--out", str(out), "--state", name])
        assert rs.main() == 0
    monkeypatch.setattr(sys, "argv", ["render_sheet", "--out", str(out), "--state", "plain@inside"])
    with pytest.raises(SystemExit):
        rs.main()


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _scene_pixel(path, spot, scale, x, y):
    """Pixel at card coordinate (x, y) of a one-frame --spot sheet."""
    crop = 172 if spot == "inside_right" else 0
    return _pixel(path, 4 + (x - crop) * scale // 4, 4 + (y + 116) * scale // 4)


@pytest.fixture
def still(monkeypatch):
    """A one-frame sitting cat, so a --spot sheet is a single scene."""
    monkeypatch.setitem(sprites.ANIMATIONS, "still", sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"L": "e", "R": "e", "A": "n"})]))


@pytest.mark.parametrize("spot", ["inside", "inside_right", "on_top"])
def test_spot_sheet_has_the_card_and_a_shadow_under_a_sitting_cat(tmp_path, still, spot):
    from render_sheet import SCENE_BACKDROP, scene_size
    from tokitty.theme import placement

    out = tmp_path / "sheet.png"
    assert render_sheet(out, scale=4, states=["still"], spot=spot) == ["still"]
    w, h = scene_size(4)
    assert (w, h) == (32 * 4, 61 * 4) and _png_size(out)[0] == 4 + w + 4
    card, backdrop = _rgb("#1c1c22"), _rgb(SCENE_BACKDROP)
    side = 296 if spot == "inside_right" else 4  # in the 8 px margin beside the sprite
    # the card body is at its real place: a pixel inside it, one above its top edge
    assert _scene_pixel(out, spot, 4, side, 60) == card
    assert _scene_pixel(out, spot, 4, side, -8) == backdrop
    # the shadow darkens the surface under the paws (columns 8..18 of the grid)
    cols = (27 - 18, 27 - 8) if placement(spot).mirrored else (8, 18)
    x = placement(spot).origin[0] + (cols[0] + 3) * 4
    if spot == "on_top":
        # straddles the top edge: darkened on the card, and on the backdrop
        # at the rim cell beside the paws (the paws cover the rest)
        rim = placement(spot).origin[0] + (cols[0] - 1) * 4
        assert all(a < b for a, b in zip(_scene_pixel(out, spot, 4, rim, -2), backdrop))
        assert all(a < b for a, b in zip(_scene_pixel(out, spot, 4, x, 2), card))
    else:
        assert all(a < b for a, b in zip(_scene_pixel(out, spot, 4, x, 118), card))
    # clear of the shadow the card is untouched
    assert _scene_pixel(out, spot, 4, side, 118) == card


def test_spot_scene_mirrors_the_cat_for_inside_right():
    from render_sheet import scene_frame

    palette = sprites.resolve_palette("orange", "tabby")
    grid = sprites.get_frames("content")[0]
    row = 20
    c = next(c for c in range(28) if palette.get(grid[row][c])
             and palette.get(grid[row][27 - c]) != palette.get(grid[row][c]))
    want = _rgb(palette[grid[row][c]])
    y = 12 + row * 4 + 116
    plain = scene_frame(grid, (0, 0), grid, "inside", palette, 4)
    right = scene_frame(grid, (0, 0), grid, "inside_right", palette, 4)
    assert plain[y][8 + c * 4] == bytes(want)
    # card x 180 + 4 * (27 - c), minus the crop of 172
    assert right[y][180 + (27 - c) * 4 - 172] == bytes(want)
    # dx is negated for the mirrored cat
    moved = scene_frame(grid, (2, 0), grid, "inside_right", palette, 4)
    assert moved[y][180 + (27 - c) * 4 - 172 - 8] == bytes(want)


def test_gif_names_never_carry_the_any_state_star():
    from render_sheet import gif_name

    assert gif_name("*>panicked") == "any-to-panicked.gif"
    assert gif_name("flopped>stirring") == "flopped-to-stirring.gif"


def test_spot_scene_widens_to_keep_a_walking_cat_in_view():
    from render_sheet import SCENE_W, scene_frame, scene_span

    # no offsets: the cat's side of the card, as before
    assert scene_span("inside") == (0, SCENE_W)
    assert scene_span("inside_right") == (300 - SCENE_W, SCENE_W)
    # 30 cells right from on_top (x 8): the sprite ends at 8 + 120 + 112 = 240, plus a cell
    assert scene_span("on_top", [(0, 0), (30, 0)]) == (0, 244)
    # the mirrored cat walks left, so its scene grows to the left
    assert scene_span("inside_right", [(30, 0)]) == (180 - 120 - 4, 300 - (180 - 120 - 4))
    palette = sprites.resolve_palette("orange", "tabby")
    grid = sprites.get_frames("content")[0]
    span = scene_span("on_top", [(30, 0)])
    far = scene_frame(grid, (30, 0), grid, "on_top", palette, 4, span)
    assert len(far[0]) == 244 and any(px != far[0][0] for px in far[60][200:240])


def test_spot_gif_and_variant_resolution(tmp_path, monkeypatch):
    PIL = pytest.importorskip("PIL.Image")
    from render_sheet import resolve_states, write_gifs

    monkeypatch.setitem(sprites.SPOT_VARIANTS, ("content", "on_top"), sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"A": "n"}), sprites.Frame({"A": "z"})]))
    assert resolve_states(["content", "alert"], "on_top") == ["content@on_top", "alert"]
    assert resolve_states(["content"], None) == ["content"]
    paths = write_gifs(tmp_path, ["content@on_top"], scale=2, seconds=1.6, spot="on_top")
    assert [p.name for p in paths] == ["content-at-on_top.gif"]
    with PIL.open(paths[0]) as gif:
        assert gif.size == (32 * 2, 61 * 2) and gif.n_frames == 2


def test_render_media_writes_gifs_for_moves_and_variants(tmp_path, hop, monkeypatch):
    pytest.importorskip("PIL.Image")
    import render_media

    monkeypatch.setattr(sprites, "SPOT_VARIANTS", {("content", "on_top"): sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"A": "n"}), sprites.Frame({"A": "z"})])})
    monkeypatch.setattr(render_media, "SPOT_VARIANTS", sprites.SPOT_VARIANTS)
    monkeypatch.setattr(render_media, "ALL_STATES", [])
    monkeypatch.setattr(sprites, "TRANSITIONS", {})
    monkeypatch.setattr(render_media, "TRANSITIONS", {})
    monkeypatch.setattr(sprites, "MOVES", {"hop": sprites.MOVES["hop"]})
    monkeypatch.setattr(render_media, "MOVES", sprites.MOVES)
    render_media.write_anims(tmp_path, 1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["anim-content-at-on_top.gif", "anim-move-hop.gif"]
