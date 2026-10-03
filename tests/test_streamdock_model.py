import pytest
import threading
import time

from tokitty.activity import SessionView
from tokitty.streamdock.model import (
    CancelOverlay,
    Decide,
    DeckModel,
    Focus,
    FocusAndOpen,
    Interrupt,
    KeySpec,
    NewSession,
    Noop,
    PlanBox,
    SessionRef,
    VerifyThenDecide,
)
from tokitty.streamdock.pending import PendingRequest


def sv(sid, first_seen, state="idle", tool=""):
    return SessionView(sid, state, tool, first_seen, first_seen)


def req(sid, nonce="n1", started=1.0, acct=0, always_rule=None):
    return PendingRequest(
        nonce, sid, "tu", "Bash", {}, "d", "rm -rf x", started, account_index=acct, always_rule=always_rule
    )


def ref(sid, acct=0):
    return SessionRef(acct, sid)


def coords(r, c):
    return {"row": r, "column": c}


def setup(roles, sessions=(), pending=(), usage=None):
    """roles: list of role strings placed on row 0, columns 0..n-1; contexts k0..kn."""
    m = DeckModel()
    for i, role in enumerate(roles):
        # A (role, prompt) pair sets the key's "During a permission prompt" choice too.
        role, prompt = role if isinstance(role, tuple) else (role, "")
        m.appear(f"k{i}", coords(0, i), "dev", {"role": role, "prompt": prompt})
    m.update({0: list(sessions)}, list(pending), usage or {})
    return m


def feed(m, sessions, pending=(), usage=None, titles=None):
    m.update({0: list(sessions)}, list(pending), usage or {}, titles=titles)


def focus(m, name, status="focused"):
    """Deliver a focus worker result for the session's latest press."""
    r = ref(name)
    m.focus_result(r, status, m._focus_seq.get(r, 0))


def test_slots_follow_first_seen_and_stay_put():
    m = setup(["slot"] * 3, [sv("a", 1), sv("b", 2)])
    plan = m.render_plan()
    assert plan["k0"].ref == ref("a") and plan["k1"].ref == ref("b")
    assert plan["k2"].kind == "status" and plan["k2"].text == ""
    # a ends: b keeps its slot, a newcomer takes the lowest free one.
    feed(m, [sv("b", 2), sv("c", 3)])
    plan = m.render_plan()
    assert plan["k0"].ref == ref("c") and plan["k1"].ref == ref("b")


def test_slot_stable_across_page_switch():
    m = setup(["slot"] * 3, [sv("a", 1), sv("b", 2)])
    for c in ("k0", "k1", "k2", "k0", "k1", "k2"):
        m.disappear(c)
    for i in range(3):
        m.appear(f"k{i}", coords(0, i), "dev", {"role": "slot"})
    plan = m.render_plan()
    assert plan["k0"].ref == ref("a") and plan["k1"].ref == ref("b")


def test_same_session_id_on_two_accounts_is_two_sessions():
    m = DeckModel()
    for i in range(2):
        m.appear(f"k{i}", coords(0, i), "dev", {"role": "slot"})
    m.update({0: [sv("x", 1)], 1: [sv("x", 2)]}, [], {})
    plan = m.render_plan()
    assert plan["k0"].ref == ref("x", 0) and plan["k1"].ref == ref("x", 1)


def test_slots_ordered_by_row_then_column():
    m = DeckModel()
    m.appear("low", coords(1, 0), "dev", {"role": "slot"})
    m.appear("high", coords(0, 4), "dev", {"role": "slot"})
    m.update({0: [sv("a", 1), sv("b", 2)]}, [], {})
    plan = m.render_plan()
    assert plan["high"].ref == ref("a") and plan["low"].ref == ref("b")


def test_overflow_shows_count_and_cycles():
    m = setup(["slot"] * 3, [sv("a", 1), sv("b", 2), sv("c", 3), sv("d", 4)])
    plan = m.render_plan()
    assert plan["k0"].ref == ref("a") and plan["k1"].ref == ref("b")
    assert plan["k2"].kind == "overflow" and plan["k2"].count == 2 and plan["k2"].ref == ref("c")
    assert m.press("k2") == Focus(ref("d"), 1)
    assert m.render_plan()["k2"].ref == ref("d")
    assert m.press("k2") == Focus(ref("c"), 1)


