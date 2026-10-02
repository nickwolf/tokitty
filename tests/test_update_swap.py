import errno
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokitty import update_swap
from tokitty.update_install import MAC_TOP, Staged, Target, UpdateInstallError, binary_paths, top_name
from tokitty.update_swap import (
    RENAME_SWAP,
    ack_path,
    backup_path,
    cleanup,
    clear_pending,
    clear_stale_pending,
    launch_detached,
    mac_swap_back,
    mac_swap_in,
    promote,
    stale_pending,
    swap_bundles,
    wait_for_ack,
    write_ack,
    write_pending,
)
from tokitty.updater import UpdateState, add_owned, load_update_state, save_update_state

TAG = "v0.3.0"
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _release_tree(top, sys_platform, marker="x"):
    gui, hook = binary_paths(top, sys_platform)
    _touch(gui, marker)
    _touch(hook, marker)
    return top


def _staged(parent, sys_platform, tag=TAG, state_dir=None):
    staging = parent / f".tokitty-update-{tag}-1"
    top = _release_tree(staging / "unpacked" / top_name(sys_platform), sys_platform, "new")
    _touch(staging / "archive.zip")
    if state_dir is not None:
        add_owned(state_dir, staging, tag, "staging")
    return Staged(staging, top, binary_paths(top, sys_platform)[0])


def _owned(state_dir):
    return {e["path"]: (e["version"], e["kind"]) for e in load_update_state(state_dir).owned}


@pytest.fixture
def state(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    return path


# promote


@pytest.mark.parametrize("sys_platform", ["linux", "win32"])
def test_promote_renames_into_the_version_folder(tmp_path, state, sys_platform):
    versions = tmp_path / "releases"
    staged = _staged(versions, sys_platform, state_dir=state)
    target = Target(versions, top_name(sys_platform), sys_platform)
    final = promote(staged, target, TAG, state, sys_platform=sys_platform)
    assert final == versions / TAG / top_name(sys_platform)
    assert binary_paths(final, sys_platform)[0].read_text() == "new"
    assert not staged.staging.exists()
    assert _owned(state) == {str(final): (TAG, "copy")}


def test_promote_reuses_an_owned_copy_that_passes_its_checks(tmp_path, state):
    versions = tmp_path / "releases"
    final = _release_tree(versions / TAG / "tokitty", "linux", "earlier")
    add_owned(state, final, TAG, "copy")
    staged = _staged(versions, "linux", state_dir=state)
    checked = []
    target = Target(versions, "tokitty", "linux")
    assert promote(staged, target, TAG, state, sys_platform="linux", self_check=checked.append) == final
    assert checked == [final / "tokitty"]
    assert (final / "tokitty").read_text() == "earlier"
    assert not staged.staging.exists()
    assert _owned(state) == {str(final): (TAG, "copy")}


def test_promote_refuses_an_owned_copy_that_fails_its_self_check(tmp_path, state):
    versions = tmp_path / "releases"
    final = _release_tree(versions / TAG / "tokitty", "linux", "earlier")
    add_owned(state, final, TAG, "copy")
    staged = _staged(versions, "linux")

    def failing(gui):
        raise UpdateInstallError("build mismatch")

    with pytest.raises(UpdateInstallError, match="build mismatch"):
        promote(staged, Target(versions, "tokitty", "linux"), TAG, state, sys_platform="linux", self_check=failing)
    assert (final / "tokitty").read_text() == "earlier"
    assert staged.top.exists()


def test_promote_refuses_an_owned_copy_with_a_bad_layout(tmp_path, state):
    versions = tmp_path / "releases"
    final = _release_tree(versions / TAG / "tokitty", "linux", "earlier")
    _touch(versions / TAG / "extra")
    add_owned(state, final, TAG, "copy")
    staged = _staged(versions, "linux")
    with pytest.raises(UpdateInstallError):
        promote(staged, Target(versions, "tokitty", "linux"), TAG, state, sys_platform="linux", self_check=lambda g: None)
    assert (versions / TAG / "extra").exists()
    assert staged.top.exists()


def test_promote_refuses_an_existing_path_the_updater_did_not_make(tmp_path, state):
    versions = tmp_path / "releases"
    final = _release_tree(versions / TAG / "tokitty", "linux", "by hand")
    staged = _staged(versions, "linux", state_dir=state)
    with pytest.raises(UpdateInstallError, match="didn't put it there"):
        promote(staged, Target(versions, "tokitty", "linux"), TAG, state, sys_platform="linux", self_check=lambda g: None)
    assert (final / "tokitty").read_text() == "by hand"
    assert staged.top.exists()
    assert str(final) not in _owned(state)


def test_promote_refuses_a_version_folder_that_is_a_link(tmp_path, state):
    versions = tmp_path / "releases"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    versions.mkdir()
    (versions / TAG).symlink_to(elsewhere, target_is_directory=True)
    staged = _staged(versions, "linux")
    with pytest.raises(UpdateInstallError, match="not a plain folder"):
        promote(staged, Target(versions, "tokitty", "linux"), TAG, state, sys_platform="linux")
    assert list(elsewhere.iterdir()) == []


def test_promote_failure_records_nothing_and_leaves_no_empty_folder(tmp_path, state, monkeypatch):
    versions = tmp_path / "releases"
    staged = _staged(versions, "linux")

    def refuse(src, dst):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "rename", refuse)
    with pytest.raises(UpdateInstallError, match="Could not move"):
        promote(staged, Target(versions, "tokitty", "linux"), TAG, state, sys_platform="linux")
    assert staged.top.exists()
    assert not (versions / TAG).exists()
    assert _owned(state) == {}


