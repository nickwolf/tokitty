import json
from datetime import datetime, timezone

from tokitty.usage_scan import STATUS_OK, STATUS_UNAVAILABLE
from tokitty.usage_watcher import UsageWatcher

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def transcript(root, output_tokens=20, ts=NOW):
    project = root / "-mnt-c-Tools"
    project.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "message": {
                "id": "msg_1",
                "model": "claude-opus-5",
                "usage": {"input_tokens": 10, "output_tokens": output_tokens},
            },
        }
    )
    (project / "a.jsonl").write_text(line + "\n", encoding="utf-8")
    return root


def make(root, **kwargs):
    kwargs.setdefault("now_fn", lambda: NOW)
    return UsageWatcher(root, **kwargs)


def test_tick_publishes_a_breakdown(tmp_path):
    watcher = make(transcript(tmp_path))
    watcher._tick_once()
    latest = watcher.get_latest()
    assert latest.status == STATUS_OK
    assert latest.total_tokens == 30


def test_no_projects_dir_publishes_unavailable_not_empty(tmp_path):
    watcher = make(None)
    watcher._tick_once()
    assert watcher.get_latest().status == STATUS_UNAVAILABLE


def test_stopped_distro_is_never_touched(tmp_path):
    """Touching the UNC path for a stopped distro boots it, and a 60s
    scanner would then keep it awake indefinitely."""
    root = transcript(tmp_path)
    touched = []

    watcher = make(
        root,
        distro_name="Ubuntu",
        list_running_distros_fn=lambda: touched.append("probed") or [],
    )
    watcher._scanner.scan = lambda: (_ for _ in ()).throw(
        AssertionError("scanned a stopped distro")
    )

    watcher._tick_once()
    assert watcher.get_latest().status == STATUS_UNAVAILABLE
    assert touched == ["probed"]


def test_running_distro_is_scanned(tmp_path):
    watcher = make(
        transcript(tmp_path),
        distro_name="Ubuntu",
        list_running_distros_fn=lambda: ["Ubuntu"],
    )
    watcher._tick_once()
    assert watcher.get_latest().status == STATUS_OK


def test_a_raising_scan_does_not_kill_the_thread(tmp_path):
    watcher = make(transcript(tmp_path))
    watcher._scanner.scan = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    watcher._tick_once()
    assert watcher.get_latest().status == STATUS_UNAVAILABLE


def test_window_getter_is_read_per_tick(tmp_path):
    window = {"value": "7d"}
    watcher = make(transcript(tmp_path), window=lambda: window["value"])
    watcher._tick_once()
    assert watcher.get_latest().window == "7d"

    window["value"] = "24h"
    watcher._tick_once()
    assert watcher.get_latest().window == "24h"


def test_rebuild_for_window_reaggregates_without_rescanning(tmp_path):
    window = {"value": "7d"}
    watcher = make(transcript(tmp_path), window=lambda: window["value"])
    watcher._tick_once()

    watcher._scanner.scan = lambda: (_ for _ in ()).throw(
        AssertionError("rescanned on a window switch")
    )
    window["value"] = "24h"
    rebuilt = watcher.rebuild_for_window()
    assert rebuilt.window == "24h"
    assert rebuilt.total_tokens == 30


def test_rebuild_before_a_first_scan_is_a_noop(tmp_path):
    assert make(transcript(tmp_path)).rebuild_for_window() is None


def test_published_snapshots_are_immutable(tmp_path):
    watcher = make(transcript(tmp_path))
    watcher._tick_once()
    latest = watcher.get_latest()
    assert isinstance(latest.models, tuple)
    assert isinstance(latest.unpriced_models, tuple)


def test_snapshot_held_across_a_rescan_is_not_mutated(tmp_path):
    root = transcript(tmp_path)
    watcher = make(root)
    watcher._tick_once()
    held = watcher.get_latest()

    transcript(root, output_tokens=999)
    watcher._tick_once()

    assert held.total_tokens == 30
    assert watcher.get_latest().total_tokens == 1009
