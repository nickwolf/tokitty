"""Worker results for the Settings window, delivered on the Tk thread.

Worker threads never touch Tk, not even root.after: a worker-side Tk call
reproduced as a hard interpreter abort in this repo. A worker only calls
Mailbox.put(); a Poller scheduled with toplevel.after on the Tk thread drains
the mailbox and hands each result to a handler. The shape matches
AccountsManager._poll_retry_done.
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Any, Callable, List, Optional, Tuple


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
        except Exception as exc:  # reported to the Tk side, never raised here
            mailbox.put(tag, False, exc)
        else:
            mailbox.put(tag, True, value)

    threading.Thread(target=target, daemon=True, name=f"settings-{tag}").start()


class Poller:
    """Tk-thread poll that feeds mailbox results to handler(tag, ok, value).

    It reschedules itself only while keep_alive() is true, so an idle tab
    costs nothing. cancel() drops the pending after() id and discards
    anything still in the mailbox: results that arrive after close are
    ignored."""

    def __init__(self, toplevel: tk.Misc, mailbox: Mailbox,
                 handler: Callable[[str, bool, Any], None],
                 keep_alive: Callable[[], bool], interval_ms: int = 120,
                 on_tick: Optional[Callable[[], None]] = None) -> None:
        self._toplevel = toplevel
        self._mailbox = mailbox
        self._handler = handler
        self._keep_alive = keep_alive
        self._on_tick = on_tick
        self._interval = interval_ms
        self._after_id: Optional[str] = None
        self._cancelled = False

    def start(self) -> None:
        if self._cancelled or self._after_id is not None:
            return
        try:
            self._after_id = self._toplevel.after(self._interval, self._tick)
        except tk.TclError:
            self._cancelled = True

    def cancel(self) -> None:
        self._cancelled = True
        after_id, self._after_id = self._after_id, None
        if after_id is not None:
            try:
                self._toplevel.after_cancel(after_id)
            except tk.TclError:
                pass
        self._mailbox.drain()

    def _tick(self) -> None:
        self._after_id = None
        if self._cancelled:
            return
        for tag, ok, value in self._mailbox.drain():
            if self._cancelled:
                return
            self._handler(tag, ok, value)
        if self._cancelled:
            return
        if self._on_tick is not None:
            self._on_tick()
        if self._keep_alive():
            self.start()
