"""The updater wired into run_qt (#113 slice 3): the seams, the menu item, the
Updates rows, the tick, and the two update hand-offs. The controller and
checker are fakes in-process; the after-update path also runs for real in a
subprocess with a scratch state dir."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from tokitty.app_core import UI_REFRESH_MS, Discovery
from tokitty.settings import Settings, load_settings, save_settings
from tokitty.update_check import CheckResult
from tokitty.updater import Release

pytestmark = pytest.mark.qt

REPO_ROOT = Path(__file__).resolve().parents[2]
SEAMS = ("update_available_label", "on_install_update", "on_check_updates",
         "update_check_enabled", "on_toggle_update_check")
RELEASE = Release(tag="v9.0.0", html_url="https://example.invalid/r", asset_url=None,
                  asset_size=None, sums_url=None)


class FakeChecker:
    instances = []

    def __init__(self, state_dir, running, *, enabled, on_available):
        self.enabled_fn, self.on_available = enabled, on_available
        self.label = None
        self.latest_release = None
        self.release_history = None
        self.update_available = False
        self.ticks = 0
        self.waiters = []
        FakeChecker.instances.append(self)

    @property
    def enabled(self):
        return self.enabled_fn()

    def menu_label(self):
        return self.label

    def tick(self):
        self.ticks += 1

    def check_now(self, on_result):
        self.waiters.append(on_result)


class FakeUpdates:
    instances = []

    def __init__(self, state_dir, host):
        self.host = host
        self.running = SimpleNamespace(version="0.0.1")
        self.busy = False
        self.ticks = 0
        self.startups = []
        self.apply_done = None
        FakeUpdates.instances.append(self)

    def tick(self):
        self.ticks += 1

    def startup(self, *, frozen):
        self.startups.append(frozen)

    def start_apply_newest(self, on_done=None):
        self.apply_done = on_done
        return True


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tokitty.paths.get_state_dir", lambda: tmp_path)
    for name in ("TOKITTY_DEBUG_STATE", "TOKITTY_DEBUG_ACCOUNTS", "TOKITTY_CREDENTIALS", "TOKITTY_FIRST_RUN"):
        monkeypatch.delenv(name, raising=False)
    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    return tmp_path


@pytest.fixture
def fakes(monkeypatch):
    """No workers or network, and the fake controller and checker in place."""
    from tokitty import autostart, update_controller, update_check
    from tokitty.activity_watcher import ActivityWatcher
    from tokitty.poller import Poller
    from tokitty.usage_watcher import UsageWatcher

    for cls in (Poller, ActivityWatcher, UsageWatcher, Discovery):
        monkeypatch.setattr(cls, "start", lambda self: None)
    for cls in (Poller, ActivityWatcher, UsageWatcher):
        monkeypatch.setattr(cls, "stop", lambda self: None)
    monkeypatch.setattr(Poller, "get_latest", lambda self: None)
    monkeypatch.setattr(ActivityWatcher, "get_latest", lambda self: None)
    monkeypatch.setattr(UsageWatcher, "get_latest", lambda self: None)
    monkeypatch.setattr(autostart, "get_backend", lambda: None)
    FakeChecker.instances, FakeUpdates.instances = [], []
    monkeypatch.setattr(update_controller, "UpdateController", FakeUpdates)
    monkeypatch.setattr(update_check, "UpdateChecker", FakeChecker)
    return SimpleNamespace(updates=lambda: FakeUpdates.instances[0],
                           checker=lambda: FakeChecker.instances[0])


def run_with(monkeypatch, qapp, hook, **kwargs):
    """run_qt with `hook(widget)` run right after the widget shows, and a quit
    the test owns so a hung run cannot outlive it. Returns run_qt's code."""
    from tokitty.qt.app import run_qt
    from tokitty.qt.widget import TokittyQtWidget

    real_show = TokittyQtWidget.show
    done = []

    def show(self):
        real_show(self)
        if not done:
            done.append(1)
            hook(self)

    monkeypatch.setattr(TokittyQtWidget, "show", show)
    net = QTimer()
    net.setSingleShot(True)
    net.timeout.connect(qapp.quit)
    net.start(8000)
    try:
        return run_qt(**kwargs)
    finally:
        net.stop()


