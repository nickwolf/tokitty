"""The system-tray icon (the menu-bar icon on macOS).

Everything runs on the GUI thread: QSystemTrayIcon lives there and its
signals arrive there, so there is no worker thread and no marshalling back.
Each menu action is still deferred with QTimer.singleShot(0, ...) so it never
runs inside the native menu's own event handling.

The menu is rendered once in start() and updated in place by refresh().
Clearing and re-rendering it can delete the QAction the user just picked: on macOS and on Linux's DBusMenu the native menu reports its
close before Qt delivers the activation to the action, and there is no
dependable signal for "the native menu is closed" to postpone a rebuild to.
So no QAction is created or deleted after start().

notify() is always False on macOS. Qt's Cocoa tray posts through the
deprecated NSUserNotificationCenter, which current macOS may drop without a
word for an app with no notification permission, and a True would end the
announcing for a toast nobody saw. It can be tried once there is a Qt Mac
bundle (#115). This module imports no pyobjc.

`available` is live: it starts as the system's answer and drops to False for
good if start() fails, so callers that read it later (the Settings row, the
update handover's restore) see a tray that cannot be shown.
"""
from __future__ import annotations

import sys
from typing import Callable, List, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from tokitty.qt.menu import render_menu
from tokitty.settings import update_settings
from tokitty.tray_icon import tray_raster


def _tray_qicon(colorway: str, pattern: str, tail: str = "plain") -> QIcon:
    """The shared tray sprite, centred on a transparent square."""
    width, height, raw = tray_raster(colorway, pattern, tail)
    sprite = QImage(raw, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    side = max(width, height)
    canvas = QImage(side, side, QImage.Format.Format_RGBA8888)
    canvas.fill(0)
    painter = QPainter(canvas)
    painter.drawImage((side - width) // 2, (side - height) // 2, sprite)
    painter.end()
    return QIcon(QPixmap.fromImage(canvas))


class QtTray:
    def __init__(self, menu_provider: Callable[[], List], state_dir,
                 colorway: str = "orange", pattern: str = "tabby", tail: str = "plain", *,
                 on_action_done: Optional[Callable[[], None]] = None,
                 icon_factory=None, tray_available=None, platform: str = sys.platform):
        self._menu_provider = menu_provider
        self._state_dir = state_dir
        self._on_action_done = on_action_done
        self._icon_factory = icon_factory or QSystemTrayIcon
        self._platform = platform
        self._qicon: Optional[QIcon] = None
        self._icon = None
        self._menu = None
        self._sync: Optional[Callable[[], None]] = None
        try:
            check = tray_available or QSystemTrayIcon.isSystemTrayAvailable
            self._qicon = _tray_qicon(colorway, pattern, tail)
            self.available = bool(check())
        except Exception:
            self._qicon = None
            self.available = False

    def _wrap(self, action):
        if action is None:
            return None

        def run() -> None:
            try:
                action()
            finally:
                # Qt toggled a checkable item when it was clicked; put the
                # marks back in line with the model even if the action raised
                # or left it unchanged.
                self.refresh()
            if self._on_action_done is not None:
                self._on_action_done()

        return lambda *args: QTimer.singleShot(0, run)

    def start(self) -> None:
        if not self.available or self._icon is not None:
            return
        try:
            icon = self._icon_factory()
            icon.setIcon(self._qicon)
            icon.setToolTip("Tokitty")
            menu = QMenu()
            self._menu = menu
            self._icon = icon
            self._sync = render_menu(menu, self._menu_provider(), self._wrap)
            icon.setContextMenu(menu)
            icon.show()
        except Exception as exc:
            # Recorded: from here the tray counts as unavailable.
            print(f"tokitty: tray: could not show the tray icon: {exc}", file=sys.stderr)
            self.available = False
            self._drop()

    def _drop(self) -> None:
        icon, menu = self._icon, self._menu
        self._icon = self._menu = self._sync = None
        for obj in (icon, menu):
            if obj is not None and hasattr(obj, "deleteLater"):
                try:
                    obj.deleteLater()
                except Exception:
                    pass

    def refresh(self) -> None:
        """Re-read the menu's getters in place. A no-op with no icon."""
        sync = self._sync
        if sync is None:
            return
        try:
            sync()
        except Exception:
            # A tray that fails to refresh must never take the app down.
            pass

    def notify(self, title: str, message: str) -> bool:
        """Show a tray message. True means submitted to Qt, not seen."""
        icon = self._icon
        if icon is None or self._platform == "darwin":
            return False
        try:
            if not icon.supportsMessages():
                return False
            icon.showMessage(title, message)
        except Exception:
            return False
        return True

    def debug_notify(self, title: str, message: str) -> bool:
        """notify without the macOS gate, for TOKITTY_DEBUG_NOTIFY (#115):
        one real try at the Cocoa tray's message, with the outcome on stderr."""
        icon = self._icon
        if icon is None:
            reason = "no tray icon"
        else:
            try:
                if icon.supportsMessages():
                    icon.showMessage(title, message)
                    print("tokitty: debug notify: submitted", file=sys.stderr)
                    return True
                reason = "the tray does not support messages"
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
        print(f"tokitty: debug notify: not submitted ({reason})", file=sys.stderr)
        return False

    def stop(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.hide()
        except Exception:
            pass
        self._drop()

    def set_enabled(self, enabled: bool) -> None:
        update_settings(self._state_dir, tray_enabled=enabled)
        if enabled:
            self.start()
        else:
            self.stop()
