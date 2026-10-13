"""QtUpdateDialog: the Qt renderer over UpdateFlow."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent

from tokitty.qt import update_dialog as ud
from tokitty.qt.update_dialog import QtUpdateDialog
from tokitty.update_controller import UpdateResult
from tokitty.updater import Release, RunningVersion

pytestmark = pytest.mark.qt

RELEASE = Release("v0.3.0", "https://example.test/notes", "https://example.test/a", 10, "https://example.test/s")


class FakeController:
    running = RunningVersion("v0.2.0", True)

    def __init__(self, reason=None, accept=True):
        self.reason, self.accept, self.busy = reason, accept, False
        self.refusals, self.installs, self.cancels = 0, [], 0

    def refusal(self, release):
        self.refusals += 1
        return self.reason

    def start_install(self, release, on_progress=None, on_done=None):
        self.installs.append((release, on_progress, on_done))
        self.busy = self.accept
        return self.accept

    def cancel(self):
        self.cancels += 1


@pytest.fixture
def opened(make_world):
    """Opens dialogs against a real host; closes whatever is left afterwards."""
    world = make_world(1)
    made = []

    def open_dialog(controller=None, release=RELEASE, urls=None):
        urls = [] if urls is None else urls
        dialog = QtUpdateDialog.open(world.host, release, "v0.2.0", controller or FakeController(), urls.append)
        made.append(dialog)
        return dialog

    open_dialog.world = world
    yield open_dialog
    for dialog in made:
        if dialog.alive:
            dialog._flow.close()
    ud._instance = None


def test_installable_release_offers_install_release_notes_and_later(opened):
    controller = FakeController()
    dialog = opened(controller)
    assert dialog.button_labels() == ["Install", "Release notes", "Later"]
    assert dialog.windowTitle() == "Update Tokitty"
    assert dialog.note == ""
    assert controller.refusals == 1
    assert dialog.parent() is None and dialog.isVisible()


def test_a_refused_release_shows_the_reason_and_the_release_page(opened):
    urls = []
    dialog = opened(FakeController(reason="This copy of Tokitty can't update itself."), urls=urls)
    assert dialog.button_labels() == ["Open release page", "Later"]
    assert dialog.note == "This copy of Tokitty can't update itself."
    dialog.invoke("Open release page")
    assert urls == ["https://example.test/notes"]


def test_the_note_wraps_at_the_shared_width(opened):
    dialog = opened()
    assert dialog._note.wordWrap() and dialog._note.width() == ud.WRAP_PX


def test_release_notes_opens_the_release_url(opened):
    urls = []
    opened(urls=urls).invoke("Release notes")
    assert urls == ["https://example.test/notes"]


def test_install_shows_preparing_and_cancel(opened):
    controller = FakeController()
    dialog = opened(controller)
    dialog.invoke("Install")
    assert dialog.note == "Preparing…"
    assert dialog.button_labels() == ["Cancel"]
    assert controller.installs[0][0] is RELEASE
    dialog.invoke("Cancel")
    assert controller.cancels == 1
    assert dialog.note == "Cancelling…" and dialog.button_labels() == []


def test_progress_updates_the_note_and_keeps_the_same_buttons(opened):
    controller = FakeController()
    dialog = opened(controller)
    dialog.invoke("Install")
    before = dialog._buttons()
    on_progress = controller.installs[0][1]
    on_progress(0, 0)
    assert dialog.note == "Downloading…"
    on_progress(5, 10)
    assert dialog.note == "Downloading… 50%"
    on_progress(10, 10)
    assert dialog.note == "Checking the download…"
    assert dialog._buttons() == before and dialog.button_labels() == ["Cancel"]


def test_progress_after_cancel_is_ignored(opened):
    controller = FakeController()
    dialog = opened(controller)
    dialog.invoke("Install")
    dialog.invoke("Cancel")
    controller.installs[0][1](5, 10)
    assert dialog.note == "Cancelling…"


def test_a_refused_second_install_says_one_is_running(opened):
    dialog = opened(FakeController(accept=False))
    dialog.invoke("Install")
    assert dialog.note == "An update is already in progress."
    assert dialog.button_labels() == ["Install", "Release notes", "Later"]


@pytest.mark.parametrize("result, note", [
    (UpdateResult("cancelled"), "Cancelled."),
    (UpdateResult("failed", message="No space left."), "No space left."),
    (UpdateResult("failed"), "The update failed."),
])
def test_cancel_and_failure_show_the_choices_again(opened, result, note):
    controller = FakeController()
    dialog = opened(controller)
    dialog.invoke("Install")
    controller.installs[0][2](result)
    assert dialog.note == note
    assert dialog.button_labels() == ["Install", "Release notes", "Later"]


def test_installed_shows_restarting_with_no_buttons(opened):
    controller = FakeController()
    dialog = opened(controller)
    dialog.invoke("Install")
    controller.installs[0][2](UpdateResult("installed", tag="v0.3.0"))
    assert dialog.note == "Updated to v0.3.0. Restarting…"
    assert dialog.button_labels() == []


def test_rolled_back_closes_the_window(opened):
    controller = FakeController()
    dialog = opened(controller)
    dialog.invoke("Install")
    controller.installs[0][2](UpdateResult("rolled_back"))
    assert not dialog.alive and not dialog.isVisible()
    assert not opened.world.host.is_registered(dialog)


def test_later_closes_the_window(opened):
    dialog = opened()
    dialog.invoke("Later")
    assert not dialog.alive and not dialog.isVisible()


def test_the_title_bar_close_is_ignored_while_busy(opened):
    controller = FakeController()
    dialog = opened(controller)
    dialog.invoke("Install")
    event = QCloseEvent()
    dialog.closeEvent(event)
    assert not event.isAccepted()
    assert dialog.alive and dialog.isVisible()
    assert dialog.close() is False and dialog.isVisible()
    controller.busy = False
    assert dialog.close() is True
    assert not dialog.alive and not dialog.isVisible()


def test_reopening_raises_the_same_window(opened):
    controller = FakeController()
    first = opened(controller)
    first.hide()
    second = opened(controller)
    assert second is first and first.isVisible()
    assert controller.refusals == 1


def test_a_closed_dialog_is_replaced_by_a_fresh_one(opened):
    first = opened()
    first.invoke("Later")
    second = opened()
    assert second is not first and second.alive


def test_it_is_registered_for_stacking_and_follows_always_in_front(opened):
    world = opened.world
    dialog = opened()
    assert world.host.is_registered(dialog)
    hint = Qt.WindowType.WindowStaysOnTopHint
    assert bool(dialog.windowFlags() & hint) is world.widget.always_on_top
    world.host.toggle_always_on_top()
    assert bool(dialog.windowFlags() & hint) is world.widget.always_on_top
    world.host.toggle_always_on_top()
    assert bool(dialog.windowFlags() & hint) is world.widget.always_on_top


def test_it_is_forgotten_after_it_closes(opened):
    dialog = opened()
    dialog.close()
    assert not opened.world.host.is_registered(dialog)
    assert ud._instance is None
