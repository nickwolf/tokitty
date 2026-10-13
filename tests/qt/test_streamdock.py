"""The Qt Stream Dock pieces: the DeckStarter timers, the in-window permission
view, the presets window and the Settings tab."""
import errno
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton, QWidget

from tokitty.accounts import Account
from tokitty.qt import presets as qt_presets
from tokitty.qt import settings as qt_settings
from tokitty.streamdock import wiring
from tokitty.streamdock.pending import PendingRequest
from tokitty.streamdock.window import InWindowViews

pytestmark = pytest.mark.qt

NATIVE = Account("native-1", "C:\\Users\\u\\.claude")
WSL = Account("wsl-1", "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude")
CODEX = Account("cx", "C:\\Users\\u\\.codex", provider="codex")


def spin(ms):
    """Run the event loop for `ms`, so single-shot timers fire."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def req(**kw):
    base = dict(nonce="n1", session_id="s", tool_use_id="t", tool_name="Bash",
                tool_input={"command": "ls -la"}, digest="d", preview="ls", started=1.0)
    base.update(kw)
    return PendingRequest(**base)


# -- the DeckStarter timers -----------------------------------------------------


class FakeDeckRuntime:
    """Fails to bind `failures` times, then starts."""

    failures = 0
    starts = 0
    configured = staticmethod(lambda settings: True)

    def __init__(self, *args, **kw):
        pass

    def start(self):
        FakeDeckRuntime.starts += 1
        if FakeDeckRuntime.starts <= FakeDeckRuntime.failures:
            raise OSError(errno.EADDRINUSE, "address in use")

    def stop(self):
        pass


@pytest.fixture
def deck_world(qapp, monkeypatch):
    from tokitty.qt.app import qt_timers

    FakeDeckRuntime.failures = 0
    FakeDeckRuntime.starts = 0
    monkeypatch.setattr(wiring, "make_watcher_factory", lambda units, probe: None)
    parent = QApplication.instance()
    after, cancel = qt_timers(parent)
    settings = SimpleNamespace(streamdock_port=4000, streamdock_token="t",
                               streamdock_presets=[], streamdock_hidden_accounts=[])

    def starter(**kw):
        return wiring.DeckStarter(
            settings, [], list, palette_fn=dict, after=after, cancel=cancel,
            runtime_cls=FakeDeckRuntime, deadline_s=30.0, retry_s=0.02, **kw)

    return SimpleNamespace(after=after, cancel=cancel, starter=starter)


def test_after_returns_a_single_shot_timer_that_fires_once(deck_world):
    calls = []
    timer = deck_world.after(10, lambda: calls.append(1))
    assert timer.isSingleShot() and timer.isActive()
    spin(120)
    assert calls == [1]


def test_cancel_stops_the_timer_before_it_fires(deck_world):
    calls = []
    timer = deck_world.after(30, lambda: calls.append(1))
    deck_world.cancel(timer)
    assert not timer.isActive()
    spin(120)
    assert calls == []


def test_cancel_tolerates_a_timer_that_already_fired(deck_world):
    timer = deck_world.after(5, lambda: None)
    spin(100)
    deck_world.cancel(timer)  # deleted after firing: must not raise


def test_the_starter_retries_a_busy_port_on_the_timer_until_it_binds(deck_world):
    FakeDeckRuntime.failures = 2
    deck = deck_world.starter()
    deck.begin()
    assert deck.state == "starting" and deck.runtime is None
    spin(300)
    assert deck.state == "running" and isinstance(deck.runtime, FakeDeckRuntime)
    assert FakeDeckRuntime.starts == 3


def test_stop_cancels_the_pending_retry(deck_world):
    FakeDeckRuntime.failures = 100
    deck = deck_world.starter()
    deck.begin()
    assert FakeDeckRuntime.starts == 1
    deck.stop()
    spin(150)
    assert FakeDeckRuntime.starts == 1 and deck.runtime is None


# -- the in-window view ---------------------------------------------------------


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.pending = set()

    def session_title(self, request):
        return "my session"

    def pending_nonces(self):
        return set(self.pending)

    def in_window_opened(self, nonce):
        self.calls.append(("opened", nonce))

    def in_window_closed(self, nonce):
        self.calls.append(("closed", nonce))

    def decide_from_window(self, nonce, behavior):
        self.calls.append(("decide", nonce, behavior))


@pytest.fixture
def views(make_world):
    from tokitty.qt.deck_views import qt_view_factory

    world = make_world(2)
    runtime = FakeRuntime()
    bookkeeping = InWindowViews(lambda: runtime, lambda i: f"acct{i}", world.widget.pane_anchor,
                                qt_view_factory())
    yield SimpleNamespace(world=world, runtime=runtime, views=bookkeeping)
    bookkeeping.close_all()


def only_view(views):
    (view,) = views.views._open.values()
    return view


def button(view, title):
    return next(b for b in view.findChildren(QPushButton) if b.text() == title)


def test_a_pending_request_opens_one_view_at_the_panes_anchor(views):
    runtime = views.runtime
    runtime.pending = {"n1"}
    views.views.open(req(account_index=1), "not_front")
    views.views.open(req(account_index=1), "not_front")  # the same nonce again
    view = only_view(views)
    assert view.isVisible()
    assert view.windowTitle() == "Tokitty: acct1 permission request"
    assert view.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert view.parent() is None
    assert (view.x(), view.y()) == views.world.widget.pane_anchor(1)
    assert "my session" in view.explanation.text() and "couldn't bring it" in view.explanation.text()
    assert view.tool_input.isReadOnly() and '"command": "ls -la"' in view.tool_input.toPlainText()
    assert runtime.calls == [("opened", "n1")]


def test_the_tool_input_is_shown_whole(views):
    long = "x" * 5000
    views.views.open(req(tool_input={"command": long}))
    assert long in only_view(views).tool_input.toPlainText()


def test_the_always_button_appears_only_with_a_narrow_rule(views):
    views.views.open(req())
    assert only_view(views).always_button is None
    views.views.open(req(nonce="n2", always_rule="Bash(ls:*)"))
    second = views.views._open["n2"]
    assert second.always_button is not None and second.always_button.text() == "Always: Bash(ls:*)"


@pytest.mark.parametrize("title,behavior", [("Allow", "allow"), ("Deny", "deny"),
                                            ("Always: Bash(ls:*)", "always")])
def test_an_answer_reaches_the_runtime_and_closes_the_view(views, title, behavior):
    views.views.open(req(always_rule="Bash(ls:*)"))
    view = only_view(views)
    button(view, title).click()
    assert views.runtime.calls == [("opened", "n1"), ("decide", "n1", behavior), ("closed", "n1")]
    assert views.views._open == {}
    assert not view.isVisible()


def test_sync_closes_a_view_whose_request_left_pending(views):
    views.runtime.pending = {"n1"}
    views.views.open(req())
    view = only_view(views)
    views.views.sync()
    assert view.isVisible()
    views.runtime.pending = set()
    views.views.sync()
    assert not view.isVisible() and views.views._open == {}
    assert views.runtime.calls == [("opened", "n1"), ("closed", "n1")]


def test_close_all_closes_every_view(views):
    views.views.open(req())
    views.views.open(req(nonce="n2"))
    shown = list(views.views._open.values())
    views.views.close_all()
    assert not any(v.isVisible() for v in shown) and views.views._open == {}
    assert views.runtime.calls.count(("closed", "n1")) == 1


def test_the_title_bar_close_reaches_the_bookkeeping_once(views):
    """The window's own close (title-bar X) goes through closeEvent."""
    views.views.open(req())
    view = only_view(views)
    event = QCloseEvent()
    view.closeEvent(event)
    assert event.isAccepted()
    assert views.views._open == {}
    assert views.runtime.calls == [("opened", "n1"), ("closed", "n1")]
    assert not view.isVisible()


