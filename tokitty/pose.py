"""Pure function combining v1's usage-driven display state with a live
ActivityView into the final sprite state + label the UI should render.

Priority (spec, verbatim): permission flag > capped/wake sequence >
working / thinking / done-hop > idle. An idle activity state is exactly
v1 behavior (mood ladder from usage %) -- v1's own display state passes
through untouched.
"""
from __future__ import annotations

from typing import Optional

from tokitty.activity import ActivityView

# Placeholder sprite mapping (task 8 draws the real ones). Keep this the
# ONLY place activity states map to sprite names -- task 8 swaps it by
# changing only this dict, once real sprites named "working", "thinking",
# "permission", "done_hop" exist.
ACTIVITY_SPRITE_PLACEHOLDERS = {
    "permission": "alert",
    "working": "interested",
    "thinking": "content",
    "done_hop": "activate",
}

# v1 substates of the capped/wake sequence (see mood.compute_capped_substate)
# -- these outrank an activity-derived pose, per spec.
_CAPPED_WAKE_STATES = frozenset({"flopped", "stirring", "waking"})

_ACTIVITY_POSE_STATES = frozenset({"working", "thinking", "done_hop"})


def resolve_pose(v1_state: str, activity: Optional[ActivityView]) -> dict:
    """Return {"sprite_state": str, "tool_label": str, "accent": bool}.

    `v1_state` is whatever tokitty.__main__._display_state_for already
    computed (a mood, a capped substate, "activate", or "confused").
    `activity` is the latest ActivityView from the watcher, or None if no
    watcher is running / nothing resolved yet -- treated the same as an
    idle ActivityView.
    """
    if activity is not None and activity.state == "permission":
        return {
            "sprite_state": ACTIVITY_SPRITE_PLACEHOLDERS["permission"],
            "tool_label": "",
            "accent": True,
        }

    if v1_state in _CAPPED_WAKE_STATES:
        return {"sprite_state": v1_state, "tool_label": "", "accent": False}

    if activity is not None and activity.state in _ACTIVITY_POSE_STATES:
        tool_label = activity.tool_label if activity.state == "working" else ""
        return {
            "sprite_state": ACTIVITY_SPRITE_PLACEHOLDERS[activity.state],
            "tool_label": tool_label,
            "accent": False,
        }

    return {"sprite_state": v1_state, "tool_label": "", "accent": False}
