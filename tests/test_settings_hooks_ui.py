"""GUI tests for the Settings Accounts and Stream Dock tabs (xvfb).

hook_status_for_dir, apply_hook_operation, retry_pending_hook_op and the
Stream Dock install seams are replaced with fakes; nothing here touches a
real config dir."""
import json
import threading
import time

import pytest

tk = pytest.importorskip("tkinter")

from test_settings_ui import Harness  # noqa: E402
from tokitty import hook_guard, settings_accounts, settings_ui  # noqa: E402
from tokitty.hooks_install import HookOperationResult, HookStatus  # noqa: E402
from tokitty.settings_ui import SettingsWindow  # noqa: E402
from tokitty.settings_widgets import _PILL_COLORS  # noqa: E402

MAIN = threading.main_thread()


@pytest.fixture
def harness(tmp_path):
    root = tk.Tk()
    root.withdraw()
    yield Harness(root, tmp_path)
    for instance in list(settings_ui._instances.values()):
        instance.close()
    root.destroy()


@pytest.fixture(autouse=True)
def tk_main_thread_only(monkeypatch):
    """The threading rule, enforced: any after() from a worker fails the test."""
    violations = []
    for name in ("after", "after_idle"):
        original = getattr(tk.Misc, name)

        def guarded(self, *args, _orig=original, _name=name, **kwargs):
            if threading.current_thread() is not MAIN:
                violations.append(_name)
            return _orig(self, *args, **kwargs)

        monkeypatch.setattr(tk.Misc, name, guarded)
    yield
    assert violations == []


def write_accounts(state_dir, *accounts):
    entries = [{"name": n, "config_dir": d, "provider": p} for n, d, p in accounts]
    (state_dir / "accounts.json").write_text(json.dumps({"accounts": entries}), encoding="utf-8")


