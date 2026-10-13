import pytest

from tokitty.update_check import CheckResult
from tokitty.update_controller import UpdateResult
from tokitty.update_flow import RELEASES_URL, WRAP_PX, UpdateActions, UpdateFlow
from tokitty.updater import Release, RunningVersion

RELEASE = Release("v0.3.0", "https://example.test/notes", "https://example.test/a", 10, "https://example.test/s")
CHOICES = ["Install", "Release notes", "Later"]


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


class Render:
    """Records what the flow draws: the note and the button row as the user sees them."""

    def __init__(self):
        self.note, self.buttons, self.closed, self.shows = None, [], 0, 0

    def show(self, note, buttons):
        self.shows += 1
        self.note = note
        if buttons is not None:
            self.buttons = buttons

    def close(self):
        self.closed += 1

    def labels(self):
        return [label for label, _ in self.buttons]

    def invoke(self, label):
        for text, callback in self.buttons:
            if text == label:
                callback()
                return
        raise LookupError(label)


def make_flow(controller=None, release=RELEASE, urls=None):
    render = Render()
    controller = controller or FakeController()
    flow = UpdateFlow(release, "v0.2.0", controller, render, (urls if urls is not None else []).append)
    return flow, render, controller


def test_the_constants_are_the_ones_the_dialogs_use():
    assert RELEASES_URL == "https://github.com/nickwolf/tokitty/releases"
    assert WRAP_PX == 340


def test_info_text_names_the_running_and_the_offered_version():
    flow, _, _ = make_flow()
    assert flow.info_text == "Tokitty v0.2.0 is installed.\nv0.3.0 is available."


def test_an_installable_release_offers_install_release_notes_and_later():
    flow, render, controller = make_flow()
    assert render.labels() == CHOICES and render.note == "" and flow.note == ""
    assert controller.refusals == 1 and flow.alive


@pytest.mark.parametrize("reason", ["This copy of Tokitty can't update itself.", "Tokitty can't write to /Applications."])
def test_a_refused_release_offers_the_release_page_and_shows_the_reason(reason):
    urls = []
    flow, render, controller = make_flow(FakeController(reason=reason), urls=urls)
    assert render.labels() == ["Open release page", "Later"]
    assert render.note == reason
    render.invoke("Open release page")
    assert urls == [RELEASE.html_url]
    assert controller.installs == [] and controller.refusals == 1


def test_release_notes_falls_back_to_the_releases_list():
    urls = []
    _, render, _ = make_flow(urls=urls)
    render.invoke("Release notes")
    _, render, _ = make_flow(release=Release("v0.3.0", None, None, None, None), urls=urls)
    render.invoke("Release notes")
    assert urls == ["https://example.test/notes", RELEASES_URL]


def test_install_shows_preparing_with_cancel():
    flow, render, controller = make_flow()
    render.invoke("Install")
    release, on_progress, on_done = controller.installs[0]
    assert release is RELEASE
    assert render.labels() == ["Cancel"] and render.note == "Preparing…"
    assert on_progress == flow.on_progress and on_done == flow.on_done


def test_an_install_that_cannot_start_returns_to_the_choices():
    _, render, _ = make_flow(FakeController(accept=False))
    render.invoke("Install")
    assert render.note == "An update is already in progress."
    assert render.labels() == CHOICES


@pytest.mark.parametrize("done, total, text", [
    (0, 0, "Downloading…"),
    (5, -1, "Downloading…"),
    (0, 100, "Downloading… 0%"),
    (25, 100, "Downloading… 25%"),
    (99, 100, "Downloading… 99%"),
    (100, 100, "Checking the download…"),
    (150, 100, "Checking the download…"),
])
def test_progress_texts(done, total, text):
    flow, render, _ = make_flow()
    render.invoke("Install")
    flow.on_progress(done, total)
    assert render.note == text
    assert render.labels() == ["Cancel"]  # progress keeps the row it has


def test_cancel_clears_the_buttons_and_late_progress_is_ignored():
    flow, render, controller = make_flow()
    render.invoke("Install")
    render.invoke("Cancel")
    assert controller.cancels == 1 and render.labels() == [] and render.note == "Cancelling…"
    flow.on_progress(50, 100)
    assert render.note == "Cancelling…"
    flow.on_done(UpdateResult("cancelled", "", "v0.3.0"))
    assert render.labels() == CHOICES and render.note == "Cancelled."


def test_a_failed_install_shows_its_message_over_the_choices():
    flow, render, controller = make_flow()
    render.invoke("Install")
    controller.busy = False
    flow.on_done(UpdateResult("failed", "The download didn't match its checksum.", "v0.3.0"))
    assert flow.alive
    assert render.note == "The download didn't match its checksum." and render.labels() == CHOICES


def test_a_failure_with_no_message_says_so():
    flow, render, _ = make_flow()
    render.invoke("Install")
    flow.on_done(UpdateResult("failed", "", "v0.3.0"))
    assert render.note == "The update failed."


