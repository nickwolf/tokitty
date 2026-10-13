"""The updater wired into run_qt (#113 slice 3): the seams, the menu item, the
Updates rows, the tick, and the two update hand-offs. The controller and
checker are fakes in-process; the after-update path also runs for real in a
subprocess with a scratch state dir."""
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
        self.enabled, self.on_available = enabled, on_available
        self.label = None
        self.latest_release = None
        self.update_available = False
        self.ticks = 0
        self.waiters = []
        FakeChecker.instances.append(self)

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


def test_general_shows_the_updates_rows_and_the_toggle_writes_settings(
        qapp, state_dir, fakes, monkeypatch):
    from tokitty.qt.settings import SettingsWindow

    seen = {}

    def hook(widget):
        window = SettingsWindow.open(host_of(fakes), 0)
        general = window.tabs["general"]
        seen["rows"] = (general.update_toggle is not None, general.update_pill is not None)
        seen["before"] = load_settings(state_dir).update_check
        general.update_toggle._clicked()
        seen["after"] = load_settings(state_dir).update_check
        seen["shadow"] = fakes.checker().enabled()
        window.close()
        QTimer.singleShot(0, qapp.quit)

    assert run_with(monkeypatch, qapp, hook) == 0
    assert seen["rows"] == (True, True)
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
    from tokitty.qt.update_dialog import QtUpdateDialog

    offered = []
    monkeypatch.setattr(QtUpdateDialog, "open", classmethod(
        lambda cls, host, release, running, controller, open_url=None:
        offered.append((release, running, controller))))
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
    assert offered == [(RELEASE, "0.0.1", seen["updates"])]


def test_install_offers_the_known_release_and_checks_first_otherwise(
        qapp, state_dir, fakes, monkeypatch, boxes):
    from tokitty.qt.update_dialog import QtUpdateDialog

    offered = []
    monkeypatch.setattr(QtUpdateDialog, "open", classmethod(
        lambda cls, host, release, running, controller, open_url=None: offered.append(release)))
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
    assert offered == [RELEASE]


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
    queued = [["a hook warning"]]
    monkeypatch.setattr(Discovery, "take_hook_warnings", lambda self: queued.pop(0) if queued else [])
    seen = {}

    def hook(widget):
        update_host = fakes.updates().host
        timer = tick_timer(widget)
        update_host.hide()
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
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(REPO_ROOT),
           "QT_QPA_PLATFORM": "offscreen", **env_extra}
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
