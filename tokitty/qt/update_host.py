"""The Qt side of an update handover (#113): the counterpart of TkHost.

The controller calls four methods, all on the GUI thread. The lock, runner
relink and autostart steps are update_controller.reclaim's, shared with Tk.
Nothing here imports tkinter.
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

from tokitty import update_controller
from tokitty.qt.deck_views import QtView


class QtUpdateHost:
    """`quiet` (the hidden --apply-update) opens no box, since nobody is there
    to close it; the caller reports through on_done."""

    def __init__(self, app, host, lock, state_dir, backend, *, quiet: bool = False):
        self._app = app
        self._host = host
        self._lock = lock
        self._state_dir = state_dir
        self._backend = backend
        self._quiet = quiet
        self._hidden = False
        self._finishing = False
        self._shown: List[QWidget] = []

    @property
    def hidden(self) -> bool:
        return self._hidden

    def hide(self) -> None:
        """Handover step 5.3: windows away, lock released. Every visible
        top-level goes, so a modal box open while staging finished goes too:
        hiding one inside exec() ends its loop as if dismissed, and restore
        does not show it again. The Stream Dock views stay, as in Tk."""
        self._hidden = True
        self._shown = []
        for window in QApplication.topLevelWidgets():
            if not window.isVisible() or isinstance(window, QtView):
                continue
            if window is self._host.widget or self._host.is_registered(window):
                self._shown.append(window)
            window.hide()
        self._lock.release()

    def restore(self) -> None:
        """Handover step 5.8 without the message. Raises LockAcquisitionError
        if another copy holds the lock, in which case nothing is shown."""
        update_controller.reclaim(self._lock, self._state_dir, self._backend)
        for window in self._shown:
            try:
                self._host.restack(window)
                window.show()
            except RuntimeError:  # the C++ window is already gone
                pass
        self._shown = []
        self._hidden = False

    def finish(self) -> None:
        """Close the app. Deferred, because tick() calls this and still has
        widgets to touch, and safe to call twice."""
        if self._finishing:
            return
        self._finishing = True
        QTimer.singleShot(0, self._app.quit)

    def notice(self, text: str) -> None:
        if self._quiet:
            return
        QTimer.singleShot(0, lambda: self._host.warn("Tokitty", text))
