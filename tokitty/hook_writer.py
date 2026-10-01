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
script must therefore NEVER exit non-zero, under any circumstances --
garbage input, missing args, unwritable directories, anything. All logic
lives inside main(), which is wrapped in a bare try/except at module level
so no exception can ever propagate out.

It also never writes to stdout, with ONE exception: on a PermissionRequest
event, _emit_decision() prints a single allow/deny JSON line, and only after
wait_for_decision() has found a decision file whose nonce, session id and
input digest all match this exact request. Claude Code applies that line as
the user's answer, so every other path (error, ambiguity, timeout, disabled
feature) prints nothing and exits 0, leaving Claude Code's own terminal
prompt in charge. Nothing else in this module may print.

PermissionRequest never touches the per-session state file in sessions/.
"""

import hashlib
import json
import os
import secrets
import signal
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


def _atomic_write(sessions_dir, state_file, data, tmp_suffix=".json"):
    fd, tmp_path = tempfile.mkstemp(dir=sessions_dir, prefix=".tmp-", suffix=tmp_suffix)
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


# Permission wait (Stream Dock). Tunables are module constants.
_PERM_CAP_S = 590.0  # just under Claude Code's 600 s default hook timeout
_POLL_S = 0.25
_HEARTBEAT_S = 5.0
_MARKER_MAX_AGE_S = 120.0
_TAIL_BYTES = 256 * 1024
_LOOKUP_TRIES = 5
_LOOKUP_GAP_S = 0.1
_PREVIEW_MAX = 200
_DENY_MESSAGE = "Denied from Stream Dock"
_HEX = frozenset("0123456789abcdef")


def _digest(tool_input):
    blob = json.dumps(tool_input, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _read_tail(path):
    """Return the last _TAIL_BYTES of a file as text, without a partial first line."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(0, size - _TAIL_BYTES)
        f.seek(start)
        raw = f.read()
    if start > 0:
        nl = raw.find(b"\n")
        raw = b"" if nl < 0 else raw[nl + 1 :]
    return raw.decode("utf-8", errors="replace")


def _content_blocks(obj):
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _answered(tail, tool_use_id):
    """True if the tail shows a tool_result for this id.

    A parsed tool_result block counts, and so does the raw key/value text, so
    a result line too large to fit the tail (and therefore unparseable) is
    still noticed. A false positive only makes the hook stay silent.
    """
    if f'"tool_use_id":"{tool_use_id}"' in tail:
        return True
    if f'"tool_use_id": "{tool_use_id}"' in tail:
        return True
    for line in tail.splitlines():
        if "tool_result" not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        for b in _content_blocks(obj):
            if b.get("type") == "tool_result" and b.get("tool_use_id") == tool_use_id:
                return True
    return False


def _unresolved_matches(tail, tool_name, digest):
    """Ids of tool_use blocks matching name and input digest with no result yet."""
    ids = []
    for line in tail.splitlines():
        if "tool_use" not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue  # the first line of a tail is usually truncated
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        for b in _content_blocks(obj):
            if b.get("type") != "tool_use" or b.get("name") != tool_name:
                continue
            tid = b.get("id")
            if not isinstance(tid, str) or not tid or tid in ids:
                continue
            try:
                if _digest(b.get("input")) != digest:
                    continue
            except Exception:
                continue
            ids.append(tid)
    return [t for t in ids if not _answered(tail, t)]


def _lookup_tool_use_id(payload, digest, read_tail_fn, sleep_fn):
    """The one unresolved matching tool_use id, or None (zero, several, unreadable)."""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    for attempt in range(_LOOKUP_TRIES):
        try:
            tail = read_tail_fn(path)
        except Exception:
            tail = ""
        found = _unresolved_matches(tail, payload["tool_name"], digest)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
        if attempt < _LOOKUP_TRIES - 1:
            sleep_fn(_LOOKUP_GAP_S)
    return None


def _preview(tool_name, tool_input):
    key = {
        "Bash": "command",
        "Edit": "file_path",
        "Write": "file_path",
        "Read": "file_path",
        "WebFetch": "url",
    }.get(tool_name)
    text = tool_input.get(key) if key else None
    if not isinstance(text, str):
        text = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text[:_PREVIEW_MAX]


