"""The Qt accounts manager and the Settings Accounts tab, over a real widget,
QtHost and Controls. apply_account_mutation, retry_pending_hook_op,
hook_status_for_dir and apply_hook_operation are replaced with fakes that
record their thread; dialogs are patched, never shown. Named test_qt_accounts
because tests/test_accounts.py already owns the basename (no __init__.py)."""
import json
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tokitty import accounts_logic, accounts_rows, hook_guard
from tokitty.accounts import Account, save_accounts
from tokitty.customize import SINGLE_KEY, load_customization
from tokitty.hooks_install import HookOperationResult, HookStatus
from tokitty.manual_path import PathValidationResult
from tokitty.qt import accounts as qa
from tokitty.qt import settings as qs
from tokitty.qt import settings_accounts as qsa
from tokitty.settings import load_settings

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
    """Stands in for QMessageBox and QInputDialog in the manager's module,
    recording the parent, the text and the thread of every call."""

    def __init__(self):
        self.boxes = []
        self.threads = []
        self.answers = []  # what QInputDialog.getText returns, in order
        self.prompts = []

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

        def get_text(parent, title, label):
            dialogs.threads.append(threading.current_thread())
            dialogs.prompts.append((parent, title, label))
            return dialogs.answers.pop(0) if dialogs.answers else ("", False)

        monkeypatch.setattr(qa, "QMessageBox", message_box)
        monkeypatch.setattr(qa, "QInputDialog", SimpleNamespace(getText=get_text))
        return self


@pytest.fixture
def dialogs(monkeypatch):
    return Dialogs().install(monkeypatch)


@pytest.fixture(autouse=True)
def clean(qapp):
    yield
    manager = qa.current()
    if manager is not None:
        manager.close()
    window = qs._instance
    if window is not None:
        window.close()
    hook_guard._in_flight_operations.clear()


@pytest.fixture
def retries(monkeypatch):
    """The startup retry, faked: records the thread and returns no outcome."""
    calls = []
    monkeypatch.setattr(accounts_logic, "retry_pending_hook_op",
                        lambda state_dir: calls.append((state_dir, threading.current_thread())))
    return calls


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


def open_manager(world, matches=None, **kwargs):
    return qa.AccountsManager.open(world.state_dir, matches, host=world.host, **kwargs)


def settled(manager):
    return lambda: not (manager._retry_in_flight or manager._mutation_in_flight
                        or manager._validation_in_flight)


def two_accounts(state_dir):
    write_accounts(state_dir, ("acct-a", str(state_dir / "a"), "claude"),
                   ("acct-b", str(state_dir / "b"), "codex"))


# ---- every section renders ----------------------------------------------------

def test_every_section_renders_for_a_valid_accounts_file(make_world, retries, dialogs):
    world = make_world(2)
    two_accounts(world.state_dir)
    (world.state_dir / "a" / "projects").mkdir(parents=True)
    (world.state_dir / "a" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {}}))
    (world.state_dir / "b" / "sessions").mkdir(parents=True)
    (world.state_dir / "b" / "sessions" / "rollout.jsonl").write_text("{}")
    manager = open_manager(world, [("Ubuntu", "/home/u/.claude/.credentials.json")])
    text = labels(manager)

    assert manager.windowTitle() == "Accounts" and manager.parent() is None
    assert "Cat 1" in text and "Cat 2" in text
    # capability facts, per harness
    assert any("subscription usage: available" in t and "local usage: transcripts found" in t
               for t in text)
    assert any(t.startswith("Codex") and "local usage: rollouts found" in t for t in text)
    # discovered path
    assert any(t.startswith("Found in Ubuntu:") for t in text)
    # usage section
    assert set(manager.usage_groups) == {"view", "window", "readout"}
    assert manager._budget_entry is not None
    # restart notice, provider picker and the add button
    assert any("Tokitty restart needed for new panes" in t for t in text)
    assert manager.provider_picker.get() == "claude"
    assert set(manager.provider_picker.items) == {"claude", "codex"}
    assert not manager.add_box.isHidden() and manager.add_button.isEnabled() is False  # retry runs
    assert len(buttons(manager, "Rename…")) == 2 and len(buttons(manager, "Remove")) == 2
    assert pump(settled(manager))
    assert all(b.isEnabled() for b in buttons(manager, "Remove") + [manager.add_button])


