"""TOKITTY_DEBUG_NOTIFY (#115): one real tray message with the macOS gate lifted."""
import pytest

from tokitty.qt.tray import QtTray

pytestmark = pytest.mark.qt


class FakeIcon:
    instances = []

    def __init__(self, messages=True):
        self.messages = messages
        self.sent = []
        FakeIcon.instances.append(self)

    def setIcon(self, icon):
        pass

    def setToolTip(self, text):
        pass

    def setContextMenu(self, menu):
        pass

    def contextMenu(self):
        return None

    def show(self):
        pass

    def hide(self):
        pass

    def supportsMessages(self):
        return self.messages

    def showMessage(self, title, message):
        self.sent.append((title, message))

    def deleteLater(self):
        pass


@pytest.fixture(autouse=True)
def reset_icons():
    FakeIcon.instances = []


def make_tray(tmp_path, available=True, platform="linux", icon_factory=FakeIcon):
    return QtTray(lambda: [], tmp_path, tray_available=lambda: available,
                  icon_factory=icon_factory, platform=platform)


def test_debug_notify_sends_on_darwin_where_notify_does_not(qapp, tmp_path, capsys):
    tray = make_tray(tmp_path, platform="darwin")
    tray.start()
    icon = FakeIcon.instances[0]
    assert tray.notify("t", "m") is False
    assert icon.sent == []
    assert tray.debug_notify("t", "m") is True
    assert icon.sent == [("t", "m")]
    assert "tokitty: debug notify: submitted" in capsys.readouterr().err


def test_debug_notify_reports_a_tray_that_cannot_show_messages(qapp, tmp_path, capsys):
    tray = make_tray(tmp_path, icon_factory=lambda: FakeIcon(messages=False))
    tray.start()
    assert tray.debug_notify("t", "m") is False
    assert FakeIcon.instances[0].sent == []
    assert "tokitty: debug notify: not submitted (the tray does not support messages)" in capsys.readouterr().err


def test_debug_notify_reports_the_exception(qapp, tmp_path, capsys):
    class Failing(FakeIcon):
        def showMessage(self, title, message):
            raise RuntimeError("no center")

    tray = make_tray(tmp_path, icon_factory=Failing)
    tray.start()
    assert tray.debug_notify("t", "m") is False
    assert "tokitty: debug notify: not submitted (RuntimeError: no center)" in capsys.readouterr().err


def test_debug_notify_without_an_icon_says_so(qapp, tmp_path, capsys):
    tray = make_tray(tmp_path, available=False)
    tray.start()
    assert tray.debug_notify("t", "m") is False
    assert "not submitted (no tray icon)" in capsys.readouterr().err
