"""The Settings Accounts tab, the one place for accounts (#268), over a real
widget, QtHost and Controls. apply_account_mutation, hook_status_for_dir and
apply_hook_operation are replaced with fakes that record their thread; dialogs
are patched, never shown. Named test_qt_accounts because tests/test_accounts.py
already owns the basename (no __init__.py)."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tokitty import accounts_logic, accounts_rows, hook_guard
from tokitty.accounts import load_accounts_result
from tokitty.customize import load_customization
from tokitty.hooks_install import HookOperationResult, HookStatus
from tokitty.manual_path import PathValidationResult
from tokitty.qt import settings as qs
from tokitty.qt import settings_accounts as qsa

pytestmark = pytest.mark.qt

MAIN = threading.main_thread()


def pump(until=lambda: False, limit_ms=4000):
    deadline = time.monotonic() + limit_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


def write_accounts(state_dir, *entries):
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"name": n, "config_dir": d, "provider": p}
                                 for n, d, p in entries]}), encoding="utf-8")


def labels(window):
    return [label.text() for label in window.findChildren(QLabel)]


def buttons(window, title):
    return [b for b in window.findChildren(QPushButton) if b.text() == title]


def ok_result(**extra):
    return SimpleNamespace(ok=True, message="done", warning=None, note=None, **extra)


class Dialogs:
    """Stands in for QMessageBox, QInputDialog and QFileDialog in the tab's
    module, recording the parent, the text and the thread of every call."""

    def __init__(self):
        self.boxes = []
        self.threads = []
        self.answers = []  # what QInputDialog.getText returns, in order
        self.prompts = []
        self.folders = []  # what QFileDialog.getExistingDirectory returns, in order
        self.folder_calls = []

    def _box(self, kind):
        def show(parent, title, body, *rest):
            self.threads.append(threading.current_thread())
            self.boxes.append((kind, parent, title, body))
        return staticmethod(show)

    def install(self, monkeypatch):
        dialogs = self
        message_box = type("Box", (), {
            "critical": self._box("critical"), "warning": self._box("warning"),
            "information": self._box("information")})

        def get_text(parent, title, label, **kwargs):
            dialogs.threads.append(threading.current_thread())
            dialogs.prompts.append((parent, title, label, kwargs.get("text")))
            return dialogs.answers.pop(0) if dialogs.answers else ("", False)

        def get_folder(parent, caption, start):
            dialogs.folder_calls.append((parent, caption, start))
            return dialogs.folders.pop(0) if dialogs.folders else ""

        monkeypatch.setattr(qsa, "QMessageBox", message_box)
        monkeypatch.setattr(qsa, "QInputDialog", SimpleNamespace(getText=get_text))
        monkeypatch.setattr(qsa, "QFileDialog", SimpleNamespace(getExistingDirectory=get_folder))
        return self


@pytest.fixture
def dialogs(monkeypatch):
    return Dialogs().install(monkeypatch)


@pytest.fixture(autouse=True)
def clean(qapp):
    yield
    window = qs._instance
    if window is not None:
        window.close()
    hook_guard._in_flight_operations.clear()


@pytest.fixture
def mutations(monkeypatch):
    """apply_account_mutation, faked. `outcome` is what it returns (or raises)."""
    calls = []
    box = SimpleNamespace(calls=calls, outcome=ok_result(), gate=None)

    def fake(state_dir, accounts, op, config_dir, provider="claude"):
        calls.append(SimpleNamespace(op=op, config_dir=config_dir, provider=provider,
                                     names=[a.name for a in accounts],
                                     thread=threading.current_thread(),
                                     guarded=hook_guard.held(state_dir)))
        if box.gate is not None:
            assert box.gate.wait(5)
        if isinstance(box.outcome, Exception):
            raise box.outcome
        return box.outcome

    monkeypatch.setattr(accounts_logic, "apply_account_mutation", fake)
    return box


def two_accounts(state_dir):
    write_accounts(state_dir, ("acct-a", str(state_dir / "a"), "claude"),
                   ("acct-b", str(state_dir / "b"), "codex"))


def three_accounts(state_dir):
    write_accounts(state_dir, *[(n, str(state_dir / n), "claude") for n in ("acct-a", "acct-b", "acct-c")])


STATUSES = {
    "/c/personal": HookStatus("installed"),
    "/c/work": HookStatus("outdated", "Needs update: Stop"),
    "/c/none": HookStatus("not_installed"),
    "/c/bad": HookStatus("error", "settings.json is not valid JSON"),
    "/c/arch": HookStatus("unreachable", "WSL distro Ubuntu is not running."),
}


@pytest.fixture
def fake_status(monkeypatch):
    calls = []

    def fake(config_dir, provider, *, distro_running=None, state_dir=None):
        calls.append((config_dir, provider, threading.current_thread(), distro_running))
        return STATUSES[config_dir]

    monkeypatch.setattr(accounts_rows, "hook_status_for_dir", fake)
    return calls


def open_tab(world):
    window = qs.SettingsWindow.open(world.host, 0)
    window._show("accounts")
    return window, window.tabs["accounts"]


def tab_settled(tab):
    return lambda: (tab.snapshot is not None and not tab._checking and tab._pending == 0
                    and not tab._mutating and not tab._validating)


def action(view, title):
    return view.menu_actions[title]


def by_name(tab):
    return {row.data.name: row for row in tab.rows}


def test_the_accounts_tab_sits_after_usage(make_world):
    window = qs.SettingsWindow.open(make_world(1).host, 0)
    assert list(window.tabs) == ["general", "look", "theme", "usage", "accounts", "streamdock"]
    assert [key for key, _title in qs.TABS][-3:-1] == ["usage", "accounts"]


def test_rows_come_from_accounts_with_pills_and_buttons(make_world, fake_status):
    world = make_world(2)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"), ("work", "/c/work", "codex"),
                   ("fresh", "/c/none", "claude"), ("broken", "/c/bad", "claude"),
                   ("arch", "/c/arch", "codex"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    rows = by_name(tab)
    assert {n: r.pill.text() for n, r in rows.items()} == {
        "personal": "Installed", "work": "Outdated", "fresh": "Not installed",
        "broken": "Error", "arch": "Unreachable"}
    assert rows["personal"].pill.property("tone") == "good"
    assert rows["broken"].pill.property("tone") == "bad"
    # a button only when one is needed: Install, or Repair for outdated hooks
    assert rows["personal"].install is None
    assert rows["fresh"].install.text() == "Install" and rows["fresh"].install.isEnabled()
    assert rows["broken"].install.text() == "Install"
    assert rows["work"].install.text() == "Repair" and rows["work"].install.isEnabled()
    assert rows["arch"].install is None
    # Remove hooks only follows REMOVE_STATES
    enabled = {n: action(r, "Remove hooks only").isEnabled() for n, r in rows.items()}
    assert enabled == {"personal": True, "work": True, "fresh": False, "broken": False,
                       "arch": False}
    assert rows["personal"].more.text() == "⋯"
    assert any("settings.json is not valid JSON" in t for t in labels(tab.table))
    assert fake_status and all(call[2] is not MAIN for call in fake_status)


def test_a_keychain_only_row_is_read_only(make_world, fake_status):
    world = make_world(1)
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    tab.snapshot = qsa.Snapshot((qsa.RowData("macOS", "claude", None, False, None),))
    tab._render()
    (row,) = tab.rows
    assert row.pill.text() == "Read-only"
    assert row.install is None
    assert not action(row, "Remove hooks only").isEnabled()
    assert not action(row, "Remove account…").isEnabled()
    assert "macOS Keychain (no config directory)" in labels(tab.table)


def test_the_distro_probe_matches_case_insensitively(make_world, fake_status):
    world = make_world(1)
    world.host.running_distros = lambda: ["Ubuntu"]
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    probe = fake_status[0][3]
    assert probe("ubuntu") is True and probe("Debian") is False


def test_a_status_pass_probes_the_running_distros_once(make_world, monkeypatch):
    # Each probe is a wsl.exe call: one per account held every row at
    # "Checking…" for about 200 ms per WSL account.
    probes = []
    world = make_world(1)
    world.host.running_distros = lambda: probes.append(1) or ["Ubuntu"]

    def fake(config_dir, provider, *, distro_running=None, state_dir=None):
        distro_running("Ubuntu")
        return STATUSES[config_dir]

    monkeypatch.setattr(accounts_rows, "hook_status_for_dir", fake)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"),
                   ("work", "/c/work", "claude"), ("none", "/c/none", "claude"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    assert len(probes) == 1
    tab.refresh()
    assert pump(tab_settled(tab))
    assert len(probes) == 2


def test_checking_pills_while_a_query_is_pending(make_world, monkeypatch, fake_status):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    gate = threading.Event()
    monkeypatch.setattr(accounts_rows, "hook_status_for_dir",
                        lambda *a, **k: (gate.wait(5), STATUSES["/c/personal"])[1])
    tab.refresh()
    assert {r.pill.text() for r in tab.rows} == {"Checking…"}
    assert not any(action(r, "Remove hooks only").isEnabled() for r in tab.rows)
    gate.set()
    assert pump(tab_settled(tab))
    assert tab.rows[0].pill.text() == "Installed"


def test_hook_buttons_are_disabled_while_the_guard_is_held(make_world, fake_status):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    token = hook_guard.try_acquire(world.state_dir, "test")
    try:
        _window, tab = open_tab(world)
        assert pump(tab_settled(tab))
        row = tab.rows[0]
        assert not action(row, "Remove hooks only").isEnabled()
        assert qsa.BUSY_MESSAGE in labels(tab.banner_holder)
        tab._start_op(row.data, "uninstall")  # ignored while locked
        assert tab._op_row is None
    finally:
        hook_guard.release(token)
    # the poll notices the release and re-enables the buttons by itself
    assert pump(lambda: tab.rows and action(tab.rows[0], "Remove hooks only").isEnabled())
    assert labels(tab.banner_holder) == []


def test_install_and_remove_run_under_the_guard_off_thread_then_requery(
        make_world, monkeypatch, fake_status):
    world = make_world(1)
    write_accounts(world.state_dir, ("fresh", "/c/none", "claude"))
    ops = []

    def fake_apply(state_dir, config_dir, provider, op):
        ops.append((config_dir, provider, op, threading.current_thread(), hook_guard.held(state_dir)))
        STATUSES["/c/none"] = HookStatus("installed")
        return HookOperationResult(True, "ok")

    monkeypatch.setattr(accounts_rows, "apply_hook_operation", fake_apply)
    monkeypatch.setitem(STATUSES, "/c/none", HookStatus("not_installed"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    tab.rows[0].install.click()
    assert tab.rows[0].pill.text() == "Working…"
    assert pump(lambda: tab.rows and tab.rows[0].pill.text() == "Installed")
    assert ops == [("/c/none", "claude", "install", ops[0][3], True)]
    assert ops[0][3] is not MAIN
    assert not hook_guard.held(world.state_dir)
    assert action(tab.rows[0], "Remove hooks only").isEnabled()


def test_remove_hooks_only_runs_the_uninstall_op(make_world, monkeypatch, fake_status):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    ops = []
    monkeypatch.setattr(accounts_rows, "apply_hook_operation",
                        lambda sd, d, p, op: ops.append((d, op)) or HookOperationResult(True, "ok"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    action(tab.rows[0], "Remove hooks only").trigger()
    assert pump(lambda: ops and tab_settled(tab)())
    assert ops == [("/c/personal", "uninstall")]
    assert load_accounts_result(world.state_dir).accounts  # the account stays


def test_an_op_for_an_account_removed_meanwhile_does_not_run(make_world, monkeypatch, fake_status):
    world = make_world(2)
    write_accounts(world.state_dir, ("fresh", "/c/none", "claude"), ("personal", "/c/personal", "claude"))
    ran = []
    monkeypatch.setattr(accounts_rows, "apply_hook_operation", lambda *a: ran.append(a))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    row = by_name(tab)["fresh"]
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    row.install.click()
    assert pump(lambda: list(by_name(tab)) == ["personal"] and tab_settled(tab)())
    assert ran == [] and "no longer" in tab.banner[1]


def test_a_blocked_banner_offers_finish_it(make_world, monkeypatch, fake_status):
    world = make_world(2)
    write_accounts(world.state_dir, ("fresh", "/c/none", "claude"), ("work", "/c/work", "codex"))
    monkeypatch.setattr(
        accounts_rows, "apply_hook_operation",
        lambda sd, d, p, op: HookOperationResult(
            False, "Finish the pending change for /c/work first.", blocked_by="/c/work"))
    finished = []

    def fake_retry(state_dir, list_running_distros_fn=None):
        finished.append((threading.current_thread(), hook_guard.held(state_dir)))
        return HookOperationResult(True, "done")

    monkeypatch.setattr(accounts_rows, "retry_pending_hook_op", fake_retry)
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    by_name(tab)["fresh"].install.click()
    assert pump(lambda: tab.banner is not None and tab_settled(tab)())
    assert tab.banner[0] == "blocked"
    assert any("Pending change for work." in t for t in labels(tab.banner_holder))
    assert tab.finish_button.isEnabled()
    tab.finish_button.click()
    assert pump(lambda: tab.banner is None and tab_settled(tab)())
    assert len(finished) == 1 and finished[0][0] is not MAIN and finished[0][1] is True
    assert not hook_guard.held(world.state_dir)


def test_a_failed_op_shows_a_bad_banner(make_world, monkeypatch, fake_status):
    world = make_world(1)
    write_accounts(world.state_dir, ("fresh", "/c/none", "claude"))
    monkeypatch.setattr(accounts_rows, "apply_hook_operation",
                        lambda *a: HookOperationResult(False, "settings.json is read-only"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    tab.rows[0].install.click()
    assert pump(lambda: tab.banner is not None and tab_settled(tab)())
    assert tab.banner[0] == "bad"
    assert "settings.json is read-only" in labels(tab.banner_holder)


def test_render_banner_returns_nothing_for_no_banner_and_replaces_the_old_one(make_world):
    world = make_world(1)
    _window, tab = open_tab(world)
    kit, holder = tab.kit, tab.banner_holder
    assert qsa.render_banner(kit, holder, None, name_for=str, on_finish=lambda: None,
                             finish_disabled=False) == (None, None)
    label, finish = qsa.render_banner(kit, holder, ("warn", "careful", None), name_for=str,
                                      on_finish=lambda: None, finish_disabled=False)
    assert label.text() == "careful" and finish is None
    label, finish = qsa.render_banner(kit, holder, ("blocked", "wait.", "/c/x"), name_for=str,
                                      on_finish=lambda: None, finish_disabled=True)
    assert label.text() == "wait. Pending change for /c/x." and not finish.isEnabled()
    assert len(holder.findChildren(QLabel)) == 2  # the glyph and the message: the old one is gone


def test_pill_spec_and_render_pill_are_reusable(make_world):
    make_world(1)  # a QApplication and the style sheet
    kit = qs.Kit()
    installed = qsa.RowData("a", "claude", "/c/a", False, HookStatus("installed"))
    assert qsa.pill_spec(installed) == ("Installed", "good")
    assert qsa.pill_spec(installed, "Working…") == ("Working…", "neutral")
    assert qsa.pill_spec(qsa.RowData("m", "claude", None, False, None)) == ("Read-only", "neutral")
    odd = qsa.RowData("a", "claude", "/c/a", False, HookStatus("surprising"))
    assert qsa.pill_spec(odd) == ("surprising", "neutral")
    pill = qsa.render_pill(kit, installed)
    assert (pill.text(), pill.property("tone")) == ("Installed", "good")


def test_results_after_the_window_closes_are_ignored(make_world, monkeypatch):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    gate, done = threading.Event(), threading.Event()

    def slow(config_dir, provider, **kwargs):
        gate.wait(5)
        done.set()
        return HookStatus("installed")

    monkeypatch.setattr(accounts_rows, "hook_status_for_dir", slow)
    window, tab = open_tab(world)
    applied = []
    tab._on_result = lambda *a: applied.append(a)
    window.close()
    gate.set()
    assert done.wait(5)
    pump(lambda: False, 300)
    assert applied == [] and tab.snapshot is None


def test_refresh_from_another_tab_does_not_query(make_world, fake_status):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    window = qs.SettingsWindow.open(world.host, 0)
    world.host.notify_state_changed()
    pump(lambda: False, 150)
    assert fake_status == []
    assert window.tabs["accounts"].snapshot is None


# ---- the merged page: layout, menu, rename ---------------------------------------------

def open_ready(world, fake_status=None):
    window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    return window, tab


def key_units(world, *names):
    """Key the fake units by account slug, as build_units does for real accounts."""
    for unit, name in zip(world.units, names):
        world.store[name] = world.store.pop(unit["key"])
        unit["key"] = name


@pytest.fixture
def accounts_changes(make_world):
    """Counts host.accounts_changed() calls without a #166 rebuild behind it."""
    def watch(world):
        calls = []
        world.host.on_accounts_changed = lambda: calls.append(load_accounts_result(world.state_dir))
        return calls
    return watch


