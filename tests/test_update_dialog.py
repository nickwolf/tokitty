import pytest

from tokitty.update_check import CheckResult
from tokitty.update_controller import UpdateResult
from tokitty.updater import Release, RunningVersion

pytestmark = pytest.mark.gui

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


class FakeChecker:
    def __init__(self, latest=None, available=False):
        self.latest_release, self.update_available = latest, available
        self.waiting = []

    def check_now(self, on_result):
        self.waiting.append(on_result)


@pytest.fixture
def root():
    tk = pytest.importorskip("tkinter")
    from tokitty.update_dialog import UpdateDialog

    UpdateDialog._current = None
    root = tk.Tk()
    root.withdraw()
    yield root
    UpdateDialog._current = None
    root.destroy()


def open_dialog(root, controller=None, release=RELEASE, urls=None):
    from tokitty.update_dialog import UpdateDialog

    urls = [] if urls is None else urls
    return UpdateDialog.open(root, release, "v0.2.0", controller or FakeController(), urls.append)


def test_installable_release_offers_install_release_notes_and_later(root):
    controller = FakeController()
    dialog = open_dialog(root, controller)
    assert dialog.button_labels() == ["Install", "Release notes", "Later"]
    assert dialog.note == ""
    assert controller.refusals == 1


@pytest.mark.parametrize("reason", [
    "This copy of Tokitty can't update itself.",
    "This release has no download for this system, or no SHA256SUMS to check it against.",
    "Tokitty.app was renamed, so it can't be replaced.",
    "Tokitty can't write to /Applications.",
])
def test_a_refused_release_swaps_install_for_the_release_page_and_the_reason(root, reason):
    controller = FakeController(reason=reason)
    urls = []
    dialog = open_dialog(root, controller, urls=urls)
    assert dialog.button_labels() == ["Open release page", "Later"]
    assert dialog.note == reason
    dialog.invoke("Open release page")
    assert urls == [RELEASE.html_url]
    assert controller.installs == [] and controller.refusals == 1


def test_release_notes_opens_the_release_page_and_falls_back_to_the_releases_list(root):
    urls = []
    open_dialog(root, urls=urls).invoke("Release notes")
    assert urls == ["https://example.test/notes"]
    from tokitty.update_dialog import UpdateDialog

    UpdateDialog._current.destroy()
    dialog = open_dialog(root, release=Release("v0.3.0", None, None, None, None), urls=urls)
    dialog.invoke("Release notes")
    assert urls[-1] == "https://github.com/nickwolf/tokitty/releases"


def test_only_one_dialog_at_a_time(root):
    first = open_dialog(root)
    assert open_dialog(root) is first
    first.invoke("Later")
    assert not first.alive
    assert open_dialog(root) is not first


def test_install_swaps_to_a_progress_line_and_cancel(root):
    controller = FakeController()
    dialog = open_dialog(root, controller)
    dialog.invoke("Install")
    release, on_progress, on_done = controller.installs[0]
    assert release is RELEASE
    assert dialog.button_labels() == ["Cancel"] and dialog.note == "Preparing…"
    on_progress(25, 100)
    assert dialog.note == "Downloading… 25%"
    on_progress(100, 100)
    assert dialog.note == "Checking the download…"
    dialog.invoke("Cancel")
    assert controller.cancels == 1 and dialog.button_labels() == []
    on_progress(50, 100)
    assert dialog.note == "Cancelling…"
    on_done(UpdateResult("cancelled", "", "v0.3.0"))
    assert dialog.button_labels() == ["Install", "Release notes", "Later"] and dialog.note == "Cancelled."


def test_a_failed_install_is_shown_in_the_dialog_and_can_be_retried(root):
    controller = FakeController()
    dialog = open_dialog(root, controller)
    dialog.invoke("Install")
    controller.busy = False
    controller.installs[0][2](UpdateResult("failed", "The download didn't match its checksum.", "v0.3.0"))
    assert dialog.alive
    assert dialog.note == "The download didn't match its checksum."
    assert dialog.button_labels() == ["Install", "Release notes", "Later"]


