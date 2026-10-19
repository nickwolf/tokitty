"""The custom-colorway editor (#156): a modal dialog over Settings.

New seeds from the pane's current colorway, Edit from the stored entry. The
draft lives only in this dialog; nothing is written until Save, which goes
through tokitty.colorways and then the host's on_colorways_changed so every
pane and the tray restyle. GUI thread only.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QDialog, QGridLayout, QMessageBox, QWidget

from tokitty import colorways, sprites
from tokitty.customize import load_customization
from tokitty.qt.kit import Kit, SpritePreview, hbox, text, vbox
from tokitty.settings_style import BAD, DIM_COLOR, FONT_SMALL, FONT_TITLE, MUTED

# (caption, tone key)
TONE_ROWS = (
    ("Coat", "coat"),
    ("Shade", "shade"),
    ("Markings", "mark"),
    ("Light", "light"),
    ("Ears", "ear"),
)
PREVIEW_CELL = 6   # the big cat: twice the Look tab's preview
STRIP_CELL = 2     # the eleven patterns
STRIP_COLUMNS = 6  # per row: eleven columns don't fit the dialog's 600 px
DEFAULT_NAME = "My colorway"


def free_name(store: dict) -> str:
    """"My colorway", else "My colorway 2", 3, ... the first the store accepts."""
    name, n = DEFAULT_NAME, 1
    while colorways.validate_name(name, store) is not None:
        n += 1
        name = f"{DEFAULT_NAME} {n}"
    return name


class ColorwayEditor(QDialog):
    """cid None is New (and Save also selects the new colorway on the pane)."""

    def __init__(self, parent: QWidget, host, pane_index: int, cid: Optional[str] = None):
        super().__init__(parent)
        from tokitty.qt.settings import ColorRow  # settings imports this module lazily

        self.host = host
        self.kit = kit = Kit()
        self.pane_index = pane_index
        self.cid = cid
        pane = host.panes[pane_index]
        self.pattern, self.tail = pane._pattern, pane._tail
        store = host.colorway_store()
        if cid is not None and cid in store:
            self.draft: Dict[str, str] = colorways.tones_of(store[cid])
            start_name = store[cid]["name"]
        else:
            self.cid = None
            self.draft = dict(sprites.colorway_tones(pane._colorway))
            start_name = free_name(store)
        kit.style_window(self)
        self.setWindowTitle("Edit colorway" if self.cid else "New colorway")
        self.setModal(True)
        self.setFixedWidth(600)

        root = vbox(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.addWidget(text(self.windowTitle(), font=FONT_TITLE))
        root.addSpacing(12)

        body = QWidget()
        cols = hbox(body)
        root.addWidget(body)
        left = QWidget()
        left_col = vbox(left)
        cols.addWidget(left, 1)
        cols.addSpacing(16)

        caption = text("Name", color=MUTED, font=FONT_SMALL)
        caption.setContentsMargins(0, 0, 0, 4)
        left_col.addWidget(caption)
        self.name_holder, self.name_entry = kit.entry()
        self.name_entry.setMaxLength(colorways.MAX_NAME_LEN)
        self.name_entry.setText(start_name)
        self.name_entry.textEdited.connect(lambda _t: self._show_error(""))
        left_col.addWidget(self.name_holder)
        self.error = text("", color=BAD, font=FONT_SMALL)
        self.error.setContentsMargins(0, 3, 0, 0)
        left_col.addWidget(self.error)
        left_col.addSpacing(8)

        self.rows: Dict[str, ColorRow] = {}
        for label, key in TONE_ROWS:
            row = ColorRow(kit, label, lambda k=key, c=label: self._choose(k, c))
            self.rows[key] = row
            left_col.addWidget(row)
            left_col.addSpacing(3)
        derive_row = QWidget()
        derive_layout = hbox(derive_row)
        derive_layout.setContentsMargins(0, 5, 0, 0)
        self.derive_button = kit.button("Derive from coat", "secondary", self._derive)
        derive_layout.addWidget(self.derive_button)
        derive_layout.addSpacing(8)
        derive_layout.addWidget(text("Sets Shade, Markings and Light", color=MUTED, font=FONT_SMALL))
        derive_layout.addStretch(1)
        left_col.addWidget(derive_row)
        left_col.addStretch(1)

        card = kit.surface()
        card.setFixedSize(PREVIEW_CELL * 28 + 24, PREVIEW_CELL * 26 + 80)
        card_col = card.layout()
        title = text("PREVIEW", color=DIM_COLOR, font=FONT_SMALL)
        title.setContentsMargins(10, 8, 0, 0)
        card_col.addWidget(title)
        self.preview: SpritePreview = kit.sprite_preview(
            self._palette(self.pattern), cell=PREVIEW_CELL)
        card_col.addWidget(self.preview, 1, Qt.AlignmentFlag.AlignCenter)
        # Preview only: which pattern the big cat wears here. The pane's own
        # pattern is chosen on the Look tab.
        self.pattern_pick = kit.dropdown(list(sprites.PATTERNS), on_change=self._pick_pattern)
        self.pattern_pick.set(self.pattern)
        pick_row = QWidget()
        pick_layout = hbox(pick_row)
        pick_layout.setContentsMargins(10, 0, 10, 10)
        pick_layout.addWidget(self.pattern_pick, 1)
        card_col.addWidget(pick_row)
        cols.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)

        root.addSpacing(12)
        root.addWidget(text("All patterns", color=MUTED, font=FONT_SMALL))
        root.addSpacing(4)
        strip = QWidget()
        strip_grid = QGridLayout(strip)
        strip_grid.setContentsMargins(0, 0, 0, 0)
        strip_grid.setHorizontalSpacing(4)
        strip_grid.setVerticalSpacing(8)
        self.strip: Dict[str, SpritePreview] = {}
        self.strip_labels: Dict[str, QWidget] = {}
        for i, pattern in enumerate(sprites.PATTERNS):
            cell = kit.sprite_preview(self._palette(pattern), cell=STRIP_CELL)
            cell.setToolTip(pattern.replace("_", " "))
            self.strip[pattern] = cell
            column = QWidget()
            column_layout = vbox(column)
            column_layout.addWidget(cell, 0, Qt.AlignmentFlag.AlignHCenter)
            # Spaces, not underscores, so "tabby white" can wrap under its cat.
            label = text(pattern.replace("_", " "), color=MUTED, font=FONT_SMALL)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            label.setFixedWidth(STRIP_CELL * 28 + 4)
            column_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            self.strip_labels[pattern] = label
            strip_grid.addWidget(column, i // STRIP_COLUMNS, i % STRIP_COLUMNS, Qt.AlignmentFlag.AlignTop)
        strip_grid.setColumnStretch(STRIP_COLUMNS, 1)
        root.addWidget(strip)
        root.addSpacing(14)

        buttons = QWidget()
        buttons_row = hbox(buttons, spacing=8)
        self.delete_button = None
        if self.cid:
            self.delete_button = kit.button("Delete", "danger", self._delete)
            buttons_row.addWidget(self.delete_button)
        buttons_row.addStretch(1)
        self.cancel_button = kit.button("Cancel", "secondary", self.reject)
        buttons_row.addWidget(self.cancel_button)
        self.save_button = kit.button("Save", "primary", self._save)
        buttons_row.addWidget(self.save_button)
        root.addWidget(buttons)
        self._sync()

    # -- draft -----------------------------------------------------------------

    def _palette(self, pattern: str) -> dict:
        return colorways.palette_from_tones(self.draft, pattern, self.tail)

    def _sync(self) -> None:
        """Repaint every swatch and sprite from the draft."""
        for key, row in self.rows.items():
            row.set(self.draft[key])
        self.preview.set_sprite(self._palette(self.pattern))
        for pattern, cell in self.strip.items():
            cell.set_sprite(self._palette(pattern))

    def _pick_pattern(self, pattern: str) -> None:
        if pattern in sprites.PATTERNS:
            self.pattern = pattern
            self._sync()

    def _choose(self, key: str, caption: str) -> None:
        chosen = QColorDialog.getColor(QColor(self.draft[key]), self, caption)
        if chosen.isValid():
            self.draft[key] = chosen.name().lower()
            self._sync()

    def _derive(self) -> None:
        self.draft.update(colorways.derive_from_coat(self.draft["coat"]))
        self._sync()

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.kit.set_entry_error(self.name_holder, bool(message))

    # -- actions ---------------------------------------------------------------

    def _save(self) -> None:
        host, name = self.host, self.name_entry.text()
        problem = colorways.validate_name(name, host.colorway_store(), exclude_id=self.cid)
        if problem:
            self._show_error(problem)
            return
        try:
            if self.cid is None:
                new_id = colorways.add_colorway(host.state_dir, name, self.draft)
            else:
                new_id = None
                colorways.update_colorway(host.state_dir, self.cid, name=name, tones=self.draft)
        except (ValueError, KeyError) as exc:
            self._show_error(str(exc.args[0]) if exc.args else "Could not save.")
            return
        host.on_colorways_changed()  # the new id must be registered before it is chosen
        if new_id is not None:
            host.on_customization_changed(self.pane_index, "colorway", new_id)
        host.notify_state_changed()
        self.accept()

    def users(self) -> List[str]:
        """Names of the panes wearing this colorway, plus a count of other
        customization entries (cats not shown) that do."""
        host = self.host
        named = [host.pane_name(i)[0] or f"Cat {i + 1}"
                 for i, pane in enumerate(host.panes) if pane._colorway == self.cid]
        total = sum(1 for c in load_customization(host.state_dir).values() if c.colorway == self.cid)
        extra = total - len(named)
        if extra > 0:
            named.append(f"{extra} other cat" + ("s" if extra > 1 else ""))
        return named

    def _delete(self) -> None:
        host = self.host
        name = self.name_entry.text().strip() or self.cid
        users = self.users()
        message = f"Delete the colorway “{name}”?"
        if users:
            message += (f"\n\nUsed by {', '.join(users)}. "
                        "They will go back to orange.")
        answer = QMessageBox.question(
            self, "Delete colorway", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        colorways.delete_colorway(host.state_dir, self.cid)
        host.on_colorways_changed()
        host.notify_state_changed()
        self.accept()
