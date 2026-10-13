"""QtHost: the seams the Qt tabs read, over a real widget and Controls."""
import pytest
from PySide6.QtWidgets import QMessageBox, QWidget

from tokitty import theme as T
from tokitty.customize import Customization, load_customization

pytestmark = pytest.mark.qt


def test_attach_exposes_every_controls_seam_under_its_own_name(make_world):
    world = make_world(1)
    for name, value in world.controls.seams().items():
        assert getattr(world.host, name) is value
    assert world.host.on_toggle_ntfy is world.controls.on_toggle_ntfy
    assert world.host.panes is world.widget.panes


def test_tray_update_and_opener_seams_are_none(make_world):
    host = make_world(1).host
    for name in ("tray_refresh", "on_toggle_tray", "tray_enabled", "update_check_enabled", "on_toggle_update_check",
                 "on_check_updates", "update_available_label", "on_install_update", "on_open_accounts",
                 "on_edit_streamdock_presets"):
        assert getattr(host, name) is None


def test_notify_state_changed_refreshes_the_tray_before_settings(make_world):
    host = make_world(1).host
    order = []
    host.tray_refresh = lambda: order.append("tray")
    host.settings_refresh = lambda: order.append("settings")
    host.notify_state_changed()
    assert order == ["tray", "settings"]


def test_notify_state_changed_works_with_neither_seam_set(make_world):
    make_world(1).host.notify_state_changed()


def test_always_on_top_is_a_method_over_the_widgets_property(make_world):
    world = make_world(1)
    assert world.host.always_on_top() is True
    world.host.toggle_always_on_top()
    assert world.widget.always_on_top is False and world.host.always_on_top() is False


def test_set_theme_saves_theme_json_and_applies_it(make_world):
    world = make_world(1)
    theme = T.Theme(card=T.CardStyle(opacity=70))
    world.host.set_theme(theme)
    assert world.host.theme() == theme
    assert world.widget.theme == theme
    assert T.load_theme(world.state_dir) == theme


def test_set_theme_still_applies_when_the_save_fails(make_world, monkeypatch):
    world = make_world(1)

    def fail(state_dir, theme):
        raise OSError("disk full")

    monkeypatch.setattr(T, "save_theme", fail)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a)))
    world.host.set_theme(T.Theme(card=T.CardStyle(opacity=40)))
    assert world.widget.theme.card.opacity == 40
    assert warned and "disk full" in warned[0][2]


def test_pane_color_reads_the_palette_and_overrides(make_world):
    world = make_world(1, customs=[Customization(overrides={"coat_base": "#010203", "bar_fill": "#0a0b0c"})])
    pane = world.widget.panes[0]
    assert world.host.pane_color(0, "coat_base") == pane.palette["o"]
    assert world.host.pane_color(0, "coat_shade") == pane.palette["O"]
    assert world.host.pane_color(0, "bar_fill") == "#0a0b0c"
    assert world.host.pane_color(0, "card_bg") == T.CardStyle.background


def test_on_layout_changed_replaces_the_customization_and_rebuilds_on_a_new_home(make_world):
    world = make_world(2)
    world.widget.move(300, 300)
    custom = Customization(colorway="gray", cat_home="on_top")
    world.host.on_layout_changed(0, custom)
    assert world.widget.panes[0].customization is custom
    assert world.widget.homes == ["on_top", "inside"]
    assert world.widget.height() == T.layout_window(["on_top", "inside"]).size[1]

    size = world.widget.size()
    world.host.on_layout_changed(1, Customization(colorway="gray"))
    assert world.widget.size() == size  # no home moved, nothing to rebuild


def test_a_customization_save_leaves_the_shown_label_to_controls(make_world):
    world = make_world(1)
    pane = world.widget.panes[0]
    pane.set_appearance(label="Shown")
    world.controls.on_customization_changed(0, "colorway", "gray")
    assert pane._label == "Shown"
    world.controls.on_customization_changed(0, "label", "Renamed")
    assert pane._label == "Renamed"
    assert load_customization(world.state_dir)["k0"].label == "Renamed"


def test_cat_home_survives_a_colour_change_and_a_reset(make_world):
    world = make_world(1)
    world.controls.on_customization_changed(0, "cat_home", "inside_right")
    world.controls.on_customization_changed(0, "coat_base", "#112233")
    assert world.widget.panes[0].customization.cat_home == "inside_right"
    world.controls.on_customization_changed(0, "reset", None)
    assert world.widget.panes[0].customization.overrides == {}
    assert world.widget.panes[0].customization.cat_home == "inside_right"
    assert world.widget.homes == ["inside_right"]


def test_notify_state_changed_calls_the_open_refresh_only(make_world):
    host = make_world(1).host
    host.notify_state_changed()  # nothing open: no error
    seen = []
    host.settings_refresh = lambda: seen.append(1)
    host.notify_state_changed()
    assert seen == [1]


def test_warn_is_parented_to_the_asking_window(make_world, monkeypatch):
    world = make_world(1)
    parents = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, *a: parents.append(parent)))
    world.host.warn("t", "x")
    assert parents == [world.widget]
    window = QWidget()
    world.host.register_window(window)
    window.show()
    world.host.warn("t", "x")
    assert parents[-1] is window
    window.close()
