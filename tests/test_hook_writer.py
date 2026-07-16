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

import pytest

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
