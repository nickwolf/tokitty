"""The Qt first-run walkthrough (offscreen). Discovery, save, status, install
and recovery are fakes (or the real save into tmp_path); each fake records the
thread it ran on so the Settings threading rule is asserted."""
import json
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from tokitty import first_run
from tokitty.accounts import load_accounts_result
from tokitty.first_run import Candidate
from tokitty.hooks_install import HookOperationResult, HookStatus
from tokitty.qt import walkthrough as qw
from tokitty.qt.poller import QtPoller
from tokitty.qt.walkthrough import Walkthrough
from tokitty.accounts_rows import RowData, Snapshot
from tokitty.settings import load_settings

pytestmark = pytest.mark.qt

MAIN = threading.main_thread()


@pytest.fixture(autouse=True)
def gui_thread_only(monkeypatch):
    """The threading rule, enforced: a worker arming the poller (a QTimer) or
    putting anything but a Mailbox result would fail the test."""
    violations = []
    real_start = QtPoller.start

    def guarded(self):
        if threading.current_thread() is not MAIN:
            violations.append("poller.start")
        return real_start(self)

    monkeypatch.setattr(QtPoller, "start", guarded)
    yield
    assert violations == []


def candidates(tmp_path):
    return [
        Candidate("claude", str(tmp_path / "home" / ".claude"), "This PC", True),
        Candidate("claude", str(tmp_path / "wsl" / ".claude"), "WSL: Ubuntu", True),
        Candidate("codex", str(tmp_path / "home" / ".codex"), "This PC", True),
        Candidate("claude", str(tmp_path / "wsl" / ".claude-work"), "WSL: Ubuntu", False),
    ]


def pump(until=lambda: False, limit_ms=4000):
    deadline = time.monotonic() + limit_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


class Rig:
    """A Walkthrough over fakes that record threads and calls."""

    def __init__(self, state_dir, found, **overrides):
        self.state_dir = state_dir
        self.found = found
        self.threads = {}
        self.calls = []
        self.statuses = {}
        self.discover_gate = None
        self.op_gate = None
        self.op_result = None
        self.finish_result = ("done", None)
        self.extra_rows = []
        self.saves = []
        self.closed = 0

        def discover():
            self.threads["discover"] = threading.current_thread()
            if self.discover_gate is not None:
                self.discover_gate.wait(5)
            return self.found

        def save(picked):
            self.saves.append(list(picked))
            return first_run.save_picked_accounts(state_dir, picked)

        def collect(running):
            self.threads.setdefault("collect", []).append(threading.current_thread())
            self.calls.append("collect")
            loaded = load_accounts_result(state_dir)
            if loaded.state == "absent":
                entries = [("Default", "claude", str(found[0].config_dir) if found else None)]
            else:
                entries = [(a.name, a.provider, a.config_dir) for a in loaded.accounts]
            entries += self.extra_rows
            rows = [RowData(n, p, d, False, self.statuses.get(n, HookStatus("not_installed", "Ready.")))
                    for n, p, d in entries]
            return Snapshot(tuple(rows))

        def run_hook_op(row, op):
            self.threads.setdefault("op", []).append(threading.current_thread())
            self.calls.append(("op", row.name, op))
            if self.op_gate is not None:
                self.op_gate.wait(5)
            if self.op_result is not None:
                return self.op_result
            self.statuses[row.name] = HookStatus("installed", "")
            return "done", HookOperationResult(True, "ok")

        def finish_pending():
            self.threads.setdefault("finish", []).append(threading.current_thread())
            self.calls.append("finish")
            return self.finish_result

        kwargs = dict(discover=discover, save=save, collect=collect,
                      run_hook_op=run_hook_op, finish_pending=finish_pending)
        kwargs.update(overrides)
        self.walk = Walkthrough(state_dir, **kwargs)
        self.walk.closed.connect(self._count_closed)
        self.walk.show()

    def _count_closed(self):
        self.closed += 1

    def pump(self, condition, timeout_ms=5000):
        assert pump(lambda: not self.alive() or condition(), timeout_ms)

    def alive(self):
        return self.walk.alive()

    def settle(self, n=15):
        for _ in range(n):
            QApplication.processEvents()
            time.sleep(0.01)

    def to_accounts(self):
        self.walk.primary_button.click()
        self.pump(lambda: self.walk.candidates is not None or self.walk.discover_error)

    def to_hooks(self):
        self.to_accounts()
        self.walk.primary_button.click()
        self.pump(lambda: self.walk.snapshot is not None)

    def texts(self):
        return [label.text() for label in self.walk.page.findChildren(QLabel)]

    def done(self):
        return load_settings(self.state_dir).first_run