def host_of(fakes):
    return fakes.updates().host._host


def tick_timer(widget):
    return next(t for t in widget.findChildren(QTimer)
                if t.isActive() and not t.isSingleShot() and t.interval() in (100, UI_REFRESH_MS))


def settle(qapp):
    qapp.processEvents()
    qapp.processEvents()


def test_the_wiring_sets_the_five_update_seams(qapp, state_dir, fakes, monkeypatch):
    seen = {}

    def hook(widget):
        host = host_of(fakes)
        seen.update({name: getattr(host, name) for name in SEAMS})
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert all(callable(value) for value in seen.values()), seen
    assert fakes.updates().startups == [False]


def test_the_menu_shows_the_update_item_first_only_while_a_release_is_known(
        qapp, state_dir, fakes, monkeypatch):
    seen = {}

    def hook(widget):
        def visible():
            return [i.dynamic_label() if i.dynamic_label else i.label
                    for i in widget.build_menu_model(0)
                    if i.dynamic_label is None or i.dynamic_label()]

        seen["none"] = visible()
        fakes.checker().label = "Update to v9.0.0…"
        seen["known"] = visible()
        item = widget.build_menu_model(0)[0]
        seen["action"] = item.action
        seen["install"] = host_of(fakes).on_install_update
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["none"][0] == "Refresh now"
    assert seen["known"][0] == "Update to v9.0.0…"
    assert seen["action"] == seen["install"]


def test_the_updates_tab_has_the_rows_and_the_toggle_writes_settings(
        qapp, state_dir, fakes, monkeypatch):
    from tokitty.qt.settings import SettingsWindow

    seen = {}

    def hook(widget):
        window = SettingsWindow.open(host_of(fakes), 0)
        updates = window.tabs["updates"]
        seen["rows"] = (updates.update_toggle is not None, updates.update_pill is not None,
                        hasattr(window.tabs["general"], "update_toggle"))
        seen["before"] = load_settings(state_dir).update_check
        updates.update_toggle._clicked()
        seen["after"] = load_settings(state_dir).update_check
        seen["shadow"] = fakes.checker().enabled_fn()
        window.close()
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["rows"] == (True, True, False)
    assert seen["before"] is True
    assert seen["after"] is False and seen["shadow"] is False


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: shown.append(("critical", title, text)))
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(("information", title, text)))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: shown.append(("warning", title, text)))
    return shown


def test_check_now_shows_the_right_box_for_each_result(qapp, state_dir, fakes, monkeypatch, boxes):
    from tokitty.qt.settings import SettingsWindow

    offered = []
    monkeypatch.setattr(SettingsWindow, "open", classmethod(
        lambda cls, host, pane_index=None, tab=None: offered.append(tab)))
    results = [CheckResult(error="offline"), CheckResult(RELEASE, newer=True),
               CheckResult(RELEASE, newer=False)]
    seen = {}

    def hook(widget):
        host = host_of(fakes)
        checker = fakes.checker()
        for result in results:
            host.on_check_updates()
            # A second click while one is in flight does nothing.
            host.on_check_updates()
            assert len(checker.waiters) == 1
            checker.waiters.pop()(result)
            settle(qapp)
        seen["updates"] = fakes.updates()
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert boxes == [
        ("critical", "Check for updates", "Couldn't check for updates: offline"),
        ("information", "Check for updates", "Tokitty 0.0.1 is the latest version."),
    ]
    assert offered == ["updates"]


def test_install_offers_the_known_release_and_checks_first_otherwise(
        qapp, state_dir, fakes, monkeypatch, boxes):
    from tokitty.qt.settings import SettingsWindow

    offered = []
    monkeypatch.setattr(SettingsWindow, "open", classmethod(
        lambda cls, host, pane_index=None, tab=None: offered.append(tab)))
    seen = {}

    def hook(widget):
        host, checker = host_of(fakes), fakes.checker()
        host.on_install_update()
        seen["waiting"] = len(checker.waiters)
        checker.waiters.clear()
        checker.latest_release, checker.update_available = RELEASE, True
        host.on_install_update()
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["waiting"] == 1
    assert offered == ["updates"]