def test_installed_keeps_the_dialog_open_without_buttons():
    flow, render, controller = make_flow()
    render.invoke("Install")
    controller.busy = False
    flow.on_done(UpdateResult("installed", "Updated to v0.3.0.", "v0.3.0"))
    assert flow.alive and render.note == "Updated to v0.3.0. Restarting…" and render.labels() == []
    assert render.closed == 0


def test_rolled_back_closes_the_dialog():
    flow, render, controller = make_flow()
    render.invoke("Install")
    controller.busy = False
    flow.on_done(UpdateResult("rolled_back", "Tokitty v0.3.0 didn't start.", "v0.3.0"))
    assert not flow.alive and render.closed == 1


def test_close_is_refused_while_an_install_runs_and_accepted_after():
    flow, render, controller = make_flow()
    render.invoke("Install")
    flow.close_requested()
    assert flow.alive and render.closed == 0
    controller.busy = False
    flow.close_requested()
    assert not flow.alive and render.closed == 1


def test_later_closes_unless_an_install_runs():
    flow, render, _ = make_flow()
    render.invoke("Later")
    assert not flow.alive and render.closed == 1


def test_close_is_idempotent_and_safe_to_reenter_from_the_renderer():
    flow, render, _ = make_flow()
    render.close = lambda: (setattr(render, "closed", render.closed + 1), flow.close())
    flow.close()
    flow.close()
    assert not flow.alive and render.closed == 1


def test_callbacks_after_close_are_ignored():
    flow, render, controller = make_flow()
    render.invoke("Install")
    controller.busy = False
    flow.close()
    shows = render.shows
    flow.on_progress(1, 2)
    flow.on_done(UpdateResult("failed", "x", "v0.3.0"))
    flow.on_done(UpdateResult("installed", "", "v0.3.0"))
    assert render.shows == shows


# --- the menu actions ---------------------------------------------------------


class Hooks:
    def __init__(self):
        self.deferred, self.errors, self.infos, self.offers = [], [], [], []

    def actions(self, checker, controller=None):
        return UpdateActions(
            checker,
            controller or FakeController(),
            defer=self.deferred.append,
            error=lambda title, text: self.errors.append((title, text)),
            info=lambda title, text: self.infos.append((title, text)),
            offer=self.offers.append,
        )

    def run_deferred(self):
        pending, self.deferred = self.deferred, []
        for fn in pending:
            fn()


def test_the_update_item_offers_a_known_release():
    hooks, checker = Hooks(), FakeChecker(latest=RELEASE, available=True)
    hooks.actions(checker).install()
    assert hooks.offers == [RELEASE] and checker.waiting == []


@pytest.mark.parametrize("latest, available", [(None, False), (RELEASE, False), (None, True)])
def test_the_update_item_checks_first_without_a_release_in_hand(latest, available):
    hooks, checker = Hooks(), FakeChecker(latest=latest, available=available)
    hooks.actions(checker).install()
    assert len(checker.waiting) == 1 and hooks.offers == []
    checker.waiting[0](CheckResult(RELEASE, True))
    assert hooks.offers == [] and len(hooks.deferred) == 1  # the result goes through defer
    hooks.run_deferred()
    assert hooks.offers == [RELEASE]


def test_a_second_check_while_one_is_in_flight_does_nothing_and_the_flag_resets():
    hooks, checker = Hooks(), FakeChecker()
    actions = hooks.actions(checker)
    actions.check_now()
    actions.check_now()
    assert len(checker.waiting) == 1
    checker.waiting[0](CheckResult(error="HTTP 403 from the releases list"))
    actions.check_now()
    assert len(checker.waiting) == 2


def test_a_check_error_is_reported():
    hooks, checker = Hooks(), FakeChecker()
    hooks.actions(checker).check_now()
    checker.waiting[0](CheckResult(error="HTTP 403 from the releases list"))
    assert hooks.errors == []
    hooks.run_deferred()
    assert hooks.errors == [("Check for updates", "Couldn't check for updates: HTTP 403 from the releases list")]
    assert hooks.infos == [] and hooks.offers == []


def test_nothing_newer_says_so():
    hooks, checker = Hooks(), FakeChecker()
    hooks.actions(checker).check_now()
    checker.waiting[0](CheckResult(Release("v0.2.0", None, None, None, None), False))
    hooks.run_deferred()
    assert hooks.infos == [("Check for updates", "Tokitty v0.2.0 is the latest version.")]
    assert hooks.errors == [] and hooks.offers == []


def test_a_newer_result_with_no_release_says_latest():
    hooks, checker = Hooks(), FakeChecker()
    hooks.actions(checker).check_now()
    checker.waiting[0](CheckResult(None, True))
    hooks.run_deferred()
    assert len(hooks.infos) == 1 and hooks.offers == []
