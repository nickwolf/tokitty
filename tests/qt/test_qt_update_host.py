"""QtUpdateHost: the Qt half of an update handover."""
import pytest
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from tokitty import update_controller
from tokitty.lock import LockAcquisitionError
from tokitty.qt.update_host import QtUpdateHost

pytestmark = pytest.mark.qt


class FakeLock:
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls

    def release(self):
        self.calls.append("release")


class FakeTray:
    """Records start, stop and refresh; `calls` can be shared with a lock."""

    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls

    def start(self):
        self.calls.append("tray start")

    def stop(self):
        self.calls.append("tray stop")

    def refresh(self):
        self.calls.append("tray refresh")


class FakeApp:
    def __init__(self):
        self.quits = 0
        self.exits = []

    def quit(self):
        self.quits += 1

    def exit(self, code=0):
        self.exits.append(code)


@pytest.fixture
def reclaimed(monkeypatch):
    """Replaces reclaim; `fail` makes it raise as a lock held elsewhere does."""
    calls = []
    state = {"fail": False}

    def fake(lock, state_dir, backend):
        calls.append((lock, state_dir, backend))
        if state["fail"]:
            raise LockAcquisitionError("held")

    monkeypatch.setattr(update_controller, "reclaim", fake)
    fake.calls, fake.state = calls, state
    return fake


@pytest.fixture
def setup(make_world, qapp):
    world = make_world(1)
    world.widget.show()
    extra = []

    def window(register=True, visible=True):
        w = QWidget()
        if register:
            world.host.register_window(w)
        if visible:
            w.show()
        extra.append(w)
        return w

    def build(quiet=False, tray_on=True):
        lock, app = FakeLock(), FakeApp()
        host = QtUpdateHost(app, world.host, lock, world.state_dir, None, quiet=quiet,
                            tray=world.tray, tray_enabled=lambda: tray_on)
        return host, lock, app

    world.tray = FakeTray()

    world.window, world.build = window, build
    yield world
    for w in extra:
        world.host.forget_window(w)
        w.close()
    world.widget.hide()


def test_hide_hides_the_widget_and_visible_registered_windows_then_releases(setup, reclaimed):
    host, lock, _ = setup.build()
    shown = setup.window()
    already_hidden = setup.window(visible=False)
    unregistered = setup.window(register=False)
    host.hide()
    assert host.hidden
    assert not setup.widget.isVisible() and not shown.isVisible()
    assert not already_hidden.isVisible()
    # Every visible top-level goes, registered or not.
    assert not unregistered.isVisible()
    assert lock.calls == ["release"]


def test_restore_reclaims_then_shows_what_hide_hid(setup, reclaimed):
    host, lock, _ = setup.build()
    shown = setup.window()
    already_hidden = setup.window(visible=False)
    unregistered = setup.window(register=False)
    host.hide()
    host.restore()
    assert reclaimed.calls == [(lock, setup.state_dir, None)]
    assert setup.widget.isVisible() and shown.isVisible()
    assert not already_hidden.isVisible()
    # Unregistered windows come back too: the presets form is one.
    assert unregistered.isVisible()
    assert not host.hidden


def test_restore_keeps_the_stacking_hint_in_step_with_the_widget(setup, reclaimed):
    host, _, _ = setup.build()
    window = setup.window()
    hint = Qt.WindowType.WindowStaysOnTopHint
    host.hide()
    setup.host.toggle_always_on_top()
    host.restore()
    assert window.isVisible()
    assert bool(window.windowFlags() & hint) is setup.widget.always_on_top


def test_restore_with_the_lock_held_elsewhere_raises_and_shows_nothing(setup, reclaimed):
    host, _, _ = setup.build()
    window = setup.window()
    host.hide()
    reclaimed.state["fail"] = True
    with pytest.raises(LockAcquisitionError):
        host.restore()
    assert host.hidden
    assert not setup.widget.isVisible() and not window.isVisible()


def test_hide_leaves_stream_dock_views_alone(setup, reclaimed):
    from tokitty.qt.deck_views import QtView

    host, _, _ = setup.build()
    view = QtView.__new__(QtView)
    QWidget.__init__(view)
    view.show()
    try:
        host.hide()
        assert view.isVisible()
    finally:
        view.hide()


