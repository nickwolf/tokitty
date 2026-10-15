"""The Qt "New-session presets" window: the counterpart of
streamdock/presets_ui. Thin Qt over tokitty.streamdock.presets, which owns
the validation and the saving.

One PresetsDialog per process: open() raises the existing one. It is a
parentless window like Settings, so a host, when there is one, keeps it above
the widget (host.register_window). The add/edit form is a child window of it,
owned and so always above it. GUI thread only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHeaderView, QTreeWidget, QTreeWidgetItem, QWidget,
)

from tokitty.qt import macos
from tokitty.accounts import Account
from tokitty.qt.kit import Kit, hbox, qfont, text, vbox
from tokitty.settings import load_settings
from tokitty.settings_style import (
    ACCENT_BG, ACCENT_FG, BAD, BORDER, DIM_COLOR, FG_COLOR, FONT_BODY, FONT_SMALL, MUTED, RAIL,
    SURFACE,
)
from tokitty.streamdock.presets import (
    account_label,
    check_preset,
    claude_accounts,
    is_wsl,
    preset_for,
    save_presets,
)

WSL_HINT = "A path inside WSL, such as /mnt/c/Tools or ~/repo"

_instance: Optional["PresetsDialog"] = None


class PresetsDialog(QWidget):
    """One window per process. Use PresetsDialog.open(), not the constructor."""

    def __init__(
        self,
        state_dir: Path,
        accounts_fn: Callable[[], List[Optional[Account]]],
        label_fn: Callable[[int], Optional[str]],
        on_saved: Callable[[List[dict]], None],
        host=None,
    ) -> None:
        super().__init__()
        self.state_dir = state_dir
        self._accounts_fn = accounts_fn
        self._label_fn = label_fn
        self._on_saved = on_saved
        self.host = host
        self._presets: List[dict] = load_settings(state_dir).streamdock_presets
        self._form: Optional[QWidget] = None
        self._closed = False
        self.kit = Kit()
        self.kit.style_window(self)
        self.setWindowTitle("New-session presets")
        if host is not None:
            host.register_window(self)

        column = vbox(self)
        column.setContentsMargins(8, 8, 8, 8)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Account", "Folder"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setMinimumSize(560, 150)
        self.tree.setFont(qfont(FONT_BODY))
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.verticalScrollBar().setProperty("kit", "thin")
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background: {SURFACE}; color: {FG_COLOR}; border: 1px solid {BORDER}; }}"
            f"QTreeWidget::item {{ padding: 3px 4px; }}"
            f"QTreeWidget::item:selected {{ background: {ACCENT_BG}; color: {ACCENT_FG}; }}"
            f"QHeaderView::section {{ background: {RAIL}; color: {MUTED}; border: none; "
            f"padding: 4px 6px; }}")
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for index, width in enumerate((120, 220, 220)):
            self.tree.setColumnWidth(index, width)
        column.addWidget(self.tree, 1)
        column.addSpacing(8)

        buttons = QWidget()
        row = hbox(buttons, spacing=6)
        self.add_button = self.kit.button("Add", "secondary", self._add)
        self.edit_button = self.kit.button("Edit", "secondary", self._edit)
        self.remove_button = self.kit.button("Remove", "secondary", self._remove)
        for button in (self.add_button, self.edit_button, self.remove_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.kit.button("Close", "secondary", self.close))
        column.addWidget(buttons)
        self._refresh()

    @classmethod
    def open(cls, state_dir, accounts_fn, label_fn, on_saved, *, host=None) -> "PresetsDialog":
        global _instance
        existing = _instance
        if existing is not None and existing.alive():
            existing.raise_window()
            return existing
        dialog = _instance = cls(state_dir, accounts_fn, label_fn, on_saved, host)
        dialog.raise_window()
        return dialog

    def alive(self) -> bool:
        return not self._closed

    def raise_window(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        macos.activate_app()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        global _instance
        if not self._closed:
            self._closed = True
            if self._form is not None:
                self._form.close()
            if self.host is not None:
                self.host.forget_window(self)
            if _instance is self:
                _instance = None
        super().closeEvent(event)

    def _account_for(self, preset: dict) -> Optional[Account]:
        accounts = self._accounts_fn()
        for account in accounts:
            if account is not None and account.name == preset.get("account"):
                return account
        index = preset.get("account_index")
        if "account" not in preset and isinstance(index, int) and 0 <= index < len(accounts):
            return accounts[index]
        return None

    def _refresh(self) -> None:
        self.tree.clear()
        accounts = self._accounts_fn()
        for preset in self._presets:
            account = self._account_for(preset)
            if account is None:
                shown = "(account missing)"
            else:
                shown = account_label(accounts.index(account), account, self._label_fn(accounts.index(account)))
            self.tree.addTopLevelItem(QTreeWidgetItem([preset["name"], shown, preset["cwd"]]))

    def _selected(self) -> Optional[int]:
        picked = self.tree.selectedItems()
        return self.tree.indexOfTopLevelItem(picked[0]) if picked else None

    def _add(self) -> None:
        self._open_form(None)

    def _edit(self) -> None:
        position = self._selected()
        if position is not None:
            self._open_form(position)

    def _remove(self) -> None:
        position = self._selected()
        if position is None:
            return
        self._save(self._presets[:position] + self._presets[position + 1:])

    def _save(self, presets: List[dict]) -> None:
        self._presets = save_presets(self.state_dir, presets)
        self._on_saved(list(self._presets))
        self._refresh()

    def _open_form(self, position: Optional[int]) -> None:
        if self._form is not None:
            self._form.close()
        accounts = self._accounts_fn()
        offered = claude_accounts(accounts)
        existing = self._presets[position] if position is not None else None
        labels = [account_label(i, a, self._label_fn(i)) for i, a in offered]
        kit = self.kit
        form = self._form = QWidget(self, Qt.WindowType.Dialog)
        kit.style_window(form)
        form.setWindowTitle("Edit preset" if existing else "Add preset")
        column = vbox(form)
        column.setContentsMargins(12, 10, 12, 10)

        def field(caption: str, widget) -> None:
            line = QWidget()
            row = hbox(line, spacing=8)
            caption_label = text(caption)
            caption_label.setFixedWidth(52)
            row.addWidget(caption_label)
            row.addWidget(widget, 1)
            column.addWidget(line)
            column.addSpacing(6)

        name_holder, name_entry = kit.entry(width=40)
        name_entry.setText(existing["name"] if existing else "")
        field("Name", name_holder)
        combo = kit.dropdown(labels, width=38, on_change=lambda _label: sync_account())
        combo.set(labels[0] if labels else "")
        if existing:
            current = self._account_for(existing)
            for label, (_, account) in zip(labels, offered):
                if account is current:
                    combo.set(label)
        field("Account", combo)
        cwd_holder, cwd_entry = kit.entry(width=32)
        cwd_entry.setText(existing["cwd"] if existing else "")
        browse = kit.button("Browse…", "secondary", lambda: self._browse(form, cwd_entry), compact=True)
        folder = QWidget()
        folder_row = hbox(folder, spacing=6)
        folder_row.addWidget(cwd_holder, 1)
        folder_row.addWidget(browse)
        field("Folder", folder)
        hint = text(WSL_HINT, color=DIM_COLOR, font=FONT_SMALL)
        hint.setWordWrap(True)
        hint.setContentsMargins(60, 0, 0, 0)
        column.addWidget(hint)
        error = text("", color=BAD, font=FONT_SMALL)
        error.setWordWrap(True)
        error.setContentsMargins(0, 4, 0, 4)
        column.addWidget(error)

        def chosen() -> Optional[Account]:
            if combo.get() in labels:
                return offered[labels.index(combo.get())][1]
            return None

        def sync_account() -> None:
            account = chosen()
            wsl = account is not None and is_wsl(account)
            browse.setEnabled(not wsl)
            hint.setVisible(wsl)

        sync_account()

        def save() -> None:
            account = chosen()
            if account is None:
                error.setText("No Claude account to launch on")
                return
            preset = preset_for(name_entry.text(), account, cwd_entry.text(), accounts)
            others = [p for i, p in enumerate(self._presets) if i != position]
            problem = check_preset(preset, account, others)
            if problem:
                error.setText(problem)
                return
            updated = list(self._presets)
            if position is None:
                updated.append(preset)
            else:
                updated[position] = preset
            self._save(updated)
            form.close()

        buttons = QWidget()
        row = hbox(buttons, spacing=6)
        row.addStretch(1)
        row.addWidget(kit.button("Save", "primary", save))
        row.addWidget(kit.button("Cancel", "secondary", form.close))
        column.addWidget(buttons)
        form.save, form.name_entry, form.cwd_entry = save, name_entry, cwd_entry
        form.combo, form.error, form.browse, form.hint = combo, error, browse, hint
        form.show()
        macos.activate_app()
        form.raise_()
        form.activateWindow()

    @staticmethod
    def _browse(parent: QWidget, entry) -> None:
        picked = QFileDialog.getExistingDirectory(parent, "Choose folder", entry.text() or "")
        if picked:
            entry.setText(picked.replace("/", "\\"))
