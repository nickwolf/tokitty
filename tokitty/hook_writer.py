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

It prints only validated control output, through _print_line(): the
PermissionRequest decision and the usage note.

On a PermissionRequest event, _emit_decision() prints a single allow/deny JSON
line (an "always" answer is an allow that also carries one session-scoped
addRules entry, built here from the stdin payload and never from the decision
file), and only after _wait() has found a decision file whose nonce, session id
and input digest all match this exact request. Claude Code applies that line as
the user's answer, so every other path (error, ambiguity, timeout, disabled
feature) prints nothing and exits 0, leaving Claude Code's own terminal
prompt in charge.

On PostToolUse, SessionStart and SubagentStart, _usage_note() may print one
additionalContext line when the widget's usage.json (next to the sessions dir)
holds a fresh alert this context has not been told about yet. SessionStart and
SubagentStart write no state file, so activity resolution never sees them.
Nothing else in this module may print.

A PermissionRequest updates the per-session state file like any other event
(Codex sessions rely on it for their permission state) before the Stream Dock
wait starts, so the wait never delays it.
"""

import hashlib
import json
import os
import re
import secrets
import signal
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone


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
        "PermissionRequest",
        "Interrupt",
        "SessionStart",
        "SubagentStart",
    }
)

# Events that only carry the usage note; they never write the state file.
_NOTE_ONLY_EVENTS = frozenset({"SessionStart", "SubagentStart"})
_NOTE_EVENTS = frozenset({"PostToolUse", "SessionStart", "SubagentStart"})
_USAGE_MAX_AGE_S = 900.0


# Permission wait (Stream Dock). Tunables are module constants.
_PERM_CAP_S = 590.0  # just under Claude Code's 600 s default hook timeout
_POLL_S = 0.25
_HEARTBEAT_S = 5.0
_MARKER_MAX_AGE_S = 120.0
_TAIL_BYTES = 256 * 1024
_READ_CAP = 8 * 1024 * 1024  # most bytes read from the transcript in one call
_MAX_CHUNKS = 16  # read calls per poll; the rest is picked up on the next poll
_STALE_CALL_S = 60.0  # a matching tool_use older than this before the hook started is not ours
_LOOKUP_TRIES = 50  # 5 s: on Claude Code 2.1.287 the tool_use reaches the transcript ~0.4 s after the hook starts
_LOOKUP_GAP_S = 0.1
_PREVIEW_MAX = 200
_DENY_MESSAGE = "Denied from Stream Dock"
_HEX = frozenset("0123456789abcdef")


def _digest(tool_input):
    blob = json.dumps(tool_input, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _read_tail(path):
    """Return (text, size): the last _TAIL_BYTES of a file as text, without a
    partial first line, and the byte size the read ended at."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(0, size - _TAIL_BYTES)
        f.seek(start)
        raw = f.read(size - start)
    if start > 0:
        nl = raw.find(b"\n")
        raw = b"" if nl < 0 else raw[nl + 1 :]
    return raw.decode("utf-8", errors="replace"), size


def _read_from(path, offset):
    """Bytes appended at or after offset, at most _READ_CAP of them.

    Raises if the file is now shorter than offset (truncated or replaced).
    """
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        if size < offset:
            raise OSError("transcript is shorter than the recorded offset")
        f.seek(offset)
        return f.read(min(_READ_CAP, size - offset))


def _tool_use_id_ok(tid):
    return isinstance(tid, str) and tid.isascii() and all(c.isalnum() or c in "_-" for c in tid) and bool(tid)


def _needles(tool_use_id):
    return [
        f'"tool_use_id":"{tool_use_id}"'.encode(),
        f'"tool_use_id": "{tool_use_id}"'.encode(),
    ]


def _new_scan(tool_use_id, size, tail_text):
    needles = _needles(tool_use_id)
    keep = max(len(n) for n in needles)
    return {
        "offset": size,
        "needles": needles,
        "keep": keep,
        "carry": tail_text.encode("utf-8", errors="replace")[-keep:],
    }


def _scan(scan, path, read_from_fn):
    """Scan the bytes appended since the last scan for a tool_result for our id.

    Returns (answered, caught_up). answered is True if the needle appeared or the
    transcript can no longer be trusted (read error, truncated or replaced).
    caught_up is True only if a read came back empty, so the scan reached the end
    of the file; at most _MAX_CHUNKS reads happen per call, and a bigger backlog
    leaves caught_up False. Carries the last len(needle) bytes across chunks so a
    split needle is still found. A decision may only be acted on when the latest
    scan was (False, True)."""
    try:
        for _ in range(_MAX_CHUNKS):
            chunk = read_from_fn(path, scan["offset"])
            if not chunk:
                return False, True
            data = scan["carry"] + chunk
            if any(n in data for n in scan["needles"]):
                return True, False
            scan["carry"] = data[-scan["keep"] :]
            scan["offset"] += len(chunk)
        return False, False
    except Exception:
        return True, False


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
    for needle in _needles(tool_use_id):
        if needle.decode() in tail:
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


