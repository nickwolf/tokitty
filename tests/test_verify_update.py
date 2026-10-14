"""Tests for the pure helpers of freeze/verify_update.py (#77). The real run
is the release workflow; nothing here starts a process, opens a socket or
imports cryptography."""
import importlib.util
import io
import json
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


@pytest.mark.skipif(not hasattr(tarfile, "data_filter"), reason="tarfile has no data filter before 3.10.12")
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


def _scenario_ctx(tmp_path, **extra):
    return {"work": tmp_path, "tag": "v99.0.0", "old_tag": "v0.0.1", "old_archive": tmp_path / "old.tar.gz", **extra}


def test_scenario_folders_follow_the_old_tag(vu, tmp_path, monkeypatch):
    monkeypatch.setattr(vu.sys, "platform", "linux")
    default = vu.Scenario(_scenario_ctx(tmp_path), "a")
    assert default.old_app_dir == tmp_path / "a" / "releases" / "v0.0.1" / "tokitty"
    legacy = vu.Scenario(_scenario_ctx(tmp_path, old_tag="v0.6.2"), "b")
    assert legacy.old_app_dir == tmp_path / "b" / "releases" / "v0.6.2" / "tokitty"
    assert legacy.new_app_dir == tmp_path / "b" / "releases" / "v99.0.0" / "tokitty"
    assert legacy.old_tag == "v0.6.2"


def test_scenario_unpacks_the_old_archive_under_the_old_tag(vu, tmp_path, monkeypatch):
    monkeypatch.setattr(vu.sys, "platform", "linux")
    seen = {}
    monkeypatch.setattr(vu, "unpack_archive", lambda archive, dest: seen.update(dest=Path(dest)))
    scn = vu.Scenario(_scenario_ctx(tmp_path, old_tag="v0.6.2"), "c")
    with pytest.raises(RuntimeError):
        scn.unpack_old()
    assert seen["dest"] == tmp_path / "c" / "releases" / "v0.6.2"


def test_prepare_compares_the_tag_with_the_old_tag(vu, tmp_path):
    ctx = _scenario_ctx(tmp_path, tag="v0.6.2", old_tag="v0.6.2", new_archive=tmp_path / "n", new_sums=tmp_path / "s")
    for name in ("old.tar.gz", "n", "s"):
        (tmp_path / name).write_bytes(b"x")
    ctx.update(new_archive=tmp_path / "n", new_sums=tmp_path / "s", repo_root=Path(__file__).resolve().parent.parent, rehearsal=False)
    result = vu.step_prepare(ctx)
    assert not result["ok"]
    assert "is not newer than the old fixture v0.6.2" in result["detail"]


def test_qt_mode_env_removes_the_platform_variables_and_the_switch(vu):
    env = vu.qt_mode_env({"HOME": "/h", "QT_QPA_PLATFORM": "offscreen", "WAYLAND_DISPLAY": "wayland-0", "TOKITTY_QT": "1"})
    assert env == {"HOME": "/h"}


def test_old_process_env_sets_the_switch_only_under_old_mode_qt(vu):
    assert vu.old_process_env({"HOME": "/h", "TOKITTY_QT": "0"}, "tk") == {"HOME": "/h"}
    assert vu.old_process_env({"HOME": "/h"}, "qt") == {"HOME": "/h", "TOKITTY_QT": "1"}


def test_scratch_env_is_always_qt_ready_and_never_carries_the_switch(vu, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("TOKITTY_QT", "1")
    env = vu.scratch_env(tmp_path / "p")
    assert "QT_QPA_PLATFORM" not in env and "WAYLAND_DISPLAY" not in env and "TOKITTY_QT" not in env


def test_old_mode_qt_puts_tokitty_qt_in_the_old_process_env_only(vu, tmp_path, monkeypatch):
    monkeypatch.delenv("TOKITTY_QT", raising=False)
    qt = vu.Scenario(_scenario_ctx(tmp_path, old_mode="qt"), "d")
    assert qt.old_env["TOKITTY_QT"] == "1"
    assert "TOKITTY_QT" not in qt.env
    assert qt.apply_env({"server_url": "https://x", "certs": {"ca": "ca.pem"}})["TOKITTY_QT"] == "1"
    for ctx in (_scenario_ctx(tmp_path), _scenario_ctx(tmp_path, old_mode="tk")):
        tk = vu.Scenario(ctx, "e")
        assert "TOKITTY_QT" not in tk.old_env and "TOKITTY_QT" not in tk.env
        assert "TOKITTY_QT" not in tk.apply_env({"server_url": "https://x", "certs": {"ca": "ca.pem"}})


def test_an_inherited_tokitty_qt_never_reaches_a_tk_mode_old_process(vu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKITTY_QT", "1")
    scn = vu.Scenario(_scenario_ctx(tmp_path), "f")
    assert "TOKITTY_QT" not in scn.old_env and "TOKITTY_QT" not in scn.env


def _main_ctx(vu, tmp_path, monkeypatch, *extra):
    seen = {}
    monkeypatch.setattr(vu, "STEPS", [("prepare", lambda ctx: seen.update(ctx) or {"ok": True})])
    argv = [
        "--old-archive", str(tmp_path / "o"), "--new-archive", str(tmp_path / "n"), "--new-sums", str(tmp_path / "s"),
        "--tag", "v99.0.0", "--work", str(tmp_path / "w"), "--report", str(tmp_path / "r.json"), *extra,
    ]
    assert vu.main(argv) == 0
    return seen


def test_old_mode_defaults_to_tk_and_accepts_qt(vu, tmp_path, monkeypatch):
    assert _main_ctx(vu, tmp_path, monkeypatch)["old_mode"] == "tk"
    assert _main_ctx(vu, tmp_path, monkeypatch, "--old-mode", "qt")["old_mode"] == "qt"
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["old_mode"] == "qt"


def test_old_mode_rejects_other_values_and_the_qt_flag_is_gone(vu, tmp_path, monkeypatch):
    for extra in (("--old-mode", "both"), ("--qt",)):
        with pytest.raises(SystemExit):
            _main_ctx(vu, tmp_path, monkeypatch, *extra)


def test_the_qt_marker_check_applies_to_every_pass():
    # apply_update judges after-update-qt.json for the new copy whatever the old mode is.
    source = VERIFY_UPDATE_PATH.read_text(encoding="utf-8")
    assert 'ctx.get("qt")' not in source
    assert "problems += after_update_qt_problems(scn.state_dir)" in source


def test_after_update_qt_problems(vu, tmp_path):
    assert "missing" in vu.after_update_qt_problems(tmp_path)[0]
    path = tmp_path / "after-update-qt.json"
    path.write_text('{"exposed_before_ack": true, "waited_s": 0.4}', encoding="utf-8")
    assert vu.after_update_qt_problems(tmp_path) == []
    path.write_text('{"exposed_before_ack": false, "waited_s": 9}', encoding="utf-8")
    assert "exposed_before_ack" in vu.after_update_qt_problems(tmp_path)[0]
    path.write_text("not json", encoding="utf-8")
    assert "not JSON" in vu.after_update_qt_problems(tmp_path)[0]
    path.write_text("[]", encoding="utf-8")
    assert vu.after_update_qt_problems(tmp_path)


def test_the_constant_matches_the_qt_app_file_name(vu):
    from tokitty.qt.app import AFTER_UPDATE_QT_FILE

    assert vu.AFTER_UPDATE_QT_FILE == AFTER_UPDATE_QT_FILE
