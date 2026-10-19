"""The Qt Settings window's Updates tab: the daily-check controls, the recent
releases' notes (#171) and the install progress (#155). The counterpart of
settings_accounts.

What to say and which buttons to offer is update_flow's (UpdateStatus and
UpdateFlow); this draws it. The flow lives with the tab, so a closed window
takes it along and a reopened one starts from the controller's state.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPushButton, QTextBrowser, QWidget

from tokitty.app_version import version_text
from tokitty.qt.kit import clear_layout, hbox, text, vbox
from tokitty.qt.settings import _Tab, _hrow
from tokitty.settings_style import (
    _PILL_COLORS, BLUE, FG_COLOR, FONT_BODY, FONT_SMALL, LOG_BG, MUTED,
)
from tokitty.update_flow import (
    INSTALLED, MORE_ON_GITHUB, NEW, RELEASES_URL, Buttons, HistoryEntry, UpdateFlow, notes_markdown,
)
from tokitty.updater import Release

NOTES_MIN_HEIGHT = 160
HEADING_GAP = 18
# Above the Markdown import's h2, the largest heading GitHub's generated
# release notes use, so those sit under the release's own heading.
TAG_POINT_SIZE = 16
BADGE_TONES = {NEW: "warn", INSTALLED: "good"}


class _Renderer:
    """What the flow draws on. A separate object so the flow's show(note,
    buttons) doesn't have to be a method of the tab."""

    def __init__(self, tab: "UpdatesTab"):
        self._tab = tab

    def show(self, note: str, buttons: Optional[Buttons]) -> None:
        self._tab._show_note(note, buttons)

    def close(self) -> None:
        self._tab._flow_closed()


def _recolor_links(document) -> None:
    """The Markdown import paints links in the application palette's blue,
    which is unreadable on LOG_BG and ignores the widget's own palette."""
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.charFormat().isAnchor():
                cursor = QTextCursor(document)
                cursor.setPosition(fragment.position())
                cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                link = fragment.charFormat()
                link.setForeground(QColor(BLUE))
                cursor.setCharFormat(link)
            it += 1
        block = block.next()


def _write_history(browser: QTextBrowser, entries: List[HistoryEntry]) -> None:
    """Each release under a heading of its tag, badge and date, then its
    notes, then the link to the rest."""
    browser.clear()
    document = browser.document()
    cursor = QTextCursor(document)
    for index, entry in enumerate(entries):
        if index:
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        heading = cursor.block().blockNumber()
        # Inserted Markdown merges its first block into the cursor's, losing
        # that block's format. So the first block is the tag line, a plain
        # paragraph (a tag is only digits and dots), styled afterwards.
        cursor.insertMarkdown(f"{entry.release.tag}\n\n{notes_markdown(entry.release)}")
        _style_heading(document.findBlockByNumber(heading), entry, top=HEADING_GAP if index else 0)
    if entries:
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        cursor.insertMarkdown(f"[{MORE_ON_GITHUB}]({RELEASES_URL})")
    _recolor_links(document)
    browser.moveCursor(QTextCursor.MoveOperation.Start)


def _style_heading(block, entry: HistoryEntry, *, top: int) -> None:
    cursor = QTextCursor(block)
    spacing = block.blockFormat()
    spacing.setTopMargin(top)
    cursor.setBlockFormat(spacing)
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    tag = QTextCharFormat()
    tag.setFontWeight(QFont.Weight.Bold)
    tag.setFontPointSize(TAG_POINT_SIZE)
    cursor.mergeCharFormat(tag)
    cursor.clearSelection()
    if entry.badge:
        fg, bg = _PILL_COLORS[BADGE_TONES[entry.badge]]
        badge = QTextCharFormat()
        badge.setFontWeight(QFont.Weight.Bold)
        badge.setFontPointSize(FONT_SMALL[1])
        badge.setForeground(QColor(fg))
        badge.setBackground(QColor(bg))
        cursor.insertText("  ", QTextCharFormat())
        cursor.insertText(f" {entry.badge} ", badge)
    if entry.release.published:
        date = QTextCharFormat()
        date.setForeground(QColor(MUTED))
        date.setFontPointSize(FONT_SMALL[1])
        cursor.insertText(f"  {entry.release.published}", date)


