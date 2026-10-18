import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.qt.widget import TokittyQtWidget

import cat_check

pytestmark = pytest.mark.qt

ROOT = Path(__file__).resolve().parents[2]


# No wander headroom, so a card's pixels sit where these tests look for them.
STILL = T.CatStyle(wander=False)


def make(qapp, count=1, theme=None, homes=None):
    customs = [Customization(cat_home=(homes[i] if homes else "inside")) for i in range(count)]
    widget = TokittyQtWidget(theme or T.Theme(cat=STILL), customs, count)
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
    widget = make(qapp, theme=T.Theme(card=T.CardStyle(opacity=0), cat=STILL))
    image = widget.grab().toImage()
    assert image.pixelColor(150, 5).alpha() == 1


def test_accented_pane_is_opaque_beside_a_translucent_one(qapp):
    # two panes fill one column, so stack: pane 1 sits below pane 0
    widget = make(qapp, 2, theme=T.Theme(card=T.CardStyle(opacity=50), cat=STILL))
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


def test_make_font_is_the_one_font_seam(qapp):
    from PySide6.QtGui import QFont

    class Probe(TokittyQtWidget):
        def _make_font(self, points, weight):
            font = QFont("Aileron")
            font.setPixelSize(points)
            font.setWeight(weight)
            return font

    widget = Probe(T.Theme(), [Customization()], 1)
    widget.anim.stop()
    label, model, text, label_semi, model_semi = widget._font_set()
    assert (label.pixelSize(), model.pixelSize(), text.pixelSize()) == (
        T.FONT_SIZE_LABEL, T.FONT_SIZE_MODEL, T.FONT_SIZE_TEXT)
    assert (label_semi.pixelSize(), model_semi.pixelSize()) == (T.FONT_SIZE_LABEL, T.FONT_SIZE_MODEL)
    assert [f.weight() for f in (label, model, text, label_semi, model_semi)] == [
        QFont.Weight.Bold, QFont.Weight.Bold, QFont.Weight.Normal,
        QFont.Weight.DemiBold, QFont.Weight.DemiBold]


def test_status_and_account_label_go_through_text(qapp):
    seen = []

    class Probe(TokittyQtWidget):
        def _text(self, p, color, rect, flags, text, halo=False):
            seen.append((text, halo))
            super()._text(p, color, rect, flags, text, halo)

    widget = Probe(T.Theme(), [Customization(label="work")], 1)
    widget.anim.stop()
    widget.panes[0].render("working", 42, 67, "2h", "Mon", "", None, "open Claude Code", False)
    widget.grab()
    assert ("work", False) in seen
    assert ("open Claude Code", False) in seen


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


# ---- set_theme, anchors, close -------------------------------------------

def test_set_theme_changes_the_card_alpha(qapp):
    widget = make(qapp)
    assert widget.grab().toImage().pixelColor(150, 5).alpha() == 255
    widget.set_theme(T.Theme(card=T.CardStyle(opacity=50), cat=STILL))
    assert widget.theme.card.opacity == 50
    assert abs(widget.grab().toImage().pixelColor(150, 5).alpha() - T.card_alpha(50)) <= 1
    widget.set_theme(T.Theme(card=T.CardStyle(opacity=0), cat=STILL))
    assert widget.grab().toImage().pixelColor(150, 5).alpha() == 1


def test_set_theme_drops_cached_layouts_and_images(qapp):
    widget = make(qapp)
    widget.grab()
    assert widget._layouts and widget._images
    widget.set_theme(T.Theme(bars=T.BarStyle(height=10)))
    assert not widget._layouts and not widget._images
    r = widget._layout_for("limits", "inside")["session_bar"]
    assert r.h == 10