def test_the_tick_polls_the_update_core_and_speeds_up_while_busy(qapp, state_dir, fakes, monkeypatch):
    seen = {}

    def hook(widget):
        updates, checker, timer = fakes.updates(), fakes.checker(), tick_timer(widget)
        seen["start"] = timer.interval()
        timer.timeout.emit()
        seen["idle"] = timer.interval()
        updates.busy = True
        timer.timeout.emit()
        seen["busy"] = timer.interval()
        updates.busy = False
        timer.timeout.emit()
        seen["back"] = timer.interval()
        seen["ticks"] = (updates.ticks, checker.ticks)
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert (seen["start"], seen["idle"], seen["busy"], seen["back"]) == (
        UI_REFRESH_MS, UI_REFRESH_MS, 100, UI_REFRESH_MS)
    assert seen["ticks"] == (3, 3)


def test_hook_warnings_wait_while_the_update_host_is_hidden(
        qapp, state_dir, fakes, monkeypatch, boxes):
    from tokitty import update_controller

    monkeypatch.setattr(update_controller, "reclaim", lambda lock, state_dir, backend: None)
    # Queued only once the app is hidden, and only for this run's Discovery:
    # an earlier test's run_qt can leave a live tick timer behind (seen on
    # Windows CI), and its tick would take the warning through the class patch.
    queued, created = [], []
    real_init = Discovery.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(Discovery, "__init__", init)
    monkeypatch.setattr(Discovery, "take_hook_warnings",
                        lambda self: queued.pop(0) if queued and self is created[-1] else [])
    seen = {}

    def hook(widget):
        update_host = fakes.updates().host
        timer = tick_timer(widget)
        update_host.hide()
        queued.append(["a hook warning"])
        timer.timeout.emit()
        settle(qapp)
        seen["hidden"] = (list(boxes), len(queued))
        update_host.restore()
        timer.timeout.emit()
        settle(qapp)
        seen["restored"] = list(boxes)
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["hidden"] == ([], 1)
    assert seen["restored"] == [("warning", "Tokitty", "a hook warning")]


def test_a_check_result_waits_while_the_update_host_is_hidden(
        qapp, state_dir, fakes, monkeypatch, boxes):
    """Staging can finish and hide the app in the same tick that delivers a
    manual check: the result must not reopen the dialog over a handover."""
    from tokitty import update_controller
    from tokitty.qt.settings import SettingsWindow

    monkeypatch.setattr(update_controller, "reclaim", lambda lock, state_dir, backend: None)
    offered = []
    monkeypatch.setattr(SettingsWindow, "open", classmethod(
        lambda cls, host, pane_index=None, tab=None: offered.append(tab)))
    seen = {}

    def hook(widget):
        host, checker = host_of(fakes), fakes.checker()
        update_host = fakes.updates().host
        host.on_check_updates()
        update_host.hide()
        checker.waiters.pop()(CheckResult(RELEASE, newer=True))
        host.on_check_updates()
        checker.waiters.pop()(CheckResult(error="offline"))
        settle(qapp)
        seen["hidden"] = (list(offered), list(boxes))
        update_host.restore()
        settle(qapp)
        seen["restored"] = (list(offered), list(boxes))
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["hidden"] == ([], [])
    assert seen["restored"] == (["updates"], [("critical", "Check for updates", "Couldn't check for updates: offline")])


# ---- hand-offs --------------------------------------------------------------

@pytest.mark.parametrize("outcome, code", [
    ("installed", 0), ("rolled_back", 2), ("failed", 1), ("cancelled", 1)])
def test_apply_update_returns_the_exit_code_for_each_outcome(
        qapp, state_dir, fakes, monkeypatch, capsys, outcome, code):
    # A fake controller: the real one needs a frozen install to apply to.
    seen = {}

    def hook(widget):
        seen["pending"] = fakes.updates().apply_done is not None
        fakes.updates().apply_done(SimpleNamespace(outcome=outcome, message="why"))
        if outcome == "installed":
            # The real handover quits the app through finish(); the test does.
            QTimer.singleShot(50, qapp.quit)

    assert run_with(monkeypatch, qapp, hook, apply_update=True) == code
    assert seen["pending"]
    err = capsys.readouterr().err
    assert ("tokitty: update: why" in err) == (outcome != "installed")


