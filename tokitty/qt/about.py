"""The About Tokitty window: the cat in pane 0's coat, the name and version,
what it is, links, and the notices the licences ask us to show.

A plain non-modal window, one at a time, brought to the front the way the
Settings window is (host.register_window for the stay-on-top hint, then
activate_app, raise_, activateWindow). The same window serves every platform.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional, Tuple

from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from tokitty.qt import macos
from tokitty.qt.kit import Kit, SpritePreview, text, vbox
from tokitty.settings_style import DIM_COLOR, FONT_BODY, FONT_SMALL, FONT_TITLE, MUTED
from tokitty.sprites import resolve_palette

GITHUB_URL = "https://github.com/nickwolf/tokitty"
RELEASE_URL = GITHUB_URL + "/releases/tag/{tag}"
DESCRIPTION = "A cat-themed desktop widget for tracking Claude Code and Codex usage limits."
COPYRIGHT = "Copyright © 2026 Nick Wolf. MIT License."
CAT_CELL = 4  # logical pixels per sprite cell
WIDTH = 380

_instance: Optional["AboutWindow"] = None


def licenses_dir(frozen: bool, executable: Optional[str] = None,
                 platform: str = sys.platform) -> Optional[Path]:
    """Where a frozen build keeps its licence files: beside the executable on
    Windows and Linux, in Tokitty.app/Contents/Resources on macOS. None for a
    source run, which has no bundle."""
    if not frozen:
        return None
    exe = Path(executable or sys.executable)
    if platform == "darwin":
        return exe.parent.parent / "Resources" / "licenses"
    return exe.parent / "licenses"


def release_notes_url(version: str) -> Optional[str]:
    """The release page for a version that is a release tag, else None."""
    from tokitty.updater import parse_version

    return RELEASE_URL.format(tag=version) if parse_version(version) is not None else None


def qt_notice() -> str:
    """LGPL-3.0 section 4(c): the notice goes wherever copyright is shown."""
    import PySide6

    return (f"Includes Qt {qVersion()} and PySide6 {PySide6.__version__} "
            "under the LGPL-3.0, see the licenses folder.")


def _open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class AboutWindow(QWidget):
    """Use AboutWindow.open(), never the constructor."""

    @classmethod
    def open(cls, host, coat: Callable[[], Tuple[str, str]], *,
             frozen: Optional[bool] = None) -> "AboutWindow":
        """Show the window, or raise the one already open. `coat` returns
        (colorway, pattern) of pane 0 and is read each time a window is built."""
        global _instance
        existing = _instance
        if existing is not None and existing.alive() and existing.host is host:
            existing.raise_window()
            return existing
        if existing is not None:
            existing.close()
        if frozen is None:
            frozen = bool(getattr(sys, "frozen", False))
        instance = _instance = cls(host, coat(), frozen)
        instance.raise_window()
        return instance

    def __init__(self, host, coat: Tuple[str, str], frozen: bool):
        super().__init__()
        from tokitty.app_version import version_text

        self.host = host
        self.kit = Kit()
        self._closed = False
        self.version = version_text()
        self.kit.style_window(self)
        self.setWindowTitle("About Tokitty")
        self.setFixedWidth(WIDTH)
        # Parentless, so it follows the widget's Always in front itself.
        host.register_window(self)

        column = vbox(self)
        column.setContentsMargins(28, 24, 28, 18)
        center = Qt.AlignmentFlag.AlignHCenter

        colorway, pattern = coat
        self.cat = SpritePreview(resolve_palette(colorway, pattern), "content", cell=CAT_CELL)
        column.addWidget(self.cat, 0, center)
        column.addSpacing(14)

        self.title = text("Tokitty", font=FONT_TITLE)
        column.addWidget(self.title, 0, center)
        self.version_label = text(self.version, color=MUTED, font=FONT_BODY)
        column.addWidget(self.version_label, 0, center)
        self.source_label = None
        if not frozen:
            self.source_label = text("Running from source", color=DIM_COLOR, font=FONT_SMALL)
            column.addWidget(self.source_label, 0, center)
        column.addSpacing(12)

        self.description = self._wrapped(DESCRIPTION, MUTED, FONT_BODY)
        column.addWidget(self.description)
        column.addSpacing(14)

        links = QHBoxLayout()
        links.setContentsMargins(0, 0, 0, 0)
        links.setSpacing(6)
        links.addStretch(1)
        self.links = {}
        self._add_link(links, "github", "GitHub", GITHUB_URL)
        notes = release_notes_url(self.version)
        if notes is not None:
            self._add_link(links, "release_notes", "Release notes", notes)
        self.licenses_path = licenses_dir(frozen)
        if self.licenses_path is not None and self.licenses_path.is_dir():
            self._add_link(links, "licenses", "Licences", QUrl.fromLocalFile(str(self.licenses_path)).toString())
        links.addStretch(1)
        column.addLayout(links)
        column.addSpacing(16)

        self.copyright_label = self._wrapped(COPYRIGHT, DIM_COLOR, FONT_SMALL)
        column.addWidget(self.copyright_label)
        column.addSpacing(4)
        self.qt_label = self._wrapped(qt_notice(), DIM_COLOR, FONT_SMALL)
        column.addWidget(self.qt_label)
        column.addSpacing(14)

        self.close_button = self.kit.button("Close", "secondary", self.close, compact=True)
        column.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignRight)

    @staticmethod
    def _wrapped(value: str, color: str, font: tuple) -> QLabel:
        label = text(value, color=color, font=font)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        return label

    def _add_link(self, row: QHBoxLayout, key: str, title: str, url: str) -> None:
        self.links[key] = (title, url)
        button = self.kit.button(title, "quiet", lambda: _open_url(url), compact=True)
        setattr(self, f"{key}_button", button)
        row.addWidget(button)

    # -- lifecycle -----------------------------------------------------------

    def alive(self) -> bool:
        return not self._closed

    def raise_window(self) -> None:
        """Bring it up in front: show, lift and ask for focus. The stay-on-top
        hint (host.register_window) is what keeps it above a topmost widget."""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        macos.activate_app()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        global _instance
        if not self._closed:
            self._closed = True
            self.host.forget_window(self)
            if _instance is self:
                _instance = None
        super().closeEvent(event)
