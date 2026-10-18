"""The custom-colorway editor and the Look tab's colorway controls (#156):
real widget, QtHost and Controls from the make_world fixture. Dialogs are
built and driven directly, never exec'd; QColorDialog and QMessageBox are
patched."""
import pytest
from PySide6.QtGui import QColor

from tokitty import colorways, sprites
from tokitty.customize import Customization, load_customization
from tokitty.qt import colorway_editor as ce
from tokitty.qt.colorway_editor import ColorwayEditor
from tokitty.qt.settings import SettingsWindow
from tokitty.qt.tray import QtTray

pytestmark = pytest.mark.qt

SLATE = {"coat": "#7d8aa6", "shade": "#626e88", "mark": "#4a5468", "light": "#e0e5ef", "ear": "#f2a7b8"}


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    sprites.set_custom_colorways({})


def add(world, name="Slate", tones=None):
    cid = colorways.add_colorway(world.state_dir, name, tones or SLATE)
    world.host.on_colorways_changed()
    return cid


def select(world, cid, pane=0):
    world.host.on_customization_changed(pane, "colorway", cid)


def stored(world, pane=0):
    return load_customization(world.state_dir).get(f"k{pane}", world.store[f"k{pane}"]).colorway


# ---- palette_from_tones --------------------------------------------------------

@pytest.mark.parametrize("colorway", list(sprites.COLORWAYS))
def test_palette_from_tones_matches_resolve_palette(colorway):
    for pattern in sprites.PATTERNS:
        for tail in sprites.TAIL_STYLES:
            assert colorways.palette_from_tones(sprites.COLORWAYS[colorway], pattern, tail) \
                == sprites.resolve_palette(colorway, pattern, tail), (colorway, pattern, tail)


def test_palette_from_tones_matches_a_registered_custom(make_world):
    world = make_world(1)
    cid = add(world)
    for pattern in sprites.PATTERNS:
        assert colorways.palette_from_tones(SLATE, pattern, "ringed") \
            == sprites.resolve_palette(cid, pattern, "ringed")


# ---- Look tab -------------------------------------------------------------------

def test_dropdown_lists_builtins_then_custom_names_and_writes_the_id(make_world):
    world = make_world(1)
    cid = add(world)
    look = SettingsWindow.open(world.host, 0).tabs["look"]
    assert look.colorway.choices() == list(sprites.COLORWAYS) + ["Slate"]

    look.colorway.menu.actions()[-1].trigger()
    assert world.store["k0"].colorway == cid
    assert look.colorway.get() == "Slate"
    assert world.widget.panes[0]._colorway == cid

    look.colorway.menu.actions()[1].trigger()
    assert world.store["k0"].colorway == sprites.colorway_names()[1]
    assert look.colorway.get() == sprites.colorway_names()[1]


def test_a_dangling_colorway_id_shows_as_orange(make_world):
    world = make_world(customs=[Customization(colorway="custom-9")])
    look = SettingsWindow.open(world.host, 0).tabs["look"]
    assert look.colorway.get() == "orange"
    assert not look.edit_colorway_button.isEnabled()


def test_edit_is_enabled_only_for_a_custom_colorway(make_world):
    world = make_world(1)
    cid = add(world)
    window = SettingsWindow.open(world.host, 0)
    look = window.tabs["look"]
    assert look.new_colorway_button.isEnabled()
    assert not look.edit_colorway_button.isEnabled()
    assert not look.builtin_hint.isHidden()
    select(world, cid)
    world.host.notify_state_changed()
    assert look.edit_colorway_button.isEnabled()
    assert look.builtin_hint.isHidden()
    select(world, "gray")
    world.host.notify_state_changed()
    assert not look.edit_colorway_button.isEnabled()
    assert not look.builtin_hint.isHidden()


def test_new_and_edit_buttons_open_the_editor(make_world, monkeypatch):
    world = make_world(1)
    cid = add(world)
    select(world, cid)
    window = SettingsWindow.open(world.host, 0)
    look = window.tabs["look"]
    opened = []
    monkeypatch.setattr(ColorwayEditor, "exec", lambda self: opened.append((self.cid, self.parent())))
    look.new_colorway_button.click()
    look.edit_colorway_button.click()
    assert opened == [(None, window), (cid, window)]


def test_coat_rows_are_gone_and_card_and_bar_remain(make_world):
    look = SettingsWindow.open(make_world(1).host, 0).tabs["look"]
    assert list(look.color_rows) == ["card_bg", "bar_fill"]
    assert look.reset_button.text() == "Reset to preset"


# ---- editor ---------------------------------------------------------------------

