"""Sprite offsets and the ground shadow in the live widget (#116, #137): the
painted cat moves by a frame's offset inside a click mask that holds every
offset of the animation, a
frame change repaints where the cat was too, and the shadow sits under the
cat without ever joining the click area."""
import random
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QRegion

from tokitty import shadow as shadow_mod
from tokitty import sprites
from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.qt.widget import TokittyQtWidget
from tokitty.wander import Pose

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


def make(home="inside", shadow=True, clock=None, count=1, wander=False):
    theme = T.Theme(cat=T.CatStyle(shadow=shadow, wander=wander))
    widget = TokittyQtWidget(theme, [Customization(cat_home=home)] * count, count,
                             clock=clock or Clock())
    widget.anim.stop()
    return widget


def play(widget, clock, state="hop", index=0):
    widget.panes[index].render(state, 0, 0, "", "", "", None, None, False)
    widget.anim.stop()


def _unmasked(widget, draw):
    """render() shifts a region's output when the widget has a mask (by an
    amount that depends on the mask), so paint with it lifted. The mask
    itself is checked by test_the_mask_holds_the_cat_through_a_whole_trip."""
    mask = widget.mask()
    widget.clearMask()
    try:
        draw()
    finally:
        widget.setMask(mask)


def render_part(widget, canvas, rect):
    """Repaint `rect` into canvas, as a partial update would."""
    _unmasked(widget, lambda: widget.render(canvas, rect.topLeft(), QRegion(rect)))


