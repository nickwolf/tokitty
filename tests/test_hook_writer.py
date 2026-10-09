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

    def test_codex_events_are_recorded(self, tmp_path):
        for event in ("PermissionRequest", "Interrupt"):
            sessions = tmp_path / event
            payload = json.dumps(
                {"session_id": "sess-1", "hook_event_name": event}
            ).encode()
            result = run_hook(payload, ["--sessions-dir", str(sessions)])
            assert result.returncode == 0
            assert result.stdout == b""
            data = json.loads(state_path(sessions, "sess-1").read_text())
            assert data["event"] == event
            assert data["seq"] == 1

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
            mono_fn=self.clock.now,
            sleep_fn=self.clock.sleep,
            rand_fn=lambda: NONCE,
            **kw,
        )

    def run(self, **kw):
        return hw._run_permission(
            self.payload,
            str(self.sessions),
            now_fn=self.clock.now,
            mono_fn=self.clock.now,
            sleep_fn=self.clock.sleep,
            rand_fn=lambda: NONCE,
            **kw,
        )

    @property
    def claim_files(self):
        d = self.tokitty / "pending"
        return list(d.glob("*.claim")) if d.exists() else []


def tool_use(tid, name="Bash", inp=None, ts=None):
    return {
        **({} if ts is None else {"timestamp": ts}),
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


class TestQuiet:
    def run_quiet(self, tmp_path, event, value):
        env = dict(os.environ, TOKITTY_QUIET=value)
        env.pop("PYTHONPATH", None)
        payload = json.dumps({"session_id": "sess-q", "hook_event_name": event, "tool_name": "Bash",
                              "tool_input": {"command": "ls"}}).encode()
        (tmp_path.parent / "streamdock.enabled").touch()
        return subprocess.run([sys.executable, SCRIPT, "--sessions-dir", str(tmp_path)], input=payload,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=10)

    @pytest.mark.parametrize("event", ["PermissionRequest", "Notification"])
    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_permission_events_dropped(self, tmp_path, event, value):
        result = self.run_quiet(tmp_path, event, value)
        assert result.returncode == 0 and result.stdout == b""
        assert not state_path(tmp_path, "sess-q").exists()
        assert not (tmp_path.parent / "pending").exists()

    def test_other_events_still_recorded(self, tmp_path):
        result = self.run_quiet(tmp_path, "PreToolUse", "1")
        assert result.returncode == 0
        assert json.loads(state_path(tmp_path, "sess-q").read_text())["event"] == "PreToolUse"

    @pytest.mark.parametrize("value", ["", "0", "false", "No"])
    def test_off_values_keep_the_flag(self, tmp_path, value):
        self.run_quiet(tmp_path, "Notification", value)
        assert json.loads(state_path(tmp_path, "sess-q").read_text())["event"] == "Notification"


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
        assert seen["always_rule"] == "Bash(ls -la)"
        assert seen["cwd"] == "/work"
        assert seen["started"] == T0
        assert seen["pid"] == os.getpid()

    def test_pending_always_rule_is_null_without_a_narrow_rule(self, env):
        star = {"command": "rm *.log"}
        env.write_transcript(tool_use("toolu_A", inp=star))
        env.payload["tool_input"] = star
        seen = {}

        def peek(clock):
            seen.update(json.loads(env.pending.read_text()))
            env.decide(digest=digest_of(star))

        env.clock.on_sleep = peek
        env.wait()
        assert "always_rule" in seen and seen["always_rule"] is None

    def test_always_decision_emits_session_scoped_rule(self, env, capsys):
        env.decide(behavior="always", rule={"toolName": "Bash", "ruleContent": "evil"})
        env.run()
        out = json.loads(capsys.readouterr().out)
        assert out == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "allow",
                    "updatedPermissions": [
                        {
                            "type": "addRules",
                            "rules": [{"toolName": "Bash", "ruleContent": "ls -la"}],
                            "behavior": "allow",
                            "destination": "session",
                        }
                    ],
                },
            }
        }

    def test_always_decision_without_rule_prints_nothing_then_allow_works(self, env, capsys):
        star = {"command": "rm *.log"}
        env.write_transcript(tool_use("toolu_A", inp=star))
        env.payload["tool_input"] = star
        digest = digest_of(star)
        env.decide(behavior="always", digest=digest)

        def second(clock):
            if len(clock.sleeps) == 2:
                env.decide(digest=digest)

        env.clock.on_sleep = second
        assert env.wait() == {"behavior": "allow"}
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize(
        "over",
        [
            {"digest": "0" * 64},
            {"session_id": "other"},
            {"nonce": "ffff"},
            {"behavior": "sometimes"},
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
        env.decide(behavior="sometimes")

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
        tail, size = hw._read_tail(str(env.transcript))
        assert size == env.transcript.stat().st_size
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

    def test_lookup_keeps_trying_for_five_seconds_100ms_apart(self, env):
        env.write_transcript(tool_use("toolu_X", inp={"command": "other"}))
        env.wait()
        assert env.clock.sleeps == [0.1] * 49

    def test_lookup_finds_line_written_after_400ms(self, env):
        # Claude Code 2.1.287 writes the tool_use about 0.4 s after the hook starts.
        env.write_transcript()

        def late(clock):
            if len(clock.sleeps) == 5:
                env.append_transcript(tool_use("toolu_A"))
                env.decide()

        env.clock.on_sleep = late
        assert env.wait() == {"behavior": "allow"}

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


class TestSubagentTranscript:
    def test_main_session_uses_transcript_path(self):
        assert hw._transcript_path({"transcript_path": "/p/s.jsonl"}) == "/p/s.jsonl"

    def test_subagent_reads_its_own_transcript(self):
        got = hw._transcript_path({"transcript_path": "/p/s.jsonl", "agent_id": "a2e07e30b37670bb8"})
        assert got == os.path.join("/p/s", "subagents", "agent-a2e07e30b37670bb8.jsonl")

    @pytest.mark.parametrize("agent_id", ["", "../x", "a/b", 5, "a b"])
    def test_bad_agent_id_gives_up(self, agent_id):
        assert hw._transcript_path({"transcript_path": "/p/s.jsonl", "agent_id": agent_id}) is None

    def test_subagent_without_jsonl_suffix_gives_up(self):
        assert hw._transcript_path({"transcript_path": "/p/s", "agent_id": "abc"}) is None


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
        # The activity state is written before the wait starts, not after it.
        state = json.loads((tdir / "sessions" / "sess-1.json").read_text())
        assert state["event"] == "PermissionRequest" and state["tool_name"] == "Bash"
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

    def test_subagent_allow_round_trip(self, tmp_path):
        import time as _t

        payload = self._payload(tmp_path)
        # The main transcript only mentions the command; the call lives in the subagent's file.
        (tmp_path / "t.jsonl").write_text("")
        sub = tmp_path / "t" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-abc123.jsonl").write_text(json.dumps(tool_use("toolu_SUB")) + "\n")
        payload["agent_id"] = "abc123"
        payload["agent_type"] = "general-purpose"
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
        assert json.loads(files[0].read_text())["tool_use_id"] == "toolu_SUB"
        nonce = files[0].stem
        (tdir / "decisions").mkdir()
        (tdir / "decisions" / f"{nonce}.json").write_text(
            json.dumps({"nonce": nonce, "session_id": "sess-1", "digest": digest_of(TOOL_INPUT), "behavior": "allow"})
        )
        out = proc.stdout.read()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()
        assert json.loads(out)["hookSpecificOutput"]["decision"] == {"behavior": "allow"}


def iso(epoch):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


class TestReviewGaps:
    def test_read_failure_after_lookup_is_silent(self, env, capsys):
        env.decide()

        def boom(path, offset):
            raise OSError("gone")

        assert env.wait(read_from_fn=boom) is None
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    def test_read_failure_via_run_prints_nothing(self, env, capsys):
        env.decide()
        env.transcript.unlink()
        env.run()
        assert capsys.readouterr().out == ""
        assert pending_files(env) == [] and env.claim_files == []

    def test_huge_result_line_after_lookup(self, env, capsys):
        env.decide()

        # decision present from the start: the answer must be found on the first poll
        with open(env.transcript, "a") as f:
            f.write('{"x":"' + "a" * (hw._TAIL_BYTES * 2) + '","tool_use_id":"toolu_A"}\n')
        env.run()
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    def test_huge_result_scanned_in_chunks(self, env, monkeypatch):
        monkeypatch.setattr(hw, "_READ_CAP", 1000)
        env.decide()
        with open(env.transcript, "a") as f:
            f.write("a" * 5000 + '"tool_use_id":"toolu_A"\n')
        assert env.wait() is None

    def test_needle_split_across_reads(self, env):
        needle = '"tool_use_id":"toolu_A"'
        halves = [needle[:9], needle[9:]]
        env.decide()
        calls = []

        def reader(path, offset):
            calls.append(offset)
            if len(calls) == 1:
                return halves[0].encode()
            if len(calls) == 2:
                return halves[1].encode()
            return b""

        # the decision only arrives after the second poll, which read the second half
        env.decision.unlink()

        def later(clock):
            if len(clock.sleeps) == 2:
                env.decide()

        env.clock.on_sleep = later
        assert env.wait(read_from_fn=reader) is None

    def test_truncated_transcript_is_silent(self, env):
        env.decide()
        env.transcript.write_text("")
        assert env.wait() is None
        assert pending_files(env) == []

    def test_read_from_rejects_shrunk_file(self, tmp_path):
        f = tmp_path / "x"
        f.write_bytes(b"abc")
        with pytest.raises(OSError):
            hw._read_from(str(f), 10)
        assert hw._read_from(str(f), 1) == b"bc"

    def test_second_waiter_for_same_call_fails_closed(self, env):
        seen = {}

        def second(clock):
            if "r" in seen:
                return
            seen["claims"] = [p.name for p in env.claim_files]
            seen["r"] = env.wait()
            seen["pending_after"] = [p.name for p in pending_files(env)]
            env.decide()

        env.clock.on_sleep = second
        assert env.wait() == {"behavior": "allow"}
        assert seen["r"] is None
        assert len(seen["claims"]) == 1
        assert sorted(seen["pending_after"]) == sorted(seen["claims"] + [f"{NONCE}.json"])
        assert env.claim_files == []
        assert pending_files(env) == []

    def test_claim_name_is_hash_of_session_and_id(self, env):
        got = {}

        def peek(clock):
            got["c"] = [p.name for p in env.claim_files]
            env.decide()

        env.clock.on_sleep = peek
        env.wait()
        key = hashlib.sha256(b"sess-1\0toolu_A").hexdigest()[:32]
        assert got["c"] == [key + ".claim"]

    def test_older_identical_call_with_old_timestamp_ignored(self, env):
        env.write_transcript(tool_use("toolu_OLD", ts=iso(T0 - 61)))
        got = {}

        def step(clock):
            if env.pending.exists():
                got.update(json.loads(env.pending.read_text()))
                env.decide()
            elif len(clock.sleeps) == 2:
                env.append_transcript(tool_use("toolu_A", ts=iso(T0 + 0.2)))

        env.clock.on_sleep = step
        assert env.wait() == {"behavior": "allow"}
        assert got["tool_use_id"] == "toolu_A"
        assert env.clock.sleeps[:2] == [0.1, 0.1]

    def test_old_timestamp_alone_gives_no_pending(self, env, capsys):
        env.write_transcript(tool_use("toolu_OLD", ts=iso(T0 - 600)))
        env.decide()
        env.run()
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    @pytest.mark.parametrize("ts", [None, "garbage", 12, iso(T0 - 59)])
    def test_missing_unparseable_or_recent_timestamp_still_counts(self, env, ts):
        line = tool_use("toolu_A")
        if ts is not None:
            line["timestamp"] = ts
        env.write_transcript(line)
        env.decide()
        assert env.wait() == {"behavior": "allow"}

    def test_stale_marker_with_decision_prints_nothing(self, env, capsys):
        env.decide()
        ages = iter([0.0, 500.0])  # fresh on entry, stale at the first poll

        env.run(mtime_fn=lambda p: env.clock.t - next(ages))
        assert capsys.readouterr().out == ""
        assert pending_files(env) == []

    def test_cap_passed_with_decision_prints_nothing(self, env, capsys):
        env.decide()

        def jump(path, offset):
            env.clock.t += hw._PERM_CAP_S + 1
            env.enable()
            return b""

        env.run(read_from_fn=jump)
        assert capsys.readouterr().out == ""

    def test_monotonic_cap_survives_wall_clock_jump_back(self, env):
        mono = {"t": 0.0}

        def sleep(dt):
            mono["t"] += dt
            env.clock.t -= 1000  # wall clock runs backwards
            env.enable(mtime=env.clock.t)

        assert (
            hw.wait_for_decision(
                env.payload,
                str(env.tokitty),
                now_fn=env.clock.now,
                mono_fn=lambda: mono["t"],
                sleep_fn=sleep,
                rand_fn=lambda: NONCE,
                read_tail_fn=lambda p: (tool_use_text(), 0),
                read_from_fn=lambda p, o: b"",
            )
            is None
        )
        assert 589.9 <= mono["t"] <= 590.5

    def test_answer_between_decision_read_and_recheck(self, env, capsys):
        env.decide()
        n = {"reads": 0}

        def reader(path, offset):
            n["reads"] += 1
            if not env.decision.exists():  # decision already consumed
                return b'"tool_use_id":"toolu_A"'
            return b""

        env.run(read_from_fn=reader)
        assert capsys.readouterr().out == ""
        assert n["reads"] == 2

    def test_normal_allow_still_works_with_recheck(self, env):
        env.decide()
        assert env.wait() == {"behavior": "allow"}
        assert env.claim_files == []


def tool_use_text():
    return json.dumps(tool_use("toolu_A")) + "\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
class TestSigint:
    def test_sigint_exits_zero_silently_and_cleans_up(self, tmp_path):
        import time as _t

        transcript = tmp_path / "t.jsonl"
        transcript.write_text(json.dumps(tool_use("toolu_A")) + "\n")
        tdir = tmp_path / "tokitty"
        tdir.mkdir()
        (tdir / "streamdock.enabled").write_text("")
        payload = {
            "session_id": "sess-1",
            "transcript_path": str(transcript),
            "cwd": "/w",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": TOOL_INPUT,
        }
        proc = subprocess.Popen(
            [sys.executable, SCRIPT, "--sessions-dir", str(tdir / "sessions")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.stdin.write(json.dumps(payload).encode())
        proc.stdin.close()
        pend = tdir / "pending"
        deadline = _t.time() + 8
        while _t.time() < deadline and not list(pend.glob("*.json") if pend.exists() else []):
            _t.sleep(0.05)
        assert list(pend.glob("*.json")) and list(pend.glob("*.claim"))
        proc.send_signal(signal.SIGINT)
        out = proc.stdout.read()
        proc.wait(timeout=10)
        err = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        assert proc.returncode == 0
        assert out == b"" and err == b""
        assert list(pend.iterdir()) == []

    def test_main_guard_catches_base_exception(self):
        text = Path(SCRIPT).read_text()
        assert "except BaseException:" in text


class TestCaughtUpAndClaim:
    def reads(self, seq):
        """A read_from_fn that serves seq in order, then empty reads."""
        it = iter(seq)
        n = {"calls": 0}

        def reader(path, offset):
            n["calls"] += 1
            return next(it, b"")

        reader.n = n
        return reader

    def test_backlog_beyond_one_scan_with_answer_beyond_gives_no_output(self, env, capsys, monkeypatch):
        monkeypatch.setattr(hw, "_MAX_CHUNKS", 2)
        env.decide()
        seen = []
        env.clock.on_sleep = lambda c: seen.append(env.decision.exists())
        reader = self.reads([b"fill"] * 4 + [b'"tool_use_id":"toolu_A"'])
        env.run(read_from_fn=reader)
        assert capsys.readouterr().out == ""
        # the decision was not consumed while the scan was behind
        assert seen == [True, True]
        assert env.claim_files == [] and pending_files(env) == []

    def test_backlog_then_caught_up_returns_decision(self, env, monkeypatch):
        monkeypatch.setattr(hw, "_MAX_CHUNKS", 2)
        env.decide()
        reader = self.reads([b"fill"] * 3)
        assert env.wait(read_from_fn=reader) == {"behavior": "allow"}
        assert len(env.clock.sleeps) == 1

    def test_final_rescan_not_caught_up_holds_decision_until_caught_up(self, env, monkeypatch):
        monkeypatch.setattr(hw, "_MAX_CHUNKS", 1)
        env.decide()
        seen = []
        env.clock.on_sleep = lambda c: seen.append(env.decision.exists())
        # poll 1: empty (caught up) -> decision consumed; rescan: data (behind);
        # poll 2: empty (caught up) -> return the held decision
        reader = self.reads([b"", b"fill", b""])
        assert env.wait(read_from_fn=reader) == {"behavior": "allow"}
        assert seen == [False]

    def test_final_rescan_not_caught_up_then_answer_gives_none(self, env, monkeypatch):
        monkeypatch.setattr(hw, "_MAX_CHUNKS", 1)
        env.decide()
        reader = self.reads([b"", b"fill", b'"tool_use_id":"toolu_A"'])
        assert env.wait(read_from_fn=reader) is None
        assert not env.decision.exists()

    def test_final_rescan_not_caught_up_until_cap_gives_none(self, env, monkeypatch):
        monkeypatch.setattr(hw, "_MAX_CHUNKS", 1)
        env.decide()
        reader = self.reads([b""] + [b"fill"] * 100000)
        assert env.wait(read_from_fn=reader) is None

    def test_claim_held_through_emit_and_removed_after(self, env, monkeypatch, capsys):
        env.decide()
        seen = {}

        def fake_emit(decision):
            seen["claims"] = list(env.claim_files)
            seen["decision"] = decision

        monkeypatch.setattr(hw, "_emit_decision", fake_emit)
        env.run()
        assert len(seen["claims"]) == 1
        assert seen["decision"] == {"behavior": "allow"}
        assert env.claim_files == []
        assert pending_files(env) == []

    def test_claim_removed_when_emit_raises(self, env, monkeypatch):
        env.decide()

        def boom(decision):
            raise RuntimeError("x")

        monkeypatch.setattr(hw, "_emit_decision", boom)
        with pytest.raises(RuntimeError):
            env.run()
        assert env.claim_files == []

    @pytest.mark.parametrize("exc", [RuntimeError("x"), SystemExit(0)])
    @pytest.mark.parametrize("entry", ["wait", "run"])
    def test_claim_removed_when_wait_raises(self, env, exc, entry):
        def boom(clock):
            assert len(env.claim_files) == 1
            raise exc

        env.clock.on_sleep = boom
        with pytest.raises(type(exc)):
            getattr(env, entry)()
        assert env.claim_files == [] and pending_files(env) == []

    def test_cap_passing_between_decision_read_and_return_gives_none(self, env, capsys):
        env.decide()
        calls = {"n": 0}

        def reader(path, offset):
            calls["n"] += 1
            if calls["n"] == 2:  # the final re-scan
                env.clock.t += hw._PERM_CAP_S + 1
                env.enable()
            return b""

        env.run(read_from_fn=reader)
        assert capsys.readouterr().out == ""
        assert calls["n"] == 2
        assert env.claim_files == []

    def test_marker_going_stale_between_decision_read_and_return_gives_none(self, env, capsys):
        env.decide()
        flag = {"stale": False}

        def reader(path, offset):
            if not env.decision.exists():  # decision consumed: this is the re-scan
                flag["stale"] = True
            return b""

        env.run(
            read_from_fn=reader,
            mtime_fn=lambda p: env.clock.t - (500 if flag["stale"] else 0),
        )
        assert capsys.readouterr().out == ""
        assert env.claim_files == []


class TestNarrowRule:
    @pytest.mark.parametrize(
        "tool, tool_input, expected",
        [
            ("Bash", {"command": "ls -la"}, ("Bash", "ls -la")),
            ("Bash", {"command": " git  status "}, ("Bash", " git  status ")),
            ("Edit", {"file_path": "/home/n/a.py"}, ("Edit", "//home/n/a.py")),
            ("Write", {"file_path": "/home/n/my file.txt"}, ("Edit", "//home/n/my file.txt")),
            ("WebFetch", {"url": "https://Example.COM/x?q=1"}, ("WebFetch", "domain:example.com")),
            ("WebFetch", {"url": "http://a-b.example.org:8080/"}, ("WebFetch", "domain:a-b.example.org")),
        ],
    )
    def test_accepted(self, tool, tool_input, expected):
        assert hw._narrow_rule(tool, tool_input) == expected

    @pytest.mark.parametrize(
        "tool, tool_input",
        [
            ("Bash", {"command": "rm *"}),
            ("Bash", {"command": "a\nb"}),
            ("Bash", {"command": "a\rb"}),
            ("Bash", {"command": ""}),
            ("Bash", {"command": 5}),
            ("Bash", {}),
            ("Edit", {"file_path": "rel/a.py"}),
            ("Edit", {"file_path": "C:\\x\\a.py"}),
            ("Edit", {"file_path": "C:/x/a.py"}),
            ("Edit", {"file_path": "/a/*.py"}),
            ("Edit", {"file_path": "/a/b?.py"}),
            ("Edit", {"file_path": "/a/[b].py"}),
            ("Edit", {"file_path": "/a/{b,c}"}),
            ("Edit", {"file_path": "/a/!b"}),
            ("Edit", {"file_path": "/a/b\\c"}),
            ("Edit", {"file_path": "/a/../b"}),
            ("Edit", {"file_path": "/a/./b"}),
            ("Edit", {"file_path": "/a//b"}),
            ("Edit", {"file_path": "/a/b/"}),
            ("Edit", {"file_path": "/"}),
            ("Edit", {"file_path": "/a/b "}),
            ("Write", {"file_path": " /a/b"}),
            ("Write", {"file_path": None}),
            ("WebFetch", {"url": "ftp://example.com/x"}),
            ("WebFetch", {"url": "https:///path"}),
            ("WebFetch", {"url": "example.com"}),
            ("WebFetch", {"url": "https://exa_mple.com"}),
            ("WebFetch", {"url": "https://[::1]/"}),
            ("WebFetch", {"url": 5}),
            ("mcp__srv__tool", {"command": "ls"}),
            ("Read", {"file_path": "/a/b"}),
        ],
    )
    def test_rejected(self, tool, tool_input):
        assert hw._narrow_rule(tool, tool_input) is None

    @pytest.mark.parametrize("tool_input", [None, [], "ls", 5])
    def test_non_dict_input(self, tool_input):
        assert hw._narrow_rule("Bash", tool_input) is None

    @pytest.mark.parametrize("tool", [None, 5, ["Bash"]])
    def test_non_str_tool(self, tool):
        assert hw._narrow_rule(tool, {"command": "ls"}) is None
