"""Tests for tokitty.activity_watcher — the watcher thread that reads
hook-writer session files and publishes ActivityView snapshots."""

import threading
import time
from pathlib import Path

from tokitty.activity import ActivityTracker, ActivityView
from tokitty.activity_watcher import ActivityWatcher, FAST_INTERVAL_S, SLOW_INTERVAL_S


def rec(event, ts, seq=1, tool_name=None):
    d = {"session_id": "s1", "event": event, "seq": seq, "ts": ts}
    if tool_name is not None:
        d["tool_name"] = tool_name
    return d


class FakeFs:
    """In-memory stand-in for the sessions dir: session_id -> record dict
    (or a raw string, to simulate unparseable garbage)."""

    def __init__(self):
        self.files = {}
        self.deleted = []

    def list_files_fn(self, sessions_dir):
        return [Path(f"{sid}.json") for sid in sorted(self.files.keys())]

    def read_file_fn(self, path):
        import json

        sid = Path(path).stem
        content = self.files[sid]
        return content if isinstance(content, str) else json.dumps(content)

    def delete_file_fn(self, sessions_dir, session_id):
        self.deleted.append(session_id)
        self.files.pop(session_id, None)


def _watcher(fs, tracker=None, time_fn=None, sleep_fn=None, **kwargs):
    return ActivityWatcher(
        sessions_dir="/fake/sessions",
        tracker=tracker or ActivityTracker(),
        list_files_fn=fs.list_files_fn,
        read_file_fn=fs.read_file_fn,
        delete_file_fn=fs.delete_file_fn,
        time_fn=time_fn or (lambda: 100.0),
        sleep_fn=sleep_fn or (lambda seconds: True),
        **kwargs,
    )


def test_get_latest_is_none_before_first_tick():
    fs = FakeFs()
    watcher = _watcher(fs)
    assert watcher.get_latest() is None


def test_tick_once_publishes_idle_when_no_sessions():
    fs = FakeFs()
    watcher = _watcher(fs)
    watcher._tick_once()
    assert watcher.get_latest() == ActivityView(state="idle")


def test_tick_once_parses_files_and_publishes_aggregate():
    fs = FakeFs()
    fs.files["s1"] = rec("PreToolUse", ts=100.0, tool_name="Edit")
    watcher = _watcher(fs, time_fn=lambda: 100.0)
    watcher._tick_once()
    view = watcher.get_latest()
    assert view.state == "working"
    assert view.tool_label == "Editing"
    assert view.session_id == "s1"


def test_tick_once_ignores_unparseable_files():
    fs = FakeFs()
    fs.files["s1"] = "not json{{{"
    watcher = _watcher(fs)
    watcher._tick_once()
    assert watcher.get_latest() == ActivityView(state="idle")


def test_tick_once_deletes_stale_session_files():
    fs = FakeFs()
    fs.files["s1"] = rec("Stop", ts=0.0, seq=1)
    tracker = ActivityTracker()
    # First tick observes and ages the session out via GONE_S.
    watcher = _watcher(fs, tracker=tracker, time_fn=lambda: 2000.0)
    watcher._tick_once()
    assert fs.deleted == ["s1"]


def test_missing_sessions_dir_reports_idle_without_listing():
    called = {"n": 0}

    def list_files_fn(_dir):
        called["n"] += 1
        return []

    watcher = ActivityWatcher(
        sessions_dir=lambda: None,
        tracker=ActivityTracker(),
        list_files_fn=list_files_fn,
        time_fn=lambda: 100.0,
        sleep_fn=lambda s: True,
    )
    watcher._tick_once()

    assert watcher.get_latest() == ActivityView(state="idle")
    assert called["n"] == 0


def test_distro_down_backs_off_without_reading_files():
    called = {"n": 0}

    def list_files_fn(_dir):
        called["n"] += 1
        return []

    watcher = ActivityWatcher(
        sessions_dir="\\\\wsl.localhost\\Ubuntu\\home\\x\\.claude\\tokitty\\sessions",
        tracker=ActivityTracker(),
        distro_name="Ubuntu",
        list_running_distros_fn=lambda: ["OtherDistro"],
        list_files_fn=list_files_fn,
        time_fn=lambda: 100.0,
        sleep_fn=lambda s: True,
    )
    watcher._tick_once()

    assert watcher.get_latest() == ActivityView(state="idle")
    assert called["n"] == 0


def test_distro_running_proceeds_to_read_files():
    fs = FakeFs()
    fs.files["s1"] = rec("PreToolUse", ts=100.0, tool_name="Bash")

    watcher = ActivityWatcher(
        sessions_dir="\\\\wsl.localhost\\Ubuntu\\home\\x\\.claude\\tokitty\\sessions",
        tracker=ActivityTracker(),
        distro_name="Ubuntu",
        list_running_distros_fn=lambda: ["Ubuntu"],
        list_files_fn=fs.list_files_fn,
        read_file_fn=fs.read_file_fn,
        delete_file_fn=fs.delete_file_fn,
        time_fn=lambda: 100.0,
        sleep_fn=lambda s: True,
    )
    watcher._tick_once()

    assert watcher.get_latest().state == "working"


def test_running_distros_probed_once_per_tick_not_per_file():
    fs = FakeFs()
    fs.files["s1"] = rec("PreToolUse", ts=100.0, tool_name="Bash")
    fs.files["s2"] = rec("PreToolUse", ts=100.0, tool_name="Bash")
    calls = {"n": 0}

    def list_running():
        calls["n"] += 1
        return ["Ubuntu"]

    watcher = ActivityWatcher(
        sessions_dir="\\\\wsl.localhost\\Ubuntu\\...\\sessions",
        tracker=ActivityTracker(),
        distro_name="Ubuntu",
        list_running_distros_fn=list_running,
        list_files_fn=fs.list_files_fn,
        read_file_fn=fs.read_file_fn,
        delete_file_fn=fs.delete_file_fn,
        time_fn=lambda: 100.0,
        sleep_fn=lambda s: True,
    )
    watcher._tick_once()

    assert calls["n"] == 1


def test_adaptive_cadence_fast_when_non_idle_slow_when_idle():
    sleeps = []

    def sleep_fn(seconds):
        sleeps.append(seconds)
        # stop after capturing 2 intervals
        if len(sleeps) >= 2:
            watcher.stop_event_for_test.set()
        return True

    fs = FakeFs()
    fs.files["s1"] = rec("PreToolUse", ts=100.0, tool_name="Bash")

    watcher = _watcher(fs, time_fn=lambda: 100.0, sleep_fn=sleep_fn)
    watcher.stop_event_for_test = watcher._stop_event
    watcher._run()

    assert sleeps[0] == FAST_INTERVAL_S


def test_adaptive_cadence_slow_when_idle():
    sleeps = []

    def sleep_fn(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 1:
            watcher._stop_event.set()
        return True

    fs = FakeFs()
    watcher = _watcher(fs, time_fn=lambda: 100.0, sleep_fn=sleep_fn)
    watcher._run()

    assert sleeps[0] == SLOW_INTERVAL_S


def test_start_and_stop_run_thread_and_publish():
    fs = FakeFs()
    fs.files["s1"] = rec("PreToolUse", ts=100.0, tool_name="Bash")
    ready = threading.Event()
    real_time = time.time

    def sleep_fn(seconds):
        ready.set()
        return True

    watcher = _watcher(fs, time_fn=real_time, sleep_fn=sleep_fn)
    watcher.start()
    try:
        assert ready.wait(timeout=2)
        time.sleep(0.05)
        assert watcher.get_latest() is not None
    finally:
        watcher.stop()