def test_promote_is_not_for_macos(tmp_path, state):
    with pytest.raises(ValueError):
        promote(_staged(tmp_path, "darwin"), Target(tmp_path, MAC_TOP, "darwin"), TAG, state, sys_platform="darwin")


# swap_bundles and the macOS swap


def _fake_swap(a, b):
    a, b = Path(a), Path(b)
    tmp = a.with_name(a.name + ".swaptmp")
    os.rename(a, tmp)
    os.rename(b, a)
    os.rename(tmp, b)


def test_swap_bundles_asks_for_rename_swap():
    calls = []
    swap_bundles("/a", "/b", renamex=lambda a, b, flags: calls.append((a, b, flags)))
    assert calls == [("/a", "/b", RENAME_SWAP)] and RENAME_SWAP == 0x2


@pytest.mark.parametrize("code", [errno.ENOTSUP, errno.EOPNOTSUPP])
def test_swap_bundles_maps_unsupported_to_a_release_page_error(code):
    def renamex(a, b, flags):
        raise OSError(code, "no")

    with pytest.raises(UpdateInstallError, match="release page"):
        swap_bundles("/a", "/b", renamex=renamex)


def test_swap_bundles_passes_other_errors_through():
    def renamex(a, b, flags):
        raise OSError(errno.EXDEV, "cross-device")

    with pytest.raises(OSError) as info:
        swap_bundles("/a", "/b", renamex=renamex)
    assert info.value.errno == errno.EXDEV


def test_swap_bundles_without_the_symbol_is_an_install_error():
    def renamex(a, b, flags):
        raise AttributeError("renamex_np")

    with pytest.raises(UpdateInstallError):
        swap_bundles("/a", "/b", renamex=renamex)


