"""The Settings window's Updates tab (#155): the release notes, the install
progress rendered from UpdateFlow, the entry point and the close guard. The
host's update_actions is a real UpdateActions over a fake checker and
controller."""
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QCloseEvent

from tokitty.qt import settings as qs
from tokitty.qt import settings_updates as qsu
from tokitty.qt.settings import SettingsWindow
from tokitty.update_check import CheckResult
from tokitty.update_controller import UpdateResult
from tokitty.update_flow import MORE_ON_GITHUB, NO_NOTES, NOTES_CAP, RELEASES_URL, SHORTENED, UpdateActions
from tokitty.updater import Release, RunningVersion

pytestmark = pytest.mark.qt

BODY = "## Fixed\n\n- the **thing**\n\n[docs](https://example.test/docs)"
RELEASE = Release("v0.3.0", "https://example.test/notes", "https://example.test/a", 10,
                  "https://example.test/s", body=BODY, published="2026-10-19")
HISTORY = (
    Release("v0.4.0", "https://example.test/v0.4.0", None, None, None, body="- four", published="2026-10-20"),
    RELEASE,
    Release("v0.2.0", "https://example.test/v0.2.0", None, None, None, body="- two", published="2026-10-12"),
    Release("v0.1.0", "https://example.test/v0.1.0", None, None, None, published="2026-10-01"),
)


class FakeController:
    running = RunningVersion("v0.2.0", True)

    def __init__(self, reason=None):
        self.reason, self.busy, self.installs, self.cancels = reason, False, [], 0

    def refusal(self, release):
        return self.reason

    def start_install(self, release, on_progress=None, on_done=None):
        self.installs.append((release, on_progress, on_done))
        self.busy = True
        return True

    def cancel(self):
        self.cancels += 1


class FakeChecker:
    def __init__(self, latest=None, available=False, history=None):
        self.latest_release, self.update_available, self.waiting = latest, available, []
        self.release_history, self.enabled = history, False

    def check_now(self, on_result):
        self.waiting.append(on_result)


class Setup:
    def __init__(self, world, release=RELEASE, controller=None, available=True, history=None):
        self.world = world
        self.urls, self.offers = [], []
        self.controller = controller or FakeController()
        if history is None:
            history = (release,) if available and release is not None else ()
        self.checker = FakeChecker(release if available else None, available, history)
        self.actions = UpdateActions(
            self.checker, self.controller,
            defer=lambda fn: fn(), error=lambda *a: None, info=lambda *a: None,
            offer=lambda r: SettingsWindow.open(world.host, tab="updates"),
            changed=world.host.notify_state_changed, open_url=self.urls.append)
        host = world.host
        host.update_actions = self.actions
        host.update_check_enabled = lambda: True
        host.on_toggle_update_check = lambda: None
        host.on_check_updates = self.actions.check_now
        host.update_available_label = lambda: f"Update to {RELEASE.tag}…" if available else None

    def tab(self):
        window = SettingsWindow.open(self.world.host, tab="updates")
        return window, window.tabs["updates"]


@pytest.fixture
def setup(make_world):
    world = make_world(1)

    def make(**kwargs):
        return Setup(world, **kwargs)

    return make


def test_the_tab_sits_after_general_and_only_exists_with_the_update_seams(make_world):
    assert [key for key, _title in qs.TABS][:2] == ["general", "updates"]
    world = make_world(1)
    assert "updates" not in SettingsWindow.open(world.host).tabs


def test_the_controls_moved_out_of_general(setup):
    window, tab = setup().tab()
    assert not hasattr(window.tabs["general"], "update_toggle")
    assert tab.update_toggle is not None and tab.update_pill.text() == "Update to v0.3.0…"


def test_an_available_release_shows_headline_rendered_notes_and_install(setup):
    window, tab = setup().tab()
    assert tab.headline.text() == "Tokitty v0.2.0 is installed.\nv0.3.0 is available."
    assert tab.notes.isReadOnly() and not tab.notes.openLinks()
    plain = tab.notes.toPlainText()
    assert "Fixed" in plain and "the thing" in plain and "**" not in plain
    assert "View on GitHub" in plain
    assert tab.button_labels() == ["Install"]
    assert "[View on GitHub](https://example.test/notes)" in tab.notes.toMarkdown()


