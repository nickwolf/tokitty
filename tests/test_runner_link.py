"""Tests for tokitty/runner_link.py: the stable hook path (spec Q2a, #48 Task 3).

SAFETY: every link created here lives under its own tmp_path and is torn
down with os.unlink (POSIX) / os.rmdir (Windows junction) in the `state`
fixture -- never shutil.rmtree, and never a link to a real directory
outside tmp_path. No test here patches sys.platform: link mechanics run
for real on the host OS (junction tests on Windows CI legs, symlink tests
elsewhere).
"""
import os
import sys

import pytest

from tokitty import hooks_install, runner_link
from tokitty.frozen import AppTranslocatedError
from tokitty.lock import SingleInstanceLock

EXE = "tokitty.exe" if sys.platform == "win32" else "tokitty"
RUNNER = "tokitty-hook.exe" if sys.platform == "win32" else "tokitty-hook"


def _release(root):
    root.mkdir(parents=True)
    (root / EXE).write_text("gui", encoding="utf-8")
    (root / RUNNER).write_text("hook", encoding="utf-8")
    (root / "_internal").mkdir()
    (root / "_internal" / "marker").write_text("x", encoding="utf-8")
    return root / EXE


def _listing(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file())


@pytest.fixture
def state(tmp_path):
    state_dir = tmp_path / "state"
    yield state_dir
    link = state_dir / "current"
    if runner_link._is_link(str(link)):
        (os.rmdir if sys.platform == "win32" else os.unlink)(link)


def test_first_launch_creates_link_and_returns_stable_path(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    outcome = runner_link.ensure_runner_link(state, executable=str(a))
    assert outcome.note is None
    assert outcome.runner == hooks_install.stable_runner_path(state, sys.platform)
    assert runner_link._is_link(str(state / "current"))
    assert os.path.realpath(state / "current") == os.path.realpath(a.parent)


def test_stable_path_identical_across_two_releases(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    b = _release(tmp_path / "rel-b")
    before = (_listing(a.parent), _listing(b.parent))
    first = runner_link.ensure_runner_link(state, executable=str(a))
    second = runner_link.ensure_runner_link(state, executable=str(b))
    assert first.runner == second.runner
    assert os.path.realpath(state / "current") == os.path.realpath(b.parent)
    assert (_listing(a.parent), _listing(b.parent)) == before


def test_moved_release_is_repointed(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    runner_link.ensure_runner_link(state, executable=str(a))
    moved = tmp_path / "moved"
    os.rename(a.parent, moved)
    runner_link.ensure_runner_link(state, executable=str(moved / EXE))
    assert os.path.realpath(state / "current") == os.path.realpath(moved)


def test_launch_through_link_keeps_real_target(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    runner_link.ensure_runner_link(state, executable=str(a))
    runner_link.ensure_runner_link(state, executable=str(state / "current" / EXE))
    assert os.path.realpath(state / "current") == os.path.realpath(a.parent)


def test_real_directory_left_alone(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    (state / "current").mkdir(parents=True)
    (state / "current" / "keep.txt").write_text("mine", encoding="utf-8")
    outcome = runner_link.ensure_runner_link(state, executable=str(a))
    assert outcome.runner == str(a.parent / RUNNER) and outcome.note
    assert (state / "current" / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_translocated_leaves_link_alone(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    runner_link.ensure_runner_link(state, executable=str(a))
    t = _release(tmp_path / "private" / "AppTranslocation" / "x" / "rel")
    with pytest.raises(AppTranslocatedError):
        runner_link.ensure_runner_link(state, executable=str(t))
    assert os.path.realpath(state / "current") == os.path.realpath(a.parent)


def test_release_without_hook_runner_raises_and_creates_nothing(tmp_path, state):
    root = tmp_path / "rel-bare"
    root.mkdir(parents=True)
    exe = root / EXE
    exe.write_text("gui", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        runner_link.ensure_runner_link(state, executable=str(exe))
    assert not (state / "current").exists()
    assert not runner_link._is_link(str(state / "current"))


def test_repoint_failure_leaves_old_link_target_in_place(tmp_path, state, monkeypatch):
    a = _release(tmp_path / "rel-a")
    b = _release(tmp_path / "rel-b")
    runner_link.ensure_runner_link(state, executable=str(a))

    if sys.platform == "win32":
        import _winapi

        real_create_junction = _winapi.CreateJunction

        def flaky(target, link):
            if os.path.normcase(target) == os.path.normcase(str(b.parent)):
                raise OSError("boom")
            return real_create_junction(target, link)

        monkeypatch.setattr(_winapi, "CreateJunction", flaky)
    else:

        def flaky(*args, **kwargs):
            raise OSError("boom")

        monkeypatch.setattr(runner_link.os, "symlink", flaky)

    with pytest.raises(OSError):
        runner_link.ensure_runner_link(state, executable=str(b))

    assert os.path.realpath(state / "current") == os.path.realpath(a.parent)


def test_second_caller_times_out_while_lock_is_held(tmp_path, state):
    a = _release(tmp_path / "rel-a")
    runner_link.ensure_runner_link(state, executable=str(a))

    holder = SingleInstanceLock(state, name="current.lock")
    holder.acquire()
    try:
        b = _release(tmp_path / "rel-b")
        with pytest.raises(OSError):
            runner_link.ensure_runner_link(state, executable=str(b), lock_timeout=0.2)
    finally:
        holder.release()

    assert os.path.realpath(state / "current") == os.path.realpath(a.parent)


def test_self_loop_guard_refuses_release_inside_link(tmp_path, state):
    # A release nested inside the link path itself must never be linked
    # to: resolving it would create a link that points into itself.
    inside = state / "current" / "rel"
    _release(inside)
    with pytest.raises(OSError):
        runner_link.ensure_runner_link(state, executable=str(inside / EXE))
