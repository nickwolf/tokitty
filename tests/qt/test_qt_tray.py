"""QtTray: the Qt counterpart of TrayManager, against a fake icon (the
offscreen platform has no system tray, so a real QSystemTrayIcon is never
shown)."""
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

from tokitty.menu import MenuItem
from tokitty.qt.tray import QtTray
from tokitty.settings import load_settings
from tokitty.update_check import announcer
from tokitty.updater import Release, load_update_state

pytestmark = pytest.mark.qt

RELEASE = Release(tag="v9.0.0", html_url="https://example.invalid/r", asset_url=None,
                  asset_size=None, sums_url=None)


class FakeIcon:
    instances = []

    def __init__(self, messages=True):
        self.messages = messages
        self.icon = None
        self.tooltip = None
        self.menu = None
        self.shown = False
        self.sent = []
        self.deleted = False
        FakeIcon.instances.append(self)

    def setIcon(self, icon):
        self.icon = icon

    def setToolTip(self, text):
        self.tooltip = text

    def setContextMenu(self, menu):
        self.menu = menu

    def contextMenu(self):
        return self.menu

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False

    def isVisible(self):
        return self.shown

    def supportsMessages(self):
        return self.messages

    def showMessage(self, title, message):
        self.sent.append((title, message))

    def deleteLater(self):
        self.deleted = True


@pytest.fixture(autouse=True)
def reset_icons():
    FakeIcon.instances = []


def make_tray(tmp_path, items=None, available=True, **kw):
    kw.setdefault("icon_factory", FakeIcon)
    return QtTray(lambda: items if items is not None else [MenuItem(label="Go", action=lambda: None)],
                  tmp_path, tray_available=lambda: available, **kw)


def all_actions(menu):
    found = []
    for action in menu.actions():
        found.append(action)
        if action.menu() is not None:
            found.extend(all_actions(action.menu()))
    return found


# ---- availability and lifecycle --------------------------------------------

def test_unavailable_when_the_check_is_false(qapp, tmp_path):
    tray = make_tray(tmp_path, available=False)
    tray.start()
    tray.refresh()
    assert not tray.available
    assert FakeIcon.instances == []
    assert tray.notify("t", "m") is False


def test_unavailable_when_the_image_raises(qapp, tmp_path, monkeypatch):
    def boom(colorway, pattern):
        raise RuntimeError("no sprite")

    monkeypatch.setattr("tokitty.qt.tray.tray_raster", boom)
    tray = make_tray(tmp_path)
    tray.start()
    assert not tray.available
    assert FakeIcon.instances == []
    assert tray.notify("t", "m") is False


def test_unavailable_when_the_check_raises(qapp, tmp_path):
    def boom():
        raise RuntimeError("no tray")

    tray = QtTray(lambda: [], tmp_path, icon_factory=FakeIcon, tray_available=boom)
    assert not tray.available


def test_start_shows_one_square_icon_with_the_tooltip(qapp, tmp_path):
    tray = make_tray(tmp_path)
    tray.start()
    tray.start()
    assert len(FakeIcon.instances) == 1
    icon = FakeIcon.instances[0]
    assert icon.shown and icon.tooltip == "Tokitty"
    size = icon.icon.availableSizes()[0]
    assert size.width() == size.height() > 0
    assert isinstance(icon.menu, QMenu) and icon.menu.parent() is None


def test_start_failure_marks_unavailable_and_leaves_no_icon(qapp, tmp_path):
    class Broken(FakeIcon):
        def show(self):
            raise RuntimeError("no host")

    tray = make_tray(tmp_path, icon_factory=Broken)
    tray.start()
    assert not tray.available
    assert tray.notify("t", "m") is False
    tray.refresh()


def test_stop_hides_and_drops_the_icon(qapp, tmp_path):
    tray = make_tray(tmp_path)
    tray.stop()  # safe before start
    tray.start()
    icon = FakeIcon.instances[0]
    tray.stop()
    assert not icon.shown and icon.deleted
    assert tray.notify("t", "m") is False
    tray.start()
    assert len(FakeIcon.instances) == 2 and FakeIcon.instances[1].shown


def test_set_enabled_writes_the_setting_and_starts_or_stops(qapp, tmp_path):
    tray = make_tray(tmp_path)
    tray.set_enabled(True)
    assert load_settings(tmp_path).tray_enabled is True
    assert FakeIcon.instances[0].shown
    tray.set_enabled(False)
    assert load_settings(tmp_path).tray_enabled is False
    assert not FakeIcon.instances[0].shown


# ---- menu ------------------------------------------------------------------

def test_update_item_is_hidden_until_the_getter_has_text(qapp, tmp_path):
    state = {"label": None}
    items = [MenuItem(label="x", dynamic_label=lambda: state["label"], action=lambda: None),
             MenuItem(label="Quit", action=lambda: None)]
    tray = make_tray(tmp_path, items)
    tray.start()
    menu = FakeIcon.instances[0].menu
    assert not menu.actions()[0].isVisible() and menu.actions()[1].text() == "Quit"
    state["label"] = "Update to v9"
    tray.refresh()
    assert menu.actions()[0].isVisible() and menu.actions()[0].text() == "Update to v9"


