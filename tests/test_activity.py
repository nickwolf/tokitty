"""Tests for tokitty.activity — per-session activity state machine + aggregation."""

from tokitty.activity import (
    ACTIVITY_STATES,
    ActivityTracker,
    ActivityView,
    IDLE_DECAY_S,
    GONE_S,
    DONE_HOP_S,
    WORK_STRETCH_MIN_S,
)


def rec(session_id, event, ts, seq=1, tool_name=None, agent_id=None):
    d = {"session_id": session_id, "event": event, "seq": seq, "ts": ts}
    if tool_name is not None:
        d["tool_name"] = tool_name
    if agent_id is not None:
        d["agent_id"] = agent_id
    return d


def test_constants():
    assert ACTIVITY_STATES == ("permission", "working", "thinking", "done_hop", "idle")


def test_no_sessions_is_idle():
    t = ActivityTracker()
    t.observe({}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "idle"
    assert view.session_id == ""
    assert view.tool_label == ""


def test_user_prompt_submit_is_thinking():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "UserPromptSubmit", 100.0)}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "thinking"
    assert view.session_id == "s1"


def test_pre_tool_use_is_working_with_label():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PreToolUse", 100.0, tool_name="Bash")}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "working"
    assert view.tool_label == "Running"
    assert view.session_id == "s1"


def test_tool_label_mapping():
    cases = {
        "Bash": "Running",
        "Edit": "Editing",
        "Write": "Editing",
        "NotebookEdit": "Editing",
        "Read": "Reading",
        "Grep": "Searching",
        "Glob": "Searching",
        "Agent": "Delegating",
        "Task": "Delegating",
        "WebFetch": "Browsing",
        "WebSearch": "Browsing",
        "SomeOtherTool": "SomeOtherTool",
    }
    for tool_name, label in cases.items():
        t = ActivityTracker()
        t.observe({"s1": rec("s1", "PreToolUse", 100.0, tool_name=tool_name)}, now=100.0)
        view = t.aggregate(100.0)
        assert view.state == "working"
        assert view.tool_label == label, f"{tool_name} -> expected {label}, got {view.tool_label}"


def test_post_tool_use_is_thinking():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PostToolUse", 100.0, tool_name="Bash")}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "thinking"


def test_notification_is_permission():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "Notification", 100.0)}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "permission"
    assert view.session_id == "s1"


def test_subagent_stop_is_thinking():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "SubagentStop", 100.0, agent_id="a1")}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "thinking"


def test_stale_stop_at_startup_does_not_hop():
    """First-ever record for a session is a Stop -> must not produce done_hop."""
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "Stop", 100.0)}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state != "done_hop"
    assert view.state == "idle"


def test_stop_after_short_stretch_is_idle_not_done_hop():
    """Conversational back-and-forth: working stretch < WORK_STRETCH_MIN_S -> no hop."""
    t = ActivityTracker()
    base = 1000.0
    t.observe({"s1": rec("s1", "UserPromptSubmit", base, seq=1)}, now=base)
    short = WORK_STRETCH_MIN_S - 5.0
    t.observe({"s1": rec("s1", "Stop", base + short, seq=2)}, now=base + short)
    view = t.aggregate(base + short)
    assert view.state == "idle"


def test_stop_after_long_stretch_is_done_hop():
    t = ActivityTracker()
    base = 1000.0
    t.observe({"s1": rec("s1", "UserPromptSubmit", base, seq=1)}, now=base)
    long_stretch = WORK_STRETCH_MIN_S + 5.0
    t.observe({"s1": rec("s1", "Stop", base + long_stretch, seq=2)}, now=base + long_stretch)
    view = t.aggregate(base + long_stretch)
    assert view.state == "done_hop"
    assert view.session_id == "s1"


