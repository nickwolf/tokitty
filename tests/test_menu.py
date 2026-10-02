import pytest

from tokitty.menu import build_menu


def _kwargs(**overrides):
    calls = {"colorway": [], "pattern": [], "customize": 0, "rename": 0,
             "refresh": 0, "toggle_aot": 0, "quit": 0, "toggle_tray": 0}
    state = {"colorway": "gray", "pattern": "tabby", "aot": True, "tray": True}
    base = dict(
        colorways=["orange", "gray", "black"],
        patterns=["solid", "tabby", "calico"],
        current_colorway=lambda: state["colorway"],
        current_pattern=lambda: state["pattern"],
        on_colorway=lambda c: calls["colorway"].append(c),
        on_pattern=lambda p: calls["pattern"].append(p),
        on_customize=lambda: calls.__setitem__("customize", calls["customize"] + 1),
        on_rename=lambda: calls.__setitem__("rename", calls["rename"] + 1),
        on_refresh=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
        always_on_top=lambda: state["aot"],
        on_toggle_always_on_top=lambda: calls.__setitem__("toggle_aot", calls["toggle_aot"] + 1),
        on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1),
    )
    base.update(overrides)
    return base, calls, state


def test_structure_and_labels():
    kwargs, _, _ = _kwargs()
    items = build_menu(**kwargs)
    labels = [i.label for i in items if not i.separator]
    assert labels == ["Colorway", "Pattern", "Customize…", "Rename…",
                      "Refresh now", "Always in front", "Exit"]
    assert [c.label for c in items[0].submenu] == ["orange", "gray", "black"]
    assert [p.label for p in items[1].submenu] == ["solid", "tabby", "calico"]


def test_radio_reflects_current_selection():
    kwargs, _, state = _kwargs()
    items = build_menu(**kwargs)
    assert [c.label for c in items[0].submenu if c.radio_selected()] == ["gray"]
    assert [p.label for p in items[1].submenu if p.radio_selected()] == ["tabby"]
    state["pattern"] = "calico"
    assert [p.label for p in build_menu(**kwargs)[1].submenu if p.radio_selected()] == ["calico"]


def test_action_wiring():
    kwargs, calls, _ = _kwargs()
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    items["Customize…"].action()
    items["Rename…"].action()
    items["Refresh now"].action()
    items["Always in front"].action()
    items["Exit"].action()
    assert (calls["customize"], calls["rename"], calls["refresh"],
            calls["toggle_aot"], calls["quit"]) == (1, 1, 1, 1, 1)
    build_menu(**kwargs)[0].submenu[1].action()   # colorway "gray"
    build_menu(**kwargs)[1].submenu[2].action()   # pattern "calico"
    assert calls["colorway"] == ["gray"]
    assert calls["pattern"] == ["calico"]


def test_always_on_front_checkbox_getter():
    kwargs, _, state = _kwargs()
    item = {i.label: i for i in build_menu(**kwargs) if not i.separator}["Always in front"]
    assert item.checkbox() is True
    state["aot"] = False
    assert item.checkbox() is False


def test_tray_item_absent_without_seam():
    kwargs, _, _ = _kwargs()
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Show tray icon" not in labels


def test_tray_item_present_with_seam():
    kwargs, calls, state = _kwargs(
        tray_enabled=lambda: state["tray"],
        on_toggle_tray=lambda: calls.__setitem__("toggle_tray", calls["toggle_tray"] + 1),
    )
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    assert "Show tray icon" in items
    assert items["Show tray icon"].checkbox() is True
    items["Show tray icon"].action()
    assert calls["toggle_tray"] == 1


def test_randomize_and_surprise_absent_without_seams():
    kwargs, _, _ = _kwargs()
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Randomize" not in labels
    assert "Surprise me" not in labels


def test_randomize_and_surprise_present_with_seams():
    kwargs, calls, state = _kwargs(
        on_randomize=lambda: calls.__setitem__("rand", calls.get("rand", 0) + 1),
        surprise_me=lambda: state.get("surprise", True),
        on_toggle_surprise=lambda: calls.__setitem__("tsurp", calls.get("tsurp", 0) + 1),
    )
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    assert "Randomize" in items and "Surprise me" in items
    items["Randomize"].action()
    assert calls["rand"] == 1
    assert items["Surprise me"].checkbox() is True
    items["Surprise me"].action()
    assert calls["tsurp"] == 1


def test_accounts_item_omitted_when_callback_not_given():
    kwargs, _, _ = _kwargs()
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Accounts…" not in labels


def test_accounts_item_present_when_callback_given():
    kwargs, calls, _ = _kwargs(
        on_open_accounts=lambda: calls.__setitem__("accounts", calls.get("accounts", 0) + 1),
    )
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert labels.count("Accounts…") == 1
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    items["Accounts…"].action()
    assert calls["accounts"] == 1


