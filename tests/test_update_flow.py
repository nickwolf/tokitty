import pytest

from tokitty.update_check import CheckResult
from tokitty.update_controller import UpdateResult
from tokitty.update_flow import (
    NO_NOTES, NOTES_CAP, RELEASES_URL, SHORTENED, UpdateActions, UpdateFlow, UpdateStatus, notes_markdown,
    release_page_url,
)
from tokitty.updater import Release, RunningVersion

RELEASE = Release("v0.3.0", "https://example.test/notes", "https://example.test/a", 10, "https://example.test/s")
CHOICES = ["Install"]


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


def make_flow(controller=None, release=RELEASE):
    render = Render()
    controller = controller or FakeController()
    flow = UpdateFlow(release, controller, render)
    return flow, render, controller


def test_an_installable_release_offers_install_only():
    flow, render, controller = make_flow()
    assert render.labels() == CHOICES and render.note == "" and flow.note == ""
    assert controller.refusals == 1 and flow.alive


@pytest.mark.parametrize("reason", ["This copy of Tokitty can't update itself.", "Tokitty can't write to /Applications."])
def test_a_refused_release_shows_the_reason_and_no_buttons(reason):
    flow, render, controller = make_flow(FakeController(reason=reason))
    assert render.labels() == []
    assert render.note == reason
    assert controller.installs == [] and controller.refusals == 1


def test_the_release_page_falls_back_to_the_releases_list():
    assert release_page_url(RELEASE) == "https://example.test/notes"
    assert release_page_url(Release("v0.3.0", None, None, None, None)) == RELEASES_URL


def test_notes_are_the_body_then_the_github_link():
    release = Release("v0.3.0", "https://example.test/notes", None, None, None, body="## Fixed\n- a thing\n")
    assert notes_markdown(release) == "## Fixed\n- a thing\n\n[View on GitHub](https://example.test/notes)"


@pytest.mark.parametrize("body", [None, "", "  \n "])
def test_an_empty_body_shows_the_fallback_and_the_link(body):
    release = Release("v0.3.0", None, None, None, None, body=body)
    assert notes_markdown(release) == f"{NO_NOTES}\n\n[View on GitHub]({RELEASES_URL})"


def test_a_long_body_is_cut_at_the_cap_with_a_pointer_to_github():
    release = Release("v0.3.0", "https://example.test/notes", None, None, None, body="x" * (NOTES_CAP + 500))
    got = notes_markdown(release)
    assert got.startswith("x" * NOTES_CAP + "\n\n") and "x" * (NOTES_CAP + 1) not in got
    assert SHORTENED in got and got.endswith("[View on GitHub](https://example.test/notes)")


def test_a_body_exactly_at_the_cap_is_not_shortened():
    release = Release("v0.3.0", None, None, None, None, body="x" * NOTES_CAP)
    assert SHORTENED not in notes_markdown(release)


def test_the_headline_for_each_state():
    assert UpdateStatus("v0.2.0", release=RELEASE).headline == "Tokitty v0.2.0 is installed.\nv0.3.0 is available."
    assert UpdateStatus("v0.2.0").headline == "Tokitty v0.2.0 is the latest version."
    assert UpdateStatus("v0.2.0", checking=True).headline == "Checking…"
    assert UpdateStatus("v0.2.0", pending=True).headline == "Checking…"
    assert UpdateStatus("v0.2.0", error="offline").headline == "Couldn't check for updates: offline"


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


def test_installed_keeps_the_flow_open_without_buttons():
    flow, render, controller = make_flow()
    render.invoke("Install")
    controller.busy = False
    flow.on_done(UpdateResult("installed", "Updated to v0.3.0.", "v0.3.0"))
    assert flow.alive and render.note == "Updated to v0.3.0. Restarting…" and render.labels() == []
    assert render.closed == 0


def test_rolled_back_closes_the_flow():
    flow, render, controller = make_flow()
    render.invoke("Install")
    controller.busy = False
    flow.on_done(UpdateResult("rolled_back", "Tokitty v0.3.0 didn't start.", "v0.3.0"))
    assert not flow.alive and render.closed == 1


def test_busy_follows_the_controller_and_the_flow_being_alive():
    flow, render, controller = make_flow()
    assert not flow.busy
    render.invoke("Install")
    assert flow.busy
    controller.busy = False
    assert not flow.busy


def test_detach_stops_late_callbacks_without_calling_the_renderer():
    flow, render, controller = make_flow()
    render.invoke("Install")
    flow.detach()
    shows = render.shows
    flow.on_progress(1, 2)
    flow.on_done(UpdateResult("failed", "x", "v0.3.0"))
    assert render.shows == shows and render.closed == 0 and not flow.busy


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
        self.changes = 0

    def actions(self, checker, controller=None):
        return UpdateActions(
            checker,
            controller or FakeController(),
            defer=self.deferred.append,
            error=lambda title, text: self.errors.append((title, text)),
            info=lambda title, text: self.infos.append((title, text)),
            offer=self.offers.append,
            changed=self._changed,
        )

    def _changed(self):
        self.changes += 1

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


def test_status_reports_the_check_and_the_release():
    hooks, checker = Hooks(), FakeChecker(latest=RELEASE, available=True)
    actions = hooks.actions(checker)
    assert actions.status() == UpdateStatus("v0.2.0", release=RELEASE)
    checker.latest_release, checker.update_available = None, False
    assert actions.status() == UpdateStatus("v0.2.0")
    checker.update_available = True
    assert actions.status().pending and actions.status().release is None
    actions.check_now()
    assert actions.status().checking and hooks.changes == 1


def test_status_keeps_a_check_error_until_the_next_check():
    hooks, checker = Hooks(), FakeChecker()
    actions = hooks.actions(checker)
    actions.check_now()
    checker.waiting[0](CheckResult(error="offline"))
    assert actions.status().error == "offline" and not actions.status().checking
    hooks.run_deferred()
    assert hooks.changes == 2  # started, then presented
    actions.check_now()
    assert actions.status().error is None


def test_ensure_release_checks_once_for_a_tag_with_no_release():
    hooks, checker = Hooks(), FakeChecker(available=True)
    actions = hooks.actions(checker)
    actions.ensure_release()
    actions.ensure_release()
    assert len(checker.waiting) == 1
    checker.waiting[0](CheckResult(error="offline"))
    actions.ensure_release()
    assert len(checker.waiting) == 1  # a failure is not retried until Check now


def test_ensure_release_does_nothing_with_a_release_in_hand_or_no_update():
    for checker in (FakeChecker(latest=RELEASE, available=True), FakeChecker()):
        hooks = Hooks()
        hooks.actions(checker).ensure_release()
        assert checker.waiting == []


def test_new_flow_asks_the_controller_once():
    hooks, controller = Hooks(), FakeController()
    render = Render()
    flow = hooks.actions(FakeChecker(), controller).new_flow(RELEASE, render)
    assert flow.alive and render.labels() == CHOICES and controller.refusals == 1
