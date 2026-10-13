"""The Qt side of an update handover (#113): the counterpart of TkHost.

The controller calls four methods, all on the GUI thread. The lock, runner
relink and autostart steps are update_controller.reclaim's, shared with Tk;
the tray goes off with the windows and comes back after them, as in TkHost.
Nothing here imports tkinter.
"""
from __future__ import annotations

from typing import Callable, List

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QWidget

from tokitty import update_controller
from tokitty.qt.deck_views import QtView


class QtUpdateHost:
    """`quiet` (the hidden --apply-update) opens no box, since nobody is there
    to close it; the caller reports through on_done."""

    def __init__(self, app, host, lock, state_dir, backend, *, tray,
                 tray_enabled: Callable[[], bool], quiet: bool = False):
        self._app = app
        self._host = host
        self._lock = lock
        self._state_dir = state_dir
        self._backend = backend
        self._quiet = quiet
        self._tray = tray
        self._tray_enabled = tray_enabled
        self._hidden = False
        self._finishing = False
        self._shown: List[QWidget] = []
        # Update presentations (a check result, the dialog) that arrived while
        # hidden; they run after restore and are dropped by finish.
        self._held: List[Callable[[], None]] = []

    @property
    def hidden(self) -> bool:
        return self._hidden

    def hide(self) -> None:
        """Handover step 5.3: tray off, windows away, lock released. Every visible
        top-level goes, so a modal box open while staging finished goes too:
        hiding one inside exec() ends its loop as if dismissed, so restore
        does not show dialogs or menus again. Everything else comes back,
        registered or not (the presets form is a child window). The Stream
        Dock views stay, as in Tk."""
        self._hidden = True
        self._shown = []
        self._tray.stop()
        for window in QApplication.topLevelWidgets():
            if not window.isVisible() or isinstance(window, QtView):
                continue
            if not isinstance(window, (QDialog, QMenu)):
                self._shown.append(window)
            window.hide()
        self._lock.release()

    def restore(self) -> None:
        """Handover step 5.8 without the message. Raises LockAcquisitionError
        if another copy holds the lock, in which case nothing is shown and the
        tray stays stopped. The tray comes back only if it is still enabled,
        and is refreshed either way."""
        update_controller.reclaim(self._lock, self._state_dir, self._backend)
        for window in self._shown:
            try:
                if self._host.is_registered(window):
                    self._host.restack(window)
                window.show()
            except RuntimeError:  # the C++ window is already gone
                pass
        self._shown = []
        if self._tray_enabled():
            self._tray.start()
        self._tray.refresh()
        self._hidden = False
        held, self._held = self._held, []
        for fn in held:
            # Next turn of the loop: one may open a modal box, and restore
            # runs inside tick() with the rollback still to finish.
            QTimer.singleShot(0, fn)

    def when_shown(self, fn: Callable[[], None]) -> None:
        """Run fn now, or after restore if the handover has the app hidden."""
        if self._hidden:
            if not self._finishing:
                self._held.append(fn)
            return
        fn()

    def finish(self) -> None:
        """Close the app. Deferred, because tick() calls this and still has
        widgets to touch, and safe to call twice."""
        if self._finishing:
            return
        self._finishing = True
        self._held = []
        QTimer.singleShot(0, self._app.quit)

    def notice(self, text: str) -> None:
        if self._quiet:
            return
        QTimer.singleShot(0, lambda: self._host.warn("Tokitty", text))