def test_the_page_has_one_row_per_account_and_no_manage_button_or_restart_note(
        make_world, fake_status):
    world = make_world(2)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"), ("work", "/c/work", "codex"))
    _window, tab = open_ready(world)
    text = labels(tab.frame)
    assert "Each account is one cat on the card." in text
    assert "Add an account" in text and "Somewhere else" in text
    assert [r.label for r in tab.rows] == ["Cat 1", "Cat 2"]  # never the raw slug
    assert any(t.strip() == "Claude Code" for t in text) and any(t.strip() == "Codex" for t in text)
    assert buttons(tab.frame, "Manage accounts…") == []
    assert not any("restart" in t.lower() for t in text)
    assert set(tab.provider_picker.items) == {"claude", "codex"}
    assert tab.provider_picker.get() == "claude"


def test_the_menu_carries_the_account_actions_with_end_rules(make_world, fake_status):
    world = make_world(3)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"), ("work", "/c/work", "codex"),
                   ("fresh", "/c/none", "claude"))
    _window, tab = open_ready(world)
    first, middle, last = tab.rows
    assert list(first.menu_actions) == ["Rename…", "Move up", "Move down", "Remove hooks only",
                                        "Remove account…"]
    assert [a.text() for a in first.menu.actions() if a.text()] == list(first.menu_actions)
    assert [a for a in first.menu.actions() if a.isSeparator()]
    assert not action(first, "Move up").isEnabled() and action(first, "Move down").isEnabled()
    assert action(middle, "Move up").isEnabled() and action(middle, "Move down").isEnabled()
    assert action(last, "Move up").isEnabled() and not action(last, "Move down").isEnabled()
    assert action(first, "Remove hooks only").isEnabled()  # installed
    assert not action(last, "Remove hooks only").isEnabled()  # not installed
    assert all(action(r, "Remove account…").isEnabled() for r in tab.rows)
    assert all(action(r, "Rename…").isEnabled() for r in tab.rows)