def test_links_in_the_notes_open_through_open_url(setup):
    made = setup()
    _, tab = made.tab()
    tab.notes.anchorClicked.emit(QUrl("https://example.test/docs"))
    tab.notes.anchorClicked.emit(QUrl("https://example.test/notes"))
    tab.notes.anchorClicked.emit(QUrl("file:///etc/passwd"))
    assert made.urls == ["https://example.test/docs", "https://example.test/notes"]


def test_an_empty_body_shows_the_fallback_and_the_link(setup):
    _, tab = setup(release=Release("v0.3.0", "https://example.test/notes", None, None, None)).tab()
    assert NO_NOTES in tab.notes.toPlainText() and "View on GitHub" in tab.notes.toPlainText()


def test_a_long_body_is_truncated_with_a_notice(setup):
    body = "word " * NOTES_CAP
    _, tab = setup(release=Release("v0.3.0", "https://example.test/notes", None, None, None, body=body)).tab()
    plain = tab.notes.toPlainText()
    assert len(plain) < NOTES_CAP + 500 and SHORTENED in plain


def blocks(tab):
    block, found = tab.notes.document().begin(), []
    while block.isValid():
        found.append(block)
        block = block.next()
    return found


def headings(tab):
    """The release heading lines: the blocks that open in the tag's size."""
    return [b.text() for b in blocks(tab) if b.begin().fragment().charFormat().fontPointSize() == qsu.TAG_POINT_SIZE]


def test_the_history_lists_each_release_under_its_heading_newest_first(setup):
    _, tab = setup(release=HISTORY[0], history=HISTORY).tab()
    assert headings(tab) == [
        "v0.4.0   New   2026-10-20",
        "v0.3.0   New   2026-10-19",
        "v0.2.0   Installed   2026-10-12",
        "v0.1.0  2026-10-01",
    ]
    markdown = tab.notes.toMarkdown()
    for release in HISTORY:
        assert f"[View on GitHub]({release.html_url})" in markdown
    assert markdown.rstrip().endswith(f"[{MORE_ON_GITHUB}]({RELEASES_URL})")
    assert NO_NOTES in tab.notes.toPlainText()
    assert tab.button_labels() == ["Install"]


def test_a_heading_in_a_release_body_stays_its_own_heading(setup):
    _, tab = setup().tab()
    fixed = [b for b in blocks(tab) if b.text() == "Fixed"]
    assert len(fixed) == 1 and fixed[0].blockFormat().headingLevel() == 2
    assert headings(tab)[0] == "v0.3.0   New   2026-10-19"


def test_the_history_shows_while_up_to_date_with_no_install(setup):
    made = setup(available=False, history=HISTORY[2:])
    made.controller.running = RunningVersion("v0.2.0", True)
    window, tab = made.tab()
    assert tab.headline.text() == "Tokitty v0.2.0 is the latest version."
    assert tab.notes.isVisibleTo(window) and not tab.row_holder.isVisibleTo(window)
    assert headings(tab)[0] == "v0.2.0   Installed   2026-10-12"


def test_the_more_link_opens_the_releases_page(setup):
    made = setup(history=HISTORY)
    _, tab = made.tab()
    tab.notes.anchorClicked.emit(QUrl(RELEASES_URL))
    assert made.urls == [RELEASES_URL]


def test_a_refresh_with_the_same_history_keeps_the_scroll_position(setup):
    made = setup(release=HISTORY[0], history=HISTORY * 5)
    window, tab = made.tab()
    window.resize(600, 400)
    bar = tab.notes.verticalScrollBar()
    assert bar.value() == 0 and bar.maximum() > 0
    bar.setValue(bar.maximum())
    tab.refresh()
    assert bar.value() == bar.maximum()


