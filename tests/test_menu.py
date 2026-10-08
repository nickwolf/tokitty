import pytest

from tokitty.menu import build_menu

VIEW_MODES = [("cards", "Cards"), ("compact", "Compact")]


def _kwargs(**overrides):
    calls = {"refresh": 0, "toggle_aot": 0, "quit": 0}
    state = {"aot": True}
    base = dict(
        on_refresh=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
        always_on_top=lambda: state["aot"],
        on_toggle_always_on_top=lambda: calls.__setitem__("toggle_aot", calls["toggle_aot"] + 1),
        on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1),
    )
    base.update(overrides)
    return base, calls, state


def _labels(items):
    return [None if i.separator else i.label for i in items]


def test_minimal_structure():
    kwargs, _, _ = _kwargs()
    assert _labels(build_menu(**kwargs)) == ["Refresh now", "Always in front", None, "Exit"]


def test_full_structure_and_order():
    view = {"mode": "cards"}
    kwargs, _, _ = _kwargs(
        on_open_settings=lambda: None,
        view_modes=VIEW_MODES, current_view_mode=lambda: view["mode"], on_view_mode=lambda v: None,
        update_available_label=lambda: "Update to v0.3.0…", on_install_update=lambda: None,
    )
    assert _labels(build_menu(**kwargs)) == [
        "Update to v0.3.0…", "Refresh now", "View", "Always in front", None, "Settings…", None, "Exit"]


def test_settings_item_omitted_without_seam():
    kwargs, _, _ = _kwargs()
    assert "Settings…" not in _labels(build_menu(**kwargs))


def test_settings_item_runs_its_callback():
    opened = []
    kwargs, _, _ = _kwargs(on_open_settings=lambda: opened.append(1))
    {i.label: i for i in build_menu(**kwargs) if not i.separator}["Settings…"].action()
    assert opened == [1]


def test_action_wiring():
    kwargs, calls, _ = _kwargs()
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    items["Refresh now"].action()
    items["Always in front"].action()
    items["Exit"].action()
    assert (calls["refresh"], calls["toggle_aot"], calls["quit"]) == (1, 1, 1)


def test_always_on_front_checkbox_getter():
    kwargs, _, state = _kwargs()
    item = {i.label: i for i in build_menu(**kwargs) if not i.separator}["Always in front"]
    assert item.checkbox() is True
    state["aot"] = False
    assert item.checkbox() is False


def test_view_submenu_radio_and_actions():
    chosen = []
    view = {"mode": "compact"}
    kwargs, _, _ = _kwargs(view_modes=VIEW_MODES, current_view_mode=lambda: view["mode"],
                           on_view_mode=chosen.append)
    sub = {i.label: i for i in build_menu(**kwargs) if not i.separator}["View"].submenu
    assert [i.label for i in sub] == ["Cards", "Compact"]
    assert [i.label for i in sub if i.radio_selected()] == ["Compact"]
    view["mode"] = "cards"
    assert [i.label for i in sub if i.radio_selected()] == ["Cards"]
    sub[0].action()
    assert chosen == ["cards"]


def test_view_item_needs_all_its_seams():
    kwargs, _, _ = _kwargs(view_modes=VIEW_MODES, current_view_mode=lambda: "cards")
    assert "View" not in _labels(build_menu(**kwargs))


def _update_seams(label=lambda: "Update to v0.3.0…"):
    calls = {"install": 0}
    return dict(
        update_available_label=label,
        on_install_update=lambda: calls.__setitem__("install", calls["install"] + 1),
    ), calls


def test_update_item_sits_first():
    seams, calls = _update_seams()
    kwargs, _, _ = _kwargs(**seams)
    items = build_menu(**kwargs)
    assert items[0].label == "Update to v0.3.0…"
    items[0].action()
    assert calls["install"] == 1


def test_update_item_label_follows_the_getter():
    current = {"label": None}
    seams, _ = _update_seams(label=lambda: current["label"])
    kwargs, _, _ = _kwargs(**seams)
    item = build_menu(**kwargs)[0]
    assert item.dynamic_label() is None
    current["label"] = "Update to v0.4.0…"
    assert item.dynamic_label() == "Update to v0.4.0…"


@pytest.mark.parametrize("drop", [
    ["update_available_label"], ["on_install_update"],
    ["update_available_label", "on_install_update"],
])
def test_update_item_needs_both_its_seams(drop):
    seams, _ = _update_seams()
    for key in drop:
        del seams[key]
    kwargs, _, _ = _kwargs(**seams)
    labels = _labels(build_menu(**kwargs))
    assert labels[0] == "Refresh now"
    assert not any(label and label.startswith("Update to") for label in labels)
