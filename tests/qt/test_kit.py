"""The Qt widget kit: controls fire on_change on a click and never on set()."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from tokitty import sprites
from tokitty.qt.kit import Kit, Spin, qfont, sprite_image
from tokitty.settings_style import FONT_MEDIUM, SURFACE
from tokitty.sprite_raster import raster_rgba

pytestmark = pytest.mark.qt


@pytest.fixture
def kit(qapp):
    return Kit()


def test_px_is_the_identity():
    assert [Kit.px(n) for n in (0, 1, 7, 640)] == [0, 1, 7, 640]


def test_font_tuples_become_qfonts(qapp):
    font = qfont(FONT_MEDIUM)
    assert (font.family(), font.pointSize(), font.bold()) == ("Segoe UI", 9, True)
    assert not qfont(("Consolas", 8)).bold()


def test_toggle_fires_on_click_and_not_on_set(kit):
    seen = []
    toggle = kit.toggle("Title", detail="Detail", on_change=seen.append)
    toggle.resize(300, 40)
    toggle.set(True)
    toggle.set(False)
    toggle.set(True)
    assert seen == [] and toggle.get() is True

    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert seen == [False] and toggle.get() is False
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert seen == [False, True]


def test_disabled_toggle_ignores_clicks(kit):
    seen = []
    toggle = kit.toggle("Title", compact=True, on_change=seen.append)
    toggle.resize(300, 40)
    toggle.set_enabled(False)
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert seen == [] and toggle.get() is False
    toggle.set_enabled(True)
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert seen == [True]


def test_segmented_fires_on_click_and_not_on_set(kit):
    seen = []
    seg = kit.segmented([("a", "A"), ("b", "B")], on_change=seen.append)
    seg.set("a")
    assert seen == [] and seg.get() == "a"
    assert seg.items["a"].property("active") is True
    assert seg.items["b"].property("active") is False

    seg.items["b"].click()
    assert seen == ["b"] and seg.get() == "b"
    assert seg.items["b"].property("active") is True


def test_dropdown_fires_on_a_menu_pick_and_not_on_set(kit):
    seen = []
    drop = kit.dropdown(["one", "two"], on_change=seen.append)
    drop.set("one")
    drop.set_choices(["one", "two", "three"])
    assert seen == [] and drop.get() == "one" and drop.text() == "one"
    assert drop.choices() == ["one", "two", "three"]

    drop.menu.actions()[2].trigger()
    assert seen == ["three"] and drop.get() == "three"


def test_dropdown_button_opens_its_menu(kit, qapp):
    drop = kit.dropdown(["one"])
    drop.show()
    drop.click()
    assert drop.menu.isVisible()
    drop.menu.hide()


def test_spin_fires_on_a_change_and_not_on_set(kit):
    seen = []
    holder, spin = kit.spin(4, 10, on_change=seen.append)
    assert isinstance(spin, Spin)
    spin.set(7)
    assert seen == [] and spin.get() == 7
    spin.setValue(9)
    assert seen == [9]
    assert (spin.minimum(), spin.maximum()) == (4, 10)


def test_entry_error_toggles_a_property_the_sheet_reads(kit):
    holder, entry = kit.entry(width=9)
    assert holder.property("error") is False
    kit.set_entry_error(holder, True)
    assert holder.property("error") is True
    kit.set_entry_error(holder, False)
    assert holder.property("error") is False
    sheet = Kit.style_sheet()
    assert 'QFrame[kit="entry-holder"][error="true"]' in sheet


def test_error_property_reaches_the_painted_colour(kit):
    window = QWidget()
    kit.style_window(window)
    layout = QVBoxLayout(window)
    holder, _entry = kit.entry()
    layout.addWidget(holder)
    window.resize(200, 60)
    window.show()
    QApplication.processEvents()

    def corner():
        image = window.grab().toImage()
        pos = holder.mapTo(window, QPoint(0, 0))
        return image.pixelColor(pos.x(), pos.y() + holder.height() // 2).name()

    normal = corner()
    kit.set_entry_error(holder, True)
    QApplication.processEvents()
    assert corner() == "#f3a1a9" and normal == "#393943"
    window.close()


def test_pill_changes_its_tone(kit):
    pill = kit.pill("Ready", "good")
    assert pill.property("tone") == "good"
    kit.set_pill(pill, "Broken", "bad")
    assert (pill.text(), pill.property("tone")) == ("Broken", "bad")


def test_button_styles_and_command(kit):
    calls = []
    for style in ("primary", "secondary", "danger", "quiet"):
        button = kit.button("Go", style, lambda s=style: calls.append(s), compact=True)
        assert button.property("variant") == style and button.property("compact") is True
        button.click()
    assert calls == ["primary", "secondary", "danger", "quiet"]
    off = kit.button("Off", disabled=True, command=lambda: calls.append("off"))
    off.click()
    assert not off.isEnabled() and "off" not in calls


def test_style_sheet_is_set_on_the_window_and_not_the_application(kit, qapp):
    window = QWidget()
    kit.style_window(window)
    assert window.styleSheet() == Kit.style_sheet()
    assert qapp.styleSheet() == ""


def test_headers_add_themselves_to_the_parent_layout(kit):
    page = QWidget()
    QVBoxLayout(page)
    kit.page_header(page, "Title", "Sub")
    row = kit.section_header(page, "Section", "hint", top=10)
    assert page.layout().count() == 3
    assert row.hint.text() == "hint"
    assert kit.section_header(page, "Bare").hint is None


def test_rail_selects_and_marks_the_current_entry(kit):
    chosen = []
    rail = kit.rail([("a", "Alpha"), ("b", "Beta")], "v1.2.3", on_select=chosen.append)
    rail.resize(132, 400)
    QTest.mouseClick(rail.strips["b"], Qt.MouseButton.LeftButton)
    assert chosen == ["b"]
    rail.paint("b")
    assert rail.strips["b"].property("active") is True
    assert rail.strips["a"].property("active") is False
    plain = kit.rail([("a", "Alpha")], "v1")
    QTest.mouseClick(plain.strips["a"], Qt.MouseButton.LeftButton)  # no on_select: a label


def opaque(image):
    out = {}
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.alpha():
                out[(x, y)] = c.name()
    return out


def expected_pixels(palette, state, cell):
    w, h, data = raster_rgba(sprites.get_frames(state)[0], {**sprites.PALETTE, **palette}, cell)
    out = {}
    for y in range(h):
        for x in range(w):
            r, g, b, a = data[(y * w + x) * 4:(y * w + x) * 4 + 4]
            if a:
                out[(x, y)] = QColor(r, g, b).name()
    return out


def test_sprite_preview_pixels_match_raster_rgba(kit):
    palette = sprites.resolve_palette("orange", "tabby")
    preview = kit.sprite_preview(palette, "content", False, cell=3)
    assert opaque(preview.image) == expected_pixels(palette, "content", 3)
    cols = len(sprites.get_frames("content")[0][0])
    assert preview.width() == cols * 3


def test_sprite_preview_mirrors_when_asked(kit):
    palette = sprites.resolve_palette("orange", "tabby")
    plain = sprite_image(palette, "content", False, cell=2)
    flipped = sprite_image(palette, "content", True, cell=2)
    assert flipped.flipped(Qt.Orientation.Horizontal) == plain
    preview = kit.sprite_preview(palette, mirrored=True, cell=2)
    assert preview.image == flipped
    preview.set_sprite(palette, "content", False)
    assert preview.image == plain


def test_surface_is_a_tagged_card(kit):
    card = kit.surface()
    assert card.property("kit") == "surface" and card.layout() is not None
    assert SURFACE in Kit.style_sheet()