def test_overflow_alert_and_pending_first():
    m = setup(["slot"] * 2, [sv("a", 1), sv("b", 2), sv("c", 3)], [req("c")])
    spec = m.render_plan()["k1"]
    assert spec.kind == "overflow" and spec.alert and spec.ref == ref("c") and spec.pending


def test_overflow_session_keeps_index_when_keys_disappear():
    m = setup(["slot"] * 3, [sv("a", 1), sv("b", 2), sv("c", 3)])
    m.disappear("k2")
    m.disappear("k2")
    plan = m.render_plan()
    assert plan["k1"].kind == "overflow" and plan["k1"].count == 2
    m.appear("k2", coords(0, 2), "dev", {"role": "slot"})
    assert m.render_plan()["k2"].ref == ref("c")


def test_overflow_with_single_slot_key():
    m = setup(["slot"], [sv("a", 1), sv("b", 2)])
    spec = m.render_plan()["k0"]
    assert spec.kind == "overflow" and spec.count == 2


def test_duplicate_appear_and_disappear_are_idempotent():
    m = setup(["slot"], [sv("a", 1)])
    rev = m.revision
    m.appear("k0", coords(0, 0), "dev", {"role": "slot"})
    assert m.revision == rev
    m.disappear("k0")
    r2 = m.revision
    m.disappear("k0")
    assert m.revision == r2
    m.disappear("never-seen")
    assert m.render_plan() == {}


def test_disappear_before_appear_then_appear():
    m = DeckModel()
    m.disappear("k0")
    m.appear("k0", coords(0, 0), "dev", {"role": "slot"})
    assert m.render_plan()["k0"].kind == "status"


def test_bad_settings_make_status_key_that_never_takes_part():
    m = DeckModel()
    m.appear("bad", coords(0, 0), "dev", {})
    m.appear("bad2", coords(0, 1), "dev", {"role": "usage:abc"})
    m.appear("s", coords(0, 2), "dev", {"role": "slot"})
    m.appear("t", coords(0, 3), "dev", {"role": "slot"})
    m.appear("u", coords(0, 4), "dev", {"role": "slot"})
    m.update({0: [sv("a", 1)]}, [req("a")], {})
    assert m.render_plan()["bad"].kind == "status" and m.render_plan()["bad2"].kind == "status"
    assert isinstance(m.press("s"), FocusAndOpen)
    plan = m.render_plan()
    assert plan["bad"].kind == "status" and plan["bad2"].kind == "status"
    assert m.press("bad") == Noop("no role")


def test_usage_and_new_and_missing_account():
    m = setup(["usage:0", "usage:1", "new:work"], usage={0: (42.0, 10.0, True)})
    plan = m.render_plan()
    assert plan["k0"].kind == "usage" and plan["k0"].session_pct == 42.0 and plan["k0"].warn
    assert plan["k1"].kind == "status" and plan["k1"].account == 1
    assert plan["k2"].kind == "new" and plan["k2"].text == "work"
    assert isinstance(m.press("k0"), Noop)
    assert m.press("k2") == NewSession("work")
    assert m.press("nope") == Noop("unknown key")


def test_titles_flow_into_slot_spec():
    m = setup(["slot"], [sv("a", 1)])
    feed(m, [sv("a", 1)], titles={ref("a"): "Fix the thing"})
    assert m.render_plan()["k0"].title == "Fix the thing"


def test_slot_press_without_request_is_focus():
    m = setup(["slot"], [sv("a", 1)])
    assert m.press("k0") == Focus(ref("a"), 1)
    assert m.press("nope") == Noop("unknown key")


def test_empty_slot_press_is_noop():
    m = setup(["slot", "slot"], [sv("a", 1)])
    assert m.press("k1") == Noop("empty slot")


# overlay