def test_set_theme_keeps_the_window_matching_its_layout(qapp):
    widget = make(qapp, 2, homes=["on_top", "inside"])
    widget.set_theme(T.Theme(bars=T.BarStyle(layout="full_width")))
    expected = T.layout_window(widget.homes, wander=True)
    assert (widget.width(), widget.height()) == expected.size
    # homes that changed under the new customizations are picked up too
    widget.panes[0].set_customization(Customization(cat_home="inside"))
    widget.set_theme(T.Theme())
    assert widget.homes == ["inside", "inside"]
    assert (widget.width(), widget.height()) == T.layout_window(["inside", "inside"], wander=True).size


def test_pane_anchor_is_the_window_position_plus_origin_plus_16(qapp):
    widget = make(qapp, 2, homes=["on_top", "inside"])
    widget.move(300, 200)
    for index in (0, 1):
        ox, oy = widget.window_layout.pane_origins[index]
        assert widget.pane_anchor(index) == (300 + ox + 16, 200 + oy + 16)


def test_closing_the_widget_quits_the_application(qapp):
    from PySide6.QtCore import QTimer

    widget = make(qapp)
    widget.show()
    QTimer.singleShot(0, widget.close)
    net = QTimer()
    net.setSingleShot(True)
    net.timeout.connect(qapp.quit)  # the safety net if close() does not quit
    net.start(2000)
    start = time.monotonic()
    qapp.exec()
    net.stop()
    assert time.monotonic() - start < 1.5


# ---- card design (#139) ----------------------------------------------------

class _TextLog(TokittyQtWidget):
    def __init__(self, *args, **kw):
        self.log = []
        super().__init__(*args, **kw)

    def _text(self, p, color, rect, flags, text, halo=False):
        self.log.append((text, color.name(), (rect.x(), rect.y(), rect.width(), rect.height()),
                         int(flags), halo))
        super()._text(p, color, rect, flags, text, halo)


def _logged(theme, label="work", status="open Claude Code", render_models=False):
    widget = _TextLog(replace(theme, cat=STILL), [Customization(label=label)], 1)
    widget.anim.stop()
    pane = widget.panes[0]
    if render_models:
        from tokitty.usage_display import UsageRow, UsageView
        pane.render_usage("working", UsageView(
            rows=(UsageRow("Opus 4", "$12.40", 62.0), UsageRow("Sonnet 4", "$6.10", 31.0)),
            status_text=status, ramp_pct=None))
    else:
        pane.render("working", 42, 67, "6:10 PM", "Mon", "", None, status, False)
    widget.grab()
    return widget


def test_refined_limits_text_follows_the_layout(qapp):
    widget = _logged(T.REFINED)
    lay = T.layout_card(widget.theme, "limits", "inside")
    seen = {t: (color, rect) for t, color, rect, _f, _h in widget.log}
    assert {"Session", "Week", "42%", "67%", "resets 6:10 PM", "resets Mon", "work"} <= set(seen)
    assert "SESSION" not in seen
    r = lay["session_label"]
    assert seen["Session"] == (T.REFINED.text.foreground, (r.x, r.y, r.w, r.h))
    r = lay["session_percent"]
    assert seen["42%"] == (T.REFINED.text.foreground, (r.x, r.y, r.w, r.h))
    assert seen["resets 6:10 PM"][0] == T.REFINED.text.dim
    assert seen["work"][0] == T.REFINED.text.dim


def test_refined_status_is_one_elided_line_clear_of_the_account_label(qapp):
    long = "a very long status line that cannot possibly fit in the stats column"
    widget = _logged(T.REFINED, status=long)
    status = next(e for e in widget.log if e[0] != "work" and e[0].startswith("a very"))
    assert status[0] != long and status[0].endswith("…")
    label = next(e for e in widget.log if e[0] == "work")
    status_right = status[2][0] + status[2][2]
    assert status_right <= label[2][0] + label[2][2] - 6  # the label's text starts after it
    assert not (status[3] & int(Qt.TextFlag.TextWordWrap))
    short = _logged(T.REFINED, status="idle")
    assert any(e[0] == "idle" for e in short.log)
    no_label = _logged(T.REFINED, label="", status=long)
    wide = next(e for e in no_label.log if e[0].startswith("a very"))
    assert wide[2][2] == T.layout_card(T.REFINED, "limits", "inside")["status"].w