def test_a_modal_box_open_during_hide_is_ended_and_not_restored(setup, reclaimed, qapp, monkeypatch):
    host, _, _ = setup.build()
    outcome = {}
    loop = QEventLoop()

    def open_box():
        outcome["button"] = QMessageBox.warning(None, "Tokitty", "A hook warning")
        QTimer.singleShot(0, loop.quit)

    def hide_inside():
        outcome["visible_before"] = any(
            isinstance(w, QMessageBox) and w.isVisible() for w in qapp.topLevelWidgets())
        host.hide()

    def give_up():  # a hide that fails to end the box must not hang the run
        for w in qapp.topLevelWidgets():
            if isinstance(w, QMessageBox):
                w.reject()
        loop.quit()

    QTimer.singleShot(0, open_box)
    QTimer.singleShot(150, hide_inside)
    QTimer.singleShot(3000, give_up)
    loop.exec()
    assert outcome["visible_before"] is True
    assert outcome["button"] == QMessageBox.StandardButton.NoButton
    host.restore()
    assert setup.widget.isVisible()
    assert not any(isinstance(w, QMessageBox) and w.isVisible() for w in qapp.topLevelWidgets())


def test_notice_is_deferred_and_shown_through_the_host_warning(setup, qapp, monkeypatch):
    host, _, _ = setup.build()
    seen = []
    monkeypatch.setattr(setup.host, "warn", lambda title, text: seen.append((title, text)))
    host.notice("The update didn't start; the old copy is back.")
    assert seen == []
    qapp.processEvents()
    assert seen == [("Tokitty", "The update didn't start; the old copy is back.")]


def test_notice_is_silent_when_quiet(setup, qapp, monkeypatch):
    host, _, _ = setup.build(quiet=True)
    seen = []
    monkeypatch.setattr(setup.host, "warn", lambda title, text: seen.append((title, text)))
    host.notice("anything")
    qapp.processEvents()
    assert seen == []


def test_finish_twice_quits_once_and_only_after_the_event_loop_turns(setup, qapp):
    # exit() rather than quit(), which on macOS goes through NSApp terminate
    # and can end the process with 0 before run_qt returns its code (#115).
    host, _, app = setup.build()
    host.finish()
    host.finish()
    assert app.exits == []
    qapp.processEvents()
    assert app.exits == [0]
    assert app.quits == 0


def test_a_child_dialog_window_comes_back_but_a_qdialog_does_not(setup, reclaimed):
    host, _, _ = setup.build()
    parent = setup.window()
    form = QWidget(parent, Qt.WindowType.Dialog)  # how the presets form is made
    form.show()
    box = QMessageBox(parent)
    box.show()
    host.hide()
    assert not form.isVisible() and not box.isVisible()
    host.restore()
    assert form.isVisible() and not box.isVisible()
    form.close()
    box.close()


def test_when_shown_runs_now_while_visible(setup):
    host, _, _ = setup.build()
    ran = []
    host.when_shown(lambda: ran.append(1))
    assert ran == [1]


def test_when_shown_holds_while_hidden_and_runs_after_restore(setup, reclaimed, qapp):
    host, _, _ = setup.build()
    ran = []
    host.hide()
    host.when_shown(lambda: ran.append(1))
    qapp.processEvents()
    assert ran == []
    host.restore()
    assert ran == []  # not inside restore, which runs in tick()
    qapp.processEvents()
    assert ran == [1]


def test_when_shown_drops_what_it_held_once_finishing(setup, reclaimed, qapp):
    host, _, _ = setup.build()
    ran = []
    host.hide()
    host.when_shown(lambda: ran.append(1))
    host.finish()
    host.when_shown(lambda: ran.append(2))
    qapp.processEvents()
    assert ran == []


def test_hide_stops_the_tray_before_the_lock_is_released(setup, reclaimed):
    order = []
    setup.tray = FakeTray(order)
    host, lock, _ = setup.build()
    lock.calls = order
    host.hide()
    assert order == ["tray stop", "release"]


def test_restore_restarts_the_tray_when_enabled_and_always_refreshes(setup, reclaimed):
    host, _, _ = setup.build(tray_on=True)
    host.hide()
    setup.tray.calls.clear()
    host.restore()
    assert setup.tray.calls == ["tray start", "tray refresh"]


def test_restore_leaves_a_disabled_tray_stopped_but_still_refreshes(setup, reclaimed):
    host, _, _ = setup.build(tray_on=False)
    host.hide()
    setup.tray.calls.clear()
    host.restore()
    assert setup.tray.calls == ["tray refresh"]


def test_restore_does_not_start_a_tray_that_became_unavailable(setup, reclaimed):
    host, _, _ = setup.build(tray_on=True)
    host.hide()
    setup.tray.available = False
    setup.tray.calls.clear()
    host.restore()
    assert setup.tray.calls == ["tray refresh"]


def test_restore_that_fails_at_reclaim_leaves_the_tray_stopped(setup, reclaimed):
    host, _, _ = setup.build()
    host.hide()
    setup.tray.calls.clear()
    reclaimed.state["fail"] = True
    with pytest.raises(LockAcquisitionError):
        host.restore()
    assert setup.tray.calls == []
