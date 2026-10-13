"""The Qt Settings window: a dark left rail over a stack of tabs.

The counterpart of settings_ui. One SettingsWindow per QApplication, opened
with SettingsWindow.open(host, pane_index). It owns no state: every control is
read from the getters the QtHost it is given carries, and every write goes
through the same seam the right-click menu uses, followed by
host.notify_state_changed() so every other view re-syncs.

GUI thread only: the one worker (the ntfy test) publishes into a Mailbox that
a QtPoller drains, and never touches a QObject. Nothing here imports tkinter.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog, QFrame, QHBoxLayout, QLineEdit, QScrollArea, QWidget,
)

from tokitty import sprites, theme as T
from tokitty.app_version import version_text
from tokitty.display import bar_color
from tokitty.qt.kit import Kit, SpritePreview, hbox, set_text_color, text, vbox
from tokitty.qt.poller import QtPoller
from tokitty.settings_style import (
    BAD, BASE_HEIGHT, BASE_WIDTH, DIM_COLOR, FONT_BODY, FONT_MEDIUM, FONT_MONO,
    FONT_SMALL, GOOD, MUTED, WARN,
)
from tokitty.usage_items import USAGE_READOUT_ITEMS, USAGE_WINDOW_ITEMS, VIEW_MODE_ITEMS
from tokitty.worker_mailbox import Mailbox, run_worker

_instance: Optional["SettingsWindow"] = None

TABS = (
    ("general", "General"),
    ("look", "Look"),
    ("theme", "Theme"),
    ("usage", "Usage"),
)

_BUDGET_LABELS = {
    "24h": "Budget for the last 24 hours",
    "7d": "Budget for the last 7 days",
    "month": "Budget for this month",
}
_BUDGET_HINTS = {"24h": "last 24 hours", "7d": "last 7 days", "month": "this month"}

# (caption, customization field)
_COLOR_ROWS = (
    ("Coat", "coat_base"),
    ("Shade", "coat_shade"),
    ("Card background", "card_bg"),
    ("Bar fill", "bar_fill"),
)

CAT_SPOT_LABELS = (("inside", "Left"), ("inside_right", "Right"), ("on_top", "On top"))

_TEXT_NAMES = (
    ("section_labels", "SESSION and WEEK labels"),
    ("percent", "Percentages"),
    ("reset", "Reset times"),
    ("status", "Status line"),
    ("account_label", "Account name"),
    ("model_rows", "Per-model rows"),
    ("cat_tags", "Cat tags"),
)

OPACITY_HINT = "You'll need to right-click where the widget is to get back here"


def _hrow() -> Tuple[QWidget, QHBoxLayout]:
    row = QWidget()
    return row, hbox(row)


class ColorRow(QFrame):
    """A swatch, the hex value and a Change... button on a raised strip."""

    def __init__(self, kit: Kit, caption: str, command: Callable[[], None]):
        super().__init__()
        self.setProperty("kit", "surface")
        self.setFixedHeight(32)
        row = hbox(self)
        row.setContentsMargins(11, 0, 6, 0)
        row.addWidget(text(caption, font=FONT_BODY))
        row.addStretch(1)
        self.swatch = QFrame()
        self.swatch.setFixedSize(14, 14)
        self.swatch.setAutoFillBackground(True)
        row.addWidget(self.swatch)
        row.addSpacing(8)
        self.value = text("", color=MUTED, font=FONT_MONO)
        row.addWidget(self.value)
        row.addSpacing(6)
        self.button = kit.button("Change…", "quiet", command, compact=True)
        row.addWidget(self.button)

    def set(self, color: Optional[str]) -> None:
        """Show a colour; None is the automatic one (the bar ramp's low end)."""
        shown = color or bar_color(0.0)
        palette = self.swatch.palette()
        palette.setColor(self.swatch.backgroundRole(), QColor(shown))
        self.swatch.setPalette(palette)
        self.value.setText(color.upper() if color else "AUTOMATIC")

    def shown(self) -> str:
        return self.swatch.palette().color(self.swatch.backgroundRole()).name()


class _Tab:
    """One page of the stack. build() once, refresh() whenever shown or
    state changes; refresh() only ever calls control.set()."""

    def __init__(self, owner: "SettingsWindow"):
        self.owner = owner
        self.host = owner.host
        self.kit = owner.kit
        self.frame = QWidget()
        vbox(self.frame)
        self.build()
        self.frame.layout().addStretch(1)

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        pass

    def close(self) -> None:
        """The window is going away: cancel any poll this tab scheduled."""

    def write(self, action: Callable[[], None]) -> None:
        """Run a seam, then re-sync every view (this one included)."""
        action()
        self.host.notify_state_changed()

    def add(self, widget) -> None:
        self.kit.add(self.frame, widget)

    def card(self):
        card = self.kit.surface()
        self.add(card)
        return card

    def color_row(self, caption: str, command: Callable[[], None]) -> ColorRow:
        row = ColorRow(self.kit, caption, command)
        row.setContentsMargins(0, 0, 0, 0)
        self.add(row)
        self.frame.layout().addSpacing(3)
        return row


class GeneralTab(_Tab):
    def build(self) -> None:
        kit, host = self.kit, self.host
        kit.page_header(self.frame, "General", "The little things that make tokitty yours.")
        card = self.card()
        self.toggles: Dict[str, object] = {}

        def add(key, title, detail, on_change):
            toggle = kit.toggle(title, detail=detail, on_change=on_change)
            toggle.setContentsMargins(12, 2, 12, 2)
            kit.add(card, toggle)
            self.toggles[key] = toggle

        add("aot", "Always in front", "Keep panes above other windows",
            lambda _v: self.write(host.toggle_always_on_top))
        if host.on_toggle_tray is not None and host.tray_enabled is not None:
            add("tray", "Show tray icon", "Quick access when panes are hidden",
                lambda _v: self.write(host.on_toggle_tray))
        if host.on_toggle_autostart is not None and host.autostart_enabled is not None:
            add("autostart", "Start at login", "Launch tokitty when you sign in",
                lambda _v: self.write(host.on_toggle_autostart))
        if host.on_toggle_surprise is not None and host.surprise_me is not None:
            add("surprise", "Surprise me", "New random cat look at every launch",
                lambda _v: self.write(host.on_toggle_surprise))

        self.update_toggle = None
        self.update_pill = None
        if host.update_check_enabled is not None or host.on_check_updates is not None:
            kit.section_header(self.frame, "Updates", top=10)
            card = self.card()
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

    def refresh(self) -> None:
        host = self.host
        self.toggles["aot"].set(host.always_on_top())
        for key, getter in (("tray", host.tray_enabled), ("autostart", host.autostart_enabled),
                            ("surprise", host.surprise_me)):
            if key in self.toggles and getter is not None:
                self.toggles[key].set(getter())
        if self.update_toggle is not None:
            self.update_toggle.set(host.update_check_enabled())
        if self.update_pill is not None:
            label = host.update_available_label() if host.update_available_label else None
            if label:
                self.kit.set_pill(self.update_pill, label, "warn")
            else:
                self.kit.set_pill(self.update_pill, f"Up to date · {version_text()}", "good")


class LookTab(_Tab):
    def build(self) -> None:
        kit, host = self.kit, self.host
        kit.page_header(self.frame, "Look", "Give each pane its own personality.")
        picker, row = _hrow()
        picker.setContentsMargins(0, 0, 0, 11)
        row.addWidget(text("Editing", font=FONT_MEDIUM))
        row.addSpacing(10)
        self.pane_picker = kit.dropdown([], width=18, on_change=self.owner.pick_pane_by_name)
        row.addWidget(self.pane_picker)
        row.addStretch(1)
        if host.on_randomize is not None:
            row.addWidget(kit.button(
                "Randomize", "secondary", compact=True,
                command=lambda: self.write(lambda: host.on_randomize(self.owner.pane_index))))
        self.add(picker)

        upper, upper_row = _hrow()
        left = QWidget()
        left_col = vbox(left)
        upper_row.addWidget(left, 1)
        upper_row.addSpacing(10)
        upper_row.addWidget(self._build_preview(), 0, Qt.AlignmentFlag.AlignBottom)
        self.add(upper)

        caption = text("Name", color=MUTED, font=FONT_SMALL)
        caption.setContentsMargins(0, 0, 0, 4)
        left_col.addWidget(caption)
        holder, self.name_entry = kit.entry()
        holder.setContentsMargins(0, 0, 0, 0)
        left_col.addWidget(holder)
        left_col.addSpacing(8)
        self.name_entry.editingFinished.connect(self._save_name)

        choices, choice_row = _hrow()
        self.colorway = self._choice(choice_row, "Colorway", list(sprites.COLORWAYS),
                                     lambda i, name: host.on_customization_changed(i, "colorway", name))
        self.pattern = self._choice(choice_row, "Pattern", list(sprites.PATTERNS),
                                    lambda i, name: host.on_customization_changed(i, "pattern", name))
        left_col.addWidget(choices)
        left_col.addSpacing(8)

        sits = text("Cat sits", color=MUTED, font=FONT_SMALL)
        sits.setContentsMargins(0, 0, 0, 4)
        left_col.addWidget(sits)
        self.cat_sits = kit.segmented(
            CAT_SPOT_LABELS, on_change=lambda spot: self.write(
                lambda: host.on_customization_changed(self.owner.pane_index, "cat_home", spot)))
        left_col.addWidget(self.cat_sits)

        kit.section_header(self.frame, "Custom colors", "Overrides the preset", top=14)
        self.color_rows: Dict[str, ColorRow] = {}
        for label, field in _COLOR_ROWS:
            self.color_rows[field] = self.color_row(
                label, lambda f=field, c=label: self._choose_color(f, c))
        self.reset_button = reset = kit.button(
            "Reset to preset", "quiet", compact=True,
            command=lambda: self.write(
                lambda: host.on_customization_changed(self.owner.pane_index, "reset", None)))
        reset_row, reset_layout = _hrow()
        reset_row.setContentsMargins(0, 5, 0, 0)
        reset_layout.addWidget(reset)
        reset_layout.addStretch(1)
        self.add(reset_row)

    def _choice(self, row_layout, caption: str, options, write_seam) -> object:
        box = QWidget()
        column = vbox(box)
        label = text(caption, color=MUTED, font=FONT_SMALL)
        label.setContentsMargins(0, 0, 0, 4)
        column.addWidget(label)
        dropdown = self.kit.dropdown(
            options, on_change=lambda name: self.write(
                lambda: write_seam(self.owner.pane_index, name)))
        column.addWidget(dropdown)
        row_layout.addWidget(box, 1)
        row_layout.addSpacing(6)
        return dropdown

    def _build_preview(self) -> QFrame:
        frame = self.kit.surface()
        frame.setFixedSize(132, 116)
        layout = frame.layout()
        title = text("PANE PREVIEW", color=DIM_COLOR, font=FONT_SMALL)
        title.setContentsMargins(10, 8, 0, 0)
        layout.addWidget(title)
        self.preview: SpritePreview = self.kit.sprite_preview(sprites.resolve_palette())
        layout.addWidget(self.preview, 1, Qt.AlignmentFlag.AlignCenter)
        return frame

    def _choose_color(self, field: str, caption: str) -> None:
        index = self.owner.pane_index
        current = self.host.pane_color(index, field)
        chosen = QColorDialog.getColor(QColor(current) if current else QColor(), self.owner, caption)
        if chosen.isValid():
            self.write(lambda: self.host.on_customization_changed(index, field, chosen.name()))

    def _save_name(self) -> None:
        index = self.owner.pane_index
        pane = self.host.panes[index]
        new = self.name_entry.text().strip()
        if new == (pane._label or ""):
            return
        self.write(lambda: self.host.on_customization_changed(index, "label", new))
        # The write may have resolved the typed name (blank back to the
        # default); show what the pane now carries even though the entry
        # still has focus.
        self.name_entry.setText(self.host.panes[index]._label or "")

    def refresh(self) -> None:
        owner, host = self.owner, self.host
        index = owner.pane_index
        pane = host.panes[index]
        names = owner.pane_names()
        self.pane_picker.set_choices(names)
        self.pane_picker.set(names[index])
        if not self.name_entry.hasFocus():
            self.name_entry.setText(pane._label or "")
        self.colorway.set(pane._colorway)
        self.pattern.set(pane._pattern)
        for _caption, field in _COLOR_ROWS:
            self.color_rows[field].set(host.pane_color(index, field))
        count = len(host.panes)
        # A saved home the pane cannot reach shows as what the widget draws.
        home = T.resolve_home(pane.customization.cat_home, index, count)
        self.cat_sits.set(home)
        self.cat_sits.items["on_top"].setVisible(T.resolve_home("on_top", index, count) == "on_top")
        self.preview.set_sprite(pane.palette, "content", T.placement(home).mirrored)


class UsageTab(_Tab):
    def build(self) -> None:
        kit, host = self.kit, self.host
        kit.page_header(self.frame, "Usage", "Choose what each pane tells you at a glance.")
        self.groups: Dict[str, tuple] = {}
        first = True
        for key, title, items, getter, setter in (
            ("view", "View", VIEW_MODE_ITEMS, host.view_mode, host.on_view_mode),
            ("window", "Usage window", USAGE_WINDOW_ITEMS, host.usage_window, host.on_usage_window),
            ("readout", "Readout", USAGE_READOUT_ITEMS, host.usage_readout, host.on_usage_readout),
        ):
            if getter is None or setter is None:
                continue
            kit.section_header(self.frame, title, top=0 if first else 13)
            first = False
            control = kit.segmented(items, on_change=lambda v, s=setter: self.write(lambda: s(v)))
            self.add(control)
            self.groups[key] = (control, getter)

        self.budget_hint = None
        self.has_notes = (host.usage_notes_enabled is not None and host.on_toggle_usage_notes is not None
                          and host.usage_note_threshold is not None
                          and host.set_usage_note_threshold is not None)
        if self.has_notes:
            self._build_notes(kit)
        self.has_budget = host.budget_for_pane is not None and host.set_budget_for_pane is not None
        if not self.has_budget:
            return
        header = kit.section_header(self.frame, "Budget", "Per pane", top=17)
        self.budget_hint = header.hint
        card = self.card()
        top, top_row = _hrow()
        top_row.setContentsMargins(12, 11, 12, 8)
        top_row.addWidget(text("Editing", font=FONT_MEDIUM))
        top_row.addSpacing(12)
        self.pane_picker = kit.dropdown([], width=17, on_change=self.owner.pick_pane_by_name)
        top_row.addWidget(self.pane_picker)
        top_row.addStretch(1)
        kit.add(card, top)
        bottom, bottom_row = _hrow()
        bottom_row.setContentsMargins(12, 0, 12, 5)
        self.budget_label = text("")
        bottom_row.addWidget(self.budget_label)
        bottom_row.addStretch(1)
        bottom_row.addWidget(text("$", color=MUTED))
        bottom_row.addSpacing(5)
        self.budget_holder, self.budget_entry = kit.entry(width=9)
        bottom_row.addWidget(self.budget_holder)
        kit.add(card, bottom)
        self.budget_entry.editingFinished.connect(self._save_budget)
        self.budget_error = text("", color=BAD, font=FONT_SMALL)
        self.budget_error.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.budget_error.setContentsMargins(12, 0, 12, 10)
        kit.add(card, self.budget_error)

    def _entry_row(self, card, caption: str, width: int, *, secret: bool = False,
                   top: int = 0):
        row, layout = _hrow()
        layout.setContentsMargins(12, top, 12, 5)
        layout.addWidget(text(caption))
        layout.addStretch(1)
        holder, entry = self.kit.entry(width=width)
        if secret:
            entry.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(holder)
        self.kit.add(card, row)
        error = text("", color=BAD, font=FONT_SMALL)
        error.setAlignment(Qt.AlignmentFlag.AlignRight)
        error.setContentsMargins(12, 0, 12, 0)
        self.kit.add(card, error)
        return holder, entry, error

    def _build_notes(self, kit) -> None:
        host = self.host
        kit.section_header(self.frame, "Usage alerts", top=17)
        card = self.card()
        self.note_entries: Dict[str, tuple] = {}
        for kind, caption in (("session", "Session %"), ("weekly", "Weekly %")):
            holder, entry, error = self._entry_row(card, caption, 6, top=8 if kind == "session" else 0)
            entry.editingFinished.connect(lambda k=kind: self._save_note_threshold(k))
            self.note_entries[kind] = (holder, entry, error)
        self.notes_toggle = kit.toggle(
            "Tell Claude Code sessions",
            detail="Tells running Claude Code sessions when usage passes these levels.",
            on_change=lambda _v: self.write(host.on_toggle_usage_notes))
        self.notes_toggle.setContentsMargins(12, 2, 12, 4)
        kit.add(card, self.notes_toggle)
        self.has_ntfy = all(seam is not None for seam in (
            host.ntfy_enabled, host.on_toggle_ntfy, host.ntfy_value, host.set_ntfy_value))
        if self.has_ntfy:
            self._build_ntfy(kit, card)

    def _build_ntfy(self, kit, card) -> None:
        host = self.host
        self.ntfy_toggle = kit.toggle(
            "Send to ntfy",
            detail="Pushes a notification when usage passes these levels.",
            on_change=lambda _v: self.write(host.on_toggle_ntfy))
        self.ntfy_toggle.setContentsMargins(12, 2, 12, 4)
        kit.add(card, self.ntfy_toggle)
        self.ntfy_entries: Dict[str, tuple] = {}
        for field, caption, secret in (("url", "Server", False), ("topic", "Topic", False),
                                       ("token", "Access token", True)):
            holder, entry, error = self._entry_row(card, caption, 30, secret=secret)
            entry.editingFinished.connect(lambda f=field: self._save_ntfy(f))
            self.ntfy_entries[field] = (holder, entry, error)
        row, layout = _hrow()
        layout.setContentsMargins(12, 0, 12, 10)
        self.ntfy_test_button = kit.button("Send test", "secondary", self._send_ntfy_test,
                                           compact=True)
        layout.addWidget(self.ntfy_test_button)
        layout.addSpacing(10)
        self.ntfy_result = text("", color=MUTED, font=FONT_SMALL)
        layout.addWidget(self.ntfy_result)
        layout.addStretch(1)
        kit.add(card, row)
        self._ntfy_testing = False
        self.ntfy_mailbox = Mailbox()
        self.ntfy_poller = QtPoller(self.owner, self.ntfy_mailbox, self._on_ntfy_result,
                                    lambda: self._ntfy_testing)

    def close(self) -> None:
        if getattr(self, "has_ntfy", False):
            self.ntfy_poller.cancel()

    def _save_ntfy(self, field: str) -> bool:
        """Save one ntfy entry if it changed. True when the entry is valid."""
        holder, entry, error_label = self.ntfy_entries[field]
        if entry.text().strip() == self.host.ntfy_value(field):
            error = None
        else:
            error = self.host.set_ntfy_value(field, entry.text())
            if error is None:
                self.host.notify_state_changed()
        error_label.setText(error or "")
        self.kit.set_entry_error(holder, error is not None)
        return error is None

    def _send_ntfy_test(self) -> None:
        if self._ntfy_testing or self.host.send_ntfy_test is None:
            return
        if not all([self._save_ntfy(f) for f in self.ntfy_entries]):
            return
        self._ntfy_testing = True
        self.ntfy_result.setText("Sending...")
        set_text_color(self.ntfy_result, MUTED)
        run_worker(self.ntfy_mailbox, "ntfy-test", self.host.send_ntfy_test)
        self.ntfy_poller.start()

    def _on_ntfy_result(self, _tag: str, ok: bool, value) -> None:
        self._ntfy_testing = False
        error = value if ok else str(value)
        if error:
            self.ntfy_result.setText(str(error))
            set_text_color(self.ntfy_result, BAD)
        else:
            self.ntfy_result.setText("Sent")
            set_text_color(self.ntfy_result, GOOD)

    def _save_note_threshold(self, kind: str) -> None:
        holder, entry, error_label = self.note_entries[kind]
        if entry.text().strip() == str(self.host.usage_note_threshold(kind)):
            error = None
        else:
            error = self.host.set_usage_note_threshold(kind, entry.text())
            if error is None:
                self.host.notify_state_changed()
        error_label.setText(error or "")
        self.kit.set_entry_error(holder, error is not None)

    def _budget_text(self) -> str:
        current = self.host.budget_for_pane(self.owner.pane_index)
        return "" if current is None else f"{current:g}"

    def _save_budget(self) -> None:
        if self.budget_entry.text().strip() == self._budget_text():
            self._show_budget_error(None)
            return
        index = self.owner.pane_index
        error = self.host.set_budget_for_pane(index, self.budget_entry.text())
        self._show_budget_error(error)
        if error is None:
            self.host.notify_state_changed()

    def _show_budget_error(self, error: Optional[str]) -> None:
        self.budget_error.setText(error or "")
        self.kit.set_entry_error(self.budget_holder, error is not None)

    def refresh(self) -> None:
        host = self.host
        for control, getter in self.groups.values():
            control.set(getter())
        if self.has_notes:
            self.notes_toggle.set(host.usage_notes_enabled())
            for kind, (_holder, entry, _error) in self.note_entries.items():
                if not entry.hasFocus():
                    entry.setText(str(host.usage_note_threshold(kind)))
            if self.has_ntfy:
                self.ntfy_toggle.set(host.ntfy_enabled())
                for field, (_holder, entry, _error) in self.ntfy_entries.items():
                    if not entry.hasFocus():
                        entry.setText(host.ntfy_value(field))
        if not self.has_budget:
            return
        owner = self.owner
        names = owner.pane_names()
        self.pane_picker.set_choices(names)
        self.pane_picker.set(names[owner.pane_index])
        window_key = host.usage_window() if host.usage_window else "7d"
        self.budget_label.setText(_BUDGET_LABELS.get(window_key, "Budget"))
        if self.budget_hint is not None:
            self.budget_hint.setText(f"Per pane · {_BUDGET_HINTS.get(window_key, '')}")
        if not self.budget_entry.hasFocus():
            self.budget_entry.setText(self._budget_text())
            self._show_budget_error(None)


class ThemeTab(_Tab):
    """The global theme (theme.json), applied to the widget live. Each edit
    changes one key of the current theme's dict and lets theme.py say whether
    it would survive load_theme; a refused value is marked and not saved."""

    def build(self) -> None:
        kit = self.kit
        kit.page_header(self.frame, "Theme", "How every pane looks. Applies to all of them.")
        self.entries: Dict[Tuple[str, str], object] = {}   # (section, key) -> error holder
        self.controls: Dict[Tuple[str, str], object] = {}  # (section, key) -> control with set()
        self.show_toggles: Dict[str, object] = {}

        kit.section_header(self.frame, "Card", top=0)
        self.opacity = kit.segmented(
            [(str(n), "Invisible" if n == 0 else f"{n}%") for n in range(0, 101, 10)],
            on_change=lambda v: self._edit("card", "opacity", int(v)), compact=True)
        self.add(self.opacity)
        self.opacity_hint = text(OPACITY_HINT, color=WARN, font=FONT_SMALL)
        self.opacity_hint.setContentsMargins(0, 4, 0, 0)
        self.add(self.opacity_hint)
        self.frame.layout().addSpacing(6)
        self._color("card", "background", "Background")
        self._number("card", "radius", "Corner radius", 0, 16)
        self._number("card", "border_width", "Border width", 0, 3)
        self._color("card", "border_color", "Border colour")

        kit.section_header(self.frame, "Bars", top=14)
        self.layout_choice = kit.segmented(
            [("beside_cat", "Beside the cat"), ("full_width", "Full width")],
            on_change=lambda v: self._edit("bars", "layout", v))
        self.add(self.layout_choice)
        hint = text("Full width only applies to cards whose cat sits outside.",
                    color=DIM_COLOR, font=FONT_SMALL)
        hint.setContentsMargins(0, 4, 0, 6)
        self.add(hint)
        self.shape = kit.segmented([("flat", "Flat"), ("rounded", "Rounded")],
                                   on_change=lambda v: self._edit("bars", "shape", v))
        self.add(self.shape)
        self.frame.layout().addSpacing(6)
        self._number("bars", "height", "Height", 4, 10)
        for key, caption in (("track", "Track"), ("low", "Low"), ("mid", "Mid"),
                             ("high", "High"), ("neutral", "Neutral")):
            self._color("bars", key, caption)
        self._number("bars", "mid_at", "Mid from", 0, 100, suffix="%")
        self._number("bars", "high_at", "High from", 0, 100, suffix="%")

        kit.section_header(self.frame, "Text", top=14)
        self._color("text", "foreground", "Text")
        self._color("text", "dim", "Dim")
        kit.section_header(self.frame, "Show", top=8)
        card = self.card()
        for element, name in _TEXT_NAMES:
            toggle = kit.toggle(name, compact=True,
                                on_change=lambda on, e=element: self._set_shown(e, on))
            toggle.setContentsMargins(12, 4, 12, 4)
            kit.add(card, toggle)
            self.show_toggles[element] = toggle
        kit.section_header(self.frame, "Outline", "Helps text over a see-through card", top=10)
        self.halo = kit.segmented([("off", "Off"), ("auto", "Auto"), ("on", "On")],
                                  on_change=lambda v: self._edit("text", "halo", v))
        self.add(self.halo)
        self.frame.layout().addSpacing(6)
        self._number("text", "halo_below", "Auto below", 0, 100, suffix="%")

        reset_row, reset_layout = _hrow()
        reset_row.setContentsMargins(0, 14, 0, 0)
        self.reset_button = kit.button(
            "Reset theme", "danger", compact=True,
            command=lambda: self.write(lambda: self.host.set_theme(T.Theme())))
        reset_layout.addWidget(self.reset_button)
        reset_layout.addStretch(1)
        self.add(reset_row)

    # -- rows --------------------------------------------------------------

    def _color(self, section: str, key: str, caption: str) -> None:
        row = self.color_row(caption, lambda: self._choose_color(section, key, caption))
        self.controls[(section, key)] = row

    def _number(self, section: str, key: str, caption: str, lo: int, hi: int, *,
                suffix: str = "") -> None:
        holder, spin = self.kit.spin(lo, hi, suffix=suffix,
                                     on_change=lambda v: self._edit(section, key, v))
        card = self.kit.surface(rows=False)
        card.setFixedHeight(36)
        row = hbox(card)
        row.setContentsMargins(11, 0, 11, 0)
        row.addWidget(text(caption, font=FONT_BODY))
        row.addStretch(1)
        row.addWidget(holder)
        self.add(card)
        self.frame.layout().addSpacing(3)
        self.entries[(section, key)] = holder
        self.controls[(section, key)] = spin

    def _choose_color(self, section: str, key: str, caption: str) -> None:
        current = getattr(getattr(self.host.theme(), section), key)
        chosen = QColorDialog.getColor(QColor(current), self.owner, caption)
        if chosen.isValid():
            self._edit(section, key, chosen.name())

    def _set_shown(self, element: str, on: bool) -> None:
        shown = set(self.host.theme().text.show)
        shown.add(element) if on else shown.discard(element)
        # Sorted, as theme_to_dict writes it.
        self._edit("text", "show", sorted(shown))

    # -- edits -------------------------------------------------------------

    def _edit(self, section: str, key: str, value) -> bool:
        """Change one key of the current theme. True when it was saved."""
        current = self.host.theme()
        raw = T.theme_to_dict(current)
        raw[section][key] = value
        bad = T.rejected_fields(raw, current)
        if f"{section}.{key}" in bad:
            # Every refused field is marked (Mid from and High from together)
            # and the old value stays.
            for name in bad:
                holder = self.entries.get(tuple(name.split(".", 1)))
                if holder is not None:
                    self.kit.set_entry_error(holder, True)
            return False
        for holder in self.entries.values():
            self.kit.set_entry_error(holder, False)
        self.write(lambda: self.host.set_theme(T.theme_from_dict(raw, current)))
        return True

    def refresh(self) -> None:
        theme = self.host.theme()
        self.opacity.set(str(theme.card.opacity))
        self.opacity_hint.setVisible(theme.card.opacity == 0)
        self.layout_choice.set(theme.bars.layout)
        self.shape.set(theme.bars.shape)
        self.halo.set(theme.text.halo)
        for (section, key), control in self.controls.items():
            value = getattr(getattr(theme, section), key)
            if isinstance(control, ColorRow):
                control.set(value)
            elif not control.hasFocus():
                control.set(value)
        for element, toggle in self.show_toggles.items():
            toggle.set(element in theme.text.show)
        for holder in self.entries.values():
            self.kit.set_entry_error(holder, False)


def _tab_classes() -> Dict[str, type]:
    # Accounts and Stream Dock join this table (and TABS) in later slices.
    return {
        "general": GeneralTab,
        "look": LookTab,
        "theme": ThemeTab,
        "usage": UsageTab,
    }


class SettingsWindow(QWidget):
    """The Settings window. Use SettingsWindow.open(), never the constructor."""

    @classmethod
    def open(cls, host, pane_index: int = 0) -> "SettingsWindow":
        global _instance
        existing = _instance
        if existing is not None and existing.alive() and existing.host is host:
            existing.set_pane(pane_index)
            existing.raise_window()
            return existing
        if existing is not None:
            existing.close()
        instance = _instance = cls(host, pane_index)
        instance.raise_window()
        return instance

    def __init__(self, host, pane_index: int = 0):
        super().__init__()
        self.host = host
        self.kit = Kit()
        self.pane_index = self._clamp(pane_index)
        self._names: List[str] = []
        self._closed = False
        self.kit.style_window(self)
        self.setWindowTitle("Tokitty: Settings")
        self.setMinimumSize(BASE_WIDTH, BASE_HEIGHT)
        # Parentless, so it follows the widget's Always in front itself.
        host.register_window(self)

        shell = hbox(self)
        self.rail = self.kit.rail(TABS, version_text(), on_select=self._show)
        shell.addWidget(self.rail)
        body = QWidget()
        body_col = vbox(body)
        body_col.setContentsMargins(20, 18, 20, 12)
        shell.addWidget(body, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.verticalScrollBar().setProperty("kit", "thin")
        self._stack = QWidget()
        vbox(self._stack)
        self.scroll.setWidget(self._stack)
        # setWidget turns the fill on, which would paint the system colour
        # over the window's.
        self._stack.setAutoFillBackground(False)
        body_col.addWidget(self.scroll, 1)

        self.footer = QWidget()
        footer_col = vbox(self.footer)
        rule = QFrame()
        rule.setProperty("kit", "rule")
        rule.setFixedHeight(1)
        footer_col.addWidget(rule)
        footer_col.addSpacing(9)
        footer_row, row = _hrow()
        row.addWidget(text("Changes apply immediately", font=FONT_SMALL, color=DIM_COLOR))
        row.addStretch(1)
        row.addWidget(self.kit.button("Close", "secondary", self.close, compact=True))
        footer_col.addWidget(footer_row)
        body_col.addWidget(self.footer)

        classes = _tab_classes()
        self.tabs = {key: classes[key](self) for key, _title in TABS}
        for tab in self.tabs.values():
            tab.frame.setVisible(False)
            self._stack.layout().addWidget(tab.frame)
        self.resize(BASE_WIDTH, self._tallest_tab_height())
        self.current = "general"
        self._show(self.current)
        host.settings_refresh = self.refresh

    def _tallest_tab_height(self) -> int:
        """The base height, grown to the tallest tab. The base fits Windows
        fonts; macOS ones run larger and cut off the bottom of Usage. Capped
        to the screen, and the window stays resizable either way."""
        chrome = 18 + 12 + self.footer.sizeHint().height()
        needed = max(chrome + tab.frame.sizeHint().height() for tab in self.tabs.values())
        screen = self.screen()
        cap = screen.availableGeometry().height() - 80 if screen is not None else needed
        return max(BASE_HEIGHT, min(needed, cap))

    # -- shell ---------------------------------------------------------------

    def _show(self, key: str) -> None:
        self.current = key
        self.rail.paint(key)
        for tab_key, tab in self.tabs.items():
            tab.frame.setVisible(tab_key == key)
        self.scroll.verticalScrollBar().setValue(0)
        self.refresh()

    # -- panes ---------------------------------------------------------------

    def _clamp(self, index: int) -> int:
        count = len(self.host.panes)
        return min(max(int(index or 0), 0), max(count - 1, 0))

    def pane_names(self) -> List[str]:
        names = [pane._label or f"Cat {i + 1}" for i, pane in enumerate(self.host.panes)]
        seen = {n for n in names if names.count(n) == 1}
        out = [n if n in seen else f"{n} ({i + 1})" for i, n in enumerate(names)]
        self._names = out
        return out

    def pick_pane_by_name(self, name: str) -> None:
        names = self.pane_names()
        if name in names:
            self.set_pane(names.index(name))

    def set_pane(self, index: int) -> None:
        self.pane_index = self._clamp(index)
        self.refresh()

    # -- lifecycle -----------------------------------------------------------

    def alive(self) -> bool:
        return not self._closed

    def raise_window(self) -> None:
        """Bring it up in front: show, lift and ask for focus. The stay-on-top
        hint (host.register_window) is what keeps it above a topmost widget."""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        """Re-read every control from the getters. Writes nothing."""
        if not self.alive():
            return
        for tab in self.tabs.values():
            tab.refresh()

    def closeEvent(self, event) -> None:
        self._release()
        super().closeEvent(event)

    def _release(self) -> None:
        global _instance
        if self._closed:
            return
        self._closed = True
        for tab in self.tabs.values():
            tab.close()
        if self.host.settings_refresh == self.refresh:
            self.host.settings_refresh = None
        self.host.forget_window(self)
        if _instance is self:
            _instance = None

