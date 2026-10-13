"""The Qt Settings window's Stream Dock tab: install state, install/uninstall,
presets and per-account visibility. The counterpart of settings_streamdock.

Install and uninstall copy or delete a plugin folder, so they run on a worker
thread and publish into a Mailbox that a QtPoller drains on the GUI thread.
The running process does not change on either: see streamdock/gui_state.py
for the restart states.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QWidget

from tokitty.qt.kit import clear_layout, hbox, text, vbox
from tokitty.qt.poller import QtPoller
from tokitty.qt.settings import _Tab
from tokitty.settings_style import (
    BAD, DIM_COLOR, FONT_MONO, FONT_SMALL, GOOD, LOG_BG, MUTED,
)
from tokitty.streamdock.gui_state import (
    INSTALLED_STATES, MAX_LOG_LINES, STATE_INFO, STATE_WATCH_MS, UNSUPPORTED_NOTE,
)
from tokitty.worker_mailbox import Mailbox, run_worker


class StreamDockTab(_Tab):
    def build(self) -> None:
        kit = self.kit
        kit.page_header(self.frame, "Stream Dock", "Session controls beside your Tokitty panes.")
        card = self.card()
        top = QWidget()
        top_row = hbox(top)
        top_row.setContentsMargins(13, 9, 13, 9)
        self.pill = kit.pill("", "neutral")
        top_row.addWidget(self.pill)
        top_row.addStretch(1)
        self.actions = QWidget()
        hbox(self.actions)
        top_row.addWidget(self.actions)
        card.layout().addWidget(top)
        self.detail = text("", color=MUTED, font=FONT_SMALL)
        self.detail.setWordWrap(True)
        self.detail.setContentsMargins(13, 0, 13, 7)
        card.layout().addWidget(self.detail)
        self.note = text("", color=DIM_COLOR, font=FONT_SMALL)
        self.note.setWordWrap(True)
        self.note.setContentsMargins(13, 0, 13, 7)
        card.layout().addWidget(self.note)
        self.presets_holder = QWidget()
        presets_col = vbox(self.presets_holder)
        presets_col.setContentsMargins(0, 7, 0, 0)
        self.add(self.presets_holder)
        self.sources_header = kit.section_header(self.frame, "Show sessions from", top=10)
        self.sources = kit.surface()
        self.add(self.sources)
        kit.section_header(self.frame, "Installer log", top=6)
        self.log_frame = QFrame()
        self.log_frame.setObjectName("streamdockLog")
        self.log_frame.setStyleSheet(f"QFrame#streamdockLog {{ background: {LOG_BG}; }}")
        vbox(self.log_frame).setContentsMargins(11, 4, 11, 4)
        self.add(self.log_frame)

        self.mailbox = Mailbox()
        self.poller = QtPoller(self.owner, self.mailbox, self._on_result, lambda: self._busy)
        self._busy = False
        self._confirming = False
        self._source_keys: Optional[Tuple[str, ...]] = None
        self.toggles: List = []
        self.log: List[Tuple[str, str, str]] = []  # (clock, line, color)
        self.install_button = None
        self.uninstall_button = None
        self.confirm_button = None
        self.presets_button = None
        self._painted: Optional[Tuple[str, str]] = None
        self._paint_log()
        self._watcher = QTimer(self.owner)
        self._watcher.timeout.connect(self._watch)
        self._watcher.start(STATE_WATCH_MS)

    # -- state ---------------------------------------------------------------

    def state(self) -> str:
        getter = self.host.streamdock_state
        state = getter() if getter is not None else "not_installed"
        return state if state in STATE_INFO else "not_installed"

    def supported(self) -> bool:
        probe = self.host.streamdock_install_supported
        return bool(probe()) if probe is not None else False

    def close(self) -> None:
        self.poller.cancel()
        self._watcher.stop()

    def _watch(self) -> None:
        """Repaint when the state or its detail changed since the last paint."""
        if self._painted is not None and self._painted != self._status():
            self.refresh()

    def _status(self) -> Tuple[str, str]:
        state = self.state()
        info = self.host.streamdock_info
        detail = STATE_INFO[state][2].format(**(info() if info is not None else {"port": "?", "reason": ""}))
        return state, detail

    def refresh(self) -> None:
        state, detail = self._painted = self._status()
        label, kind, _ = STATE_INFO[state]
        self.kit.set_pill(self.pill, label, kind)
        self.detail.setText(detail)
        supported = self.supported()
        self.note.setText("" if supported else UNSUPPORTED_NOTE)
        self.note.setVisible(not supported)
        self._paint_actions(state, supported)
        self._paint_presets(state)
        self._paint_sources(state)

    def _paint_actions(self, state: str, supported: bool) -> None:
        kit, host = self.kit, self.host
        layout = self.actions.layout()
        clear_layout(layout)
        self.install_button = self.uninstall_button = self.confirm_button = None
        installed = state in INSTALLED_STATES
        if self._confirming and installed:
            layout.addWidget(text("Remove the plugin?", color=MUTED, font=FONT_SMALL))
            layout.addSpacing(8)
            self.confirm_button = kit.button("Remove", "danger", self._uninstall, compact=True,
                                             disabled=self._busy)
            layout.addWidget(self.confirm_button)
            layout.addSpacing(4)
            layout.addWidget(kit.button("Cancel", "quiet", self._cancel_confirm, compact=True,
                                        disabled=self._busy))
            return
        if installed:
            self.uninstall_button = kit.button(
                "Uninstall", "danger", self._ask_uninstall, compact=True,
                disabled=self._busy or not supported or host.streamdock_uninstall is None)
            layout.addWidget(self.uninstall_button)
        else:
            # not_installed, or removed this session (restart before reinstalling).
            self.install_button = kit.button(
                "Installing…" if self._busy else "Install", "primary", self._install, compact=True,
                disabled=(self._busy or not supported or state == "restart_to_finish_removal"
                          or host.streamdock_install is None))
            layout.addWidget(self.install_button)

    def _paint_presets(self, state: str) -> None:
        layout = self.presets_holder.layout()
        clear_layout(layout)
        self.presets_button = None
        if self.host.on_edit_streamdock_presets is None:
            return
        self.presets_button = self.kit.button(
            "New-session presets…", "secondary", self.host.on_edit_streamdock_presets,
            compact=True, disabled=state == "restart_to_finish_removal")
        holder = QWidget()
        row = hbox(holder)
        row.addWidget(self.presets_button)
        row.addStretch(1)
        layout.addWidget(holder)

    def _paint_sources(self, state: str) -> None:
        getter = self.host.streamdock_account_toggles
        entries = list(getter()) if getter is not None else []
        keys = tuple(label for label, _shown, _toggle in entries)
        if keys != self._source_keys:
            layout = self.sources.layout()
            clear_layout(layout)
            self.toggles = []
            for label, _shown, toggle in entries:
                widget = self.kit.toggle(
                    label, compact=True, on_change=lambda _v, t=toggle: self.write(t))
                widget.setContentsMargins(12, 3, 12, 3)
                layout.addWidget(widget)
                self.toggles.append(widget)
            self._source_keys = keys
            if not entries:
                empty = text("No accounts to show.", color=MUTED, font=FONT_SMALL)
                empty.setContentsMargins(12, 8, 12, 8)
                layout.addWidget(empty)
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
        layout = self.log_frame.layout()
        clear_layout(layout)
        if not self.log:
            layout.addWidget(text("No installer activity yet.", color=DIM_COLOR, font=FONT_MONO))
        for clock, line, color in self.log:
            label = text(f"[{clock}] {line}", color=color, font=FONT_MONO)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            layout.addWidget(label)

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
        self._start("install", self.host.streamdock_install, "Installing the Stream Dock plugin…")

    def _ask_uninstall(self) -> None:
        self._confirming = True
        self.refresh()

    def _cancel_confirm(self) -> None:
        self._confirming = False
        self.refresh()

    def _uninstall(self) -> None:
        self._start("uninstall", self.host.streamdock_uninstall, "Removing the Stream Dock plugin…")

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
