#!/usr/bin/env python3
"""Standalone stdlib Claude Code hook script for tokitty.

Reads one JSON payload from stdin (a Claude Code hook event), and writes or
updates a per-session state file under --sessions-dir so tokitty's poller
can render live activity state.

This file must remain a SELF-CONTAINED, dependency-free script: it is
deployed by copying it to <config-dir>/tokitty/hook_writer.py and invoked
directly as `python3 .../hook_writer.py --sessions-dir <dir>` with no
tokitty package on sys.path. Do not import anything from the `tokitty`
package here, and stdlib only — no third-party dependencies.

SAFETY: Claude Code treats hook stdout/exit code as live control signals
(stdout can be parsed as a permission decision on PreToolUse, injected into
prompt context on UserPromptSubmit, and exit code 2 blocks the tool). This
script must therefore NEVER write to stdout and NEVER exit non-zero, under
any circumstances -- garbage input, missing args, unwritable directories,
anything. All logic lives inside main(), which is wrapped in a bare
try/except at module level so no exception can ever propagate out.
"""

import json
import os
import sys
import tempfile
import time


def _read_stdin_payload():
    raw = sys.stdin.buffer.read()
    return json.loads(raw)


def _parse_sessions_dir(argv):
    for i, arg in enumerate(argv):
        if arg == "--sessions-dir" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _read_prev_seq(state_file):
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            prev = json.load(f)
        return int(prev.get("seq", 0))
    except Exception:
        return 0


def _atomic_write(sessions_dir, state_file, data):
    fd, tmp_path = tempfile.mkstemp(dir=sessions_dir, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, state_file)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


_KNOWN_EVENTS = frozenset(
    {
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Notification",
        "Stop",
        "SubagentStop",
        "SessionEnd",
    }
)


def main():
    sessions_dir = _parse_sessions_dir(sys.argv[1:])
    if not sessions_dir:
        return

    payload = _read_stdin_payload()
    if not isinstance(payload, dict):
        return

    session_id = payload.get("session_id")
    if not session_id:
        return

    event = payload.get("hook_event_name")
    if not event or event not in _KNOWN_EVENTS:
        return

    os.makedirs(sessions_dir, exist_ok=True)

    state_file = os.path.join(sessions_dir, f"{session_id}.json")

    if event == "SessionEnd":
        try:
            os.remove(state_file)
        except FileNotFoundError:
            pass
        return

    prev_seq = _read_prev_seq(state_file)

    data = {
        "session_id": session_id,
        "event": event,
        "seq": prev_seq + 1,
        "ts": time.time(),
    }

    tool_name = payload.get("tool_name")
    if tool_name is not None:
        data["tool_name"] = tool_name

    agent_id = payload.get("agent_id")
    if agent_id is not None:
        data["agent_id"] = agent_id

    _atomic_write(sessions_dir, state_file, data)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