def test_a_real_window_close_takes_the_title_bar_path(views):
    """QWidget.close() with the flag unset is what the window system's X does."""
    views.views.open(req())
    view = only_view(views)
    QWidget.close(view)  # not the view's own close(), which sets the flag
    assert views.views._open == {}
    assert views.runtime.calls == [("opened", "n1"), ("closed", "n1")]


def test_closing_through_the_bookkeeping_does_not_call_on_close_again(qapp):
    from tokitty.qt.deck_views import qt_view_factory

    closes = []
    view = qt_view_factory()(req(), "t", "why", None, lambda b: None, lambda: closes.append(1))
    assert view.isVisible()
    view.close()
    assert closes == [] and not view.isVisible()
    view.close()  # closing twice stays quiet
    assert closes == []


def test_a_title_bar_close_calls_on_close_once_even_if_it_closes_the_view(qapp):
    from tokitty.qt.deck_views import qt_view_factory

    holder = []
    closes = []

    def on_close():
        closes.append(1)
        holder[0].close()  # what InWindowViews.close does

    view = qt_view_factory()(req(), "t", "why", None, lambda b: None, on_close)
    holder.append(view)
    view.closeEvent(QCloseEvent())
    assert closes == [1] and not view.isVisible()
    spin(30)  # the deferred delete runs without trouble