@pytest.fixture
def rig(qapp, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    made = []

    def build(found=None, **overrides):
        r = Rig(state, candidates(tmp_path) if found is None else found, **overrides)
        made.append(r)
        return r

    build.state = state
    yield build
    for r in made:
        if r.alive():
            r.walk.close()
        r.walk.deleteLater()
    QApplication.processEvents()


def test_click_through_every_step(rig, monkeypatch):
    marks = []
    real = first_run.mark_done
    monkeypatch.setattr(first_run, "mark_done", lambda d: (marks.append(d), real(d)))
    r = rig()
    w = r.walk
    assert w.step == "welcome" and w.back_button is None
    assert w.windowTitle() == "Tokitty setup" and w.parent() is None
    assert "Welcome to tokitty" in r.texts()
    assert w.nav.strips["welcome"].property("active") is True

    r.to_accounts()
    assert w.step == "accounts"
    assert [b.checked for b in w.checks] == [True, True, True, False]
    assert w.primary_button.text() == "Add 3 accounts"
    assert r.threads["discover"] is not MAIN
    assert not (r.state_dir / "accounts.json").exists()

    w.primary_button.click()
    assert w.step == "hooks"
    r.pump(lambda: w.snapshot is not None)
    assert len(w.hook_rows) == 3
    assert {v["install"].isEnabled() for v in w.hook_rows} == {True}
    assert len(load_accounts_result(r.state_dir).accounts) == 3

    w.install_all_button.click()
    r.pump(lambda: [v["pill"].text() for v in w.hook_rows] == ["Installed"] * 3 and not w._writing())
    assert [c for c in r.calls if isinstance(c, tuple)] == [
        ("op", a.name, "install") for a in load_accounts_result(r.state_dir).accounts]

    w.primary_button.click()
    assert w.step == "done"
    w.primary_button.click()
    assert not r.alive()
    assert r.done() == "done"
    assert len(marks) == 1 and r.closed == 1


def test_the_rail_lists_the_four_steps_and_marks_the_current_one(rig):
    r = rig()
    assert list(r.walk.nav.strips) == ["welcome", "accounts", "hooks", "done"]
    assert [s.name.text() for s in r.walk.nav.strips.values()] == [
        "Welcome", "Accounts", "Live activity", "Done"]
    r.to_hooks()
    active = [k for k, s in r.walk.nav.strips.items() if s.property("active")]
    assert active == ["hooks"]


def test_default_checks_and_the_add_count(rig):
    r = rig()
    r.to_accounts()
    w = r.walk
    assert [b.checked for b in w.checks] == [True, True, True, False]
    w.checks[3].toggle()
    assert w.primary_button.text() == "Add 4 accounts"
    for box in w.checks[:3]:
        box.toggle()
    assert w.primary_button.text() == "Add 1 account"
    assert qw.TRANSCRIPTS_ONLY_NOTE in r.texts()


def test_unchecking_everything_continues_without_accounts_and_skips_hooks(rig):
    r = rig()
    r.to_accounts()
    w = r.walk
    for box in w.checks:
        if box.checked:
            box.toggle()
    assert w.primary_button.text() == "Continue without accounts"
    w.primary_button.click()
    assert w.step == "done"
    assert not (r.state_dir / "accounts.json").exists()
    assert r.saves == []
    w.back_button.click()
    assert w.step == "accounts"


def test_workers_never_run_on_the_gui_thread(rig):
    r = rig()
    r.to_hooks()
    r.walk.install_all_button.click()
    r.pump(lambda: not r.walk._writing() and len(r.threads.get("op", [])) == 3)
    for name in ("discover", "collect", "finish", "op"):
        threads = r.threads[name]
        threads = threads if isinstance(threads, list) else [threads]
        assert threads and all(t is not MAIN for t in threads), name


def test_recovery_runs_before_install_buttons_are_enabled(rig):
    r = rig()
    gate = threading.Event()
    real_finish = r.walk._finish_pending

    def slow_finish():
        gate.wait(5)
        return real_finish()

    r.walk._finish_pending = slow_finish
    r.walk.primary_button.click()
    r.pump(lambda: r.walk.candidates is not None)
    r.walk.primary_button.click()
    r.settle()
    assert r.walk.step == "hooks" and r.walk.snapshot is None
    assert r.walk.hook_rows == [] and "collect" not in r.calls[:1]
    gate.set()
    r.pump(lambda: r.walk.snapshot is not None)
    assert r.calls.index("finish") < r.calls.index("collect")
    assert all(v["install"].isEnabled() for v in r.walk.hook_rows)


def test_blocked_install_shows_finish_it_and_it_runs_recovery(rig):
    r = rig()
    r.to_hooks()
    w = r.walk
    blocked = HookOperationResult(False, "Another change is waiting.", blocked_by=w._rows()[1].config_dir)
    r.op_result = ("done", blocked)
    w.hook_rows[0]["install"].click()
    r.pump(lambda: w.finish_it_button is not None and w.finish_it_button.isEnabled())
    assert "Another change is waiting." in w.banner_label.text()
    assert "Pending change for" in w.banner_label.text()
    r.calls.clear()
    r.op_result = None
    w.finish_it_button.click()
    r.pump(lambda: "finish" in r.calls and not w._writing() and w.finish_it_button is None)
    assert r.calls.index("finish") < r.calls.index("collect")


def test_a_busy_guard_shows_the_busy_banner(rig):
    r = rig()
    r.to_hooks()
    r.op_result = ("busy", None)
    r.walk.hook_rows[0]["install"].click()
    r.pump(lambda: r.walk.banner is not None)
    assert r.walk.banner[1] == qw.BUSY_MESSAGE
    assert r.walk.banner_label.text() == qw.BUSY_MESSAGE


def test_a_failed_install_shows_the_failure_banner(rig):
    r = rig()
    r.to_hooks()
    r.op_result = ("done", HookOperationResult(False, "settings.json is read-only"))
    r.walk.hook_rows[0]["install"].click()
    r.pump(lambda: r.walk.banner is not None and not r.walk._writing())
    assert r.walk.banner[0] == "bad" and "read-only" in r.walk.banner_label.text()


def test_skip_and_close_are_disabled_while_an_install_is_in_flight(rig):
    r = rig()
    r.to_hooks()
    w = r.walk
    r.op_gate = threading.Event()
    w.hook_rows[0]["install"].click()
    r.pump(lambda: w._op_row is not None)
    assert not w.skip_button.isEnabled()
    assert not w.back_button.isEnabled()
    assert not w.primary_button.isEnabled()
    w.skip()
    assert w.close() is False  # the window's close button
    assert r.alive() and r.done() == "" and r.closed == 0
    r.op_gate.set()
    r.pump(lambda: not w._writing())
    assert w.skip_button.isEnabled()
    w.skip()
    assert not r.alive() and r.done() == "done" and r.closed == 1


def test_skip_on_welcome_writes_only_the_marker(rig):
    r = rig()
    r.walk.skip_button.click()
    assert not r.alive()
    assert r.done() == "done"
    assert not (r.state_dir / "accounts.json").exists()
    assert not r.walk.isVisible()


def test_skip_during_discovery_drops_the_result(rig, monkeypatch):
    cancels = []
    real_cancel = QtPoller.cancel
    monkeypatch.setattr(QtPoller, "cancel", lambda self: (cancels.append(1), real_cancel(self)))
    r = rig()
    r.discover_gate = threading.Event()
    r.walk.primary_button.click()
    r.settle()
    assert r.walk.candidates is None
    assert r.walk.spinner is not None and r.walk.skip_button.isEnabled()
    assert not r.walk.primary_button.isEnabled()
    r.walk.skip_button.click()
    assert not r.alive() and r.done() == "done" and cancels
    r.discover_gate.set()
    r.settle()
    assert r.walk.candidates is None
    assert not (r.state_dir / "accounts.json").exists()


def test_the_spinner_advances_while_discovery_runs(rig):
    r = rig()
    r.discover_gate = threading.Event()
    r.walk.primary_button.click()
    assert pump(lambda: r.walk._spin >= 2)
    assert r.walk.spinner.angle == (-r.walk._spin * 40) % 360
    r.discover_gate.set()
    r.pump(lambda: r.walk.candidates is not None)
    assert r.walk.spinner is None


@pytest.mark.parametrize("platform, note", [
    ("darwin", "Checking this Mac"),
    ("win32", "Checking this PC and WSL installs"),
])
def test_the_search_note_only_mentions_wsl_on_windows(rig, platform, note):
    r = rig(platform=platform)
    r.discover_gate = threading.Event()
    r.walk.primary_button.click()
    r.settle()
    assert r.walk.candidates is None
    assert note in r.texts()
    if platform == "darwin":
        assert not any("WSL" in t for t in r.texts())
    r.discover_gate.set()
    r.pump(lambda: r.walk.candidates is not None)


def test_skip_on_accounts_before_the_button_writes_no_accounts(rig):
    r = rig()
    r.to_accounts()
    r.walk.skip_button.click()
    assert not r.alive() and r.done() == "done"
    assert not (r.state_dir / "accounts.json").exists()
    assert r.saves == []


def test_skip_on_hooks_keeps_the_accounts_and_installs_nothing(rig):
    r = rig()
    r.to_hooks()
    r.walk.skip_button.click()
    assert not r.alive() and r.done() == "done"
    assert len(load_accounts_result(r.state_dir).accounts) == 3
    assert [c for c in r.calls if isinstance(c, tuple)] == []


def test_closing_the_window_is_skip(rig):
    r = rig()
    r.to_accounts()
    r.walk.close()
    assert not r.alive() and r.done() == "done"
    assert not (r.state_dir / "accounts.json").exists()


def test_closed_fires_once_however_it_is_reached(rig):
    r = rig()
    r.walk.skip()
    r.walk.skip()
    r.walk.finish()
    r.walk.close()
    r.settle()
    assert r.closed == 1


def test_a_write_failure_still_closes(rig, monkeypatch, capsys):
    def fail(state_dir):
        raise OSError("disk full")

    monkeypatch.setattr(first_run, "mark_done", fail)
    r = rig()
    r.walk.skip()
    assert r.closed == 1
    assert "disk full" in capsys.readouterr().err


def test_done_has_back_and_finish_but_no_skip(rig):
    r = rig()
    r.to_hooks()
    r.walk.primary_button.click()
    w = r.walk
    assert w.step == "done"
    assert w.skip_button is None
    assert w.back_button.text() == "Back"
    assert w.primary_button.text() == "Finish"
    assert "You're set" in r.texts()
    w.back_button.click()
    assert w.step == "hooks"


def test_going_back_after_saving_never_saves_again(rig):
    r = rig()
    r.to_hooks()
    r.walk.back_button.click()
    assert r.walk.step == "accounts"
    assert all(not b.enabled for b in r.walk.checks)
    assert qw.LOCKED_NOTE in r.texts()
    r.walk.primary_button.click()
    assert r.walk.step == "hooks"
    assert len(r.saves) == 1


def test_macos_single_read_only_row_writes_nothing(rig, tmp_path):
    row = Candidate("claude", str(tmp_path / ".claude"), "This Mac", True, read_only=True,
                    detail="Signed in (Keychain)")
    r = rig(found=[row])
    r.to_accounts()
    w = r.walk
    assert any("Signed in (Keychain)" in t for t in r.texts())
    assert w.checks == [None]
    assert w.primary_button.text() == "Next"
    w.primary_button.click()
    assert w.step == "hooks"
    r.pump(lambda: w.snapshot is not None)
    assert [v["data"].name for v in w.hook_rows] == ["Default"]
    assert not (r.state_dir / "accounts.json").exists() and r.saves == []


def test_a_forced_run_never_overwrites_an_existing_accounts_file(rig):
    original = json.dumps({"accounts": [{"name": "mine", "config_dir": "/x/.claude", "provider": "claude"}]})
    (rig.state / "accounts.json").write_text(original, encoding="utf-8")
    r = rig()
    r.to_accounts()
    w = r.walk
    assert qw.LOCKED_NOTE in r.texts()
    assert w.checks and all(b is None for b in w.checks)
    w.primary_button.click()
    r.pump(lambda: w.snapshot is not None)
    assert [v["data"].name for v in w.hook_rows] == ["mine"]
    w.primary_button.click()
    w.primary_button.click()
    assert not r.alive()
    assert (r.state_dir / "accounts.json").read_text(encoding="utf-8") == original
    assert r.saves == []


def test_nothing_found_says_so_and_offers_skip(rig):
    r = rig(found=[])
    r.to_accounts()
    assert "No Claude Code or Codex installs found." in r.texts()
    assert r.walk.skip_button is not None
    assert r.walk.primary_button.text() == "Continue"
    r.walk.primary_button.click()
    assert r.walk.step == "done" and not (r.state_dir / "accounts.json").exists()


def test_a_discovery_failure_is_shown_and_skippable(rig):
    def boom():
        raise RuntimeError("wsl.exe hung")

    r = rig(discover=boom)
    r.to_accounts()
    assert any("wsl.exe hung" in t for t in r.texts())
    r.walk.skip_button.click()
    assert r.done() == "done"


def test_a_save_failure_stays_on_the_step_with_the_error(rig):
    def refuse(picked):
        raise OSError("read-only disk")

    r = rig(save=refuse)
    r.to_accounts()
    r.walk.primary_button.click()
    assert r.walk.step == "accounts"
    assert any("Could not save the accounts: read-only disk" in t for t in r.texts())
    assert not r.walk.accounts_locked


def test_only_the_saved_accounts_get_hook_rows(rig):
    r = rig()
    r.extra_rows = [("stranger", "claude", "/elsewhere/.claude")]
    r.to_hooks()
    assert "stranger" not in [v["data"].name for v in r.walk.hook_rows]


def test_unsupported_rows_show_a_pill_and_no_button(rig):
    r = rig()
    r.to_hooks()
    name = r.walk.hook_rows[0]["data"].name
    r.statuses[name] = HookStatus("unsupported", "")
    r.walk._query()
    r.pump(lambda: r.walk.hook_rows[0]["pill"].text() == "Not supported")
    assert r.walk.hook_rows[0]["install"] is None


def test_done_points_mac_users_at_the_app_menu(rig):
    r = rig(platform="darwin")
    r.to_hooks()
    r.walk.primary_button.click()
    assert any("Tokitty ▸ Settings…" in t for t in r.texts())


def test_done_says_right_click_elsewhere(rig):
    r = rig(platform="win32")
    r.to_hooks()
    r.walk.primary_button.click()
    texts = r.texts()
    assert any("Right-click any cat" in t for t in texts)
    assert not any("Tokitty ▸ Settings…" in t for t in texts)


def test_the_defaults_discover_through_the_shared_cache_and_save_for_real(qapp, tmp_path, monkeypatch):
    seen = {}

    def fake_discover(cache, *, platform):
        seen["cache"], seen["platform"] = cache, platform
        seen["thread"] = threading.current_thread()
        return [Candidate("claude", str(tmp_path / "c" / ".claude"), "This PC", True)]

    monkeypatch.setattr(first_run, "discover_candidates", fake_discover)
    state = tmp_path / "state"
    state.mkdir()
    cache = object()
    w = Walkthrough(state, cache, lambda: [], platform="win32")
    w.show()
    try:
        w.primary_button.click()
        assert pump(lambda: w.candidates is not None)
        assert seen["cache"] is cache and seen["platform"] == "win32" and seen["thread"] is not MAIN
        assert w._running_distros() == []
        w.primary_button.click()
        assert [a.config_dir for a in load_accounts_result(state).accounts] == [str(tmp_path / "c" / ".claude")]
    finally:
        w.close()
        w.deleteLater()
