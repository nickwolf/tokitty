"""The hover popup (#130): which target a point is over, when the popup shows
and hides, that it never activates, and where it lands."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tokitty import theme as T
from tokitty.burn import PACE_WONT_CAP, Pace
from tokitty.customize import Customization
from tokitty.hover import LimitHover, LimitsHover
from tokitty.qt.hover import place_popup
from tokitty.qt.widget import TokittyQtWidget
from tokitty.usage_display import UsageRow, UsageView, RowDetail

pytestmark = pytest.mark.qt

STILL = T.CatStyle(wander=False)
CLASSIC = T.Theme(card=T.CardStyle(design="classic"), cat=STILL)
NOW = datetime.now(timezone.utc)


def make(qapp, theme=None, homes=("inside",)):
    customs = [Customization(cat_home=h) for h in homes]
    widget = TokittyQtWidget(theme or T.Theme(cat=STILL), customs, len(customs))
    widget.anim.stop()
    widget.hover_delay_ms = 20
    return widget


def hover(session=42.0, weekly=81.0):
    def row(title, kind, pct, left):
        return LimitHover(title, kind, pct, NOW + left, False, Pace(PACE_WONT_CAP))
    return LimitsHover(row("Session", "session", session, timedelta(hours=2, minutes=14)),
                       row("Week", "weekly", weekly, timedelta(days=2, hours=3)))


def render(pane, **kw):
    pane.render("content", 42, 81, "6:10 PM", "Mon", "", None, None, False, hover=hover(), **kw)


def detail(label="Opus 4.6"):
    return RowDetail(label, 1, 12.4, False, 120_000, 45_000, 1_800_000, 90_000, 2_100_000, 62.0)


def usage_view(count=2):
    rows = tuple(UsageRow(f"m{i}", "$1.00", 50.0, detail(f"M{i}")) for i in range(count))
    return UsageView(rows=rows, status_text="7d", ramp_pct=None, window_text="7d")


def row_point(widget, name, index=0, view="limits"):
    """A window point inside a limits row, or a models row's bar."""
    ox, oy = widget.window_layout.pane_origins[index]
    layout = widget._layout_for(view, widget.homes[index])
    r = layout.lines[name] if name in layout.lines else layout[name]
    return ox + r.x + r.w - 4, oy + r.y + r.h // 2


def opaque_point(widget, index=0, within=None):
    """A window point on a pixel the cat draws, and one on a transparent pixel of its frame."""
    pane = widget.panes[index]
    rect = widget._cat_rect(index, pane)
    image = widget._cat_image(pane.grid(), pane.palette, pane.palette_key,
                              widget.devicePixelRatioF(), pane.pose.mirrored)
    dpr = widget.devicePixelRatioF()
    solid = clear = None
    for py in range(image.height()):
        for px in range(image.width()):
            point = (rect.x() + (px + 0.5) / dpr, rect.y() + (py + 0.5) / dpr)
            if within is not None and not (within[0] <= point[0] < within[2]
                                           and within[1] <= point[1] < within[3]):
                continue
            if image.pixelColor(px, py).alpha() > 0:
                solid = solid or point
            else:
                clear = clear or point
    return solid, clear


def wait_for(done, ms=3000):
    deadline = time.monotonic() + ms / 1000
    while not done() and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(5)
    return done()


# ---- placement (pure) ------------------------------------------------------

AREA = (0, 0, 1000, 800)


def test_popup_goes_right_of_the_target_level_with_its_top():
    assert place_popup((100, 50), (200, 300, 300, 20), AREA) == (508, 300)


def test_popup_flips_left_when_the_right_would_leave_the_screen():
    assert place_popup((100, 50), (650, 300, 300, 20), AREA) == (542, 300)


def test_popup_goes_above_when_neither_side_fits():
    x, y = place_popup((300, 50), (100, 300, 500, 20), (0, 0, 700, 800))
    assert y == 300 - 8 - 50 and 0 <= x <= 400


def test_popup_goes_below_when_there_is_no_room_above_either():
    x, y = place_popup((300, 50), (100, 10, 500, 20), (0, 0, 700, 800))
    assert y == 10 + 20 + 8


def test_popup_is_clamped_to_the_area_vertically():
    assert place_popup((100, 50), (200, 790, 300, 20), AREA)[1] == 750
    assert place_popup((100, 50), (200, -30, 300, 20), AREA)[1] == 0


# ---- hit testing -----------------------------------------------------------

@pytest.mark.parametrize("theme", [T.Theme(cat=STILL), CLASSIC], ids=["refined", "classic"])
@pytest.mark.parametrize("home", ["inside", "inside_right", "on_top"])
def test_limit_rows_are_targets(qapp, theme, home):
    widget = make(qapp, theme, homes=(home,))
    render(widget.panes[0])
    assert widget.hover_target_at(*row_point(widget, "session")) == (0, "limits", "session")
    assert widget.hover_target_at(*row_point(widget, "weekly")) == (0, "limits", "weekly")