def overlay_model(extra=("slot", "slot", "usage:0", "new:x")):
    return setup(["slot", *extra], [sv("a", 1), sv("b", 2)], [req("a")], usage={0: (1, 2, False)})


def test_overlay_entry_layout_and_untouched_keys():
    m = overlay_model()
    r = req("a")
    assert m.press("k0") == FocusAndOpen(ref("a"), r, True, 1)
    plan = m.render_plan()
    assert plan["k0"].kind == "decision" and plan["k0"].decision == "cancel"
    assert plan["k1"].decision == "allow" and plan["k2"].decision == "deny"
    assert plan["k3"].kind == "usage" and plan["k4"].kind == "new"
    assert m.render_plan()["k3"].session_pct == 1


def test_overlay_preview_keys_fill_remaining_participants():
    m = setup(["slot"] * 6, [sv("a", 1)], [req("a")])
    assert m.press("k0").overlay is True
    plan = m.render_plan()
    previews = [plan[f"k{i}"] for i in range(3, 6)]
    assert [p.kind for p in previews] == ["preview"] * 3
    assert [p.index for p in previews] == [0, 1, 2] and all(p.total == 3 for p in previews)
    assert previews[0].text == "rm -rf x"
    assert isinstance(m.press("k3"), Noop)


def test_interrupt_key_takes_part_in_overlay():
    m = setup(["slot", "interrupt", "slot"], [sv("a", 1)], [req("a")])
    assert m.press("k0").overlay is True
    plan = m.render_plan()
    assert plan["k1"].decision == "allow" and plan["k2"].decision == "deny"


def test_too_few_participants_falls_back_to_window():
    m = setup(["slot", "slot", "usage:0", "new:x"], [sv("a", 1)], [req("a")])
    before = m.render_plan()
    act = m.press("k0")
    assert isinstance(act, FocusAndOpen) and act.overlay is False
    assert m.render_plan() == before


def test_overlay_exit_on_request_gone():
    m = overlay_model()
    m.press("k0")
    feed(m, [sv("a", 1), sv("b", 2)], [])
    assert m.render_plan()["k0"].kind == "slot"
    assert m.press("k1") == Focus(ref("b"), 1)


def test_overlay_exit_on_cancel_and_second_press():
    m = overlay_model()
    m.press("k0")
    assert m.press("k0") == CancelOverlay()
    assert m.render_plan()["k0"].kind == "slot"
    m.press("k0")
    m.press("k0")
    assert m.render_plan()["k1"].kind == "slot"


def test_overlay_exit_when_pressed_key_disappears():
    m = overlay_model()
    m.press("k0")
    m.disappear("k0")
    assert m.render_plan()["k1"].kind == "slot"


def test_overlay_exit_when_participants_drop_below_two():
    m = setup(["slot", "slot", "slot"], [sv("a", 1)], [req("a")])
    m.press("k0")
    m.disappear("k1")
    assert m.render_plan()["k2"].kind != "decision"


def test_overlay_on_overflow_key_and_exit_second_press():
    m = setup(["slot"] * 4, [sv(s, i) for i, s in enumerate("abcd")], [req("d")])
    act = m.press("k3")
    assert isinstance(act, FocusAndOpen) and act.session == ref("d") and act.overlay
    assert m.press("k3") == CancelOverlay()


def test_oldest_request_wins_for_a_session():
    m = setup(["slot", "slot", "slot"], [sv("a", 1)], [req("a", "late", 5.0), req("a", "early", 1.0)])
    assert m.press("k0").request.nonce == "early"


def test_pending_for_unknown_session_ignored():
    m = setup(["slot"], [sv("a", 1)], [req("zzz")])
    assert m.press("k0") == Focus(ref("a"), 1)


# arming

def test_unarmed_allow_greyed_and_noop_deny_live():
    m = overlay_model()
    m.press("k0")
    plan = m.render_plan()
    assert plan["k1"].armed is False and plan["k2"].armed is True
    assert m.press("k1") == Noop("request not visible")
    assert m.press("k2") == Decide(req("a"), "deny")