def full_render(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    _unmasked(widget, lambda: widget.render(image))
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
        render_part(widget, canvas, dirty)
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
    monkeypatch.setattr(shadow_mod, "shadow_for", lambda *a, **kw: (seen.append(a), real(*a, **kw))[1])
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
    monkeypatch.setattr(shadow_mod, "shadow_for", lambda *a, **kw: calls.append(a) or real(*a, **kw))
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


# ---- wandering (#22) --------------------------------------------------------

def drive(widget, clock, stop, index=0, limit=600):
    """Tick the widget at every due time until stop(pane) holds."""
    pane = widget.panes[index]
    widget.panes[index].render("content", 0, 0, "", "", "", None, None, False)
    for _ in range(limit):
        if stop(pane):
            return pane
        clock.now = max(clock.now, pane.next_due_ms())
        widget._next_frames()
        widget.anim.stop()
    raise AssertionError("the cat never got there")


def away(pane):
    return pane.pose.spot == "on_top" and pane.pose.x != pane._roam.wanderer.home.x


def test_wander_gives_an_inside_cat_the_headroom_for_its_jump(qapp):
    on, off = make(wander=True), make(wander=False)
    assert on.window_layout.margins[1] == 116 and off.window_layout.margins[1] == 0
    assert on.height() == off.height() + 116


def test_only_first_row_panes_roam(qapp):
    widget = make(wander=True, count=5)  # two columns, three rows
    assert [pane._roams for pane in widget.panes] == [True, True, False, False, False]
    assert not any(pane._roams for pane in make(wander=False, count=5).panes)


def test_set_theme_toggles_the_headroom_and_keeps_the_cards_still(qapp):
    widget = make(wander=False)
    widget.move(100, 300)
    card = widget.first_card_pos()
    widget.set_theme(replace(widget.theme, cat=T.CatStyle(wander=True)))
    assert widget.window_layout.margins[1] == 116 and widget.panes[0]._roams
    assert widget.first_card_pos() == card
    widget.set_theme(replace(widget.theme, cat=T.CatStyle(wander=False)))
    assert widget.window_layout.margins[1] == 0 and not widget.panes[0]._roams
    assert widget.first_card_pos() == card


def test_set_theme_flips_roaming_even_when_the_layout_stays(qapp):
    widget = make("on_top", wander=False)
    size = widget.size()
    widget.set_theme(replace(widget.theme, cat=T.CatStyle(wander=True)))
    assert widget.size() == size and widget.panes[0]._roams


def test_cat_origin_follows_the_pose_and_its_mirroring(qapp):
    widget = make(wander=True)
    wanderer = widget.panes[0]._roam.wanderer
    home = wanderer.home
    ox, oy = widget.window_layout.pane_origins[0]
    assert widget.cat_origin(0) == (ox + home.x, oy + T.placement("inside").origin[1])
    wanderer.pose = Pose("on_top", home.x + 3 * sprites.SCALE, True)
    assert widget.cat_origin(0) == (ox + home.x + 3 * sprites.SCALE,
                                    oy + T.placement("on_top").origin[1])
    # a mirrored cat's frame offset runs the other way
    dx, dy = widget.cat_origin(0, (2, -1))
    assert (dx, dy) == (ox + home.x + 3 * sprites.SCALE - 2 * sprites.SCALE,
                        oy + T.placement("on_top").origin[1] - sprites.SCALE)
    wanderer.pose = replace(wanderer.pose, mirrored=False)
    assert widget.cat_origin(0, (2, 0))[0] == ox + home.x + 3 * sprites.SCALE + 2 * sprites.SCALE


def test_the_walking_cat_is_drawn_mirrored_as_it_faces(qapp):
    widget = make(wander=True)
    wanderer = widget.panes[0]._roam.wanderer
    wanderer.pose = Pose("on_top", wanderer.home.x, True)
    pane = widget.panes[0]
    mirrored = widget._cat_image(pane.grid(), pane.palette, pane.palette_key, 1.0, True)
    plain = widget._cat_image(pane.grid(), pane.palette, pane.palette_key, 1.0, False)
    canvas = full_render(widget)
    x, y = widget.cat_origin(0)
    px, py = next((px, py) for py in range(mirrored.height()) for px in range(mirrored.width())
                  if mirrored.pixelColor(px, py).alpha() == 255
                  and mirrored.pixelColor(px, py) != plain.pixelColor(px, py))
    assert canvas.pixelColor(round(x) + px, round(y) + py) == mirrored.pixelColor(px, py)


def test_the_mask_holds_the_cat_through_a_whole_trip(qapp):
    # The mask clips painting on Windows: any part of the cat outside it vanishes.
    clock = Clock()
    widget = make(wander=True, clock=clock)
    pane = drive(widget, clock, lambda p: True)
    widget._update_mask()
    spots, moves = set(), 0
    for _ in range(600):
        clock.now = pane.next_due_ms()
        widget._next_frames()
        widget.anim.stop()
        cat = widget._cat_rect(0, pane).toAlignedRect()
        assert QRegion(cat).subtracted(widget.mask()).isEmpty(), (pane.playing, pane.pose)
        spots.add((pane.pose.spot, pane.pose.x))
        moves += pane.playing.startswith(sprites.MOVE_PREFIX)
    assert len(spots) > 2 and moves


def test_the_mask_is_rebuilt_only_when_a_clickable_place_moved(qapp):
    clock = Clock()
    widget = make(wander=True, clock=clock)
    calls = []
    real = widget._update_mask
    widget._update_mask = lambda: (calls.append(1), real())[1]
    drive(widget, clock, lambda p: p.playing.startswith(sprites.MOVE_PREFIX))
    assert len(calls) <= 1  # the move that just started
    ticks = 0
    for _ in range(40):  # frames of the move and of the walk that follows
        clock.now = widget.panes[0].next_due_ms()
        before = len(calls)
        widget._next_frames()
        widget.anim.stop()
        ticks += 1
        assert len(calls) - before <= 1
    assert len(calls) < ticks


# Fixed walks, including paths that sit and loaf between strides.
@pytest.mark.parametrize("seed", [0, 12, 17, 23])
def test_a_walking_cat_leaves_no_ghost_and_its_shadow_follows(qapp, monkeypatch, seed):
    from tokitty.qt import pane as pane_mod

    monkeypatch.setattr(pane_mod, "random", SimpleNamespace(Random=lambda: random.Random(seed)))
    clock = Clock()
    widget = make(wander=True, clock=clock)
    pane = widget.panes[0]
    drive(widget, clock, lambda p: p.pose.spot == "on_top")
    full_render(widget)
    canvas = full_render(widget)
    rects = []
    widget.update = lambda *a: rects.append(a)
    xs, moved = set(), 0
    for _ in range(600):
        clock.now = pane.next_due_ms()
        before = widget._cat_rect(0, pane)
        old_art = widget._art_rect(0, pane)
        rects.clear()
        widget._next_frames()
        widget.anim.stop()
        if widget._cat_rect(0, pane) != before:
            moved += 1
            dirty = QRectF(rects[-1][0])
            assert dirty.contains(old_art) and dirty.contains(widget._art_rect(0, pane))
        if rects:
            render_part(widget, canvas, rects[-1][0])
            assert canvas == full_render(widget)
        xs.add(pane.pose.x)
        # The shadow stays under the cat as it walks. Its offset from pose.x
        # changes with each stride frame and with the pose's width, so measure
        # it from the cat's rect (at most 10 px over 200 seeded walks).
        placed = widget._shadow_placed(0, pane)
        if placed is not None and pane.pose.spot == "on_top":
            cat = widget._cat_rect(0, pane)
            assert abs(placed[0].center().x() - cat.center().x()) <= 4 * sprites.SCALE
        if len(xs) > 3:
            break
    assert moved and len(xs) > 3


def test_the_shadow_passes_the_pose_on(qapp, monkeypatch):
    seen = []
    real = shadow_mod.shadow_for
    monkeypatch.setattr(shadow_mod, "shadow_for", lambda *a, **kw: (seen.append((a, kw)), real(*a, **kw))[1])
    widget = make(wander=True)
    wanderer = widget.panes[0]._roam.wanderer
    wanderer.pose = Pose("on_top", wanderer.home.x + 4 * sprites.SCALE, True)
    first = widget._shadow_placed(0, widget.panes[0])
    args, kw = seen[-1]
    assert args[3:5] == ("on_top", True) and kw == {"x": wanderer.pose.x}
    wanderer.pose = replace(wanderer.pose, x=wanderer.pose.x + 4 * sprites.SCALE)
    second = widget._shadow_placed(0, widget.panes[0])
    assert second[0].x() - first[0].x() == 4 * sprites.SCALE
