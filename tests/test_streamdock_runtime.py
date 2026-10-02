import json

import threading

import pytest

from tokitty.accounts import Account
from tokitty.activity import SessionView
from tokitty.settings import Settings
from tokitty.streamdock.model import SessionRef
from tokitty.streamdock.pending import PendingRequest
from tokitty.streamdock.runtime import AccountInput, StreamdockRuntime
from tokitty.streamdock.server import DeckEvent
from tokitty.streamdock.wt_focus import FocusDone, InterruptDone, TitleDone, VerifyDone

A0 = SessionRef(0, "s0")
A1 = SessionRef(1, "s1")


class FakeServer:
    def __init__(self, inbound):
        self.inbound = inbound
        self.is_connected = False
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def connected(self):
        return self.is_connected


class FakeWatcher:
    def __init__(self):
        self.pending = []
        self.active = None
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def set_active(self, active):
        self.active = active

    def get_pending(self):
        return list(self.pending)


class FakeWorker:
    def __init__(self, on_result):
        self.on_result = on_result
        self.calls = []
        self.stopped = False

    def submit_focus(self, session, seq, config_dir):
        self.calls.append(("focus", session, seq, config_dir))

    def submit_verify(self, nonce, session):
        self.calls.append(("verify", nonce, session))

    def submit_interrupt(self, session):
        self.calls.append(("interrupt", session))

    def submit_title(self, session, config_dir, reachable=lambda: True):
        self.calls.append(("title", session, config_dir))
        self.reachable = reachable

    def forget(self, session):
        self.calls.append(("forget", session))

    def stop(self):
        self.stopped = True

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


def view(sid, first_seen=1.0):
    return SessionView(session_id=sid, state="idle", tool_label="", first_seen=first_seen, last_ts=first_seen)


def req(nonce, ref, **kw):
    return PendingRequest(
        nonce=nonce * 16 if len(nonce) == 1 else nonce,
        session_id=ref.session_id,
        tool_use_id="tu",
        tool_name="Bash",
        tool_input={"command": "ls"},
        digest="d",
        preview="ls",
        started=1.0,
        account_index=ref.account_index,
        **kw,
    )


class Harness:
    def __init__(self, tmp_path, **kw):
        self.tmp = tmp_path
        self.now = 100.0
        self.dirs = {i: tmp_path / f"acct{i}" for i in (0, 1)}
        accounts = [
            AccountInput(i, f"acct{i}", Account(f"acct{i}", f"/cfg{i}"), f"/cfg{i}", self.dirs[i]) for i in (0, 1)
        ]
        self.watchers = {}
        self.server = None
        self.opened = []
        self.reasons = []
        self.launched = []
        self.sessions = {0: [view("s0", 1.0)], 1: [view("s1", 2.0)]}
        self.usage = {}

        def server_factory(port, token, box, inbound, *, image_fn, meta_fn):
            self.server = FakeServer(inbound)
            self.meta_fn = meta_fn
            return self.server

        def watcher_factory(acct):
            self.watchers[acct.index] = FakeWatcher()
            return self.watchers[acct.index]

        self.worker = None

        def worker_factory(on_result):
            self.worker = FakeWorker(on_result)
            return self.worker

        opts = dict(
            palette_fn=lambda i: {},
            open_in_window=lambda r, reason: (self.opened.append(r), self.reasons.append(reason)),
            server_factory=server_factory,
            watcher_factory=watcher_factory,
            worker_factory=worker_factory,
            launch_fn=lambda preset, account: self.launched.append((preset["name"], account.name)),
            monotonic_fn=lambda: self.now,
            list_running_distros_fn=lambda: self.running,
            run_io=lambda job: job(),
        )
        self.running = []
        opts.update(kw)
        presets = [
            {"name": "p0", "account_index": 0, "env": "native", "cwd": "C:\\a"},
            {"name": "p1", "account_index": 1, "env": "native", "cwd": "C:\\b"},
        ]
        self.rt = StreamdockRuntime(4000, "tok", presets, accounts, **opts)
        self.rt.start()
        self.inbound = self.server.inbound

    def put(self, *items):
        for item in items:
            self.inbound.put(item)

    def key(self, ctx, role, col):
        self.put(DeckEvent("willAppear", ctx, "dev", 0, col, {"role": role}))

    def tick(self):
        self.rt.tick(self.sessions, self.usage)

    def press(self, ctx):
        self.put(DeckEvent("keyUp", ctx))
        self.tick()

    def spec(self, ctx):
        return self.rt._model.render_plan()[ctx]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def slots(h, n):
    for i in range(n):
        h.key(f"k{i}", "slot", i)
    h.tick()