def test_installed_keeps_the_dialog_for_the_controller_to_close_the_app(root):
    controller = FakeController()
    dialog = open_dialog(root, controller)
    dialog.invoke("Install")
    controller.busy = False
    controller.installs[0][2](UpdateResult("installed", "Updated to v0.3.0.", "v0.3.0"))
    assert dialog.alive and dialog.note == "Updated to v0.3.0. Restarting…" and dialog.button_labels() == []


def test_rolled_back_just_closes_the_dialog(root):
    controller = FakeController()
    dialog = open_dialog(root, controller)
    dialog.invoke("Install")
    controller.busy = False
    controller.installs[0][2](UpdateResult("rolled_back", "Tokitty v0.3.0 didn't start.", "v0.3.0"))
    assert not dialog.alive


def test_an_install_that_cannot_start_returns_to_the_choices(root):
    dialog = open_dialog(root, FakeController(accept=False))
    dialog.invoke("Install")
    assert dialog.note == "An update is already in progress."
    assert dialog.button_labels() == ["Install", "Release notes", "Later"]


def test_the_window_close_button_is_ignored_while_an_install_runs(root):
    controller = FakeController()
    dialog = open_dialog(root, controller)
    dialog.invoke("Install")
    dialog._close_requested()
    assert dialog.alive
    controller.busy = False
    dialog._close_requested()
    assert not dialog.alive


def test_late_callbacks_after_the_dialog_closed_are_ignored(root):
    controller = FakeController()
    dialog = open_dialog(root, controller)
    dialog.invoke("Install")
    controller.busy = False
    dialog.destroy()
    _, on_progress, on_done = controller.installs[0]
    on_progress(1, 2)
    on_done(UpdateResult("failed", "x", "v0.3.0"))


# --- the menu actions ---------------------------------------------------------


def make_ui(root, checker, controller=None, urls=None):
    from tokitty.update_dialog import UpdateUi

    return UpdateUi(root, checker, controller or FakeController(), (urls if urls is not None else []).append)


def test_the_update_item_opens_the_dialog_for_a_known_release(root):
    from tokitty.update_dialog import UpdateDialog

    checker = FakeChecker(latest=RELEASE, available=True)
    make_ui(root, checker).install()
    assert UpdateDialog._current is not None and UpdateDialog._current.button_labels()[0] == "Install"
    assert checker.waiting == []


def test_the_update_item_checks_first_when_only_the_tag_is_known(root):
    from tokitty.update_dialog import UpdateDialog

    checker = FakeChecker()
    make_ui(root, checker).install()
    assert len(checker.waiting) == 1 and UpdateDialog._current is None
    checker.waiting[0](CheckResult(RELEASE, True))
    root.update()
    assert UpdateDialog._current is not None


def test_a_manual_check_reports_an_error(root, monkeypatch):
    shown = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda title, text, parent=None: shown.append((title, text)))
    checker = FakeChecker()
    ui = make_ui(root, checker)
    ui.check_now()
    ui.check_now()  # a second click while the first is out does nothing
    assert len(checker.waiting) == 1
    checker.waiting[0](CheckResult(error="HTTP 403 from the releases list"))
    root.update()
    assert shown == [("Check for updates", "Couldn't check for updates: HTTP 403 from the releases list")]
    ui.check_now()
    assert len(checker.waiting) == 2


def test_a_manual_check_with_nothing_newer_says_so(root, monkeypatch):
    shown = []
    monkeypatch.setattr("tkinter.messagebox.showinfo", lambda title, text, parent=None: shown.append((title, text)))
    checker = FakeChecker()
    make_ui(root, checker).check_now()
    checker.waiting[0](CheckResult(Release("v0.2.0", None, None, None, None), False))
    root.update()
    assert shown == [("Check for updates", "Tokitty v0.2.0 is the latest version.")]


def test_a_manual_check_that_finds_a_release_opens_the_dialog(root):
    from tokitty.update_dialog import UpdateDialog

    checker = FakeChecker()
    make_ui(root, checker).check_now()
    checker.waiting[0](CheckResult(RELEASE, True))
    root.update()
    assert UpdateDialog._current.button_labels() == ["Install", "Release notes", "Later"]
