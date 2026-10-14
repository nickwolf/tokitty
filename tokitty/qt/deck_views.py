"""The in-window permission view: the view factory InWindowViews in
streamdock/window.py is given.

InWindowViews does the bookkeeping (one view per nonce, closing a view whose
request left pending); qt_view_factory() supplies the windows. GUI thread
only.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from tokitty.qt.kit import Kit, hbox, qfont, text
from tokitty.settings_style import BORDER, FG_COLOR, FONT_BODY, FONT_MONO, SURFACE
from tokitty.streamdock.pending import PendingRequest
from tokitty.streamdock.window import always_label, format_tool_input

VIEW_WIDTH = 480


def keep_on_screen(x: int, y: int, width: int, height: int, area: QRect) -> tuple:
    """Where a frame at (x, y) has to move to lie inside `area`: pulled left
    and up past the right and bottom edges, then down and right past the
    left and top ones, so a frame larger than the area keeps its top-left
    corner (and the title bar) visible."""
    x = min(x, area.x() + area.width() - width)
    y = min(y, area.y() + area.height() - height)
    return max(x, area.x()), max(y, area.y())


class QtView(QWidget):
    """One permission request. Its buttons call on_answer; the title-bar close
    goes through closeEvent to on_close, so the bookkeeping drops the nonce and
    tells the runtime. close(), which the
    bookkeeping calls, sets a flag first so the closeEvent it triggers does not
    call on_close a second time."""

    def __init__(self, request: PendingRequest, title: str, explain: str,
                 anchor: Optional[tuple], on_answer: Callable[[str], None],
                 on_close: Callable[[], None]) -> None:
        super().__init__()
        self._on_close = on_close
        self._closing = False
        kit = Kit()
        kit.style_window(self)
        self.setWindowTitle(title)
        # Always in front: it is asking a question.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setFixedWidth(VIEW_WIDTH)

        column = QVBoxLayout(self)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(0)
        self.explanation = text(explain, font=FONT_BODY)
        self.explanation.setWordWrap(True)
        column.addWidget(self.explanation)
        column.addSpacing(8)
        self.tool_input = QPlainTextEdit()
        self.tool_input.setReadOnly(True)
        self.tool_input.setFont(qfont(FONT_MONO))
        self.tool_input.setStyleSheet(
            f"QPlainTextEdit {{ background: {SURFACE}; color: {FG_COLOR}; "
            f"border: 1px solid {BORDER}; }}")
        self.tool_input.setPlainText(format_tool_input(request.tool_input))
        self.tool_input.setMinimumHeight(220)
        column.addWidget(self.tool_input, 1)
        column.addSpacing(10)

        buttons = QWidget()
        row = hbox(buttons, spacing=6)
        self.allow_button = kit.button("Allow", "primary", lambda: on_answer("allow"))
        self.deny_button = kit.button("Deny", "secondary", lambda: on_answer("deny"))
        row.addWidget(self.allow_button)
        row.addWidget(self.deny_button)
        self.always_button = None
        label = always_label(request)
        if label:
            self.always_button = kit.button(label, "secondary", lambda: on_answer("always"))
            row.addWidget(self.always_button)
        row.addStretch(1)
        column.addWidget(buttons)

        if anchor:
            self.move(anchor[0], anchor[1])
        self.show()
        if anchor:
            # The frame size is only known once shown. A pane near the bottom
            # or right of the screen would otherwise push the buttons off it.
            screen = QGuiApplication.screenAt(QPoint(anchor[0], anchor[1])) or self.screen()
            if screen is not None:
                frame = self.frameGeometry()
                x, y = keep_on_screen(frame.x(), frame.y(), frame.width(), frame.height(),
                                      screen.availableGeometry())
                if (x, y) != (frame.x(), frame.y()):
                    self.move(x, y)
        # No macos.activate_app() here: the request arrives while the user is
        # typing elsewhere, and taking the app's focus could send a keystroke
        # to these buttons.
        self.raise_()
        self.activateWindow()

    def close(self) -> bool:
        self._closing = True
        closed = super().close()
        # Freed on the next turn of the loop: this can run inside one of the
        # view's own button slots, which must not outlive the window.
        QTimer.singleShot(0, self.deleteLater)
        return closed

    def closeEvent(self, event) -> None:
        if not self._closing:
            self._closing = True
            self._on_close()
        event.accept()


def qt_view_factory() -> Callable[..., QtView]:
    """A make_view for InWindowViews that builds an always-on-top window."""

    def make_view(request: PendingRequest, title: str, explain: str, anchor: Optional[tuple],
                  on_answer: Callable[[str], None], on_close: Callable[[], None]) -> QtView:
        return QtView(request, title, explain, anchor, on_answer, on_close)

    return make_view