def decisions(h, i):
    d = h.dirs[i] / "decisions"
    return sorted(json.loads(p.read_text()) for p in d.glob("*.json")) if d.exists() else []


def test_configured():
    assert StreamdockRuntime.configured(Settings(streamdock_port=4000, streamdock_token="t"))
    assert not StreamdockRuntime.configured(Settings(streamdock_port=0, streamdock_token="t"))
    assert not StreamdockRuntime.configured(Settings(streamdock_port=4000, streamdock_token=""))


def test_start_stop_lifecycle(h):
    assert h.server.started and all(w.started for w in h.watchers.values())
    h.rt.stop()
    assert h.server.stopped and h.worker.stopped and all(w.stopped for w in h.watchers.values())


def test_meta(h):
    assert h.meta_fn() == {
        "accounts": [{"index": 0, "name": "acct0"}, {"index": 1, "name": "acct1"}],
        "presets": ["p0", "p1"],
    }


def test_events_are_applied_only_inside_tick(h):
    h.key("k0", "slot", 0)
    assert h.rt._model.render_plan() == {}
    assert h.rt._box.get().plan == {}
    h.tick()
    assert "k0" in h.rt._model.render_plan()
    snap = h.rt._box.get()
    assert snap.revision == h.rt._model.revision and "k0" in snap.plan


def test_unchanged_plan_is_not_republished(h):
    slots(h, 1)
    rev = h.rt._box.get().revision
    h.tick()
    assert h.rt._box.get().revision == rev


def test_focus_result_and_press_apply_in_arrival_order(h):
    h.key("k0", "slot", 0)
    h.key("k1", "slot", 1)
    h.key("esc", "interrupt", 2)
    h.tick()
    h.press("k0")
    assert h.worker.of("focus") == [("focus", A0, 1, "/cfg0")]
    # Result first, then the Interrupt press: the press sees the confirmed tab.
    h.put(FocusDone(A0, 1, "focused"), DeckEvent("keyUp", "esc"))
    h.tick()
    assert h.worker.of("interrupt") == [("interrupt", A0)]
    assert h.spec("k0").focus == "focused"


def test_press_before_result_does_not_see_it(h):
    h.key("k0", "slot", 0)
    h.key("k1", "slot", 1)
    h.key("esc", "interrupt", 2)
    h.tick()
    h.press("k0")
    h.put(DeckEvent("keyUp", "esc"), FocusDone(A0, 1, "focused"))
    h.tick()
    assert h.worker.of("interrupt") == []
    assert h.spec("k0").focus == "focused"


def test_focus_result_uses_the_seq_it_carries(h):
    slots(h, 2)
    h.tick()
    h.press("k0")
    h.press("k0")
    assert [c[2] for c in h.worker.of("focus")] == [1, 2]
    h.put(FocusDone(A0, 1, "not_found"))
    h.tick()
    assert h.spec("k0").focus == ""
    h.put(FocusDone(A0, 2, "focused"))
    h.tick()
    assert h.spec("k0").focus == "focused"


def pending_slot_press(h, n_slots=3, **kw):
    r = req("a", A0, **kw)
    h.watchers[0].pending = [r]
    slots(h, n_slots)
    h.press("k0")
    return r


@pytest.mark.parametrize("status,opens", [("focused", False), ("not_found", True), ("ambiguous", True)])
def test_focus_and_open_falls_back_on_unconfirmed_tab(h, status, opens):
    r = pending_slot_press(h)
    assert h.opened == []
    h.put(FocusDone(A0, 1, status))
    h.tick()
    assert h.opened == ([r] if opens else [])


def test_focus_on_a_title_another_session_shares_is_ambiguous(h):
    h.put(TitleDone(A0, "Same"), TitleDone(A1, "Same"))
    r = pending_slot_press(h)
    h.put(FocusDone(A0, 1, "focused"))
    h.tick()
    assert h.opened == [r] and h.reasons == ["ambiguous"]
    assert h.rt.session_title(r) == "Same"
    assert h.spec("k1").armed is False


def test_focus_and_open_without_overlay_opens_at_once(h):
    h.sessions = {0: [view("s0")], 1: []}
    r = pending_slot_press(h, n_slots=1)
    assert h.opened == [r] and h.reasons == ["few_keys"]
    assert h.worker.of("focus")