def _too_old(obj, started):
    """True if the line carries a parseable timestamp over _STALE_CALL_S before started."""
    ts = obj.get("timestamp")
    if not isinstance(ts, str):
        return False
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp() < started - _STALE_CALL_S
    except Exception:
        return False


def _unresolved_matches(tail, tool_name, digest, started):
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
        if _too_old(obj, started):
            continue
        for b in _content_blocks(obj):
            if b.get("type") != "tool_use" or b.get("name") != tool_name:
                continue
            tid = b.get("id")
            if not _tool_use_id_ok(tid) or tid in ids:
                continue
            try:
                if _digest(b.get("input")) != digest:
                    continue
            except Exception:
                continue
            ids.append(tid)
    return [t for t in ids if not _answered(tail, t)]


_AGENT_ID_CHARS = frozenset("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-")


def _transcript_path(payload):
    """The transcript that holds this call. A subagent's calls go to
    <session transcript minus .jsonl>/subagents/agent-<agent_id>.jsonl, while
    the payload's transcript_path still names the main session's file."""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    agent_id = payload.get("agent_id")
    if agent_id is None:
        return path
    if not isinstance(agent_id, str) or not agent_id or not set(agent_id) <= _AGENT_ID_CHARS:
        return None
    if not path.endswith(".jsonl"):
        return None
    return os.path.join(path[: -len(".jsonl")], "subagents", "agent-" + agent_id + ".jsonl")


def _lookup_tool_use_id(payload, digest, read_tail_fn, sleep_fn, started):
    """(tool_use_id, scan state) for the one unresolved matching call, or None
    (zero, several, unreadable). The scan state starts at the byte size the
    lookup read ended at, so later polls cover everything appended after it."""
    path = _transcript_path(payload)
    if path is None:
        return None
    for attempt in range(_LOOKUP_TRIES):
        try:
            tail, size = read_tail_fn(path)
        except Exception:
            tail, size = "", None
        found = _unresolved_matches(tail, payload["tool_name"], digest, started)
        if len(found) == 1:
            if size is None:
                return None
            return found[0], _new_scan(found[0], size, tail)
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


_HOST = re.compile(r"[a-z0-9.-]+")
_PATH_BAD = set("*?[]{}!\\")


def _narrow_rule(tool_name, tool_input):
    """The one permission rule an "always" answer may add, or None.

    Returns (rule tool name, rule content). Only shapes that cannot widen into
    a glob or another target qualify; everything else gets no Always key.
    """
    try:
        if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return None
        if tool_name == "Bash":
            command = tool_input.get("command")
            if not isinstance(command, str) or not command:
                return None
            if "*" in command or "\n" in command or "\r" in command:
                return None
            return ("Bash", command)
        if tool_name in ("Edit", "Write"):
            path = tool_input.get("file_path")
            if not isinstance(path, str) or not path.startswith("/"):
                return None
            if path != path.strip() or any(c in _PATH_BAD for c in path):
                return None
            if any(seg in ("", ".", "..") for seg in path.split("/")[1:]):
                return None
            # Edit rules cover Write too; a leading // marks an absolute path.
            return ("Edit", "/" + path)
        if tool_name == "WebFetch":
            url = tool_input.get("url")
            if not isinstance(url, str):
                return None
            parts = urllib.parse.urlsplit(url)
            host = parts.hostname
            if parts.scheme not in ("http", "https") or not host or not _HOST.fullmatch(host):
                return None
            return ("WebFetch", "domain:" + host)
    except Exception:
        return None
    return None


def _read_decision(path, nonce, session_id, digest, rule=None):
    """Consume a decision file. Returns the behavior only if it provably matches.

    "always" is accepted only when the hook computed a rule for this request.
    """
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
        and (data.get("behavior") in ("allow", "deny") or (data.get("behavior") == "always" and rule is not None))
    ):
        return data["behavior"]
    return None


def wait_for_decision(payload, tokitty_dir, **kwargs):
    """Hold a PermissionRequest for a Stream Dock answer.

    Returns {"behavior": "allow" | "deny" | "always"} only for a decision file that
    matches this request's nonce, session id and input digest; None for every
    other outcome. Never prints. The claim is released before returning; a
    caller that prints should use _wait() and release the claim after printing.
    """
    claims = []
    try:
        return _wait(payload, tokitty_dir, claims, **kwargs)
    finally:
        for c in claims:
            _remove(c)