def editor(world, cid=None, pane=0):
    return ColorwayEditor(None, world.host, pane, cid)


def test_new_seeds_from_the_panes_current_tones_and_a_free_name(make_world):
    world = make_world(customs=[Customization(colorway="gray", pattern="calico")])
    add(world, "My colorway")
    dlg = editor(world)
    assert dlg.windowTitle() == "New colorway"
    assert dlg.draft == sprites.COLORWAYS["gray"]
    assert dlg.name_entry.text() == "My colorway 2"
    assert dlg.delete_button is None
    assert dlg.preview.image == dlg.kit.sprite_preview(
        sprites.resolve_palette("gray", "calico"), cell=ce.PREVIEW_CELL).image
    assert list(dlg.strip) == list(sprites.PATTERNS)
    assert {w.toolTip() for w in dlg.strip.values()} == {p.replace("_", " ") for p in sprites.PATTERNS}


def test_edit_seeds_from_the_stored_entry(make_world):
    world = make_world(1)
    cid = add(world)
    dlg = editor(world, cid)
    assert dlg.windowTitle() == "Edit colorway"
    assert dlg.draft == SLATE
    assert dlg.name_entry.text() == "Slate"
    assert dlg.rows["mark"].value.text() == "#4A5468"
    assert dlg.delete_button is not None


def test_a_swatch_change_updates_the_preview_and_writes_nothing(make_world, monkeypatch):
    world = make_world(customs=[Customization(pattern="solid")])
    dlg = editor(world)
    before = dlg.preview.image.copy()
    asked = []
    monkeypatch.setattr(ce.QColorDialog, "getColor",
                        staticmethod(lambda initial, parent, title: (asked.append((initial.name(), title)),
                                                                     QColor("#123456"))[1]))
    dlg.rows["coat"].button.click()
    assert asked == [(sprites.COLORWAYS["orange"]["coat"], "Coat")]
    assert dlg.draft["coat"] == "#123456"
    assert dlg.rows["coat"].value.text() == "#123456".upper()
    assert dlg.preview.image != before
    assert dlg._palette("solid")["o"] == "#123456"
    assert dlg.strip["tabby"].image != before
    assert not (world.state_dir / colorways.COLORWAYS_FILENAME).exists()


def test_a_cancelled_color_dialog_changes_nothing(make_world, monkeypatch):
    world = make_world(1)
    dlg = editor(world)
    draft = dict(dlg.draft)
    monkeypatch.setattr(ce.QColorDialog, "getColor", staticmethod(lambda *a: QColor()))
    dlg.rows["shade"].button.click()
    assert dlg.draft == draft


def test_derive_fills_shade_mark_and_light_from_the_coat(make_world):
    world = make_world(1)
    dlg = editor(world)
    dlg.draft.update(coat="#7d8aa6", ear="#010203")
    dlg.derive_button.click()
    assert {k: dlg.draft[k] for k in ("shade", "mark", "light")} == colorways.derive_from_coat("#7d8aa6")
    assert dlg.draft["coat"] == "#7d8aa6" and dlg.draft["ear"] == "#010203"
    assert dlg.rows["light"].value.text() == dlg.draft["light"].upper()


@pytest.mark.parametrize("name,expect", [
    ("", "Give the colorway a name."),
    ("Gray", "That name belongs to a built-in colorway."),
    ("slate", "Another colorway already has that name."),
])
def test_saving_a_bad_name_shows_the_message_and_writes_nothing(make_world, name, expect):
    world = make_world(1)
    add(world)
    before = (world.state_dir / colorways.COLORWAYS_FILENAME).read_text()
    dlg = editor(world)
    dlg.name_entry.setText(name)
    dlg.save_button.click()
    assert dlg.error.text() == expect
    assert dlg.result() == 0
    assert (world.state_dir / colorways.COLORWAYS_FILENAME).read_text() == before
    assert world.store["k0"].colorway == "orange"
    dlg.name_entry.textEdited.emit("x")
    assert dlg.error.text() == ""


def test_name_entry_is_capped(make_world):
    dlg = editor(make_world(1))
    assert dlg.name_entry.maxLength() == colorways.MAX_NAME_LEN


def test_save_new_creates_the_colorway_and_selects_it(make_world, monkeypatch):
    world = make_world(2)
    window = SettingsWindow.open(world.host, 1)
    dlg = editor(world, pane=1)
    dlg.draft.update(SLATE)
    dlg.name_entry.setText("  Slate ")
    dlg.save_button.click()
    assert dlg.result() == 1
    assert colorways.load_colorways(world.state_dir) == {"custom-1": {"name": "Slate", **SLATE}}
    assert stored(world, 1) == "custom-1" and world.widget.panes[1]._colorway == "custom-1"
    assert world.widget.panes[1].palette == sprites.resolve_palette("custom-1", "tabby", "plain")
    assert stored(world, 0) == "orange"
    look = window.tabs["look"]
    assert look.colorway.get() == "Slate" and look.edit_colorway_button.isEnabled()