def test_stale_focus_result_does_not_open(h):
    pending_slot_press(h)
    h.put(FocusDone(A0, 99, "not_found"))
    h.tick()
    assert h.opened == []


def test_window_already_open_is_not_reopened(h):
    r = pending_slot_press(h)
    h.rt.in_window_opened(r.nonce)
    h.put(FocusDone(A0, 1, "not_found"))
    h.tick()
    assert h.opened == []


def test_window_open_arms_allow_and_decide_from_window(h):
    r = pending_slot_press(h)
    h.rt.in_window_opened(r.nonce)
    h.tick()
    assert h.spec("k1").armed
    h.rt.in_window_closed(r.nonce)
    h.tick()
    assert not h.spec("k1").armed
    assert h.rt.decide_from_window(r.nonce, "allow")
    assert decisions(h, 0)[0]["behavior"] == "allow"
    assert not h.rt.decide_from_window("f" * 16, "allow")


def pressed_overlay(h, ref, n=4, **kw):
    r = req("c", ref, **kw)
    h.watchers[ref.account_index].pending = [r]
    slots(h, n)
    slot = f"k{ref.account_index}"
    h.press(slot)
    others = [f"k{i}" for i in range(n) if f"k{i}" != slot]
    return r, others


def test_deny_writes_immediately_to_the_right_dir(h):
    r, others = pressed_overlay(h, A1)
    h.press(others[1])
    assert decisions(h, 0) == []
    [d] = decisions(h, 1)
    assert d["behavior"] == "deny" and d["nonce"] == r.nonce


def test_allow_writes_only_after_verify_ok(h):
    r, others = pressed_overlay(h, A1)
    h.put(FocusDone(A1, 1, "focused"))
    h.tick()
    h.press(others[0])
    assert h.worker.of("verify") == [("verify", r.nonce, A1)]
    assert decisions(h, 1) == []
    h.put(VerifyDone(r.nonce, True))
    h.tick()
    [d] = decisions(h, 1)
    assert d["behavior"] == "allow"
    assert decisions(h, 0) == []


def test_allow_writes_nothing_when_verify_fails(h):
    r, others = pressed_overlay(h, A1)
    h.put(FocusDone(A1, 1, "focused"))
    h.tick()
    h.press(others[0])
    h.put(VerifyDone(r.nonce, False))
    h.tick()
    assert decisions(h, 1) == []
    assert h.spec(others[0]).armed is False


def test_always_passes_through(h):
    r, others = pressed_overlay(h, A0, always_rule="ls")
    h.put(FocusDone(A0, 1, "focused"))
    h.tick()
    h.press(others[2])
    h.put(VerifyDone(r.nonce, True))
    h.tick()
    [d] = decisions(h, 0)
    assert d["behavior"] == "always"


def test_deny_while_always_verifies_is_not_overwritten(h):
    r, others = pressed_overlay(h, A0, always_rule="ls")
    h.put(FocusDone(A0, 1, "focused"))
    h.tick()
    h.press(others[2])  # Always: waits for the verify
    h.press(others[1])  # Deny: written at once
    h.put(VerifyDone(r.nonce, True))
    h.tick()
    [d] = decisions(h, 0)
    assert d["behavior"] == "deny"


def test_window_decision_wins_over_a_later_verify(h):
    r, others = pressed_overlay(h, A0)
    h.put(FocusDone(A0, 1, "focused"))
    h.tick()
    h.press(others[0])
    assert h.rt.decide_from_window(r.nonce, "deny")
    h.put(VerifyDone(r.nonce, True))
    h.tick()
    [d] = decisions(h, 0)
    assert d["behavior"] == "deny"


def test_interrupt_and_unverified_verify_for_unknown_nonce(h):
    h.put(VerifyDone("z" * 16, True), InterruptDone(A0, True))
    h.tick()
    assert decisions(h, 0) == [] and decisions(h, 1) == []


def test_new_session_repeat_guard(h):
    h.key("n0", "new:p0", 0)
    h.key("n1", "new:p1", 1)
    h.tick()
    h.press("n0")
    h.now += 0.5
    h.press("n0")
    assert h.launched == [("p0", "acct0")]
    h.press("n1")
    assert h.launched == [("p0", "acct0"), ("p1", "acct1")]
    h.now += 0.6
    h.press("n0")
    assert h.launched == [("p0", "acct0"), ("p1", "acct1"), ("p0", "acct0")]


