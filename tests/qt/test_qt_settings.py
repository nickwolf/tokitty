"""The Qt Settings window: General, Look, Theme and Usage over a real widget,
QtHost and Controls. Dialogs are patched, never shown. Pixel samples are on
card and bar areas only: offscreen Qt on Windows draws no glyphs."""
import threading
import time

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tokitty import sprites, theme as T
from tokitty.customize import Customization, effective_palette, load_customization
from tokitty.qt import settings as qs
from tokitty.qt.settings import SettingsWindow
from tokitty.settings import load_settings
from tokitty.settings_style import BASE_HEIGHT, BASE_WIDTH

pytestmark = pytest.mark.qt


def spin_events(until, limit_ms=3000):
    deadline = time.monotonic() + limit_ms / 1000
    while not until() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return until()


def open_window(world, pane=0):
    return SettingsWindow.open(world.host, pane)


def counted(world):
    """Count notify_state_changed calls, keeping the real one."""
    calls = []
    real = world.host.notify_state_changed
    world.host.notify_state_changed = lambda: (calls.append(1), real())[1]
    return calls


# ---- window ---------------------------------------------------------------

def test_opens_on_the_requested_pane(make_world):
    world = make_world(3)
    window = open_window(world, 2)
    assert window.pane_index == 2
    assert window.windowTitle() == "Tokitty: Settings"
    assert window.parent() is None
    assert window.tabs["look"].pane_picker.get() == "Cat 3"
    assert world.host.settings_refresh == window.refresh


def test_reopening_switches_the_pane_and_does_not_make_a_second_window(make_world):
    world = make_world(3)
    first = open_window(world, 0)
    second = open_window(world, 1)
    assert second is first
    assert first.pane_index == 1
    assert first.tabs["look"].pane_picker.get() == "Cat 2"


def test_opens_as_tall_as_the_look_tab_not_the_tallest(make_world):
    window = open_window(make_world(2))
    assert (window.minimumWidth(), window.minimumHeight()) == (BASE_WIDTH, BASE_HEIGHT)
    assert window.width() == BASE_WIDTH and window.height() >= BASE_HEIGHT
    chrome = 18 + 12 + window.footer.sizeHint().height()
    look = chrome + window.tabs["look"].frame.sizeHint().height()
    theme = chrome + window.tabs["theme"].frame.sizeHint().height()
    assert theme > look  # otherwise this test proves nothing
    assert window.height() == max(BASE_HEIGHT, look)
    window.close()


def test_rail_switches_tabs(make_world):
    window = open_window(make_world())
    assert list(window.tabs) == ["general", "look", "theme", "usage", "accounts", "streamdock"]
    window.rail.strips["usage"].clicked.emit("usage")
    assert window.current == "usage"
    assert not window.tabs["usage"].frame.isHidden()
    assert window.tabs["general"].frame.isHidden()
    assert window.rail.strips["usage"].property("active") is True


def test_closing_the_window_releases_it(make_world):
    world = make_world()
    window = open_window(world)
    window.close()
    assert not window.alive()
    assert world.host.settings_refresh is None
    assert qs._instance is None
    again = open_window(world)
    assert again is not window and again.alive()


# ---- General ----------------------------------------------------------------

def test_always_in_front_toggle_calls_the_seam_then_notifies(make_world):
    world = make_world()
    window = open_window(world)
    calls = counted(world)
    order = []
    real = world.host.toggle_always_on_top
    world.host.toggle_always_on_top = lambda: (order.append(("seam", len(calls))), real())[1]
    toggle = window.tabs["general"].toggles["aot"]
    assert toggle.get() is True

    toggle._clicked()

    assert order == [("seam", 0)]  # the seam ran before any notify
    assert len(calls) == 1
    assert world.widget.always_on_top is False
    assert toggle.get() is False


def test_surprise_toggle_writes_the_setting_and_notifies(make_world):
    world = make_world()
    window = open_window(world)
    calls = counted(world)
    window.tabs["general"].toggles["surprise"]._clicked()
    assert load_settings(world.state_dir).surprise_me is True
    assert len(calls) == 1


def _write_accounts(world):
    import json

    (world.state_dir / "accounts.json").write_text(json.dumps({"accounts": [
        {"name": "alice-work", "config_dir": str(world.state_dir / "alice-dir")},
        {"name": "zed-codex", "config_dir": str(world.state_dir / "zed-dir"),
         "provider": "codex"}]}))


