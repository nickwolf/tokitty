"""run_qt end to end under the offscreen platform."""
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import Qt, QTimer

from tokitty.api import UsageSnapshot
from tokitty.customize import Customization
from tokitty.poller import PollResult
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

    assert run_for(qapp, 1500) == 0

    assert renders, "the tick never rendered"
    assert renders[0]["session_pct"] == 40.0
    assert renders[0]["weekly_pct"] == 20.0
    assert ("start", "Poller") in fake_workers and ("start", "Discovery") in fake_workers
    assert ("stop", "Poller") in fake_workers
    assert ("stop", "ActivityWatcher") in fake_workers
    assert ("stop", "UsageWatcher") in fake_workers
    assert (state_dir / "customization.json").exists()


def test_normal_launch_on_a_fresh_dir_marks_the_walkthrough_pending(qapp, state_dir, fake_workers):
    assert run_for(qapp, 100) == 0

    assert load_settings(state_dir).first_run == "pending"


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
        seen["labels"] = [i.label for i in self.build_menu_model(0)]
        view = next(i for i in self.build_menu_model(0) if i.label == "View")
        next(i for i in view.submenu if i.label == "Per-model").action()
        real_show(self)

    monkeypatch.setattr(TokittyQtWidget, "show", show)

    assert run_for(qapp, 100) == 0

    assert seen["labels"] == ["Refresh now", "View", "Always in front", "", "Settings…", "", "Exit"]
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


def test_the_preferences_action_has_the_preferences_role(qapp):
    from PySide6.QtGui import QAction

    from tokitty.qt.app import make_preferences_menu

    opened = []
    menubar, menu, action = make_preferences_menu(lambda: opened.append(1))
    assert action.menuRole() == QAction.MenuRole.PreferencesRole
    assert action in menu.actions() and menu.menuAction() in menubar.actions()
    assert menubar.parent() is None
    action.trigger()
    assert opened == [1]