def test_armed_by_focus_requires_verify():
    m = overlay_model()
    m.press("k0")
    focus(m, "a")
    assert m.render_plan()["k1"].armed is True
    act = m.press("k1")
    assert act == VerifyThenDecide(req("a"), "allow")
    assert m.render_plan()["k1"].decision == "allow"


def test_verify_ok_marks_sent():
    m = overlay_model()
    m.press("k0")
    focus(m, "a")
    m.press("k1")
    m.verify_result("n1", True)
    plan = m.render_plan()
    assert plan["k1"].decision == "sent" and plan["k2"].decision == "sent"
    assert m.press("k1") == Noop("already sent")
    assert m.press("k2") == Noop("already sent")


def test_manual_tab_switch_disarms_allow():
    m = overlay_model()
    m.press("k0")
    focus(m, "a")
    assert isinstance(m.press("k1"), VerifyThenDecide)
    m.verify_result("n1", False)
    assert m.render_plan()["k1"].armed is False
    assert m.press("k1") == Noop("request not visible")
    # An unchanged update keeps it disarmed; only a fresh focus result rearms.
    feed(m, [sv("a", 1), sv("b", 2)], [req("a")])
    assert m.render_plan()["k1"].armed is False
    assert m.press("k1") == Noop("request not visible")
    focus(m, "a")
    assert m.render_plan()["k1"].armed is True


def test_in_window_arms_and_decides_directly():
    m = overlay_model()
    m.press("k0")
    m.set_in_window("n1", True)
    assert m.press("k1") == Decide(req("a"), "allow")
    assert m.render_plan()["k1"].decision == "sent"


def test_in_window_preferred_over_focus():
    m = overlay_model()
    m.press("k0")
    focus(m, "a")
    m.set_in_window("n1", True)
    assert m.press("k1") == Decide(req("a"), "allow")


def test_in_window_cleared_and_dropped_with_request():
    m = overlay_model()
    m.press("k0")
    m.set_in_window("n1", True)
    m.set_in_window("n1", False)
    assert m.press("k1") == Noop("request not visible")
    m.set_in_window("n1", True)
    feed(m, [sv("a", 1), sv("b", 2)], [])
    feed(m, [sv("a", 1), sv("b", 2)], [req("a")])
    m.press("k0")
    assert m.press("k1") == Noop("request not visible")


def test_sent_dropped_when_request_leaves():
    m = overlay_model()
    m.press("k0")
    assert m.press("k2") == Decide(req("a"), "deny")
    assert m.press("k1") == Noop("already sent")
    feed(m, [sv("a", 1), sv("b", 2)], [])
    feed(m, [sv("a", 1), sv("b", 2)], [req("a")])
    assert isinstance(m.press("k0"), FocusAndOpen)
    assert m.render_plan()["k2"].decision == "deny"


def test_always_appears_only_with_narrow_rule():
    m = overlay_model()
    m.press("k0")
    assert all(s.decision != "always" for s in m.render_plan().values())
    # With an always_rule the third participant becomes Always.
    r = req("a", always_rule="Bash(x)")
    m2 = setup(["slot"] * 5, [sv("a", 1)], [r])
    m2.press("k0")
    focus(m2, "a")
    assert m2.render_plan()["k3"].decision == "always"
    assert m2.render_plan()["k4"].kind == "preview"
    assert m2.press("k3") == VerifyThenDecide(r, "always")


# interrupt

def test_interrupt_gating():
    m = setup(["slot", "interrupt"], [sv("a", 1)])
    assert m.press("k1") == Noop("session tab not confirmed")
    assert m.render_plan()["k1"].armed is False
    m.press("k0")
    assert m.press("k1") == Noop("session tab not confirmed")
    focus(m, "a")
    assert m.render_plan()["k1"].armed is True
    assert m.press("k1") == Interrupt(ref("a"))
    focus(m, "a", "ambiguous")
    assert m.press("k1") == Noop("session tab not confirmed")


def test_interrupt_follows_last_pressed_slot_and_clears_on_end():
    m = setup(["slot", "slot", "interrupt"], [sv("a", 1), sv("b", 2)])
    m.press("k0")
    focus(m, "a")
    m.press("k1")
    focus(m, "b")
    assert m.press("k2") == Interrupt(ref("b"))
    feed(m, [sv("a", 1)])
    assert m.press("k2") == Noop("session tab not confirmed")


