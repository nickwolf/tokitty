"""Per-session Claude Code activity state machine + multi-session aggregation.

Pure logic, no I/O: consumes the parsed JSON records that hook_writer.py
writes to <sessions-dir>/<session_id>.json (one file per session, latest
event only) and turns them into a single ActivityView the UI can render.

See docs/task-6-brief (issue #6) for the full spec this implements.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

ACTIVITY_STATES = ("permission", "working", "thinking", "done_hop", "idle")

IDLE_DECAY_S = 180.0
GONE_S = 1800.0
DONE_HOP_S = 1.5
WORK_STRETCH_MIN_S = 20.0

_KNOWN_EVENTS = frozenset(
    {
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Notification",
        "Stop",
        "SubagentStop",
    }
)

_WORKING_THINKING_EVENTS = frozenset(
    {"UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop"}
)

_TOOL_LABELS = {
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
}

_PRIORITY = {"permission": 0, "working": 1, "thinking": 2, "done_hop": 3}


def _tool_label(tool_name):
    if not tool_name:
        return ""
    return _TOOL_LABELS.get(tool_name, tool_name)


@dataclass
class ActivityView:
    state: str
    tool_label: str = ""
    session_id: str = ""


@dataclass
class _SessionState:
    last_seq: int = -1
    last_ts: float = 0.0
    last_event: str = ""
    base_state: str = "idle"  # "working" | "thinking" | "idle" | "done_hop"
    tool_label: str = ""
    permission: bool = False
    stretch_start: Optional[float] = None
    done_hop_at: Optional[float] = None


def _valid_record(rec):
    if not isinstance(rec, dict):
        return False
    event = rec.get("event")
    if event not in _KNOWN_EVENTS:
        return False
    seq = rec.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        return False
    ts = rec.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return False
    return True


class ActivityTracker:
    def __init__(self):
        self._sessions: Dict[str, _SessionState] = {}

    def observe(self, records: Dict[str, dict], now: float) -> None:
        # Forget sessions whose file has been deleted.
        for session_id in list(self._sessions.keys()):
            if session_id not in records:
                del self._sessions[session_id]

        for session_id, rec in records.items():
            if not _valid_record(rec):
                continue

            seq = rec["seq"]
            ts = float(rec["ts"])
            event = rec["event"]

            state = self._sessions.get(session_id)
            if state is None:
                state = _SessionState()
                self._sessions[session_id] = state

            if seq <= state.last_seq:
                continue  # ignore seq regressions / duplicates

            if event in _WORKING_THINKING_EVENTS:
                if state.stretch_start is None:
                    state.stretch_start = ts
                if event == "PreToolUse":
                    state.base_state = "working"
                    state.tool_label = _tool_label(rec.get("tool_name"))
                else:
                    state.base_state = "thinking"
                    state.tool_label = ""
                state.done_hop_at = None
            elif event == "Notification":
                # Overlay only: doesn't touch the working/thinking stretch or
                # base_state.
                pass
            elif event == "Stop":
                gated = (
                    state.stretch_start is not None
                    and (ts - state.stretch_start) >= WORK_STRETCH_MIN_S
                )
                if gated:
                    state.base_state = "done_hop"
                    state.done_hop_at = ts
                else:
                    state.base_state = "idle"
                    state.done_hop_at = None
                state.stretch_start = None
                state.tool_label = ""

            # Permission flag: raised on Notification, lowered by the next
            # (higher-seq) event of any other kind.
            state.permission = event == "Notification"

            state.last_seq = seq
            state.last_ts = ts
            state.last_event = event

    def _effective_view(self, session_id: str, state: _SessionState, now: float) -> Optional[ActivityView]:
        if state.permission:
            # Permission does not decay on the idle timer.
            return ActivityView(state="permission", session_id=session_id)

        if state.base_state == "done_hop":
            if state.done_hop_at is not None and (now - state.done_hop_at) < DONE_HOP_S:
                return ActivityView(state="done_hop", session_id=session_id)
            return None

        if state.base_state in ("working", "thinking"):
            if (now - state.last_ts) >= IDLE_DECAY_S:
                return None
            if state.base_state == "working":
                return ActivityView(state="working", tool_label=state.tool_label, session_id=session_id)
            return ActivityView(state="thinking", session_id=session_id)

        return None

    def aggregate(self, now: float) -> ActivityView:
        best = None
        best_key = None
        for session_id, state in self._sessions.items():
            view = self._effective_view(session_id, state, now)
            if view is None:
                continue
            key = (_PRIORITY[view.state], -state.last_ts)
            if best_key is None or key < best_key:
                best_key = key
                best = view
        return best if best is not None else ActivityView(state="idle")

    def stale_session_ids(self, now: float) -> List[str]:
        return [
            session_id
            for session_id, state in self._sessions.items()
            if (now - state.last_ts) >= GONE_S
        ]
