"""run_qt end to end under the offscreen platform."""
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

from tokitty.accounts import load_accounts
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


def test_the_accounts_opener_passes_discovery_matches_to_the_manager(
        qapp, state_dir, monkeypatch, fake_workers):
    from tokitty.app_core import Discovery
    from tokitty.qt import accounts as qt_accounts
    from tokitty.qt import settings as qt_settings

    save_settings(state_dir, Settings(tray_enabled=False, surprise_me=False, first_run="done"))
    monkeypatch.setattr(Discovery, "wsl_matches",
                        lambda self: [("Ubuntu", "/home/u/.claude/.credentials.json")])
    monkeypatch.setattr("tokitty.accounts_logic.retry_pending_hook_op", lambda state_dir: None)
    seen = capture_widget(monkeypatch)
    found = {}

    def hook(widget):
        settings_action(widget)()
        host = qt_settings._instance.host
        manager = host.on_open_accounts()
        found["manager"] = manager
        found["current"] = qt_accounts.current()
        found["paths"] = [d.distro for d in manager._discovered_paths]
        found["parent"] = manager.parent()
        manager.close()
        qt_settings._instance.close()

    seen["hook"] = hook
    assert run_for(qapp, 300) == 0
    assert found["manager"] is found["current"] and found["parent"] is None
    assert found["paths"] == ["Ubuntu"]


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


# -- the first-run walkthrough ------------------------------------------------


class FakeWalkthrough(QWidget):
    """Stands in for the real window: records what run_qt built it with and
    what was running when it showed, then finishes from a timer, as a user
    pressing Finish would after saving two accounts."""

    closed = Signal()
    instances = []
    save_accounts_on_finish = True
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
        if FakeWalkthrough.when_shown is not None:
            FakeWalkthrough.when_shown(self)
        QTimer.singleShot(30, self.finish)

    def finish(self):
        from tokitty import first_run
        from tokitty.accounts import Account, save_accounts

        if FakeWalkthrough.save_accounts_on_finish:
            save_accounts(self.state_dir, [
                Account(name="one", config_dir=str(self.state_dir / "c1"), provider="claude"),
                Account(name="two", config_dir=str(self.state_dir / "c2"), provider="claude"),
            ])
        first_run.mark_done(self.state_dir)
        self.closed.emit()


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