# revision

def test_revision_bumps_only_on_change():
    m = setup(["slot", "usage:0"], [sv("a", 1)], usage={0: (1, 2, False)})
    rev = m.revision
    feed(m, [sv("a", 1)], usage={0: (1, 2, False)})
    assert m.revision == rev
    feed(m, [sv("a", 1, "working", "Bash")], usage={0: (1, 2, False)})
    assert m.revision == rev + 1
    feed(m, [sv("a", 1, "working", "Bash")], usage={0: (1, 3, False)})
    assert m.revision == rev + 2


def test_unknown_role_and_equal_inputs_equal_specs():
    a = setup(["slot"], [sv("a", 1)]).render_plan()
    b = setup(["slot"], [sv("a", 1)]).render_plan()
    assert a == b
    assert hash(a["k0"]) == hash(b["k0"])


# plan box

def test_planbox_get_and_immutable():
    box = PlanBox()
    assert box.get().revision == 0
    src = {"k": setup(["slot"]).render_plan().get("k")}
    box.publish(3, src)
    src["x"] = None
    snap = box.get()
    assert snap.revision == 3 and "x" not in snap.plan
    try:
        snap.plan["x"] = None
    except TypeError:
        pass
    else:
        raise AssertionError("plan must be immutable")


def test_planbox_wait_times_out_when_unchanged():
    box = PlanBox()
    t0 = time.monotonic()
    snap = box.wait_for_change(0, 0.05)
    assert snap.revision == 0 and time.monotonic() - t0 < 0.5


def test_planbox_wait_returns_immediately_if_already_newer():
    box = PlanBox()
    box.publish(2, {})
    assert box.wait_for_change(0, 0.5).revision == 2


def test_planbox_wakes_waiter_across_threads():
    box = PlanBox()
    out = []
    t = threading.Thread(target=lambda: out.append(box.wait_for_change(0, 0.5)))
    t.start()
    time.sleep(0.05)
    box.publish(1, {})
    t.join(1)
    assert out and out[0].revision == 1


# model-owned focus status

def test_slot_press_clears_prior_focused():
    m = setup(["slot", "interrupt"], [sv("a", 1)])
    m.press("k0")
    focus(m, "a")
    assert m.render_plan()["k1"].armed is True
    act = m.press("k0")
    assert act == Focus(ref("a"), 2)
    assert m.render_plan()["k1"].armed is False
    assert m.press("k1") == Noop("session tab not confirmed")


def test_stale_seq_focus_result_ignored():
    m = setup(["slot", "interrupt"], [sv("a", 1)])
    first = m.press("k0")
    second = m.press("k0")
    m.focus_result(ref("a"), "focused", first.seq)
    assert m.render_plan()["k1"].armed is False
    m.focus_result(ref("a"), "focused", second.seq)
    assert m.render_plan()["k1"].armed is True


def test_focus_entries_dropped_for_ended_sessions():
    m = setup(["slot", "interrupt"], [sv("a", 1)])
    m.press("k0")
    focus(m, "a")
    feed(m, [])
    feed(m, [sv("a", 1)])
    assert ref("a") not in m._focus
    m.press("k0")
    assert m.render_plan()["k1"].armed is False


def test_two_pending_in_one_session_never_open_overlay():
    m = setup(["slot", "slot", "slot"], [sv("a", 1)], [req("a", "n1", 1), req("a", "n2", 2)])
    act = m.press("k0")
    assert isinstance(act, FocusAndOpen) and act.overlay is False and act.request.nonce == "n1"
    assert m.render_plan()["k1"].kind == "status"