def test_a_quiet_check_fills_the_history_when_the_tab_opens(setup):
    made = setup(available=False, history=None)
    made.checker.release_history, made.checker.enabled = None, True
    window, tab = made.tab()
    assert len(made.checker.waiting) == 1 and tab.headline.text() == "Checking…"
    assert not tab.notes.isVisibleTo(window)
    made.checker.release_history = HISTORY[2:]
    made.checker.waiting[0](CheckResult(HISTORY[2], False, history=HISTORY[2:]))
    assert tab.notes.isVisibleTo(window) and headings(tab)[0].startswith("v0.2.0")


def test_up_to_date_checking_and_error_headlines(setup):
    made = setup(available=False)
    window, tab = made.tab()
    assert tab.headline.text() == "Tokitty v0.2.0 is the latest version."
    assert not tab.notes.isVisibleTo(window) and tab.button_labels() == []
    made.actions.check_now()
    assert tab.headline.text() == "Checking…"
    made.checker.waiting[0](CheckResult(error="offline"))
    assert tab.headline.text() == "Couldn't check for updates: offline"


def test_a_remembered_tag_triggers_a_check_when_the_tab_opens(setup):
    made = setup(release=None)
    made.checker.update_available = True
    made.tab()
    assert len(made.checker.waiting) == 1


def test_a_refused_release_shows_the_reason_and_no_install(setup):
    _, tab = setup(controller=FakeController(reason="Can't update itself.")).tab()
    assert tab.note.text() == "Can't update itself." and tab.button_labels() == []


def test_install_progress_renders_in_the_tab(setup):
    made = setup()
    _, tab = made.tab()
    tab.invoke("Install")
    assert tab.note.text() == "Preparing…" and tab.button_labels() == ["Cancel"]
    made.controller.installs[0][1](5, 10)
    assert tab.note.text() == "Downloading… 50%"
    tab.invoke("Cancel")
    assert made.controller.cancels == 1 and tab.note.text() == "Cancelling…"
    made.controller.busy = False
    made.controller.installs[0][2](UpdateResult("cancelled"))
    assert tab.note.text() == "Cancelled." and tab.button_labels() == ["Install"]


def test_closing_is_refused_while_an_install_runs(setup):
    made = setup()
    window, tab = made.tab()
    tab.invoke("Install")
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and window.alive()
    assert window.close() is False and window.isVisible()
    made.controller.busy = False
    assert window.close() is True and not window.alive()


def test_reopening_after_a_failed_install_shows_the_current_state(setup):
    made = setup()
    window, tab = made.tab()
    tab.invoke("Install")
    made.controller.busy = False
    made.controller.installs[0][2](UpdateResult("failed", message="No space left."))
    window.close()
    # A late callback must not reach the dead tab.
    made.controller.installs[0][2](UpdateResult("failed", message="again"))
    made.controller.installs[0][1](1, 2)
    window2, tab2 = made.tab()
    assert window2 is not window and tab2.note.text() == "" and tab2.button_labels() == ["Install"]


def test_a_rolled_back_install_starts_the_tab_over(setup):
    made = setup()
    _, tab = made.tab()
    tab.invoke("Install")
    made.controller.busy = False
    made.controller.installs[0][2](UpdateResult("rolled_back"))
    assert tab.note.text() == "" and tab.button_labels() == ["Install"]


def test_open_selects_the_tab_for_a_new_and_an_open_window(setup):
    made = setup()
    window = SettingsWindow.open(made.world.host, 0)
    assert window.current == "general"
    again = SettingsWindow.open(made.world.host, tab="updates")
    assert again is window and window.current == "updates"
    window.close()
    fresh = SettingsWindow.open(made.world.host, tab="updates")
    assert fresh is not window and fresh.current == "updates"


def test_open_without_a_pane_keeps_the_open_windows_pane(make_world):
    world = make_world(3)
    window = SettingsWindow.open(world.host, 2)
    SettingsWindow.open(world.host, tab="look")
    assert window.pane_index == 2 and window.current == "look"
    SettingsWindow.open(world.host, tab="nonsense")
    assert window.current == "look"


def test_the_offer_opens_settings_on_the_updates_tab(setup):
    made = setup()
    made.actions.check_now()
    made.checker.waiting[0](CheckResult(RELEASE, newer=True))
    assert SettingsWindow.open(made.world.host).current == "updates"