def test_stretch_survives_multiple_working_thinking_transitions():
    """Working/thinking stretch start tracked from first non-idle event, not reset
    on each working<->thinking transition."""
    t = ActivityTracker()
    base = 1000.0
    t.observe({"s1": rec("s1", "UserPromptSubmit", base, seq=1)}, now=base)
    t.observe({"s1": rec("s1", "PreToolUse", base + 5, seq=2, tool_name="Bash")}, now=base + 5)
    t.observe({"s1": rec("s1", "PostToolUse", base + 10, seq=3, tool_name="Bash")}, now=base + 10)
    stop_ts = base + WORK_STRETCH_MIN_S + 1
    t.observe({"s1": rec("s1", "Stop", stop_ts, seq=4)}, now=stop_ts)
    view = t.aggregate(stop_ts)
    assert view.state == "done_hop"


def test_done_hop_expires_to_idle():
    t = ActivityTracker()
    base = 1000.0
    t.observe({"s1": rec("s1", "UserPromptSubmit", base, seq=1)}, now=base)
    stop_ts = base + WORK_STRETCH_MIN_S + 1
    t.observe({"s1": rec("s1", "Stop", stop_ts, seq=2)}, now=stop_ts)
    # right after: still done_hop
    view = t.aggregate(stop_ts + 0.1)
    assert view.state == "done_hop"
    # after DONE_HOP_S: idle
    view2 = t.aggregate(stop_ts + DONE_HOP_S + 0.1)
    assert view2.state == "idle"


def test_done_hop_anchored_to_observe_time_not_event_ts():
    """A ~1Hz watcher may observe a Stop event well after its ts (polling lag).
    The DONE_HOP_S window must be anchored to the observe-time `now`, not the
    event's own `ts`, or slow polling could eat the whole hop window."""
    t = ActivityTracker()
    base = 1000.0
    t.observe({"s1": rec("s1", "UserPromptSubmit", base, seq=1)}, now=base)
    stop_event_ts = base + WORK_STRETCH_MIN_S + 1
    # Observed 5s late relative to the event's own ts.
    observed_at = stop_event_ts + 5.0
    t.observe({"s1": rec("s1", "Stop", stop_event_ts, seq=2)}, now=observed_at)

    # Immediately after observation: still done_hop, anchored to observed_at.
    view = t.aggregate(observed_at + 0.1)
    assert view.state == "done_hop"

    # Just before DONE_HOP_S has elapsed from observed_at: still done_hop.
    view2 = t.aggregate(observed_at + DONE_HOP_S - 0.1)
    assert view2.state == "done_hop"

    # After DONE_HOP_S elapsed from observed_at: idle.
    view3 = t.aggregate(observed_at + DONE_HOP_S + 0.1)
    assert view3.state == "idle"


def test_ignore_seq_regression():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PreToolUse", 100.0, seq=5, tool_name="Bash")}, now=100.0)
    # a lower seq arrives (out-of-order read) - should be ignored
    t.observe({"s1": rec("s1", "Notification", 101.0, seq=3)}, now=101.0)
    view = t.aggregate(101.0)
    assert view.state == "working"


def test_seq_equal_is_regression_and_ignored():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PreToolUse", 100.0, seq=5, tool_name="Bash")}, now=100.0)
    t.observe({"s1": rec("s1", "Notification", 101.0, seq=5)}, now=101.0)
    view = t.aggregate(101.0)
    assert view.state == "working"


def test_permission_flag_cleared_by_newer_event():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "Notification", 100.0, seq=1)}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "permission"
    t.observe({"s1": rec("s1", "PostToolUse", 105.0, seq=2, tool_name="Bash")}, now=105.0)
    view2 = t.aggregate(105.0)
    assert view2.state == "thinking"


def test_permission_does_not_decay_with_idle_timer():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "Notification", 100.0, seq=1)}, now=100.0)
    later = 100.0 + IDLE_DECAY_S + 100.0
    view = t.aggregate(later)
    assert view.state == "permission"


def test_idle_decay_for_non_permission_states():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PreToolUse", 100.0, seq=1, tool_name="Bash")}, now=100.0)
    later = 100.0 + IDLE_DECAY_S + 1.0
    view = t.aggregate(later)
    assert view.state == "idle"


def test_idle_decay_boundary_not_yet_decayed():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PreToolUse", 100.0, seq=1, tool_name="Bash")}, now=100.0)
    view = t.aggregate(100.0 + IDLE_DECAY_S - 1.0)
    assert view.state == "working"


