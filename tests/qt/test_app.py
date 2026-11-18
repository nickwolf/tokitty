"""run_qt end to end under the offscreen platform."""
import sys
import time
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

from tokitty.accounts import load_accounts
from tokitty.api import UsageSnapshot
from tokitty.customize import Customization
from tokitty.poller import PollResult
from tokitty.streamdock.pending import PendingRequest
from tokitty.qt.pane import QtPane
from tokitty.settings import Settings, load_settings, save_settings

pytestmark = pytest.mark.qt


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tokitty.paths.get_state_dir", lambda: tmp_path)
    for name in ("TOKITTY_DEBUG_STATE", "TOKITTY_DEBUG_ACCOUNTS", "TOKITTY_CREDENTIALS", "TOKITTY_FIRST_RUN"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def run_for(qapp, ms):
    from tokitty.qt.app import run_qt

    QTimer.singleShot(ms, qapp.quit)
    return run_qt()


def run_until(qapp, done, ceiling_ms=20000):
    """Run the app until done() is true, checked every 20 ms, then quit. The
    ceiling only stops a broken test from hanging; a slow CI runner gets all
    of it, where a fixed window could end before the first tick (#165)."""
    from tokitty.qt.app import run_qt

    deadline = time.monotonic() + ceiling_ms / 1000
    check = QTimer()
    check.setInterval(20)
    check.timeout.connect(lambda: (done() or time.monotonic() > deadline) and qapp.quit())
    check.start()
    try:
        return run_qt()
    finally:
        check.stop()


def test_debug_state_launch_builds_one_pane_and_writes_only_the_lock(qapp, state_dir, monkeypatch):
    monkeypatch.setenv("TOKITTY_DEBUG_STATE", "content")
    panes = []
    real_render = QtPane.render
    monkeypatch.setattr(QtPane, "render", lambda self, **kw: panes.append((self, kw)) or real_render(self, **kw))

    assert run_for(qapp, 100) == 0

    assert len(panes) == 1
    assert panes[0][1]["state"] == "content"
    assert [p.name for p in state_dir.iterdir()] == ["tokitty.lock"]


def test_debug_accounts_launch_builds_two_panes_and_writes_only_the_lock(qapp, state_dir, monkeypatch):
    monkeypatch.setenv("TOKITTY_DEBUG_ACCOUNTS", "2")
    panes = []
    real_render = QtPane.render
    monkeypatch.setattr(QtPane, "render", lambda self, **kw: panes.append((self, kw)) or real_render(self, **kw))

    assert run_for(qapp, 100) == 0

    assert [kw["state"] for _, kw in panes] == ["content", "sleeping"]
    assert panes[1][1]["dimmed"] is True
    assert [p.name for p in state_dir.iterdir()] == ["tokitty.lock"]



def test_debug_launch_has_a_menu_to_exit_by(qapp, state_dir, monkeypatch):
    # The Tk debug window has a menu; without one a frameless Qt window
    # cannot be closed short of killing the process.
    from tokitty.qt.widget import TokittyQtWidget

    monkeypatch.setenv("TOKITTY_DEBUG_STATE", "content")
    shown = []
    real_show = TokittyQtWidget.show
    monkeypatch.setattr(TokittyQtWidget, "show", lambda self: shown.append(self) or real_show(self))

    assert run_for(qapp, 100) == 0

    labels = [item.label for item in shown[0].build_menu_model(0)]
    assert "Always in front" in labels
    assert labels[-1] == "Exit"


@pytest.fixture
def fake_workers(monkeypatch):
    """Class-level fakes: the first poll result is published straight away and
    nothing touches the network, WSL or the transcripts."""
    from tokitty.activity_watcher import ActivityWatcher
    from tokitty.app_core import Discovery
    from tokitty.poller import Poller
    from tokitty.usage_watcher import UsageWatcher

    log = []
    snapshot = UsageSnapshot(
        session_pct=40.0, session_resets_at=datetime(2030, 1, 2, 15, 0, tzinfo=timezone.utc),
        weekly_pct=20.0, weekly_resets_at=datetime(2030, 1, 6, 15, 0, tzinfo=timezone.utc),
        limits=[], fetched_at=datetime.now(timezone.utc),
    )
    poll = PollResult(status="ok", snapshot=snapshot, message=None, fetched_at=snapshot.fetched_at)
    for cls in (Poller, ActivityWatcher, UsageWatcher):
        monkeypatch.setattr(cls, "start", lambda self, n=cls.__name__: log.append(("start", n)))
        monkeypatch.setattr(cls, "stop", lambda self, n=cls.__name__: log.append(("stop", n)))
    monkeypatch.setattr(Poller, "get_latest", lambda self: poll)
    monkeypatch.setattr(ActivityWatcher, "get_latest", lambda self: None)
    monkeypatch.setattr(UsageWatcher, "get_latest", lambda self: None)
    monkeypatch.setattr(Discovery, "start", lambda self: log.append(("start", "Discovery")))
    return log


def test_normal_launch_renders_a_published_result_and_stops_the_workers(
    qapp, state_dir, monkeypatch, fake_workers
):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False))
    renders = []
    monkeypatch.setattr(QtPane, "render", lambda self, **kw: renders.append(kw))
    notes = []
    monkeypatch.setattr("tokitty.usage_notes.write_usage_file", lambda *a: notes.append(a))
    monkeypatch.setattr("tokitty.usage_notes.remove_usage_file", lambda *a: None)

    assert run_until(qapp, lambda: renders) == 0

    assert renders, "the tick never rendered"
    assert renders[0]["session_pct"] == 40.0
    assert renders[0]["weekly_pct"] == 20.0
    assert ("start", "Poller") in fake_workers and ("start", "Discovery") in fake_workers
    assert ("stop", "Poller") in fake_workers
    assert ("stop", "ActivityWatcher") in fake_workers
    assert ("stop", "UsageWatcher") in fake_workers
    assert (state_dir / "customization.json").exists()