def test_new_session_with_unknown_preset_is_ignored(h, capsys):
    h.key("n", "new:nope", 0)
    h.tick()
    h.press("n")
    assert h.launched == []
    assert "nope" in capsys.readouterr().err


def test_new_session_resolves_account_by_name_not_index(h):
    h.rt.set_presets([{"name": "p", "account": "acct1", "account_index": 0, "env": "native", "cwd": "C:\\a"}])
    h.key("n", "new:p", 0)
    h.tick()
    h.press("n")
    assert h.launched == [("p", "acct1")]


def test_new_session_refuses_unknown_account_name(h, capsys):
    h.rt.set_presets([{"name": "p", "account": "gone", "account_index": 0, "env": "native", "cwd": "C:\\a"}])
    h.key("n", "new:p", 0)
    h.tick()
    h.press("n")
    assert h.launched == []
    assert "unknown account" in capsys.readouterr().err


def test_new_session_without_account_name_falls_back_to_index(h):
    h.rt.set_presets([{"name": "p", "account_index": 1, "env": "native", "cwd": "C:\\a"}])
    h.key("n", "new:p", 0)
    h.tick()
    h.press("n")
    assert h.launched == [("p", "acct1")]


def test_set_presets_changes_meta_and_launching(h):
    h.rt.set_presets([{"name": "fresh", "account": "acct0", "account_index": 0, "env": "native", "cwd": "C:\\a"}])
    assert h.meta_fn()["presets"] == ["fresh"]
    h.key("n", "new:fresh", 0)
    h.tick()
    h.press("n")
    assert h.launched == [("fresh", "acct0")]


def marker(h, i):
    return h.dirs[i] / "streamdock.enabled"


def wsl_harness(tmp_path, **kw):
    h = Harness(tmp_path, **kw)
    a = h.rt._accounts[1]
    h.rt._accounts[1] = AccountInput(a.index, a.name, a.account, a.config_dir, a.tokitty_dir, "Ubuntu")
    return h


def test_stopped_distro_marker_is_never_touched_or_cleared(tmp_path):
    h = wsl_harness(tmp_path)
    h.server.is_connected = True
    h.tick()
    assert marker(h, 0).exists() and not marker(h, 1).exists()
    h.running = ["Ubuntu"]
    h.now += 30
    h.tick()
    assert marker(h, 1).exists()
    h.running = []
    h.server.is_connected = False
    h.tick()
    h.rt.stop()
    assert not marker(h, 0).exists() and marker(h, 1).exists()


def test_codex_account_never_gets_the_enabled_marker(tmp_path):
    h = Harness(tmp_path)
    a = h.rt._accounts[1]
    codex = Account("codex", "C:\\codex", provider="codex")
    h.rt._accounts[1] = AccountInput(a.index, a.name, codex, a.config_dir, a.tokitty_dir)
    h.server.is_connected = True
    h.tick()
    assert marker(h, 0).exists() and not marker(h, 1).exists()


def test_title_job_checks_the_distro_on_the_worker(tmp_path):
    h = wsl_harness(tmp_path)
    h.tick()
    assert h.worker.reachable() is False
    h.running = ["Ubuntu"]
    assert h.worker.reachable() is True


def test_markers_go_through_the_io_thread_by_default(tmp_path):
    calls = []
    h = Harness(
        tmp_path,
        run_io=None,
        touch_enabled_fn=lambda d: calls.append(("touch", threading.current_thread().name)),
        clear_enabled_fn=lambda d: calls.append(("clear", threading.current_thread().name)),
    )
    h.server.is_connected = True
    h.tick()
    h.rt.stop()
    assert h.rt._io_thread is None
    assert calls == [("touch", "streamdock-io")] * 2 + [("clear", "streamdock-io")] * 2


def test_heartbeat_connect_refresh_and_disconnect(h):
    h.tick()
    assert not marker(h, 0).exists()
    assert all(w.active is None for w in h.watchers.values())
    h.server.is_connected = True
    h.tick()
    assert marker(h, 0).exists() and marker(h, 1).exists()
    assert all(w.active is True for w in h.watchers.values())
    marker(h, 0).unlink()
    h.now += 29
    h.tick()
    assert not marker(h, 0).exists()
    h.now += 1
    h.tick()
    assert marker(h, 0).exists() and marker(h, 1).exists()
    assert h.rt.connected
    h.server.is_connected = False
    h.tick()
    assert not marker(h, 0).exists() and not marker(h, 1).exists()
    assert all(w.active is False for w in h.watchers.values())
    assert not h.rt.connected