def test_refresh_shows_state_changed_elsewhere(qapp, tmp_path):
    state = {"top": False, "view": "a"}
    items = [
        MenuItem(label="Top", checkbox=lambda: state["top"], action=lambda: None),
        MenuItem(label="View", submenu=[
            MenuItem(label="A", radio_selected=lambda: state["view"] == "a", action=lambda: None),
            MenuItem(label="B", radio_selected=lambda: state["view"] == "b", action=lambda: None),
        ]),
    ]
    tray = make_tray(tmp_path, items)
    tray.start()
    menu = FakeIcon.instances[0].menu
    top = menu.actions()[0]
    sub = menu.actions()[1].menu()
    assert not top.isChecked() and sub.actions()[0].isChecked()
    state.update(top=True, view="b")
    tray.refresh()
    assert top.isChecked()
    assert [a.isChecked() for a in sub.actions()] == [False, True]


def test_refresh_never_creates_or_deletes_actions(qapp, tmp_path):
    state = {"label": None, "top": False}
    items = [
        MenuItem(label="x", dynamic_label=lambda: state["label"], action=lambda: None),
        MenuItem(label="Top", checkbox=lambda: state["top"], action=lambda: None),
        MenuItem(separator=True),
        MenuItem(label="View", submenu=[
            MenuItem(label="A", radio_selected=lambda: state["top"], action=lambda: None)]),
    ]
    tray = make_tray(tmp_path, items)
    tray.start()
    icon = FakeIcon.instances[0]
    before = all_actions(icon.menu)
    state.update(label="Update", top=True)
    tray.refresh()
    state.update(label=None, top=False)
    icon.menu.popup(QPoint(0, 0))  # shown, as when the user has it open
    try:
        assert icon.menu.isVisible()
        tray.refresh()
        state.update(label="Again", top=True)
        tray.refresh()
    finally:
        icon.menu.close()
    after = all_actions(icon.menu)
    assert len(before) == len(after) == 5
    assert all(a is b for a, b in zip(before, after))


def test_refresh_swallows_exceptions(qapp, tmp_path):
    state = {"fail": False}

    def getter():
        if state["fail"]:
            raise RuntimeError("x")
        return True

    tray = make_tray(tmp_path, [MenuItem(label="Top", checkbox=getter, action=lambda: None)])
    tray.start()
    state["fail"] = True
    tray.refresh()


def test_actions_are_deferred_then_followed_by_on_action_done(qapp, tmp_path):
    order = []
    items = [MenuItem(label="Go", action=lambda: order.append("action"))]
    tray = make_tray(tmp_path, items, on_action_done=lambda: order.append("done"))
    tray.start()
    FakeIcon.instances[0].menu.actions()[0].trigger()
    assert order == []
    qapp.processEvents()
    assert order == ["action", "done"]


# ---- notify ----------------------------------------------------------------

def test_notify_submits_when_shown_and_supported(qapp, tmp_path):
    tray = make_tray(tmp_path)
    tray.start()
    assert tray.notify("Title", "Body") is True
    assert FakeIcon.instances[0].sent == [("Title", "Body")]


def test_notify_false_when_not_started(qapp, tmp_path):
    assert make_tray(tmp_path).notify("t", "m") is False


def test_notify_false_when_messages_unsupported(qapp, tmp_path):
    tray = make_tray(tmp_path, icon_factory=lambda: FakeIcon(messages=False))
    tray.start()
    assert tray.notify("t", "m") is False
    assert FakeIcon.instances[0].sent == []


def test_notify_false_when_show_message_raises(qapp, tmp_path):
    class Raising(FakeIcon):
        def showMessage(self, title, message):
            raise RuntimeError("x")

    tray = make_tray(tmp_path, icon_factory=Raising)
    tray.start()
    assert tray.notify("t", "m") is False


def test_notify_false_on_darwin_without_touching_the_icon(qapp, tmp_path):
    class Untouchable(FakeIcon):
        def supportsMessages(self):
            raise AssertionError("touched")

        def showMessage(self, title, message):
            raise AssertionError("touched")

    tray = make_tray(tmp_path, icon_factory=Untouchable, platform="darwin")
    tray.start()
    assert tray.notify("t", "m") is False


def notified_tag(state_dir):
    return load_update_state(state_dir).notified_tag


def test_announcer_records_the_tag_only_after_a_true_notify(qapp, tmp_path):
    tray = make_tray(tmp_path)
    announce = announcer(tmp_path, tray)
    announce(RELEASE)  # not started: notify is False
    assert notified_tag(tmp_path) is None
    tray.start()
    announce(RELEASE)
    assert FakeIcon.instances[0].sent
    assert notified_tag(tmp_path) == "v9.0.0"


def test_announcer_leaves_the_tag_absent_on_darwin(qapp, tmp_path):
    tray = make_tray(tmp_path, platform="darwin")
    tray.start()
    announcer(tmp_path, tray)(RELEASE)
    assert notified_tag(tmp_path) is None
