"""Pixel-exact reference images for the Qt widget (#128).

The widget's real paint output, taken with grab(), against PNGs in
tests/qt/pixel_refs/. dpr 1 renders in this process; dpr 2 renders in one
child process with QT_SCALE_FACTOR=2 (see pixel_check.py). This module never
writes a reference: `python tests/qt/pixel_check.py update` does.
"""
import sys
from pathlib import Path

import pytest

import pixel_check as pc
from tokitty import sprites

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
    assert rendered.dpr == pc.split_name(name)[2], f"{name} was rendered at dpr {rendered.dpr}"
    ref = pc.read_png(path)
    masked = rendered.always_mask or (MAC and name.startswith("card/"))
    rects = rendered.rects if masked else []
    diff = pc.compare(rendered.img, ref, rects)
    if diff is None:
        return
    out = pc.diff_dir()
    pc.write_compare(out / f"{pc.flat(name)}.compare.png", ref, rendered.img, diff)
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


def test_every_reference_is_used_and_present():
    wanted = {pc.ref_path(n) for n in pc.all_names()}
    assert set(pc.REFS.rglob("*.png")) == wanted


def test_compare_masks_only_the_given_rects():
    a = pc.Img(4, 2, bytes(range(32)))
    flipped = bytearray(a.data)
    flipped[0] ^= 0xFF  # pixel (0, 0)
    b = pc.Img(4, 2, bytes(flipped))
    diff = pc.compare(b, a)
    assert (diff.count, diff.bbox) == (1, (0, 0, 0, 0))
    assert pc.compare(b, a, [(0, 0, 1, 1)]) is None
    assert pc.compare(b, a, [(1, 0, 4, 2)]).count == 1