def _marker_fresh(marker, now_fn, mtime_fn):
    try:
        return now_fn() - mtime_fn(marker) < _MARKER_MAX_AGE_S
    except Exception:
        return False


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _read_decision(path, nonce, session_id, digest):
    """Consume a decision file. Returns the behavior only if it provably matches."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        data = None
    _remove(path)
    if not isinstance(data, dict):
        return None
    if (
        data.get("nonce") == nonce
        and data.get("session_id") == session_id
        and data.get("digest") == digest
        and data.get("behavior") in ("allow", "deny")
    ):
        return data["behavior"]
    return None


def wait_for_decision(
    payload,
    tokitty_dir,
    *,
    now_fn=time.time,
    sleep_fn=time.sleep,
    read_tail_fn=_read_tail,
    rand_fn=lambda: secrets.token_hex(8),
    mtime_fn=os.path.getmtime,
    utime_fn=os.utime,
):
    """Hold a PermissionRequest for a Stream Dock answer.

    Returns {"behavior": "allow" | "deny"} only for a decision file that
    matches this request's nonce, session id and input digest; None for every
    other outcome. Never prints. The caller decides what to do with the result.
    """
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(tool_name, str) or not tool_name:
        return None
    if not isinstance(tool_input, dict):
        return None

    marker = os.path.join(tokitty_dir, "streamdock.enabled")
    if not _marker_fresh(marker, now_fn, mtime_fn):
        return None

    started = now_fn()
    digest = _digest(tool_input)
    tool_use_id = _lookup_tool_use_id(payload, digest, read_tail_fn, sleep_fn)
    if tool_use_id is None:
        return None

    nonce = rand_fn()
    if not isinstance(nonce, str) or not nonce or not set(nonce) <= _HEX:
        return None

    pending_dir = os.path.join(tokitty_dir, "pending")
    pending = os.path.join(pending_dir, nonce + ".json")
    decision_file = os.path.join(tokitty_dir, "decisions", nonce + ".json")
    os.makedirs(pending_dir, exist_ok=True)
    try:
        _atomic_write(
            pending_dir,
            pending,
            {
                "v": 1,
                "nonce": nonce,
                "session_id": session_id,
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "digest": digest,
                "preview": _preview(tool_name, tool_input),
                "cwd": payload.get("cwd"),
                "started": started,
                "pid": os.getpid(),
            },
            tmp_suffix=".tmp",
        )
        last_beat = started
        while True:
            try:
                tail = read_tail_fn(payload["transcript_path"])
            except Exception:
                tail = ""
            if _answered(tail, tool_use_id):
                return None
            behavior = _read_decision(decision_file, nonce, session_id, digest)
            if behavior is not None:
                return {"behavior": behavior}
            if not _marker_fresh(marker, now_fn, mtime_fn):
                return None
            now = now_fn()
            if now - started >= _PERM_CAP_S:
                return None
            if now - last_beat >= _HEARTBEAT_S:
                try:
                    utime_fn(pending, None)
                except OSError:
                    pass
                last_beat = now
            sleep_fn(_POLL_S)
    finally:
        _remove(pending)
        _remove(decision_file)


def _emit_decision(decision):
    """The only place this module prints: one JSON line for a validated decision."""
    out = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
    line = json.dumps(out, separators=(",", ":")) + "\n"
    if sys.stdout is not None:
        sys.stdout.write(line)
        sys.stdout.flush()
        return
    # Windowed PyInstaller builds have no sys.stdout; fd 1 may still be the pipe.
    try:
        os.write(1, line.encode("ascii"))
    except Exception:
        pass


def _raise_exit(signum, frame):
    raise SystemExit(0)


def _install_exit_handlers():
    """Turn SIGTERM/SIGHUP into SystemExit so the pending file's finally runs."""
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _raise_exit)
        except Exception:
            pass


def _run_permission(payload, sessions_dir, **kwargs):
    tokitty_dir = os.path.dirname(os.path.abspath(sessions_dir))
    _install_exit_handlers()
    result = wait_for_decision(payload, tokitty_dir, **kwargs)
    if result is None:
        return
    behavior = result.get("behavior")
    if behavior == "allow":
        _emit_decision({"behavior": "allow"})
    elif behavior == "deny":
        _emit_decision({"behavior": "deny", "message": _DENY_MESSAGE})


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
    if event == "PermissionRequest":
        _run_permission(payload, sessions_dir)
        return
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