# -- the presets window ---------------------------------------------------------


@pytest.fixture
def presets_world(qapp, tmp_path):
    saved = []
    accounts = [NATIVE, WSL, CODEX]
    labels = {0: "Home", 1: "Work"}

    def open_dialog(host=None):
        return qt_presets.PresetsDialog.open(
            tmp_path, lambda: accounts, labels.get, lambda presets: saved.append(presets), host=host)

    yield SimpleNamespace(open=open_dialog, saved=saved, state_dir=tmp_path, accounts=accounts)
    if qt_presets._instance is not None:
        qt_presets._instance.close()


def rows(dialog):
    return [[item.text(c) for c in range(3)]
            for item in (dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount()))]


def fill_and_save(dialog, name, account_label_prefix, cwd):
    form = dialog._form
    form.name_entry.setText(name)
    if account_label_prefix is not None:
        label = next(c for c in form.combo.choices() if c.startswith(account_label_prefix))
        form.combo.set(label)
        form.combo._on_change(label)
    form.cwd_entry.setText(cwd)
    form.save()


def test_the_dialog_is_one_window_and_reopening_raises_it(presets_world):
    first = presets_world.open()
    assert first.isVisible() and first.windowTitle() == "New-session presets"
    assert presets_world.open() is first


def test_closing_lets_a_new_dialog_open(presets_world):
    first = presets_world.open()
    first.close()
    assert presets_world.open() is not first


def test_add_saves_a_native_preset_and_lists_it(presets_world):
    dialog = presets_world.open()
    dialog._add()
    fill_and_save(dialog, "  build  ", "Home", " C:\\work ")
    assert rows(dialog) == [["build", "Home (C:\\Users\\u\\.claude)", "C:\\work"]]
    saved = presets_world.saved[-1]
    assert saved[0]["name"] == "build" and saved[0]["env"] == "native" and saved[0]["cwd"] == "C:\\work"
    assert not dialog._form.isVisible()
    from tokitty.settings import load_settings
    assert load_settings(presets_world.state_dir).streamdock_presets == saved


def test_only_claude_accounts_are_offered(presets_world):
    dialog = presets_world.open()
    dialog._add()
    choices = dialog._form.combo.choices()
    assert len(choices) == 2 and not any("codex" in c for c in choices)