def test_accounts_item_positioned_after_rename_before_refresh_separator():
    kwargs, _, _ = _kwargs(on_open_accounts=lambda: None)
    items = build_menu(**kwargs)
    labels = [None if i.separator else i.label for i in items]
    idx = labels.index("Accounts…")
    assert labels[idx - 1] == "Rename…"
    assert labels[idx + 1] is None  # separator preceding "Refresh now"
    assert labels[idx + 2] == "Refresh now"


def test_autostart_item_absent_without_seam():
    kwargs, _, _ = _kwargs()
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Start at login" not in labels


def test_autostart_item_present_with_seam():
    kwargs, calls, state = _kwargs(
        autostart_enabled=lambda: state.get("autostart", True),
        on_toggle_autostart=lambda: calls.__setitem__("toggle_autostart", calls.get("toggle_autostart", 0) + 1),
    )
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    assert "Start at login" in items
    assert items["Start at login"].checkbox() is True
    items["Start at login"].action()
    assert calls["toggle_autostart"] == 1


def test_autostart_item_positioned_between_tray_and_surprise():
    kwargs, _, state = _kwargs(
        tray_enabled=lambda: state["tray"],
        on_toggle_tray=lambda: None,
        autostart_enabled=lambda: True,
        on_toggle_autostart=lambda: None,
        surprise_me=lambda: True,
        on_toggle_surprise=lambda: None,
    )
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert labels.index("Show tray icon") < labels.index("Start at login") < labels.index("Surprise me")


def test_streamdock_menu_item_states():
    from tokitty.menu import INSTALL_STREAMDOCK_COMMAND, streamdock_menu_item

    copied = []
    on = streamdock_menu_item("connected", lambda: copied.append(1))
    assert on.label == "Stream Dock"
    assert [(i.label, i.enabled) for i in on.submenu] == [("Connected", False)]
    off = streamdock_menu_item("not_connected", lambda: None)
    assert [i.label for i in off.submenu] == ["Not connected"]
    missing = streamdock_menu_item("not_installed", lambda: copied.append(1))
    assert [(i.label, i.enabled) for i in missing.submenu][0] == ("Not installed", False)
    assert INSTALL_STREAMDOCK_COMMAND in missing.submenu[1].label
    missing.submenu[1].action()
    assert copied == [1]


def test_streamdock_presets_item_only_when_installed():
    from tokitty.menu import streamdock_menu_item

    opened = []
    for state in ("connected", "not_connected"):
        item = streamdock_menu_item(state, lambda: None, lambda: opened.append(state))
        assert [i.label for i in item.submenu][-1] == "New-session presets…"
        item.submenu[-1].action()
    assert opened == ["connected", "not_connected"]
    missing = streamdock_menu_item("not_installed", lambda: None, lambda: None)
    assert "New-session presets…" not in [i.label for i in missing.submenu]
    assert "New-session presets…" not in [i.label for i in streamdock_menu_item("connected", lambda: None).submenu]


def _update_seams(label=lambda: "Update to v0.3.0…"):
    calls = {"install": 0, "check": 0, "toggle": 0}
    state = {"auto": True}
    return dict(
        update_available_label=label,
        on_install_update=lambda: calls.__setitem__("install", calls["install"] + 1),
        on_check_updates=lambda: calls.__setitem__("check", calls["check"] + 1),
        update_check_enabled=lambda: state["auto"],
        on_toggle_update_check=lambda: calls.__setitem__("toggle", calls["toggle"] + 1),
    ), calls, state


def test_update_items_sit_first_and_above_the_exit_separator():
    seams, calls, state = _update_seams()
    kwargs, _, _ = _kwargs(**seams)
    items = build_menu(**kwargs)
    labels = [i.label for i in items if not i.separator]
    assert labels == ["Update to v0.3.0…", "Colorway", "Pattern", "Customize…", "Rename…", "Refresh now",
                      "Always in front", "Check for updates", "Check for updates automatically", "Exit"]
    assert items[-2].separator and items[-1].label == "Exit"
    by_label = {i.label: i for i in items if not i.separator}
    by_label["Update to v0.3.0…"].action()
    by_label["Check for updates"].action()
    by_label["Check for updates automatically"].action()
    assert calls == {"install": 1, "check": 1, "toggle": 1}
    assert by_label["Check for updates automatically"].checkbox() is True
    state["auto"] = False
    assert by_label["Check for updates automatically"].checkbox() is False


def test_update_item_label_follows_the_getter():
    current = {"label": None}
    seams, _, _ = _update_seams(label=lambda: current["label"])
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
    seams, _, _ = _update_seams()
    for key in drop:
        del seams[key]
    kwargs, _, _ = _kwargs(**seams)
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert labels[0] == "Colorway"
    assert not any(label.startswith("Update to") for label in labels)


def test_check_items_are_independent_and_the_checkbox_needs_both_halves():
    seams, _, _ = _update_seams()
    kwargs, _, _ = _kwargs(on_check_updates=seams["on_check_updates"])
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Check for updates" in labels and "Check for updates automatically" not in labels
    kwargs, _, _ = _kwargs(on_toggle_update_check=seams["on_toggle_update_check"])
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Check for updates automatically" not in labels and "Check for updates" not in labels