class UpdatesTab(_Tab):
    # The notes take whatever height the window has.
    fills = True

    def build(self) -> None:
        kit, host = self.kit, self.host
        self._flow: Optional[UpdateFlow] = None
        self._release: Optional[Release] = None
        self._entries: List[HistoryEntry] = []
        kit.page_header(self.frame, "Updates", "Stay on the newest tokitty.")
        card = self.card()
        self.update_toggle = None
        if host.update_check_enabled is not None and host.on_toggle_update_check is not None:
            self.update_toggle = kit.toggle(
                "Check for updates automatically",
                on_change=lambda _v: self.write(host.on_toggle_update_check))
            self.update_toggle.setContentsMargins(12, 9, 12, 6)
            kit.add(card, self.update_toggle)
        lower, row = _hrow()
        row.setContentsMargins(12, 3, 12, 9)
        self.update_pill = kit.pill("", "good")
        row.addWidget(self.update_pill)
        row.addStretch(1)
        if host.on_check_updates is not None:
            row.addWidget(kit.button("Check now", "secondary", compact=True,
                                     command=lambda: self.write(host.on_check_updates)))
        kit.add(card, lower)

        self.detail = QWidget()
        column = vbox(self.detail)
        kit.section_header(self.frame, "Release notes", top=10)
        kit.add(self.frame, self.detail, 1)
        self.headline = text("", font=FONT_BODY)
        self.headline.setWordWrap(True)
        column.addWidget(self.headline)
        self.notes = QTextBrowser()
        self.notes.setOpenLinks(False)
        self.notes.setOpenExternalLinks(False)
        self.notes.setReadOnly(True)
        self.notes.setMinimumHeight(NOTES_MIN_HEIGHT)
        self.notes.setStyleSheet(
            f"QTextBrowser {{ background: {LOG_BG}; color: {FG_COLOR}; border: none; padding: 6px; }}")
        self.notes.verticalScrollBar().setProperty("kit", "thin")
        self.notes.anchorClicked.connect(self._open_link)
        column.addSpacing(8)
        column.addWidget(self.notes, 1)
        self.note = text("", color=MUTED, font=FONT_SMALL)
        self.note.setWordWrap(True)
        self.note.setContentsMargins(0, 8, 0, 0)
        column.addWidget(self.note)
        self.row_holder = QWidget()
        self._row = hbox(self.row_holder, spacing=8)
        self._row.setContentsMargins(0, 8, 0, 0)
        column.addWidget(self.row_holder)
        # Keeps the headline at the top while the notes are hidden.
        column.addStretch(0)

    # What the tab shows, for tests.
    def button_labels(self) -> List[str]:
        return [button.text() for button in self._buttons()]

    def invoke(self, label: str) -> None:
        for button in self._buttons():
            if button.text() == label:
                button.click()
                return
        raise LookupError(label)

    def _buttons(self) -> List[QPushButton]:
        return [self._row.itemAt(i).widget() for i in range(self._row.count())]

    def refresh(self) -> None:
        host = self.host
        if self.update_toggle is not None:
            self.update_toggle.set(host.update_check_enabled())
        label = host.update_available_label() if host.update_available_label else None
        if label:
            self.kit.set_pill(self.update_pill, label, "warn")
        else:
            self.kit.set_pill(self.update_pill, f"Up to date · {version_text()}", "good")
        actions = host.update_actions
        self.detail.setVisible(actions is not None)
        if actions is None:
            return
        if self.owner.current == "updates":
            actions.ensure_release()
        status = actions.status()
        self.headline.setText(status.headline)
        self._sync_notes(status.entries)
        self._sync_flow(status.release)

    def _sync_notes(self, entries: List[HistoryEntry]) -> None:
        self.notes.setVisible(bool(entries))
        # Rewriting the same list would throw away the scroll position.
        if entries != self._entries:
            self._entries = entries
            _write_history(self.notes, entries)

    def _sync_flow(self, release: Optional[Release]) -> None:
        flow = self._flow
        if flow is not None and (flow.busy or (flow.alive and release == self._release)):
            return
        self._drop_flow()
        visible = release is not None
        for widget in (self.note, self.row_holder):
            widget.setVisible(visible)
        if release is None:
            return
        self._release = release
        self._flow = self.host.update_actions.new_flow(release, _Renderer(self))

    def _drop_flow(self) -> None:
        if self._flow is not None:
            self._flow.detach()
        self._flow = None
        self._release = None
        self._show_note("", [])

    def _show_note(self, note: str, buttons: Optional[Buttons]) -> None:
        self.note.setText(note)
        self.note.setVisible(bool(note))
        if buttons is not None:
            clear_layout(self._row)
            for index, (label, command) in enumerate(buttons):
                style = "primary" if index == 0 and label == "Install" else "secondary"
                self._row.addWidget(self.kit.button(label, style, command))

    def _flow_closed(self) -> None:
        """The flow ended itself (a rolled-back install): start again from the
        current state."""
        self._flow = None
        self._release = None
        self.refresh()

    def _open_link(self, url: QUrl) -> None:
        # Only web links: a release body is somebody else's text.
        if url.scheme() in ("http", "https"):
            self.host.update_actions.open_url(url.toString())

    def close_blocked(self) -> bool:
        return self._flow is not None and self._flow.busy

    def close(self) -> None:
        if self._flow is not None:
            self._flow.detach()
