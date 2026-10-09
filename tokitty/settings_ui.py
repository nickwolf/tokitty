"""The Settings window: a dark left rail over a stack of tabs.

One SettingsWindow per Tk root, opened with SettingsWindow.open(window,
pane_index). It owns no state: every control is read from the getters the
TokittyWindow it is given carries (the seams run_gui sets), and every write
goes through the same seam the right-click menu used, followed by
window.notify_state_changed() so the tray and any other view re-sync.

Never call ttk.Style().theme_use() here; see settings_widgets.py. Tk thread
only: nothing in this module starts a worker.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser
from typing import Callable, Dict, List, Optional

from tokitty import sprites
from tokitty.settings_async import Mailbox, Poller, run_worker
from tokitty.settings_widgets import (
    BAD, BASE_HEIGHT, BASE_WIDTH, BG_COLOR, BORDER, DIM_COLOR, GOOD,
    FONT_BODY, FONT_MEDIUM, FONT_MONO, FONT_SMALL, MUTED, RAIL, SURFACE,
    Kit, build_rail, draw_sprite, paint_rail, sprite_size, text,
)
from tokitty.transparency import LEVELS, level_label
from tokitty.ui import (
    USAGE_READOUT_ITEMS, USAGE_WINDOW_ITEMS, VIEW_MODE_ITEMS, resolve_bar_fill,
)

_instances: Dict[int, "SettingsWindow"] = {}

TABS = (
    ("general", "General"),
    ("look", "Look"),
    ("usage", "Usage"),
    ("accounts", "Accounts"),
    ("streamdock", "Stream Dock"),
)

_BUDGET_LABELS = {
    "24h": "Budget for the last 24 hours",
    "7d": "Budget for the last 7 days",
    "month": "Budget for this month",
}
_BUDGET_HINTS = {"24h": "last 24 hours", "7d": "last 7 days", "month": "this month"}

# (caption, customization field, how to read the effective color from a pane)
_COLOR_ROWS = (
    ("Coat", "coat_base"),
    ("Shade", "coat_shade"),
    ("Card background", "card_bg"),
    ("Bar fill", "bar_fill"),
)


def version_text() -> str:
    """The running version for display, e.g. "v0.2.2"."""
    try:
        from tokitty.updater import running_version

        version = running_version().version
    except Exception:
        return "dev"
    if version[:1].isdigit():
        return "v" + version
    return version


def _has_focus(widget) -> bool:
    try:
        return str(widget.tk.call("focus")) == str(widget)
    except tk.TclError:
        return False


def _pane_color(pane, field: str) -> Optional[str]:
    if field == "coat_base":
        return pane.palette.get("o")
    if field == "coat_shade":
        return pane.palette.get("O")
    if field == "card_bg":
        return pane._card_bg
    return pane._bar_fill or None


class _Tab:
    """One page of the stack. build() once, refresh() whenever shown or
    state changes; refresh() only ever calls control.set()."""

    def __init__(self, owner: "SettingsWindow", parent: tk.Frame):
        self.owner = owner
        self.win = owner.window
        self.kit = owner.kit
        self.frame = tk.Frame(parent, bg=BG_COLOR)
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        pass

    def close(self) -> None:
        """The window is going away: cancel any poll this tab scheduled."""

    def write(self, action: Callable[[], None]) -> None:
        """Run a seam, then re-sync every view (this one included)."""
        action()
        self.win.notify_state_changed()


class GeneralTab(_Tab):
    def build(self) -> None:
        kit, win = self.kit, self.win
        px = kit.px
        kit.page_header(self.frame, "General", "The little things that make tokitty yours.")
        card = tk.Frame(self.frame, bg=SURFACE)
        card.pack(fill="x")
        self.toggles: Dict[str, object] = {}

        def add(key, title, detail, on_change):
            toggle = kit.toggle(card, title, detail=detail, on_change=on_change)
            toggle.frame.pack(fill="x", padx=px(12), pady=px(2))
            self.toggles[key] = toggle

        add("aot", "Always in front", "Keep panes above other windows",
            lambda _v: self.write(win._toggle_always_on_top))
        if win.on_toggle_tray is not None and win.tray_enabled is not None:
            add("tray", "Show tray icon", "Quick access when panes are hidden",
                lambda _v: self.write(win.on_toggle_tray))
        if win.on_toggle_autostart is not None and win.autostart_enabled is not None:
            add("autostart", "Start at login", "Launch tokitty when you sign in",
                lambda _v: self.write(win.on_toggle_autostart))
        if win.on_toggle_surprise is not None and win.surprise_me is not None:
            add("surprise", "Surprise me", "New random cat look at every launch",
                lambda _v: self.write(win.on_toggle_surprise))

        self.transparency = None
        if win.opacity is not None:
            kit.section_header(self.frame, "Transparency", "Panes only", top=10)
            self.transparency = kit.segmented(
                self.frame, [(str(n), level_label(n)) for n in LEVELS],
                on_change=lambda v: self.write(lambda: win._select_opacity(int(v))))
            self.transparency.frame.pack(fill="x")

        self.update_toggle = None
        self.update_pill = None
        if win.update_check_enabled is not None or win.on_check_updates is not None:
            kit.section_header(self.frame, "Updates", top=10)
            card = tk.Frame(self.frame, bg=SURFACE)
            card.pack(fill="x")
            if win.update_check_enabled is not None and win.on_toggle_update_check is not None:
                self.update_toggle = kit.toggle(
                    card, "Check for updates automatically",
                    on_change=lambda _v: self.write(win.on_toggle_update_check))
                self.update_toggle.frame.pack(fill="x", padx=px(12), pady=(px(9), px(6)))
            lower = tk.Frame(card, bg=SURFACE)
            lower.pack(fill="x", padx=px(12), pady=(px(3), px(9)))
            self.update_pill = kit.pill(lower, "", "good")
            self.update_pill.pack(side="left")
            if win.on_check_updates is not None:
                kit.button(lower, "Check now", "secondary", compact=True,
                           command=lambda: self.write(win.on_check_updates)).pack(side="right")

    def refresh(self) -> None:
        win = self.win
        self.toggles["aot"].set(win._always_on_top_bool)
        for key, getter in (("tray", win.tray_enabled), ("autostart", win.autostart_enabled),
                            ("surprise", win.surprise_me)):
            if key in self.toggles and getter is not None:
                self.toggles[key].set(getter())
        if self.transparency is not None:
            self.transparency.set(str(win.opacity()))
        if self.update_toggle is not None:
            self.update_toggle.set(win.update_check_enabled())
        if self.update_pill is not None:
            label = win.update_available_label() if win.update_available_label else None
            if label:
                self.kit.set_pill(self.update_pill, label, "warn")
            else:
                self.kit.set_pill(self.update_pill, f"Up to date · {version_text()}", "good")


class LookTab(_Tab):
    def build(self) -> None:
        kit, win = self.kit, self.win
        px = kit.px
        kit.page_header(self.frame, "Look", "Give each pane its own personality.")
        picker = tk.Frame(self.frame, bg=BG_COLOR)
        picker.pack(fill="x", pady=(0, px(11)))
        text(picker, "Editing", font=FONT_MEDIUM).pack(side="left", padx=(0, px(10)))
        self.pane_picker = kit.dropdown(picker, [], width=18, on_change=self.owner.pick_pane_by_name)
        self.pane_picker.frame.pack(side="left")
        if win.on_randomize is not None:
            kit.button(picker, "Randomize", "secondary", compact=True,
                       command=lambda: self.write(lambda: win.on_randomize(self.owner.pane_index))
                       ).pack(side="right")

        upper = tk.Frame(self.frame, bg=BG_COLOR)
        upper.pack(fill="x")
        left = tk.Frame(upper, bg=BG_COLOR)
        left.pack(side="left", fill="x", expand=True, padx=(0, px(10)))
        self._build_preview(upper).pack(side="right", anchor="s")

        text(left, "Name", color=MUTED, font=FONT_SMALL).pack(anchor="w", pady=(0, px(4)))
        holder, self.name_entry = kit.entry(left)
        holder.pack(fill="x", pady=(0, px(8)))
        self.name_entry.bind("<Return>", self._save_name)
        self.name_entry.bind("<FocusOut>", self._save_name)

        choice_row = tk.Frame(left, bg=BG_COLOR)
        choice_row.pack(fill="x")
        self.colorway = self._choice(choice_row, "Colorway", list(sprites.COLORWAYS),
                                     win._select_colorway)
        self.pattern = self._choice(choice_row, "Pattern", list(sprites.PATTERNS),
                                    win._select_pattern)

        kit.section_header(self.frame, "Custom colors", "Overrides the preset", top=14)
        self.color_widgets: Dict[str, tuple] = {}
        for caption, field in _COLOR_ROWS:
            self._color_row(caption, field)
        kit.button(self.frame, "Reset to preset", "quiet", compact=True,
                   command=lambda: self.write(
                       lambda: win._fire_customization_changed(self.owner.pane_index, "reset", None))
                   ).pack(anchor="w", pady=(px(5), 0))

    def _choice(self, row, caption, options, seam):
        px = self.kit.px
        box = tk.Frame(row, bg=BG_COLOR)
        box.pack(side="left", fill="x", expand=True, padx=(0, px(6)))
        text(box, caption, color=MUTED, font=FONT_SMALL).pack(anchor="w", pady=(0, px(4)))
        dropdown = self.kit.dropdown(
            box, options,
            on_change=lambda name: self.write(lambda: seam(self.owner.pane_index, name)))
        dropdown.frame.pack(fill="x")
        return dropdown

    def _build_preview(self, parent):
        px = self.kit.px
        frame = tk.Frame(parent, bg=SURFACE, width=px(132), height=px(116))
        frame.pack_propagate(False)
        text(frame, "PANE PREVIEW", bg=SURFACE, color=DIM_COLOR, font=FONT_SMALL).pack(
            anchor="w", padx=px(10), pady=(px(8), 0))
        self.preview = tk.Canvas(frame, bg=SURFACE, width=px(114), height=px(82),
                                 highlightthickness=0)
        self.preview.pack()
        return frame

    def _paint_preview(self, pane) -> None:
        px = self.kit.px
        self.preview.delete("all")
        cw, ch = sprite_size()
        cell = px(3)
        x0 = (px(114) - cw * cell) // 2
        y0 = (px(82) - ch * cell) // 2
        draw_sprite(self.preview, pane.palette, cell, x0, y0)

    def _color_row(self, caption: str, field: str) -> None:
        kit = self.kit
        px = kit.px
        row = tk.Frame(self.frame, bg=SURFACE, height=px(32))
        row.pack(fill="x", pady=(0, px(3)))
        row.pack_propagate(False)
        text(row, caption, bg=SURFACE, font=FONT_BODY).pack(side="left", padx=px(11))
        kit.button(row, "Change…", "quiet", compact=True,
                   command=lambda f=field, c=caption: self._choose_color(f, c)
                   ).pack(side="right", padx=px(6))
        value = text(row, "", bg=SURFACE, color=MUTED, font=FONT_MONO)
        value.pack(side="right")
        swatch = tk.Frame(row, bg=SURFACE, width=px(14), height=px(14))
        swatch.pack(side="right", padx=px(8))
        self.color_widgets[field] = (swatch, value)

    def _choose_color(self, field: str, caption: str) -> None:
        pane = self.win.panes[self.owner.pane_index]
        current = _pane_color(pane, field)
        options = {"parent": self.owner.toplevel, "title": caption}
        if current:
            options["color"] = current
        _rgb, chosen = colorchooser.askcolor(**options)
        if chosen:
            index = self.owner.pane_index
            self.write(lambda: self.win._fire_customization_changed(index, field, chosen))

    def _save_name(self, _event=None) -> None:
        index = self.owner.pane_index
        pane = self.win.panes[index]
        new = self.name_entry.get().strip()
        if new == (pane._label or ""):
            return
        self.write(lambda: self.win._fire_customization_changed(index, "label", new))
        # The write may have resolved the typed name (blank back to the
        # default); show what the pane now carries even though the entry
        # still has focus.
        self._show_name(self.win.panes[index])

    def _show_name(self, pane) -> None:
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, pane._label or "")

    def refresh(self) -> None:
        owner = self.owner
        pane = self.win.panes[owner.pane_index]
        names = owner.pane_names()
        self.pane_picker.set_choices(names)
        self.pane_picker.set(names[owner.pane_index])
        if not _has_focus(self.name_entry):
            self._show_name(pane)
        self.colorway.set(pane._colorway)
        self.pattern.set(pane._pattern)
        for caption, field in _COLOR_ROWS:
            swatch, value = self.color_widgets[field]
            color = _pane_color(pane, field)
            if color:
                swatch.configure(bg=color)
                value.configure(text=color.upper())
            else:
                swatch.configure(bg=resolve_bar_fill(0.0, None))
                value.configure(text="AUTOMATIC")
        self._paint_preview(pane)


class UsageTab(_Tab):
    def build(self) -> None:
        kit, win = self.kit, self.win
        px = kit.px
        kit.page_header(self.frame, "Usage", "Choose what each pane tells you at a glance.")
        self.groups: Dict[str, tuple] = {}
        first = True
        for key, title, items, getter, setter in (
            ("view", "View", VIEW_MODE_ITEMS, win.view_mode, win.on_view_mode),
            ("window", "Usage window", USAGE_WINDOW_ITEMS, win.usage_window, win.on_usage_window),
            ("readout", "Readout", USAGE_READOUT_ITEMS, win.usage_readout, win.on_usage_readout),
        ):
            if getter is None or setter is None:
                continue
            kit.section_header(self.frame, title, top=0 if first else 13)
            first = False
            control = kit.segmented(self.frame, items,
                                    on_change=lambda v, s=setter: self.write(lambda: s(v)))
            control.frame.pack(fill="x")
            self.groups[key] = (control, getter)

        self.budget_hint = None
        self.has_notes = (win.usage_notes_enabled is not None and win.on_toggle_usage_notes is not None
                          and win.usage_note_threshold is not None
                          and win.set_usage_note_threshold is not None)
        if self.has_notes:
            self._build_notes(kit, px)
        self.has_budget = win.budget_for_pane is not None and win.set_budget_for_pane is not None
        if not self.has_budget:
            return
        header = kit.section_header(self.frame, "Budget", "Per pane", top=17)
        self.budget_hint = header.hint
        card = tk.Frame(self.frame, bg=SURFACE)
        card.pack(fill="x")
        top = tk.Frame(card, bg=SURFACE)
        top.pack(fill="x", padx=px(12), pady=(px(11), px(8)))
        text(top, "Editing", bg=SURFACE, font=FONT_MEDIUM).pack(side="left", padx=(0, px(12)))
        self.pane_picker = kit.dropdown(top, [], width=17, on_change=self.owner.pick_pane_by_name)
        self.pane_picker.frame.pack(side="left")
        bottom = tk.Frame(card, bg=SURFACE)
        bottom.pack(fill="x", padx=px(12), pady=(0, px(5)))
        self.budget_label = text(bottom, "", bg=SURFACE)
        self.budget_label.pack(side="left")
        amount = tk.Frame(bottom, bg=SURFACE)
        amount.pack(side="right")
        text(amount, "$", bg=SURFACE, color=MUTED).pack(side="left", padx=(0, px(5)))
        self.budget_holder, self.budget_entry = kit.entry(amount, width=9)
        self.budget_holder.pack(side="left")
        self.budget_entry.bind("<Return>", self._save_budget)
        self.budget_entry.bind("<FocusOut>", self._save_budget)
        self.budget_error = text(card, "", bg=SURFACE, color=BAD, font=FONT_SMALL)
        self.budget_error.pack(anchor="e", padx=px(12), pady=(0, px(10)))

    def _build_notes(self, kit, px) -> None:
        win = self.win
        kit.section_header(self.frame, "Usage alerts", top=17)
        card = tk.Frame(self.frame, bg=SURFACE)
        card.pack(fill="x")
        self.note_entries: Dict[str, tuple] = {}
        for kind, caption in (("session", "Session %"), ("weekly", "Weekly %")):
            row = tk.Frame(card, bg=SURFACE)
            row.pack(fill="x", padx=px(12), pady=(px(8) if kind == "session" else 0, px(5)))
            text(row, caption, bg=SURFACE).pack(side="left")
            holder, entry = kit.entry(row, width=6)
            holder.pack(side="right")
            error = text(card, "", bg=SURFACE, color=BAD, font=FONT_SMALL)
            error.pack(anchor="e", padx=px(12))
            for sequence in ("<Return>", "<FocusOut>"):
                entry.bind(sequence, lambda _e, k=kind: self._save_note_threshold(k))
            self.note_entries[kind] = (holder, entry, error)
        self.notes_toggle = kit.toggle(
            card, "Tell Claude Code sessions",
            detail="Tells running Claude Code sessions when usage passes these levels.",
            on_change=lambda _v: self.write(win.on_toggle_usage_notes))
        self.notes_toggle.frame.pack(fill="x", padx=px(12), pady=(px(2), px(4)))
        self.has_ntfy = all(seam is not None for seam in (
            win.ntfy_enabled, win.on_toggle_ntfy, win.ntfy_value, win.set_ntfy_value))
        if self.has_ntfy:
            self._build_ntfy(kit, px, card)

    def _build_ntfy(self, kit, px, card) -> None:
        win = self.win
        self.ntfy_toggle = kit.toggle(
            card, "Send to ntfy",
            detail="Pushes a notification when usage passes these levels.",
            on_change=lambda _v: self.write(win.on_toggle_ntfy))
        self.ntfy_toggle.frame.pack(fill="x", padx=px(12), pady=(px(2), px(4)))
        self.ntfy_entries: Dict[str, tuple] = {}
        for field, caption, secret in (("url", "Server", False), ("topic", "Topic", False),
                                       ("token", "Access token", True)):
            row = tk.Frame(card, bg=SURFACE)
            row.pack(fill="x", padx=px(12), pady=(0, px(5)))
            text(row, caption, bg=SURFACE).pack(side="left")
            holder, entry = kit.entry(row, width=30)
            if secret:
                entry.configure(show="*")
            holder.pack(side="right")
            error = text(card, "", bg=SURFACE, color=BAD, font=FONT_SMALL)
            error.pack(anchor="e", padx=px(12))
            for sequence in ("<Return>", "<FocusOut>"):
                entry.bind(sequence, lambda _e, f=field: self._save_ntfy(f))
            self.ntfy_entries[field] = (holder, entry, error)
        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x", padx=px(12), pady=(0, px(10)))
        self.ntfy_test_button = kit.button(row, "Send test", "secondary", self._send_ntfy_test,
                                           compact=True)
        self.ntfy_test_button.pack(side="left")
        self.ntfy_result = text(row, "", bg=SURFACE, color=MUTED, font=FONT_SMALL)
        self.ntfy_result.pack(side="left", padx=(px(10), 0))
        self._ntfy_testing = False
        self.ntfy_mailbox = Mailbox()
        self.ntfy_poller = Poller(self.owner.toplevel, self.ntfy_mailbox, self._on_ntfy_result,
                                  lambda: self._ntfy_testing)

    def close(self) -> None:
        if getattr(self, "has_ntfy", False):
            self.ntfy_poller.cancel()

    def _save_ntfy(self, field: str) -> bool:
        """Save one ntfy entry if it changed. True when the entry is valid."""
        holder, entry, error_label = self.ntfy_entries[field]
        if entry.get().strip() == self.win.ntfy_value(field):
            error = None
        else:
            error = self.win.set_ntfy_value(field, entry.get())
            if error is None:
                self.win.notify_state_changed()
        error_label.configure(text=error or "")
        self.kit.set_entry_error(holder, error is not None)
        return error is None

    def _send_ntfy_test(self) -> None:
        if self._ntfy_testing or self.win.send_ntfy_test is None:
            return
        if not all([self._save_ntfy(f) for f in self.ntfy_entries]):
            return
        self._ntfy_testing = True
        self.ntfy_result.configure(text="Sending...", fg=MUTED)
        run_worker(self.ntfy_mailbox, "ntfy-test", self.win.send_ntfy_test)
        self.ntfy_poller.start()

    def _on_ntfy_result(self, _tag: str, ok: bool, value) -> None:
        self._ntfy_testing = False
        error = value if ok else str(value)
        if error:
            self.ntfy_result.configure(text=str(error), fg=BAD)
        else:
            self.ntfy_result.configure(text="Sent", fg=GOOD)

    def _save_note_threshold(self, kind: str) -> None:
        holder, entry, error_label = self.note_entries[kind]
        if entry.get().strip() == str(self.win.usage_note_threshold(kind)):
            error = None
        else:
            error = self.win.set_usage_note_threshold(kind, entry.get())
            if error is None:
                self.win.notify_state_changed()
        error_label.configure(text=error or "")
        self.kit.set_entry_error(holder, error is not None)

    def _budget_text(self) -> str:
        current = self.win.budget_for_pane(self.owner.pane_index)
        return "" if current is None else f"{current:g}"

    def _save_budget(self, _event=None) -> None:
        if self.budget_entry.get().strip() == self._budget_text():
            self._show_budget_error(None)
            return
        index = self.owner.pane_index
        error = self.win.set_budget_for_pane(index, self.budget_entry.get())
        self._show_budget_error(error)
        if error is None:
            self.win.notify_state_changed()

    def _show_budget_error(self, error: Optional[str]) -> None:
        self.budget_error.configure(text=error or "")
        self.kit.set_entry_error(self.budget_holder, error is not None)

    def refresh(self) -> None:
        for control, getter in self.groups.values():
            control.set(getter())
        if self.has_notes:
            self.notes_toggle.set(self.win.usage_notes_enabled())
            for kind, (holder, entry, _error) in self.note_entries.items():
                if not _has_focus(entry):
                    entry.delete(0, "end")
                    entry.insert(0, str(self.win.usage_note_threshold(kind)))
            if self.has_ntfy:
                self.ntfy_toggle.set(self.win.ntfy_enabled())
                for field, (_holder, entry, _error) in self.ntfy_entries.items():
                    if not _has_focus(entry):
                        entry.delete(0, "end")
                        entry.insert(0, self.win.ntfy_value(field))
        if not self.has_budget:
            return
        owner = self.owner
        names = owner.pane_names()
        self.pane_picker.set_choices(names)
        self.pane_picker.set(names[owner.pane_index])
        window_key = self.win.usage_window() if self.win.usage_window else "7d"
        self.budget_label.configure(text=_BUDGET_LABELS.get(window_key, "Budget"))
        if self.budget_hint is not None:
            self.budget_hint.configure(text=f"Per pane · {_BUDGET_HINTS.get(window_key, '')}")
        if not _has_focus(self.budget_entry):
            self.budget_entry.delete(0, "end")
            self.budget_entry.insert(0, self._budget_text())
            self._show_budget_error(None)


def _tab_classes() -> Dict[str, type]:
    # The hook-aware tabs subclass _Tab, so they import this module; load
    # them here to keep the import one-directional at module load.
    from tokitty.settings_accounts import AccountsTab
    from tokitty.settings_streamdock import StreamDockTab

    return {
        "general": GeneralTab,
        "look": LookTab,
        "usage": UsageTab,
        "accounts": AccountsTab,
        "streamdock": StreamDockTab,
    }


class SettingsWindow:
    """The Settings Toplevel. Use SettingsWindow.open(), never the constructor."""

    @classmethod
    def open(cls, window, pane_index: int = 0) -> "SettingsWindow":
        key = id(window.root)
        existing = _instances.get(key)
        if existing is not None and existing.alive():
            existing.set_pane(pane_index)
            existing.raise_window()
            return existing
        instance = cls(window, pane_index)
        _instances[key] = instance
        instance.raise_window()
        return instance

    def __init__(self, window, pane_index: int = 0):
        self.window = window
        self.root = window.root
        self.kit = Kit(getattr(window, "_scale", 1.0))
        self.pane_index = self._clamp(pane_index)
        self._names: List[str] = []
        self._closed = False
        px = self.kit.px

        top = self.toplevel = tk.Toplevel(self.root)
        top.title("Tokitty — Settings")
        top.configure(bg=BG_COLOR)
        top.resizable(True, True)
        top.minsize(px(BASE_WIDTH), px(BASE_HEIGHT))
        self.size = (px(BASE_WIDTH), px(BASE_HEIGHT))
        top.geometry(f"{self.size[0]}x{self.size[1]}")
        try:
            top.transient(self.root)
        except tk.TclError:
            pass
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Destroy>", self._on_destroy, add="+")

        shell = tk.Frame(top, bg=BG_COLOR)
        shell.pack(fill="both", expand=True)
        self.rail = tk.Frame(shell, bg=RAIL, width=px(132))
        self.rail.pack(side="left", fill="y")
        self.rail.pack_propagate(False)
        content = tk.Frame(shell, bg=BG_COLOR)
        content.pack(side="left", fill="both", expand=True)
        self._build_rail()

        body = tk.Frame(content, bg=BG_COLOR)
        body.pack(fill="both", expand=True, padx=px(20), pady=(px(18), px(12)))
        footer = tk.Frame(body, bg=BG_COLOR)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, height=px(1), bg=BORDER).pack(fill="x", pady=(0, px(9)))
        text(footer, "Changes apply immediately", font=FONT_SMALL, color=DIM_COLOR).pack(side="left")
        self.kit.button(footer, "Close", "secondary", self.close, compact=True).pack(side="right")
        stack = tk.Frame(body, bg=BG_COLOR)
        stack.pack(fill="both", expand=True)

        classes = _tab_classes()
        self.tabs = {key: classes[key](self, stack) for key, _title in TABS}
        self._fit_to_tallest_tab()
        self.current = "general"
        self._show(self.current)
        self.window.settings_refresh = self.refresh

    def _fit_to_tallest_tab(self) -> None:
        """Grow past the base size when a tab needs it. The base fits Windows
        fonts; macOS ones run larger and cut off the bottom of Usage. Capped
        to the screen, and the window stays resizable either way."""
        top = self.toplevel
        needed = 0
        # Frames only, not _show: that refreshes, and refreshing Accounts
        # starts a hook status query.
        for tab in self.tabs.values():
            tab.frame.pack(fill="both", expand=True)
            top.update_idletasks()
            needed = max(needed, top.winfo_reqheight())
            tab.frame.pack_forget()
        height = max(self.size[1], min(needed, top.winfo_screenheight() - self.kit.px(80)))
        self.size = (self.size[0], height)
        top.geometry(f"{self.size[0]}x{self.size[1]}")

    # -- shell -------------------------------------------------------------

    def _build_rail(self) -> None:
        self.nav = build_rail(self.kit, self.rail, TABS, version_text(), on_select=self._show)

    def _show(self, key: str) -> None:
        self.current = key
        paint_rail(self.nav, key)
        for tab_key, tab in self.tabs.items():
            if tab_key == key:
                tab.frame.pack(fill="both", expand=True)
            else:
                tab.frame.pack_forget()
        self.refresh()

    # -- panes -------------------------------------------------------------

    def _clamp(self, index: int) -> int:
        count = len(self.window.panes)
        return min(max(int(index or 0), 0), max(count - 1, 0))

    def pane_names(self) -> List[str]:
        names = [pane._label or f"Cat {i + 1}" for i, pane in enumerate(self.window.panes)]
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

    # -- lifecycle ---------------------------------------------------------

    def alive(self) -> bool:
        try:
            return not self._closed and bool(self.toplevel.winfo_exists())
        except tk.TclError:
            return False

    def raise_window(self) -> None:
        """The widget is -topmost, so a plain transient Toplevel can open
        behind it; lift and focus once mapped."""
        try:
            self.toplevel.update_idletasks()
            self.toplevel.lift()
            self.toplevel.focus_force()
        except tk.TclError:
            pass

    def refresh(self) -> None:
        """Re-read every control from the getters. Writes nothing."""
        if not self.alive():
            return
        for tab in self.tabs.values():
            tab.refresh()

    def close(self) -> None:
        self._release()
        try:
            self.toplevel.destroy()
        except tk.TclError:
            pass

    def _on_destroy(self, event) -> None:
        if event.widget is self.toplevel:
            self._release()

    def _release(self) -> None:
        self._closed = True
        for tab in getattr(self, "tabs", {}).values():
            tab.close()
        if self.window.settings_refresh == self.refresh:
            self.window.settings_refresh = None
        if _instances.get(id(self.root)) is self:
            del _instances[id(self.root)]
