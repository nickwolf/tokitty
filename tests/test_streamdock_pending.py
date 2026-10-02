"""Tests for tokitty.streamdock.pending: the pending-request watcher and the
decision writer, including a round trip through hook_writer._read_decision."""

import json
import os
from pathlib import Path

import pytest

from tokitty import hook_writer
from tokitty.streamdock.pending import (
    PendingRequest,
    PendingWatcher,
    clear_enabled,
    touch_enabled,
    write_decision,
)

NONCE = "0123456789abcdef"
NONCE2 = "fedcba9876543210"
NOW = 10_000.0


def body(nonce=NONCE, **over):
    d = {
        "v": 1,
        "nonce": nonce,
        "session_id": "s1",
        "tool_use_id": "toolu_1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "digest": "d" * 64,
        "preview": "ls",
        "cwd": "/w",
        "started": NOW - 5,
        "pid": 1,
    }
    d.update(over)
    return d


class FakeFs:
    """name -> (content, mtime) per directory; records every call."""

    def __init__(self):
        self.dirs = {"pending": {}, "decisions": {}}
        self.listed = []
        self.read = []
        self.removed = []

    def put(self, d, name, content, mtime=NOW):
        self.dirs[d][name] = (content if isinstance(content, str) else json.dumps(content), mtime)

    def _loc(self, path):
        p = Path(path)
        return p.parent.name, p.name

    def list_files_fn(self, directory):
        self.listed.append(str(directory))
        return [Path(directory) / n for n in sorted(self.dirs[Path(directory).name])]

    def read_file_fn(self, path):
        self.read.append(Path(path).name)
        d, n = self._loc(path)
        return self.dirs[d][n][0]

    def stat_fn(self, path):
        d, n = self._loc(path)
        if n not in self.dirs[d]:
            raise FileNotFoundError(n)
        return self.dirs[d][n][1]

    def remove_fn(self, path):
        d, n = self._loc(path)
        self.removed.append((d, n))
        self.dirs[d].pop(n, None)


class Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def watcher(fs, active=True, clock=None, **kw):
    w = PendingWatcher(
        "/fake/tokitty",
        list_files_fn=fs.list_files_fn,
        read_file_fn=fs.read_file_fn,
        stat_fn=fs.stat_fn,
        remove_fn=fs.remove_fn,
        time_fn=lambda: NOW,
        monotonic_fn=clock or Clock(),
        sleep_fn=lambda s: True,
        **kw,
    )
    if active:
        w.set_active(True)
    return w


def test_live_request_is_published():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body())
    w = watcher(fs, account_index=2)
    w._tick_once()
    (req,) = w.get_pending()
    assert req.nonce == NONCE and req.session_id == "s1" and req.tool_use_id == "toolu_1"
    assert req.tool_input == {"command": "ls"} and req.account_index == 2 and req.cwd == "/w"


def test_old_creation_with_fresh_heartbeat_stays_live():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body(started=NOW - 7200), mtime=NOW - 29)
    w = watcher(fs)
    w._tick_once()
    assert len(w.get_pending()) == 1


@pytest.mark.parametrize("offset", [-3600.0, 3600.0])
def test_heartbeat_judged_by_change_not_wall_time(offset):
    fs = FakeFs()
    clock = Clock()
    w = watcher(fs, clock=clock)
    for i in range(10):
        fs.put("pending", f"{NONCE}.json", body(), mtime=NOW + offset + i * 5)
        w._tick_once()
        assert len(w.get_pending()) == 1
        clock.advance(5)
    assert fs.removed == []


def test_stalled_heartbeat_goes_dead_after_30_monotonic_seconds():
    fs = FakeFs()
    clock = Clock()
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW)
    w = watcher(fs, clock=clock)
    w._tick_once()
    clock.advance(29.5)
    w._tick_once()
    assert len(w.get_pending()) == 1 and fs.removed == []
    clock.advance(0.5)
    w._tick_once()
    assert w.get_pending() == []
    assert fs.removed == [("pending", f"{NONCE}.json")]


def test_changed_mtime_restarts_the_window():
    fs = FakeFs()
    clock = Clock()
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW)
    w = watcher(fs, clock=clock)
    w._tick_once()
    clock.advance(25)
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW + 1)
    w._tick_once()
    clock.advance(25)
    w._tick_once()
    assert len(w.get_pending()) == 1 and fs.removed == []