def test_a_malformed_accounts_file_shows_only_the_malformed_row(make_world, retries, dialogs):
    world = make_world(1)
    (world.state_dir / "accounts.json").write_text("{nope")
    manager = open_manager(world)
    text = labels(manager)
    assert any("accounts.json is malformed" in t for t in text)
    assert buttons(manager, "Rename…") == [] and buttons(manager, "Remove") == []
    # the sections that do not depend on the file still render
    assert set(manager.usage_groups) == {"view", "window", "readout"}
    assert any("Tokitty restart needed" in t for t in text)
    assert not manager.add_box.isHidden()
    # and an add is refused with the reason
    assert pump(settled(manager))
    manager._on_add()
    assert dialogs.boxes[-1][0] == "critical" and "malformed" in dialogs.boxes[-1][3]
    assert dialogs.prompts == []


@pytest.mark.parametrize("content", ['{"accounts": []}', None])
def test_an_empty_or_absent_accounts_file_renders_no_rows(make_world, retries, dialogs, content):
    world = make_world(1)
    if content is not None:
        (world.state_dir / "accounts.json").write_text(content)
    manager = open_manager(world)
    assert buttons(manager, "Rename…") == []
    assert set(manager.usage_groups) == {"view", "window", "readout"}
    assert any("Tokitty restart needed" in t for t in labels(manager))
    assert not manager.add_box.isHidden()


def test_a_discovered_local_codex_home_gets_an_add_row(make_world, retries, dialogs, monkeypatch):
    world = make_world(1)
    codex = world.state_dir / "codex-home"
    (codex / "sessions").mkdir(parents=True)
    monkeypatch.setattr(accounts_logic, "default_codex_home", lambda: str(codex))
    write_accounts(world.state_dir, ("acct-a", str(world.state_dir / "a"), "claude"))
    manager = open_manager(world)
    assert any(t.startswith("Found Codex:") for t in labels(manager))
    assert len(buttons(manager, "Add")) == 1


def test_reopening_returns_the_same_window_and_refreshes_the_discovered_rows(
        make_world, retries, dialogs):
    world = make_world(1)
    two_accounts(world.state_dir)
    first = open_manager(world, [])
    assert not any(t.startswith("Found in") for t in labels(first))
    second = open_manager(world, [("Debian", "/home/u/.claude/.credentials.json")])
    assert second is first and qa.current() is first
    assert any(t.startswith("Found in Debian:") for t in labels(first))


def test_closing_fires_the_closed_callbacks_once_and_forgets_the_window(make_world, retries, dialogs):
    world = make_world(1)
    manager = open_manager(world)
    fired = []
    manager.closed.append(lambda: fired.append(1))
    assert manager in world.host._windows
    manager.close()
    manager.close()
    assert fired == [1]
    assert qa.current() is None and manager not in world.host._windows
    assert open_manager(world) is not manager


# ---- rename and remove ---------------------------------------------------------

def test_rename_goes_through_the_dialog_and_saves_the_label(make_world, retries, dialogs):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world)
    dialogs.answers.append(("Mochi", True))
    buttons(manager, "Rename…")[0].click()

    parent, title, label = dialogs.prompts[0]
    assert parent is manager and (title, label) == ("Rename", "Cat name:")
    assert load_customization(world.state_dir)["acct-a"].label == "Mochi"
    assert "Mochi" in labels(manager)

    dialogs.answers.append(("Nope", False))  # cancelled
    buttons(manager, "Rename…")[0].click()
    assert load_customization(world.state_dir)["acct-a"].label == "Mochi"