def test_two_pending_tab_focus_does_not_arm_allow():
    m = setup(["slot", "slot", "slot"], [sv("a", 1)], [req("a", "n1", 1)])
    m.press("k0")
    focus(m, "a")
    assert m.render_plan()["k1"].armed is True
    feed(m, [sv("a", 1)], [req("a", "n1", 1), req("a", "n2", 2)])
    plan = m.render_plan()
    assert plan["k1"].armed is False and plan["k2"].armed is True
    assert m.press("k1") == Noop("request not visible")
    m.set_in_window("n2", True)
    assert m.render_plan()["k1"].armed is False
    assert m.press("k1") == Noop("request not visible")
    m.set_in_window("n1", True)
    assert m.render_plan()["k1"].armed is True
    assert m.press("k1") == Decide(req("a", "n1", 1), "allow")


def test_one_pending_still_armed_by_focus():
    m = setup(["slot", "slot", "slot"], [sv("a", 1)], [req("a", "n1", 1)])
    act = m.press("k0")
    assert act.overlay is True
    focus(m, "a")
    assert m.press("k1") == VerifyThenDecide(req("a", "n1", 1), "allow")


# prompt settings

def test_blank_role_shows_empty_look_and_does_nothing():
    m = setup(["blank", "slot"], [sv("a", 1)])
    assert m.render_plan()["k0"] == KeySpec("status")
    assert m.press("k0") == Noop("blank key")


def test_prompt_setting_ignored_outside_the_overlay():
    m = setup([("slot", "allow"), ("usage:0", "deny"), ("blank", "always")], [sv("a", 1)], usage={0: (1, 2, False)})
    plan = m.render_plan()
    assert [plan[f"k{i}"].kind for i in range(3)] == ["slot", "usage", "status"]
    assert isinstance(m.press("k0"), Focus)
    assert m.press("k1") == Noop("usage key")
    assert m.press("k2") == Noop("blank key")


def test_unknown_prompt_value_means_none():
    m = DeckModel()
    m.appear("k0", coords(0, 0), "dev", {"role": "slot", "prompt": "bogus"})
    m.appear("k1", coords(0, 1), "dev", {"role": "slot", "prompt": 7})
    m.update({0: [sv("a", 1)]}, [req("a")], {})
    # Neither value counts, so only one participant is left and no overlay opens.
    assert m.press("k0").overlay is False


def test_usage_key_with_deny_prompt_acts_as_deny_then_as_usage_again():
    r = req("a")
    m = setup(["slot", "slot", ("usage:0", "deny")], [sv("a", 1)], [r], usage={0: (1, 2, False)})
    assert m.press("k0") == FocusAndOpen(ref("a"), r, True, 1)
    plan = m.render_plan()
    assert plan["k2"].kind == "decision" and plan["k2"].decision == "deny"
    assert plan["k1"].decision == "allow"
    assert m.press("k2") == Decide(r, "deny")
    m.press("k0")
    assert m.render_plan()["k2"].kind == "usage"
    assert m.press("k2") == Noop("usage key")


def test_slot_key_with_allow_prompt_takes_allow():
    r = req("a")
    m = setup(["slot", "slot", ("slot", "allow"), "slot"], [sv("a", 1)], [r])
    assert m.press("k0").overlay is True
    plan = m.render_plan()
    assert plan["k2"].decision == "allow"
    assert plan["k1"].decision == "deny"
    assert plan["k3"].kind == "preview"
    focus(m, "a")
    assert m.press("k2") == VerifyThenDecide(r, "allow")


def test_blank_key_with_prompt_opens_overlay_without_slot_participants():
    r = req("a")
    m = setup(["slot", ("blank", "allow"), ("blank", "deny")], [sv("a", 1)], [r])
    assert m.press("k0") == FocusAndOpen(ref("a"), r, True, 1)
    plan = m.render_plan()
    assert plan["k0"].decision == "cancel"
    assert plan["k1"].decision == "allow" and plan["k2"].decision == "deny"
    assert m.press("k2") == Decide(r, "deny")
    assert m.render_plan()["k1"].decision == "sent"


def test_pressed_key_with_prompt_stays_cancel_and_decision_falls_back():
    m = setup([("slot", "allow"), "slot", "slot"], [sv("a", 1)], [req("a")])
    assert m.press("k0").overlay is True
    plan = m.render_plan()
    assert plan["k0"].decision == "cancel"
    assert plan["k1"].decision == "allow" and plan["k2"].decision == "deny"
    assert m.press("k0") == CancelOverlay()