def test_state_for_deleted_files_is_forgotten():
    fs = FakeFs()
    clock = Clock()
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW)
    fs.put("pending", "a" * 32 + ".claim", "", mtime=NOW)
    fs.put("decisions", f"{NONCE2}.json", "{}", mtime=NOW)
    w = watcher(fs, clock=clock)
    w._tick_once()
    assert len(w._seen) == 3
    for d, n in (("pending", f"{NONCE}.json"), ("pending", "a" * 32 + ".claim"), ("decisions", f"{NONCE2}.json")):
        fs.dirs[d].pop(n)
    w._tick_once()
    assert w._seen == {}
    # A file reappearing under the same name with the same mtime is new, not stale.
    clock.advance(100)
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW)
    w._tick_once()
    assert len(w.get_pending()) == 1


def test_ignores_tmp_claim_and_misnamed_files():
    fs = FakeFs()
    fs.put("pending", ".tmp-abc.tmp", body())
    fs.put("pending", f"{'a' * 32}.claim", "")
    fs.put("pending", "0123456789ABCDEF.json", body("0123456789ABCDEF"))
    fs.put("pending", "0123456789abcde.json", body("0123456789abcde"))
    fs.put("pending", f"{NONCE}.json.bak", body())
    w = watcher(fs)
    w._tick_once()
    assert w.get_pending() == []
    assert fs.read == []


@pytest.mark.parametrize("missing", ["v", "nonce", "session_id", "tool_use_id", "tool_name", "tool_input", "digest", "preview", "started"])
def test_missing_required_field_is_ignored(missing):
    fs = FakeFs()
    d = body()
    del d[missing]
    fs.put("pending", f"{NONCE}.json", d)
    w = watcher(fs)
    w._tick_once()
    assert w.get_pending() == []


def test_garbage_and_wrong_types_are_ignored():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", "{not json")
    fs.put("pending", f"{NONCE2}.json", body(NONCE2, tool_input="ls"))
    w = watcher(fs)
    w._tick_once()
    assert w.get_pending() == []


def test_nonce_must_match_filename():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body(NONCE2))
    w = watcher(fs)
    w._tick_once()
    assert w.get_pending() == []


def test_null_tool_use_id_is_ignored():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body(tool_use_id=None))
    w = watcher(fs)
    w._tick_once()
    assert w.get_pending() == []


def test_vanished_file_is_skipped():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body())
    w = watcher(fs)
    fs.stat_fn = lambda p: (_ for _ in ()).throw(FileNotFoundError())
    w._stat_fn = fs.stat_fn
    w._tick_once()
    assert w.get_pending() == []


def test_claim_swept_only_after_660_unchanged_seconds():
    fs = FakeFs()
    clock = Clock()
    fs.put("pending", "a" * 32 + ".claim", "", mtime=NOW - 99999)
    fs.put("pending", "b" * 32 + ".claim", "", mtime=NOW + 99999)
    w = watcher(fs, clock=clock)
    w._tick_once()
    clock.advance(660)
    fs.put("pending", "b" * 32 + ".claim", "", mtime=NOW + 100000)
    w._tick_once()
    assert fs.removed == []
    clock.advance(0.5)
    w._tick_once()
    assert fs.removed == [("pending", "a" * 32 + ".claim")]


def test_stray_decision_swept_only_after_660_unchanged_seconds():
    fs = FakeFs()
    clock = Clock()
    fs.put("decisions", f"{NONCE}.json", "{}", mtime=NOW - 99999)
    fs.put("decisions", f"{NONCE2}.json", "{}", mtime=NOW)
    w = watcher(fs, clock=clock)
    w._tick_once()
    clock.advance(661)
    fs.put("decisions", f"{NONCE2}.json", "{}", mtime=NOW + 1)
    w._tick_once()
    assert fs.removed == [("decisions", f"{NONCE}.json")]


def test_remove_oserror_is_swallowed():
    fs = FakeFs()
    clock = Clock()
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW)

    def boom(path):
        raise PermissionError("no")

    w = watcher(fs, clock=clock)
    w._remove_fn = boom
    w._tick_once()
    clock.advance(30)
    w._tick_once()
    assert w.get_pending() == []


def test_inactive_watcher_does_not_poll_or_remove():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW - 99)
    w = watcher(fs, active=False)
    w._tick_once()
    assert fs.listed == [] and fs.removed == []


def test_run_loop_skips_ticks_while_inactive():
    fs = FakeFs()
    w = watcher(fs, active=False)
    sleeps = []

    def sleep_fn(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            w._stop_event.set()
        return True

    w._sleep_fn = sleep_fn
    w._run()
    assert fs.listed == []


def test_deactivating_clears_published_requests():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body())
    w = watcher(fs)
    w._tick_once()
    assert w.get_pending()
    w.set_active(False)
    assert w.get_pending() == []