def test_a_second_instance_is_refused(qapp, state_dir, fake_workers, capsys):
    from tokitty.lock import SingleInstanceLock
    from tokitty.qt.app import run_qt

    held = SingleInstanceLock(state_dir)
    held.acquire()
    try:
        assert run_qt() == 1
    finally:
        held.release()
    assert "already running" in capsys.readouterr().err


def test_view_menu_item_writes_the_setting(qapp, state_dir, monkeypatch, fake_workers):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    from tokitty.qt.widget import TokittyQtWidget

    seen = {}
    real_show = TokittyQtWidget.show

    def show(self):
        # The update item is in the model but hidden while no release is known.
        seen["labels"] = [i.label for i in self.build_menu_model(0)
                          if i.dynamic_label is None or i.dynamic_label()]
        view = next(i for i in self.build_menu_model(0) if i.label == "View")
        next(i for i in view.submenu if i.label == "Per-model").action()
        real_show(self)

    monkeypatch.setattr(TokittyQtWidget, "show", show)

    assert run_for(qapp, 100) == 0

    assert seen["labels"] == ["Refresh now", "View", "Always in front", "", "Settings…", "About Tokitty", "", "Exit"]
    assert load_settings(state_dir).view_mode == "models"


def safety_net(qapp, ms=5000):
    """A quit timer the test stops itself: a leftover singleShot would fire
    inside a later test's event loop."""
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(qapp.quit)
    timer.start(ms)
    return timer


def capture_widget(monkeypatch):
    """Collect the widget run_qt builds, and run `hook(widget)` right after it shows."""
    from tokitty.qt.widget import TokittyQtWidget

    seen = {"widgets": [], "hook": None}
    real_show = TokittyQtWidget.show

    def show(self):
        seen["widgets"].append(self)
        real_show(self)
        hook, seen["hook"] = seen["hook"], None  # one shot: a re-show must not rerun it
        if hook is not None:
            hook(self)

    monkeypatch.setattr(TokittyQtWidget, "show", show)
    return seen


def settings_action(widget, pane_index=0):
    return next(i for i in widget.build_menu_model(pane_index) if i.label == "Settings…").action


