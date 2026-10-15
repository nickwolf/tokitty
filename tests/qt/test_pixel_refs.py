"""Pixel-exact reference images for the Qt widget (#128).

The widget's real paint output, taken with grab(), against PNGs in
tests/qt/pixel_refs/. dpr 1 renders in this process; dpr 2 renders in one
child process with QT_SCALE_FACTOR=2 (see pixel_check.py). This module never
writes a reference: `python tests/qt/pixel_check.py update` does.
"""
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QRectF

import pixel_check as pc
from tokitty import sprites
from tokitty import theme as T

pytestmark = pytest.mark.qt

ROOT = Path(__file__).resolve().parents[2]
MAC = sys.platform == "darwin"


@pytest.fixture(scope="module")
def dpr2(qapp, tmp_path_factory):
    rendered, meta = pc.render_child(2, tmp_path_factory.mktemp("dpr2"))
    return rendered, meta


def check(name, rendered):
    path = pc.ref_path(name)
    assert path.exists(), f"no reference {path}; run: python tests/qt/pixel_check.py update {name}"
    dpr = pc.split_name(name)[2]
    assert dpr is None or rendered.dpr == dpr, f"{name} was rendered at dpr {rendered.dpr}"
    ref = pc.read_png(path)
    masked = rendered.always_mask or (MAC and name.startswith("card/"))
    rects = []
    if masked:
        # Where the text was when the reference was made, and where it is now:
        # a font that sets it narrower still leaves the reference glyphs behind.
        stored = pc.load_masks().get(name)
        assert stored is not None, f"no text regions stored for {name}; run: python tests/qt/pixel_check.py update {name}"
        rects = sorted(set(stored) | set(rendered.rects))
    diff = pc.compare(rendered.img, ref, rects)
    if diff is None:
        return
    out = pc.diff_dir()
    pc.write_compare(out / f"{pc.flat(name)}.compare.png", ref, rendered.img, diff, rects)
    pc.write_png(out / f"{pc.flat(name)}.actual.png", rendered.img)
    pc.write_png(out / f"{pc.flat(name)}.reference.png", ref)
    first = ", ".join(f"({x},{y}) got {g.hex()} want {w.hex()}" for x, y, g, w in diff.first)
    note = f"; {len(rects)} text regions masked" if rects else ""
    pytest.fail(f"{name}: {diff.summary()}; first {first or 'n/a'}{note}; images in {out}")


@pytest.mark.parametrize("state", sprites.ALL_STATES)
def test_cat_sheet_dpr1(qapp, state):
    check(f"cat/{state}@1x", pc.cat_sheet(state))


@pytest.mark.parametrize("name", pc.LAYOUTS)
def test_layout_dpr1(qapp, name):
    check(f"card/{name}@1x", pc.layout(name))


@pytest.mark.parametrize("state", sprites.ALL_STATES)
def test_cat_sheet_dpr2(dpr2, state):
    check(f"cat/{state}@2x", dpr2[0][f"cat/{state}@2x"])


@pytest.mark.parametrize("name", pc.LAYOUTS)
def test_layout_dpr2(dpr2, name):
    check(f"card/{name}@2x", dpr2[0][f"card/{name}@2x"])


def test_dpr2_child_used_this_checkout(dpr2):
    meta = dpr2[1]
    assert meta["dpr"] == 2.0
    assert Path(meta["tokitty_file"]).is_relative_to(ROOT)


@pytest.mark.skipif(MAC, reason="macOS draws text through CoreText, so the keys (mostly text) differ from the Linux and Windows references; the cat on the session key is covered by the cat sheets")
@pytest.mark.parametrize("name", list(pc.KEYS))
def test_stream_dock_key(qapp, name):
    check(f"keys/{name}", pc.key(name))


def test_every_reference_is_used_and_present():
    wanted = {pc.ref_path(n) for n in pc.all_names()}
    assert set(pc.REFS.rglob("*.png")) == wanted
    cards = {n for n in pc.all_names() if n.startswith("card/")}
    assert set(pc.load_masks()) == cards
    assert set(pc.REFS.rglob("*.json")) == {pc.MASKS_FILE}


@pytest.mark.parametrize("name", ["default", "models"])
def test_product_fonts_fit_the_card(qapp, name):
    """With the shipped fonts (not the pixel-test font) every text sits inside
    the card and the status line wraps to at most two lines."""
    widget = pc.layout_widget(name, pc.RecordingWidget)
    try:
        widget.grab()
        ox, oy = widget.window_layout.pane_origins[0]
        card = QRectF(ox, oy, T.CARD_WIDTH, T.CARD_HEIGHT)
        pane = widget.panes[0]
        status = pane.status_text if name == "default" else pane.usage_view.status_text
        assert widget.text_log
        seen = False
        for text, rect, spacing in widget.text_log:
            assert card.contains(rect), f"{text!r} at {rect} leaves the card {card}"
            if text == status:
                seen = True
                assert rect.height() <= 2 * spacing, f"status {text!r} wraps to more than two lines"
        assert seen
    finally:
        pc.destroy(widget)


def test_compare_masks_only_the_given_rects():
    a = pc.Img(4, 2, bytes(range(32)))
    flipped = bytearray(a.data)
    flipped[0] ^= 0xFF  # pixel (0, 0)
    b = pc.Img(4, 2, bytes(flipped))
    diff = pc.compare(b, a)
    assert (diff.count, diff.bbox) == (1, (0, 0, 0, 0))
    assert pc.compare(b, a, [(0, 0, 1, 1)]) is None
    assert pc.compare(b, a, [(1, 0, 4, 2)]).count == 1
