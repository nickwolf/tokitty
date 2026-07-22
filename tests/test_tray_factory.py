"""Headless coverage for tray.py's default factory functions.

`_default_icon_factory` and `_default_image_factory` are the two production
seams every other tray test replaces with fakes (see test_tray.py) -- so
neither is ever otherwise exercised, and a regression in the MenuItem ->
pystray.MenuItem translation would only surface on a manual Windows run.

This file inverts that: it fakes the `pystray` module itself (so no real
backend or display is needed -- real pystray can't even import headlessly,
it fails probing for a Gtk/appindicator backend) and calls the *real*
factories, locking in the mapping and the rendered tray-icon image.
"""
import sys
import types

from tokitty.menu import build_menu
from tokitty.sprite_raster import raster_rgba
from tokitty.sprites import get_frames, get_palette
from tokitty.tray import TRAY_ICON_SCALE, _default_icon_factory, _default_image_factory


class FakeMenuItem:
    """Mirrors pystray.MenuItem(text, action, checked=None, radio=False)."""

    def __init__(self, text, action=None, checked=None, radio=False):
        self.text = text
        self.action = action
        self.checked = checked
        self.radio = radio


class FakeMenu:
    """Mirrors pystray.Menu(*entries) plus the Menu.SEPARATOR sentinel the
    real code references directly as `pystray.Menu.SEPARATOR`."""

    SEPARATOR = object()

    def __init__(self, *entries):
        self.entries = entries


class FakeIcon:
    """Mirrors pystray.Icon(name, icon, title, menu)."""

    def __init__(self, name, icon, title, menu):
        self.name = name
        self.icon = icon
        self.title = title
        self.menu = menu


def _fake_pystray_module():
    mod = types.ModuleType("pystray")
    mod.MenuItem = FakeMenuItem
    mod.Menu = FakeMenu
    mod.Icon = FakeIcon
    return mod


def _make_wrap():
    """A wrap() that tags what it wraps and records every call, so wrapped
    actions are identifiable and call order/identity can be asserted."""
    calls = []

    def wrap(action):
        calls.append(action)
        return ("WRAPPED", action)

    return wrap, calls


def _noop():
    pass


def _shadow_model():
    """A real MenuItem model from build_menu(), with the tray seam supplied
    (so "Show tray icon" is present) and backed by plain-dict shadow state
    so getters can be driven *after* the model -- and the icon built from
    it -- already exist. That ordering is what would catch a late-binding
    closure bug in to_entries."""
    state = {"coat": "gray_tabby", "aot": True, "tray": True}

    model = build_menu(
        coats=["orange_tabby", "gray_tabby", "black"],
        current_coat=lambda: state["coat"],
        on_coat=lambda name: None,
        on_customize=_noop,
        on_rename=_noop,
        on_refresh=_noop,
        always_on_top=lambda: state["aot"],
        on_toggle_always_on_top=_noop,
        on_quit=_noop,
        tray_enabled=lambda: state["tray"],
        on_toggle_tray=_noop,
    )
    return model, state


def _build_icon(monkeypatch):
    monkeypatch.setitem(sys.modules, "pystray", _fake_pystray_module())
    model, state = _shadow_model()
    wrap, wrap_calls = _make_wrap()
    image = object()
    icon = _default_icon_factory(image, model, wrap, "Tokitty")
    return icon, model, state, wrap_calls, image


def _entries_by_label(model, entries):
    return {item.label: entry for item, entry in zip(model, entries) if not item.separator}