def test_always_prompt_acts_only_with_a_rule():
    r = req("a", always_rule="Bash(x)")
    m = setup(["slot", "slot", "slot", ("blank", "always")], [sv("a", 1)], [r])
    m.press("k0")
    focus(m, "a")
    assert m.render_plan()["k3"].decision == "always"
    assert m.press("k3") == VerifyThenDecide(r, "always")
    m = setup(["slot", "slot", "slot", ("blank", "always")], [sv("a", 1)], [req("a")])
    m.press("k0")
    assert m.render_plan()["k3"] == KeySpec("status")
    assert m.press("k3") == Noop("blank key")
    # Without a rule a slot key set to Always behaves as if it had no prompt setting.
    m = setup(["slot", "slot", "slot", ("slot", "always")], [sv("a", 1)], [req("a")])
    m.press("k0")
    assert [m.render_plan()[f"k{i}"].decision for i in (1, 2)] == ["allow", "deny"]
    assert m.render_plan()["k3"].kind == "preview"


def test_several_keys_with_the_same_prompt_all_act():
    r = req("a")
    m = setup(["slot", ("blank", "allow"), ("usage:0", "allow"), ("blank", "deny")], [sv("a", 1)], [r])
    m.press("k0")
    focus(m, "a")
    plan = m.render_plan()
    assert plan["k1"].decision == "allow" and plan["k2"].decision == "allow"
    assert m.press("k2") == VerifyThenDecide(r, "allow")
    assert m.press("k1") == VerifyThenDecide(r, "allow")


def test_fallback_fills_only_missing_decisions_and_previews_fill_the_rest():
    m = setup(["slot", "slot", ("blank", "deny"), "interrupt", "slot"], [sv("a", 1)], [req("a")])
    m.press("k0")
    plan = m.render_plan()
    assert plan["k2"].decision == "deny"
    assert plan["k1"].decision == "allow"
    assert plan["k3"].kind == "preview" and plan["k4"].kind == "preview"
    assert [plan["k3"].index, plan["k4"].index, plan["k3"].total] == [0, 1, 2]


def test_prompt_keys_are_not_taken_as_participants_for_other_decisions():
    # The deny key is a slot too, but it is already spoken for, so allow comes from k2.
    m = setup(["slot", ("slot", "deny"), "slot"], [sv("a", 1)], [req("a")])
    m.press("k0")
    plan = m.render_plan()
    assert plan["k1"].decision == "deny" and plan["k2"].decision == "allow"


def test_overlay_needs_both_allow_and_deny_placed():
    # One participant and no prompt keys: Deny cannot be placed.
    assert setup(["slot", "slot"], [sv("a", 1)], [req("a")]).press("k0").overlay is False
    # A prompt Allow plus one participant for Deny: placed.
    assert setup(["slot", ("blank", "allow"), "slot"], [sv("a", 1)], [req("a")]).press("k0").overlay is True
    # A prompt Allow and no participants: Deny cannot be placed.
    assert setup(["slot", ("blank", "allow")], [sv("a", 1)], [req("a")]).press("k0").overlay is False
    # Prompt Allow and Deny alone: placed.
    assert setup(["slot", ("blank", "allow"), ("blank", "deny")], [sv("a", 1)], [req("a")]).press("k0").overlay is True
    # Only the pressed key claims Allow: it is Cancel, so nothing is placed.
    assert setup([("slot", "allow"), ("blank", "deny")], [sv("a", 1)], [req("a")]).press("k0").overlay is False


def test_overlay_exits_when_a_prompt_key_disappears_and_deny_cannot_be_placed():
    m = setup(["slot", ("blank", "allow"), ("blank", "deny")], [sv("a", 1)], [req("a")])
    m.press("k0")
    m.disappear("k2")
    assert m.render_plan()["k1"] == KeySpec("status")


def press_with_status(m, ctx, name, status):
    """Press a slot key, then deliver the focus worker's result for that press."""
    action = m.press(ctx)
    focus(m, name, status)
    return action


