"""Sprite offsets and the ground shadow in the live widget (#116, #137): the
painted cat moves by a frame's offset while the click mask stays home, a
frame change repaints where the cat was too, and the shadow sits under the
cat without ever joining the click area."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QRegion

from tokitty import shadow as shadow_mod
from tokitty import sprites
from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.qt.widget import TokittyQtWidget

pytestmark = pytest.mark.qt

HOP = (1, -2)


class Clock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def hop(monkeypatch):
    """A sitting animation whose second frame moves the sprite and changes
    nothing else, so a tick on it is an offset-only change."""
    frames = [sprites.Frame({"L": "e", "R": "e", "A": "n"}, ms=100),
              sprites.Frame({"L": "e", "R": "e", "A": "n"}, ms=100, offset=HOP)]
    monkeypatch.setitem(sprites.ANIMATIONS, "hop", sprites.single_track(
        sprites.SITTING_TEMPLATE, frames, 0))


def make(home="inside", shadow=True, clock=None, count=1):
    theme = T.Theme(cat=T.CatStyle(shadow=shadow))
    widget = TokittyQtWidget(theme, [Customization(cat_home=home)] * count, count,
                             clock=clock or Clock())
    widget.anim.stop()
    return widget


def play(widget, clock, state="hop", index=0):
    widget.panes[index].render(state, 0, 0, "", "", "", None, None, False)
    widget.anim.stop()


def full_render(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    widget.render(image)
    return image


@pytest.mark.parametrize("home", ["inside", "inside_right", "on_top"])
def test_the_painted_cat_moves_by_the_offset_and_the_mask_stays_home(qapp, hop, home):
    clock = Clock()
    widget = make(home, clock=clock)
    play(widget, clock)
    home_origin = widget.cat_origin(0)
    home_rects = widget.cat_rects()
    assert widget._cat_rect(0, widget.panes[0]).topLeft().toTuple() == home_origin
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    assert widget.panes[0].offset() == HOP
    dx = -HOP[0] if T.placement(home).mirrored else HOP[0]
    x, y = widget._cat_rect(0, widget.panes[0]).topLeft().toTuple()
    assert (x - home_origin[0], y - home_origin[1]) == (dx * sprites.SCALE, HOP[1] * sprites.SCALE)
    assert widget.cat_origin(0) == home_origin and widget.cat_rects() == home_rects


def test_the_cat_is_drawn_at_the_offset_position(qapp, hop):
    clock = Clock()
    widget = make("on_top", shadow=False, clock=clock)
    play(widget, clock)
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    pane = widget.panes[0]
    image = widget._cat_image(pane.grid(), pane.palette, pane.palette_key, 1.0, False)
    # an opaque pixel of the sprite, found in the sprite's own image
    px, py = next((x, y) for y in range(image.height()) for x in range(image.width())
                  if image.pixelColor(x, y).alpha() == 255)
    canvas = full_render(widget)
    ox, oy = widget.cat_origin(0, pane.offset())
    assert canvas.pixelColor(round(ox) + px, round(oy) + py) == image.pixelColor(px, py)
    hx, hy = widget.cat_origin(0)
    assert (ox, oy) != (hx, hy)


def test_tags_stay_at_the_home_position(qapp, hop):
    clock = Clock()
    widget = make(clock=clock)
    widget.panes[0].render("hop", 0, 0, "", "", "work", None, None, False, tool_label="Bash")
    before = widget.tag_boxes(0, "inside")
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    assert widget.panes[0].offset() == HOP
    assert widget.tag_boxes(0, "inside") == before


@pytest.mark.parametrize("home", ["inside", "inside_right", "on_top"])
@pytest.mark.parametrize("shadow", [True, False])
def test_the_dirty_rect_covers_where_the_cat_was_and_where_it_goes(qapp, hop, home, shadow):
    clock = Clock()
    widget = make(home, shadow, clock)
    play(widget, clock)
    full_render(widget)  # paints frame 0, recording what it covered
    old = widget._art_rect(0, widget.panes[0])
    old_cat = widget._cat_rect(0, widget.panes[0])
    rects = []
    widget.update = lambda *a: rects.append(a)
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    assert len(rects) == 1
    dirty = QRectF(rects[0][0])
    new_cat = widget._cat_rect(0, widget.panes[0])
    assert new_cat != old_cat
    assert dirty.contains(old) and dirty.contains(widget._art_rect(0, widget.panes[0]))
    assert dirty.contains(old_cat) and dirty.contains(new_cat)


@pytest.mark.parametrize("home", ["inside", "inside_right", "on_top"])
@pytest.mark.parametrize("shadow", [True, False])
def test_a_partial_repaint_of_a_moved_cat_leaves_no_ghost(qapp, hop, home, shadow):
    clock = Clock()
    widget = make(home, shadow, clock)
    play(widget, clock)
    canvas = full_render(widget)
    rects = []
    widget.update = lambda *a: rects.append(a)
    for now in (100, 200, 300):  # out, back, out again
        clock.now = now
        widget._next_frames()
        widget.anim.stop()
        dirty = rects[-1][0]
        widget.render(canvas, dirty.topLeft(), QRegion(dirty))
        assert canvas == full_render(widget), now


def _shadow_pixel(widget, canvas):
    """A window pixel inside the lower shadow row, below the paws."""
    rect, cells = widget._shadow_placed(0, widget.panes[0])
    row = len(cells) - 1
    col = len(cells[0]) // 2
    x = round(rect.x()) + col * sprites.SCALE + 1
    y = round(rect.y()) + row * sprites.SCALE + 1
    assert cells[row][col] > 0
    return canvas.pixelColor(x, y)


def test_the_shadow_darkens_the_card_under_a_sitting_cat(qapp, hop):
    clock = Clock()
    lit, plain = make(shadow=True, clock=clock), make(shadow=False, clock=clock)
    for widget in (lit, plain):
        play(widget, clock)
    assert plain._shadow_placed(0, plain.panes[0]) is None
    shadowed = _shadow_pixel(lit, full_render(lit))
    bare = full_render(plain).pixelColor(
        round(lit._shadow_placed(0, lit.panes[0])[0].x()) + 2 * sprites.SCALE + 1,
        round(lit._shadow_placed(0, lit.panes[0])[0].bottom()) - sprites.SCALE + 1)
    assert bare.name() == T.CardStyle.background
    assert shadowed.lightness() < bare.lightness()


def test_the_shadow_is_not_part_of_the_click_area(qapp, hop):
    clock = Clock()
    lit, plain = make("on_top", True, clock), make("on_top", False, clock)
    for widget in (lit, plain):
        play(widget, clock)
    for widget in (lit, plain):
        widget._update_mask()
    assert lit.cat_rects() == plain.cat_rects()
    assert lit.mask() == plain.mask()
    assert lit._shadow_placed(0, lit.panes[0]) is not None


def test_shadow_images_are_cached_and_dropped_with_the_cat_images(qapp, hop):
    clock = Clock()
    widget = make(clock=clock)
    play(widget, clock)
    full_render(widget)
    assert len(widget._shadow_images) == 1
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    full_render(widget)
    assert len(widget._shadow_images) == 2  # the lifted frame has its own cells
    full_render(widget)
    assert len(widget._shadow_images) == 2
    widget._drop_images()
    assert widget._shadow_images == {}


def test_the_shadow_switch_follows_set_theme(qapp, hop):
    clock = Clock()
    widget = make(clock=clock)
    play(widget, clock)
    assert widget._shadow_placed(0, widget.panes[0]) is not None
    widget.set_theme(replace(widget.theme, cat=T.CatStyle(shadow=False)))
    assert widget._shadow_placed(0, widget.panes[0]) is None
    assert widget._shadow_images == {}


def test_shadow_uses_the_pane_inputs(qapp, hop, monkeypatch):
    seen = []
    real = shadow_mod.shadow_for
    monkeypatch.setattr(shadow_mod, "shadow_for", lambda *a: (seen.append(a), real(*a))[1])
    clock = Clock()
    widget = make("inside_right", clock=clock)
    play(widget, clock)
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    widget._shadow_placed(0, widget.panes[0])
    rest, grid, offset, home, mirrored = seen[-1]
    assert rest is sprites.get_frames("hop")[0] and grid is widget.panes[0].grid()
    assert (offset, home, mirrored) == (HOP, "inside_right", True)


def test_the_shadow_is_worked_out_once_per_frame(qapp, hop, monkeypatch):
    calls = []
    real = shadow_mod.shadow_for
    monkeypatch.setattr(shadow_mod, "shadow_for", lambda *a: calls.append(a) or real(*a))
    clock = Clock()
    widget = make(clock=clock)
    play(widget, clock)
    full_render(widget)
    first = widget._shadow_placed(0, widget.panes[0])
    assert len(calls) == 1
    # the next frame moves the cat, so the shadow is worked out again, once
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    full_render(widget)
    assert len(calls) == 2 and widget._shadow_placed(0, widget.panes[0]) != first
