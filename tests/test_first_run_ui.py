"""GUI tests for the first-run walkthrough (xvfb). Discovery, save, status,
install and recovery are fakes (or the real save into tmp_path); each fake
records the thread it ran on so the Settings threading rule is asserted."""
import json
import threading
import time

import pytest

tk = pytest.importorskip("tkinter")

from tokitty import first_run, first_run_ui  # noqa: E402
from tokitty.accounts import load_accounts_result  # noqa: E402
from tokitty.first_run import Candidate  # noqa: E402
from tokitty.first_run_ui import Walkthrough  # noqa: E402
from tokitty.hooks_install import HookOperationResult, HookStatus  # noqa: E402
from tokitty.settings import load_settings  # noqa: E402
from tokitty.settings_accounts import RowData, Snapshot  # noqa: E402

pytestmark = pytest.mark.gui

MAIN = threading.main_thread()


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


def candidates(tmp_path):
    return [
        Candidate("claude", str(tmp_path / "home" / ".claude"), "This PC", True),
        Candidate("claude", str(tmp_path / "wsl" / ".claude"), "WSL: Ubuntu", True),
        Candidate("codex", str(tmp_path / "home" / ".codex"), "This PC", True),
        Candidate("claude", str(tmp_path / "wsl" / ".claude-work"), "WSL: Ubuntu", False),
    ]