def pump(harness, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        harness.window.root.update()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the Tk poll")


def open_tab(harness, name="accounts"):
    settings = SettingsWindow.open(harness.window, 0)
    settings._show(name)
    return settings, settings.tabs[name]


def settled(tab):
    return lambda: tab.snapshot is not None and not tab._checking and tab._pending == 0


STATUSES = {
    "/c/personal": HookStatus("installed"),
    "/c/work": HookStatus("outdated", "Needs update: Stop"),
    "/c/side": HookStatus("awaiting_approval", "Approve in Codex"),
    "/c/arch": HookStatus("unreachable", "WSL distro Ubuntu is not running."),
    "/c/local": HookStatus("local_only", "Hooks are in settings.local.json."),
    "/c/none": HookStatus("not_installed"),
    "/c/bad": HookStatus("error", "settings.json is not valid JSON"),
    "/c/gem": HookStatus("unsupported", "This harness has no hooks."),
}


@pytest.fixture
def fake_status(monkeypatch):
    calls = []

    def fake(config_dir, provider, *, distro_running=None, state_dir=None):
        calls.append((config_dir, provider, threading.current_thread(), distro_running))
        return STATUSES[config_dir]

    monkeypatch.setattr(settings_accounts, "hook_status_for_dir", fake)
    return calls


def all_accounts(state_dir):
    write_accounts(
        state_dir, ("personal", "/c/personal", "claude"), ("work", "/c/work", "codex"),
        ("side", "/c/side", "codex"), ("archive", "/c/arch", "codex"),
        ("local", "/c/local", "claude"), ("fresh", "/c/none", "claude"),
        ("broken", "/c/bad", "claude"), ("gem", "/c/gem", "gemini"))


def by_name(tab):
    return {row.data.name: row for row in tab.rows}


def enabled(button):
    return button is not None and str(button["state"]) == "normal"


@pytest.mark.gui
def test_rows_come_from_accounts_with_status_pills_and_buttons(harness, fake_status):
    all_accounts(harness.window.state_dir)
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    rows = by_name(tab)
    assert list(rows) == ["personal", "work", "side", "archive", "local", "fresh", "broken", "gem"]
    labels = {n: r.pill.cget("text") for n, r in rows.items()}
    assert labels == {
        "personal": "Installed", "work": "Outdated", "side": "Awaiting approval",
        "archive": "Unreachable", "local": "In local settings", "fresh": "Not installed",
        "broken": "Error", "gem": "Not supported"}
    # (install enabled, remove enabled); None = no buttons at all
    expected = {
        "personal": (False, True), "work": (True, True), "side": (False, True),
        "fresh": (True, False), "broken": (True, False),
        "archive": None, "local": None, "gem": None}
    for name, want in expected.items():
        row = rows[name]
        if want is None:
            assert row.install is None and row.remove is None, name
        else:
            assert (enabled(row.install), enabled(row.remove)) == want, name


@pytest.mark.gui
def test_status_runs_on_a_worker_and_lands_only_through_the_poll(harness, fake_status):
    all_accounts(harness.window.state_dir)
    _settings, tab = open_tab(harness)
    # Nothing has been applied yet: the worker result sits in the mailbox.
    assert tab.snapshot is None
    assert [r.pill.cget("text") for r in tab.rows] == []
    pump(harness, settled(tab))
    assert fake_status and all(call[2] is not MAIN for call in fake_status)


@pytest.mark.gui
def test_checking_pills_while_pending(harness, monkeypatch, fake_status):
    all_accounts(harness.window.state_dir)
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    gate = threading.Event()
    original = settings_accounts.hook_status_for_dir

    def slow(*args, **kwargs):
        gate.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(settings_accounts, "hook_status_for_dir", slow)
    tab.refresh()
    assert {r.pill.cget("text") for r in tab.rows} == {"Checking…"}
    assert not any(enabled(r.install) or enabled(r.remove) for r in tab.rows)
    gate.set()
    pump(harness, settled(tab))
    assert by_name(tab)["personal"].pill.cget("text") == "Installed"


@pytest.mark.gui
def test_distro_probe_is_passed_through_and_matches_case_insensitively(harness, fake_status):
    harness.window.running_distros = lambda: ["Ubuntu"]
    write_accounts(harness.window.state_dir, ("personal", "/c/personal", "claude"))
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    probe = fake_status[0][3]
    assert probe("ubuntu") is True and probe("Debian") is False


@pytest.mark.gui
def test_no_accounts_file_shows_default_row_resolved_on_a_worker(harness, monkeypatch, fake_status):
    seen = []
    monkeypatch.setattr(settings_accounts, "_default_config_dir",
                        lambda: seen.append(threading.current_thread()) or "/c/none")
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    assert seen and seen[0] is not MAIN
    (row,) = tab.rows
    assert row.data.is_default and row.data.name == "Default"
    assert enabled(row.install)


@pytest.mark.gui
def test_keychain_only_row_is_read_only(harness, fake_status):
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    tab.snapshot = settings_accounts.Snapshot(
        (settings_accounts.RowData("macOS", "claude", None, False, None),))
    tab._render()
    (row,) = tab.rows
    assert row.pill.cget("text") == "Read-only"
    assert row.install is None and row.remove is None


@pytest.mark.gui
def test_removed_account_disappears_on_refresh(harness, fake_status):
    write_accounts(harness.window.state_dir, ("personal", "/c/personal", "claude"),
                   ("work", "/c/work", "codex"))
    settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    assert list(by_name(tab)) == ["personal", "work"]
    write_accounts(harness.window.state_dir, ("personal", "/c/personal", "claude"))
    settings.refresh()
    pump(harness, settled(tab))
    assert list(by_name(tab)) == ["personal"]


@pytest.mark.gui
def test_buttons_disabled_and_banner_while_guard_held(harness, fake_status):
    state_dir = harness.window.state_dir
    write_accounts(state_dir, ("personal", "/c/personal", "claude"))
    token = hook_guard.try_acquire(state_dir, "test")
    try:
        _settings, tab = open_tab(harness)
        pump(harness, settled(tab))
        row = tab.rows[0]
        assert not enabled(row.remove) and not enabled(row.install)
        labels = [w.cget("text") for w in tab.banner_holder.winfo_children()[0].winfo_children()]
        assert "Another account change is in progress" in labels
        tab._start_op(row.data, "uninstall")  # ignored while locked
        assert tab._op_row is None
    finally:
        hook_guard.release(token)
    # The poll notices the release and re-enables the buttons by itself.
    pump(harness, lambda: tab.rows and enabled(tab.rows[0].remove))
    assert tab.banner_holder.winfo_children() == []


@pytest.mark.gui
def test_install_and_remove_run_under_the_guard_off_thread_then_requery(harness, monkeypatch, fake_status):
    state_dir = harness.window.state_dir
    write_accounts(state_dir, ("fresh", "/c/none", "claude"))
    ops = []

    def fake_apply(sd, config_dir, provider, op):
        ops.append((config_dir, provider, op, threading.current_thread(), hook_guard.held(sd)))
        STATUSES["/c/none"] = HookStatus("installed")
        return HookOperationResult(True, "ok")

    monkeypatch.setattr(settings_accounts, "apply_hook_operation", fake_apply)
    monkeypatch.setitem(STATUSES, "/c/none", HookStatus("not_installed"))
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    tab.rows[0].install.invoke()
    assert tab.rows[0].pill.cget("text") == "Working…"
    pump(harness, lambda: tab.rows and tab.rows[0].pill.cget("text") == "Installed")
    assert ops == [("/c/none", "claude", "install", ops[0][3], True)]
    assert ops[0][3] is not MAIN
    assert not hook_guard.held(state_dir)
    assert enabled(tab.rows[0].remove)


@pytest.mark.gui
def test_op_for_an_account_removed_meanwhile_does_not_run(harness, monkeypatch, fake_status):
    state_dir = harness.window.state_dir
    write_accounts(state_dir, ("fresh", "/c/none", "claude"), ("personal", "/c/personal", "claude"))
    ran = []
    monkeypatch.setattr(settings_accounts, "apply_hook_operation", lambda *a: ran.append(a))
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    row = by_name(tab)["fresh"]
    write_accounts(state_dir, ("personal", "/c/personal", "claude"))
    row.install.invoke()
    pump(harness, lambda: list(by_name(tab)) == ["personal"] and settled(tab)())
    assert ran == []
    assert "no longer" in tab.banner[1]
    assert not hook_guard.held(state_dir)


@pytest.mark.gui
def test_busy_guard_at_click_time_shows_banner(harness, fake_status):
    state_dir = harness.window.state_dir
    write_accounts(state_dir, ("fresh", "/c/none", "claude"))
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    row = tab.rows[0]
    token = hook_guard.try_acquire(state_dir, "someone else")
    try:
        # Bypass the Tk-side lock check to model the race the worker guards.
        assert settings_accounts.run_hook_op(state_dir, row.data, "install") == ("busy", None)
    finally:
        hook_guard.release(token)


@pytest.mark.gui
def test_blocked_by_banner_and_finish_it(harness, monkeypatch, fake_status):
    state_dir = harness.window.state_dir
    write_accounts(state_dir, ("fresh", "/c/none", "claude"), ("work", "/c/work", "codex"))
    monkeypatch.setattr(
        settings_accounts, "apply_hook_operation",
        lambda sd, d, p, op: HookOperationResult(False, "Finish the pending change for /c/work first.",
                                                 blocked_by="/c/work"))
    finished = []

    def fake_retry(sd, list_running_distros_fn=None):
        finished.append((threading.current_thread(), hook_guard.held(sd)))
        return HookOperationResult(True, "done")

    monkeypatch.setattr(settings_accounts, "retry_pending_hook_op", fake_retry)
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    by_name(tab)["fresh"].install.invoke()
    pump(harness, lambda: tab.banner is not None and settled(tab)())
    assert tab.banner[0] == "blocked"
    texts = [w.cget("text") for w in tab.banner_holder.winfo_children()[0].winfo_children()]
    assert any("work" in t and "Finish the pending change" in t for t in texts)
    tab.finish_button.invoke()
    pump(harness, lambda: tab.banner is None and settled(tab)())
    assert len(finished) == 1 and finished[0][0] is not MAIN and finished[0][1] is True
    assert not hook_guard.held(state_dir)


@pytest.mark.gui
def test_manage_accounts_close_rebuilds_rows(harness, monkeypatch, fake_status):
    from tokitty import accounts_ui

    state_dir = harness.window.state_dir
    write_accounts(state_dir, ("personal", "/c/personal", "claude"), ("work", "/c/work", "codex"))
    fake_manager = type("M", (), {"toplevel": tk.Toplevel(harness.window.root)})()
    harness.window.on_open_accounts = lambda: accounts_ui._manager_instances.__setitem__(
        id(harness.window.root), fake_manager)
    _settings, tab = open_tab(harness)
    pump(harness, settled(tab))
    assert list(by_name(tab)) == ["personal", "work"]
    tab._manage()
    write_accounts(state_dir, ("personal", "/c/personal", "claude"))
    time.sleep(0.3)
    harness.window.root.update()
    assert list(by_name(tab)) == ["personal", "work"]  # still open: no rebuild
    fake_manager.toplevel.destroy()
    accounts_ui._manager_instances.pop(id(harness.window.root), None)
    pump(harness, lambda: list(by_name(tab)) == ["personal"] and settled(tab)())


@pytest.mark.gui
def test_results_after_close_are_ignored_and_poll_cancelled(harness, monkeypatch):
    write_accounts(harness.window.state_dir, ("personal", "/c/personal", "claude"))
    gate = threading.Event()
    done = threading.Event()

    def slow(config_dir, provider, **kwargs):
        gate.wait(5)
        done.set()
        return HookStatus("installed")

    monkeypatch.setattr(settings_accounts, "hook_status_for_dir", slow)
    settings, tab = open_tab(harness)
    applied = []
    tab._on_result = lambda *a: applied.append(a)
    settings.close()
    assert tab.poller._after_id is None
    gate.set()
    assert done.wait(5)
    for _ in range(10):
        harness.window.root.update()
        time.sleep(0.02)
    assert applied == []
    assert tab.snapshot is None


@pytest.mark.gui
def test_refresh_from_other_tabs_does_not_query(harness, fake_status):
    write_accounts(harness.window.state_dir, ("personal", "/c/personal", "claude"))
    settings = SettingsWindow.open(harness.window, 0)
    harness.window.notify_state_changed()
    time.sleep(0.1)
    harness.window.root.update()
    assert fake_status == []
    assert settings.tabs["accounts"].snapshot is None


def test_elide_middle():
    assert settings_accounts.elide_middle("C:\\a\\b") == "C:\\a\\b"
    long = "C:\\Users\\nickw\\AppData\\Roaming\\profiles\\Claude\\personal"
    assert settings_accounts.elide_middle(long) == "C:\\Users\\nickw\\…\\Claude\\personal"
    out = settings_accounts.elide_middle("x" * 80)
    assert len(out) <= 38 and "…" in out


# -- Stream Dock -------------------------------------------------------------


class Deck:
    """Fake Stream Dock seams over a mutable state."""

    def __init__(self, harness, state="not_installed", supported=True):
        self.state = state
        self.hidden = set()
        self.calls = []
        w = harness.window
        w.streamdock_state = lambda: self.state
        w.streamdock_install = self.install
        w.streamdock_uninstall = self.uninstall
        w.streamdock_install_supported = lambda: supported
        w.on_edit_streamdock_presets = lambda: self.calls.append("presets")
        w.streamdock_account_toggles = lambda: [
            (n, (lambda n=n: n not in self.hidden), (lambda n=n: self.hidden.symmetric_difference_update({n})))
            for n in ("personal (/c/a)", "work (/c/b)")]

    def install(self):
        self.calls.append(("install", threading.current_thread()))
        self.state = "restart_to_connect"
        return True, ["Installed the Stream Dock plugin (port 1)."]

    def uninstall(self):
        self.calls.append(("uninstall", threading.current_thread()))
        self.state = "restart_to_finish_removal"
        return True, ["Removed the plugin."]


@pytest.mark.gui
@pytest.mark.parametrize("state,label", [
    ("not_installed", "Not installed"), ("not_connected", "Installed, not connected"),
    ("connected", "Connected"), ("restart_to_connect", "Restart tokitty to finish"),
    ("restart_to_finish_removal", "Restart tokitty to finish"),
    ("starting", "Starting"), ("failed", "Couldn't start")])
def test_streamdock_state_maps_to_pill(harness, state, label):
    Deck(harness, state)
    _settings, tab = open_tab(harness, "streamdock")
    assert tab.pill.cget("text") == label
    assert (tab.install_button is not None) == (state in ("not_installed", "restart_to_finish_removal"))
    assert (tab.uninstall_button is not None) == (
        state in ("not_connected", "connected", "restart_to_connect", "starting", "failed"))
    if state == "restart_to_finish_removal":
        assert not enabled(tab.install_button)


@pytest.mark.gui
@pytest.mark.parametrize("state,kind,words", [
    ("starting", "warn", ["Waiting for port 59967", "older copy"]),
    ("failed", "bad", ["listen on port 59967", "address in use", "Restart tokitty"])])
def test_streamdock_starting_and_failed_details_name_port_and_reason(harness, state, kind, words):
    Deck(harness, state)
    harness.window.streamdock_info = lambda: {"port": 59967, "reason": "address in use"}
    _settings, tab = open_tab(harness, "streamdock")
    detail = tab.detail.cget("text")
    assert all(w in detail for w in words)
    assert tab.pill.cget("bg") == _PILL_COLORS[kind][1]
    assert tab.presets_button is not None and enabled(tab.presets_button)


@pytest.mark.gui
def test_streamdock_open_tab_follows_state_without_switching_tabs(harness, monkeypatch):
    from tokitty import settings_streamdock

    monkeypatch.setattr(settings_streamdock, "STATE_WATCH_MS", 20)
    deck = Deck(harness, "starting")
    harness.window.streamdock_info = lambda: {"port": 59967, "reason": ""}
    settings, tab = open_tab(harness, "streamdock")
    assert tab.pill.cget("text") == "Starting"
    deck.state = "connected"
    pump(harness, lambda: tab.pill.cget("text") == "Connected")
    settings.close()
    assert tab._watch_id is None


@pytest.mark.gui
def test_streamdock_install_runs_on_worker_and_updates_pill(harness):
    deck = Deck(harness, "not_installed")
    _settings, tab = open_tab(harness, "streamdock")
    assert enabled(tab.install_button)
    tab.install_button.invoke()
    assert tab.pill.cget("text") == "Not installed"  # nothing applied until the poll
    pump(harness, lambda: tab.pill.cget("text") == "Restart tokitty to finish")
    assert deck.calls[0][0] == "install" and deck.calls[0][1] is not MAIN
    assert any("Installed the Stream Dock plugin" in line for _c, line, _col in tab.log)
    assert tab.uninstall_button is not None


@pytest.mark.gui
def test_streamdock_failed_install_logs_and_keeps_state(harness):
    deck = Deck(harness, "not_installed")
    harness.window.streamdock_install = lambda: (False, ["VSD Craft not found."])
    _settings, tab = open_tab(harness, "streamdock")
    tab.install_button.invoke()
    pump(harness, lambda: not tab._busy)
    assert deck.state == "not_installed"
    assert tab.pill.cget("text") == "Not installed"
    assert any("VSD Craft not found." in line for _c, line, _col in tab.log)
    assert tab.log[-1][1] == "Install did not finish."


@pytest.mark.gui
def test_streamdock_uninstall_confirms_then_disables_presets_and_toggles(harness):
    deck = Deck(harness, "connected")
    _settings, tab = open_tab(harness, "streamdock")
    assert enabled(tab.presets_button) and all(t.enabled for t in tab.toggles)
    tab.uninstall_button.invoke()
    assert deck.calls == []  # only asked
    tab.confirm_button.invoke()
    pump(harness, lambda: tab.pill.cget("text") == "Restart tokitty to finish")
    assert deck.calls[0][0] == "uninstall" and deck.calls[0][1] is not MAIN
    assert not enabled(tab.presets_button)
    assert tab.toggles and not any(t.enabled for t in tab.toggles)
    before = set(deck.hidden)
    tab.toggles[0]._clicked()
    assert deck.hidden == before


@pytest.mark.gui
def test_streamdock_cancel_confirm_does_nothing(harness):
    deck = Deck(harness, "connected")
    _settings, tab = open_tab(harness, "streamdock")
    tab.uninstall_button.invoke()
    tab._cancel_confirm()
    assert deck.calls == [] and tab.uninstall_button is not None


@pytest.mark.gui
def test_streamdock_presets_and_toggles(harness):
    deck = Deck(harness, "connected")
    _settings, tab = open_tab(harness, "streamdock")
    tab.presets_button.invoke()
    assert deck.calls == ["presets"]
    assert [t.get() for t in tab.toggles] == [True, True]
    tab.toggles[1]._clicked()
    assert deck.hidden == {"work (/c/b)"}
    assert tab.toggles[1].get() is False
    assert "done" in harness.calls


@pytest.mark.gui
def test_streamdock_non_windows_disables_install(harness):
    deck = Deck(harness, "not_installed", supported=False)
    _settings, tab = open_tab(harness, "streamdock")
    assert not enabled(tab.install_button)
    assert "Windows" in tab.note.cget("text")
    tab.install_button.invoke()
    assert deck.calls == []


@pytest.mark.gui
def test_streamdock_result_after_close_is_ignored(harness):
    gate = threading.Event()

    def slow():
        gate.wait(5)
        return True, ["late"]

    Deck(harness, "not_installed")
    harness.window.streamdock_install = slow
    settings, tab = open_tab(harness, "streamdock")
    tab.install_button.invoke()
    settings.close()
    gate.set()
    for _ in range(10):
        harness.window.root.update()
        time.sleep(0.02)
    assert not any(line == "late" for _c, line, _col in tab.log)