def test_stop_clears_markers_and_never_raises(h):
    h.server.is_connected = True
    h.tick()
    assert marker(h, 0).exists()
    h.server.stop = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    h.rt.stop()
    assert not marker(h, 0).exists() and not marker(h, 1).exists()
    assert h.worker.stopped


def test_failing_launch_does_not_break_tick(tmp_path, capsys):
    def boom(preset, account):
        raise OSError("no wt")

    h = Harness(tmp_path, launch_fn=boom)
    h.key("n0", "new:p0", 0)
    h.key("k0", "slot", 1)
    h.tick()
    h.press("n0")
    assert "no wt" in capsys.readouterr().err
    h.press("k0")
    assert h.worker.of("focus")


def test_failing_decision_write_does_not_break_tick(tmp_path, capsys):
    def boom(directory, request, behavior, **kw):
        raise OSError("disk full")

    h = Harness(tmp_path, write_decision_fn=boom)
    r, others = pressed_overlay(h, A0)
    h.press(others[1])
    assert "disk full" in capsys.readouterr().err
    assert not h.rt.decide_from_window(r.nonce, "deny")
    h.press("k0")
    h.tick()


def test_titles_are_fetched_off_thread_and_throttled(h):
    h.key("k0", "slot", 0)
    h.tick()
    assert {c[1] for c in h.worker.of("title")} == {A0, A1}
    h.tick()
    assert len(h.worker.of("title")) == 2  # still in flight
    h.put(TitleDone(A0, "Fix the build"), TitleDone(A1, None))
    h.tick()
    assert h.spec("k0").title == "Fix the build"
    h.now += 9
    h.tick()
    assert len(h.worker.of("title")) == 2
    h.now += 1
    h.tick()
    assert len(h.worker.of("title")) == 4
    h.put(TitleDone(A0, None))
    h.tick()
    assert h.spec("k0").title == "Fix the build"  # a failed read keeps the last title


def test_ended_session_is_forgotten(h):
    h.tick()
    h.sessions = {0: [view("s0")], 1: []}
    h.tick()
    assert h.worker.of("forget") == [("forget", A1)]


def test_callable_config_dir_is_resolved_at_use(tmp_path):
    h = Harness(tmp_path)
    h.rt._accounts[0] = AccountInput(0, "acct0", None, lambda: "/late", h.dirs[0])
    assert h.rt._config_dir(0) == "/late"
    h.rt._accounts[0] = AccountInput(0, "acct0", None, lambda: None, h.dirs[0])
    assert h.rt._config_dir(0) == ""


def test_pending_nonces(h):
    assert h.rt.pending_nonces() == set()
    h.watchers[0].pending = [req("n1", A0)]
    h.tick()
    assert h.rt.pending_nonces() == {"n1"}


def test_hidden_account_sessions_and_pending_never_reach_the_deck(tmp_path):
    h = Harness(tmp_path, hidden_accounts=["acct1"])
    slots(h, 3)
    assert h.spec("k0").ref == A0
    assert h.spec("k1").kind == "status"
    h.watchers[1].pending = [req("b", A1)]
    h.watchers[0].pending = [req("a", A0)]
    h.tick()
    assert h.spec("k0").pending and h.spec("k1").kind == "status"
    # No titles are asked for a hidden account either.
    assert {c[1] for c in h.worker.of("title")} == {A0}


def test_set_hidden_accounts_applies_live(h):
    slots(h, 3)
    assert [h.spec(f"k{i}").ref for i in range(2)] == [A0, A1]
    h.rt.set_hidden_accounts(["acct0"])
    h.tick()
    assert [h.spec("k0").kind, h.spec("k1").ref] == ["status", A1]
    h.rt.set_hidden_accounts([])
    h.tick()
    assert {h.spec("k0").ref, h.spec("k1").ref} == {A0, A1}


def test_default_unit_cannot_be_hidden(tmp_path):
    h = Harness(tmp_path, hidden_accounts=["Claude"])
    h.rt._accounts[0] = AccountInput(0, "Claude", None, "/cfg0", h.dirs[0])
    slots(h, 3)
    assert [h.spec("k0").ref, h.spec("k1").ref] == [A0, A1]
