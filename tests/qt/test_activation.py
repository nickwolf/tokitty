"""Windows opened from a menu activate the app (an agent app is not activated
by opening a window), and the helper that does it, activate_app."""
from types import SimpleNamespace

import pytest

from tokitty.accounts import Account
from tokitty.qt import macos
from tokitty.streamdock.pending import PendingRequest
from tokitty.updater import Release

pytestmark = pytest.mark.qt

RELEASE = Release("v0.3.0", "https://example.test/notes", "https://example.test/a", 10, "https://example.test/s")


@pytest.fixture
def activations(monkeypatch):
    calls = []
    monkeypatch.setattr(macos, "activate_app", lambda *a, **kw: calls.append(1) or True)
    return calls


class FakeObjC:
    def __init__(self, null=()):
        self.calls = []
        self.null = set(null)

    def get_class(self, name):
        self.calls.append(("class", name))
        return 0 if name in self.null else 11

    def sel(self, name):
        return name

    def send_id(self, obj, sel):
        self.calls.append(("id", sel))
        return 0 if sel in self.null else 22

    def send_void_bool(self, obj, sel, value):
        self.calls.append(("void_bool", obj, sel, value))


def test_activate_app_asks_the_shared_application_to_activate():
    fake = FakeObjC()
    assert macos.activate_app(fake, platform="darwin") is True
    assert ("class", "NSApplication") in fake.calls and ("id", "sharedApplication") in fake.calls
    assert fake.calls[-1] == ("void_bool", 22, "activateIgnoringOtherApps:", True)


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_activate_app_does_nothing_off_macos(platform):
    fake = FakeObjC()
    assert macos.activate_app(fake, platform=platform) is False
    assert fake.calls == []


@pytest.mark.parametrize("null", [{"NSApplication"}, {"sharedApplication"}])
def test_activate_app_reports_a_null_result_and_does_not_raise(null, capsys):
    fake = FakeObjC(null=null)
    assert macos.activate_app(fake, platform="darwin") is False
    assert "cannot activate the app" in capsys.readouterr().err
    assert not any(c[0] == "void_bool" for c in fake.calls)


def test_activate_app_survives_an_unavailable_runtime(monkeypatch, capsys):
    def broken():
        raise OSError("libobjc not found")

    monkeypatch.setattr(macos, "ObjC", broken)
    assert macos.activate_app(platform="darwin") is False
    assert "libobjc not found" in capsys.readouterr().err


def test_settings_activates_the_app_when_opened_and_when_raised_again(make_world, activations):
    from tokitty.qt.settings import SettingsWindow

    world = make_world(1)
    SettingsWindow.open(world.host, 0)
    assert len(activations) == 1
    SettingsWindow.open(world.host, 0)
    assert len(activations) == 2


def test_the_accounts_manager_activates_the_app(make_world, activations, monkeypatch):
    from tokitty.qt import accounts as qa

    world = make_world(1)
    manager = qa.AccountsManager.open(world.state_dir, None, host=world.host)
    try:
        assert len(activations) == 1
    finally:
        manager.close()


def test_the_update_dialog_activates_the_app_new_and_existing(make_world, activations):
    from tokitty.qt import update_dialog as ud

    world = make_world(1)
    controller = SimpleNamespace(refusal=lambda release: None, cancel=lambda: None)
    dialog = ud.QtUpdateDialog.open(world.host, RELEASE, "v0.2.0", controller, lambda url: None)
    try:
        ud.QtUpdateDialog.open(world.host, RELEASE, "v0.2.0", controller, lambda url: None)
        assert len(activations) == 2
    finally:
        dialog._flow.close()
        ud._instance = None


def test_the_presets_window_activates_the_app(qapp, tmp_path, activations):
    from tokitty.qt import presets as qt_presets

    dialog = qt_presets.PresetsDialog.open(tmp_path, lambda: [Account("a", "C:\\x\\.claude")], lambda i: "A",
                                           lambda presets: None)
    try:
        assert len(activations) == 1
    finally:
        dialog.close()


def test_an_in_window_permission_view_does_not_activate_the_app(qapp, activations):
    # It appears unasked while the user types elsewhere; taking the app's
    # focus could send a keystroke to its buttons.
    from tokitty.qt.deck_views import qt_view_factory

    request = PendingRequest(nonce="n1", session_id="s", tool_use_id="t", tool_name="Bash",
                             tool_input={"command": "ls"}, digest="d", preview="ls", started=1.0)
    view = qt_view_factory()(request, "t", "e", None, lambda b: None, lambda: None)
    try:
        assert activations == []
    finally:
        view.close()


def test_the_walkthrough_activates_the_app_when_presented(qapp, tmp_path, activations):
    from tokitty.qt.walkthrough import Walkthrough

    walk = Walkthrough(tmp_path, discover=lambda: [], save=lambda picked: [])
    try:
        walk.present()
        assert len(activations) == 1
    finally:
        walk._finished = True
        walk.close()


def test_about_activates_the_app_and_opens_the_window(qapp, activations):
    from tokitty.qt import about
    from tokitty.qt.app import show_about

    host = SimpleNamespace(register_window=lambda w: None, forget_window=lambda w: None)
    pane0 = SimpleNamespace(_colorway="gray", _pattern="tabby")
    window = show_about(host, pane0)
    try:
        assert isinstance(window, about.AboutWindow) and window.isVisible()
        assert activations == [1]
    finally:
        window.close()