def test_remove_runs_the_mutation_off_thread_under_the_guard(make_world, retries, dialogs, mutations):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    refreshes = []
    real = manager._refresh_rows
    manager._refresh_rows = lambda: (refreshes.append(threading.current_thread()), real())[1]

    buttons(manager, "Remove")[1].click()

    assert manager._mutation_in_flight
    assert not any(b.isEnabled() for b in buttons(manager, "Remove") + [manager.add_button])
    assert pump(settled(manager))
    (call,) = mutations.calls
    assert (call.op, call.provider, call.names) == ("remove", "codex", ["acct-a"])
    assert call.thread is not MAIN and call.guarded
    assert not hook_guard.held(world.state_dir)
    assert dialogs.boxes == []
    assert refreshes and all(t is MAIN for t in refreshes)
    assert all(b.isEnabled() for b in buttons(manager, "Remove"))


def test_the_last_account_cannot_be_removed(make_world, retries, dialogs, mutations):
    world = make_world(1)
    write_accounts(world.state_dir, ("acct-a", str(world.state_dir / "a"), "claude"))
    manager = open_manager(world)
    assert pump(settled(manager))
    (remove,) = buttons(manager, "Remove")
    assert not remove.isEnabled()
    manager._on_remove("acct-a", str(world.state_dir / "a"))
    assert mutations.calls == []


def test_a_stale_row_is_refreshed_instead_of_acted_on(make_world, retries, dialogs, mutations):
    world = make_world(2)
    dirs = [str(world.state_dir / n) for n in "abc"]
    write_accounts(world.state_dir, ("acct-a", dirs[0], "claude"), ("acct-b", dirs[1], "codex"),
                   ("acct-c", dirs[2], "claude"))
    manager = open_manager(world)
    assert pump(settled(manager))
    assert len(buttons(manager, "Remove")) == 3
    write_accounts(world.state_dir, ("acct-a", dirs[0], "claude"), ("acct-c", dirs[2], "claude"))
    manager._on_remove("acct-b", dirs[1], "codex")
    assert mutations.calls == []
    assert len(buttons(manager, "Remove")) == 2


def test_a_failed_mutation_is_reported_and_blocks_the_next_change(
        make_world, retries, dialogs, mutations):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    mutations.outcome = SimpleNamespace(ok=False, message="hook install failed", warning=None, note=None)
    buttons(manager, "Remove")[0].click()
    assert pump(lambda: dialogs.boxes)
    kind, parent, title, body = dialogs.boxes[0]
    assert (kind, parent, title) == ("critical", manager, "Accounts")
    assert "hook install failed" in body and "Reopen Accounts" in body
    assert manager._pending_hook_failure
    assert not any(b.isEnabled() for b in buttons(manager, "Remove") + [manager.add_button])


def test_a_raising_mutation_is_reported(make_world, retries, dialogs, mutations):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    mutations.outcome = RuntimeError("boom")
    buttons(manager, "Remove")[0].click()
    assert pump(lambda: dialogs.boxes)
    assert dialogs.boxes[0][0] == "critical" and "boom" in dialogs.boxes[0][3]
    assert not hook_guard.held(world.state_dir)


def test_a_warning_and_a_note_share_one_box(make_world, retries, dialogs, mutations):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    mutations.outcome = SimpleNamespace(ok=True, message="done", warning="partly done",
                                        note="waiting for approval in Codex")
    buttons(manager, "Remove")[0].click()
    assert pump(lambda: dialogs.boxes)
    assert len(dialogs.boxes) == 1
    assert dialogs.boxes[0][0] == "warning"
    assert dialogs.boxes[0][3] == "partly done\n\nwaiting for approval in Codex"


# ---- add ---------------------------------------------------------------------------

def fake_validation(monkeypatch, config_dir, threads=None):
    def validate(provider, raw, active):
        if threads is not None:
            threads.append(threading.current_thread())
        return PathValidationResult(ok=True, config_dir=config_dir)

    monkeypatch.setattr(qa, "validate_account_path", validate)
    monkeypatch.setattr(accounts_logic, "validate_account_path", validate)