def test_the_menus_settings_item_opens_the_window_on_that_pane(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.qt import settings as qt_settings

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    seen = capture_widget(monkeypatch)
    found = {}

    def hook(widget):
        # a two-pane menu would pass 1; one pane here, so check the wiring and the window
        settings_action(widget)()
        found["window"] = qt_settings._instance
        found["pane"] = qt_settings._instance.pane_index
        found["parent"] = qt_settings._instance.parent()
        qt_settings._instance.close()

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["window"] is not None and found["pane"] == 0 and found["parent"] is None


def capture_host(monkeypatch):
    """Collect the QtHost run_qt builds."""
    from tokitty.qt.host import QtHost

    seen = []
    real_init = QtHost.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        seen.append(self)

    monkeypatch.setattr(QtHost, "__init__", init)
    return seen


def capture_units(monkeypatch):
    """The units list run_qt builds; a rebuild changes it in place."""
    from tokitty.qt import app

    seen = []
    real = app.build_units

    def build_units(**kwargs):
        units = real(**kwargs)
        seen.append(units)
        return units

    monkeypatch.setattr(app, "build_units", build_units)
    return seen


def test_the_host_gets_discoverys_homes_and_the_accounts_hook(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.app_core import Discovery

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    monkeypatch.setattr(Discovery, "wsl_matches",
                        lambda self: [("Ubuntu", "/home/u/.claude/.credentials.json")])
    hosts = capture_host(monkeypatch)
    seen = capture_widget(monkeypatch)
    found = {}

    from tokitty import wsl_probe
    from tokitty.distro_probe import RunningDistroProbe

    monkeypatch.setattr(RunningDistroProbe, "get_running", lambda self: ["Ubuntu"])
    monkeypatch.setattr(wsl_probe, "find_running_wsl_credentials", lambda running: [
        ("Ubuntu", "/home/u/.claude/.credentials.json"),
        ("Ubuntu", "/home/u/.claude-work/.credentials.json")])

    def hook(widget):
        host = hosts[0]
        with monkeypatch.context() as m:
            m.setattr(sys, "platform", "win32")
            found["homes"] = host.found_homes()
        found["hook"] = host.on_accounts_changed
        # The Manage window is gone (#268): nothing opens it any more.
        found["manage"] = getattr(host, "on_open_accounts", None)

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    # Discovery's startup list, then what a fresh sweep adds, no repeats.
    assert found["homes"] == [("Ubuntu", "/home/u/.claude/.credentials.json"),
                              ("Ubuntu", "/home/u/.claude-work/.credentials.json")]
    assert callable(found["hook"]) and found["manage"] is None


def test_found_homes_sweeps_running_distros_only_on_windows():
    from tokitty.qt.app import make_found_homes

    swept = []

    def sweep(running):
        swept.append(running)
        return [("Ubuntu", "/home/u/.claude-edlio/.credentials.json")]

    found = make_found_homes(lambda: [], lambda: ["Ubuntu"], platform="win32", sweep=sweep)
    assert found() == [("Ubuntu", "/home/u/.claude-edlio/.credentials.json")]
    assert swept == [["Ubuntu"]]
    elsewhere = make_found_homes(lambda: [], lambda: ["Ubuntu"], platform="linux", sweep=sweep)
    assert elsewhere() == [] and len(swept) == 1


# -- applying account changes without a restart (#166) ------------------------


def account(state_dir, name, provider="claude"):
    from tokitty.accounts import Account

    return Account(name=f"acct-v1-{name}", config_dir=str(state_dir / name), provider=provider)


def launch_with(state_dir, monkeypatch, accounts, labels=None):
    """Settings and accounts for a launch, and the hooks to reach its pieces."""
    from tokitty.accounts import save_accounts
    from tokitty.customize import save_customization

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    if accounts:
        save_accounts(state_dir, accounts)
    if labels:
        save_customization(state_dir, {
            f"acct-v1-{name}": Customization(colorway="gray", pattern="solid", label=label)
            for name, label in labels.items()
        })
    return capture_host(monkeypatch), capture_units(monkeypatch), capture_widget(monkeypatch)


def change_accounts(host, state_dir, accounts):
    from tokitty.accounts import save_accounts

    save_accounts(state_dir, accounts)
    host.accounts_changed()


def test_adding_an_account_starts_only_its_workers_and_retags_the_panes(
        qapp, state_dir, monkeypatch, fake_workers):
    a, b = account(state_dir, "a"), account(state_dir, "b", provider="codex")
    hosts, units_seen, seen = launch_with(state_dir, monkeypatch, [a], labels={"a": "Work"})
    found = {}

    def hook(widget):
        units = units_seen[0]
        found["before"] = (units[0], units[0]["poller"], widget.panes[0]._label)
        del fake_workers[:]
        change_accounts(hosts[0], state_dir, [a, b])
        found["log"] = list(fake_workers)
        found["units"] = list(units)
        found["labels"] = [pane._label for pane in widget.panes]
        found["panes"] = [unit["pane"] for unit in units] == widget.panes

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    old_unit, old_poller, old_label = found["before"]
    assert old_label == "Work"
    assert found["units"][0] is old_unit and old_unit["poller"] is old_poller
    assert sorted(found["log"]) == [("start", "ActivityWatcher"), ("start", "Poller"), ("start", "UsageWatcher")]
    assert found["labels"] == ["claude\u00b7Work", "codex"]
    assert found["panes"] is True


def test_removing_an_account_stops_its_workers_off_the_gui_thread(
        qapp, state_dir, monkeypatch, fake_workers):
    import threading

    from tokitty.poller import Poller

    a, b = account(state_dir, "a"), account(state_dir, "b")
    hosts, units_seen, seen = launch_with(state_dir, monkeypatch, [a, b])
    stopped = threading.Event()
    stops = []

    def stop(self):
        stops.append((self, threading.current_thread()))
        stopped.set()

    monkeypatch.setattr(Poller, "stop", stop)
    found = {}

    def hook(widget):
        units = units_seen[0]
        gone, kept = units
        found["gone"], found["kept"] = gone["poller"], kept
        change_accounts(hosts[0], state_dir, [b])
        found["units"] = list(units)
        found["panes"] = len(widget.panes)
        found["stopped"] = stopped.wait(5)

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["stopped"] and found["units"] == [found["kept"]] and found["panes"] == 1
    poller, thread = stops[0]
    assert poller is found["gone"]
    assert thread is not threading.main_thread()


def test_reordering_starts_and_stops_nothing_and_the_labels_follow(
        qapp, state_dir, monkeypatch, fake_workers):
    a, b = account(state_dir, "a"), account(state_dir, "b")
    hosts, units_seen, seen = launch_with(state_dir, monkeypatch, [a, b], labels={"a": "Work", "b": "Home"})
    found = {}

    def hook(widget):
        units = units_seen[0]
        first, second = units
        del fake_workers[:]
        change_accounts(hosts[0], state_dir, [b, a])
        found["log"] = list(fake_workers)
        found["permuted"] = units[0] is second and units[1] is first
        found["labels"] = [pane._label for pane in widget.panes]

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["log"] == [] and found["permuted"]
    assert found["labels"] == ["Home", "Work"]


def test_the_same_list_again_or_a_malformed_file_keeps_the_panes(
        qapp, state_dir, monkeypatch, fake_workers):
    a, b = account(state_dir, "a"), account(state_dir, "b")
    hosts, _units, seen = launch_with(state_dir, monkeypatch, [a, b])
    found = {}

    def hook(widget):
        panes = list(widget.panes)
        del fake_workers[:]
        change_accounts(hosts[0], state_dir, [a, b])
        found["same"] = widget.panes == panes
        (state_dir / "accounts.json").write_text("{not json", encoding="utf-8")
        hosts[0].accounts_changed()
        found["malformed"] = widget.panes == panes
        found["log"] = list(fake_workers)

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["same"] and found["malformed"] and found["log"] == []


def test_removing_every_account_leaves_the_default_pane(qapp, state_dir, monkeypatch, fake_workers):
    a, b = account(state_dir, "a"), account(state_dir, "b")
    hosts, units_seen, seen = launch_with(state_dir, monkeypatch, [a, b])
    found = {}

    def hook(widget):
        change_accounts(hosts[0], state_dir, [])
        units = units_seen[0]
        found["units"] = [(unit["key"], unit["account"]) for unit in units]
        found["panes"] = len(widget.panes)

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["units"] == [("default", None)] and found["panes"] == 1


def test_a_hand_edit_of_accounts_json_is_picked_up_by_the_tick(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.accounts import save_accounts
    from tokitty.qt import app

    monkeypatch.setattr(app, "ACCOUNTS_CHECK_S", 0.0)
    a, b = account(state_dir, "a"), account(state_dir, "b")
    _hosts, _units, seen = launch_with(state_dir, monkeypatch, [a])
    widgets = []

    def hook(widget):
        widgets.append(widget)
        save_accounts(state_dir, [a, b])

    seen["hook"] = hook
    assert run_until(qapp, lambda: widgets and len(widgets[0].panes) == 2) == 0
    assert len(widgets[0].panes) == 2


def test_a_change_waits_for_an_open_modal_dialog(qapp, state_dir, monkeypatch, fake_workers):
    # A dialog that read a pane index before opening must not see the panes
    # change under it; the tick applies the change once it closes.
    from PySide6.QtWidgets import QDialog

    from tokitty.qt import app

    monkeypatch.setattr(app, "ACCOUNTS_CHECK_S", 0.0)
    a, b, c = account(state_dir, "a"), account(state_dir, "b"), account(state_dir, "c")
    hosts, _units, seen = launch_with(state_dir, monkeypatch, [a, b, c])
    widgets, found = [], {}

    def hook(widget):
        widgets.append(widget)
        dialog = QDialog()
        dialog.setModal(True)
        dialog.show()
        change_accounts(hosts[0], state_dir, [b, c])
        found["while_open"] = len(widget.panes)
        dialog.close()

    seen["hook"] = hook
    assert run_until(qapp, lambda: widgets and len(widgets[0].panes) == 2) == 0
    assert found["while_open"] == 3 and len(widgets[0].panes) == 2


def test_an_open_settings_window_keeps_its_pane_in_range(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.qt import settings as qt_settings

    a, b = account(state_dir, "a"), account(state_dir, "b")
    hosts, _units, seen = launch_with(state_dir, monkeypatch, [a, b], labels={"a": "Work", "b": "Home"})
    found = {}

    def hook(widget):
        settings_action(widget, 1)()
        window = qt_settings._instance
        window.select_tab("look")
        found["before"] = window.pane_index
        change_accounts(hosts[0], state_dir, [a])
        found["after"] = window.pane_index
        found["names"] = window.pane_names()
        found["picked"] = window.tabs["look"].pane_picker.get()
        window.close()

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["before"] == 1 and found["after"] == 0
    assert found["names"] == ["Work"] and found["picked"] == "Work"


def test_an_account_change_closes_the_deck_views_and_restarts_the_deck(
        qapp, state_dir, monkeypatch, fake_deck):
    a, b = account(state_dir, "a"), account(state_dir, "b")
    hosts, _units, seen = launch_with(state_dir, monkeypatch, [a])
    found = {}

    def hook(widget):
        del fake_deck.log[:]
        change_accounts(hosts[0], state_dir, [a, b])
        found["log"] = settled(fake_deck.log, "views.close_all", "deck.restart")

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["log"] == [("views.close_all",), ("deck.restart",)]


def test_closing_settings_leaves_the_event_loop_running(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.qt import settings as qt_settings
    from tokitty.qt.app import run_qt

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    seen = capture_widget(monkeypatch)
    reached = {"after_close": False}

    def hook(widget):
        settings_action(widget)()
        QTimer.singleShot(50, lambda: qt_settings._instance.close())

        def after_close():
            reached["after_close"] = True
            qapp.quit()

        QTimer.singleShot(400, after_close)

    seen["hook"] = hook
    net = safety_net(qapp)
    assert run_qt() == 0
    net.stop()
    assert reached["after_close"], "closing Settings ended the event loop"
    assert qapp.quitOnLastWindowClosed() is False


def test_closing_the_widget_ends_the_run(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.qt.app import run_qt

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    seen = capture_widget(monkeypatch)
    seen["hook"] = lambda widget: QTimer.singleShot(50, widget.close)
    net = safety_net(qapp)
    start = datetime.now()
    assert run_qt() == 0
    net.stop()
    assert (datetime.now() - start).total_seconds() < 4


def test_the_menu_action_done_hook_refreshes_settings(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.qt import settings as qt_settings

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    seen = capture_widget(monkeypatch)
    states = []

    def hook(widget):
        settings_action(widget)()
        toggle = qt_settings._instance.tabs["general"].toggles["aot"]
        states.append(toggle.get())
        always = next(i for i in widget.build_menu_model(0) if i.label == "Always in front")
        widget._after_menu_action(always.action)()
        states.append(toggle.get())
        states.append(bool(qt_settings._instance.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        qt_settings._instance.close()

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert states == [True, False, False]


def test_surprise_me_randomizes_every_pane_at_startup(qapp, state_dir, monkeypatch, fake_workers):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=True, first_run="done"))
    calls = []
    from tokitty import app_controls

    monkeypatch.setattr(app_controls, "random_look", lambda cws, pats: (calls.append(1), (cws[2], pats[2]))[1])
    seen = capture_widget(monkeypatch)
    assert run_for(qapp, 150) == 0
    pane = seen["widgets"][0].panes[0]
    assert calls and (pane._colorway, pane._pattern) != ("orange", "tabby")


def test_apply_customization_clears_overrides_that_are_gone():
    from tokitty.qt.app import apply_customization

    pane = QtPane(Customization(overrides={"card_bg": "#112233", "bar_fill": "#445566"}))
    apply_customization(pane, Customization(colorway="gray", overrides={}))
    assert pane.customization.overrides == {}
    assert pane._colorway == "gray"


def test_the_menu_has_about_after_settings_and_no_app_menu_exists(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.qt import app as qt_app

    assert not hasattr(qt_app, "make_app_menu")
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    seen = {}
    abouts = []
    monkeypatch.setattr(qt_app, "show_about", lambda host, pane0, **kw: abouts.append((host, pane0)))
    from tokitty.qt.widget import TokittyQtWidget

    real_show = TokittyQtWidget.show

    def show(self):
        items = self.build_menu_model(0)
        labels = [i.label for i in items]
        seen["after_settings"] = labels[labels.index("Settings…") + 1]
        next(i for i in items if i.label == "About Tokitty").action()
        real_show(self)

    monkeypatch.setattr(TokittyQtWidget, "show", show)
    assert run_for(qapp, 100) == 0
    assert seen["after_settings"] == "About Tokitty"
    assert len(abouts) == 1 and abouts[0][1] is not None


# -- the first-run walkthrough ------------------------------------------------


class FakeWalkthrough(QWidget):
    """Stands in for the real window: records what run_qt built it with and
    what was running when it showed, then finishes from a timer, as a user
    pressing Finish would after saving two accounts."""

    closed = Signal()
    quit_requested = Signal()
    instances = []
    save_accounts_on_finish = True
    refuse_close = False
    when_shown = None  # called with the instance in show()

    def __init__(self, state_dir, credentials_cache=None, running_distros=None):
        super().__init__()
        self.state_dir = state_dir
        self.credentials_cache = credentials_cache
        self.running_distros = running_distros
        self.shown = False
        FakeWalkthrough.instances.append(self)

    def show(self):
        self.shown = True
        super().show()  # visible, so a Quit closes it as it would the real one
        if FakeWalkthrough.when_shown is not None:
            FakeWalkthrough.when_shown(self)
        QTimer.singleShot(30, self.finish)

    def quit_app(self):
        # As the real one: refused while an install runs, else ends the
        # launch without marking onboarding done.
        if FakeWalkthrough.refuse_close:
            return
        self.hide()
        self.quit_requested.emit()

    def finish(self):
        from tokitty import first_run
        from tokitty.accounts import Account, save_accounts

        if FakeWalkthrough.save_accounts_on_finish:
            save_accounts(self.state_dir, [
                Account(name="one", config_dir=str(self.state_dir / "c1"), provider="claude"),
                Account(name="two", config_dir=str(self.state_dir / "c2"), provider="claude"),
            ])
        first_run.mark_done(self.state_dir)
        self.emitted = True
        self.hide()  # the real one closes on Finish
        self.closed.emit()

    def closeEvent(self, event):
        # As the real one: a close (button, or Qt closing every window on a
        # Quit) is the end of the walkthrough, unless an install is running.
        if FakeWalkthrough.refuse_close:
            event.ignore()
            return
        if not getattr(self, "emitted", False):
            self.emitted = True
            self.closed.emit()
        super().closeEvent(event)


def fake_home(monkeypatch, tmp_path):
    """An empty home outside the state dir (which is tmp_path), so no real
    credentials are found and the state dir stays fresh."""
    home = tmp_path.parent / (tmp_path.name + "_home")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def fake_walkthrough(monkeypatch, tmp_path):
    from tokitty.qt import walkthrough as qt_walkthrough

    FakeWalkthrough.instances = []
    FakeWalkthrough.save_accounts_on_finish = True
    FakeWalkthrough.when_shown = None
    FakeWalkthrough.refuse_close = False
    monkeypatch.setattr(qt_walkthrough, "Walkthrough", FakeWalkthrough)
    FakeWalkthrough.home = fake_home(monkeypatch, tmp_path)
    return FakeWalkthrough


def quit_from_widget(monkeypatch, ms=100):
    seen = capture_widget(monkeypatch)
    seen["hook"] = lambda widget: QTimer.singleShot(ms, widget.close)
    return seen


def test_a_fresh_dir_shows_the_walkthrough_before_anything_starts(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    from tokitty import app_core
    from tokitty.qt.widget import TokittyQtWidget

    built = []
    state_at_show = {}
    real_build = app_core.build_units
    monkeypatch.setattr("tokitty.qt.app.build_units", lambda **kw: built.append(1) or real_build(**kw))
    real_init = TokittyQtWidget.__init__
    monkeypatch.setattr(TokittyQtWidget, "__init__",
                        lambda self, *a, **k: (built.append("widget"), real_init(self, *a, **k))[1])

    def when_shown(walk):
        # Nothing but the walkthrough exists yet: no units, widget, poller,
        # watcher or discovery, and the marker says pending.
        state_at_show["log"] = list(fake_workers)
        state_at_show["built"] = list(built)
        state_at_show["first_run"] = load_settings(state_dir).first_run
        state_at_show["quit_policy"] = qapp.quitOnLastWindowClosed()

    fake_walkthrough.when_shown = when_shown
    seen = quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()

    assert state_at_show == {"log": [], "built": [], "first_run": "pending", "quit_policy": False}
    assert len(fake_walkthrough.instances) == 1 and fake_walkthrough.instances[0].shown
    # The widget was built after it closed, from what it saved.
    assert built == [1, "widget"]
    assert len(seen["widgets"][0].panes) == 2
    assert [a.name for a in load_accounts(state_dir)] == ["one", "two"]
    assert load_settings(state_dir).first_run == "done"
    assert ("start", "Poller") in fake_workers and ("start", "Discovery") in fake_workers


def test_the_walkthrough_gets_the_state_dir_cache_and_distro_probe(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    walk = fake_walkthrough.instances[0]
    assert walk.state_dir == state_dir
    assert walk.credentials_cache._done is False  # enabled; no native credentials: swept as usual
    assert walk.running_distros.__self__.__class__.__name__ == "RunningDistroProbe"


def test_native_credentials_reset_the_cache_after_the_walkthrough(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    from tokitty.app_core import Discovery
    from tokitty.wsl_probe import WslCredentialsCache

    claude = fake_walkthrough.home / ".claude"
    claude.mkdir()
    (claude / ".credentials.json").write_text("{}", encoding="utf-8")
    built = []
    real_init = WslCredentialsCache.__init__
    monkeypatch.setattr(WslCredentialsCache, "__init__",
                        lambda self, enabled=True, **kw: (built.append(enabled), real_init(self, enabled=enabled, **kw))[1])
    caches = []
    real_discovery_init = Discovery.__init__
    monkeypatch.setattr(Discovery, "__init__",
                        lambda self, *a, **kw: (caches.append(kw["wsl_credentials"]), real_discovery_init(self, *a, **kw))[1])
    quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()

    # Enabled for the walkthrough, which lists every install, then replaced by
    # a disabled one for the rest of the launch.
    assert built == [True, False]
    assert fake_walkthrough.instances[0].credentials_cache._done is False  # enabled
    assert caches and caches[0]._done is True  # disabled


def test_closing_a_real_walkthrough_does_not_end_the_run(
        qapp, state_dir, monkeypatch, fake_workers, tmp_path):
    from tokitty.qt import walkthrough as qt_walkthrough

    fake_home(monkeypatch, tmp_path)
    real_show = qt_walkthrough.Walkthrough.show
    monkeypatch.setattr(qt_walkthrough.Walkthrough, "show",
                        lambda self: (real_show(self), QTimer.singleShot(30, self.skip))[0])
    seen = quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    # Skip was the last window closing; the widget was still built and shown.
    assert len(seen["widgets"]) == 1
    assert load_settings(state_dir).first_run == "done"
    assert not (state_dir / "accounts.json").exists()


def test_an_existing_dir_never_shows_the_walkthrough(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    # A launch before this one left its files behind; first_run is unset.
    (state_dir / "customization.json").write_text("{}", encoding="utf-8")
    quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert fake_walkthrough.instances == []
    assert load_settings(state_dir).first_run == ""


def test_a_finished_walkthrough_is_not_shown_again(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert fake_walkthrough.instances == []


def test_a_killed_walkthrough_shows_again(qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="pending"))
    quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert len(fake_walkthrough.instances) == 1


def test_the_force_variable_shows_it_on_an_existing_dir(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    monkeypatch.setenv("TOKITTY_FIRST_RUN", "1")
    quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert len(fake_walkthrough.instances) == 1


@pytest.mark.parametrize("name, value", [("TOKITTY_DEBUG_STATE", "content"), ("TOKITTY_DEBUG_ACCOUNTS", "2")])
def test_debug_launches_skip_it_even_when_forced(
        qapp, state_dir, monkeypatch, fake_walkthrough, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("TOKITTY_FIRST_RUN", "1")

    assert run_for(qapp, 100) == 0

    assert fake_walkthrough.instances == []
    assert [p.name for p in state_dir.iterdir()] == ["tokitty.lock"]


# -- the Stream Dock in run_qt ------------------------------------------------


class FakeDeckRuntime:
    connected = True

    def __init__(self, log, fail=False):
        self.log = log
        self.fail = fail

    def tick(self, sessions, usage):
        self.log.append(("deck.tick",))
        if self.fail:
            raise RuntimeError("tick boom")


@pytest.fixture
def fake_deck(monkeypatch, fake_workers):
    """Stand-ins for DeckStarter and InWindowViews that log into the same list
    as the workers, so the order of the finally shows."""
    from types import SimpleNamespace

    from tokitty.streamdock import wiring, window

    log = fake_workers
    seen = SimpleNamespace(log=log, decks=[], views=[], runtime=None, close_all_error=None,
                           stop_error=None)

    class FakeDeck:
        state = "off"
        port = 0
        reason = ""

        def __init__(self, settings, units, probe, **kw):
            self.kw = kw
            self.presets = []
            seen.decks.append(self)

        @property
        def runtime(self):
            return seen.runtime

        def begin(self):
            log.append(("deck.begin",))

        def set_presets(self, presets):
            self.presets.append(presets)

        def set_hidden_accounts(self, names):
            pass

        def stop(self):
            log.append(("deck.stop",))
            if seen.stop_error:
                raise seen.stop_error

        def restart(self, settings=None):
            log.append(("deck.restart",))

    class FakeViews:
        def __init__(self, runtime_fn, name_fn, anchor_fn, make_view):
            self.args = (runtime_fn, name_fn, anchor_fn, make_view)
            seen.views.append(self)

        def open(self, request, reason=""):
            pass

        def sync(self):
            log.append(("views.sync",))

        def close_all(self):
            log.append(("views.close_all",))
            if seen.close_all_error:
                raise seen.close_all_error

    monkeypatch.setattr(wiring, "DeckStarter", FakeDeck)
    monkeypatch.setattr(window, "InWindowViews", FakeViews)
    return seen


def settled(log, *names):
    return [entry for entry in log if entry[0] in names]


def test_the_key_font_is_registered_before_the_deck_can_start(qapp, state_dir, monkeypatch, fake_deck):
    from tokitty.streamdock import render

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    monkeypatch.setattr(render, "register_font", lambda: fake_deck.log.append(("register_font",)) or "Aileron")
    assert run_for(qapp, 100) == 0
    order = [entry[0] for entry in fake_deck.log if entry[0] in ("register_font", "deck.begin")]
    assert order == ["register_font", "deck.begin"]


def test_a_failing_font_registration_is_one_stderr_line_and_the_app_runs(
        qapp, state_dir, monkeypatch, fake_deck, capsys):
    from tokitty.streamdock import render

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))

    def boom():
        raise RuntimeError("could not load the Stream Dock key font")

    monkeypatch.setattr(render, "register_font", boom)
    assert run_for(qapp, 100) == 0
    err = [line for line in capsys.readouterr().err.splitlines() if "key font" in line]
    assert err == ["tokitty: streamdock: key font: could not load the Stream Dock key font"]
    assert ("deck.begin",) in fake_deck.log


def test_the_deck_is_built_with_qt_timers_and_the_widgets_anchor(
        qapp, state_dir, monkeypatch, fake_deck):
    from tokitty.streamdock.wiring import DEFAULT_NAME

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    seen = capture_widget(monkeypatch)
    found = {}

    def hook(widget):
        deck = fake_deck.decks[0]
        after, cancel = deck.kw["after"], deck.kw["cancel"]
        fired = []
        timer = after(5, lambda: fired.append(1))
        found["timer"] = isinstance(timer, QTimer) and timer.isSingleShot()
        cancel(timer)
        found["active"] = timer.isActive()
        found["anchor"] = fake_deck.views[0].args[2] == widget.pane_anchor
        found["open"] = deck.kw["open_in_window"] == fake_deck.views[0].open
        found["name"] = fake_deck.views[0].args[1](0)
        found["palette"] = deck.kw["palette_fn"](0) is widget.panes[0].palette

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found == {"timer": True, "active": False, "anchor": True, "open": True,
                     "name": DEFAULT_NAME, "palette": True}
    assert settled(fake_deck.log, "deck.begin")


def test_the_view_factory_is_the_qt_one(qapp, state_dir, monkeypatch, fake_deck):
    from tokitty.qt.deck_views import QtView

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    assert run_for(qapp, 200) == 0
    make_view = fake_deck.views[0].args[3]
    request = PendingRequest(nonce="n", session_id="s", tool_use_id="t", tool_name="Bash",
                             tool_input={}, digest="d", preview="p", started=1.0)
    view = make_view(request, "t", "e", None, lambda b: None, lambda: None)
    assert isinstance(view, QtView)
    view.close()


def test_the_tick_drives_the_deck_and_syncs_the_views(qapp, state_dir, monkeypatch, fake_deck):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    fake_deck.runtime = FakeDeckRuntime(fake_deck.log)
    def both_seen():
        return {e[0] for e in settled(fake_deck.log, "deck.tick", "views.sync")} == {"deck.tick", "views.sync"}

    assert run_until(qapp, both_seen) == 0
    ticks = settled(fake_deck.log, "deck.tick", "views.sync")
    assert ("deck.tick",) in ticks and ("views.sync",) in ticks


def test_a_failing_deck_tick_is_reported_and_the_loop_goes_on(
        qapp, state_dir, monkeypatch, fake_deck, capsys):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    fake_deck.runtime = FakeDeckRuntime(fake_deck.log, fail=True)
    assert run_until(qapp, lambda: len(settled(fake_deck.log, "deck.tick")) >= 2) == 0
    assert "streamdock: tick: tick boom" in capsys.readouterr().err
    assert len(settled(fake_deck.log, "deck.tick")) >= 2


def test_the_presets_opener_is_wired_to_the_deck(qapp, state_dir, monkeypatch, fake_deck):
    from tokitty.qt import presets as qt_presets
    from tokitty.qt import settings as qt_settings

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    seen = capture_widget(monkeypatch)
    found = {}

    def hook(widget):
        settings_action(widget)()
        host = qt_settings._instance.host
        found["state"] = host.streamdock_state()
        found["supported"] = host.streamdock_install_supported is not None
        host.on_edit_streamdock_presets()
        dialog = qt_presets._instance
        found["host"] = dialog.host is host
        dialog._on_saved(["saved"])
        dialog.close()
        qt_settings._instance.close()

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["state"] == "not_installed" and found["supported"] and found["host"]
    assert fake_deck.decks[0].presets == [["saved"]]


def test_the_finally_closes_the_views_then_stops_the_deck_then_the_workers(
        qapp, state_dir, monkeypatch, fake_deck):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    assert run_for(qapp, 200) == 0
    order = [e for e in fake_deck.log if e[0] in ("views.close_all", "deck.stop") or e[0] == "stop"]
    assert order[:2] == [("views.close_all",), ("deck.stop",)]
    assert ("stop", "Poller") in order[2:]


def test_the_finally_runs_when_setup_raises_after_the_deck_exists(
        qapp, state_dir, monkeypatch, fake_deck):
    from tokitty.app_core import Discovery

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))

    def boom(self, *a, **kw):
        raise RuntimeError("setup boom")

    monkeypatch.setattr(Discovery, "__init__", boom)
    from tokitty.qt.app import run_qt

    with pytest.raises(RuntimeError, match="setup boom"):
        run_qt()
    order = [e for e in fake_deck.log if e[0] in ("views.close_all", "deck.stop")]
    assert order == [("views.close_all",), ("deck.stop",)]


def test_the_finally_skips_the_deck_when_build_units_raises(
        qapp, state_dir, monkeypatch, fake_deck):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))

    def boom(**kw):
        raise RuntimeError("build boom")

    monkeypatch.setattr("tokitty.qt.app.build_units", boom)
    from tokitty.qt.app import run_qt

    with pytest.raises(RuntimeError, match="build boom"):
        run_qt()
    assert fake_deck.decks == [] and fake_deck.views == []
    assert not settled(fake_deck.log, "views.close_all", "deck.stop")


def test_a_failing_close_all_does_not_skip_the_deck_or_the_workers(
        qapp, state_dir, monkeypatch, fake_deck, capsys):
    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    fake_deck.close_all_error = RuntimeError("views boom")
    assert run_for(qapp, 200) == 0
    assert ("deck.stop",) in fake_deck.log and ("stop", "Poller") in fake_deck.log
    assert "close deck views: views boom" in capsys.readouterr().err


def test_a_failing_deck_stop_does_not_skip_the_workers_or_hide_the_error(
        qapp, state_dir, monkeypatch, fake_deck, capsys):
    from tokitty.app_core import Discovery

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    fake_deck.stop_error = RuntimeError("stop boom")

    def boom(self, *a, **kw):
        raise RuntimeError("setup boom")

    monkeypatch.setattr(Discovery, "__init__", boom)
    from tokitty.qt.app import run_qt

    with pytest.raises(RuntimeError, match="setup boom"):
        run_qt()
    assert ("stop", "Poller") in fake_deck.log
    assert "stop deck: stop boom" in capsys.readouterr().err


def send_quit(qapp, ms=50):
    """Command-Q on macOS reaches Qt as a Quit event sent to the application,
    which closes every window. QApplication.quit() is a no-op before exec()."""
    from PySide6.QtCore import QCoreApplication, QEvent

    QTimer.singleShot(ms, lambda: QCoreApplication.sendEvent(qapp, QEvent(QEvent.Type.Quit)))


def test_quit_while_the_walkthrough_is_open_ends_the_launch(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    built = []
    monkeypatch.setattr("tokitty.qt.app.build_units", lambda **kw: built.append(1) or [])

    def when_shown(walk):
        walk.finish = lambda: None
        send_quit(qapp)

    fake_walkthrough.when_shown = when_shown
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert built == [] and fake_workers == []


def test_the_walkthrough_quit_shortcut_ends_the_launch_and_leaves_onboarding_pending(
        qapp, state_dir, monkeypatch, fake_workers, tmp_path):
    from tokitty.qt import walkthrough as qt_walkthrough

    fake_home(monkeypatch, tmp_path)
    # An earlier test's Quit event leaves the thread's quit flag up, which
    # only a QApplication.exec() clears; spend it here so run_walkthrough's
    # own loop really runs.
    QTimer.singleShot(0, qapp.quit)
    qapp.exec()
    real_show = qt_walkthrough.Walkthrough.show
    monkeypatch.setattr(qt_walkthrough.Walkthrough, "show",
                        lambda self: (real_show(self), QTimer.singleShot(30, self.quit_shortcut.activated.emit))[0])
    built = []
    monkeypatch.setattr("tokitty.qt.app.build_units", lambda **kw: built.append(1) or [])
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert built == [] and fake_workers == []
    assert load_settings(state_dir).first_run == "pending"


def test_a_quit_event_during_the_real_walkthrough_leaves_onboarding_pending(
        qapp, state_dir, monkeypatch, fake_workers, tmp_path):
    from tokitty.qt import walkthrough as qt_walkthrough

    fake_home(monkeypatch, tmp_path)
    QTimer.singleShot(0, qapp.quit)
    qapp.exec()
    real_show = qt_walkthrough.Walkthrough.show
    monkeypatch.setattr(qt_walkthrough.Walkthrough, "show",
                        lambda self: (real_show(self), send_quit(qapp, 30))[0])
    built = []
    monkeypatch.setattr("tokitty.qt.app.build_units", lambda **kw: built.append(1) or [])
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert built == [] and fake_workers == []
    assert load_settings(state_dir).first_run == "pending"


def test_a_quit_the_walkthrough_refuses_does_not_end_the_launch(
        qapp, state_dir, monkeypatch, fake_workers, fake_walkthrough):
    # An install is running, so the walkthrough stays open; finishing it
    # afterwards launches as usual.
    def when_shown(walk):
        fake_walkthrough.refuse_close = True
        real_finish = walk.finish
        walk.finish = lambda: None
        send_quit(qapp)
        # The install ends, so it stops refusing, and the user finishes.
        QTimer.singleShot(150, lambda: (setattr(fake_walkthrough, "refuse_close", False), real_finish()))

    fake_walkthrough.when_shown = when_shown
    seen = quit_from_widget(monkeypatch)
    net = safety_net(qapp)
    from tokitty.qt.app import run_qt

    assert run_qt() == 0
    net.stop()
    assert len(seen["widgets"]) == 1


def test_a_failing_unit_stop_does_not_skip_the_others(
        qapp, state_dir, monkeypatch, fake_deck, capsys):
    from tokitty.poller import Poller

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))

    def boom(self):
        raise RuntimeError("join boom")

    monkeypatch.setattr(Poller, "stop", boom)
    assert run_for(qapp, 200) == 0
    stops = settled(fake_deck.log, "stop")
    assert ("stop", "ActivityWatcher") in stops and ("stop", "UsageWatcher") in stops
    assert "stop poller: join boom" in capsys.readouterr().err


# -- TOKITTY_DEBUG_NOTIFY and the platform mapping (#115) ----------------------

def test_the_debug_notify_is_scheduled_only_with_the_variable(qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.qt import app
    from tokitty.qt.tray import QtTray

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False))
    sent = []
    monkeypatch.setattr(QtTray, "debug_notify", lambda self, title, message: sent.append((title, message)))
    monkeypatch.setattr(app, "DEBUG_NOTIFY_DELAY_MS", 50)

    monkeypatch.delenv("TOKITTY_DEBUG_NOTIFY", raising=False)
    assert run_for(qapp, 500) == 0
    assert sent == []

    monkeypatch.setenv("TOKITTY_DEBUG_NOTIFY", "1")
    assert run_for(qapp, 500) == 0
    assert sent == [("Tokitty", "Test notification (TOKITTY_DEBUG_NOTIFY)")]


def test_the_debug_notify_is_not_scheduled_in_a_debug_state_launch(qapp, state_dir, monkeypatch):
    from tokitty.qt import app
    from tokitty.qt.tray import QtTray

    sent = []
    monkeypatch.setattr(QtTray, "debug_notify", lambda self, title, message: sent.append(1))
    monkeypatch.setattr(app, "DEBUG_NOTIFY_DELAY_MS", 10)
    monkeypatch.setenv("TOKITTY_DEBUG_STATE", "content")
    monkeypatch.setenv("TOKITTY_DEBUG_NOTIFY", "1")
    assert run_for(qapp, 300) == 0
    assert sent == []


def test_run_qt_maps_the_platform_before_it_creates_the_application(qapp, state_dir, monkeypatch):
    from tokitty import frozen

    calls = []
    monkeypatch.setattr(frozen, "qt_platform_env", lambda *a, **k: calls.append(1))
    monkeypatch.setenv("TOKITTY_DEBUG_STATE", "content")
    assert run_for(qapp, 100) == 0
    assert calls == [1]


# -- Hide panes (#230) ---------------------------------------------------------

def test_a_saved_hidden_is_ignored_without_a_tray_and_the_menu_offers_no_hide(
    qapp, state_dir, monkeypatch, fake_workers
):
    save_settings(state_dir, Settings(tray_enabled=False, hidden_panes=True, first_run="done"))
    seen = capture_widget(monkeypatch)
    assert run_for(qapp, 100) == 0
    widget = seen["widgets"][0]
    assert "Hide panes" not in [i.label for i in widget.build_menu_model(0)]


def test_a_saved_hidden_keeps_the_panes_hidden_when_a_tray_is_up(qapp, state_dir, monkeypatch, fake_workers):
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    save_settings(state_dir, Settings(tray_enabled=True, hidden_panes=True, first_run="done"))
    from tokitty.qt.widget import TokittyQtWidget

    built = []
    real_init = TokittyQtWidget.__init__
    monkeypatch.setattr(TokittyQtWidget, "__init__",
                        lambda self, *a, **k: (real_init(self, *a, **k), built.append(self))[0])
    seen = {}

    def probe() -> bool:
        # Read while the loop runs: the tray stops with it.
        if built:
            seen["visible"] = built[0].isVisible()
            seen["item"] = next((i for i in built[0].build_menu_model(0) if i.label == "Hide panes"), None)
        return bool(built)

    assert run_until(qapp, probe) == 0
    assert seen["visible"] is False
    assert seen["item"].checkbox() is True


def test_hide_panes_from_the_menu_is_saved(qapp, state_dir, monkeypatch, fake_workers):
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    save_settings(state_dir, Settings(tray_enabled=True, first_run="done"))
    seen = capture_widget(monkeypatch)
    seen["hook"] = lambda w: next(i for i in w.build_menu_model(0) if i.label == "Hide panes").action()
    assert run_for(qapp, 150) == 0
    assert load_settings(state_dir).hidden_panes is True