@pytest.mark.skipif(sys.platform != "darwin", reason="renamex_np exists only on macOS")
def test_swap_bundles_really_swaps_two_directories(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _touch(a / "who", "a")
    _touch(b / "who", "b")
    swap_bundles(a, b)
    assert (a / "who").read_text() == "b" and (b / "who").read_text() == "a"


def _mac_setup(tmp_path, state):
    apps = tmp_path / "Applications"
    app = _release_tree(apps / MAC_TOP, "darwin", "old")
    staged = _staged(apps, "darwin", state_dir=state)
    assert staged.top == staged.staging / "unpacked" / MAC_TOP
    return apps, app, staged


def test_mac_swap_in_leaves_the_new_bundle_and_a_hidden_backup(tmp_path, state):
    apps, app, staged = _mac_setup(tmp_path, state)
    backup = mac_swap_in(staged, app, "v0.2.1", state, swap=_fake_swap)
    assert backup == apps / ".Tokitty-v0.2.1.app" == backup_path(app, "v0.2.1")
    assert binary_paths(app, "darwin")[0].read_text() == "new"
    assert binary_paths(backup, "darwin")[0].read_text() == "old"
    assert not staged.staging.exists()
    assert _owned(state) == {str(backup): ("v0.2.1", "backup")}


def test_mac_swap_back_restores_the_old_bundle_and_removes_the_failed_one(tmp_path, state):
    apps, app, staged = _mac_setup(tmp_path, state)
    backup = mac_swap_in(staged, app, "v0.2.1", state, swap=_fake_swap)
    mac_swap_back(app, backup, state, swap=_fake_swap)
    assert binary_paths(app, "darwin")[0].read_text() == "old"
    assert not backup.exists()
    assert _owned(state) == {}
    assert sorted(p.name for p in apps.iterdir()) == [MAC_TOP]


def test_mac_swap_back_keeps_an_undeletable_failed_bundle_for_cleanup(tmp_path, state, monkeypatch):
    apps, app, staged = _mac_setup(tmp_path, state)
    backup = mac_swap_in(staged, app, "v0.2.1", state, swap=_fake_swap)
    monkeypatch.setattr(update_swap, "_trash_and_remove", lambda path, rename: "kept")
    mac_swap_back(app, backup, state, swap=_fake_swap)
    assert _owned(state) == {str(backup): ("v0.2.1", "staging")}


def test_mac_swap_in_that_cannot_swap_changes_nothing(tmp_path, state):
    apps, app, staged = _mac_setup(tmp_path, state)

    def unsupported(a, b):
        raise UpdateInstallError("release page")

    with pytest.raises(UpdateInstallError):
        mac_swap_in(staged, app, "v0.2.1", state, swap=unsupported)
    assert binary_paths(app, "darwin")[0].read_text() == "old"
    assert staged.top.exists()
    assert str(backup_path(app, "v0.2.1")) not in _owned(state)


def test_mac_swap_in_replaces_an_owned_backup_of_the_same_tag(tmp_path, state):
    apps, app, staged = _mac_setup(tmp_path, state)
    stale = _release_tree(backup_path(app, "v0.2.1"), "darwin", "stale")
    add_owned(state, stale, "v0.2.1", "backup")
    backup = mac_swap_in(staged, app, "v0.2.1", state, swap=_fake_swap)
    assert binary_paths(backup, "darwin")[0].read_text() == "old"


def test_mac_swap_in_refuses_an_unowned_backup_without_swapping(tmp_path, state):
    apps, app, staged = _mac_setup(tmp_path, state)
    mine = _release_tree(backup_path(app, "v0.2.1"), "darwin", "by hand")
    swaps = []
    with pytest.raises(UpdateInstallError, match="already there"):
        mac_swap_in(staged, app, "v0.2.1", state, swap=lambda a, b: swaps.append((a, b)))
    assert swaps == []
    assert binary_paths(mine, "darwin")[0].read_text() == "by hand"


def test_mac_swap_in_undoes_the_swap_when_it_cannot_keep_the_old_bundle(tmp_path, state, monkeypatch):
    apps, app, staged = _mac_setup(tmp_path, state)
    real_rename = os.rename

    def rename(src, dst):
        if Path(dst).name.startswith(".Tokitty-"):
            raise PermissionError(errno.EACCES, "denied")
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(UpdateInstallError, match="not installed"):
        mac_swap_in(staged, app, "v0.2.1", state, swap=_fake_swap)
    assert binary_paths(app, "darwin")[0].read_text() == "old"
    assert str(backup_path(app, "v0.2.1")) not in _owned(state)


# pending, launch and ack


def test_write_pending_records_the_handover(state):
    record = write_pending(
        state,
        old_version="v0.2.1",
        new_version=TAG,
        old_path="/r/v0.2.1/tokitty",
        new_path="/r/v0.3.0/tokitty",
        token="abc123",
        staging="/r/.tokitty-update-v0.3.0-1",
        now=NOW,
    )
    assert load_update_state(state).pending == record
    assert record["token"] == "abc123" and record["started"] == NOW.isoformat()
    assert record["staging"] == "/r/.tokitty-update-v0.3.0-1"


def test_clear_pending_with_a_token_only_clears_its_own_record(state):
    write_pending(state, old_version="a", new_version="b", old_path="o", new_path="n", token="one", staging="s")
    clear_pending(state, "two")
    assert load_update_state(state).pending is not None
    clear_pending(state, "one")
    assert load_update_state(state).pending is None
    write_pending(state, old_version="a", new_version="b", old_path="o", new_path="n", token="one", staging="s")
    clear_pending(state)
    assert load_update_state(state).pending is None


def test_pending_edits_keep_the_other_fields(state):
    save_update_state(state, UpdateState(latest_tag=TAG, owned=[{"path": "/r/a", "version": "v0.1.0", "kind": "copy"}]))
    write_pending(state, old_version="a", new_version="b", old_path="o", new_path="n", token="t", staging="s")
    clear_pending(state)
    loaded = load_update_state(state)
    assert loaded.latest_tag == TAG and len(loaded.owned) == 1


def test_stale_pending_is_older_than_five_minutes():
    def state(started):
        return UpdateState(pending={"started": started})

    assert not stale_pending(UpdateState(), NOW)
    assert not stale_pending(state((NOW - timedelta(minutes=4)).isoformat()), NOW)
    assert stale_pending(state((NOW - timedelta(minutes=6)).isoformat()), NOW)
    assert stale_pending(state("2026-10-02T11:00:00"), NOW)
    assert stale_pending(state("garbage"), NOW)
    assert stale_pending(UpdateState(pending={}), NOW)


def test_clear_stale_pending_only_clears_a_stale_record(state):
    write_pending(state, old_version="a", new_version="b", old_path="o", new_path="n", token="t", staging="s", now=NOW)
    assert clear_stale_pending(state, NOW + timedelta(minutes=1)) is False
    assert load_update_state(state).pending is not None
    assert clear_stale_pending(state, NOW + timedelta(minutes=6)) is True
    assert load_update_state(state).pending is None


class FakePopen:
    def __init__(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs


def test_launch_detached_on_windows_is_detached_in_its_own_group():
    proc = launch_detached(["Tokitty.exe", "--after-update", "t"], {"A": "1"}, "win32", popen=FakePopen)
    assert proc.argv == ["Tokitty.exe", "--after-update", "t"]
    assert proc.kwargs["creationflags"] == 0x8 | 0x200
    assert proc.kwargs["close_fds"] is True
    assert "start_new_session" not in proc.kwargs
    assert proc.kwargs["env"] == {"A": "1"}


@pytest.mark.parametrize("sys_platform", ["linux", "darwin"])
def test_launch_detached_elsewhere_starts_a_new_session(sys_platform):
    proc = launch_detached(["tokitty"], {}, sys_platform, popen=FakePopen)
    assert proc.kwargs["start_new_session"] is True
    assert "creationflags" not in proc.kwargs
    assert all(proc.kwargs[k] is not None for k in ("stdin", "stdout", "stderr"))


def test_ack_path_rejects_a_token_that_could_escape(state):
    assert ack_path(state, "abc-123_X") == state / "update-ack-abc-123_X"
    for bad in ("", "../x", "a/b", "a b", "x" * 65):
        with pytest.raises(ValueError):
            ack_path(state, bad)


def test_wait_for_ack_returns_true_once_written_and_removes_the_file(state):
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds
        if now[0] >= 1.0:
            write_ack(state, "tok")

    path = ack_path(state, "tok")
    assert wait_for_ack(path, 120, poll=0.25, clock=lambda: now[0], sleep=sleep) is True
    assert now[0] == 1.0
    assert not path.exists()


def test_wait_for_ack_times_out(state):
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    assert wait_for_ack(ack_path(state, "tok"), 2.0, poll=0.5, clock=lambda: now[0], sleep=sleep) is False
    assert now[0] == 2.0


def test_write_ack_leaves_no_temp_file(state):
    write_ack(state, "tok")
    assert [p.name for p in state.iterdir()] == ["update-ack-tok"]


# cleanup


class Layout:
    """A versions dir shaped like Nick's: updater-installed copies, a staging
    folder, hand-unpacked copies, and the links a cleanup must not follow."""

    def __init__(self, tmp_path, state):
        self.state = state
        self.versions = tmp_path / "releases"
        self.outside = tmp_path / "outside"
        _touch(self.outside / "precious", "keep me")
        self.target = Target(self.versions, "tokitty", "linux")

    def copy(self, tag, owned=True):
        top = _release_tree(self.versions / tag / "tokitty", "linux", tag)
        if owned:
            add_owned(self.state, top, tag, "copy")
        return top

    def staging(self, tag, pid, owned=True):
        folder = _touch(self.versions / f".tokitty-update-{tag}-{pid}" / "unpacked" / "x").parent.parent
        if owned:
            add_owned(self.state, folder, tag, "staging")
        return folder

    def run(self, running, current=None, **kwargs):
        running_top = self.versions / running / "tokitty"
        return cleanup(
            self.state,
            running_release=running_top,
            current_target=current,
            running_version=running,
            target=self.target,
            now=NOW,
            **kwargs,
        )


def test_cleanup_deletes_only_what_the_updater_owns(tmp_path, state):
    lay = Layout(tmp_path, state)
    running, replaced = lay.copy("v0.3.0"), lay.copy("v0.2.0")
    current = lay.copy("v0.1.5")
    older = lay.copy("v0.1.0")
    stale_staging = lay.staging("v0.2.5", 99)
    (stale_staging / "unpacked" / "link").symlink_to(lay.outside / "precious")
    live_staging = lay.staging("v0.3.1", 100)
    by_hand = lay.copy("v0.0.0", owned=False)
    unowned_staging = lay.staging("v0.0.2", 7, owned=False)
    (lay.versions / "v0.0.1").symlink_to(lay.outside, target_is_directory=True)
    (lay.outside / "tokitty").mkdir()
    add_owned(state, lay.versions / "v0.0.1" / "tokitty", "v0.0.1", "copy")
    (lay.versions / "v0.0.3").mkdir()
    (lay.versions / "v0.0.3" / "tokitty").symlink_to(lay.outside, target_is_directory=True)
    add_owned(state, lay.versions / "v0.0.3" / "tokitty", "v0.0.3", "copy")
    write_pending(
        state,
        old_version="v0.3.0",
        new_version="v0.3.1",
        old_path=running,
        new_path="n",
        token="t",
        staging=live_staging,
        now=NOW - timedelta(minutes=1),
    )

    removed = lay.run("v0.3.0", current=current)

    assert sorted(removed) == sorted([str(older), str(stale_staging)])
    for kept in (running, replaced, current, live_staging, by_hand, unowned_staging):
        assert kept.is_dir()
    assert not older.exists() and not older.parent.exists()
    assert not stale_staging.exists()
    assert (lay.outside / "precious").read_text() == "keep me" and (lay.outside / "tokitty").is_dir()
    assert (lay.versions / "v0.0.1").is_symlink() and (lay.versions / "v0.0.3" / "tokitty").is_symlink()
    assert not [p for p in lay.versions.iterdir() if p.name.startswith(".tokitty-trash-")]
    owned = _owned(state)
    assert str(older) not in owned and str(stale_staging) not in owned
    for kept in (running, replaced, current, live_staging):
        assert str(kept) in owned
    assert str(lay.versions / "v0.0.1" / "tokitty") in owned and str(lay.versions / "v0.0.3" / "tokitty") in owned
    assert str(by_hand) not in owned


def test_cleanup_deletes_a_staging_folder_named_by_a_stale_pending(tmp_path, state):
    lay = Layout(tmp_path, state)
    lay.copy("v0.3.0")
    staging = lay.staging("v0.3.1", 100)
    write_pending(
        state, old_version="a", new_version="b", old_path="o", new_path="n", token="t", staging=staging, now=NOW - timedelta(hours=1)
    )
    assert lay.run("v0.3.0") == [str(staging)]


def test_cleanup_keeps_every_copy_when_nothing_is_older_than_the_running_one(tmp_path, state):
    lay = Layout(tmp_path, state)
    running, newer = lay.copy("v0.3.0"), lay.copy("v0.4.0")
    assert lay.run("v0.3.0") == []
    assert running.is_dir() and newer.is_dir()


def test_cleanup_never_deletes_the_running_release_even_when_it_is_old(tmp_path, state):
    lay = Layout(tmp_path, state)
    lay.copy("v0.5.0")
    running = lay.copy("v0.1.0")
    lay.copy("v0.2.0")
    removed = lay.run("v0.3.0", current=running)
    assert removed == []
    running_dir = lay.versions / "v0.1.0" / "tokitty"
    assert running_dir.is_dir()
    assert lay.run("v0.1.0") == []


def test_cleanup_does_not_delete_when_the_rename_is_refused(tmp_path, state):
    lay = Layout(tmp_path, state)
    lay.copy("v0.3.0"), lay.copy("v0.2.0")
    older, stale_staging = lay.copy("v0.1.0"), lay.staging("v0.2.5", 99)

    def denied(src, dst):
        raise PermissionError(errno.EACCES, "in use")

    assert lay.run("v0.3.0", rename=denied) == []
    assert (older / "tokitty").read_text() == "v0.1.0"
    assert (stale_staging / "unpacked" / "x").exists()
    assert {str(older), str(stale_staging)} <= set(_owned(state))
    assert not [p for p in lay.versions.iterdir() if p.name.startswith(".tokitty-trash-")]
    assert sorted(lay.run("v0.3.0")) == sorted([str(older), str(stale_staging)])


def test_cleanup_keeps_the_entry_of_a_folder_it_could_only_half_remove_and_finishes_next_time(tmp_path, state, monkeypatch):
    lay = Layout(tmp_path, state)
    lay.copy("v0.3.0"), lay.copy("v0.2.0")
    older = lay.copy("v0.1.0")
    monkeypatch.setattr(update_swap.shutil, "rmtree", lambda path, ignore_errors=False: None)
    assert lay.run("v0.3.0") == []
    trash = older.with_name(".tokitty-trash-tokitty")
    assert trash.is_dir() and not older.exists()
    assert str(older) in _owned(state)
    monkeypatch.undo()
    assert lay.run("v0.3.0") == []
    assert not trash.exists()
    assert str(older) not in _owned(state)


def test_cleanup_removes_leftover_trash_beside_owned_paths_only(tmp_path, state):
    lay = Layout(tmp_path, state)
    lay.copy("v0.3.0")
    leftover = _touch(lay.versions / "v0.3.0" / ".tokitty-trash-tokitty" / "f").parent
    unrelated = _touch(lay.versions / ".tokitty-trash-v0.1.0" / "f").parent
    lay.run("v0.3.0")
    assert not leftover.exists()
    assert unrelated.exists()


def test_cleanup_ignores_entries_outside_the_versions_dir_or_with_the_wrong_name(tmp_path, state):
    lay = Layout(tmp_path, state)
    lay.copy("v0.3.0"), lay.copy("v0.2.0")
    stray = _touch(tmp_path / "Documents" / "keep")
    wrong_name = _touch(lay.versions / "notes" / "keep")
    other_tree = _release_tree(tmp_path / "other" / "v0.1.0" / "tokitty", "linux")
    add_owned(state, stray.parent, "v0.0.1", "staging")
    add_owned(state, wrong_name.parent, "v0.0.1", "staging")
    add_owned(state, lay.versions, "v0.1.0", "copy")
    add_owned(state, other_tree, "v0.1.0", "copy")
    add_owned(state, "relative/path", "v0.1.0", "copy")
    assert lay.run("v0.3.0") == []
    assert stray.exists() and wrong_name.exists() and other_tree.exists() and lay.versions.is_dir()
    assert len(_owned(state)) == 7


def test_cleanup_prunes_entries_whose_path_is_gone(tmp_path, state):
    lay = Layout(tmp_path, state)
    lay.copy("v0.3.0")
    gone = lay.copy("v0.1.0")
    shutil.rmtree(gone.parent)
    assert lay.run("v0.3.0") == []
    assert str(gone) not in _owned(state)


def test_cleanup_does_not_count_a_missing_copy_as_the_replaced_one(tmp_path, state):
    lay = Layout(tmp_path, state)
    lay.copy("v0.3.0")
    gone = lay.copy("v0.2.0")
    shutil.rmtree(gone.parent)
    older = lay.copy("v0.1.0")
    assert lay.run("v0.3.0") == []
    assert older.is_dir()


def test_cleanup_with_no_pending_and_no_owned_entries_does_nothing(tmp_path, state):
    lay = Layout(tmp_path, state)
    by_hand = lay.copy("v0.1.0", owned=False)
    assert lay.run("v0.3.0") == []
    assert by_hand.is_dir()
    assert not (state / "update.json").exists()


def test_cleanup_on_macos_treats_a_backup_as_the_replaced_copy(tmp_path, state):
    apps = tmp_path / "Applications"
    app = _release_tree(apps / MAC_TOP, "darwin", "running")
    replaced = _release_tree(apps / ".Tokitty-v0.2.0.app", "darwin")
    older = _release_tree(apps / ".Tokitty-v0.1.0.app", "darwin")
    add_owned(state, replaced, "v0.2.0", "backup")
    add_owned(state, older, "v0.1.0", "backup")
    stale_staging = _touch(apps / ".tokitty-update-v0.2.5-9" / "x").parent
    add_owned(state, stale_staging, "v0.2.5", "staging")
    running_release = app / "Contents" / "MacOS"
    removed = cleanup(
        state,
        running_release=running_release,
        current_target=running_release,
        running_version="v0.3.0",
        target=Target(apps, MAC_TOP, "darwin"),
        now=NOW,
    )
    assert sorted(removed) == sorted([str(older), str(stale_staging)])
    assert app.is_dir() and replaced.is_dir()
    assert set(_owned(state)) == {str(replaced)}


def test_cleanup_never_deletes_a_bundle_the_running_release_lives_inside(tmp_path, state):
    apps = tmp_path / "Applications"
    replaced = _release_tree(apps / ".Tokitty-v0.2.0.app", "darwin")
    running_from_backup = _release_tree(apps / ".Tokitty-v0.1.0.app", "darwin")
    add_owned(state, replaced, "v0.2.0", "backup")
    add_owned(state, running_from_backup, "v0.1.0", "backup")
    removed = cleanup(
        state,
        running_release=running_from_backup / "Contents" / "MacOS",
        current_target=running_from_backup / "Contents" / "MacOS",
        running_version="v0.3.0",
        target=Target(apps, MAC_TOP, "darwin"),
        now=NOW,
    )
    assert removed == [] and running_from_backup.is_dir()
