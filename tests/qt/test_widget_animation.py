"""The widget's frame timer (#125): one single-shot precise timer armed for the
earliest due frame, and a frame advance repaints only the cat."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPixmap, QRegion

from tokitty import sprites
from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.qt.widget import TokittyQtWidget

pytestmark = pytest.mark.qt


class Clock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def synth(monkeypatch):
    for name, ms in (("slow", [800, 800]), ("fast", [100, 100, 100]), ("once", [100, 100])):
        anim = sprites.single_track(
            sprites.SITTING_TEMPLATE,
            [sprites.Frame({"L": "e", "R": "e", "A": c}, ms=m) for c, m in zip("nz!", ms)],
            None if name == "once" else 0,
        )
        monkeypatch.setitem(sprites.ANIMATIONS, name, anim)


def make(count=1, clock=None, homes=None, **custom):
    customs = [Customization(cat_home=(homes[i] if homes else "inside"), **custom)
               for i in range(count)]
    return TokittyQtWidget(T.Theme(), customs, count, clock=clock or Clock())


def render(pane, state):
    pane.render(state, 42, 67, "2h", "Mon", "", None, None, False)


def test_timer_is_single_shot_and_precise(qapp):
    widget = make()
    assert widget.anim.isSingleShot()
    assert widget.anim.timerType() == Qt.TimerType.PreciseTimer
    widget.anim.stop()


def test_timer_arms_for_the_earliest_due_frame(qapp, synth):
    clock = Clock(1000)
    widget = make(2, clock)
    render(widget.panes[0], "slow")
    render(widget.panes[1], "fast")
    assert widget.anim.isActive() and widget.anim.interval() == 100
    clock.now = 1040
    render(widget.panes[0], "slow")  # restarts pane 0 only, but the earliest is still pane 1
    assert widget.anim.interval() == 60
    widget.anim.stop()


def test_timer_is_not_armed_when_nothing_is_due(qapp, synth):
    widget = make()
    render(widget.panes[0], "once")
    assert widget.anim.isActive()
    widget.panes[0].tick(10_000)
    widget._arm()
    assert widget.panes[0].next_due_ms() is None and not widget.anim.isActive()


def test_frame_advance_repaints_only_the_cat(qapp, synth):
    clock = Clock()
    widget = make(2, clock, homes=["inside", "inside"])
    widget.anim.stop()
    render(widget.panes[0], "fast")
    render(widget.panes[1], "slow")
    rects = []
    widget.update = lambda *a: rects.append(a)
    clock.now = 100
    widget._next_frames()
    assert widget.panes[0].current_frame()[1] == 1 and widget.panes[1].current_frame()[1] == 0
    assert len(rects) == 1 and len(rects[0]) == 1
    dirty = rects[0][0]
    assert isinstance(dirty, QRect)
    cat = QRect(*(round(v) for v in (*widget.cat_origin(0), T.SPRITE_W, T.SPRITE_H)))
    assert dirty.contains(cat)
    assert dirty.width() < T.CARD_WIDTH // 2 and dirty.height() < T.CARD_HEIGHT
    # re-armed for the next frame
    assert widget.anim.interval() == 0 or widget.anim.interval() <= 100
    widget.anim.stop()


def test_tag_overlay_is_inside_the_dirty_rect(qapp, synth):
    clock = Clock()
    widget = make(1, clock)
    widget.anim.stop()
    widget.panes[0].render("fast", 0, 0, "", "", "work", None, None, False, tool_label="Bash")
    driving, tool, _ = widget.tag_boxes(0, "inside")
    dirty = widget._cat_dirty_rect(0, widget.panes[0])
    assert dirty.contains(driving.toRect()) and dirty.contains(tool.toRect())


@pytest.mark.parametrize("home", ["inside", "on_top"])
def test_partial_repaint_matches_a_full_one(qapp, synth, home):
    """Paint only the dirty rect over the previous frame, as the window does
    after update(rect), and get exactly what a full repaint of the next frame
    gives."""
    clock = Clock()
    widget = make(1, clock, homes=[home])
    widget.anim.stop()
    render(widget.panes[0], "fast")
    canvas = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    widget.render(canvas)
    before = canvas.copy()
    rects = []
    widget.update = lambda *a: rects.append(a)
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    assert len(rects) == 1
    # render() places the region's top-left at targetOffset.
    widget.render(canvas, rects[0][0].topLeft(), QRegion(rects[0][0]))
    full = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    full.fill(0)
    widget.render(full)
    assert canvas == full
    assert canvas != before


def test_paint_skips_items_outside_the_dirty_rect(qapp, synth):
    widget = make(2, homes=["inside", "inside"])
    widget.anim.stop()
    for pane in widget.panes:
        render(pane, "fast")
    painted = {"cards": 0, "cats": 0}
    orig_card, orig_cat = widget._paint_card_contents, widget._paint_cat

    def card(*a, **k):
        painted["cards"] += 1
        return orig_card(*a, **k)

    def cat(*a, **k):
        painted["cats"] += 1
        return orig_cat(*a, **k)

    widget._paint_card_contents, widget._paint_cat = card, cat
    target = QPixmap(widget.size())
    widget.render(target)
    assert painted == {"cards": 2, "cats": 2}
    painted.update(cards=0, cats=0)
    x, y = widget.cat_origin(1)
    widget.render(target, QPoint(), QRegion(QRect(round(x), round(y), 4, 4)))
    assert painted == {"cards": 1, "cats": 1}


def test_images_are_shared_by_grid_and_pruned_with_the_state(qapp, synth):
    widget = make()
    widget.anim.stop()
    render(widget.panes[0], "fast")
    widget.grab()
    keys = list(widget._images)
    assert len(keys) == 1 and keys[0][0] == id(sprites.get_frames("fast")[0])
    widget.panes[0].draw_next_frame()
    widget.grab()
    assert len(widget._images) == 2
    # Equal grids are one object across states, so move to a state whose art
    # shares nothing with "fast".
    render(widget.panes[0], "sleeping")
    assert widget._images == {}  # "fast" images are no longer shown by any pane
    widget.anim.stop()


def test_drop_images_clears_everything(qapp):
    widget = make()
    widget.anim.stop()
    widget.grab()
    assert widget._images
    widget._drop_images()
    assert widget._images == {}