def test_apply_update_shows_no_walkthrough_and_marks_nothing(qapp, tmp_path, monkeypatch, fakes):
    monkeypatch.setattr("tokitty.paths.get_state_dir", lambda: tmp_path)
    for name in ("TOKITTY_DEBUG_STATE", "TOKITTY_DEBUG_ACCOUNTS", "TOKITTY_CREDENTIALS", "TOKITTY_FIRST_RUN"):
        monkeypatch.delenv(name, raising=False)
    from tokitty.qt import walkthrough

    monkeypatch.setattr(walkthrough, "Walkthrough", lambda *a, **k: pytest.fail("walkthrough shown"))

    def hook(widget):
        fakes.updates().apply_done(SimpleNamespace(outcome="rolled_back", message="x"))

    assert run_with(monkeypatch, qapp, hook, apply_update=True) == 2
    assert load_settings(tmp_path).first_run != "pending"


def test_a_failed_after_update_lock_is_silent(qapp, state_dir, monkeypatch, capsys):
    from tokitty.lock import LockAcquisitionError
    from tokitty.qt.app import run_qt

    def busy(lock, timeout):
        raise LockAcquisitionError("held")

    monkeypatch.setattr("tokitty.lock.acquire_with_retry", busy)
    assert run_qt(after_update_token="a" * 32) == 1
    assert capsys.readouterr().err == ""


DRIVER = textwrap.dedent("""
    import os, sys
    from pathlib import Path

    state = Path(sys.argv[1])
    token = sys.argv[2]
    import tokitty.paths
    tokitty.paths.get_state_dir = lambda: state
    from PySide6.QtCore import QTimer
    from tokitty import autostart
    from tokitty.activity_watcher import ActivityWatcher
    from tokitty.app_core import Discovery
    from tokitty.poller import Poller
    from tokitty.qt import walkthrough
    from tokitty.qt.app import run_qt
    from tokitty.update_swap import ack_path
    from tokitty.usage_watcher import UsageWatcher

    for cls in (Poller, ActivityWatcher, UsageWatcher):
        cls.start = cls.stop = lambda self: None
        cls.get_latest = lambda self: None
    Discovery.start = lambda self: None
    autostart.get_backend = lambda: None

    def refuse(*a, **k):
        (state / "walkthrough-shown").write_text("1")
        raise SystemExit(9)
    walkthrough.Walkthrough = refuse

    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    ack = ack_path(state, token)

    def poll():
        if ack.exists():
            QTimer.singleShot(100, app.quit)
        else:
            QTimer.singleShot(50, poll)
    QTimer.singleShot(0, poll)
    QTimer.singleShot(15000, app.quit)
    sys.exit(run_qt(after_update_token=token))
""")


def run_driver(tmp_path, env_extra):
    from tokitty.update_swap import new_token

    state = tmp_path / "scratch-state"
    state.mkdir()
    token = new_token()
    # The real environment minus any tokitty switches: Windows needs SYSTEMROOT
    # and friends to start Python and Qt, and USERPROFILE for Path.home().
    env = {k: v for k, v in os.environ.items() if not k.startswith("TOKITTY_")}
    env.update({"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "PYTHONPATH": str(REPO_ROOT),
                "QT_QPA_PLATFORM": "offscreen", **env_extra})
    proc = subprocess.run([sys.executable, "-c", DRIVER, str(state), token], cwd=REPO_ROOT,
                          env=env, capture_output=True, text=True, timeout=60)
    return proc, state, token


