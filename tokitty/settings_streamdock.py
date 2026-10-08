"""The Settings window's Stream Dock tab: install state, install/uninstall,
presets and per-account visibility.

Install and uninstall copy or delete a plugin folder, so they run on a worker
thread and publish into a Mailbox that a Tk-thread Poller drains (see
settings_async). The running process does not change on either: see
streamdock/gui_state.py for the restart states.
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import List, Optional, Tuple

from tokitty.settings_async import Mailbox, Poller, run_worker
from tokitty.settings_ui import _Tab
from tokitty.settings_widgets import (
    BAD, DIM_COLOR, FONT_MONO, FONT_SMALL, GOOD, MUTED, SURFACE, text,
)

LOG_BG = "#15151a"
MAX_LOG_LINES = 8

# state -> (pill label, pill kind, detail line)
STATE_INFO = {
    "not_installed": ("Not installed", "neutral",
                      "Install the plugin to put your sessions on a Stream Dock."),
    "not_connected": ("Installed, not connected", "warn",
                      "Start VSD Craft and check that the Tokitty plugin is loaded."),
    "connected": ("Connected", "good", "Stream Dock is ready to show live sessions."),
    "restart_to_connect": ("Restart tokitty to finish", "warn",
                           "Plugin installed. Restart tokitty, then restart VSD Craft."),
    "restart_to_finish_removal": ("Restart tokitty to finish", "warn",
                                  "Plugin removed. Restart tokitty to stop its Stream Dock connection."),
    # Details with {port} and {reason} are formatted from window.streamdock_info().
    "starting": ("Starting", "warn",
                 "Waiting for port {port}. An older copy of tokitty may still be closing."),
    "failed": ("Couldn't start", "bad",
               "Couldn't listen on port {port}: {reason}. Restart tokitty to try again."),
}
UNSUPPORTED_NOTE = "Installing the Stream Dock plugin needs Windows and VSD Craft."


class StreamDockTab(_Tab):
    def build(self) -> None:
        kit = self.kit
        px = kit.px
        kit.page_header(self.frame, "Stream Dock", "Session controls beside your Tokitty panes.")
        card = tk.Frame(self.frame, bg=SURFACE)
        card.pack(fill="x")
        top = tk.Frame(card, bg=SURFACE)
        top.pack(fill="x", padx=px(13), pady=(px(9), px(9)))
        self.pill = kit.pill(top, "", "neutral")
        self.pill.pack(side="left")
        self.actions = tk.Frame(top, bg=SURFACE)
        self.actions.pack(side="right")
        self.detail = text(card, "", bg=SURFACE, color=MUTED, font=FONT_SMALL,
                           wraplength=px(430), justify="left")
        self.detail.pack(anchor="w", padx=px(13), pady=(0, px(7)))
        self.note = text(card, "", bg=SURFACE, color=DIM_COLOR, font=FONT_SMALL,
                         wraplength=px(430), justify="left")
        self.presets_holder = tk.Frame(self.frame, bg=self.frame.cget("bg"))
        self.presets_holder.pack(anchor="w", pady=(px(7), 0))
        self.sources_header = kit.section_header(self.frame, "Show sessions from", top=10)
        self.sources = tk.Frame(self.frame, bg=SURFACE)
        self.sources.pack(fill="x")
        kit.section_header(self.frame, "Installer log", top=6)
        self.log_frame = tk.Frame(self.frame, bg=LOG_BG)
        self.log_frame.pack(fill="x")

        self.mailbox = Mailbox()
        self.poller = Poller(self.owner.toplevel, self.mailbox, self._on_result,
                             lambda: self._busy)
        self._busy = False
        self._confirming = False
        self._source_keys: Optional[Tuple[str, ...]] = None
        self.toggles: List = []
        self.log: List[Tuple[str, str, str]] = []  # (clock, line, color)
        self.install_button: Optional[tk.Button] = None
        self.uninstall_button: Optional[tk.Button] = None
        self.presets_button: Optional[tk.Button] = None
        self._paint_log()

    # -- state ---------------------------------------------------------------

    def state(self) -> str:
        getter = self.win.streamdock_state
        state = getter() if getter is not None else "not_installed"
        return state if state in STATE_INFO else "not_installed"

    def supported(self) -> bool:
        probe = self.win.streamdock_install_supported
        return bool(probe()) if probe is not None else False

    def close(self) -> None:
        self.poller.cancel()

    def refresh(self) -> None:
        state = self.state()
        label, kind, detail = STATE_INFO[state]
        info = self.win.streamdock_info
        detail = detail.format(**(info() if info is not None else {"port": "?", "reason": ""}))
        self.kit.set_pill(self.pill, label, kind)
        self.detail.configure(text=detail)
        supported = self.supported()
        self.note.configure(text="" if supported else UNSUPPORTED_NOTE)
        if supported:
            self.note.pack_forget()
        elif not self.note.winfo_manager():
            self.note.pack(anchor="w", padx=self.kit.px(13), pady=(0, self.kit.px(7)))
        self._paint_actions(state, supported)
        self._paint_presets(state)
        self._paint_sources(state)

    def _paint_actions(self, state: str, supported: bool) -> None:
        kit = self.kit
        for child in self.actions.winfo_children():
            child.destroy()
        self.install_button = self.uninstall_button = None
        installed = state in ("not_connected", "connected", "restart_to_connect", "starting", "failed")
        if self._confirming and installed:
            text(self.actions, "Remove the plugin?", bg=SURFACE, color=MUTED,
                 font=FONT_SMALL).pack(side="left", padx=(0, kit.px(8)))
            self.confirm_button = kit.button(self.actions, "Remove", "danger", compact=True,
                                             command=self._uninstall, disabled=self._busy)
            self.confirm_button.pack(side="left", padx=(0, kit.px(4)))
            kit.button(self.actions, "Cancel", "quiet", compact=True,
                       command=self._cancel_confirm, disabled=self._busy).pack(side="left")
            return
        if installed:
            self.uninstall_button = kit.button(
                self.actions, "Uninstall", "danger", compact=True, command=self._ask_uninstall,
                disabled=self._busy or not supported or self.win.streamdock_uninstall is None)
            self.uninstall_button.pack()
        else:
            # not_installed, or removed this session (restart before reinstalling).
            self.install_button = kit.button(
                self.actions, "Installing…" if self._busy else "Install", "primary", compact=True,
                command=self._install,
                disabled=(self._busy or not supported or state == "restart_to_finish_removal"
                          or self.win.streamdock_install is None))
            self.install_button.pack()

    def _paint_presets(self, state: str) -> None:
        for child in self.presets_holder.winfo_children():
            child.destroy()
        self.presets_button = None
        if self.win.on_edit_streamdock_presets is None:
            return
        self.presets_button = self.kit.button(
            self.presets_holder, "New-session presets…", "secondary", compact=True,
            command=self.win.on_edit_streamdock_presets,
            disabled=state == "restart_to_finish_removal")
        self.presets_button.pack()

    def _paint_sources(self, state: str) -> None:
        getter = self.win.streamdock_account_toggles
        entries = list(getter()) if getter is not None else []
        keys = tuple(label for label, _shown, _toggle in entries)
        px = self.kit.px
        if keys != self._source_keys:
            for child in self.sources.winfo_children():
                child.destroy()
            self.toggles = []
            for label, _shown, toggle in entries:
                widget = self.kit.toggle(
                    self.sources, label, compact=True,
                    on_change=lambda _v, t=toggle: self.write(t))
                widget.frame.pack(fill="x", padx=px(12), pady=px(3))
                self.toggles.append(widget)
            self._source_keys = keys
            if not entries:
                text(self.sources, "No accounts to show.", bg=SURFACE, color=MUTED,
                     font=FONT_SMALL).pack(anchor="w", padx=px(12), pady=px(8))
        enabled = state != "restart_to_finish_removal"
        for widget, (_label, shown, _toggle) in zip(self.toggles, entries):
            widget.set(shown())
            widget.set_enabled(enabled)

    # -- install and uninstall ---------------------------------------------

    def _log(self, line: str, color: str = MUTED) -> None:
        self.log.append((time.strftime("%H:%M:%S"), line, color))
        del self.log[:-MAX_LOG_LINES]
        self._paint_log()

    def _paint_log(self) -> None:
        for child in self.log_frame.winfo_children():
            child.destroy()
        px = self.kit.px
        if not self.log:
            text(self.log_frame, "No installer activity yet.", bg=LOG_BG, color=DIM_COLOR,
                 font=FONT_MONO).pack(anchor="w", padx=px(11), pady=px(4))
        for clock, line, color in self.log:
            text(self.log_frame, f"[{clock}] {line}", bg=LOG_BG, color=color, font=FONT_MONO,
                 wraplength=px(450), justify="left").pack(anchor="w", padx=px(11))

    def _start(self, tag: str, fn, message: str) -> None:
        if self._busy or fn is None:
            return
        self._busy = True
        self._confirming = False
        self._log(message, DIM_COLOR)
        self.refresh()
        run_worker(self.mailbox, tag, fn)
        self.poller.start()

    def _install(self) -> None:
        self._start("install", self.win.streamdock_install, "Installing the Stream Dock plugin…")

    def _ask_uninstall(self) -> None:
        self._confirming = True
        self.refresh()

    def _cancel_confirm(self) -> None:
        self._confirming = False
        self.refresh()

    def _uninstall(self) -> None:
        self._start("uninstall", self.win.streamdock_uninstall, "Removing the Stream Dock plugin…")

    def _on_result(self, tag: str, ok: bool, value) -> None:
        self._busy = False
        if not ok:
            self._log(f"{tag.capitalize()} failed: {value}", BAD)
        else:
            success, lines = value
            for line in lines:
                self._log(line, MUTED)
            self._log(f"{tag.capitalize()} finished." if success else f"{tag.capitalize()} did not finish.",
                      GOOD if success else BAD)
        self.refresh()
