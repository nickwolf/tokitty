"""Tests for the pure helpers of freeze/verify_update.py (#77). The real run
is the release workflow; nothing here starts a process, opens a socket or
imports cryptography."""
import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

VERIFY_UPDATE_PATH = Path(__file__).resolve().parent.parent / "freeze" / "verify_update.py"


@pytest.fixture(scope="module")
def vu():
    spec = importlib.util.spec_from_file_location("verify_update_under_test", VERIFY_UPDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asset_file_name_matches_the_updaters(vu):
    from tokitty.updater import asset_name

    for target in ("windows-x64", "macos-arm64", "macos-x86_64", "linux-x86_64"):
        assert vu.asset_file_name("v1.2.3", target) == asset_name("v1.2.3", target)


def test_sums_text_is_sha256sum_format_and_round_trips(vu):
    from tokitty.update_install import parse_sums

    digest = "ab" * 32
    text = vu.sums_text("tokitty-v1.2.3-linux-x86_64.tar.gz", digest)
    assert text == f"{digest}  tokitty-v1.2.3-linux-x86_64.tar.gz\n"
    assert parse_sums(text) == {"tokitty-v1.2.3-linux-x86_64.tar.gz": digest}
    assert vu.parse_sums_text(text) == parse_sums(text)


def test_parse_sums_text_takes_every_line_and_the_binary_marker(vu):
    one, two = "a" * 64, "b" * 64
    text = f"{one}  first.zip\n{two} *second.zip\nnot a sums line\n"
    assert vu.parse_sums_text(text) == {"first.zip": one, "second.zip": two}


def test_release_list_is_what_select_latest_accepts(vu):
    from tokitty.updater import select_latest

    releases = vu.release_list("v0.3.0", "tokitty-v0.3.0-linux-x86_64.tar.gz", 1234, "https://127.0.0.1:4443", 99)
    assert len(releases) == 1
    release = releases[0]
    assert release["draft"] is False and release["prerelease"] is False
    assert release["html_url"].endswith("/v0.3.0")
    picked = select_latest(releases, "linux-x86_64")
    assert picked.tag == "v0.3.0"
    assert picked.asset_size == 1234
    assert picked.asset_url == "https://127.0.0.1:4443/download/v0.3.0/tokitty-v0.3.0-linux-x86_64.tar.gz"
    assert picked.sums_url == "https://127.0.0.1:4443/download/v0.3.0/SHA256SUMS"
    assert picked.html_url == release["html_url"]
    assert picked.installable_from_app


def test_tree_snapshot_marks_links_and_stops_at_depth(vu, tmp_path):
    (tmp_path / "v0.0.1" / "tokitty" / "deep").mkdir(parents=True)
    (tmp_path / "v0.0.1" / "tokitty" / "deep" / "file").write_text("x")
    (tmp_path / "plain.txt").write_text("x")
    (tmp_path / "alias").symlink_to(tmp_path / "v0.0.1", target_is_directory=True)
    assert vu.tree_snapshot(tmp_path, depth=2) == [
        "alias -> link",
        "plain.txt",
        "v0.0.1",
        "v0.0.1/tokitty",
    ]
    assert vu.tree_snapshot(tmp_path / "missing") == []


def test_snapshot_diff_reports_additions_and_removals(vu):
    assert vu.snapshot_diff(["a", "b"], ["a", "b"]) == []
    assert vu.snapshot_diff(["a", "b"], ["b", "c"]) == ["added: c", "removed: a"]


def test_survivor_problems(vu, tmp_path):
    kept, gone = tmp_path / "kept", tmp_path / "gone"
    kept.mkdir()
    assert vu.survivor_problems([kept], [gone]) == []
    gone.mkdir()
    kept.rmdir()
    problems = vu.survivor_problems([kept], [gone])
    assert problems == [
        f"deleted but should have survived: {kept}",
        f"survived but should have been deleted: {gone}",
    ]


def test_tls_github_passes_on_a_good_fetch(vu):
    assert vu.tls_github_verdict(0, {"error": None, "latest": "v0.2.2"}) == (True, {})


@pytest.mark.parametrize("status", [403, 429, 500])
def test_tls_github_passes_on_any_http_status(vu, status):
    report = {"error": f"HTTP {status} from the releases list", "latest": None}
    assert vu.tls_github_verdict(1, report) == (True, {"tls": "ok", "http_status": status})


@pytest.mark.parametrize(
    ("code", "report"),
    [
        (1, {"error": "Network error reaching the releases list: [SSL: CERTIFICATE_VERIFY_FAILED] bad", "latest": None}),
        (1, {"error": "Network error reaching the releases list: timed out", "latest": None}),
        (1, {"error": "The releases list was not valid JSON: x", "latest": None}),
        (0, {"error": None, "latest": None}),
        (1, None),
        (1, []),
    ],
)
def test_tls_github_fails_on_network_tls_or_a_missing_report(vu, code, report):
    ok, extra = vu.tls_github_verdict(code, report)
    assert ok is False and extra == {}


@pytest.mark.parametrize("plat", ["linux", "win32", "darwin"])
def test_cleanup_seed_is_consistent_with_the_cleanup_rules(vu, plat, tmp_path):
    base = tmp_path / "releases"
    seed = vu.cleanup_seed(plat, base, "v0.0.2")
    owned = {e["path"]: e for e in seed["owned"]}
    # Everything expected to go is owned, so the rule that deletes it applies;
    # nothing expected to stay out of harm's way by being unowned is recorded.
    for path in seed["gone"]:
        assert str(path) in owned
    unowned_kept = [p for p in seed["kept"] if str(p) not in owned]
    assert unowned_kept
    kinds = {e["kind"] for e in seed["owned"]}
    assert kinds == ({"backup", "staging"} if plat == "darwin" else {"copy", "staging"})
    # The owned link is recorded as a staging folder that would be deleted if
    # it were a folder, and is expected to survive.
    (link, target), = seed["links"]
    assert owned[str(link)]["kind"] == "staging"
    assert link in seed["kept"]
    assert target / "sentinel.txt" in seed["kept"]
    assert not set(seed["gone"]) & set(seed["kept"])
    # The two owned copies or backups are below the tag, the replaced one
    # newer than the one to delete.
    versions = sorted(e["version"] for e in seed["owned"] if e["kind"] != "staging")
    assert versions == ["v0.0.0", "v0.0.1"]


def test_cleanup_seed_agrees_with_the_real_cleanup(vu, tmp_path):
    """Run the updater's own cleanup over the seed on this machine."""
    from datetime import datetime, timezone

    from tokitty.update_install import Target
    from tokitty.update_swap import cleanup
    from tokitty.updater import UpdateState, save_update_state

    base = tmp_path / "releases"
    seed = vu.cleanup_seed("linux", base, "v0.0.2")
    for directory in seed["dirs"]:
        directory.mkdir(parents=True)
    (link, target), = seed["links"]
    target.mkdir()
    (target / "sentinel.txt").write_text("x")
    link.symlink_to(target, target_is_directory=True)
    running = base / "v0.0.2" / "tokitty"
    running.mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    save_update_state(state, UpdateState(owned=vu.with_identity(seed["owned"])))
    cleanup(
        state,
        running_release=running,
        current_target=running,
        running_version="v0.0.2",
        target=Target(base, "tokitty", "linux"),
        now=datetime.now(timezone.utc),
    )
    assert vu.survivor_problems(seed["kept"] + [running], seed["gone"]) == []


def test_make_bad_layout_archive_has_an_extra_top_level_entry(vu, tmp_path):
    zip_path, tar_path = tmp_path / "bad.zip", tmp_path / "bad.tar.gz"
    vu.make_bad_layout_archive(zip_path, "win32")
    vu.make_bad_layout_archive(tar_path, "linux")
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted({n.split("/")[0] for n in zf.namelist()}) == ["Tokitty", "extra.txt"]
    with tarfile.open(tar_path) as tf:
        assert sorted({n.split("/")[0] for n in tf.getnames()}) == ["extra.txt", "tokitty"]


def test_the_bad_layout_archive_is_rejected_by_the_updaters_layout_check(vu, tmp_path):
    from tokitty.update_install import UpdateInstallError, validate_layout

    archive = tmp_path / "bad.tar.gz"
    vu.make_bad_layout_archive(archive, "linux")
    root = tmp_path / "unpacked"
    root.mkdir()
    with tarfile.open(archive) as tf:
        tf.extractall(root, filter="data")
    with pytest.raises(UpdateInstallError, match="should hold only"):
        validate_layout(root, "linux")


def test_parse_tasklist_csv_skips_the_no_tasks_line(vu):
    text = '"Tokitty.exe","4242","Console","1","52,000 K"\r\n"Tokitty.exe","99","Console","1","10 K"\r\n'
    assert vu.parse_tasklist_csv(text) == [("Tokitty.exe", 4242), ("Tokitty.exe", 99)]
    assert vu.parse_tasklist_csv("INFO: No tasks are running which match the specified criteria.\r\n") == []


def test_parse_ps_and_gui_process_filter(vu):
    text = io.StringIO(
        "  101 /work/apply/releases/v0.0.2/tokitty/tokitty\n"
        "  102 /work/apply/releases/v0.0.2/tokitty/tokitty-hook PreToolUse\n"
        "  103 /usr/bin/tokitty\n"
        "  104 /work/other/releases/v0.0.1/tokitty/tokitty --apply-update\n"
        "  105 pkill -f /work/apply/releases\n"
        "notapid /work/apply/releases/x/tokitty\n"
    ).read()
    rows = vu.parse_ps(text)
    assert [pid for pid, _ in rows] == [101, 102, 103, 104, 105]
    found = vu.gui_processes_from_ps(rows, ["/work/apply"], "tokitty")
    assert found == [{"pid": 101, "path": "/work/apply/releases/v0.0.2/tokitty/tokitty"}]
    both = vu.gui_processes_from_ps(rows, ["/work/apply", "/work/other"], "tokitty")
    assert [p["pid"] for p in both] == [101, 104]


def test_single_process_problems(vu, tmp_path):
    new_exe = tmp_path / "new" / "tokitty"
    new_exe.parent.mkdir()
    new_exe.write_text("x")
    other = tmp_path / "old" / "tokitty"
    other.parent.mkdir()
    other.write_text("x")
    ok = [{"pid": 7, "path": str(new_exe)}]
    assert vu.single_process_problems(ok, new_exe, old_pid=3) == []
    assert vu.single_process_problems([{"pid": 7, "path": None}], new_exe, old_pid=3) == []
    assert "found 0" in vu.single_process_problems([], new_exe, old_pid=3)[0]
    two = ok + [{"pid": 8, "path": str(new_exe)}]
    assert "found 2" in vu.single_process_problems(two, new_exe, old_pid=3)[0]
    assert "still running" in vu.single_process_problems(ok, new_exe, old_pid=7)[0]
    wrong = vu.single_process_problems([{"pid": 7, "path": str(other)}], new_exe, old_pid=3)
    assert len(wrong) == 1 and "not" in wrong[0]


def test_top_name(vu):
    assert [vu.top_name(p) for p in ("win32", "darwin", "linux")] == ["Tokitty", "Tokitty.app", "tokitty"]