def test_save_edit_retones_and_the_pane_palette_follows(make_world):
    world = make_world(1)
    cid = add(world)
    select(world, cid)
    old = dict(world.widget.panes[0].palette)
    window = SettingsWindow.open(world.host, 0)
    dlg = editor(world, cid)
    dlg.draft["coat"] = "#aa0000"
    dlg.name_entry.setText("Slate 2")
    dlg.save_button.click()
    assert colorways.load_colorways(world.state_dir)[cid]["name"] == "Slate 2"
    assert colorways.load_colorways(world.state_dir)[cid]["coat"] == "#aa0000"
    assert world.widget.panes[0].palette != old
    assert world.widget.panes[0].palette["o"] == "#aa0000"
    assert stored(world) == cid
    assert window.tabs["look"].colorway.get() == "Slate 2"


def test_editing_keeps_its_own_name(make_world):
    world = make_world(1)
    cid = add(world)
    dlg = editor(world, cid)
    dlg.save_button.click()
    assert dlg.result() == 1 and dlg.error.text() == ""


def test_cancel_writes_nothing(make_world):
    world = make_world(1)
    dlg = editor(world)
    dlg.name_entry.setText("Nope")
    dlg.cancel_button.click()
    assert dlg.result() == 0
    assert not (world.state_dir / colorways.COLORWAYS_FILENAME).exists()
    assert world.store["k0"].colorway == "orange"


def test_delete_confirms_naming_the_users_and_moves_them_to_orange(make_world, monkeypatch):
    world = make_world(customs=[Customization(label="Alpha"), Customization(label="Beta"),
                                Customization()])
    cid = add(world)
    select(world, cid, 0)
    select(world, cid, 2)
    asked = []
    monkeypatch.setattr(ce.QMessageBox, "question",
                        staticmethod(lambda parent, title, msg, *rest: (asked.append(msg),
                                                                        ce.QMessageBox.StandardButton.Yes)[1]))
    dlg = editor(world, cid)
    dlg.delete_button.click()
    assert len(asked) == 1
    assert "Slate" in asked[0] and "Alpha" in asked[0] and "Cat 3" in asked[0]
    assert "Beta" not in asked[0] and "orange" in asked[0]
    assert dlg.result() == 1
    assert colorways.load_colorways(world.state_dir) == {}
    assert [stored(world, i) for i in range(3)] == ["orange"] * 3
    assert world.widget.panes[0]._colorway == "orange"
    assert sprites.colorway_names() == list(sprites.COLORWAYS)


def test_declining_the_delete_confirm_keeps_everything(make_world, monkeypatch):
    world = make_world(1)
    cid = add(world)
    select(world, cid)
    monkeypatch.setattr(ce.QMessageBox, "question",
                        staticmethod(lambda *a: ce.QMessageBox.StandardButton.No))
    dlg = editor(world, cid)
    dlg.delete_button.click()
    assert dlg.result() == 0
    assert cid in colorways.load_colorways(world.state_dir)
    assert stored(world) == cid


# ---- tray -----------------------------------------------------------------------

def test_tray_set_look_repaints_the_icon_only_when_the_look_changes(qapp):
    class Icon:
        def __init__(self):
            self.icons = []

        def setIcon(self, icon):
            self.icons.append(icon)

        def setToolTip(self, _t): pass
        def setContextMenu(self, _m): pass
        def show(self): pass

    fake = Icon()
    tray = QtTray(lambda: [], None, "orange", "tabby", icon_factory=lambda: fake,
                  tray_available=lambda: True)
    tray.start()
    assert len(fake.icons) == 1
    first = fake.icons[0].pixmap(56, 56).toImage()
    tray.set_look("orange", "tabby", "plain")
    assert len(fake.icons) == 1
    tray.set_look("gray", "tabby", "plain")
    assert len(fake.icons) == 2
    assert fake.icons[1].pixmap(56, 56).toImage() != first

    sprites.set_custom_colorways({"custom-1": SLATE})
    tray.set_look("custom-1", "tabby", "plain")
    assert len(fake.icons) == 3
    sprites.set_custom_colorways({"custom-1": {**SLATE, "coat": "#aa0000"}})
    tray.set_look("custom-1", "tabby", "plain")
    assert len(fake.icons) == 4  # same id, new tones