def test_icon_factory_maps_menu_model_to_pystray_entries(monkeypatch):
    icon, model, _state, wrap_calls, image = _build_icon(monkeypatch)

    assert isinstance(icon, FakeIcon)
    assert icon.name == "tokitty"
    assert icon.icon is image
    assert icon.title == "Tokitty"
    assert isinstance(icon.menu, FakeMenu)

    entries = icon.menu.entries
    assert len(entries) == len(model)

    non_sep_labels = [item.label for item in model if not item.separator]
    assert non_sep_labels == [
        "Coat", "Customize…", "Rename…", "Refresh now",
        "Always in front", "Show tray icon", "Exit",
    ]

    items_by_label = {item.label: item for item in model if not item.separator}
    entries_by_label = _entries_by_label(model, entries)

    for item, entry in zip(model, entries):
        if item.separator:
            assert entry is FakeMenu.SEPARATOR
            continue
        assert entry.text == item.label
        if item.submenu is not None:
            # The submenu parent ("Coat") is never itself wrapped: its
            # 2nd positional is the nested Menu, not wrap(it.action).
            assert isinstance(entry.action, FakeMenu)
            assert entry.checked is None
            assert entry.radio is False
        else:
            assert entry.action == ("WRAPPED", item.action)
            if item.radio_selected is not None:
                assert entry.radio is True
                assert callable(entry.checked)
            elif item.checkbox is not None:
                assert entry.radio is False
                assert callable(entry.checked)
            else:
                # Plain command entries: Customize…, Rename…, Refresh now, Exit.
                assert entry.radio is False
                assert entry.checked is None

    # Coat submenu: each coat is a radio MenuItem with a checked getter.
    coat_item = items_by_label["Coat"]
    coat_entries = entries_by_label["Coat"].action.entries
    assert len(coat_entries) == len(coat_item.submenu) == 3
    for entry, sub_item in zip(coat_entries, coat_item.submenu):
        assert entry.text == sub_item.label
        assert entry.radio is True
        assert entry.action == ("WRAPPED", sub_item.action)
        assert callable(entry.checked)

    # wrap() fires for every actionable entry, in traversal order (coat
    # submenu resolved inline before its parent's siblings), and never for
    # a separator or the Coat submenu parent (whose own action is None).
    expected_wrap_order = [c.action for c in coat_item.submenu] + [
        items_by_label["Customize…"].action,
        items_by_label["Rename…"].action,
        items_by_label["Refresh now"].action,
        items_by_label["Always in front"].action,
        items_by_label["Show tray icon"].action,
        items_by_label["Exit"].action,
    ]
    assert wrap_calls == expected_wrap_order
    assert None not in wrap_calls


def test_icon_factory_checked_getters_track_live_shadow_state(monkeypatch):
    icon, model, state, _wrap_calls, _image = _build_icon(monkeypatch)
    entries = icon.menu.entries
    items_by_label = {item.label: item for item in model if not item.separator}
    entries_by_label = _entries_by_label(model, entries)

    coat_entries = entries_by_label["Coat"].action.entries
    coat_items = items_by_label["Coat"].submenu

    dummy_item = object()  # pystray calls checked() with one positional MenuItem.

    def selected_coats():
        return {sub.label for entry, sub in zip(coat_entries, coat_items)
                 if entry.checked(dummy_item)}

    assert selected_coats() == {"gray_tabby"}
    assert entries_by_label["Always in front"].checked(dummy_item) is True
    assert entries_by_label["Show tray icon"].checked(dummy_item) is True

    # Flip the shadow state after the model/icon were already built: a
    # late-binding closure bug would freeze these on the values captured at
    # build time instead of re-reading state on every call.
    state["coat"] = "black"
    state["aot"] = False
    state["tray"] = False

    assert selected_coats() == {"black"}
    assert entries_by_label["Always in front"].checked(dummy_item) is False
    assert entries_by_label["Show tray icon"].checked(dummy_item) is False


def test_image_factory_produces_square_transparent_rgba_icon():
    # Derive the expected side from the same lower-level calls
    # _default_image_factory makes, rather than hardcoding sprite pixels.
    frame = get_frames("content")[0]
    width, height, _raw = raster_rgba(frame, get_palette("orange_tabby"), TRAY_ICON_SCALE)
    expected_side = max(width, height)

    img = _default_image_factory("orange_tabby")

    assert img.mode == "RGBA"
    assert img.size == (expected_side, expected_side)
    assert img.size[0] == img.size[1]
    # A real cat on a transparent background: both fully-transparent and
    # fully-opaque pixels must be present in the alpha channel.
    assert img.getchannel("A").getextrema() == (0, 255)
