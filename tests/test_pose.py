"""Tests for tokitty.pose — pose priority: permission > capped/wake >
working/thinking/done-hop > idle (exactly v1 behavior)."""

from tokitty.activity import ActivityView
from tokitty.pose import ACTIVITY_SPRITE_PLACEHOLDERS, prompt_state, resolve_pose


def test_idle_activity_is_exactly_v1_behavior():
    result = resolve_pose("content", ActivityView(state="idle"))
    assert result == {"sprite_state": "content", "tool_label": "", "accent": False}


def test_no_activity_is_exactly_v1_behavior():
    result = resolve_pose("interested", None)
    assert result == {"sprite_state": "interested", "tool_label": "", "accent": False}


def test_working_overrides_v1_mood_state():
    result = resolve_pose("content", ActivityView(state="working", tool_label="Editing"))
    assert result["sprite_state"] == ACTIVITY_SPRITE_PLACEHOLDERS["working"]
    assert result["tool_label"] == "Editing"
    assert result["accent"] is False


def test_thinking_overrides_v1_and_has_no_label():
    result = resolve_pose("content", ActivityView(state="thinking"))
    assert result["sprite_state"] == ACTIVITY_SPRITE_PLACEHOLDERS["thinking"]
    assert result["tool_label"] == ""


def test_done_hop_overrides_v1_and_has_no_label():
    result = resolve_pose("sleeping", ActivityView(state="done_hop"))
    assert result["sprite_state"] == ACTIVITY_SPRITE_PLACEHOLDERS["done_hop"]
    assert result["tool_label"] == ""


def test_permission_beats_working():
    result = resolve_pose("content", ActivityView(state="permission"))
    assert result["sprite_state"] == ACTIVITY_SPRITE_PLACEHOLDERS["permission"]
    assert result["accent"] is True


def test_activate_not_overridden_by_working_activity():
    result = resolve_pose("activate", ActivityView(state="working", tool_label="Coding"))
    assert result["sprite_state"] == "activate"
    assert result["tool_label"] == ""
    assert result["accent"] is False


def test_capped_wake_states_beat_activity_states():
    capped_states = ("activate", "flopped", "stirring", "waking")
    activity_states = (
        ("working", "Running"),
        ("thinking", ""),
        ("done_hop", ""),
    )
    for capped_state in capped_states:
        for activity_state, tool_label in activity_states:
            activity = ActivityView(state=activity_state, tool_label=tool_label) if tool_label else ActivityView(state=activity_state)
            result = resolve_pose(capped_state, activity)
            assert result["sprite_state"] == capped_state
            assert result["tool_label"] == ""
            assert result["accent"] is False


def test_permission_beats_capped_wake_states():
    for capped_state in ("flopped", "stirring", "waking"):
        result = resolve_pose(capped_state, ActivityView(state="permission"))
        assert result["sprite_state"] == ACTIVITY_SPRITE_PLACEHOLDERS["permission"]
        assert result["accent"] is True


def test_question_beats_capped_wake_and_working_with_the_accent():
    for v1_state in ("content", "flopped", "stirring", "waking"):
        result = resolve_pose(v1_state, ActivityView(state="question"))
        assert result["sprite_state"] == "question" == ACTIVITY_SPRITE_PLACEHOLDERS["question"]
        assert result["tool_label"] == ""
        assert result["accent"] is True


def test_prompt_state_thresholds():
    assert prompt_state("permission", 100.0, 100.0 + 29.9) == "permission"
    assert prompt_state("permission", 100.0, 100.0 + 30.0) == "permission_urgent"
    assert prompt_state("permission", 100.0, 100.0 + 179.9) == "permission_urgent"
    assert prompt_state("permission", 100.0, 100.0 + 180.0) == "permission_frantic"


def test_prompt_state_question_never_escalates():
    assert prompt_state("question", 100.0, 100.0 + 1000.0) == "question"


def test_prompt_state_without_clock_is_unchanged():
    assert prompt_state("permission", None, 500.0) == "permission"
    assert prompt_state("permission", 100.0, None) == "permission"
    assert prompt_state("working", 100.0, 500.0) == "working"


def test_resolve_pose_escalates_permission():
    view = ActivityView(state="permission", since=100.0)
    assert resolve_pose("content", view, 110.0)["sprite_state"] == "permission"
    urgent = resolve_pose("content", view, 130.0)
    assert urgent == {"sprite_state": "permission_urgent", "tool_label": "", "accent": True}
    frantic = resolve_pose("content", view, 280.0)
    assert frantic == {"sprite_state": "permission_frantic", "tool_label": "", "accent": True}
    assert resolve_pose("content", view)["sprite_state"] == "permission"


def test_resolve_pose_question_does_not_escalate():
    view = ActivityView(state="question", since=100.0)
    assert resolve_pose("content", view, 1000.0)["sprite_state"] == "question"