def test_the_only_account_cannot_be_removed(make_world, fake_status, dialogs, mutations):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    _window, tab = open_ready(world)
    remove = action(tab.rows[0], "Remove account…")
    assert not remove.isEnabled() and "at least one account" in remove.toolTip()
    tab._remove_account("personal", "/c/personal", "claude")  # even if called directly
    assert mutations.calls == []


def test_rename_updates_the_running_pane_through_the_look_tab_seam(make_world, fake_status, dialogs):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    key_units(world, "acct-a", "acct-b")
    window, tab = open_ready(world)
    dialogs.answers.append(("Mochi", True))
    action(tab.rows[1], "Rename…").trigger()

    parent, title, label, current = dialogs.prompts[0]
    assert parent is window and (title, label) == ("Rename", "Cat name:")
    # pane i is accounts.json's i-th account, and the pane follows live
    assert world.host.pane_name(1)[0] == "Mochi" and world.host.pane_name(0)[0] == ""
    assert world.widget.panes[1]._label.endswith("Mochi")
    assert pump(lambda: [r.label for r in tab.rows] == ["Cat 1", "Mochi"] and tab_settled(tab)())

    dialogs.answers.append(("Nope", False))  # cancelled
    action(tab.rows[1], "Rename…").trigger()
    assert world.host.pane_name(1)[0] == "Mochi"
    assert dialogs.prompts[1][3] == "Mochi"  # the dialog starts from the current name