def test_a_wsl_account_disables_browse_and_shows_the_hint(presets_world):
    dialog = presets_world.open()
    dialog._add()
    form = dialog._form
    assert form.browse.isEnabled() and form.hint.isHidden()
    label = next(c for c in form.combo.choices() if c.startswith("Work"))
    form.combo.set(label)
    form.combo._on_change(label)
    assert not form.browse.isEnabled() and not form.hint.isHidden()
    fill_and_save(dialog, "w", "Work", "~/repo")
    assert presets_world.saved[-1][0]["env"] == "wsl" and presets_world.saved[-1][0]["distro"] == "Ubuntu"


def test_validation_errors_show_and_nothing_is_saved(presets_world):
    dialog = presets_world.open()
    dialog._add()
    fill_and_save(dialog, "", None, "C:\\work")
    assert dialog._form.error.text() == "Name is required"
    fill_and_save(dialog, "x", None, "  ")
    assert dialog._form.error.text() == "Folder is required"
    assert presets_world.saved == []


def test_a_repeated_name_is_refused_but_editing_in_place_is_not(presets_world):
    dialog = presets_world.open()
    dialog._add()
    fill_and_save(dialog, "a", None, "C:\\a")
    dialog._add()
    fill_and_save(dialog, "a", None, "C:\\b")
    assert dialog._form.error.text() == "Another preset already has that name"
    dialog._form.close()
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    dialog._edit()
    fill_and_save(dialog, "a", None, "C:\\c")
    assert rows(dialog)[0][2] == "C:\\c" and len(rows(dialog)) == 1


def test_edit_prefills_the_form_and_saves_over_the_row(presets_world):
    dialog = presets_world.open()
    dialog._add()
    fill_and_save(dialog, "a", "Work", "~/repo")
    dialog._add()
    fill_and_save(dialog, "b", "Home", "C:\\b")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    dialog._edit()
    form = dialog._form
    assert form.windowTitle() == "Edit preset"
    assert form.name_entry.text() == "a" and form.cwd_entry.text() == "~/repo"
    assert form.combo.get().startswith("Work")
    fill_and_save(dialog, "a2", None, "~/other")
    assert [r[0] for r in rows(dialog)] == ["a2", "b"]
    assert presets_world.saved[-1][0]["cwd"] == "~/other"


def test_edit_and_remove_do_nothing_without_a_selection(presets_world):
    dialog = presets_world.open()
    dialog._edit()
    dialog._remove()
    assert dialog._form is None and presets_world.saved == []


def test_remove_drops_the_selected_row(presets_world):
    dialog = presets_world.open()
    for name in ("a", "b"):
        dialog._add()
        fill_and_save(dialog, name, None, "C:\\x")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    dialog._remove()
    assert [r[0] for r in rows(dialog)] == ["b"]
    assert [p["name"] for p in presets_world.saved[-1]] == ["b"]


def test_a_preset_whose_account_is_gone_reads_account_missing(presets_world):
    from tokitty.settings import load_settings
    from tokitty.streamdock.presets import save_presets

    save_presets(presets_world.state_dir, [
        {"name": "old", "account": "gone", "account_index": 5, "env": "native", "cwd": "C:\\x"}])
    assert load_settings(presets_world.state_dir).streamdock_presets
    assert rows(presets_world.open())[0][1] == "(account missing)"


def test_browse_fills_the_folder_with_backslashes(presets_world, monkeypatch):
    dialog = presets_world.open()
    dialog._add()
    asked = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda parent, caption, start: asked.append((parent, start)) or "C:/Users/u/repo")
    dialog._form.cwd_entry.setText("C:\\start")
    button(dialog._form, "Browse…").click()
    assert dialog._form.cwd_entry.text() == "C:\\Users\\u\\repo"
    assert asked == [(dialog._form, "C:\\start")]


def test_a_cancelled_browse_leaves_the_folder_alone(presets_world, monkeypatch):
    dialog = presets_world.open()
    dialog._add()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: "")
    dialog._form.cwd_entry.setText("keep")
    button(dialog._form, "Browse…").click()
    assert dialog._form.cwd_entry.text() == "keep"