def test_refined_models_rows(qapp):
    widget = _logged(T.REFINED, render_models=True)
    names = {t: rect for t, _c, rect, _f, _h in widget.log}
    lay = T.layout_card(widget.theme, "models", "inside")
    r = lay["model_name_1"]
    assert names["Sonnet 4"] == (r.x, r.y, r.w, r.h)
    r = lay["model_value_1"]
    assert names["$6.10"] == (r.x, r.y, r.w, r.h)


def test_classic_keeps_its_upper_case_labels_and_inline_percent(qapp):
    widget = _logged(T.CLASSIC)
    texts = [t for t, *_ in widget.log]
    assert "SESSION" in texts and "WEEK" in texts and "42%" in texts
    assert "Session" not in texts
    assert any(t.startswith("· ") for t in texts)


def test_changing_the_design_repaints_the_cached_card(qapp):
    widget = make(qapp, theme=T.Theme(cat=STILL))
    render(widget.panes[0])
    pane, style = widget.panes[0], widget._style_for(0, widget.panes[0])
    layout = widget._layout_for(pane._view_mode, "inside")
    widget._font_set()
    before = widget._card_signature(pane, style, layout, 0, 0, 1.0)
    widget.theme = replace(widget.theme, card=replace(widget.theme.card, design="classic"))
    after = widget._card_signature(pane, style, layout, 0, 0, 1.0)
    assert before != after


# ---- per-bar colour overrides (#156) ----------------------------------------

def _bar_px(widget, layout, name, pct):
    r = layout[name]
    return widget.grab().toImage().pixelColor(r.x + int(r.w * pct / 200), r.y + r.h // 2).name()


def _overridden(design, overrides):
    theme = replace(T.Theme(cat=STILL), card=replace(T.Theme().card, design=design))
    widget = TokittyQtWidget(theme, [Customization(overrides=overrides)], 1)
    widget.anim.stop()
    return widget


@pytest.mark.parametrize("design", ["classic", "refined"])
@pytest.mark.parametrize("overrides,session,weekly", [
    ({}, None, None),
    ({"bar_fill": "#aa0000"}, "#aa0000", "#aa0000"),
    ({"session_bar": "#00aa00"}, "#00aa00", None),
    ({"weekly_bar": "#0000aa"}, None, "#0000aa"),
    ({"bar_fill": "#aa0000", "weekly_bar": "#0000aa"}, "#aa0000", "#0000aa"),
])
def test_bar_overrides_colour_each_bar_and_the_model_rows(qapp, design, overrides, session, weekly):
    from tokitty.usage_display import UsageRow, UsageView

    widget = _overridden(design, overrides)
    layout = widget._layout_for("limits", "inside")
    ramp = lambda pct: T.bar_fill(widget.theme, pct, None)  # noqa: E731
    render(widget.panes[0], session=42, weekly=67)
    assert _bar_px(widget, layout, "session_bar", 42) == (session or ramp(42))
    assert _bar_px(widget, layout, "weekly_bar", 67) == (weekly or ramp(67))

    widget.panes[0].render_usage("working", UsageView(
        rows=(UsageRow("Opus", "$1.20", 60.0),), status_text="note", ramp_pct=30.0))
    models = widget._layout_for("models", "inside")
    assert _bar_px(widget, models, "model_bar_0", 60) == (weekly or ramp(30.0))


def test_the_card_repaints_when_either_bar_override_changes(qapp):
    widget = make(qapp)
    render(widget.panes[0])
    pane = widget.panes[0]
    layout = widget._layout_for(pane._view_mode, "inside")
    widget._font_set()

    def signature():
        return widget._card_signature(pane, widget._style_for(0, pane), layout, 0, 0, 1.0)

    base = signature()
    pane.set_appearance(session_bar="#111111")
    with_session = signature()
    pane.set_appearance(weekly_bar="#222222")
    with_both = signature()
    assert len({base, with_session, with_both}) == 3
