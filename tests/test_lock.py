import sys
import types

import pytest

from tokitty.lock import LockAcquisitionError, SingleInstanceLock


def test_second_lock_acquisition_fails_while_first_holds_it(tmp_path):
    first = SingleInstanceLock(tmp_path)
    first.acquire()
    try:
        second = SingleInstanceLock(tmp_path)
        with pytest.raises(LockAcquisitionError):
            second.acquire()
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path):
    first = SingleInstanceLock(tmp_path)
    first.acquire()
    first.release()

    second = SingleInstanceLock(tmp_path)
    second.acquire()
    second.release()


def test_context_manager_releases_on_exit(tmp_path):
    with SingleInstanceLock(tmp_path):
        pass

    with SingleInstanceLock(tmp_path):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="targets the POSIX fcntl branch specifically")
def test_uses_flock_on_posix(tmp_path, monkeypatch):
    import fcntl as real_fcntl
    original_flock = real_fcntl.flock

    calls = []

    def spy_flock(fd, operation):
        calls.append(operation)
        return original_flock(fd, operation)

    monkeypatch.setattr("fcntl.flock", spy_flock)

    with SingleInstanceLock(tmp_path):
        pass

    assert (real_fcntl.LOCK_EX | real_fcntl.LOCK_NB) in calls


def test_windows_branch_calls_msvcrt_locking(tmp_path, monkeypatch):
    calls = []

    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.LK_NBLCK = 1
    fake_msvcrt.LK_UNLCK = 2
    fake_msvcrt.locking = lambda fd, mode, nbytes: calls.append(mode)

    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sys, "platform", "win32")

    lock = SingleInstanceLock(tmp_path)
    lock.acquire()
    lock.release()

    assert calls == [1, 2]