def test_after_update_writes_the_ack_once_the_widget_is_exposed(tmp_path):
    import json
    from tokitty.update_swap import ack_path

    proc, state, token = run_driver(tmp_path, {"TOKITTY_UPDATE_API_URL": "http://127.0.0.1:9/none"})

    assert proc.returncode == 0, proc.stderr
    assert ack_path(state, token).read_text(encoding="utf-8") == token
    record = json.loads((state / "after-update-qt.json").read_text(encoding="utf-8"))
    assert record["exposed_before_ack"] is True
    assert 0 <= record["waited_s"] < 5
    # A fresh dir, yet no walkthrough and no first-run mark.
    assert not (state / "walkthrough-shown").exists()
    assert load_settings(state).first_run != "pending"


def test_after_update_without_the_test_api_url_writes_no_record(tmp_path):
    from tokitty.update_swap import ack_path

    proc, state, token = run_driver(tmp_path, {})

    assert proc.returncode == 0, proc.stderr
    assert ack_path(state, token).exists()
    assert not (state / "after-update-qt.json").exists()


def test_after_update_exits_before_acking_under_skips_ack(tmp_path):
    from tokitty.update_swap import ack_path

    proc, state, token = run_driver(tmp_path, {
        "TOKITTY_UPDATE_API_URL": "http://127.0.0.1:9/none", "TOKITTY_UPDATE_TEST_NO_ACK": "1"})

    assert proc.returncode == 0, proc.stderr
    assert not ack_path(state, token).exists()
    assert not (state / "after-update-qt.json").exists()


# ---- the tray wired into run_qt (#114 slice 2) -------------------------------


class FakeTray:
    """Stands in for QtTray: no QSystemTrayIcon, availability set per test."""

    available = True
    fail_start = False  # start() then drops availability, as QtTray's does
    instances = []

    def __init__(self, menu_provider, state_dir, colorway="orange", pattern="tabby", *,
                 on_action_done=None, **_):
        self.menu_provider, self.state_dir = menu_provider, state_dir
        self.colorway, self.pattern = colorway, pattern
        self.on_action_done = on_action_done
        self.available = type(self).available
        self.calls = []
        self.notified = []
        FakeTray.instances.append(self)

    def start(self):
        self.calls.append("start")
        if type(self).fail_start:
            self.available = False

    def stop(self):
        self.calls.append("stop")

    def set_look(self, colorway, pattern, tail="plain"):
        self.colorway, self.pattern = colorway, pattern

    def refresh(self):
        self.calls.append("refresh")

    def notify(self, title, message):
        self.notified.append((title, message))
        return True

    def set_enabled(self, enabled):
        from tokitty.settings import update_settings

        update_settings(self.state_dir, tray_enabled=enabled)
        if enabled:
            self.start()
        else:
            self.stop()


@pytest.fixture
def tray(monkeypatch):
    FakeTray.instances, FakeTray.available, FakeTray.fail_start = [], True, False
    monkeypatch.setattr("tokitty.qt.tray.QtTray", FakeTray)
    return SimpleNamespace(get=lambda: FakeTray.instances[0], cls=FakeTray)


def quit_soon(qapp):
    QTimer.singleShot(0, qapp.quit)


def test_the_general_tab_has_the_tray_row_only_with_an_available_tray(
        qapp, state_dir, fakes, tray, monkeypatch):
    from tokitty.qt.settings import SettingsWindow

    seen = {}
    for available in (True, False):
        tray.cls.available = available
        FakeUpdates.instances.clear()

        def hook(widget, available=available):
            window = SettingsWindow.open(host_of(fakes), 0)
            seen[available] = "tray" in window.tabs["general"].toggles
            window.close()
            quit_soon(qapp)

        assert run_with(monkeypatch, qapp, hook) == 0
    assert seen == {True: True, False: False}


def starts_and_stops(tray):
    return [call for call in tray.get().calls if call != "refresh"]