def test_the_whole_refined_block_is_the_target_and_the_gap_between_is_not(qapp):
    widget = make(qapp)
    render(widget.panes[0])
    ox, oy = widget.window_layout.pane_origins[0]
    layout = widget._layout_for("limits", "inside")
    for name in ("label", "bar", "reset"):
        r = layout[f"session_{name}"]
        assert widget.hover_target_at(ox + r.x + 5, oy + r.y + r.h // 2) == (0, "limits", "session")
    first, second = layout.lines["session"], layout.lines["weekly"]
    gap = (first.bottom + second.y) / 2
    if second.y > first.bottom:
        assert widget.hover_target_at(ox + first.x + 5, oy + gap) is None


def test_the_classic_block_spans_label_bar_and_percent_line(qapp):
    widget = make(qapp, CLASSIC)
    render(widget.panes[0])
    ox, oy = widget.window_layout.pane_origins[0]
    layout = widget._layout_for("limits", "inside")
    for name in ("session_label", "session_bar", "session_percent"):
        r = layout[name]
        assert widget.hover_target_at(ox + r.x + 5, oy + r.y + r.h // 2) == (0, "limits", "session")


def test_points_outside_every_card_are_no_target(qapp):
    widget = make(qapp, homes=("inside", "inside"))
    for pane in widget.panes:
        render(pane)
    assert widget.hover_target_at(-5, -5) is None
    ox, oy = widget.window_layout.pane_origins[1]
    assert widget.hover_target_at(ox + T.CARD_WIDTH + 5, oy + 20) is None


def test_the_second_pane_is_reported_with_its_own_index(qapp):
    widget = make(qapp, homes=("inside", "inside"))
    for pane in widget.panes:
        render(pane)
    assert widget.hover_target_at(*row_point(widget, "weekly", index=1)) == (1, "limits", "weekly")


def test_model_rows_are_targets_only_where_a_row_exists(qapp):
    widget = make(qapp)
    widget.panes[0].render_usage("content", usage_view(2))
    ox, oy = widget.window_layout.pane_origins[0]
    layout = widget._layout_for("models", "inside")
    for i in (0, 1):
        r = layout[f"model_bar_{i}"]
        assert widget.hover_target_at(ox + r.x + r.w - 4, oy + r.y + 1) == (0, "models", i)
    r = layout["model_bar_2"]
    assert widget.hover_target_at(ox + r.x + r.w - 4, oy + r.y + 1) is None


def test_cat_pixels_beat_the_row_under_them_and_clear_pixels_are_nothing(qapp, monkeypatch):
    widget = make(qapp)
    render(widget.panes[0])
    solid, clear = opaque_point(widget)
    assert widget.hover_target_at(*solid) == (0, "cat", None)
    assert clear is not None and widget.hover_target_at(*clear) is None
    # Move the cat onto the session row: its pixels win there, its clear pixels do not.
    ox, oy = widget.window_layout.pane_origins[0]
    row = widget._layout_for("limits", "inside").lines["session"]
    monkeypatch.setattr(widget, "cat_origin", lambda index, offset=(0, 0): (ox + row.x, oy + row.y - 20))
    box = (ox + row.x, oy + row.y, ox + row.x + row.w, oy + row.y + row.h)
    solid, clear = opaque_point(widget, within=box)
    assert solid is not None and widget.hover_target_at(*solid) == (0, "cat", None)
    assert clear is not None
    # the cat's clear pixel over the row falls through to the row, not to the cat
    assert widget.hover_target_at(*clear) == (0, "limits", "session")


@pytest.mark.parametrize("home", ["inside_right", "on_top"])
def test_the_cat_is_a_target_at_other_spots(qapp, home):
    widget = make(qapp, homes=(home,))
    render(widget.panes[0])
    solid, clear = opaque_point(widget)
    assert widget.hover_target_at(*solid) == (0, "cat", None)


# ---- text ------------------------------------------------------------------

def test_hover_text_for_each_kind_of_target(qapp):
    widget = make(qapp)
    pane = widget.panes[0]
    render(pane, tool_label="Bash")
    assert widget.hover_text_for((0, "limits", "weekly")).startswith("Week 81%\nResets ")
    assert widget.hover_text_for((0, "cat", None)) == "Content"
    pane.render_usage("working", usage_view(), tool_label="Bash")
    text = widget.hover_text_for((0, "models", 1)).split("\n")
    assert text[0] == "M1 (7d)" and text[3].startswith("cache read 1.8M")
    assert widget.hover_text_for((0, "models", 5)) is None


def test_no_limits_data_means_no_popup(qapp):
    widget = make(qapp)
    widget.panes[0].render("content", 1, 1, "", "", "", None, None, False)
    assert widget.hover_text_for((0, "limits", "session")) is None


# ---- the popup window ------------------------------------------------------

def moved(widget, point):
    # QTest drops a move to where it last put the pointer, even in another window.
    QTest.mouseMove(widget, QPoint(0, 0))
    QTest.mouseMove(widget, QPoint(round(point[0]), round(point[1])))


def test_popup_appears_after_the_delay_without_activating(qapp):
    widget = make(qapp)
    render(widget.panes[0])
    widget.move(100, 100)
    widget.show()
    try:
        moved(widget, row_point(widget, "session"))
        assert widget.hover_popup() is None
        assert wait_for(lambda: widget.hover_popup() is not None)
        popup = widget.hover_popup()
        assert popup.text.startswith("Session 42%")
        flags = popup.windowFlags()
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus and flags & Qt.WindowType.FramelessWindowHint
        assert popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert popup.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert not popup.isActiveWindow()
        assert QGuiApplication.focusWindow() is not popup.windowHandle()
        # beside the card: right of its right edge
        assert popup.x() >= widget.x() + widget.window_layout.pane_origins[0][0] + T.CARD_WIDTH or popup.x() < widget.x()
    finally:
        widget.hover_cancel()
        widget.hide()


def test_popup_hides_when_the_pointer_leaves_the_target_or_the_window(qapp):
    widget = make(qapp)
    render(widget.panes[0])
    widget.move(100, 100)
    widget.show()
    try:
        moved(widget, row_point(widget, "session"))
        assert wait_for(lambda: widget.hover_popup() is not None)
        moved(widget, (2, 2))
        assert widget.hover_popup() is None
        moved(widget, row_point(widget, "weekly"))
        assert wait_for(lambda: widget.hover_popup() is not None)
        QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))
        assert widget.hover_popup() is None
    finally:
        widget.hide()