def test_copy_diagnostics_button_sits_below_the_toggles(make_world):
    world = make_world()
    general = open_window(world).tabs["general"]
    button = general.diagnostics_button
    assert button.text() == "Copy diagnostics"
    assert general.diagnostics_hint.text() == qs.DIAGNOSTICS_HINT
    assert general.frame.isAncestorOf(button)
    assert button.mapTo(general.frame, QPoint(0, 0)).y() > \
        general.toggles["aot"].mapTo(general.frame, QPoint(0, 0)).y()


def test_copy_diagnostics_puts_a_clean_report_on_the_clipboard(make_world, monkeypatch):
    from tokitty import accounts_rows
    from tokitty.hooks_install import HookStatus

    world = make_world(customs=[Customization(label="Nick's laptop"), Customization()])
    _write_accounts(world)
    (world.state_dir / "customization.json").write_text(
        '{"alice-work": {"label": "Nick\'s laptop"}}')
    monkeypatch.setattr(accounts_rows, "hook_status_for_dir",
                        lambda *a, **k: HookStatus("installed", "alice-work detail"))
    copied = []
    monkeypatch.setattr(qs, "_copy_text", copied.append)
    general = open_window(world).tabs["general"]

    general.diagnostics_button.click()
    assert general.diagnostics_hint.text() == "Collecting…"
    assert spin_events(lambda: general.diagnostics_hint.text() == "Copied")

    assert len(copied) == 1
    report = copied[0]
    assert report.splitlines()[0].startswith("tokitty: ")
    assert "accounts: 2 (claude 1, codex 1)" in report
    assert "1. claude: installed" in report and "2. codex: installed" in report
    assert "screens: " in report and "qt: " in report
    for leaked in ("alice", "zed-codex", "Nick's laptop", str(world.state_dir)):
        assert leaked not in report
    # a refresh puts the hint back
    world.host.notify_state_changed()
    assert general.diagnostics_hint.text() == qs.DIAGNOSTICS_HINT


def test_copy_diagnostics_reports_a_clipboard_failure(make_world, monkeypatch):
    def boom(_text):
        raise RuntimeError("no clipboard")

    monkeypatch.setattr(qs, "_copy_text", boom)
    general = open_window(make_world()).tabs["general"]
    general.diagnostics_button.click()
    assert spin_events(lambda: "Could not copy" in general.diagnostics_hint.text())


def test_general_has_no_transparency_row_and_hides_missing_seams(make_world):
    world = make_world()
    window = open_window(world)
    assert set(window.tabs["general"].toggles) == {"aot", "surprise"}
    assert "updates" not in window.tabs
    assert not hasattr(window.tabs["general"], "transparency")


def test_start_at_login_shows_only_when_the_backend_exists(make_world, monkeypatch):
    from tokitty import autostart

    class Backend:
        def __init__(self):
            self.registered = False

        def is_registered(self):
            return self.registered

        def deregister(self):
            self.registered = False

    backend = Backend()
    monkeypatch.setattr(autostart, "get_backend", lambda: backend)
    monkeypatch.setattr(autostart, "ensure_current", lambda *a: None)
    monkeypatch.setattr(autostart, "write_launcher_and_register",
                        lambda state_dir, b: setattr(b, "registered", True))
    world = make_world()
    window = open_window(world)
    toggle = window.tabs["general"].toggles["autostart"]
    assert toggle.get() is False
    toggle._clicked()
    assert backend.registered is True and toggle.get() is True


def test_a_menu_action_refreshes_an_open_settings_window(make_world):
    world = make_world()
    window = open_window(world)
    toggle = window.tabs["general"].toggles["aot"]
    assert toggle.get() is True

    world.widget._after_menu_action(world.host.toggle_always_on_top)()

    assert toggle.get() is False


def test_tray_and_update_rows_appear_when_their_seams_exist(make_world):
    world = make_world()
    state = {"tray": False, "updates": True}
    world.host.on_toggle_tray = lambda: state.update(tray=not state["tray"])
    world.host.tray_enabled = lambda: state["tray"]
    world.host.update_check_enabled = lambda: state["updates"]
    world.host.on_toggle_update_check = lambda: state.update(updates=not state["updates"])
    world.host.on_check_updates = lambda: None
    world.host.update_available_label = lambda: "v9 is out"
    window = open_window(world)
    general, updates = window.tabs["general"], window.tabs["updates"]
    assert "tray" in general.toggles
    general.toggles["tray"]._clicked()
    updates.update_toggle._clicked()
    assert state == {"tray": True, "updates": False}
    assert updates.update_pill.text() == "v9 is out"