def test_the_tray_toggle_writes_settings_and_starts_and_stops_the_tray(
        qapp, state_dir, fakes, tray, monkeypatch):
    from tokitty.qt.settings import SettingsWindow

    seen = {}

    def hook(widget):
        window = SettingsWindow.open(host_of(fakes), 0)
        toggle = window.tabs["general"].toggles["tray"]
        seen["before"] = (toggle.value, load_settings(state_dir).tray_enabled)
        toggle._clicked()
        seen["on"] = (host_of(fakes).tray_enabled(), load_settings(state_dir).tray_enabled,
                      starts_and_stops(tray))
        toggle._clicked()
        seen["off"] = (host_of(fakes).tray_enabled(), load_settings(state_dir).tray_enabled,
                       starts_and_stops(tray))
        window.close()
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["before"] == (False, False)
    assert seen["on"][:2] == (True, True) and seen["on"][2][-1] == "start"
    assert seen["off"][:2] == (False, False) and seen["off"][2][-1] == "stop"


def test_the_tray_starts_after_launch_only_when_available_and_enabled(
        qapp, state_dir, fakes, tray, monkeypatch):
    seen = []
    for available, enabled in ((True, True), (True, False), (False, True)):
        tray.cls.available = available
        save_settings(state_dir, Settings(tray_enabled=enabled, surprise_me=False, first_run="done"))
        calls = []

        def hook(widget, calls=calls):
            calls.extend(tray.get().calls)
            quit_soon(qapp)

        FakeTray.instances.clear()
        assert run_with(monkeypatch, qapp, hook) == 0
        seen.append(("start" in calls, tray.get().calls[-1]))
    assert seen == [(True, "stop"), (False, "stop"), (False, "stop")]


def test_a_menu_action_refreshes_the_tray_and_the_tray_gets_the_menu_model(
        qapp, state_dir, fakes, tray, monkeypatch):
    seen = {}

    def hook(widget):
        t = tray.get()
        before = t.calls.count("refresh")
        widget.on_menu_action_done()
        seen["refreshed"] = t.calls.count("refresh") - before
        seen["tray_action_done"] = t.on_action_done == host_of(fakes).notify_state_changed
        seen["menu"] = [item.label for item in t.menu_provider()]
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["refreshed"] == 1 and seen["tray_action_done"]
    assert "Refresh now" in seen["menu"]


def test_the_onboarder_is_given_the_trays_refresh(qapp, state_dir, fakes, tray, monkeypatch):
    got = []
    monkeypatch.setattr("tokitty.qt.app.make_onboarder",
                        lambda state_dir, usage_state, on_changed: got.append(on_changed) or (lambda *a: None))
    assert run_with(monkeypatch, qapp, lambda widget: quit_soon(qapp)) == 0
    assert len(got) == 1 and got[0] == tray.get().refresh


def test_surprise_me_colours_reach_the_tray_and_the_update_host_holds_it(
        qapp, state_dir, fakes, tray, monkeypatch):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=True, first_run="done"))
    seen = {}

    def hook(widget):
        pane = widget.panes[0]
        t = tray.get()
        seen["colours"] = ((t.colorway, t.pattern), (pane._colorway, pane._pattern))
        seen["same"] = fakes.updates().host._tray is t
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    tray_colours, pane_colours = seen["colours"]
    assert tray_colours == pane_colours
    assert seen["same"]


def test_update_host_gets_a_tray_enabled_that_reads_the_settings_file(
        qapp, state_dir, fakes, tray, monkeypatch):
    seen = []

    def hook(widget):
        enabled = fakes.updates().host._tray_enabled
        seen.append(enabled())
        save_settings(state_dir, Settings(tray_enabled=True, surprise_me=False, first_run="done"))
        seen.append(enabled())
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen == [False, True]


def test_the_toggle_and_the_update_host_read_the_same_tray_setting(
        qapp, state_dir, fakes, tray, monkeypatch):
    from tokitty.qt.settings import SettingsWindow

    seen = {}

    def hook(widget):
        host = host_of(fakes)
        update_host = fakes.updates().host
        seen["same_getter"] = update_host._tray_enabled is host.tray_enabled
        window = SettingsWindow.open(host, 0)
        toggle = window.tabs["general"].toggles["tray"]
        toggle._clicked()
        seen["on"] = (host.tray_enabled(), update_host._tray_enabled())
        # A handover that is rolled back restores what the user chose.
        update_host.hide()
        tray.get().calls.clear()
        update_host.restore()
        seen["restore_on"] = list(tray.get().calls)
        toggle._clicked()
        seen["off"] = (host.tray_enabled(), update_host._tray_enabled())
        update_host.hide()
        tray.get().calls.clear()
        update_host.restore()
        seen["restore_off"] = list(tray.get().calls)
        window.close()
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["same_getter"]
    assert seen["on"] == (True, True) and seen["off"] == (False, False)
    assert seen["restore_on"] == ["start", "refresh"]
    assert seen["restore_off"] == ["refresh"]
    assert load_settings(state_dir).tray_enabled is False