def test_popup_follows_the_pointer_to_another_target_at_once(qapp):
    widget = make(qapp)
    render(widget.panes[0])
    widget.move(100, 100)
    widget.show()
    try:
        moved(widget, row_point(widget, "session"))
        assert wait_for(lambda: widget.hover_popup() is not None)
        moved(widget, row_point(widget, "weekly"))
        assert widget.hover_popup().text.startswith("Week 81%")
    finally:
        widget.hover_cancel()
        widget.hide()


def test_a_press_cancels_a_pending_or_shown_popup(qapp):
    widget = make(qapp)
    render(widget.panes[0])
    widget.move(100, 100)
    widget.show()
    try:
        moved(widget, row_point(widget, "session"))
        assert wait_for(lambda: widget.hover_popup() is not None)
        point = row_point(widget, "session")
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(round(point[0]), round(point[1])))
        assert widget.hover_popup() is None
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(round(point[0]), round(point[1])))
    finally:
        widget.hover_cancel()
        widget.hide()


def test_popup_hides_when_the_cat_walks_off_from_under_a_still_pointer(qapp, monkeypatch):
    widget = make(qapp)
    render(widget.panes[0])
    widget.move(100, 100)
    widget.show()
    try:
        solid, _clear = opaque_point(widget)
        moved(widget, solid)
        assert wait_for(lambda: widget.hover_popup() is not None)
        assert widget.hover_popup().text == "Content"
        monkeypatch.setattr(widget, "cat_origin", lambda index, offset=(0, 0): (500.0, 500.0))
        widget._next_frames()
        assert widget.hover_popup() is None
    finally:
        widget.hover_cancel()
        widget.hide()


def test_hover_does_not_repaint_the_card(qapp, monkeypatch):
    widget = make(qapp)
    render(widget.panes[0])
    widget.move(100, 100)
    widget.show()
    try:
        widget.grab()
        QApplication.processEvents()
        paints = []
        original = TokittyQtWidget.paintEvent
        monkeypatch.setattr(TokittyQtWidget, "paintEvent",
                            lambda self, event: (paints.append(event.rect()), original(self, event)))
        moved(widget, row_point(widget, "session"))
        assert wait_for(lambda: widget.hover_popup() is not None)
        QTest.qWait(30)
        moved(widget, (2, 2))
        QTest.qWait(30)
        assert paints == []
    finally:
        widget.hover_cancel()
        widget.hide()


def test_no_timer_runs_while_idle(qapp):
    widget = make(qapp)
    render(widget.panes[0])
    assert not widget._hover_timer.isActive()
    widget.panes[0].render_usage("content", usage_view())
    assert not widget._hover_timer.isActive()