def test_the_dialog_follows_the_hosts_always_in_front(presets_world, make_world):
    world = make_world(1)
    dialog = presets_world.open(host=world.host)
    on_top = Qt.WindowType.WindowStaysOnTopHint
    assert bool(dialog.windowFlags() & on_top) == world.widget.always_on_top
    world.host.toggle_always_on_top()
    assert bool(dialog.windowFlags() & on_top) == world.widget.always_on_top
    dialog.close()
    assert dialog not in world.host._windows


def test_closing_the_dialog_closes_its_form(presets_world):
    dialog = presets_world.open()
    dialog._add()
    form = dialog._form
    dialog.close()
    assert not form.isVisible()


# -- the Settings tab -----------------------------------------------------------


@pytest.fixture
def tab_world(make_world):
    world = make_world(2)
    host = world.host
    state = {"value": "not_installed", "supported": True}
    calls = []
    host.streamdock_state = lambda: state["value"]
    host.streamdock_info = lambda: {"port": 4000, "reason": "in use"}
    host.streamdock_install = lambda: calls.append("install") or (True, ["copied"])
    host.streamdock_uninstall = lambda: calls.append("uninstall") or (True, ["removed"])
    host.streamdock_install_supported = lambda: state["supported"]
    host.on_edit_streamdock_presets = lambda: calls.append("presets")
    shown = {"a": True, "b": False}
    host.streamdock_account_toggles = lambda: [
        (name, (lambda n=name: shown[n]), (lambda n=name: shown.__setitem__(n, not shown[n])))
        for name in ("a", "b")]
    window = qt_settings.SettingsWindow.open(host, 0)
    window._show("streamdock")
    return SimpleNamespace(world=world, window=window, tab=window.tabs["streamdock"], state=state,
                           calls=calls, shown=shown)


def test_the_tab_follows_accounts_in_the_rail(tab_world):
    assert [key for key, _t in qt_settings.TABS][-2:] == ["accounts", "streamdock"]
    assert dict(qt_settings.TABS)["streamdock"] == "Stream Dock"


def test_not_installed_offers_install(tab_world):
    tab = tab_world.tab
    assert tab.pill.text() == "Not installed"
    assert tab.install_button is not None and tab.install_button.isEnabled()
    assert tab.uninstall_button is None
    assert tab.note.isHidden()
    assert "Install the plugin" in tab.detail.text()


@pytest.mark.parametrize("state,label,kind", [
    ("not_connected", "Installed, not connected", "warn"),
    ("connected", "Connected", "good"),
    ("restart_to_connect", "Restart tokitty to finish", "warn"),
    ("failed", "Couldn't start", "bad"),
])
def test_an_installed_state_offers_uninstall(tab_world, state, label, kind):
    tab_world.state["value"] = state
    tab_world.tab.refresh()
    tab = tab_world.tab
    assert tab.pill.text() == label and tab.pill.property("tone") == kind
    assert tab.uninstall_button is not None and tab.install_button is None


def test_starting_and_failed_details_name_the_port(tab_world):
    tab_world.state["value"] = "failed"
    tab_world.tab.refresh()
    assert "port 4000: in use" in tab_world.tab.detail.text()


def test_removal_pending_disables_install_presets_and_toggles(tab_world):
    tab_world.state["value"] = "restart_to_finish_removal"
    tab = tab_world.tab
    tab.refresh()
    assert not tab.install_button.isEnabled()
    assert not tab.presets_button.isEnabled()
    assert all(not t.enabled for t in tab.toggles)


def test_unsupported_platforms_show_the_note_and_disable_install(tab_world):
    tab_world.state["supported"] = False
    tab = tab_world.tab
    tab.refresh()
    assert not tab.note.isHidden() and "needs Windows" in tab.note.text()
    assert not tab.install_button.isEnabled()


def test_the_presets_button_calls_the_hosts_opener(tab_world):
    tab_world.tab.presets_button.click()
    assert tab_world.calls == ["presets"]


