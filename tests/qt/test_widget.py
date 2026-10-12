import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.qt.widget import TokittyQtWidget

import cat_check

pytestmark = pytest.mark.qt

ROOT = Path(__file__).resolve().parents[2]


def make(qapp, count=1, theme=None, homes=None):
    customs = [Customization(cat_home=(homes[i] if homes else "inside")) for i in range(count)]
    widget = TokittyQtWidget(theme or T.Theme(), customs, count)
    widget.anim.stop()
    return widget


def render(pane, state="working", session=42, weekly=67, accent=False, **kw):
    pane.render(state, session, weekly, "resets in 2h", "Mon", "", None, None, False,
                accent=accent, **kw)


# ---- window ---------------------------------------------------------------

@pytest.mark.parametrize("count", [1, 2, 4, 5, 8])
def test_window_matches_layout_window(qapp, count):
    widget = make(qapp, count)
    expected = T.layout_window(["inside"] * count)
    assert (widget.width(), widget.height()) == expected.size
    assert widget.window_layout.pane_origins == expected.pane_origins
    assert len(widget.panes) == count


def test_window_with_an_on_top_home(qapp):
    widget = make(qapp, 2, homes=["on_top", "inside"])
    expected = T.layout_window(["on_top", "inside"])
    assert expected.margins[1] > 0
    assert (widget.width(), widget.height()) == expected.size
    assert widget.window_layout.pane_origins == expected.pane_origins


def test_unreachable_home_resolves_to_inside(qapp):
    # pane 1 of 2 sits at the bottom, so on_top is not reachable from it
    widget = make(qapp, 2, homes=["inside", "on_top"])
    assert widget.homes == ["inside", "inside"]


def test_flags_and_attributes(qapp):
    widget = make(qapp)
    flags = widget.windowFlags()
    for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint,
                 Qt.WindowType.Tool, Qt.WindowType.WindowDoesNotAcceptFocus,
                 Qt.WindowType.NoDropShadowWindowHint):
        assert flags & flag
    for attr in (Qt.WidgetAttribute.WA_TranslucentBackground,
                 Qt.WidgetAttribute.WA_ShowWithoutActivating,
                 Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow):
        assert widget.testAttribute(attr)


def test_rebuild_layout_moves_by_the_margin_change(qapp):
    widget = make(qapp)
    widget.move(500, 400)
    widget.rebuild_layout(["on_top"])
    top = T.layout_window(["on_top"]).margins[1]
    assert top > 0
    assert (widget.x(), widget.y()) == (500, 400 - top)
    assert widget.size().height() == T.layout_window(["on_top"]).size[1]
    widget.rebuild_layout(["inside"])
    assert (widget.x(), widget.y()) == (500, 400)
    with pytest.raises(ValueError):
        widget.rebuild_layout(["inside", "inside"])


def test_screen_watch_connects_once_the_native_window_exists(qapp):
    widget = make(qapp)
    assert widget.windowHandle() is None and widget._watched is None
    widget.show()
    assert widget.windowHandle() is not None
    assert widget._watched is widget.windowHandle()
    widget.hide()


def test_images_are_dropped_on_screen_change(qapp):
    widget = make(qapp)
    widget.grab()
    assert widget._images
    widget.show()
    widget.windowHandle().screenChanged.emit(widget.screen())
    assert not widget._images
    widget.hide()


# ---- cat ------------------------------------------------------------------

def test_cat_pixels_dpr1(qapp):
    results = cat_check.run_all()
    assert results
    assert {r["dpr"] for r in results} == {1.0}
    assert all(r["opaque"] > 0 for r in results)
    assert [r for r in results if r["bad_opaque"] or r["bad_clear"]] == []
    blocks = [r for r in results if r["block_equal"] is not None]
    assert blocks and all(r["block_equal"] for r in blocks)
    assert {r["mirrored"] for r in results} == {False, True}