@pytest.mark.parametrize("status", ["not_found", "no_title", "unavailable"])
def test_second_press_on_a_no_tab_key_dismisses_the_session(status):
    m = setup(["slot"] * 3, [sv("a", 1), sv("b", 2)])
    assert isinstance(press_with_status(m, "k0", "a", status), Focus)
    assert m.render_plan()["k0"].focus == status
    assert m.press("k0") == Noop("dismissed")
    plan = m.render_plan()
    assert plan["k0"].kind == "status" and plan["k1"].ref == ref("b")


@pytest.mark.parametrize("status", ["ambiguous", "not_front", "focused", "unverified"])
def test_other_statuses_keep_focusing(status):
    m = setup(["slot"] * 2, [sv("a", 1)])
    press_with_status(m, "k0", "a", status)
    assert isinstance(m.press("k0"), Focus)
    assert m.render_plan()["k0"].ref == ref("a")


def test_first_press_focuses_even_without_a_status():
    m = setup(["slot"] * 2, [sv("a", 1)])
    assert isinstance(m.press("k0"), Focus)
    assert m.render_plan()["k0"].ref == ref("a")


def test_pending_session_is_not_dismissed():
    # Two keys cannot host the overlay, so every press is a plain FocusAndOpen.
    m = setup(["slot"] * 2, [sv("a", 1)], [req("a")])
    assert isinstance(press_with_status(m, "k0", "a", "not_found"), FocusAndOpen)
    assert isinstance(m.press("k0"), FocusAndOpen)
    assert m.render_plan()["k0"].ref == ref("a")


def test_dismissed_session_returns_when_it_asks_for_permission():
    m = setup(["slot"] * 2, [sv("a", 1)])
    press_with_status(m, "k0", "a", "not_found")
    m.press("k0")
    feed(m, [sv("a", 1)])
    assert m.render_plan()["k0"].kind == "status"
    feed(m, [sv("a", 1)], [req("a")])
    spec = m.render_plan()["k0"]
    assert spec.ref == ref("a") and spec.pending and spec.focus == ""
    # Once back it stays: the dismissal is not re-applied after the request ends.
    feed(m, [sv("a", 1)])
    assert m.render_plan()["k0"].ref == ref("a")


def test_dismissal_is_forgotten_when_the_session_ends():
    m = setup(["slot"] * 2, [sv("a", 1)])
    press_with_status(m, "k0", "a", "not_found")
    m.press("k0")
    feed(m, [])
    assert m._dismissed == set()
    feed(m, [sv("a", 5)])
    assert m.render_plan()["k0"].ref == ref("a")


def test_dismissing_frees_the_slot_for_others():
    m = setup(["slot"] * 2, [sv("a", 1), sv("b", 2)])
    press_with_status(m, "k0", "a", "no_title")
    m.press("k0")
    feed(m, [sv("a", 1), sv("b", 2), sv("c", 3)])
    plan = m.render_plan()
    assert plan["k0"].ref == ref("c") and plan["k1"].ref == ref("b")


def test_dismissing_the_last_focused_session_clears_the_interrupt_target():
    m = setup(["slot", "interrupt"], [sv("a", 1)])
    press_with_status(m, "k0", "a", "not_found")
    m.press("k0")
    assert m._last_focused is None
    assert m.press("k1") == Noop("session tab not confirmed")


def test_overflow_dismisses_the_session_it_shows():
    m = setup(["slot"] * 2, [sv("a", 1), sv("b", 2), sv("c", 3)])
    # k0 is a; k1 is overflow over b and c, showing b.
    assert m.render_plan()["k1"].ref == ref("b") and m.render_plan()["k1"].count == 2
    assert isinstance(m.press("k1"), Focus)  # cycles to c and focuses it
    focus(m, "c", "not_found")
    assert m.render_plan()["k1"].ref == ref("c")
    assert m.press("k1") == Noop("dismissed")
    plan = m.render_plan()
    assert plan["k0"].ref == ref("a") and plan["k1"].ref == ref("b") and plan["k1"].kind == "slot"
