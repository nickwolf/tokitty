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
