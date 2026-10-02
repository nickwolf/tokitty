import io
import json
import os
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

from tokitty import updater
from tokitty.updater import (
    Release,
    UpdateCheckError,
    UpdateState,
    add_owned,
    asset_name,
    drop_owned,
    fetch_releases,
    is_newer,
    load_update_state,
    mutate_update_state,
    parse_version,
    platform_target,
    running_version,
    save_update_state,
    select_latest,
)

TARGET = "linux-x86_64"


def _release(tag, *, draft=False, prerelease=False, assets=True, sums=True, created_at="2026-01-01T00:00:00Z"):
    names = []
    if assets:
        names.append(asset_name(tag, TARGET))
    if sums:
        names.append("SHA256SUMS")
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "created_at": created_at,
        "html_url": f"https://github.com/nickwolf/tokitty/releases/tag/{tag}",
        "assets": [
            {"name": n, "size": 1234, "browser_download_url": f"https://example.invalid/{tag}/{n}"} for n in names
        ],
    }


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(body, seen=None):
    def urlopen(request, timeout=None):
        if seen is not None:
            seen.append((request, timeout))
        return FakeResponse(body if isinstance(body, bytes) else json.dumps(body).encode())

    return urlopen


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v1.2.3", (1, 2, 3)),
        ("v0.10.0", (0, 10, 0)),
        ("v1.2", None),
        ("1.2.3", None),
        ("v01.2.3", None),
        ("v1.2.3-rc1", None),
        ("v1.2.3\n", None),
        ("v1.2.3.4", None),
        ("v1.2.٣", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_running_version_frozen_release_is_installable():
    assert running_version(frozen=True, build_id="v0.2.1") == updater.RunningVersion("v0.2.1", True)


def test_running_version_frozen_dry_run_is_not_installable():
    got = running_version(frozen=True, build_id="dryrun-abc123", read_metadata=lambda: "v0.2.1")
    assert got == updater.RunningVersion("v0.2.1", False)


def test_running_version_source_run_reads_metadata():
    got = running_version(frozen=False, build_id="v9.9.9", read_metadata=lambda: "v0.2.1")
    assert got == updater.RunningVersion("v0.2.1", False)


def test_running_version_without_metadata_falls_back_to_dev():
    assert running_version(frozen=False, build_id=None, read_metadata=lambda: None).version == "dev"


def test_running_version_reads_environment_by_default(monkeypatch):
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setenv("TOKITTY_BUILD_ID", "v1.4.0")
    assert running_version() == updater.RunningVersion("v1.4.0", True)


@pytest.mark.parametrize(
    ("sys_platform", "machine", "expected"),
    [
        ("win32", "AMD64", "windows-x64"),
        ("win32", "ARM64", None),
        ("darwin", "arm64", "macos-arm64"),
        ("darwin", "x86_64", "macos-x86_64"),
        ("linux", "x86_64", "linux-x86_64"),
        ("linux", "aarch64", None),
        ("freebsd14", "amd64", None),
    ],
)
def test_platform_target(sys_platform, machine, expected):
    assert platform_target(sys_platform, machine) == expected


def test_asset_name_extension():
    assert asset_name("v0.2.1", "linux-x86_64") == "tokitty-v0.2.1-linux-x86_64.tar.gz"
    assert asset_name("v0.2.1", "macos-arm64") == "tokitty-v0.2.1-macos-arm64.zip"
    assert asset_name("v0.2.1", "windows-x64") == "tokitty-v0.2.1-windows-x64.zip"


def test_select_latest_takes_highest_version_not_newest_or_listed_first():
    releases = [
        _release("v0.9.9", created_at="2026-09-01T00:00:00Z"),
        _release("v0.10.0", created_at="2026-01-01T00:00:00Z"),
        _release("v0.2.1", created_at="2026-10-01T00:00:00Z"),
    ]
    assert select_latest(releases, TARGET).tag == "v0.10.0"


def test_select_latest_ignores_drafts_prereleases_and_bad_tags():
    releases = [
        _release("v2.0.0", draft=True),
        _release("v1.5.0", prerelease=True),
        _release("v1.4.0-rc1"),
        _release("nightly"),
        "not a release",
        _release("v0.9.9"),
    ]
    assert select_latest(releases, TARGET).tag == "v0.9.9"


def test_select_latest_reports_urls_and_size():
    got = select_latest([_release("v0.3.0")], TARGET)
    assert got == Release(
        tag="v0.3.0",
        html_url="https://github.com/nickwolf/tokitty/releases/tag/v0.3.0",
        asset_url="https://example.invalid/v0.3.0/tokitty-v0.3.0-linux-x86_64.tar.gz",
        asset_size=1234,
        sums_url="https://example.invalid/v0.3.0/SHA256SUMS",
    )
    assert got.installable_from_app is True


def test_missing_sums_is_not_installable():
    got = select_latest([_release("v0.3.0", sums=False)], TARGET)
    assert got.sums_url is None
    assert got.asset_url is not None
    assert got.installable_from_app is False


def test_missing_platform_asset_is_not_installable():
    got = select_latest([_release("v0.3.0", assets=False)], TARGET)
    assert got.asset_url is None
    assert got.asset_size is None
    assert got.installable_from_app is False


def test_unknown_platform_has_no_asset():
    got = select_latest([_release("v0.3.0")], None)
    assert got.asset_url is None
    assert got.installable_from_app is False


def test_select_latest_tolerates_malformed_assets():
    release = _release("v0.3.0")
    release["assets"] = [None, {"name": 5}, {"name": "SHA256SUMS", "browser_download_url": 7}]
    got = select_latest([release], TARGET)
    assert got.tag == "v0.3.0"
    assert got.sums_url is None


def test_select_latest_none_when_nothing_qualifies():
    assert select_latest([], TARGET) is None
    assert select_latest({"message": "nope"}, TARGET) is None
    assert select_latest([_release("v1.0.0", draft=True)], TARGET) is None


def test_is_newer():
    assert is_newer("v0.10.0", "v0.9.9")
    assert not is_newer("v0.2.1", "v0.2.1")
    assert not is_newer("v0.2.0", "v0.2.1")
    assert not is_newer(None, "v0.2.1")
    assert not is_newer("v0.3.0", "dev")


def test_fetch_releases_request_shape(monkeypatch):
    monkeypatch.delenv("TOKITTY_UPDATE_API_URL", raising=False)
    seen = []
    got = fetch_releases(urlopen=_fake_urlopen([{"tag_name": "v1.0.0"}], seen))
    assert got == [{"tag_name": "v1.0.0"}]
    request, timeout = seen[0]
    assert request.full_url == "https://api.github.com/repos/nickwolf/tokitty/releases?per_page=100"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("User-agent").startswith("tokitty/")
    assert timeout == 10.0


def test_api_url_env_overrides_base(monkeypatch):
    monkeypatch.setenv("TOKITTY_UPDATE_API_URL", "https://127.0.0.1:9999/")
    seen = []
    fetch_releases(urlopen=_fake_urlopen([], seen))
    assert seen[0][0].full_url == "https://127.0.0.1:9999/repos/nickwolf/tokitty/releases?per_page=100"


def test_explicit_base_url_beats_env(monkeypatch):
    monkeypatch.setenv("TOKITTY_UPDATE_API_URL", "https://env.invalid")
    seen = []
    fetch_releases(urlopen=_fake_urlopen([], seen), base_url="https://arg.invalid")
    assert seen[0][0].full_url.startswith("https://arg.invalid/repos/")


def _raising(exc):
    def urlopen(request, timeout=None):
        raise exc

    return urlopen


@pytest.mark.parametrize(
    ("urlopen", "fragment"),
    [
        (_raising(urllib.error.HTTPError("u", 403, "rate limited", {}, None)), "HTTP 403"),
        (_raising(urllib.error.URLError("no route")), "no route"),
        (_raising(TimeoutError("timed out")), "timed out"),
        (_fake_urlopen(b"<html>"), "not valid JSON"),
        (_fake_urlopen(b"\xff\xfe"), "not valid JSON"),
        (_fake_urlopen({"message": "Not Found"}), "not a JSON array"),
    ],
)
def test_fetch_releases_failures_raise_update_check_error(urlopen, fragment):
    with pytest.raises(UpdateCheckError, match=fragment):
        fetch_releases(urlopen=urlopen)


def test_update_state_defaults_when_missing(tmp_path):
    assert load_update_state(tmp_path) == UpdateState()


def test_update_state_round_trips(tmp_path):
    state = UpdateState(
        last_checked="2026-10-02T12:00:00+00:00",
        latest_tag="v0.3.0",
        notified_tag="v0.3.0",
        owned=[{"path": "/r/v0.3.0/tokitty", "version": "v0.3.0", "kind": "copy"}],
        pending={"token": "abc", "old_version": "v0.2.1"},
    )
    save_update_state(tmp_path, state)
    assert load_update_state(tmp_path) == state
    assert [p.name for p in tmp_path.iterdir()] == ["update.json"]


@pytest.mark.parametrize("raw", ["{ not json", "[]", "7", ""])
def test_update_state_bad_file_defaults(tmp_path, raw):
    (tmp_path / "update.json").write_text(raw, encoding="utf-8")
    assert load_update_state(tmp_path) == UpdateState()


def test_update_state_degrades_per_field(tmp_path):
    good = {"path": "/r/v0.3.0/tokitty", "version": "v0.3.0", "kind": "copy"}
    (tmp_path / "update.json").write_text(
        json.dumps(
            {
                "last_checked": 12,
                "latest_tag": "v0.3.0",
                "notified_tag": "latest",
                "owned": [good, {"path": "/x", "version": "v1.0.0", "kind": "other"}, "junk", {"kind": "copy"}],
                "pending": "oops",
            }
        ),
        encoding="utf-8",
    )
    assert load_update_state(tmp_path) == UpdateState(latest_tag="v0.3.0", owned=[good])


def test_update_state_unparseable_timestamp_is_dropped(tmp_path):
    (tmp_path / "update.json").write_text('{"last_checked": "yesterday"}', encoding="utf-8")
    assert load_update_state(tmp_path).last_checked is None


def test_save_update_state_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    def boom(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(updater.os, "replace", boom)
    with pytest.raises(PermissionError):
        save_update_state(tmp_path, UpdateState())
    assert list(tmp_path.iterdir()) == []


def test_check_for_update_cli_reports_newer(monkeypatch, capsys):
    monkeypatch.setattr(updater, "running_version", lambda: updater.RunningVersion("v0.2.1", True))
    monkeypatch.setattr(updater, "platform_target", lambda: TARGET)
    monkeypatch.setattr(updater, "fetch_releases", lambda: [_release("v0.3.0"), _release("v0.2.1")])
    assert updater.check_for_update_cli() == 0
    assert json.loads(capsys.readouterr().out) == {
        "running": "v0.2.1",
        "installable": True,
        "latest": "v0.3.0",
        "newer": True,
        "installable_from_app": True,
        "error": None,
    }


def test_check_for_update_cli_failure_exits_1(monkeypatch, capsys):
    def fail():
        raise UpdateCheckError("HTTP 500 from the releases list")

    monkeypatch.setattr(updater, "fetch_releases", fail)
    assert updater.check_for_update_cli() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "HTTP 500 from the releases list"
    assert report["latest"] is None
    assert report["newer"] is False


def test_mutate_update_state_loads_applies_and_saves(tmp_path):
    save_update_state(tmp_path, UpdateState(latest_tag="v0.3.0"))

    def edit(state):
        state.notified_tag = state.latest_tag

    result = mutate_update_state(tmp_path, edit)
    assert result.notified_tag == "v0.3.0"
    assert load_update_state(tmp_path) == UpdateState(latest_tag="v0.3.0", notified_tag="v0.3.0")


def test_mutate_update_state_creates_the_state_dir(tmp_path):
    mutate_update_state(tmp_path / "new", lambda state: setattr(state, "latest_tag", "v1.0.0"))
    assert load_update_state(tmp_path / "new").latest_tag == "v1.0.0"


def test_mutate_update_state_does_not_save_when_fn_raises(tmp_path):
    def boom(state):
        state.latest_tag = "v9.9.9"
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        mutate_update_state(tmp_path, boom)
    assert load_update_state(tmp_path).latest_tag is None
    mutate_update_state(tmp_path, lambda state: None)


def test_mutate_update_state_times_out_while_another_holder_has_the_lock(tmp_path):
    from tokitty.lock import SingleInstanceLock

    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    with SingleInstanceLock(tmp_path, name="update.lock"):
        with pytest.raises(OSError, match="update.lock"):
            mutate_update_state(tmp_path, lambda state: None, lock_timeout=1.0, clock=lambda: now[0], sleep=sleep)
    assert not (tmp_path / "update.json").exists()


def test_add_and_drop_owned(tmp_path):
    add_owned(tmp_path, "/r/a", "v0.1.0", "copy")
    add_owned(tmp_path, "/r/b", "v0.2.0", "staging")
    add_owned(tmp_path, "/r/a", "v0.1.1", "backup")
    assert [(e["path"], e["version"], e["kind"]) for e in load_update_state(tmp_path).owned] == [
        ("/r/b", "v0.2.0", "staging"),
        ("/r/a", "v0.1.1", "backup"),
    ]
    drop_owned(tmp_path, "/r/b")
    assert [e["path"] for e in load_update_state(tmp_path).owned] == ["/r/a"]


def test_mutate_update_state_loses_no_edits_across_threads(tmp_path):
    def worker(n):
        for i in range(10):
            add_owned(tmp_path, f"/r/{n}-{i}", "v0.1.0", "copy")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(load_update_state(tmp_path).owned) == 60


def test_mutate_update_state_loses_no_edits_across_processes(tmp_path):
    code = (
        "import sys\n"
        "from tokitty.updater import add_owned\n"
        "for i in range(15):\n"
        "    add_owned(sys.argv[1], f'/r/{sys.argv[2]}-{i}', 'v0.1.0', 'copy')\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(updater.__file__).resolve().parent.parent))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(n)], env=env) for n in range(3)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0, 0]
    assert len(load_update_state(tmp_path).owned) == 45