def _wait(
    payload,
    tokitty_dir,
    claims,
    *,
    now_fn=time.time,
    mono_fn=time.monotonic,
    sleep_fn=time.sleep,
    read_tail_fn=_read_tail,
    read_from_fn=_read_from,
    rand_fn=lambda: secrets.token_hex(8),
    mtime_fn=os.path.getmtime,
    utime_fn=os.utime,
):
    """The wait itself. The claim file's path is appended to claims as soon as
    it exists and is never removed here: the caller removes it, after any
    output. The pending and decision files are removed on every exit.

    now_fn (wall clock) is used only for the marker age and the `started`
    field; the 590 s cap and the heartbeat use mono_fn.
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
    started_mono = mono_fn()
    digest = _digest(tool_input)
    rule = _narrow_rule(tool_name, tool_input)
    looked_up = _lookup_tool_use_id(payload, digest, read_tail_fn, sleep_fn, started)
    if looked_up is None:
        return None
    tool_use_id, scan = looked_up
    transcript = _transcript_path(payload)

    nonce = rand_fn()
    if not isinstance(nonce, str) or not nonce or not set(nonce) <= _HEX:
        return None

    pending_dir = os.path.join(tokitty_dir, "pending")
    pending = os.path.join(pending_dir, nonce + ".json")
    decision_file = os.path.join(tokitty_dir, "decisions", nonce + ".json")
    os.makedirs(pending_dir, exist_ok=True)

    # One hook process per prompt: claim the tool use id atomically. A second
    # process for the same id (or a stale claim) fails closed and prints nothing.
    claim_key = hashlib.sha256((session_id + "\0" + tool_use_id).encode("utf-8")).hexdigest()[:32]
    claim = os.path.join(pending_dir, claim_key + ".claim")
    try:
        os.close(os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        return None
    claims.append(claim)

    def expired():
        return mono_fn() - started_mono >= _PERM_CAP_S or not _marker_fresh(marker, now_fn, mtime_fn)

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
                "always_rule": f"{rule[0]}({rule[1]})" if rule else None,
                "cwd": payload.get("cwd"),
                "started": started,
                "pid": os.getpid(),
            },
            tmp_suffix=".tmp",
        )
        last_beat = started_mono
        held = None  # a consumed decision, waiting for a caught-up scan
        while True:
            answered, caught_up = _scan(scan, transcript, read_from_fn)
            if answered:
                return None
            if expired():
                return None
            if held is None and caught_up:
                held = _read_decision(decision_file, nonce, session_id, digest, rule)
                if held is not None:
                    # Narrow the race with a terminal answer: look once more.
                    answered, caught_up = _scan(scan, transcript, read_from_fn)
                    if answered:
                        return None
            if held is not None and caught_up:
                # Only a scan that found no answer and reached the end gets here.
                if expired():
                    return None
                return {"behavior": held}
            mono = mono_fn()
            if mono - last_beat >= _HEARTBEAT_S:
                try:
                    utime_fn(pending, None)
                except OSError:
                    pass
                last_beat = mono
            sleep_fn(_POLL_S)
    finally:
        _remove(pending)
        _remove(decision_file)


def _emit_decision(decision):
    """One JSON line for a validated decision."""
    _print_line({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}})


def _print_line(out):
    """The only place this module prints: one validated JSON line."""
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


def _safe_name(name):
    """True for a string usable as a file name component."""
    return (
        isinstance(name, str)
        and bool(name)
        and ".." not in name
        and not any(c in name for c in ("/", "\\", "\0"))
    )


def _parse_iso(text):
    """Epoch seconds for an ISO-8601 string (naive means UTC), or None."""
    if not isinstance(text, str):
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _note_text(alerts, fetched_ts):
    parts = []
    reading = time.strftime("%H:%M", time.localtime(fetched_ts))
    for a in alerts:
        text = f"{a['kind']} usage {int(a['pct'])}% (threshold {int(a['threshold'])}%)"
        resets = _parse_iso(a.get("resets_at"))
        if resets is not None:
            tm = time.localtime(resets)
            text += f", resets {time.strftime('%a %b', tm)} {tm.tm_mday} {time.strftime('%H:%M', tm)} local"
        parts.append(text + f", reading from {reading} local")
    return "tokitty: " + "; ".join(parts)


def _usage_note(payload, sessions_dir, session_id, event, now_fn=time.time):
    """Print the usage note once per (session, context, window), or nothing.

    Cheap on the common path: one failed open of usage.json when the feature
    is off. Any error means no output.
    """
    try:
        if not _safe_name(session_id):
            return
        tokitty_dir = os.path.dirname(os.path.abspath(sessions_dir))
        try:
            with open(os.path.join(tokitty_dir, "usage.json"), "r", encoding="utf-8") as f:
                usage = json.load(f)
        except FileNotFoundError:
            return
        if not isinstance(usage, dict) or usage.get("v") != 1:
            return
        fetched_ts = _parse_iso(usage.get("fetched_at"))
        if fetched_ts is None or now_fn() - fetched_ts > _USAGE_MAX_AGE_S:
            return
        raw = usage.get("alerts")
        if not isinstance(raw, list):
            return
        alerts = []
        for a in raw:
            if (
                isinstance(a, dict)
                and a.get("kind") in ("session", "weekly")
                and isinstance(a.get("pct"), (int, float))
                and not isinstance(a.get("pct"), bool)
                and isinstance(a.get("threshold"), (int, float))
                and not isinstance(a.get("threshold"), bool)
                and isinstance(a.get("window"), str)
                and a["window"]
            ):
                alerts.append(a)
        if not alerts:
            return

        agent_id = payload.get("agent_id")
        if agent_id is None:
            context = "main"
        elif isinstance(agent_id, str) and agent_id:
            context = agent_id
        else:
            return

        notes_dir = os.path.join(tokitty_dir, "usage_notes")
        notes_file = os.path.join(notes_dir, f"{session_id}.json")
        try:
            with open(notes_file, "r", encoding="utf-8") as f:
                notes = json.load(f)
        except Exception:
            notes = {}
        if not isinstance(notes, dict):
            notes = {}
        seen = notes.get(context)
        if not isinstance(seen, list):
            seen = []
        fresh = [a for a in alerts if a["window"] not in seen]
        if not fresh:
            return

        # Record before printing: a failed write means no output, never a repeat.
        os.makedirs(notes_dir, exist_ok=True)
        notes[context] = seen + [a["window"] for a in fresh]
        _atomic_write(notes_dir, notes_file, notes)
        _print_line(
            {"hookSpecificOutput": {"hookEventName": event, "additionalContext": _note_text(fresh, fetched_ts)}}
        )
    except Exception:
        return


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
    claims = []
    try:
        result = _wait(payload, tokitty_dir, claims, **kwargs)
        if result is None:
            return
        behavior = result.get("behavior")
        if behavior == "allow":
            _emit_decision({"behavior": "allow"})
        elif behavior == "always":
            # Recomputed from stdin: the decision file never supplies the rule.
            rule = _narrow_rule(payload.get("tool_name"), payload.get("tool_input"))
            if rule is not None:
                _emit_decision(
                    {
                        "behavior": "allow",
                        "updatedPermissions": [
                            {
                                "type": "addRules",
                                "rules": [{"toolName": rule[0], "ruleContent": rule[1]}],
                                "behavior": "allow",
                                "destination": "session",
                            }
                        ],
                    }
                )
        elif behavior == "deny":
            _emit_decision({"behavior": "deny", "message": _DENY_MESSAGE})
    finally:
        # Held through output so a second process cannot answer the same prompt
        # while this one is still printing.
        for c in claims:
            _remove(c)


# TOKITTY_QUIET in the environment of an unattended session (a cron `claude -p`,
# a remote-control server) drops its permission events, so nobody's cat raises
# a flag and no Stream Dock prompt waits on an empty room. The session's other
# activity is still recorded.
_QUIET_EVENTS = frozenset({"Notification", "PermissionRequest"})


def _quiet():
    return os.environ.get("TOKITTY_QUIET", "").strip().lower() not in ("", "0", "false", "no")


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
    if payload.get("hook_event_name") in _QUIET_EVENTS and _quiet():
        return

    event = payload.get("hook_event_name")
    if not event or event not in _KNOWN_EVENTS:
        return

    if event not in _NOTE_ONLY_EVENTS:
        try:
            _write_state(sessions_dir, payload, session_id, event)
        except Exception:
            pass
    if event == "SessionEnd" and _safe_name(session_id):
        _remove(os.path.join(os.path.dirname(os.path.abspath(sessions_dir)), "usage_notes", f"{session_id}.json"))
    if event == "PermissionRequest":
        _run_permission(payload, sessions_dir)
    elif event in _NOTE_EVENTS:
        _usage_note(payload, sessions_dir, session_id, event)


def _write_state(sessions_dir, payload, session_id, event):
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
    except BaseException:
        # KeyboardInterrupt and SystemExit included: never a non-zero exit.
        pass
