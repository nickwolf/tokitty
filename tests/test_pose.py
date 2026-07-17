"""Tests for tokitty.pose — pose priority: permission > capped/wake >
working/thinking/done-hop > idle (exactly v1 behavior)."""

from tokitty.activity import ActivityView
from tokitty.pose import ACTIVITY_SPRITE_PLACEHOLDERS, resolve_pose


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


def test_capped_wake_states_beat_working_activity():
    for capped_state in ("flopped", "stirring", "waking"):
        result = resolve_pose(capped_state, ActivityView(state="working", tool_label="Running"))
        assert result["sprite_state"] == capped_state
        assert result["tool_label"] == ""
        assert result["accent"] is False


def test_permission_beats_capped_wake_states():
    for capped_state in ("flopped", "stirring", "waking"):
        result = resolve_pose(capped_state, ActivityView(state="permission"))
        assert result["sprite_state"] == ACTIVITY_SPRITE_PLACEHOLDERS["permission"]
        assert result["accent"] is True