def test_add_by_path_validates_saves_a_look_and_installs(
        make_world, retries, dialogs, mutations, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    new_dir = str(world.state_dir / "c")
    fake_validation(monkeypatch, new_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    dialogs.answers.append((new_dir, True))
    manager.add_button.click()

    assert dialogs.prompts[0][1:] == ("Add account", "Claude config directory:")
    assert pump(settled(manager))
    (call,) = mutations.calls
    assert call.op == "install" and call.config_dir == new_dir and call.provider == "claude"
    assert call.names[:2] == ["acct-a", "acct-b"] and len(call.names) == 3
    assert call.names[2] in load_customization(world.state_dir)  # a look was saved for it


def test_the_harness_picker_chooses_the_prompt_and_provider(
        make_world, retries, dialogs, mutations, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    new_dir = str(world.state_dir / "c")
    fake_validation(monkeypatch, new_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    manager.provider_picker.items["codex"].click()
    dialogs.answers.append((new_dir, True))
    manager.add_button.click()
    assert dialogs.prompts[0][2] == "Codex home directory:"
    assert pump(settled(manager))
    assert mutations.calls[0].provider == "codex"


def test_cancelled_and_invalid_adds_start_nothing(make_world, retries, dialogs, mutations, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    dialogs.answers.append(("", True))
    manager.add_button.click()
    monkeypatch.setattr(qa, "validate_account_path",
                        lambda p, raw, a: PathValidationResult(ok=False, error="No credentials here."))
    dialogs.answers.append(("/nowhere", True))
    manager.add_button.click()
    assert dialogs.boxes[-1][0] == "critical" and dialogs.boxes[-1][3] == "No credentials here."
    assert mutations.calls == []


def test_a_duplicate_directory_is_refused(make_world, retries, dialogs, mutations, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    fake_validation(monkeypatch, str(world.state_dir / "a"))
    manager = open_manager(world)
    assert pump(settled(manager))
    dialogs.answers.append((str(world.state_dir / "a"), True))
    manager.add_button.click()
    assert dialogs.boxes[-1][3] == "This account is already added."
    assert mutations.calls == []


def test_adding_a_discovered_path_installs_it(make_world, retries, dialogs, mutations, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world, [("Ubuntu", "/home/u/.claude/.credentials.json")])
    found = accounts_logic.build_discovered_path_specs(
        [("Ubuntu", "/home/u/.claude/.credentials.json")])[0].config_dir
    fake_validation(monkeypatch, found)
    assert pump(settled(manager))
    buttons(manager, "Add")[0].click()
    assert pump(settled(manager))
    assert mutations.calls[0].op == "install" and mutations.calls[0].config_dir == found


def test_a_codex_wsl_path_is_validated_off_the_gui_thread(
        make_world, retries, dialogs, mutations, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    unc = r"\\wsl.localhost\Ubuntu\home\u\.codex"
    threads, gate = [], threading.Event()

    def validate(provider, raw, active):
        threads.append(threading.current_thread())
        assert gate.wait(5)
        return PathValidationResult(ok=True, config_dir=unc)

    monkeypatch.setattr(accounts_logic, "validate_account_path", validate)
    manager = open_manager(world)
    assert pump(settled(manager))
    manager._add_path(unc, accounts_logic.load_accounts_result(world.state_dir), "codex")

    assert manager._validation_in_flight
    assert not any(b.isEnabled() for b in buttons(manager, "Remove") + [manager.add_button])
    gate.set()
    assert pump(lambda: mutations.calls)
    assert threads and threads[0] is not MAIN
    assert mutations.calls[0].config_dir == unc and mutations.calls[0].provider == "codex"
    assert pump(settled(manager))


# ---- macOS virtual row -----------------------------------------------------------

@pytest.fixture
def mac(monkeypatch, tmp_path):
    """sys.platform is darwin as the manager sees it, with an empty home."""
    monkeypatch.setattr(qa, "sys", SimpleNamespace(platform="darwin"))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(qa.Path, "home", staticmethod(lambda: home))
    return home


def test_the_macos_virtual_row_replaces_the_rows_and_hides_add(make_world, retries, dialogs, mac):
    world = make_world(1)
    manager = open_manager(world)
    assert "Default macOS account (Keychain)" in labels(manager)
    assert manager.add_box.isHidden()
    (remove,) = buttons(manager, "Remove")
    assert not remove.isEnabled()
    assert len(buttons(manager, "Rename…")) == 1
    assert set(manager.usage_groups) == {"view", "window", "readout"}


def test_renaming_the_macos_virtual_row_saves_under_the_single_key(
        make_world, retries, dialogs, mac):
    world = make_world(1)
    manager = open_manager(world)
    dialogs.answers.append(("Kiki", True))
    buttons(manager, "Rename…")[0].click()
    assert load_customization(world.state_dir)[SINGLE_KEY].label == "Kiki"
    dialogs.answers.append(("Nope", False))
    buttons(manager, "Rename…")[0].click()
    assert load_customization(world.state_dir)[SINGLE_KEY].label == "Kiki"


def test_a_local_credentials_file_means_no_virtual_row(make_world, retries, dialogs, mac):
    world = make_world(1)
    (mac / ".claude").mkdir()
    (mac / ".claude" / ".credentials.json").write_text("{}")
    manager = open_manager(world)
    assert "Default macOS account (Keychain)" not in labels(manager)
    assert not manager.add_box.isHidden()


# ---- usage section -----------------------------------------------------------------

def test_usage_controls_write_through_the_host_seams_and_notify(make_world, retries, dialogs):
    world = make_world(1)
    manager = open_manager(world)
    notified = []
    real = world.host.notify_state_changed
    world.host.notify_state_changed = lambda: (notified.append(1), real())[1]

    manager.usage_groups["view"][0].items["models"].click()
    manager.usage_groups["window"][0].items["24h"].click()
    manager.usage_groups["readout"][0].items["tokens"].click()

    settings = load_settings(world.state_dir)
    assert (settings.view_mode, settings.usage_window, settings.usage_readout) == (
        "models", "24h", "tokens")
    assert world.usage_state["view"] == "models"  # the live state follows too
    assert len(notified) == 3


def test_the_budget_entry_sets_clears_and_reports_errors(make_world, retries, dialogs):
    world = make_world(1)
    manager = open_manager(world)
    entry = manager._budget_entry
    entry.setText("$12.5")
    entry.returnPressed.emit()
    assert world.host.budget_for_pane(0) == 12.5
    entry.setText("abc")
    entry.returnPressed.emit()
    assert dialogs.boxes[-1][0] == "critical" and dialogs.boxes[-1][2] == "Budget"
    assert "not a number" in dialogs.boxes[-1][3]
    entry.setText("0")
    entry.returnPressed.emit()
    assert "greater than zero" in dialogs.boxes[-1][3]
    assert world.host.budget_for_pane(0) == 12.5
    entry.setText("")
    entry.returnPressed.emit()
    assert world.host.budget_for_pane(0) is None


def test_the_usage_controls_show_the_current_settings_on_open(make_world, retries, dialogs):
    world = make_world(1)
    world.host.on_view_mode("models")
    world.host.set_budget_for_pane(0, "7")
    manager = open_manager(world)
    assert manager.usage_groups["view"][0].get() == "models"
    assert manager._budget_entry.text() == "7"


# ---- in flight, close and reopen --------------------------------------------------

def test_the_startup_retry_runs_off_the_gui_thread_and_gates_the_buttons(
        make_world, dialogs, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    gate, calls = threading.Event(), []

    def slow(state_dir):
        calls.append(threading.current_thread())
        assert gate.wait(5)
        return None

    monkeypatch.setattr(accounts_logic, "retry_pending_hook_op", slow)
    manager = open_manager(world)
    assert manager._retry_in_flight
    assert hook_guard.held(world.state_dir)
    assert not any(b.isEnabled() for b in buttons(manager, "Remove") + [manager.add_button])
    gate.set()
    assert pump(settled(manager))
    assert calls and calls[0] is not MAIN
    assert all(b.isEnabled() for b in buttons(manager, "Remove") + [manager.add_button])
    assert not hook_guard.held(world.state_dir)


def test_a_failed_startup_retry_is_reported(make_world, dialogs, monkeypatch):
    world = make_world(2)
    two_accounts(world.state_dir)
    monkeypatch.setattr(accounts_logic, "retry_pending_hook_op",
                        lambda state_dir: HookOperationResult(False, "still pending"))
    manager = open_manager(world)
    assert pump(lambda: dialogs.boxes)
    assert dialogs.boxes[0][0] == "critical" and "still pending" in dialogs.boxes[0][3]
    assert manager._pending_hook_failure


def test_a_mutation_in_flight_survives_close_and_reopen(make_world, retries, dialogs, mutations):
    world = make_world(2)
    two_accounts(world.state_dir)
    first = open_manager(world)
    assert pump(settled(first))
    mutations.gate = threading.Event()
    buttons(first, "Remove")[1].click()
    assert pump(lambda: mutations.calls)

    first.close()
    second = open_manager(world)

    assert second is not first
    assert second._mutation_in_flight and not second._retry_in_flight
    assert retries and len(retries) == 1  # only the first window started one
    assert not any(b.isEnabled() for b in buttons(second, "Remove") + [second.add_button])
    second._on_add()
    second._on_remove("acct-a", str(world.state_dir / "a"))
    assert dialogs.prompts == [] and len(mutations.calls) == 1

    mutations.gate.set()
    assert pump(settled(second))
    assert all(b.isEnabled() for b in buttons(second, "Remove"))
    assert not hook_guard.held(world.state_dir)
    assert len(mutations.calls) == 1


def test_results_after_close_are_not_applied(make_world, retries, dialogs, mutations):
    world = make_world(2)
    two_accounts(world.state_dir)
    manager = open_manager(world)
    assert pump(settled(manager))
    mutations.gate = threading.Event()
    mutations.outcome = SimpleNamespace(ok=False, message="late failure", warning=None, note=None)
    buttons(manager, "Remove")[0].click()
    assert pump(lambda: mutations.calls)
    manager.close()
    mutations.gate.set()
    assert pump(lambda: not hook_guard.held(world.state_dir))
    pump(lambda: False, 300)
    assert dialogs.boxes == []  # the closed window's poll never ran


def test_worker_callbacks_never_touch_the_gui(make_world, retries, dialogs, mutations, monkeypatch):
    """Every on_done runs on a worker. It may only publish through hook_guard;
    each dialog and each repaint happens on the GUI thread."""
    world = make_world(2)
    two_accounts(world.state_dir)
    done_threads = []
    real = qa._complete_operation
    monkeypatch.setattr(qa, "_complete_operation",
                        lambda *a: (done_threads.append(threading.current_thread()), real(*a))[1])
    mutations.outcome = SimpleNamespace(ok=True, message="done", warning="heads up", note=None)
    manager = open_manager(world)
    assert pump(settled(manager))
    buttons(manager, "Remove")[0].click()
    assert pump(lambda: dialogs.boxes)
    assert any(t is not MAIN for t in done_threads)  # the fake really ran off-thread
    assert all(t is MAIN for t in dialogs.threads)


def test_always_in_front_follows_the_widget_for_an_open_manager(make_world, retries, dialogs):
    world = make_world(1)
    hint = Qt.WindowType.WindowStaysOnTopHint
    assert world.widget.always_on_top
    manager = open_manager(world)
    assert manager.windowFlags() & hint
    world.host.toggle_always_on_top()
    assert not manager.windowFlags() & hint and manager.isVisible()
    world.host.toggle_always_on_top()
    assert manager.windowFlags() & hint and manager.isVisible()


def test_a_manager_opened_with_always_in_front_off_has_no_hint(make_world, retries, dialogs):
    world = make_world(1)
    world.widget.set_always_on_top(False)
    manager = open_manager(world)
    assert not manager.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


# ---- the Settings tab -----------------------------------------------------------------

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
    return lambda: tab.snapshot is not None and not tab._checking and tab._pending == 0


def by_name(tab):
    return {row.data.name: row for row in tab.rows}


def test_the_accounts_tab_sits_after_usage(make_world):
    window = qs.SettingsWindow.open(make_world(1).host, 0)
    assert list(window.tabs) == ["general", "look", "theme", "usage", "accounts"]
    assert [key for key, _title in qs.TABS][-2:] == ["usage", "accounts"]


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
    assert rows["personal"].remove.isEnabled() and not rows["personal"].install.isEnabled()
    assert rows["fresh"].install.isEnabled() and not rows["fresh"].remove.isEnabled()
    assert rows["work"].install.isEnabled() and rows["work"].remove.isEnabled()  # outdated: both
    assert rows["arch"].install is None and rows["arch"].remove is None  # no buttons
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
    assert row.install is None and row.remove is None
    assert "macOS Keychain (no config directory)" in labels(tab.table)


def test_the_distro_probe_matches_case_insensitively(make_world, fake_status):
    world = make_world(1)
    world.host.running_distros = lambda: ["Ubuntu"]
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    probe = fake_status[0][3]
    assert probe("ubuntu") is True and probe("Debian") is False


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
    assert not any(r.install.isEnabled() or r.remove.isEnabled() for r in tab.rows)
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
        assert not row.remove.isEnabled() and not row.install.isEnabled()
        assert qsa.BUSY_MESSAGE in labels(tab.banner_holder)
        tab._start_op(row.data, "uninstall")  # ignored while locked
        assert tab._op_row is None
    finally:
        hook_guard.release(token)
    # the poll notices the release and re-enables the buttons by itself
    assert pump(lambda: tab.rows and tab.rows[0].remove.isEnabled())
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
    assert tab.rows[0].remove.isEnabled()


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


def test_manage_accounts_is_hidden_without_an_opener(make_world, fake_status):
    world = make_world(1)
    _window, tab = open_tab(world)
    assert tab.manage_button is None


def test_closing_the_manager_rebuilds_the_tab_rows(make_world, retries, dialogs, fake_status):
    world = make_world(2)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"), ("work", "/c/work", "codex"))
    world.host.on_open_accounts = lambda: qa.AccountsManager.open(
        world.state_dir, [], host=world.host)
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    assert list(by_name(tab)) == ["personal", "work"]
    tab.manage_button.click()
    manager = qa.current()
    assert manager is not None and tab._on_manager_closed in manager.closed
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"))
    pump(lambda: False, 300)
    assert list(by_name(tab)) == ["personal", "work"]  # still open: no rebuild
    manager.close()
    assert pump(lambda: list(by_name(tab)) == ["personal"] and tab_settled(tab)())


def test_an_account_removed_in_the_manager_leaves_the_tab(
        make_world, retries, dialogs, monkeypatch, fake_status):
    world = make_world(2)
    write_accounts(world.state_dir, ("personal", "/c/personal", "claude"), ("work", "/c/work", "codex"))

    def fake_remove(state_dir, accounts, op, config_dir, provider="claude"):
        save_accounts(state_dir, [Account(name=a.name, config_dir=a.config_dir, provider=a.provider)
                                  for a in accounts])
        return ok_result()

    monkeypatch.setattr(accounts_logic, "apply_account_mutation", fake_remove)
    world.host.on_open_accounts = lambda: qa.AccountsManager.open(
        world.state_dir, [], host=world.host)
    _window, tab = open_tab(world)
    assert pump(tab_settled(tab))
    tab.manage_button.click()
    manager = qa.current()
    assert pump(settled(manager))
    next(b for b in buttons(manager, "Remove")).click()
    assert pump(settled(manager))
    assert list(by_name(tab)) == ["personal", "work"]  # not until the manager closes
    manager.close()
    assert pump(lambda: len(by_name(tab)) == 1 and tab_settled(tab)())


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


def test_a_closed_tab_stops_watching_the_manager(make_world, retries, dialogs, fake_status):
    world = make_world(1)
    world.host.on_open_accounts = lambda: qa.AccountsManager.open(
        world.state_dir, [], host=world.host)
    window, tab = open_tab(world)
    tab.manage_button.click()
    manager = qa.current()
    window.close()
    assert tab._on_manager_closed not in manager.closed