def test_the_tray_row_follows_the_trays_live_availability(make_world):
    world = make_world()
    state = {"tray": False, "available": True}
    world.host.on_toggle_tray = lambda: state.update(tray=not state["tray"])
    world.host.tray_enabled = lambda: state["tray"]
    world.host.tray_available = lambda: state["available"]
    window = open_window(world)
    toggle = window.tabs["general"].toggles["tray"]
    assert toggle.isVisibleTo(window)
    state["available"] = False  # start() failed while the window was open
    world.host.notify_state_changed()
    assert not toggle.isVisibleTo(window)
    window.close()
    assert "tray" not in open_window(world).tabs["general"].toggles


# ---- Look -------------------------------------------------------------------

def test_look_writes_go_to_the_selected_pane(make_world):
    world = make_world(2)
    window = open_window(world, 1)
    look = window.tabs["look"]
    other = "gray" if world.store["k1"].colorway != "gray" else "orange"
    look.colorway.menu.actions()[look.colorway.choices().index(other)].trigger()
    assert world.store["k1"].colorway == other
    assert world.store["k0"].colorway != other
    assert load_customization(world.state_dir)["k1"].colorway == other
    assert world.widget.panes[1]._colorway == other
    assert look.colorway.get() == other


def test_look_name_entry_writes_the_label(make_world):
    world = make_world(1)
    window = open_window(world)
    look = window.tabs["look"]
    look.name_entry.setText("Work")
    look.name_entry.editingFinished.emit()
    assert world.store["k0"].label == "Work"
    assert world.widget.panes[0]._label == "Work"
    assert look.pane_picker.get() == "Work"


# ---- name field in a mixed window and its save triggers ----------------------

def mixed(make_world, **kw):
    return make_world(2, tag_kinds=["claude", "codex"], **kw)


def committed(world, key="k0"):
    return load_customization(world.state_dir).get(key, Customization()).label


def test_mixed_window_name_field_shows_only_the_users_part(make_world):
    world = mixed(make_world, customs=[Customization(label="work"), Customization()])
    window = open_window(world)
    look = window.tabs["look"]
    assert world.widget.panes[0]._label == "claude\u00b7work"
    assert look.name_entry.text() == "work"
    assert look.name_hint.text() == "claude\u00b7"
    assert not look.name_hint.isHidden()
    window.set_pane(1)
    assert look.name_entry.text() == ""
    assert look.name_hint.text() == "codex\u00b7"


def test_mixed_window_name_saves_without_doubling_the_prefix(make_world):
    world = mixed(make_world, customs=[Customization(label="work"), Customization()])
    window = open_window(world)
    look = window.tabs["look"]
    look.name_entry.setText("sys")
    look.name_entry.editingFinished.emit()
    assert world.store["k0"].label == "sys"
    assert world.widget.panes[0]._label == "claude\u00b7sys"
    assert look.name_entry.text() == "sys"
    look.name_entry.setText("claude\u00b7again")
    look.name_entry.editingFinished.emit()
    assert world.store["k0"].label == "again"
    # Only the pane's own tag is stripped.
    look.name_entry.setText("codex\u00b7x")
    look.name_entry.editingFinished.emit()
    assert world.store["k0"].label == "codex\u00b7x"


def test_a_doubled_stored_label_heals_when_committed(make_world):
    world = mixed(make_world, customs=[Customization(label="claude\u00b7sys"), Customization()])
    window = open_window(world)
    look = window.tabs["look"]
    assert look.name_entry.text() == "claude\u00b7sys"
    look.name_entry.editingFinished.emit()
    assert world.store["k0"].label == "sys"
    assert look.name_entry.text() == "sys"
    assert world.widget.panes[0]._label == "claude\u00b7sys"


def test_blank_name_in_a_mixed_window_shows_the_bare_harness(make_world):
    world = mixed(make_world, customs=[Customization(label="work"), Customization()])
    window = open_window(world)
    look = window.tabs["look"]
    look.name_entry.setText("")
    look.name_entry.editingFinished.emit()
    assert world.store["k0"].label == ""
    assert world.widget.panes[0]._label == "claude"
    look.name_entry.setText("claude\u00b7")
    look.name_entry.editingFinished.emit()
    assert world.store["k0"].label == ""


def test_single_harness_window_hides_the_hint(make_world):
    world = make_world(1)
    window = open_window(world)
    assert window.tabs["look"].name_hint.isHidden()


def test_an_unchanged_name_is_not_written_twice(make_world):
    world = mixed(make_world)
    window = open_window(world)
    look = window.tabs["look"]
    calls = counted(world)
    look.name_entry.setText("sys")
    look.name_entry.editingFinished.emit()
    look.commit()
    window.close()
    assert len(calls) == 1