def test_cat_pixels_dpr2_in_a_subprocess(qapp):
    env = dict(os.environ, QT_SCALE_FACTOR="2", QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=str(ROOT))
    script = Path(__file__).with_name("cat_check.py")
    proc = subprocess.run([sys.executable, str(script)], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    assert {r["dpr"] for r in results} == {2.0}
    assert all(r["opaque"] > 0 for r in results)
    assert [r for r in results if r["bad_opaque"] or r["bad_clear"]] == []
    assert all(r["block_equal"] for r in results if r["block_equal"] is not None)


def test_cat_cache_key_uses_a_hashable_palette(qapp):
    widget = make(qapp)
    widget.grab()
    for key in widget._images:
        hash(key)
    count = len(widget._images)
    widget.grab()
    assert len(widget._images) == count  # a repaint reuses the cached image


# ---- card -----------------------------------------------------------------

def test_zero_percent_card_still_paints_alpha_one(qapp):
    widget = make(qapp, theme=T.Theme(card=T.CardStyle(opacity=0)))
    image = widget.grab().toImage()
    assert image.pixelColor(150, 5).alpha() == 1


def test_accented_pane_is_opaque_beside_a_translucent_one(qapp):
    # two panes fill one column, so stack: pane 1 sits below pane 0
    widget = make(qapp, 2, theme=T.Theme(card=T.CardStyle(opacity=50)))
    render(widget.panes[0], "permission", accent=True)
    render(widget.panes[1])
    image = widget.grab().toImage()
    y1 = widget.window_layout.pane_origins[1][1]
    assert image.pixelColor(150, 5).alpha() == 255
    assert abs(image.pixelColor(150, y1 + 5).alpha() - T.card_alpha(50)) <= 1
    accent = image.pixelColor(150, 5)
    assert accent.name() == T.ACCENT_BG


# ---- repaint --------------------------------------------------------------

def test_render_repaints_the_session_row(qapp):
    # The bar's fill, not the percentage text: Windows' offscreen platform
    # has no fonts to draw with, and the fill width changes either way.
    widget = make(qapp)
    layout = T.layout_card(T.Theme(), "limits", "inside")
    r = layout["session_bar"]
    box = (r.x, r.y, r.w, r.h)

    def crop():
        return widget.grab().toImage().copy(*box)

    render(widget.panes[0], session=42)
    before = crop()
    seen = []
    widget.panes[0].on_change = lambda: (seen.append(1), widget.update())
    render(widget.panes[0], session=88)
    after = crop()
    assert seen
    assert before != after


def test_dimmed_render_uses_the_dim_colour_for_labels(qapp):
    widget = make(qapp)
    r = T.layout_card(T.Theme(), "limits", "inside")["session_label"]

    def ink(dimmed):
        widget.panes[0].render("working", 1, 1, "x", "y", "", None, None, dimmed)
        image = widget.grab().toImage().copy(r.x, r.y, r.w, r.h)
        colors = {image.pixelColor(x, y).name() for x in range(r.w) for y in range(r.h)}
        return colors

    assert T.TextStyle.foreground in ink(False)
    assert T.TextStyle.foreground not in ink(True)
    assert T.TextStyle.dim in ink(True)


def test_models_view_paints_rows(qapp):
    from tokitty.usage_display import UsageRow, UsageView

    widget = make(qapp)
    view = UsageView(rows=(UsageRow("Opus", "$1.20", 60.0),), status_text="note", ramp_pct=None)
    widget.panes[0].render_usage("working", view)
    image = widget.grab().toImage()
    bar = T.layout_card(T.Theme(), "models", "inside")["model_bar_0"]
    # the neutral fill, since a share-of-total bar has no cap to be near
    assert image.pixelColor(bar.x + 2, bar.y + 2).name() == T.BarStyle.neutral
    assert image.pixelColor(bar.right - 2, bar.y + 2).name() == T.BarStyle.track


def test_halo_draws_outline_pixels(qapp):
    off = make(qapp)
    on = make(qapp, theme=T.Theme(text=T.TextStyle(halo="on")))
    for w in (off, on):
        render(w.panes[0])
    r = T.layout_card(T.Theme(), "limits", "inside")["session_label"]

    def crop(w):
        return w.grab().toImage().copy(r.x - 2, r.y - 2, r.w + 4, r.h + 4)

    assert crop(off) != crop(on)


def test_tags_sit_on_the_cards_outer_side(qapp):
    widget = make(qapp, 2, homes=["inside", "inside_right"])
    left_box, _tool, left_side = widget.tag_boxes(0, "inside")
    right_box, _tool, right_side = widget.tag_boxes(1, "inside_right")
    assert left_side == Qt.AlignmentFlag.AlignLeft
    assert right_side == Qt.AlignmentFlag.AlignRight
    # Left cat: inset from the card's left edge. Right cat: inset from its right edge.
    assert left_box.left() == 8 + 6
    oy = widget.window_layout.pane_origins[1][1]
    assert right_box.right() == T.CARD_WIDTH - 8 - 6
    assert right_box.top() == oy + 8