def test_a_tray_that_fails_to_start_leaves_the_settings_row_and_the_restore(
        qapp, state_dir, fakes, tray, monkeypatch):
    from tokitty.qt.settings import SettingsWindow

    tray.cls.fail_start = True
    seen = {}

    def hook(widget):
        host = host_of(fakes)
        update_host = fakes.updates().host
        window = SettingsWindow.open(host, 0)
        toggle = window.tabs["general"].toggles["tray"]
        seen["before"] = (host.tray_available(), toggle.isVisibleTo(window))
        toggle._clicked()  # writes True, then start() fails
        seen["after"] = (host.tray_available(), toggle.isVisibleTo(window),
                         load_settings(state_dir).tray_enabled)
        window.close()
        seen["reopened"] = "tray" in SettingsWindow.open(host, 0).tabs["general"].toggles
        update_host.hide()
        tray.get().calls.clear()
        update_host.restore()
        seen["restore"] = list(tray.get().calls)
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["before"] == (True, True)
    assert seen["after"] == (False, False, True)
    assert seen["reopened"] is False
    assert seen["restore"] == ["refresh"]  # no start


def test_the_announcer_notifies_through_the_tray(qapp, state_dir, fakes, tray, monkeypatch):
    seen = {}

    def hook(widget):
        fakes.checker().on_available(RELEASE)
        seen["notified"] = list(tray.get().notified)
        seen["refreshes"] = tray.get().calls.count("refresh")
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["notified"] == [("Tokitty v9.0.0 is available", "Right-click Tokitty to update")]
    assert seen["refreshes"] >= 1


def test_the_tray_is_stopped_when_the_app_exits(qapp, state_dir, fakes, tray, monkeypatch):
    assert run_with(monkeypatch, qapp, lambda widget: quit_soon(qapp)) == 0
    assert tray.get().calls[-1] == "stop"


def test_about_to_quit_stops_the_tray_before_the_loop_ends(qapp, state_dir, fakes, tray, monkeypatch):
    seen = {}

    def hook(widget):
        seen["before"] = tray.get().calls.count("stop")
        qapp.aboutToQuit.emit()
        seen["after"] = tray.get().calls.count("stop")
        quit_soon(qapp)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["after"] == seen["before"] + 1


def test_a_debug_launch_builds_no_tray(qapp, state_dir, fakes, tray, monkeypatch):
    monkeypatch.setenv("TOKITTY_DEBUG_STATE", "content")
    assert run_with(monkeypatch, qapp, lambda widget: quit_soon(qapp)) == 0
    assert FakeTray.instances == []


def test_after_update_waits_for_the_old_copys_lock_then_acks(tmp_path):
    # The handover as a shipped updater runs it: the old copy still holds the
    # lock when it starts the new one, and releases it as it hides.
    import time

    from tokitty.lock import SingleInstanceLock
    from tokitty.update_swap import ack_path, new_token

    state = tmp_path / "scratch-state"
    state.mkdir()
    token = new_token()
    old = SingleInstanceLock(state)
    old.acquire()
    env = {k: v for k, v in os.environ.items() if not k.startswith("TOKITTY_")}
    env.update({"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "PYTHONPATH": str(REPO_ROOT),
                "QT_QPA_PLATFORM": "offscreen"})
    proc = subprocess.Popen([sys.executable, "-c", DRIVER, str(state), token], cwd=REPO_ROOT,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(1.5)
        assert proc.poll() is None, "the new copy gave up on the held lock"
        assert not ack_path(state, token).exists()
        old.release()
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    assert ack_path(state, token).read_text(encoding="utf-8") == token