def focused_look(world):
    window = open_window(world)
    window.select_tab("look")
    window.show()
    window.activateWindow()
    QApplication.processEvents()
    look = window.tabs["look"]
    look.name_entry.setFocus()
    QApplication.processEvents()
    assert look.name_entry.hasFocus()
    look.name_entry.setText("typed")
    return window, look


def assert_saved_to_first_cat(world):
    assert committed(world, "k0") == "typed"
    assert world.store["k0"].label == "typed"
    assert committed(world, "k1") == ""
    assert world.store["k1"].label == ""


def test_clicking_a_blank_area_saves_the_name(make_world):
    world = mixed(make_world)
    window, look = focused_look(world)
    QTest.mouseClick(look.frame, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert not look.name_entry.hasFocus()
    assert_saved_to_first_cat(world)


def test_clicking_outside_the_scroll_area_saves_the_name(make_world):
    # The rail and footer are not in the scroll area, which takes click focus
    # itself; only the window's own click focus covers them.
    world = mixed(make_world)
    window, look = focused_look(world)
    QTest.mouseClick(window.rail, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert not look.name_entry.hasFocus()
    assert_saved_to_first_cat(world)


def test_clicking_a_rail_tab_saves_the_name(make_world):
    world = mixed(make_world)
    window, look = focused_look(world)
    strip = window.rail.strips["usage"]
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton)
    assert window.current == "usage"
    assert_saved_to_first_cat(world)


def test_switching_cats_saves_the_name_and_shows_the_new_cats(make_world):
    world = mixed(make_world, customs=[Customization(), Customization(label="other")])
    window, look = focused_look(world)

    menu = look.pane_picker.menu
    QTest.mouseClick(look.pane_picker, Qt.MouseButton.LeftButton)
    # The press alone moves focus off the entry and saves.
    assert not look.name_entry.hasFocus()
    assert committed(world, "k0") == "typed"
    menu.actions()[1].trigger()
    menu.close()
    assert window.pane_index == 1
    assert committed(world, "k0") == "typed"
    assert look.name_entry.text() == "other"
    assert world.store["k1"].label == "other"
    assert look.name_hint.text() == "codex\u00b7"


def test_picking_a_cat_without_a_click_still_saves_the_name(make_world):
    world = mixed(make_world, customs=[Customization(), Customization(label="other")])
    window, look = focused_look(world)
    look.pane_picker.menu.actions()[1].trigger()
    assert committed(world, "k0") == "typed"
    assert look.name_entry.text() == "other"
    assert world.store["k1"].label == "other"


def test_closing_the_window_saves_the_name(make_world):
    world = mixed(make_world)
    window, look = focused_look(world)
    window.close()
    assert_saved_to_first_cat(world)


def test_look_color_rows_use_the_dialog_and_report_the_effective_colors(make_world, monkeypatch):
    world = make_world(1)
    window = open_window(world)
    look = window.tabs["look"]
    assert look.color_rows["card_bg"].value.text() == T.CardStyle.background.upper()
    assert look.color_rows["bar_fill"].value.text() == "AUTOMATIC"
    asked = []

    def get_color(initial, parent, title):
        asked.append((initial.name(), parent, title))
        return QColor("#112233")

    monkeypatch.setattr(qs.QColorDialog, "getColor", staticmethod(get_color))
    look.color_rows["coat_base"].button.click()
    look.color_rows["bar_fill"].button.click()
    look.color_rows["card_bg"].button.click()

    assert [a[2] for a in asked] == ["Coat", "Bar fill", "Card background"]
    assert all(a[1] is window for a in asked)
    assert world.store["k0"].overrides == {"coat_base": "#112233", "bar_fill": "#112233",
                                           "card_bg": "#112233"}
    assert look.color_rows["bar_fill"].value.text() == "#112233".upper()
    assert world.host.pane_color(0, "card_bg") == "#112233"
    assert world.host.pane_color(0, "coat_base") == "#112233"
    assert world.widget.panes[0].palette["o"] == "#112233"

    look.reset_button.click()
    assert world.store["k0"].overrides == {}
    assert look.color_rows["bar_fill"].value.text() == "AUTOMATIC"


def test_a_cancelled_color_dialog_writes_nothing(make_world, monkeypatch):
    world = make_world(1)
    window = open_window(world)
    monkeypatch.setattr(qs.QColorDialog, "getColor", staticmethod(lambda *a: QColor()))
    window.tabs["look"].color_rows["coat_base"].button.click()
    assert world.store["k0"].overrides == {}


def test_card_background_reports_the_theme_color_when_unset(make_world):
    world = make_world(1, theme=T.Theme(card=T.CardStyle(background="#223344")))
    assert world.host.pane_color(0, "card_bg") == "#223344"
    assert world.host.pane_color(0, "bar_fill") is None


@pytest.mark.parametrize("count,first_row", [(1, {0}), (2, {0}), (4, {0}), (5, {0, 1})])
def test_cat_sits_offers_on_top_only_to_first_row_panes(make_world, count, first_row):
    world = make_world(count)
    window = open_window(world)
    window._show("look")
    look = window.tabs["look"]
    for index in range(count):
        window.set_pane(index)
        offered = look.cat_sits.items["on_top"].isVisibleTo(window)
        assert offered == (index in first_row), (count, index)
        assert look.cat_sits.items["inside"].isVisibleTo(window)
        assert look.cat_sits.items["inside_right"].isVisibleTo(window)


def test_cat_sits_right_swaps_without_resizing_the_window(make_world):
    world = make_world(2)
    window = open_window(world, 0)
    size = (world.widget.width(), world.widget.height())
    window.tabs["look"].cat_sits.items["inside_right"].click()
    assert world.store["k0"].cat_home == "inside_right"
    assert world.widget.homes[0] == "inside_right"
    assert (world.widget.width(), world.widget.height()) == size
    assert window.tabs["look"].cat_sits.get() == "inside_right"
    assert window.tabs["look"].preview.image == window.tabs["look"].kit.sprite_preview(
        world.widget.panes[0].palette, "content", True).image


def test_cat_sits_on_top_grows_the_window_upward(make_world):
    world = make_world(2)
    world.widget.move(400, 300)
    window = open_window(world, 0)
    card = world.widget.first_card_pos()
    height = world.widget.height()
    window.tabs["look"].cat_sits.items["on_top"].click()
    assert world.widget.homes[0] == "on_top"
    assert world.widget.height() > height
    assert world.widget.first_card_pos() == card
    assert world.widget.y() < 300


def test_a_saved_on_top_that_is_unreachable_shows_as_left(make_world):
    world = make_world(customs=[Customization(), Customization(cat_home="on_top")])
    window = open_window(world, 1)
    assert world.widget.homes[1] == "inside"
    assert window.tabs["look"].cat_sits.get() == "inside"


def test_a_colour_change_after_cat_sits_keeps_the_home(make_world, monkeypatch):
    world = make_world(2)
    window = open_window(world, 0)
    look = window.tabs["look"]
    look.cat_sits.items["on_top"].click()
    monkeypatch.setattr(qs.QColorDialog, "getColor", staticmethod(lambda *a: QColor("#445566")))
    look.color_rows["coat_shade"].button.click()
    look.colorway.menu.actions()[1].trigger()
    assert world.widget.panes[0].customization.cat_home == "on_top"
    assert world.store["k0"].cat_home == "on_top"
    assert world.widget.homes[0] == "on_top"
    world.host.set_theme(T.Theme(card=T.CardStyle(opacity=50)))
    assert world.widget.homes[0] == "on_top"
    assert load_customization(world.state_dir)["k0"].cat_home == "on_top"


# ---- Usage ------------------------------------------------------------------

def test_usage_groups_write_their_seams(make_world):
    world = make_world(1)
    window = open_window(world)
    usage = window.tabs["usage"]
    usage.groups["view"][0].items["models"].click()
    usage.groups["readout"][0].items["tokens"].click()
    saved = load_settings(world.state_dir)
    assert (saved.view_mode, saved.usage_readout) == ("models", "tokens")


def test_the_budget_entry_rereads_when_the_usage_window_changes(make_world):
    world = make_world(1)
    window = open_window(world)
    usage = window.tabs["usage"]
    assert usage.budget_label.text() == "Budget for the last 7 days"
    usage.budget_entry.setText("12.5")
    usage.budget_entry.editingFinished.emit()
    assert world.controls.budget_for_pane(0) == 12.5

    usage.groups["window"][0].items["24h"].click()
    assert usage.budget_label.text() == "Budget for the last 24 hours"
    assert usage.budget_entry.text() == ""
    usage.groups["window"][0].items["7d"].click()
    assert usage.budget_entry.text() == "12.5"


def test_a_bad_budget_marks_the_entry(make_world):
    world = make_world(1)
    window = open_window(world)
    usage = window.tabs["usage"]
    usage.budget_entry.setText("nan")
    usage.budget_entry.editingFinished.emit()
    assert usage.budget_holder.property("error") is True and usage.budget_error.text()
    assert world.controls.budget_for_pane(0) is None


def test_note_thresholds_validate_and_save(make_world):
    world = make_world(1)
    window = open_window(world)
    usage = window.tabs["usage"]
    holder, entry, error = usage.note_entries["session"]
    entry.setText("abc")
    entry.editingFinished.emit()
    assert holder.property("error") is True and error.text()
    entry.setText("70")
    entry.editingFinished.emit()
    assert holder.property("error") is False
    assert world.controls.usage_note_threshold("session") == 70


def test_ntfy_test_runs_on_a_worker_and_is_delivered_on_the_gui_thread(make_world):
    world = make_world(1)
    threads = {}

    def fake_send():
        threads["worker"] = threading.current_thread()
        return None

    world.host.send_ntfy_test = fake_send
    world.controls.set_ntfy_value("topic", "mytopic")
    window = open_window(world)
    usage = window.tabs["usage"]
    real = usage.ntfy_poller._handler

    def spy(tag, ok, value):
        threads["handler"] = threading.current_thread()
        real(tag, ok, value)

    usage.ntfy_poller._handler = spy
    usage.ntfy_test_button.click()
    assert spin_events(lambda: usage.ntfy_result.text() == "Sent")

    assert threads["worker"] is not threading.main_thread()
    assert threads["handler"] is threading.main_thread()


def test_closing_cancels_the_ntfy_poll(make_world):
    world = make_world(1)
    world.host.send_ntfy_test = lambda: (time.sleep(0.2), None)[1]
    world.controls.set_ntfy_value("topic", "mytopic")
    window = open_window(world)
    usage = window.tabs["usage"]
    usage.ntfy_test_button.click()
    window.close()
    time.sleep(0.4)
    QApplication.processEvents()
    assert usage.ntfy_result.text() == "Sending..."  # the late result was dropped


# ---- Theme ------------------------------------------------------------------

def card_pixel(world, x=150, y=5):
    return world.widget.grab().toImage().pixelColor(x, y)


def saved_theme(world):
    return T.load_theme(world.state_dir)


def test_opacity_writes_theme_json_and_the_card_shows_it(make_world):
    world = make_world(1)
    window = open_window(world)
    theme_tab = window.tabs["theme"]
    theme_tab.opacity.items["50"].click()
    assert saved_theme(world).card.opacity == 50
    assert world.widget.theme.card.opacity == 50
    assert abs(card_pixel(world).alpha() - T.card_alpha(50)) <= 1
    theme_tab.opacity.items["0"].click()
    assert card_pixel(world).alpha() == 1
    assert theme_tab.opacity.get() == "0"


def test_the_zero_percent_hint_shows_only_at_zero(make_world):
    world = make_world(1)
    window = open_window(world)
    window._show("theme")
    theme_tab = window.tabs["theme"]
    assert theme_tab.opacity_hint.text() == "You'll need to right-click where the widget is to get back here"
    assert not theme_tab.opacity_hint.isVisibleTo(window)
    assert theme_tab.opacity.items["0"].text() == "Invisible"
    theme_tab.opacity.items["0"].click()
    assert theme_tab.opacity_hint.isVisibleTo(window)
    theme_tab.opacity.items["100"].click()
    assert not theme_tab.opacity_hint.isVisibleTo(window)


def test_card_background_border_and_radius_reach_the_pixels(make_world, monkeypatch):
    world = make_world(1)
    window = open_window(world)
    theme_tab = window.tabs["theme"]
    monkeypatch.setattr(qs.QColorDialog, "getColor", staticmethod(lambda *a: QColor("#102030")))
    theme_tab.controls[("card", "background")].button.click()
    assert saved_theme(world).card.background == "#102030"
    assert card_pixel(world).name() == "#102030"

    theme_tab.controls[("card", "border_width")].setValue(2)
    theme_tab.controls[("card", "border_color")].button.click()
    assert saved_theme(world).card.border_width == 2
    assert saved_theme(world).card.border_color == "#102030"

    assert card_pixel(world, 0, 0).alpha() == 255
    theme_tab.controls[("card", "radius")].setValue(16)
    assert saved_theme(world).card.radius == 16
    assert card_pixel(world, 0, 0).alpha() < 255  # the rounded corner


def test_bar_colours_and_height_reach_the_bar(make_world, monkeypatch):
    world = make_world(1)
    pane = world.widget.panes[0]
    pane.render("working", 20, 20, "x", "y", "", None, None, False)
    window = open_window(world)
    theme_tab = window.tabs["theme"]
    monkeypatch.setattr(qs.QColorDialog, "getColor", staticmethod(lambda *a: QColor("#0000ff")))
    theme_tab.controls[("bars", "low")].button.click()
    assert saved_theme(world).bars.low == "#0000ff"
    bar = T.layout_card(world.widget.theme, "limits", "inside")["session_bar"]
    assert card_pixel(world, bar.x + 2, bar.y + 2).name() == "#0000ff"

    theme_tab.controls[("bars", "height")].setValue(10)
    assert world.widget.theme.bars.height == 10
    assert world.widget._layout_for("limits", "inside")["session_bar"].h == 10
    theme_tab.layout_choice.items["full_width"].click()
    theme_tab.shape.items["rounded"].click()
    assert (saved_theme(world).bars.layout, saved_theme(world).bars.shape) == ("full_width", "rounded")


def test_text_controls_write_the_theme(make_world, monkeypatch):
    world = make_world(1)
    window = open_window(world)
    theme_tab = window.tabs["theme"]
    monkeypatch.setattr(qs.QColorDialog, "getColor", staticmethod(lambda *a: QColor("#abcdef")))
    theme_tab.controls[("text", "foreground")].button.click()
    theme_tab.controls[("text", "dim")].button.click()
    theme_tab.show_toggles["cat_tags"]._clicked()
    theme_tab.show_toggles["percent"]._clicked()
    theme_tab.halo.items["on"].click()
    theme_tab.controls[("text", "halo_below")].setValue(40)
    saved = saved_theme(world).text
    assert (saved.foreground, saved.dim) == ("#abcdef", "#abcdef")
    assert saved.show == frozenset(T.TEXT_ELEMENTS) - {"cat_tags", "percent"}
    assert (saved.halo, saved.halo_below) == ("on", 40)
    assert world.widget.theme.text == saved
    theme_tab.show_toggles["cat_tags"]._clicked()
    assert "cat_tags" in saved_theme(world).text.show


def test_every_control_reflects_the_theme_after_a_refresh(make_world):
    theme = T.Theme(
        card=T.CardStyle(opacity=30, radius=5, border_width=1),
        bars=T.BarStyle(layout="full_width", shape="rounded", height=6, mid_at=40, high_at=70),
        text=T.TextStyle(show=frozenset({"percent"}), halo="auto", halo_below=20))
    world = make_world(1, theme=theme)
    theme_tab = open_window(world).tabs["theme"]
    assert theme_tab.opacity.get() == "30"
    assert theme_tab.controls[("card", "radius")].get() == 5
    assert theme_tab.layout_choice.get() == "full_width"
    assert theme_tab.shape.get() == "rounded"
    assert theme_tab.controls[("bars", "mid_at")].get() == 40
    assert theme_tab.halo.get() == "auto"
    assert theme_tab.show_toggles["percent"].get() is True
    assert theme_tab.show_toggles["reset"].get() is False


def test_inverted_thresholds_mark_both_entries_and_are_not_saved(make_world):
    world = make_world(1)
    theme_tab = open_window(world).tabs["theme"]
    theme_tab.controls[("bars", "mid_at")].setValue(90)  # high_at is 80
    assert theme_tab.entries[("bars", "mid_at")].property("error") is True
    assert theme_tab.entries[("bars", "high_at")].property("error") is True
    assert world.widget.theme.bars.mid_at == 50
    assert not (world.state_dir / T.THEME_FILENAME).exists()

    theme_tab.controls[("bars", "mid_at")].setValue(60)
    assert theme_tab.entries[("bars", "mid_at")].property("error") is False
    assert theme_tab.entries[("bars", "high_at")].property("error") is False
    assert saved_theme(world).bars.mid_at == 60


def test_a_refused_value_is_not_saved_and_the_old_one_stays(make_world):
    world = make_world(1)
    theme_tab = open_window(world).tabs["theme"]
    assert theme_tab._edit("card", "opacity", 55) is False  # not a multiple of 10
    assert theme_tab._edit("card", "background", "red") is False
    assert world.widget.theme == T.Theme(cat=T.CatStyle(wander=False))  # make_world's theme
    assert not (world.state_dir / T.THEME_FILENAME).exists()
    assert theme_tab._edit("card", "opacity", 60) is True
    assert saved_theme(world).card.opacity == 60


def test_reset_theme_writes_the_defaults(make_world):
    world = make_world(1, theme=T.Theme(card=T.CardStyle(opacity=20, radius=9)))
    window = open_window(world)
    theme_tab = window.tabs["theme"]
    theme_tab.reset_button.click()
    assert saved_theme(world) == T.Theme()
    assert world.widget.theme == T.Theme()
    assert theme_tab.opacity.get() == "100"
    assert theme_tab.controls[("card", "radius")].get() == 0


def test_a_theme_change_keeps_the_window_matching_its_layout(make_world):
    world = make_world(2)
    theme_tab = open_window(world).tabs["theme"]
    theme_tab.layout_choice.items["full_width"].click()
    expected = T.layout_window(world.widget.homes)
    assert (world.widget.width(), world.widget.height()) == expected.size


# ---- stacking and lifetime ---------------------------------------------------

def test_toggling_always_in_front_sets_and_clears_the_hint_on_an_open_window(make_world):
    world = make_world()
    window = open_window(world)
    hint = Qt.WindowType.WindowStaysOnTopHint
    assert window.windowFlags() & hint  # the widget starts in front

    world.host.toggle_always_on_top()
    assert not world.widget.always_on_top
    assert not window.windowFlags() & hint

    window.tabs["general"].toggles["aot"]._clicked()  # from General, back on
    assert world.widget.always_on_top
    assert window.windowFlags() & hint


def test_a_visible_window_is_shown_again_after_the_flag_change(make_world):
    world = make_world()
    window = open_window(world)
    assert window.isVisible()
    world.host.toggle_always_on_top()
    assert window.isVisible()


def test_a_window_opened_while_behind_has_no_hint(make_world):
    world = make_world()
    world.widget.set_always_on_top(False)
    window = open_window(world)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_a_closed_window_is_forgotten_by_the_host(make_world):
    world = make_world()
    window = open_window(world)
    assert window in world.host._windows
    window.close()
    assert window not in world.host._windows
    world.host.toggle_always_on_top()  # nothing to restyle, no error


def test_the_style_sheet_is_on_the_window_not_the_application(make_world, qapp):
    window = open_window(make_world())
    assert window.styleSheet() and not qapp.styleSheet()


def test_the_event_loop_survives_closing_settings_with_the_widget_shown(make_world, qapp):
    world = make_world()
    world.widget.show()
    state = {"reached": False}
    qapp.setQuitOnLastWindowClosed(False)  # what run_qt sets; the test_app launch test covers it
    window = open_window(world)

    def check():
        state["reached"] = True
        qapp.quit()

    QTimer.singleShot(30, window.close)
    QTimer.singleShot(200, check)
    net = QTimer()
    net.setSingleShot(True)
    net.timeout.connect(qapp.quit)
    net.start(3000)
    qapp.exec()
    net.stop()
    assert state["reached"]


def test_look_has_a_tail_dropdown_that_writes_through(make_world):
    world = make_world(2)
    window = open_window(world, 1)
    look = window.tabs["look"]
    assert look.tail.choices() == list(sprites.TAIL_STYLES)
    assert look.tail.get() == "plain"
    look.tail.menu.actions()[look.tail.choices().index("ringed")].trigger()
    assert world.store["k1"].tail == "ringed"
    assert world.store["k0"].tail == "plain"
    assert load_customization(world.state_dir)["k1"].tail == "ringed"
    assert world.widget.panes[1]._tail == "ringed"
    assert world.widget.panes[1].palette == effective_palette(world.store["k1"])
    assert look.tail.get() == "ringed"


def test_cat_shadow_toggle_writes_the_theme(make_world):
    world = make_world(1)
    theme_tab = open_window(world).tabs["theme"]
    assert theme_tab.shadow_toggle.get() is True
    theme_tab.shadow_toggle._clicked()
    assert saved_theme(world).cat.shadow is False
    assert world.widget.theme.cat.shadow is False
    theme_tab.shadow_toggle._clicked()
    assert saved_theme(world).cat.shadow is True


def test_cat_shadow_toggle_reflects_the_theme_after_a_refresh(make_world):
    world = make_world(1, theme=T.Theme(cat=T.CatStyle(shadow=False)))
    theme_tab = open_window(world).tabs["theme"]
    assert theme_tab.shadow_toggle.get() is False


def test_cat_wander_toggle_writes_the_theme_and_resizes_the_window(make_world):
    world = make_world(1, theme=T.Theme())  # wander is on by default
    theme_tab = open_window(world).tabs["theme"]
    assert theme_tab.wander_toggle.get() is True and world.widget.window_layout.margins[1] == 116
    theme_tab.wander_toggle._clicked()
    assert saved_theme(world).cat.wander is False
    assert world.widget.theme.cat.wander is False and world.widget.window_layout.margins[1] == 0
    theme_tab.wander_toggle._clicked()
    assert saved_theme(world).cat.wander is True and world.widget.window_layout.margins[1] == 116


def test_cat_wander_toggle_reflects_the_theme_after_a_refresh(make_world):
    world = make_world(1, theme=T.Theme(cat=T.CatStyle(wander=False)))
    theme_tab = open_window(world).tabs["theme"]
    assert theme_tab.wander_toggle.get() is False