def test_no_opener_hides_the_presets_button(tab_world):
    tab_world.world.host.on_edit_streamdock_presets = None
    tab_world.tab.refresh()
    assert tab_world.tab.presets_button is None


def test_a_missing_holder_reads_as_not_installed(make_world):
    window = qt_settings.SettingsWindow.open(make_world(1).host, 0)
    window._show("streamdock")
    tab = window.tabs["streamdock"]
    assert tab.pill.text() == "Not installed"
    assert not tab.install_button.isEnabled()


def test_the_account_toggles_show_and_write(tab_world):
    tab = tab_world.tab
    assert [t.title.text() for t in tab.toggles] == ["a", "b"]
    assert [t.get() for t in tab.toggles] == [True, False]
    tab.toggles[0]._clicked()
    assert tab_world.shown["a"] is False
    assert tab.toggles[0].get() is False  # refreshed after the write


def test_no_accounts_says_so(tab_world):
    tab_world.world.host.streamdock_account_toggles = lambda: []
    tab_world.tab.refresh()
    assert tab_world.tab.toggles == []
    assert tab_world.tab.sources.findChildren(type(tab_world.tab.detail))[0].text() == "No accounts to show."


def test_install_runs_on_a_worker_and_logs_the_result(tab_world):
    import threading

    seen = []
    host = tab_world.world.host
    host.streamdock_install = lambda: seen.append(threading.current_thread()) or (True, ["copied it"])
    tab = tab_world.tab
    tab.refresh()
    tab.install_button.click()
    assert tab._busy and not tab.install_button.isEnabled() and tab.install_button.text() == "Installing…"
    deadline = 60
    while tab._busy and deadline:
        spin(50)
        deadline -= 1
    assert not tab._busy
    assert seen and seen[0] is not threading.current_thread()
    lines = [line for _clock, line, _color in tab.log]
    assert lines[0] == "Installing the Stream Dock plugin…"
    assert "copied it" in lines and lines[-1] == "Install finished."


def test_uninstall_asks_first_and_cancel_backs_out(tab_world):
    tab_world.state["value"] = "connected"
    tab = tab_world.tab
    tab.refresh()
    tab.uninstall_button.click()
    assert tab.confirm_button is not None and tab_world.calls == []
    cancel = next(b for b in tab.actions.findChildren(QPushButton) if b.text() == "Cancel")
    cancel.click()
    assert tab.confirm_button is None and tab.uninstall_button is not None
    assert tab_world.calls == []


def test_confirming_uninstall_runs_it(tab_world):
    tab_world.state["value"] = "connected"
    tab = tab_world.tab
    tab.refresh()
    tab.uninstall_button.click()
    tab.confirm_button.click()
    for _ in range(60):
        if not tab._busy:
            break
        spin(50)
    assert tab_world.calls == ["uninstall"]
    assert tab.log[-1][1] == "Uninstall finished."


def test_a_failed_install_logs_the_error(tab_world):
    def boom():
        raise RuntimeError("disk full")

    tab_world.world.host.streamdock_install = boom
    tab = tab_world.tab
    tab.refresh()
    tab.install_button.click()
    for _ in range(60):
        if not tab._busy:
            break
        spin(50)
    assert tab.log[-1][1] == "Install failed: disk full"


def test_the_log_keeps_the_last_eight_lines(tab_world):
    tab = tab_world.tab
    for n in range(12):
        tab._log(f"line {n}")
    assert [line for _c, line, _k in tab.log] == [f"line {n}" for n in range(4, 12)]


def test_the_watcher_repaints_when_the_state_changes(tab_world):
    tab = tab_world.tab
    assert tab.pill.text() == "Not installed"
    tab_world.state["value"] = "connected"
    tab._watch()
    assert tab.pill.text() == "Connected"


def test_closing_the_window_stops_the_watcher(tab_world):
    tab = tab_world.tab
    assert tab._watcher.isActive()
    tab_world.window.close()
    assert not tab._watcher.isActive()