class Rig:
    """A Walkthrough over fakes that record threads and calls."""

    def __init__(self, root, state_dir, found, **overrides):
        self.root = root
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
        self.walk = Walkthrough(root, state_dir, 1.0, **kwargs)

    def pump(self, condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive():
                return
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError("condition never held")

    def alive(self):
        return self.walk.alive()

    def settle(self, n=15):
        for _ in range(n):
            if self.alive():
                self.root.update()
            time.sleep(0.01)

    def to_accounts(self):
        self.walk.primary_button.invoke()
        self.pump(lambda: self.walk.candidates is not None or self.walk.discover_error)

    def to_hooks(self):
        self.to_accounts()
        self.walk.primary_button.invoke()
        self.pump(lambda: self.walk.snapshot is not None)

    def texts(self):
        out = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    out.append(child.cget("text"))
                walk(child)

        walk(self.walk.page)
        return out

    def done(self):
        return load_settings(self.state_dir).first_run


@pytest.fixture
def rig(tmp_path):
    root = tk.Tk()
    root.withdraw()
    state = tmp_path / "state"
    state.mkdir()
    made = []

    def build(found=None, **overrides):
        r = Rig(root, state, candidates(tmp_path) if found is None else found, **overrides)
        made.append(r)
        return r

    build.root = root
    build.state = state
    yield build
    for r in made:
        if r.alive():
            r.walk.toplevel.destroy()
    root.destroy()


def button_state(button):
    return str(button.cget("state"))


def test_click_through_every_step(rig, monkeypatch):
    marks = []
    real = first_run.mark_done
    monkeypatch.setattr(first_run, "mark_done", lambda d: (marks.append(d), real(d)))
    r = rig()
    w = r.walk
    assert w.step == "welcome" and w.back_button is None
    assert "Welcome to tokitty" in r.texts()

    r.to_accounts()
    assert w.step == "accounts"
    assert [b.checked for b in w.checks] == [True, True, True, False]
    assert w.primary_button.cget("text") == "Add 3 accounts"
    assert r.threads["discover"] is not MAIN
    assert not (r.state_dir / "accounts.json").exists()

    w.primary_button.invoke()
    assert w.step == "hooks"
    r.pump(lambda: w.snapshot is not None)
    assert len(w.hook_rows) == 3
    assert {v["install"].cget("state") for v in w.hook_rows} == {"normal"}
    assert len(load_accounts_result(r.state_dir).accounts) == 3

    w.install_all_button.invoke()
    r.pump(lambda: [v["pill"].cget("text") for v in w.hook_rows] == ["Installed"] * 3 and not w._writing())
    assert [c for c in r.calls if isinstance(c, tuple)] == [
        ("op", a.name, "install") for a in load_accounts_result(r.state_dir).accounts]

    w.primary_button.invoke()
    assert w.step == "done"
    w.primary_button.invoke()
    assert not r.alive()
    assert r.done() == "done"
    assert len(marks) == 1


def test_default_checks_and_the_add_count(rig):
    r = rig()
    r.to_accounts()
    w = r.walk
    assert [b.checked for b in w.checks] == [True, True, True, False]
    w.checks[3].toggle()
    assert w.primary_button.cget("text") == "Add 4 accounts"
    for box in w.checks[:3]:
        box.toggle()
    assert w.primary_button.cget("text") == "Add 1 account"
    assert TRANSCRIPTS_ONLY in r.texts()


TRANSCRIPTS_ONLY = first_run_ui.TRANSCRIPTS_ONLY_NOTE


def test_unchecking_everything_continues_without_accounts_and_skips_hooks(rig):
    r = rig()
    r.to_accounts()
    w = r.walk
    for box in w.checks:
        if box.checked:
            box.toggle()
    assert w.primary_button.cget("text") == "Continue without accounts"
    w.primary_button.invoke()
    assert w.step == "done"
    assert not (r.state_dir / "accounts.json").exists()
    assert r.saves == []
    w.back_button.invoke()
    assert w.step == "accounts"


def test_workers_never_run_on_the_tk_thread(rig):
    r = rig()
    r.to_hooks()
    r.walk.install_all_button.invoke()
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
    r.walk.primary_button.invoke()
    r.pump(lambda: r.walk.candidates is not None)
    r.walk.primary_button.invoke()
    r.settle()
    assert r.walk.step == "hooks" and r.walk.snapshot is None
    assert r.walk.hook_rows == [] and "collect" not in r.calls[:1]
    gate.set()
    r.pump(lambda: r.walk.snapshot is not None)
    assert r.calls.index("finish") < r.calls.index("collect")
    assert all(v["install"].cget("state") == "normal" for v in r.walk.hook_rows)


def test_blocked_install_shows_finish_it_and_it_runs_recovery(rig):
    r = rig()
    r.to_hooks()
    w = r.walk
    blocked = HookOperationResult(False, "Another change is waiting.", blocked_by=w._rows()[1].config_dir)
    r.op_result = ("done", blocked)
    w.hook_rows[0]["install"].invoke()
    r.pump(lambda: w.finish_it_button is not None and button_state(w.finish_it_button) == "normal")
    assert "Another change is waiting." in w.banner_label.cget("text")
    assert "Pending change for" in w.banner_label.cget("text")
    r.calls.clear()
    r.op_result = None
    w.finish_it_button.invoke()
    r.pump(lambda: "finish" in r.calls and not w._writing() and w.finish_it_button is None)
    assert r.calls.index("finish") < r.calls.index("collect")


def test_a_busy_guard_shows_the_busy_banner(rig):
    r = rig()
    r.to_hooks()
    r.op_result = ("busy", None)
    r.walk.hook_rows[0]["install"].invoke()
    r.pump(lambda: r.walk.banner is not None)
    assert r.walk.banner[1] == first_run_ui.BUSY_MESSAGE


def test_skip_and_close_are_disabled_while_an_install_is_in_flight(rig):
    r = rig()
    r.to_hooks()
    w = r.walk
    r.op_gate = threading.Event()
    w.hook_rows[0]["install"].invoke()
    r.pump(lambda: w._op_row is not None)
    assert button_state(w.skip_button) == "disabled"
    assert button_state(w.back_button) == "disabled"
    assert button_state(w.primary_button) == "disabled"
    w.skip()
    w.toplevel.tk.call(w.toplevel.protocol("WM_DELETE_WINDOW"))
    assert r.alive() and r.done() == ""
    r.op_gate.set()
    r.pump(lambda: not w._writing())
    assert button_state(w.skip_button) == "normal"
    w.skip()
    assert not r.alive() and r.done() == "done"


def test_skip_on_welcome_writes_only_the_marker(rig):
    r = rig()
    r.walk.skip_button.invoke()
    assert not r.alive()
    assert r.done() == "done"
    assert not (r.state_dir / "accounts.json").exists()


def test_skip_during_discovery_drops_the_result(rig):
    r = rig()
    r.discover_gate = threading.Event()
    r.walk.primary_button.invoke()
    r.settle()
    assert r.walk.candidates is None
    assert button_state(r.walk.skip_button) == "normal"
    r.walk.skip_button.invoke()
    assert not r.alive() and r.done() == "done"
    r.discover_gate.set()
    r.settle()
    assert not (r.state_dir / "accounts.json").exists()


@pytest.mark.parametrize("platform, note", [
    ("darwin", "Checking this Mac"),
    ("win32", "Checking this PC and WSL installs"),
])
def test_the_search_note_only_mentions_wsl_on_windows(rig, platform, note):
    r = rig(platform=platform)
    r.discover_gate = threading.Event()
    r.walk.primary_button.invoke()
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
    r.walk.skip_button.invoke()
    assert not r.alive() and r.done() == "done"
    assert not (r.state_dir / "accounts.json").exists()
    assert r.saves == []


def test_skip_on_hooks_keeps_the_accounts_and_installs_nothing(rig):
    r = rig()
    r.to_hooks()
    r.walk.skip_button.invoke()
    assert not r.alive() and r.done() == "done"
    assert len(load_accounts_result(r.state_dir).accounts) == 3
    assert [c for c in r.calls if isinstance(c, tuple)] == []


def test_closing_the_window_is_skip(rig):
    r = rig()
    r.to_accounts()
    r.walk.toplevel.tk.call(r.walk.toplevel.protocol("WM_DELETE_WINDOW"))
    assert not r.alive() and r.done() == "done"
    assert not (r.state_dir / "accounts.json").exists()


def test_done_has_back_and_finish_but_no_skip(rig):
    r = rig()
    r.to_hooks()
    r.walk.primary_button.invoke()
    w = r.walk
    assert w.step == "done"
    assert w.skip_button is None
    assert w.back_button.cget("text") == "Back"
    assert w.primary_button.cget("text") == "Finish"
    assert "You're set" in r.texts()
    w.back_button.invoke()
    assert w.step == "hooks"


def test_going_back_after_saving_never_saves_again(rig):
    r = rig()
    r.to_hooks()
    r.walk.back_button.invoke()
    assert r.walk.step == "accounts"
    assert all(not b.enabled for b in r.walk.checks)
    assert first_run_ui.LOCKED_NOTE in r.texts()
    r.walk.primary_button.invoke()
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
    assert w.primary_button.cget("text") == "Next"
    w.primary_button.invoke()
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
    assert first_run_ui.LOCKED_NOTE in r.texts()
    assert w.checks and all(b is None for b in w.checks)
    w.primary_button.invoke()
    r.pump(lambda: w.snapshot is not None)
    assert [v["data"].name for v in w.hook_rows] == ["mine"]
    w.primary_button.invoke()
    w.primary_button.invoke()
    assert not r.alive()
    assert (r.state_dir / "accounts.json").read_text(encoding="utf-8") == original
    assert r.saves == []


def test_nothing_found_says_so_and_offers_skip(rig):
    r = rig(found=[])
    r.to_accounts()
    assert "No Claude Code or Codex installs found." in r.texts()
    assert r.walk.skip_button is not None
    assert r.walk.primary_button.cget("text") == "Continue"
    r.walk.primary_button.invoke()
    assert r.walk.step == "done" and not (r.state_dir / "accounts.json").exists()


def test_a_discovery_failure_is_shown_and_skippable(rig):
    def boom():
        raise RuntimeError("wsl.exe hung")

    r = rig(discover=boom)
    r.to_accounts()
    assert any("wsl.exe hung" in t for t in r.texts())
    r.walk.skip_button.invoke()
    assert r.done() == "done"


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
    r.pump(lambda: r.walk.hook_rows[0]["pill"].cget("text") == "Not supported")
    assert r.walk.hook_rows[0]["install"] is None


def test_done_points_mac_users_at_the_app_menu(rig):
    r = rig(platform="darwin")
    r.to_hooks()
    r.walk.primary_button.invoke()
    assert any("Tokitty ▸ Settings…" in t for t in r.texts())


def test_done_says_right_click_elsewhere(rig):
    r = rig(platform="win32")
    r.to_hooks()
    r.walk.primary_button.invoke()
    texts = r.texts()
    assert any("Right-click any cat" in t for t in texts)
    assert not any("Tokitty ▸ Settings…" in t for t in texts)
