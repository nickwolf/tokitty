"""Worker results for the Qt windows, delivered on the GUI thread.

The counterpart of settings_async.Poller. A worker only calls Mailbox.put();
a single-shot QTimer on the GUI thread drains the mailbox and hands each
result to a handler. Workers never touch a QObject.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QTimer

from tokitty.worker_mailbox import Mailbox


class QtPoller:
    """GUI-thread poll that feeds mailbox results to handler(tag, ok, value).

    It re-arms itself only while keep_alive() is true, so an idle tab costs
    nothing. cancel() stops the timer and discards anything still in the
    mailbox: results that arrive after close are ignored."""

    def __init__(self, owner: QObject, mailbox: Mailbox,
                 handler: Callable[[str, bool, Any], None],
                 keep_alive: Callable[[], bool],
                 on_tick: Optional[Callable[[], None]] = None,
                 interval_ms: int = 120) -> None:
        self._mailbox = mailbox
        self._handler = handler
        self._keep_alive = keep_alive
        self._on_tick = on_tick
        self._interval = interval_ms
        self._cancelled = False
        self._timer = QTimer(owner)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if self._cancelled or self._timer.isActive():
            return
        self._timer.start(self._interval)

    def cancel(self) -> None:
        self._cancelled = True
        self._timer.stop()
        self._mailbox.drain()

    def _tick(self) -> None:
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
