import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from tokitty import theme as T
from tokitty.customize import Customization
from tokitty.menu import MenuItem, build_menu
from tokitty.qt.menu import render_menu
from tokitty.qt.widget import TokittyQtWidget, is_context_press

pytestmark = pytest.mark.qt


def labels(menu):
    return [a.text() for a in menu.actions()]


def by_text(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def passthrough(action):
    return action


# ---- menu -----------------------------------------------------------------

def test_render_menu_covers_every_item_kind(qapp):
    ran = []
    items = [
        MenuItem(label="Plain", action=lambda: ran.append("plain")),
        MenuItem(separator=True),
        MenuItem(label="Status", enabled=False),
        MenuItem(label="Hidden", dynamic_label=lambda: None),
        MenuItem(label="x", dynamic_label=lambda: "Update to 9", action=lambda: ran.append("u")),
        MenuItem(label="Check", checkbox=lambda: True, action=lambda: ran.append("check")),
        MenuItem(label="View", submenu=[
            MenuItem(label="Limits", radio_selected=lambda: False, action=lambda: ran.append("l")),
            MenuItem(label="Models", radio_selected=lambda: True, action=lambda: ran.append("m")),
        ]),
        MenuItem(label="Inert"),
    ]
    menu = QMenu()
    render_menu(menu, items, passthrough)
    assert labels(menu) == ["Plain", "", "Status", "Update to 9", "Check", "View", "Inert"]
    assert menu.actions()[1].isSeparator()
    assert not by_text(menu, "Status").isEnabled()
    assert by_text(menu, "Check").isCheckable() and by_text(menu, "Check").isChecked()
    sub = by_text(menu, "View").menu()
    assert [(a.text(), a.isChecked()) for a in sub.actions()] == [("Limits", False), ("Models", True)]
    assert sub.actions()[0].actionGroup() is sub.actions()[1].actionGroup()
    for text in ("Plain", "Update to 9", "Check"):
        by_text(menu, text).trigger()
    sub.actions()[0].trigger()
    assert ran == ["plain", "u", "check", "l"]
    assert sub.actions()[0].isChecked() and not sub.actions()[1].isChecked()


def test_after_action_wraps_every_action(qapp):
    done = []
    menu = QMenu()

    def after(action):
        return None if action is None else (lambda: (action(), done.append(1)))

    render_menu(menu, [MenuItem(label="A", action=lambda: done.append("a")),
                       MenuItem(label="B")], after)
    by_text(menu, "A").trigger()
    by_text(menu, "B").trigger()
    assert done == ["a", 1]


def test_qt_mode_menu_model(qapp):
    state = {"view": "limits", "top": True}
    items = build_menu(
        on_refresh=lambda: None, always_on_top=lambda: state["top"],
        on_toggle_always_on_top=lambda: state.update(top=not state["top"]),
        on_quit=lambda: None,
        view_modes=[("limits", "Limits"), ("models", "Models")],
        current_view_mode=lambda: state["view"],
        on_view_mode=lambda v: state.update(view=v))
    menu = QMenu()
    render_menu(menu, items, passthrough)
    assert [t for t in labels(menu) if t] == ["Refresh now", "View", "Always in front", "Exit"]
    assert by_text(menu, "Always in front").isChecked()
    sub = by_text(menu, "View").menu()
    assert [a.isChecked() for a in sub.actions()] == [True, False]
    sub.actions()[1].trigger()
    assert state["view"] == "models"
    by_text(menu, "Always in front").trigger()
    assert state["top"] is False


# ---- widget menu ----------------------------------------------------------

def make(count=1, homes=None, path=None, model=None):
    customs = [Customization(cat_home=(homes[i] if homes else "inside")) for i in range(count)]
    widget = TokittyQtWidget(T.Theme(), customs, count, position_path=path,
                             build_menu_model=model)
    widget.anim.stop()
    return widget


def test_pane_index_outside_every_card_is_zero(qapp):
    widget = make(2, homes=["beside_left", "inside"])
    x0, y0 = widget.window_layout.pane_origins[0]
    x1, y1 = widget.window_layout.pane_origins[1]
    assert widget.pane_index_at(x0 + 5, y0 + 5) == 0
    assert widget.pane_index_at(x1 + 5, y1 + 5) == 1
    assert widget.pane_index_at(2, 2) == 0  # margin


def test_right_press_opens_a_fresh_menu_for_the_clicked_pane(qapp):
    asked, popped = [], []
    widget = make(2, model=lambda i: asked.append(i) or [MenuItem(label=f"pane {i}")])
    widget._popup = lambda menu, pos: popped.append((labels(menu), pos))
    widget.show()
    y1 = widget.window_layout.pane_origins[1][1]
    QTest.mouseClick(widget, Qt.MouseButton.RightButton, pos=QPoint(40, y1 + 10))
    QTest.mouseClick(widget, Qt.MouseButton.RightButton, pos=QPoint(40, y1 + 10))
    QTest.mouseClick(widget, Qt.MouseButton.RightButton, pos=QPoint(40, 10))
    assert asked == [1, 1, 0]  # rebuilt on every open
    assert popped[0][0] == ["pane 1"]
    assert popped[0][1] == widget.mapToGlobal(QPoint(40, y1 + 10))
    widget.hide()


def test_a_plain_left_click_opens_no_menu(qapp):
    popped = []
    widget = make(model=lambda i: [MenuItem(label="x")])
    widget._popup = lambda menu, pos: popped.append(1)
    widget.show()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(40, 10))
    assert popped == []
    widget.hide()