def test_rename_is_disabled_while_a_change_is_in_flight(make_world, fake_status, dialogs, mutations,
                                                        monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    assert all(action(r, "Rename…").isEnabled() for r in tab.rows)
    mutations.gate = threading.Event()
    action(tab.rows[0], "Move down").trigger()
    assert not any(action(r, "Rename…").isEnabled() for r in tab.rows)
    mutations.gate.set()
    assert pump(tab_settled(tab))


def test_the_look_tab_name_entry_shows_a_rename_made_here(make_world, fake_status, dialogs):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    key_units(world, "acct-a", "acct-b")
    window, tab = open_ready(world)
    dialogs.answers.append(("Mochi", True))
    action(tab.rows[0], "Rename…").trigger()
    window.set_pane(0)
    assert window.tabs["look"].name_entry.text() == "Mochi"


# ---- remove ------------------------------------------------------------------------------

def test_remove_account_confirms_first_and_cancel_changes_nothing(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    asked = []
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: asked.append(a) or False)
    action(tab.rows[1], "Remove account…").trigger()
    assert asked == [("Cat 2", "Codex", "/c/work")]
    pump(lambda: False, 200)
    assert mutations.calls == [] and len(load_accounts_result(world.state_dir).accounts) == 2


def test_the_confirmation_names_the_cat_the_path_and_defaults_to_cancel(
        make_world, fake_status, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    seen = {}

    def fake_exec(box):
        seen["text"], seen["detail"] = box.text(), box.informativeText()
        seen["buttons"] = [b.text() for b in box.buttons()]
        seen["default"] = box.defaultButton().text()
        seen["parent"] = box.parent()
        box._remove = next(b for b in box.buttons() if b.text() == "Remove account")

    monkeypatch.setattr(qsa.QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(qsa.QMessageBox, "clickedButton", lambda box: box._remove)
    assert tab._confirm_remove("sys", "Claude Code", "/c/work") is True
    # In the text, not only the title, which macOS doesn't show.
    assert seen["text"] == "Remove sys?" and seen["parent"] is tab.owner
    assert seen["detail"] == ("Its cat leaves the card and Tokitty removes its hooks from /c/work. "
                            "Your Claude Code login, history and settings stay. You can add it "
                            "back from Add an account.")
    assert seen["buttons"] == ["Cancel", "Remove account"] and seen["default"] == "Cancel"


def test_remove_runs_the_mutation_off_thread_under_the_guard_then_rebuilds(
        make_world, fake_status, dialogs, mutations, monkeypatch, accounts_changes):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    changes = accounts_changes(world)
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    mutations.gate = threading.Event()
    action(tab.rows[1], "Remove account…").trigger()

    assert tab._mutating and tab.close_blocked()
    assert not any(a.isEnabled() for r in tab.rows for a in r.menu_actions.values()
                   if a.text() != "Rename…")
    assert not tab.choose_button.isEnabled()
    mutations.gate.set()
    assert pump(lambda: tab_settled(tab)() and changes)
    (call,) = mutations.calls
    assert (call.op, call.provider, call.names) == ("remove", "codex", ["acct-a"])
    assert call.thread is not MAIN and call.guarded
    assert not hook_guard.held(world.state_dir) and not tab.close_blocked()
    assert dialogs.boxes == [] and tab.banner is None
    assert len(changes) == 1


def test_a_stale_remove_row_is_refreshed_instead_of_acted_on(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(3)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"),
                   ("acct-c", "/c/none", "claude"))
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-c", "/c/none", "claude"))
    action(tab.rows[1], "Remove account…").trigger()
    assert pump(lambda: len(tab.rows) == 2 and tab_settled(tab)())
    assert mutations.calls == []


def test_a_failed_mutation_is_reported_in_a_banner(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    mutations.outcome = SimpleNamespace(ok=False, message="hook install failed", warning=None, note=None)
    action(tab.rows[0], "Remove account…").trigger()
    assert pump(lambda: tab.banner is not None and tab_settled(tab)())
    assert tab.banner[0] == "blocked" and "hook install failed" in tab.banner[1]
    assert tab.banner[2] == "/c/personal" and tab.finish_button is not None  # Finish it retries
    assert dialogs.boxes == []


def test_a_pending_hook_change_holds_the_next_account_change_until_finished(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(3)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"),
                   ("acct-c", "/c/none", "claude"))
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    mutations.outcome = SimpleNamespace(ok=False, message="hook install failed", warning=None, note=None)
    # a failed mutation leaves its pending-op record, as apply_account_mutation does
    pending = {"op": "uninstall", "config_dir": "/c/personal", "provider": "claude"}
    monkeypatch.setattr(qsa, "load_pending_hook_op", lambda sd: pending)
    action(tab.rows[2], "Move up").trigger()
    assert tab.banner[0] == "blocked" and tab.banner[2] == "/c/personal"
    assert mutations.calls == []
    monkeypatch.setattr(qsa, "load_pending_hook_op", lambda sd: None)
    action(tab.rows[2], "Move up").trigger()
    assert pump(lambda: mutations.calls and tab_settled(tab)())


def test_a_raising_mutation_is_reported_and_frees_the_guard(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    mutations.outcome = RuntimeError("boom")
    action(tab.rows[0], "Remove account…").trigger()
    assert pump(lambda: tab.banner is not None and tab_settled(tab)())
    assert tab.banner[0] == "blocked" and "boom" in tab.banner[1]
    assert not hook_guard.held(world.state_dir)


def test_a_warning_and_a_note_share_one_banner(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    mutations.outcome = SimpleNamespace(ok=True, message="done", warning="partly done",
                                        note="waiting for approval in Codex")
    action(tab.rows[0], "Remove account…").trigger()
    assert pump(lambda: tab.banner is not None and tab_settled(tab)())
    assert tab.banner == ("warn", "partly done waiting for approval in Codex", None)


def test_a_mutation_is_refused_while_the_guard_is_held_elsewhere(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    token = hook_guard.try_acquire(world.state_dir, "elsewhere")
    try:
        tab._on_remove(tab.rows[0])
        tab._on_move("acct-a", "/c/personal", "claude", 1)
        tab._on_choose_folder()
        assert mutations.calls == [] and dialogs.folder_calls == []
    finally:
        hook_guard.release(token)


# ---- move --------------------------------------------------------------------------------

def test_move_runs_the_move_op_off_thread_with_the_new_order(
        make_world, fake_status, dialogs, mutations, accounts_changes):
    world = make_world(3)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"),
                   ("acct-c", "/c/none", "claude"))
    changes = accounts_changes(world)
    _window, tab = open_ready(world)
    action(tab.rows[2], "Move up").trigger()
    assert pump(lambda: tab_settled(tab)() and changes)
    (call,) = mutations.calls
    assert call.op == "move" and call.names == ["acct-a", "acct-c", "acct-b"]
    assert call.thread is not MAIN and call.guarded
    assert dialogs.boxes == []


def test_a_stale_move_row_changes_nothing(make_world, fake_status, dialogs, mutations):
    world = make_world(3)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"),
                   ("acct-c", "/c/none", "claude"))
    _window, tab = open_ready(world)
    tab._on_move("acct-gone", "/c/x", "claude", 1)
    tab._on_move("acct-a", "/c/personal", "claude", -1)
    assert pump(tab_settled(tab))
    assert mutations.calls == []


def test_move_uses_the_order_on_disk_not_the_stale_row(make_world, fake_status, dialogs, mutations):
    world = make_world(3)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"),
                   ("acct-c", "/c/none", "claude"))
    _window, tab = open_ready(world)
    write_accounts(world.state_dir, ("acct-c", "/c/none", "claude"), ("acct-a", "/c/personal", "claude"),
                   ("acct-b", "/c/work", "codex"))
    tab._on_move("acct-a", "/c/personal", "claude", 1)
    assert pump(lambda: mutations.calls and tab_settled(tab)())
    assert mutations.calls[0].names == ["acct-c", "acct-b", "acct-a"]


def test_a_failed_move_reports_the_save(make_world, fake_status, dialogs, mutations):
    world = make_world(3)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"),
                   ("acct-c", "/c/none", "claude"))
    _window, tab = open_ready(world)
    mutations.outcome = OSError("disk full")
    action(tab.rows[0], "Move down").trigger()
    assert pump(lambda: tab.banner is not None and tab_settled(tab)())
    assert tab.banner[0] == "bad"
    assert "Couldn't save the new order" in tab.banner[1] and "disk full" in tab.banner[1]
    assert "hook" not in tab.banner[1]


# ---- add ---------------------------------------------------------------------------------

def fake_validation(monkeypatch, config_dir, threads=None):
    def validate(provider, raw, active):
        if threads is not None:
            threads.append(threading.current_thread())
        return PathValidationResult(ok=True, config_dir=config_dir)

    monkeypatch.setattr(qsa, "validate_account_path", validate)
    monkeypatch.setattr(accounts_logic, "validate_account_path", validate)


def found_tab(world, matches, monkeypatch, codex_home=None):
    world.host.found_homes = lambda: matches
    monkeypatch.setattr(qsa, "discover_local_codex_home", lambda accounts: codex_home)
    return open_ready(world)


def test_found_homes_list_the_harness_where_and_path_with_an_add_button(
        make_world, fake_status, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = found_tab(
        world, [("Ubuntu", "/home/u/.claude-alma/.credentials.json")], monkeypatch,
        codex_home="C:\\Users\\u\\.codex")
    text = labels(tab.found_box)
    assert "Claude Code  ·  Ubuntu" in text and "Codex  ·  " + qsa._this_computer() in text
    # a WSL home shows its in-distro path, not the UNC one it is added by
    assert "/home/u/.claude-alma" in text and "C:\\Users\\u\\.codex" in text
    assert [b.text() for _p, b in tab.found_rows] == ["Add", "Add"]
    assert tab.add_header.hint.isVisibleTo(tab.frame)


def test_found_homes_are_read_on_a_worker_thread(make_world, fake_status, monkeypatch):
    # The sweep runs wsl.exe and can take seconds.
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    threads = []

    def found_homes():
        threads.append(threading.current_thread())
        return [("Ubuntu", "/home/u/.claude-edlio/.credentials.json")]

    world.host.found_homes = found_homes
    monkeypatch.setattr(qsa, "discover_local_codex_home", lambda accounts: None)
    _window, tab = open_ready(world)
    assert pump(lambda: len(tab.found_rows) == 1)
    assert threads and threading.main_thread() not in threads


def test_the_found_sweep_runs_when_the_tab_is_shown_not_on_each_refresh(
        make_world, fake_status, monkeypatch):
    # A sweep is a wsl.exe per running distro, about half a second, and a
    # move or rename cannot change which homes exist.
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    sweeps = []
    world.host.found_homes = lambda: sweeps.append(1) or []
    monkeypatch.setattr(qsa, "discover_local_codex_home", lambda accounts: None)
    window, tab = open_ready(world)
    assert pump(lambda: tab._pending == 0) and len(sweeps) == 1
    world.host.notify_state_changed()
    assert pump(tab_settled(tab)) and len(sweeps) == 1
    window._show("general")
    window._show("accounts")
    assert pump(lambda: tab._pending == 0 and tab_settled(tab)()) and len(sweeps) == 2


def test_no_found_homes_hides_the_hint_and_tolerates_a_missing_seam(
        make_world, fake_status, monkeypatch):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    world.host.found_homes = None
    monkeypatch.setattr(qsa, "discover_local_codex_home", lambda accounts: None)
    _window, tab = open_ready(world)
    assert tab.found_rows == [] and not tab.add_header.hint.isVisibleTo(tab.frame)


def test_a_found_home_that_is_already_an_account_is_filtered_at_render_time(
        make_world, fake_status, monkeypatch):
    world = make_world(1)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    matches = [("Ubuntu", "/home/u/.claude/.credentials.json")]
    _window, tab = found_tab(world, matches, monkeypatch)
    (path, _button), = tab.found_rows
    # the discovery list is a startup snapshot: the account is added meanwhile
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"), ("u", path, "claude"))
    tab._render()
    assert tab.found_rows == []


def test_adding_a_found_home_validates_saves_a_look_installs_and_rebuilds(
        make_world, fake_status, dialogs, mutations, monkeypatch, accounts_changes):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    changes = accounts_changes(world)
    _window, tab = found_tab(
        world, [("Ubuntu", "/home/u/.claude-alma/.credentials.json")], monkeypatch)
    (path, button), = tab.found_rows
    fake_validation(monkeypatch, path)
    button.click()
    assert pump(lambda: mutations.calls and tab_settled(tab)() and changes)
    (call,) = mutations.calls
    assert call.op == "install" and call.config_dir == path and call.provider == "claude"
    assert call.names[:2] == ["acct-a", "acct-b"] and len(call.names) == 3
    assert call.names[2] in load_customization(world.state_dir)  # a look was saved for it


def test_adding_the_local_codex_home_uses_the_codex_provider(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = found_tab(world, [], monkeypatch, codex_home="/home/u/.codex")
    (path, button), = tab.found_rows
    fake_validation(monkeypatch, path)
    button.click()
    assert pump(lambda: mutations.calls and tab_settled(tab)())
    assert mutations.calls[0].provider == "codex" and mutations.calls[0].config_dir == path


def test_somewhere_else_picks_a_folder_with_the_chosen_harness(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    new_dir = str(world.state_dir / "c")
    fake_validation(monkeypatch, new_dir)
    window, tab = open_ready(world)
    tab.provider_picker.items["codex"].click()
    dialogs.folders.append(new_dir)
    tab.choose_button.click()
    (parent, _caption, start), = dialogs.folder_calls
    assert parent is window and start == str(Path.home())
    assert pump(lambda: mutations.calls and tab_settled(tab)())
    assert mutations.calls[0].provider == "codex" and mutations.calls[0].config_dir == new_dir


def test_a_cancelled_or_invalid_pick_starts_nothing(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    tab.choose_button.click()  # cancelled: no folder
    monkeypatch.setattr(qsa, "validate_account_path",
                        lambda p, raw, a: PathValidationResult(ok=False, error="No credentials here."))
    dialogs.folders.append("/nowhere")
    tab.choose_button.click()
    assert dialogs.boxes[-1][0] == "critical" and dialogs.boxes[-1][3] == "No credentials here."
    assert mutations.calls == []


def test_a_duplicate_directory_is_refused(make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    fake_validation(monkeypatch, "/c/personal")
    _window, tab = open_ready(world)
    dialogs.folders.append("/c/personal")
    tab.choose_button.click()
    assert dialogs.boxes[-1][3] == "This account is already added."
    assert mutations.calls == []


def test_a_malformed_accounts_file_refuses_an_add(make_world, fake_status, dialogs, mutations):
    world = make_world(1)
    (world.state_dir / "accounts.json").write_text("{not json", encoding="utf-8")
    _window, tab = open_ready(world)
    dialogs.folders.append("/c/x")
    tab.choose_button.click()
    assert dialogs.boxes[-1][0] == "critical" and "malformed" in dialogs.boxes[-1][3]
    assert dialogs.folder_calls == [] and mutations.calls == []


def test_a_codex_wsl_path_is_validated_off_the_gui_thread(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    unc = r"\\wsl.localhost\Ubuntu\home\u\.codex"
    threads, gate = [], threading.Event()

    def validate(provider, raw, active):
        threads.append(threading.current_thread())
        assert gate.wait(5)
        return PathValidationResult(ok=True, config_dir=unc)

    monkeypatch.setattr(accounts_logic, "validate_account_path", validate)
    _window, tab = open_ready(world)
    tab._add_path(unc, load_accounts_result(world.state_dir), "codex")

    assert tab._validating
    assert not tab.choose_button.isEnabled()
    gate.set()
    assert pump(lambda: mutations.calls)
    assert threads and threads[0] is not MAIN
    assert mutations.calls[0].config_dir == unc and mutations.calls[0].provider == "codex"


def test_worker_callbacks_never_touch_the_gui(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    _window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    action(tab.rows[0], "Remove account…").trigger()
    assert pump(tab_settled(tab))
    assert all(t is MAIN for t in dialogs.threads)
    assert mutations.calls[0].thread is not MAIN


def test_the_window_stays_open_while_a_mutation_runs(
        make_world, fake_status, dialogs, mutations, monkeypatch):
    world = make_world(2)
    write_accounts(world.state_dir, ("acct-a", "/c/personal", "claude"), ("acct-b", "/c/work", "codex"))
    window, tab = open_ready(world)
    monkeypatch.setattr(tab, "_confirm_remove", lambda *a: True)
    mutations.gate = threading.Event()
    action(tab.rows[0], "Remove account…").trigger()
    window.close()
    assert window.isVisible()
    mutations.gate.set()
    assert pump(tab_settled(tab))
    window.close()
    assert not window.isVisible()
