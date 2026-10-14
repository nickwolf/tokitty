"""Toolkit-free worker results: a Mailbox filled by worker threads.

Workers only ever call Mailbox.put(); the GUI thread drains it, so a worker
never touches a widget.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, List, Tuple


class Mailbox:
    """A lock-guarded list of (tag, ok, value) results, filled by workers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: List[Tuple[str, bool, Any]] = []

    def put(self, tag: str, ok: bool, value: Any) -> None:
        with self._lock:
            self._items.append((tag, ok, value))

    def drain(self) -> List[Tuple[str, bool, Any]]:
        with self._lock:
            items, self._items = self._items, []
        return items


def run_worker(mailbox: Mailbox, tag: str, fn: Callable[[], Any]) -> None:
    """Run fn on a daemon thread; its return value (or exception) lands in the mailbox."""

    def target() -> None:
        try:
            value = fn()
        except Exception as exc:  # reported to the GUI side, never raised here
            mailbox.put(tag, False, exc)
        else:
            mailbox.put(tag, True, value)

    threading.Thread(target=target, daemon=True, name=f"settings-{tag}").start()
