"""The update confirm dialog (#113).

`QtUpdateDialog` only draws what `UpdateFlow` decides (which choices a release
gets, what each outcome says, when the window may close), so the rules live
once in update_flow. One window per process: open() raises the existing one.
"""
from __future__ import annotations

import webbrowser
from typing import List, Optional

from PySide6.QtWidgets import QLayout, QPushButton, QWidget

from tokitty.qt import macos
from tokitty.qt.kit import Kit, clear_layout, hbox, text, vbox
from tokitty.settings_style import FONT_BODY
from tokitty.update_flow import WRAP_PX, Buttons, UpdateFlow
from tokitty.updater import Release

_instance: Optional["QtUpdateDialog"] = None


class _Renderer:
    """What the flow draws on. A separate object because the flow's show(note,
    buttons) would shadow QWidget.show()."""

    def __init__(self, dialog: "QtUpdateDialog"):
        self._dialog = dialog

    def show(self, note: str, buttons: Optional[Buttons]) -> None:
        self._dialog._show_note(note, buttons)

    def close(self) -> None:
        self._dialog.close()


class QtUpdateDialog(QWidget):
    @classmethod
    def open(cls, host, release: Release, running: str, controller,
             open_url=webbrowser.open) -> "QtUpdateDialog":
        """Show the dialog, or raise the one already open: there is only ever one."""
        existing = _instance
        if existing is not None and existing.alive:
            existing.show()
            macos.activate_app()
            existing.raise_()
            existing.activateWindow()
            return existing
        dialog = cls(host, release, running, controller, open_url)
        dialog.show()
        macos.activate_app()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def __init__(self, host, release: Release, running: str, controller, open_url=webbrowser.open):
        global _instance
        super().__init__()
        self._host = host
        self._finished = False
        self.kit = Kit()
        self.kit.style_window(self)
        self.setWindowTitle("Update Tokitty")
        column = vbox(self)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(8)
        # The window follows its content, so a longer note grows it.
        column.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self._info = text("", font=FONT_BODY)
        column.addWidget(self._info)
        self._note = text("", font=FONT_BODY)
        self._note.setWordWrap(True)
        self._note.setFixedWidth(WRAP_PX)
        column.addWidget(self._note)
        row = QWidget()
        self._row = hbox(row, spacing=8)
        column.addWidget(row)
        _instance = self
        # Parentless, so it follows the widget's Always in front itself.
        host.register_window(self)
        # The flow shows its first choices as it is built, so the widgets come first.
        self._flow = UpdateFlow(release, running, controller, _Renderer(self), open_url)
        self._info.setText(self._flow.info_text)

    @property
    def alive(self) -> bool:
        return self._flow.alive

    # What the dialog shows, for tests.
    @property
    def note(self) -> str:
        return self._note.text()

    def _buttons(self) -> List[QPushButton]:
        return [self._row.itemAt(i).widget() for i in range(self._row.count())]

    def button_labels(self) -> List[str]:
        return [button.text() for button in self._buttons()]

    def invoke(self, label: str) -> None:
        for button in self._buttons():
            if button.text() == label:
                button.click()
                return
        raise LookupError(label)

    def _show_note(self, note: str, buttons: Optional[Buttons]) -> None:
        self._note.setText(note)
        if buttons is not None:
            clear_layout(self._row)
            for index, (label, command) in enumerate(buttons):
                style = "primary" if index == 0 and label == "Install" else "secondary"
                self._row.addWidget(self.kit.button(label, style, command))

    def closeEvent(self, event) -> None:
        if self._flow.alive:
            # The title-bar close: the flow refuses it while an install runs.
            # When it agrees it calls close() back, which re-enters here with
            # alive False.
            self._flow.close_requested()
            if self._flow.alive:
                event.ignore()
                return
        self._finish()
        event.accept()

    def _finish(self) -> None:
        global _instance
        if self._finished:
            return
        self._finished = True
        self._host.forget_window(self)
        if _instance is self:
            _instance = None