def test_session_forgotten_when_absent_from_records():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "Notification", 100.0, seq=1)}, now=100.0)
    assert t.aggregate(100.0).state == "permission"
    t.observe({}, now=101.0)
    assert t.aggregate(101.0).state == "idle"


def test_aggregation_priority_permission_over_working():
    t = ActivityTracker()
    t.observe(
        {
            "s1": rec("s1", "PreToolUse", 100.0, seq=1, tool_name="Bash"),
            "s2": rec("s2", "Notification", 100.0, seq=1),
        },
        now=100.0,
    )
    view = t.aggregate(100.0)
    assert view.state == "permission"
    assert view.session_id == "s2"


def test_aggregation_priority_working_over_thinking():
    t = ActivityTracker()
    t.observe(
        {
            "s1": rec("s1", "UserPromptSubmit", 100.0, seq=1),
            "s2": rec("s2", "PreToolUse", 100.0, seq=1, tool_name="Edit"),
        },
        now=100.0,
    )
    view = t.aggregate(100.0)
    assert view.state == "working"
    assert view.session_id == "s2"
    assert view.tool_label == "Editing"


def test_aggregation_priority_thinking_over_done_hop():
    t = ActivityTracker()
    base = 1000.0
    stop_ts = base + WORK_STRETCH_MIN_S + 1
    t.observe(
        {
            "s1": rec("s1", "UserPromptSubmit", base, seq=1),
        },
        now=base,
    )
    t.observe(
        {
            "s1": rec("s1", "Stop", stop_ts, seq=2),
            "s2": rec("s2", "PostToolUse", stop_ts, seq=1, tool_name="Bash"),
        },
        now=stop_ts,
    )
    view = t.aggregate(stop_ts)
    assert view.state == "thinking"
    assert view.session_id == "s2"


def test_aggregation_tie_break_most_recent_ts():
    t = ActivityTracker()
    t.observe(
        {
            "s1": rec("s1", "UserPromptSubmit", 100.0, seq=1),
            "s2": rec("s2", "UserPromptSubmit", 105.0, seq=1),
        },
        now=105.0,
    )
    view = t.aggregate(105.0)
    assert view.state == "thinking"
    assert view.session_id == "s2"


def test_malformed_record_missing_seq_ignored():
    t = ActivityTracker()
    t.observe({"s1": {"session_id": "s1", "event": "Notification", "ts": 100.0}}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "idle"


def test_malformed_record_missing_ts_ignored():
    t = ActivityTracker()
    t.observe({"s1": {"session_id": "s1", "event": "Notification", "seq": 1}}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "idle"


def test_malformed_record_missing_event_ignored():
    t = ActivityTracker()
    t.observe({"s1": {"session_id": "s1", "seq": 1, "ts": 100.0}}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "idle"


def test_malformed_record_wrong_types_ignored():
    t = ActivityTracker()
    t.observe(
        {"s1": {"session_id": "s1", "event": "Notification", "seq": "not-an-int", "ts": 100.0}},
        now=100.0,
    )
    view = t.aggregate(100.0)
    assert view.state == "idle"


def test_malformed_record_unknown_event_ignored():
    t = ActivityTracker()
    t.observe({"s1": {"session_id": "s1", "event": "BogusEvent", "seq": 1, "ts": 100.0}}, now=100.0)
    view = t.aggregate(100.0)
    assert view.state == "idle"


def test_stale_session_ids_reports_gone_sessions():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PreToolUse", 100.0, seq=1, tool_name="Bash")}, now=100.0)
    assert t.stale_session_ids(100.0 + GONE_S - 1.0) == []
    assert t.stale_session_ids(100.0 + GONE_S + 1.0) == ["s1"]


def test_stale_session_ids_empty_when_none_stale():
    t = ActivityTracker()
    t.observe({"s1": rec("s1", "PreToolUse", 100.0, seq=1, tool_name="Bash")}, now=100.0)
    assert t.stale_session_ids(150.0) == []


def test_activity_view_defaults():
    view = ActivityView(state="idle")
    assert view.tool_label == ""
    assert view.session_id == ""


def test_no_records_observed_ever_no_crash():
    t = ActivityTracker()
    view = t.aggregate(0.0)
    assert view.state == "idle"
