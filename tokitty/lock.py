"""Cross-platform single-instance advisory file lock."""
from __future__ import annotations

import sys
from pathlib import Path


class LockAcquisitionError(Exception):
    """Raised when another instance already holds the lock."""


class SingleInstanceLock:
    """An OS-native advisory lock held for the process's lifetime.

    Uses fcntl.flock on POSIX and msvcrt.locking on Windows. The OS
    releases the lock automatically if the process exits or crashes, so
    unlike a PID file this can never go stale.
    """

    def __init__(self, lock_dir: Path, name: str = "tokitty.lock"):
        self._path = lock_dir / name
        self._file = None

    def acquire(self) -> None:
        self._file = open(self._path, "a+")
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self._file.close()
                self._file = None
                raise LockAcquisitionError("Another Tokitty instance is already running") from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._file.close()
                self._file = None
                raise LockAcquisitionError("Another Tokitty instance is already running") from exc

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