def test_stopped_distro_produces_no_reads():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body(), mtime=NOW - 99)
    w = watcher(fs, distro_name="Ubuntu", list_running_distros_fn=lambda: ["Other"])
    w._tick_once()
    assert fs.listed == [] and fs.read == [] and fs.removed == []
    assert w.get_pending() == []


def test_running_distro_is_read():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body())
    w = watcher(fs, distro_name="Ubuntu", list_running_distros_fn=lambda: ["Ubuntu"])
    w._tick_once()
    assert len(w.get_pending()) == 1


def test_requests_sorted_by_started():
    fs = FakeFs()
    fs.put("pending", f"{NONCE}.json", body(started=NOW - 1))
    fs.put("pending", f"{NONCE2}.json", body(NONCE2, started=NOW - 9))
    w = watcher(fs)
    w._tick_once()
    assert [r.nonce for r in w.get_pending()] == [NONCE2, NONCE]


def make_req(**over):
    kw = dict(
        nonce=NONCE, session_id="s1", tool_use_id="t", tool_name="Bash",
        tool_input={"command": "ls"}, digest=hook_writer._digest({"command": "ls"}),
        preview="ls", started=1.0,
    )
    kw.update(over)
    return PendingRequest(**kw)


def test_write_decision_contents_and_creates_dir(tmp_path):
    req = make_req()
    write_decision(tmp_path, req, "allow", now=42.0)
    files = list((tmp_path / "decisions").iterdir())
    assert [f.name for f in files] == [f"{NONCE}.json"]
    assert json.loads(files[0].read_text()) == {
        "nonce": NONCE, "session_id": "s1", "digest": req.digest, "behavior": "allow", "decided_at": 42.0,
    }


def test_write_decision_rejects_other_behaviors(tmp_path):
    for bad in ("always", "", None, "ALLOW"):
        with pytest.raises(ValueError):
            write_decision(tmp_path, make_req(), bad)
    assert not (tmp_path / "decisions").exists() or list((tmp_path / "decisions").iterdir()) == []


def test_write_decision_is_atomic_same_dir_replace(tmp_path, monkeypatch):
    calls = []
    real = os.replace

    def spy(src, dst):
        calls.append((src, dst, (Path(dst).exists())))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    write_decision(tmp_path, make_req(), "deny")
    (src, dst, existed), = calls
    assert Path(src).parent == Path(dst).parent == tmp_path / "decisions"
    assert not existed
    assert [p.name for p in (tmp_path / "decisions").iterdir()] == [f"{NONCE}.json"]


def test_write_decision_cleans_tmp_on_failure(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("nope")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_decision(tmp_path, make_req(), "allow")
    assert list((tmp_path / "decisions").iterdir()) == []


def test_watcher_write_decision_method(tmp_path):
    w = PendingWatcher(tmp_path, time_fn=lambda: 7.0)
    w.write_decision(make_req(), "deny")
    data = json.loads((tmp_path / "decisions" / f"{NONCE}.json").read_text())
    assert data["behavior"] == "deny" and data["decided_at"] == 7.0


@pytest.mark.parametrize("behavior", ["allow", "deny"])
def test_round_trip_with_hook_writer(tmp_path, behavior):
    req = make_req()
    write_decision(tmp_path, req, behavior)
    path = tmp_path / "decisions" / f"{NONCE}.json"
    assert hook_writer._read_decision(str(path), req.nonce, req.session_id, req.digest) == behavior
    assert not path.exists()


@pytest.mark.parametrize(
    "nonce,session,digest",
    [(NONCE2, "s1", None), (NONCE, "s2", None), (NONCE, "s1", "x" * 64)],
)
def test_round_trip_rejects_any_mismatch(tmp_path, nonce, session, digest):
    req = make_req()
    write_decision(tmp_path, req, "allow")
    path = tmp_path / "decisions" / f"{NONCE}.json"
    assert hook_writer._read_decision(str(path), nonce, session, digest or req.digest) is None


def test_touch_and_clear_enabled(tmp_path):
    touch_enabled(tmp_path)
    marker = tmp_path / "streamdock.enabled"
    assert marker.exists()
    os.utime(marker, (1, 1))
    touch_enabled(tmp_path)
    assert marker.stat().st_mtime > 1000
    clear_enabled(tmp_path)
    assert not marker.exists()
    clear_enabled(tmp_path)


def test_touch_and_clear_swallow_oserror(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    touch_enabled(blocker / "sub")
    clear_enabled(blocker / "sub")
