"""Tests for the standalone tokitty/hook_writer.py stdlib hook script.

This script is invoked by Claude Code as a hook: it reads one JSON object
from stdin and writes/updates a per-session state file. It MUST behave as a
control-plane-safe script: never write to stdout, never exit non-zero,
regardless of input. These tests exercise the script via subprocess to
verify real process-level behavior (stdout bytes, exit code), not just
importable function behavior.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = str(Path(__file__).resolve().parent.parent / "tokitty" / "hook_writer.py")


def run_hook(stdin_bytes, args, cwd=None):
    """Run the hook script as a subprocess, return CompletedProcess."""
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        timeout=10,
    )


def state_path(sessions_dir, session_id):
    return Path(sessions_dir) / f"{session_id}.json"


def no_stray_temp_files(sessions_dir):
    """Assert the sessions dir contains only .json state files, no temp leftovers."""
    for entry in Path(sessions_dir).iterdir():
        assert entry.name.endswith(".json"), f"stray temp file left behind: {entry.name}"


class TestSafetyInvariants:
    def test_garbage_stdin_silent_and_zero_exit(self, tmp_path):
        result = run_hook(b"not json at all {{{", ["--sessions-dir", str(tmp_path)])
        assert result.stdout == b""
        assert result.returncode == 0
        no_stray_temp_files(tmp_path)

    def test_empty_stdin_silent_and_zero_exit(self, tmp_path):
        result = run_hook(b"", ["--sessions-dir", str(tmp_path)])
        assert result.stdout == b""
        assert result.returncode == 0
        no_stray_temp_files(tmp_path)

    def test_missing_sessions_dir_arg_silent_and_zero_exit(self, tmp_path):
        payload = json.dumps({"session_id": "abc", "hook_event_name": "Stop"}).encode()
        result = run_hook(payload, [])
        assert result.stdout == b""
        assert result.returncode == 0

    def test_readonly_sessions_dir_silent_and_zero_exit(self, tmp_path):
        target = tmp_path / "readonly"
        target.mkdir()
        payload = json.dumps({"session_id": "abc", "hook_event_name": "Stop"}).encode()
        os.chmod(target, 0o500)
        try:
            result = run_hook(payload, ["--sessions-dir", str(target)])
            assert result.stdout == b""
            assert result.returncode == 0
        finally:
            os.chmod(target, 0o700)

    def test_no_stderr_in_normal_operation(self, tmp_path):
        payload = json.dumps(
            {"session_id": "abc", "hook_event_name": "UserPromptSubmit"}
        ).encode()
        result = run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert result.stdout == b""
        assert result.returncode == 0
        assert result.stderr == b""

    def test_nonexistent_sessions_dir_is_created(self, tmp_path):
        target = tmp_path / "does" / "not" / "exist"
        payload = json.dumps({"session_id": "abc", "hook_event_name": "Stop"}).encode()
        result = run_hook(payload, ["--sessions-dir", str(target)])
        assert result.stdout == b""
        assert result.returncode == 0
        assert target.is_dir()


class TestNormalBehavior:
    def test_writes_expected_fields(self, tmp_path):
        payload = json.dumps(
            {
                "session_id": "sess-1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
            }
        ).encode()
        result = run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert result.returncode == 0
        p = state_path(tmp_path, "sess-1")
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["session_id"] == "sess-1"
        assert data["event"] == "PreToolUse"
        assert data["tool_name"] == "Bash"
        assert data["seq"] == 1
        assert "ts" in data
        assert "agent_id" not in data
        no_stray_temp_files(tmp_path)

    def test_agent_id_recorded_when_present(self, tmp_path):
        payload = json.dumps(
            {
                "session_id": "sess-1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "agent_id": "agent-42",
                "agent_type": "general-purpose",
            }
        ).encode()
        run_hook(payload, ["--sessions-dir", str(tmp_path)])
        data = json.loads(state_path(tmp_path, "sess-1").read_text())
        assert data["agent_id"] == "agent-42"

    def test_seq_increments_across_invocations(self, tmp_path):
        for i in range(3):
            payload = json.dumps(
                {"session_id": "sess-1", "hook_event_name": "PostToolUse"}
            ).encode()
            run_hook(payload, ["--sessions-dir", str(tmp_path)])
        data = json.loads(state_path(tmp_path, "sess-1").read_text())
        assert data["seq"] == 3
        no_stray_temp_files(tmp_path)

    def test_corrupt_existing_state_file_resets_seq(self, tmp_path):
        p = state_path(tmp_path, "sess-1")
        p.write_text("{not valid json")
        payload = json.dumps(
            {"session_id": "sess-1", "hook_event_name": "PostToolUse"}
        ).encode()
        result = run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert result.returncode == 0
        assert result.stdout == b""
        data = json.loads(p.read_text())
        assert data["seq"] == 1
        no_stray_temp_files(tmp_path)

    def test_session_end_deletes_file(self, tmp_path):
        payload = json.dumps(
            {"session_id": "sess-1", "hook_event_name": "UserPromptSubmit"}
        ).encode()
        run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert state_path(tmp_path, "sess-1").exists()

        end_payload = json.dumps(
            {"session_id": "sess-1", "hook_event_name": "SessionEnd"}
        ).encode()
        result = run_hook(end_payload, ["--sessions-dir", str(tmp_path)])
        assert result.returncode == 0
        assert result.stdout == b""
        assert not state_path(tmp_path, "sess-1").exists()
        no_stray_temp_files(tmp_path)

    def test_session_end_missing_file_is_fine(self, tmp_path):
        payload = json.dumps(
            {"session_id": "never-existed", "hook_event_name": "SessionEnd"}
        ).encode()
        result = run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert result.returncode == 0
        assert result.stdout == b""
        no_stray_temp_files(tmp_path)

    def test_unknown_event_does_nothing(self, tmp_path):
        payload = json.dumps(
            {"session_id": "sess-1", "hook_event_name": "SomeUnknownEvent"}
        ).encode()
        result = run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert result.returncode == 0
        assert result.stdout == b""
        assert not state_path(tmp_path, "sess-1").exists()

    def test_missing_hook_event_name_does_nothing(self, tmp_path):
        payload = json.dumps({"session_id": "sess-1"}).encode()
        result = run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert result.returncode == 0
        assert not state_path(tmp_path, "sess-1").exists()

    def test_missing_session_id_does_nothing(self, tmp_path):
        payload = json.dumps({"hook_event_name": "Stop"}).encode()
        result = run_hook(payload, ["--sessions-dir", str(tmp_path)])
        assert result.returncode == 0
        assert result.stdout == b""
        # nothing should be created at all
        assert list(tmp_path.iterdir()) == []

    def test_runs_from_any_cwd_no_package_import(self, tmp_path):
        """Script must work standalone, invoked from any cwd, no tokitty package on path."""
        other_cwd = tmp_path / "elsewhere"
        other_cwd.mkdir()
        sessions_dir = tmp_path / "sessions"
        payload = json.dumps(
            {"session_id": "sess-1", "hook_event_name": "Stop"}
        ).encode()
        result = subprocess.run(
            [sys.executable, SCRIPT, "--sessions-dir", str(sessions_dir)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(other_cwd),
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_script_has_no_tokitty_package_import(self):
        text = Path(SCRIPT).read_text()
        assert "from tokitty" not in text
        assert "import tokitty" not in text

    def test_atomicity_no_stray_temp_files_after_many_runs(self, tmp_path):
        for i in range(5):
            payload = json.dumps(
                {
                    "session_id": "sess-1",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                }
            ).encode()
            run_hook(payload, ["--sessions-dir", str(tmp_path)])
        no_stray_temp_files(tmp_path)


# ---------------------------------------------------------------------------
# PermissionRequest wait (Stream Dock)
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402
import signal  # noqa: E402

import pytest  # noqa: E402

from tokitty import hook_writer as hw  # noqa: E402

T0 = 1_000_000.0
TOOL_INPUT = {"command": "ls -la", "description": "list"}
NONCE = "0123456789abcdef"


def digest_of(tool_input):
    blob = json.dumps(tool_input, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


class Clock:
    def __init__(self, t=T0):
        self.t = t
        self.sleeps = []
        self.on_sleep = None

    def now(self):
        return self.t

    def sleep(self, dt):
        self.sleeps.append(dt)
        self.t += dt
        if self.on_sleep:
            self.on_sleep(self)


class Env:
    def __init__(self, tmp_path):
        self.tokitty = tmp_path / "tokitty"
        self.sessions = self.tokitty / "sessions"
        self.tokitty.mkdir()
        self.transcript = tmp_path / "t.jsonl"
        self.clock = Clock()
        self.payload = {
            "session_id": "sess-1",
            "transcript_path": str(self.transcript),
            "cwd": "/work",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": dict(TOOL_INPUT),
        }

    @property
    def marker(self):
        return self.tokitty / "streamdock.enabled"

    def enable(self, mtime=None):
        self.marker.write_text("")
        t = self.clock.t if mtime is None else mtime
        os.utime(self.marker, (t, t))

    @property
    def pending(self):
        return self.tokitty / "pending" / f"{NONCE}.json"

    @property
    def decision(self):
        return self.tokitty / "decisions" / f"{NONCE}.json"

    def write_transcript(self, *lines):
        text = "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n"
        self.transcript.write_text(text)

    def append_transcript(self, line):
        with open(self.transcript, "a") as f:
            f.write(json.dumps(line) + "\n")

    def decide(self, **over):
        d = {
            "nonce": NONCE,
            "session_id": "sess-1",
            "digest": digest_of(TOOL_INPUT),
            "behavior": "allow",
            "decided_at": 1,
        }
        d.update(over)
        self.decision.parent.mkdir(exist_ok=True)
        self.decision.write_text(d if isinstance(d, str) else json.dumps(d))

    def wait(self, **kw):
        return hw.wait_for_decision(
            self.payload,
            str(self.tokitty),
            now_fn=self.clock.now,
            sleep_fn=self.clock.sleep,
            rand_fn=lambda: NONCE,
            **kw,
        )

    def run(self, **kw):
        return hw._run_permission(
            self.payload,
            str(self.sessions),
            now_fn=self.clock.now,
            sleep_fn=self.clock.sleep,
            rand_fn=lambda: NONCE,
            **kw,
        )


def tool_use(tid, name="Bash", inp=None):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": tid, "name": name, "input": TOOL_INPUT if inp is None else inp},
            ]
        },
    }


def tool_result(tid):
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": "x"}]},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(hw.signal, "signal", lambda *a: None)
    e = Env(tmp_path)
    e.enable()
    e.write_transcript(tool_use("toolu_A"))
    return e


def pending_files(e):
    d = e.tokitty / "pending"
    return list(d.iterdir()) if d.exists() else []


class TestPermissionDisabled:
    def test_missing_marker(self, env, capsys):
        env.marker.unlink()
        env.run()
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    def test_stale_marker(self, env, capsys):
        env.enable(mtime=T0 - 120)
        env.run()
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    def test_marker_just_fresh_is_accepted(self, env):
        env.enable(mtime=T0 - 119)
        env.decide()
        assert env.wait() == {"behavior": "allow"}


class TestPermissionDecisions:
    def test_matching_allow_prints_exact_shape_and_cleans_up(self, env, capsys):
        env.decide()
        env.run()
        out = capsys.readouterr().out
        assert out.endswith("\n") and out.count("\n") == 1
        assert json.loads(out) == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
        assert not env.pending.exists()
        assert not env.decision.exists()

    def test_matching_deny_prints_message(self, env, capsys):
        env.decide(behavior="deny")
        env.run()
        assert json.loads(capsys.readouterr().out) == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "message": "Denied from Stream Dock"},
            }
        }

    def test_decision_arriving_later_is_applied(self, env):
        def later(clock):
            if len(clock.sleeps) == 3:
                env.decide()

        env.clock.on_sleep = later
        assert env.wait() == {"behavior": "allow"}

    def test_pending_file_contents(self, env):
        seen = {}

        def peek(clock):
            seen.update(json.loads(env.pending.read_text()))
            env.decide()

        env.clock.on_sleep = peek
        env.wait()
        assert seen["v"] == 1
        assert seen["nonce"] == NONCE
        assert seen["session_id"] == "sess-1"
        assert seen["tool_use_id"] == "toolu_A"
        assert seen["tool_name"] == "Bash"
        assert seen["tool_input"] == TOOL_INPUT
        assert seen["digest"] == digest_of(TOOL_INPUT)
        assert seen["preview"] == "ls -la"
        assert seen["cwd"] == "/work"
        assert seen["started"] == T0
        assert seen["pid"] == os.getpid()

    @pytest.mark.parametrize(
        "over",
        [
            {"digest": "0" * 64},
            {"session_id": "other"},
            {"nonce": "ffff"},
            {"behavior": "always"},
            {"behavior": "ALLOW"},
            {"behavior": None},
            {"digest": None},
        ],
    )
    def test_mismatched_decision_ignored_deleted_and_wait_continues(self, env, capsys, over):
        env.decide(**over)
        seen = []

        def check(clock):
            seen.append(env.decision.exists())

        def keep_marker(clock):
            check(clock)
            env.enable()

        env.clock.on_sleep = keep_marker
        env.run()
        assert capsys.readouterr().out == ""
        assert seen and seen[0] is False
        assert env.clock.t - T0 >= hw._PERM_CAP_S

    def test_missing_field_ignored(self, env, capsys):
        env.decision.parent.mkdir()
        env.decision.write_text(json.dumps({"nonce": NONCE, "behavior": "allow"}))
        env.run()
        assert capsys.readouterr().out == ""
        assert not env.decision.exists()

    @pytest.mark.parametrize("raw", ["{not json", "[]", '"allow"', "null", ""])
    def test_garbage_decision_ignored(self, env, capsys, raw):
        env.decision.parent.mkdir()
        env.decision.write_text(raw)
        env.run()
        assert capsys.readouterr().out == ""
        assert not env.decision.exists()

    def test_bad_decision_then_good_decision(self, env):
        env.decide(behavior="always")

        def second(clock):
            if len(clock.sleeps) == 2:
                env.decide()

        env.clock.on_sleep = second
        assert env.wait() == {"behavior": "allow"}

    def test_decision_digest_compared_to_stdin_not_pending_file(self, env):
        # a decision carrying a digest for some other input must not match,
        # even if it equals what a tampered pending file would claim
        other = digest_of({"command": "rm -rf /"})

        def tamper(clock):
            p = json.loads(env.pending.read_text())
            p["digest"] = other
            env.pending.write_text(json.dumps(p))
            env.decide(digest=other)

        env.clock.on_sleep = tamper
        assert env.wait() is None

    def test_decision_wins_nothing_when_answered_elsewhere_first(self, env, capsys):
        def both(clock):
            if len(clock.sleeps) == 1:
                env.append_transcript(tool_result("toolu_A"))
                env.decide()

        env.clock.on_sleep = both
        env.run()
        assert capsys.readouterr().out == ""
        assert not env.decision.exists()


class TestPermissionStops:
    def test_tool_result_ends_wait_silently(self, env, capsys):
        def answered(clock):
            if len(clock.sleeps) == 2:
                env.append_transcript(tool_result("toolu_A"))

        env.clock.on_sleep = answered
        env.run()
        assert capsys.readouterr().out == ""
        assert len(env.clock.sleeps) == 2
        assert not env.pending.exists()

    def test_oversized_result_line_still_detected(self, env):
        # a tool_result line larger than the tail is unparseable; the raw key still shows
        def answered(clock):
            if len(clock.sleeps) == 1:
                with open(env.transcript, "a") as f:
                    f.write('"x", "tool_use_id":"toolu_A", "content":"..."}]}}\n')

        env.clock.on_sleep = answered
        assert env.wait() is None

    def test_marker_going_stale_mid_wait(self, env, capsys):
        def stale(clock):
            if len(clock.sleeps) == 3:
                env.enable(mtime=clock.t - 500)

        env.clock.on_sleep = stale
        env.run()
        assert capsys.readouterr().out == ""
        assert len(env.clock.sleeps) == 3
        assert not env.pending.exists()

    def test_marker_removed_mid_wait(self, env):
        def gone(clock):
            if len(clock.sleeps) == 2:
                env.marker.unlink()

        env.clock.on_sleep = gone
        assert env.wait() is None

    def test_590_second_cap(self, env, capsys):
        def keep_marker(clock):
            env.enable()

        env.clock.on_sleep = keep_marker
        env.run()
        assert capsys.readouterr().out == ""
        assert 589.9 <= env.clock.t - T0 <= 590.5
        assert not env.pending.exists()

    def test_pending_removed_when_loop_raises(self, env, capsys):
        def boom(clock):
            assert env.pending.exists()
            raise RuntimeError("boom")

        env.clock.on_sleep = boom
        with pytest.raises(RuntimeError):
            env.run()
        assert capsys.readouterr().out == ""
        assert not env.pending.exists()

    def test_pending_removed_on_system_exit(self, env):
        def term(clock):
            raise SystemExit(0)

        env.clock.on_sleep = term
        with pytest.raises(SystemExit):
            env.wait()
        assert not env.pending.exists()

    def test_heartbeat_roughly_every_five_seconds(self, env):
        beats = []

        def utime(path, times):
            beats.append(env.clock.t)
            os.utime(path, times)

        def run_on(clock):
            env.enable()
            if clock.t - T0 >= 21:
                env.decide()

        env.clock.on_sleep = run_on
        assert env.wait(utime_fn=utime) == {"behavior": "allow"}
        assert 3 <= len(beats) <= 5
        gaps = [b - a for a, b in zip([T0] + beats, beats)]
        assert all(5 <= g < 5.5 for g in gaps)

    def test_heartbeat_failure_is_swallowed(self, env):
        def utime(path, times):
            raise OSError("nope")

        def run_on(clock):
            env.enable()
            if clock.t - T0 >= 6:
                env.decide()

        env.clock.on_sleep = run_on
        assert env.wait(utime_fn=utime) == {"behavior": "allow"}


class TestToolUseLookup:
    def test_older_resolved_identical_call_is_ignored(self, env):
        env.write_transcript(
            tool_use("toolu_OLD"), tool_result("toolu_OLD"), tool_use("toolu_NEW")
        )
        env.decide()
        seen = {}

        def peek(clock):
            seen.update(json.loads(env.pending.read_text()))

        env.clock.on_sleep = peek
        assert env.wait() == {"behavior": "allow"}
        assert seen == {} or seen["tool_use_id"] == "toolu_NEW"

    def test_id_recorded_for_new_call(self, env):
        env.write_transcript(
            tool_use("toolu_OLD"), tool_result("toolu_OLD"), tool_use("toolu_NEW")
        )
        got = {}

        def peek(clock):
            got.update(json.loads(env.pending.read_text()))
            env.decide()

        env.clock.on_sleep = peek
        env.wait()
        assert got["tool_use_id"] == "toolu_NEW"

    def test_truncated_first_line_and_odd_shapes_tolerated(self, env):
        env.write_transcript(
            'ntent":[{"type":"tool_use","id":"toolu_Z","name":"Bash","input":{}}]}}',
            {"type": "user", "message": {"content": "plain string"}},
            {"type": "system"},
            "[1, 2]",
            "null",
            {"type": "assistant", "message": "str"},
            tool_use("toolu_A"),
        )
        got = {}

        def peek(clock):
            got.update(json.loads(env.pending.read_text()))
            env.decide()

        env.clock.on_sleep = peek
        assert env.wait() == {"behavior": "allow"}
        assert got["tool_use_id"] == "toolu_A"

    def test_tail_is_cut_to_256k_without_partial_line(self, env):
        filler = {"type": "user", "message": {"content": "x" * 1000}}
        lines = [tool_use("toolu_A")] + [filler] * 600
        lines.append(tool_use("toolu_B"))
        env.write_transcript(*lines)
        assert env.transcript.stat().st_size > hw._TAIL_BYTES
        tail = hw._read_tail(str(env.transcript))
        assert len(tail.encode()) <= hw._TAIL_BYTES
        assert all(json.loads(ln) for ln in tail.splitlines())
        assert "toolu_A" not in tail and "toolu_B" in tail

    def test_same_id_repeated_counts_once(self, env):
        env.write_transcript(tool_use("toolu_A"), tool_use("toolu_A"))
        env.decide()
        assert env.wait() == {"behavior": "allow"}

    def test_other_tool_or_input_does_not_match(self, env, capsys):
        env.write_transcript(
            tool_use("toolu_1", name="Edit"), tool_use("toolu_2", inp={"command": "ls"})
        )
        env.run()
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    @pytest.mark.parametrize("mode", ["missing", "nomatch", "two"])
    def test_failed_lookup_is_silent_with_no_pending_file(self, env, capsys, mode):
        if mode == "missing":
            env.transcript.unlink()
        elif mode == "nomatch":
            env.write_transcript(tool_use("toolu_X", inp={"command": "other"}))
        else:
            env.write_transcript(tool_use("toolu_1"), tool_use("toolu_2"))
        env.decide()
        env.run()
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    def test_lookup_retries_five_times_100ms_apart(self, env):
        env.write_transcript(tool_use("toolu_X", inp={"command": "other"}))
        env.wait()
        assert env.clock.sleeps == [0.1] * 4

    def test_lookup_finds_line_written_late(self, env):
        env.write_transcript()

        def late(clock):
            if len(clock.sleeps) == 2:
                env.append_transcript(tool_use("toolu_A"))
                env.decide()

        env.clock.on_sleep = late
        assert env.wait() == {"behavior": "allow"}

    def test_non_dict_tool_input_is_silent(self, env):
        env.payload["tool_input"] = "ls"
        assert env.wait() is None
        assert pending_files(env) == []

    def test_unicode_input_digest_matches_transcript(self, env):
        inp = {"command": "echo héllo ☃"}
        env.payload["tool_input"] = inp
        env.write_transcript(tool_use("toolu_A", inp=inp))
        env.decide(digest=digest_of(inp))
        assert env.wait() == {"behavior": "allow"}


class TestPreview:
    @pytest.mark.parametrize(
        "tool,inp,expected",
        [
            ("Bash", {"command": "ls"}, "ls"),
            ("Edit", {"file_path": "/a/b", "old_string": "x"}, "/a/b"),
            ("Write", {"file_path": "/a/c"}, "/a/c"),
            ("Read", {"file_path": "/a/d"}, "/a/d"),
            ("WebFetch", {"url": "https://x.test"}, "https://x.test"),
            ("Grep", {"pattern": "a"}, '{"pattern":"a"}'),
            ("Bash", {"command": 5}, '{"command":5}'),
        ],
    )
    def test_preview(self, tool, inp, expected):
        assert hw._preview(tool, inp) == expected

    def test_preview_cut_to_200(self):
        assert len(hw._preview("Bash", {"command": "x" * 500})) == 200


class TestPermissionIsolation:
    def test_permission_request_never_touches_sessions_dir(self, env):
        env.decide()
        env.run()
        assert not env.sessions.exists()

    def test_existing_session_file_untouched(self, env):
        env.sessions.mkdir()
        f = env.sessions / "sess-1.json"
        f.write_text('{"seq": 7}')
        before = f.stat().st_mtime_ns
        env.decide()
        env.run()
        assert f.read_text() == '{"seq": 7}'
        assert f.stat().st_mtime_ns == before
        assert [p.name for p in env.sessions.iterdir()] == ["sess-1.json"]

    def test_other_events_still_write_state_and_have_no_pending(self, tmp_path):
        sessions = tmp_path / "tokitty" / "sessions"
        payload = json.dumps(
            {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash"}
        ).encode()
        result = run_hook(payload, ["--sessions-dir", str(sessions)])
        assert result.stdout == b"" and result.returncode == 0
        assert json.loads((sessions / "s.json").read_text())["seq"] == 1
        assert not (tmp_path / "tokitty" / "pending").exists()


class TestSignals:
    def test_handlers_installed_for_permission_only(self, env, monkeypatch):
        calls = []
        monkeypatch.setattr(hw.signal, "signal", lambda s, h: calls.append((s, h)))
        env.marker.unlink()
        env.run()
        assert (signal.SIGTERM, hw._raise_exit) in calls
        if hasattr(signal, "SIGHUP"):
            assert (signal.SIGHUP, hw._raise_exit) in calls
        calls.clear()
        monkeypatch.setattr(hw.sys, "argv", ["x", "--sessions-dir", str(env.sessions)])
        monkeypatch.setattr(
            hw.sys,
            "stdin",
            type("S", (), {"buffer": type("B", (), {"read": lambda s: b'{"session_id":"a","hook_event_name":"Stop"}'})()})(),
        )
        hw.main()
        assert calls == []

    def test_handler_raises_system_exit(self):
        with pytest.raises(SystemExit):
            hw._raise_exit(signal.SIGTERM, None)

    def test_signal_setup_failure_is_swallowed(self, env, monkeypatch):
        def bad(*a):
            raise ValueError("not main thread")

        monkeypatch.setattr(hw.signal, "signal", bad)
        env.decide()
        assert env.wait() == {"behavior": "allow"}
        hw._install_exit_handlers()


class TestEmit:
    def test_stdout_none_falls_back_to_fd1(self, monkeypatch):
        written = []
        monkeypatch.setattr(hw.sys, "stdout", None)
        monkeypatch.setattr(hw.os, "write", lambda fd, data: written.append((fd, data)))
        hw._emit_decision({"behavior": "allow"})
        assert len(written) == 1 and written[0][0] == 1
        assert json.loads(written[0][1]) == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }

    def test_fd1_failure_is_swallowed(self, monkeypatch):
        def bad(fd, data):
            raise OSError("closed")

        monkeypatch.setattr(hw.sys, "stdout", None)
        monkeypatch.setattr(hw.os, "write", bad)
        hw._emit_decision({"behavior": "allow"})

    def test_only_one_print_site(self):
        text = Path(SCRIPT).read_text()
        assert text.count("sys.stdout.write") == 1
        assert "print(" not in text.replace("never prints", "").replace("print a", "")


class TestPermissionEndToEnd:
    def _launch(self, tmp_path, transcript, payload):
        sessions = tmp_path / "tokitty" / "sessions"
        return subprocess.Popen(
            [sys.executable, SCRIPT, "--sessions-dir", str(sessions)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ), json.dumps(payload).encode()

    def _payload(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(json.dumps(tool_use("toolu_A")) + "\n")
        return {
            "session_id": "sess-1",
            "transcript_path": str(transcript),
            "cwd": "/w",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": TOOL_INPUT,
        }

    def test_disabled_is_silent_exit_zero(self, tmp_path):
        payload = self._payload(tmp_path)
        (tmp_path / "tokitty").mkdir()
        proc, data = self._launch(tmp_path, None, payload)
        out, err = proc.communicate(data, timeout=10)
        assert out == b"" and err == b"" and proc.returncode == 0

    def test_allow_round_trip(self, tmp_path):
        import time as _t

        payload = self._payload(tmp_path)
        tdir = tmp_path / "tokitty"
        tdir.mkdir()
        (tdir / "streamdock.enabled").write_text("")
        proc, data = self._launch(tmp_path, None, payload)
        proc.stdin.write(data)
        proc.stdin.close()
        pend = tdir / "pending"
        deadline = _t.time() + 8
        files = []
        while _t.time() < deadline and not files:
            files = [p for p in pend.glob("*.json")] if pend.exists() else []
            _t.sleep(0.05)
        assert files, "pending file never appeared"
        nonce = files[0].stem
        (tdir / "decisions").mkdir()
        (tdir / "decisions" / f"{nonce}.json").write_text(
            json.dumps(
                {
                    "nonce": nonce,
                    "session_id": "sess-1",
                    "digest": digest_of(TOOL_INPUT),
                    "behavior": "allow",
                }
            )
        )
        out = proc.stdout.read()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()
        assert proc.returncode == 0
        assert json.loads(out)["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
        assert list(pend.iterdir()) == []