def test_context_press_rules():
    right, left = Qt.MouseButton.RightButton, Qt.MouseButton.LeftButton
    meta, none = Qt.KeyboardModifier.MetaModifier, Qt.KeyboardModifier.NoModifier
    assert is_context_press(right, none, "linux")
    assert not is_context_press(left, none, "darwin")
    assert is_context_press(left, meta, "darwin")
    assert not is_context_press(left, meta, "win32")


def test_menu_action_done_hook_runs_after_the_action(qapp):
    order = []
    widget = make(model=lambda i: [MenuItem(label="Go", action=lambda: order.append("act"))])
    widget.on_menu_action_done = lambda: order.append("done")
    by_text(widget.context_menu(0), "Go").trigger()
    assert order == ["act", "done"]


# ---- drag and position ----------------------------------------------------

def test_drag_moves_the_window_by_the_mouse_delta_and_saves_the_card(qapp, tmp_path):
    path = tmp_path / "position.json"
    widget = make(homes=["beside_left"], path=path)
    margin = widget.window_layout.margins[0]
    assert margin > 0
    widget.move(300, 200)
    widget.show()
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(200, 50))
    QTest.mouseMove(widget, QPoint(230, 70))
    assert (widget.x(), widget.y()) == (330, 220)
    assert not path.exists()  # saved on release only
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(230, 70))
    saved = json.loads(path.read_text())
    assert saved == {"x": 330 + margin, "y": 220, "unit": "logical"}
    # a move without a press does nothing
    QTest.mouseMove(widget, QPoint(10, 10))
    assert (widget.x(), widget.y()) == (330, 220)
    widget.hide()


def test_restart_with_a_side_home_lands_the_card_in_the_same_place(qapp, tmp_path):
    path = tmp_path / "position.json"
    first = make(homes=["beside_left"], path=path)
    first.restore_position()
    first.show()
    QTest.mousePress(first, Qt.MouseButton.LeftButton, pos=QPoint(200, 50))
    QTest.mouseMove(first, QPoint(180, 40))
    QTest.mouseRelease(first, Qt.MouseButton.LeftButton, pos=QPoint(180, 40))
    card = first.first_card_pos()
    first.hide()

    second = make(homes=["beside_left"], path=path)
    second.restore_position()
    assert second.window_layout.margins[0] > 0
    assert second.first_card_pos() == card
    assert (second.x(), second.y()) == (first.x(), first.y())


def test_restore_without_a_file_uses_the_default_corner(qapp, tmp_path):
    widget = make(path=tmp_path / "none.json")
    widget.restore_position()
    area = widget._area_at(None)
    from tokitty.geometry import default_position
    assert widget.first_card_pos() == default_position(*widget.grid_size(), area)


def test_restore_pulls_an_offscreen_saved_card_back(qapp, tmp_path):
    path = tmp_path / "position.json"
    path.write_text(json.dumps({"x": 99999, "y": 99999, "unit": "logical"}))
    widget = make(path=path)
    widget.restore_position()
    left, top, right, bottom = widget._area_at(None)
    x, y = widget.first_card_pos()
    w, h = widget.grid_size()
    assert left <= x and x + w <= right and top <= y and y + h <= bottom


def test_restore_without_a_path_is_the_default(qapp):
    widget = make()
    widget.restore_position()  # no position_path: must not raise
    assert widget.first_card_pos() is not None


# ---- always in front ------------------------------------------------------

def test_always_on_top_toggle_reshows_and_runs_the_hook(qapp):
    widget = make()
    hooks = []
    widget._after_native_window = lambda: hooks.append(1)
    assert widget.always_on_top
    widget.show()
    widget.set_always_on_top(False)
    assert not widget.always_on_top
    assert not (widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert widget.isVisible()
    widget.set_always_on_top(True)
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert widget.isVisible()
    assert hooks == [1, 1]
    widget.hide()
