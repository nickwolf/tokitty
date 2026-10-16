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
    orig_card, orig_cat = widget._draw_card, widget._paint_cat

    def card(*a, **k):
        painted["cards"] += 1
        return orig_card(*a, **k)

    def cat(*a, **k):
        painted["cats"] += 1
        return orig_cat(*a, **k)

    widget._draw_card, widget._paint_cat = card, cat
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


def test_pruning_keeps_the_grids_of_a_playing_transition(qapp, synth, monkeypatch):
    monkeypatch.setitem(sprites.TRANSITIONS, ("slow", "fast"), sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"L": "k", "A": c}, ms=100) for c in "nz"], None))
    widget = make()
    widget.anim.stop()
    pane = widget.panes[0]
    render(pane, "slow")
    render(pane, "fast")
    assert pane.playing == "slow>fast"
    transition_grid = sprites.get_frames("slow>fast")[1]
    from tokitty.qt.widget import palette_signature
    widget._images[(id(transition_grid), palette_signature(pane.palette))] = "kept"
    stale = object()  # a grid no state owns
    widget._images[(id(stale), palette_signature(pane.palette))] = "gone"
    widget._prune_images()
    assert list(widget._images.values()) == ["kept"]
    widget.anim.stop()


def test_timer_wakes_for_the_end_of_a_transition(qapp, synth, monkeypatch):
    monkeypatch.setitem(sprites.TRANSITIONS, ("slow", "fast"), sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"L": "k"}, ms=300)], None))
    clock = Clock()
    widget = make(1, clock)
    render(widget.panes[0], "slow")
    render(widget.panes[0], "fast")
    assert widget.panes[0].next_due_ms() == 300
    assert widget.anim.isActive() and widget.anim.interval() == 300
    clock.now = 300
    widget._next_frames()
    assert widget.panes[0].playing == "fast" and widget.panes[0].next_due_ms() == 400
    widget.anim.stop()


def test_card_is_drawn_once_and_blitted_on_cat_ticks(qapp, synth):
    clock = Clock()
    widget = make(2, clock, homes=["inside", "inside"])
    widget.anim.stop()
    for pane in widget.panes:
        render(pane, "fast")
    builds = []
    orig = widget._paint_card_contents
    widget._paint_card_contents = lambda *a, **k: (builds.append(1), orig(*a, **k))[1]
    widget.grab()
    assert len(builds) == 2
    # A cat frame tick, a repaint and a poll that re-renders the same values
    # all reuse the cached cards.
    clock.now = 100
    widget._next_frames()
    widget.anim.stop()
    widget.grab()
    render(widget.panes[0], "fast")
    widget.grab()
    assert len(builds) == 2


def test_card_is_redrawn_when_its_inputs_change(qapp, synth):
    widget = make(2, homes=["inside", "inside"])
    widget.anim.stop()
    for pane in widget.panes:
        render(pane, "fast")
    first = widget.grab().toImage()
    pane = widget.panes[0]
    pane.render("fast", 90, 67, "2h", "Mon", "", None, None, False)
    widget.anim.stop()
    second = widget.grab().toImage()
    assert second != first
    # only pane 0's card changed
    ox, oy = widget.window_layout.pane_origins[1]
    rect = QRect(ox, oy, T.CARD_WIDTH, T.CARD_HEIGHT)
    assert second.copy(rect) == first.copy(rect)
    # a theme swap and an accent flip repaint with the new values too
    pane.render("fast", 90, 67, "2h", "Mon", "", None, None, False, accent=True)
    widget.anim.stop()
    accent = widget.grab().toImage()
    assert accent != second
    pane.set_appearance(card_bg="#123456")
    pane.render("fast", 90, 67, "2h", "Mon", "", None, None, False)
    widget.anim.stop()
    assert widget.grab().toImage() != second


def test_cached_card_matches_a_fresh_widget(qapp, synth):
    """A widget that has painted other values first ends up pixel-identical to
    one that only ever painted the final values."""
    old = make(1)
    old.anim.stop()
    render(old.panes[0], "fast")
    old.grab()
    old.panes[0].render("fast", 10, 20, "5h", "Tue", "", None, "hint", True)
    old.anim.stop()
    fresh = make(1)
    fresh.anim.stop()
    fresh.panes[0].render("fast", 10, 20, "5h", "Tue", "", None, "hint", True)
    fresh.anim.stop()
    assert old.grab().toImage() == fresh.grab().toImage()


def test_due_frames_within_the_window_tick_together(qapp, synth):
    clock = Clock()
    widget = make(2, clock, homes=["inside", "inside"])
    widget.anim.stop()
    render(widget.panes[0], "fast")
    clock.now = 4  # pane 1 starts 4 ms later, so its first frame is due 4 ms later
    render(widget.panes[1], "fast")
    widget.anim.stop()
    rects = []
    widget.update = lambda *a: rects.append(a)
    clock.now = 100
    widget._next_frames()
    assert len(rects) == 2  # both panes advanced in the one wakeup
    assert [p.current_frame()[1] for p in widget.panes] == [1, 1]
    # their next frames stay on the original schedule, not shifted by the window
    assert widget.panes[0].next_due_ms() == 200 and widget.panes[1].next_due_ms() == 204
    widget.anim.stop()


def test_a_frame_far_from_due_is_not_ticked_early(qapp, synth):
    clock = Clock()
    widget = make(1, clock)
    widget.anim.stop()
    render(widget.panes[0], "fast")
    clock.now = 80
    widget._next_frames()
    assert widget.panes[0].current_frame()[1] == 0
    widget.anim.stop()
