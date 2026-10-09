import json
import os
from datetime import datetime, timezone

import pytest

from tokitty import usage_notes
from tokitty.api import UsageSnapshot
from tokitty.usage_notes import alerts_for, remove_usage_file, window_key, write_usage_file

UTC = timezone.utc
RESET = datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC)
FETCHED = datetime(2026, 10, 9, 17, 42, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_cache():
    usage_notes._last_written.clear()
    yield
    usage_notes._last_written.clear()


def snap(session=10.0, weekly=10.0, s_reset=RESET, w_reset=RESET):
    return UsageSnapshot(session_pct=session, session_resets_at=s_reset,
                         weekly_pct=weekly, weekly_resets_at=w_reset)


def test_below_threshold_no_alerts():
    assert alerts_for(snap(89.9, 94.9), 90, 95) == []


def test_at_threshold_alerts():
    alerts = alerts_for(snap(90.0, 95.0), 90, 95)
    assert [a["kind"] for a in alerts] == ["session", "weekly"]


def test_above_threshold_entry_shape():
    (alert,) = alerts_for(snap(91.0, 10.0), 90, 95)
    assert alert == {
        "kind": "session", "pct": 91.0, "threshold": 90,
        "resets_at": "2026-10-09T20:00:00+00:00", "window": "session:2026-10-09T20:00Z",
    }


def test_only_weekly_over():
    (alert,) = alerts_for(snap(10.0, 99.0), 90, 95)
    assert alert["kind"] == "weekly" and alert["threshold"] == 95


def test_jitter_maps_to_same_window():
    a = datetime(2026, 10, 9, 20, 0, 0, 906000, tzinfo=UTC)
    b = datetime(2026, 10, 9, 19, 59, 59, 690000, tzinfo=UTC)
    assert window_key("session", a) == window_key("session", b) == "session:2026-10-09T20:00Z"


def test_rounds_to_nearest_minute():
    assert window_key("weekly", datetime(2026, 10, 9, 20, 0, 31, tzinfo=UTC)) == "weekly:2026-10-09T20:01Z"
    assert window_key("weekly", datetime(2026, 10, 9, 20, 0, 29, tzinfo=UTC)) == "weekly:2026-10-09T20:00Z"


def test_non_utc_input_normalised():
    from datetime import timedelta
    local = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone(timedelta(hours=-6)))
    assert window_key("session", local) == "session:2026-10-09T20:00Z"


def test_none_resets_at():
    (alert,) = alerts_for(snap(95.0, 0.0, s_reset=None), 90, 95)
    assert alert["resets_at"] is None
    assert alert["window"] == "session:unknown"


def test_write_creates_contract_file(tmp_path):
    write_usage_file(str(tmp_path), FETCHED, alerts_for(snap(91.0), 90, 95))
    data = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert data["v"] == 1
    assert data["fetched_at"] == "2026-10-09T17:42:10+00:00"
    assert data["alerts"][0]["window"] == "session:2026-10-09T20:00Z"
    assert sorted(os.listdir(tmp_path)) == ["usage.json"]


def test_write_skipped_when_unchanged(tmp_path):
    write_usage_file(str(tmp_path), FETCHED, [])
    (tmp_path / "usage.json").write_text("sentinel", encoding="utf-8")
    write_usage_file(str(tmp_path), FETCHED, [])
    assert (tmp_path / "usage.json").read_text(encoding="utf-8") == "sentinel"
    write_usage_file(str(tmp_path), datetime(2026, 10, 9, 17, 43, 10, tzinfo=UTC), [])
    assert json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))["v"] == 1


def test_remove_deletes_and_allows_rewrite(tmp_path):
    write_usage_file(str(tmp_path), FETCHED, [])
    remove_usage_file(str(tmp_path))
    assert not (tmp_path / "usage.json").exists()
    write_usage_file(str(tmp_path), FETCHED, [])
    assert (tmp_path / "usage.json").exists()


def test_remove_when_absent_is_fine(tmp_path):
    remove_usage_file(str(tmp_path))
    remove_usage_file(str(tmp_path / "missing"))


def test_write_swallows_oserror_and_retries(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("unc down")
    monkeypatch.setattr(usage_notes.os, "replace", boom)
    write_usage_file(str(tmp_path), FETCHED, [])
    assert os.listdir(tmp_path) == []
    monkeypatch.undo()
    write_usage_file(str(tmp_path), FETCHED, [])
    assert (tmp_path / "usage.json").exists()


def test_write_to_missing_dir_swallowed(tmp_path):
    write_usage_file(str(tmp_path / "nope"), FETCHED, [])


class _Provider:
    def __init__(self, kind):
        self.kind = kind


def _unit(tmp_path, kind="claude"):
    sessions = tmp_path / "tokitty" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    return {"provider": _Provider(kind), "sessions_dir": str(sessions)}


def _result(snapshot):
    from tokitty.poller import PollResult
    return PollResult(status="ok", snapshot=snapshot, message=None, fetched_at=FETCHED)


def test_publish_writes_for_claude_and_skips_codex(tmp_path):
    from tokitty.__main__ import publish_usage_notes
    claude = _unit(tmp_path)
    publish_usage_notes(claude, _result(snap(91.0)), (True, 90, 95), set())
    data = json.loads((tmp_path / "tokitty" / "usage.json").read_text(encoding="utf-8"))
    assert data["alerts"][0]["kind"] == "session"
    other = tmp_path / "codex"
    codex = _unit(other, kind="codex")
    publish_usage_notes(codex, _result(snap(99.0)), (True, 90, 95), set())
    assert not (other / "tokitty" / "usage.json").exists()


def test_publish_disabled_removes_once(tmp_path):
    from tokitty.__main__ import publish_usage_notes
    unit = _unit(tmp_path)
    removed = set()
    publish_usage_notes(unit, _result(snap(91.0)), (True, 90, 95), removed)
    path = tmp_path / "tokitty" / "usage.json"
    assert path.exists()
    publish_usage_notes(unit, _result(snap(91.0)), (False, 90, 95), removed)
    assert not path.exists()
    path.write_text("x", encoding="utf-8")
    publish_usage_notes(unit, _result(snap(91.0)), (False, 90, 95), removed)
    assert path.exists()


def test_publish_without_snapshot_or_dir_is_quiet(tmp_path):
    from tokitty.__main__ import publish_usage_notes
    unit = _unit(tmp_path)
    publish_usage_notes(unit, None, (True, 90, 95), set())
    publish_usage_notes(unit, _result(None), (True, 90, 95), set())
    publish_usage_notes({"provider": _Provider("claude"), "sessions_dir": None}, None, (True, 90, 95), set())
    assert not (tmp_path / "tokitty" / "usage.json").exists()
