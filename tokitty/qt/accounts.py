"""The Qt accounts manager: add, rename and remove accounts without
hand-editing accounts.json. The counterpart of accounts_ui.

Kept parallel to accounts_ui (same class and method names where the method
still makes sense) over the shared pure functions in accounts_logic. One
manager per process: AccountsManager.open() raises the existing window.

Threading rule: a worker only calls its on_done, and on_done only
calls hook_guard.complete_operation or writes plain data under a lock. Result
polls run on single-shot QTimers on the GUI thread, and the in-flight state
of a mutation lives in hook_guard (module level), so closing the manager and
reopening it picks the operation back up.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QInputDialog, QMessageBox, QPushButton, QWidget,
)

from tokitty.qt import macos
from tokitty.accounts import (
    DEFAULT_PROVIDER,
    Account,
    AccountsLoadResult,
    assign_identity_slug,
    backfill_identity_history,
    canonicalize_locator,
    load_accounts_result,
    load_identity_history,
    save_identity_history,
)
from tokitty.accounts_logic import (
    CODEX_KIND, _needs_off_thread_validation, _run_mutation_off_thread,
    _run_retry_off_thread, _run_validation_off_thread, _same_dir, build_discovered_path_specs,
    build_row_specs, codex_hook_text, describe_capabilities, describe_codex_capabilities,
    discover_local_codex_home, local_capabilities, local_codex_facts, reconcile_before_save,
    validate_account_path,
)
from tokitty.customize import (
    Customization,
    SINGLE_KEY,
    load_customization,
    rename_account,
    save_customization_entry,
)
from tokitty.hook_guard import (
    InFlightOperation as _InFlightOperation,
    claim_operation as _claim_operation,
    complete_operation as _complete_operation,
)
from tokitty.manual_path import PathValidationResult
from tokitty.migration import absorb_implicit_default
from tokitty.providers import UnknownProviderError, get_provider, supported_kinds
from tokitty.qt.kit import Kit, clear_layout, hbox, text, vbox
from tokitty.randomize import random_look
from tokitty.settings_style import DIM_COLOR, FONT_BODY, FONT_MEDIUM, FONT_SMALL
from tokitty.usage_items import USAGE_READOUT_ITEMS, USAGE_WINDOW_ITEMS, VIEW_MODE_ITEMS
from tokitty import sprites

_instance: Optional["AccountsManager"] = None

# How often the GUI-thread polls check whether a background operation has
# finished. Short, since each only gates a one-shot refresh.
_RETRY_POLL_MS = 100
_MUTATION_POLL_MS = 100
_VALIDATION_POLL_MS = 100

_WIDTH = 580


def current() -> Optional["AccountsManager"]:
    """The open manager, or None. The Accounts tab uses it to find the window
    a host opener returned nothing for."""
    manager = _instance
    return manager if manager is not None and not manager._closed else None


def capability_text(config_dir: str, provider: str, state_dir: Path) -> Optional[str]:
    """The facts line under an account row, or None when it cannot be told
    cheaply from here."""
    if provider == CODEX_KIND:
        facts = local_codex_facts(config_dir)
        describe = describe_codex_capabilities
    elif provider == DEFAULT_PROVIDER:
        facts = local_capabilities(config_dir)
        describe = describe_capabilities
    else:
        facts = None
    if facts is None:
        return None
    first, local = describe(*facts)
    line = f"{first} · {local}"
    if provider == CODEX_KIND:
        hooks = codex_hook_text(config_dir, state_dir)
        if hooks:
            line = f"{line} · {hooks}"
    if provider != DEFAULT_PROVIDER:
        line = f"{get_provider(provider).display_name} · {line}"
    return line


class AccountsManager(QWidget):
    """Singleton top-level window: AccountsManager.open() raises the existing
    one instead of creating a second."""

    # Set while a Codex WSL path is being checked off the GUI thread. A class
    # default so managers built without __init__ in tests still read it.
    _validation_in_flight = False

    @classmethod
    def open(
        cls,
        state_dir: Path,
        discovered_matches: Optional[Sequence[Tuple[str, str]]] = None,
        *,
        host,
        focus_usage: bool = False,
    ) -> "AccountsManager":
        global _instance
        existing = current()
        if existing is not None and existing.host is host and existing.state_dir == state_dir:
            if discovered_matches is not None:
                existing._discovered_paths = build_discovered_path_specs(discovered_matches)
                existing._refresh_rows()
            if focus_usage:
                existing.focus_usage_section()
            existing.raise_window()
            return existing
        if existing is not None:
            existing.close()
        manager = _instance = cls(state_dir, discovered_matches, host)
        if focus_usage:
            manager.focus_usage_section()
        manager.raise_window()
        return manager

    def __init__(
        self,
        state_dir: Path,
        discovered_matches: Optional[Sequence[Tuple[str, str]]],
        host,
    ):
        super().__init__()
        self.state_dir = state_dir
        self.host = host
        self.kit = Kit()
        # Callbacks fired once when the window closes; the Accounts tab
        # rebuilds its rows from one.
        self.closed: List[Callable[[], None]] = []
        self._closed = False
        self._pending_hook_failure = False
        self._mutation_controls: List[Tuple[QPushButton, bool]] = []
        self._discovered_paths = build_discovered_path_specs(discovered_matches or [])
        initial_accounts = load_accounts_result(state_dir)
        if initial_accounts.state != "malformed":
            backfill_identity_history(state_dir, initial_accounts.accounts)
        self.kit.style_window(self)
        self.setWindowTitle("Accounts")
        self.setFixedWidth(_WIDTH)
        # Parentless, so it follows the widget's Always in front itself.
        host.register_window(self)
        self._retry_timer = self._single_shot(self._poll_retry_done)
        self._mutation_timer = self._single_shot(self._poll_mutation_done)
        self._validation_timer = self._single_shot(self._poll_validation_done)
        state_key, operation, claimed = _claim_operation(state_dir, "retry")
        self._state_key = state_key
        self._mutation_in_flight = operation.kind == "mutation"
        self._retry_in_flight = operation.kind == "retry"
        self._build()
        # Best-effort, silent unless it matters: retries a hook install or
        # uninstall left incomplete by a prior crash, one more chance to
        # complete whenever the user opens the manager. Off the GUI thread,
        # so a stuck wsl.exe call or a slow filesystem cannot freeze the UI;
        # _build() above runs first with whatever is already on disk.
        # _run_retry_off_thread starts no thread of its own, so it gets one
        # here. The thread only ever publishes through hook_guard under its
        # lock; the QTimer poll below picks the result up on the GUI thread.
        if claimed:
            def on_done(outcome):
                _complete_operation(state_key, operation, outcome)

            try:
                threading.Thread(
                    target=_run_retry_off_thread,
                    args=(self.state_dir, on_done),
                    daemon=True,
                ).start()
            except Exception as exc:
                _complete_operation(state_key, operation, exc)
        self._watch_operation(operation)

    def _single_shot(self, slot: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(slot)
        return timer

    # ---- operations in flight ------------------------------------------

    def _watch_operation(self, operation: _InFlightOperation) -> None:
        """Poll an operation, including one started by an old window."""
        self._mutation_in_flight = operation.kind == "mutation"
        self._retry_in_flight = operation.kind == "retry"
        if operation.kind == "mutation":
            self._mutation_operation = operation
            self._mutation_lock = operation.lock
            self._mutation_state = operation.state
            if not self._mutation_timer.isActive():
                self._mutation_timer.start(_MUTATION_POLL_MS)
        else:
            self._retry_lock = operation.lock
            self._retry_state = operation.state
            if not self._retry_timer.isActive():
                self._retry_timer.start(_RETRY_POLL_MS)
        self._update_mutation_controls()

    def _poll_retry_done(self) -> None:
        if self._closed:
            return  # closed before the retry finished
        with self._retry_lock:
            done = self._retry_state["done"]
        if done:
            with self._retry_lock:
                outcome = self._retry_state.get("outcome")
            self._retry_in_flight = False
            self._pending_hook_failure = (
                isinstance(outcome, Exception)
                or (outcome is not None and not outcome.ok)
            )
            self._refresh_rows()
            if self._pending_hook_failure:
                detail = str(outcome) if isinstance(outcome, Exception) else outcome.message
                QMessageBox.critical(
                    self,
                    "Accounts",
                    "A pending hook update could not be completed. "
                    f"Reopen Accounts to retry it before making another change. {detail}",
                )
            elif outcome is not None and getattr(outcome, "warning", None):
                QMessageBox.warning(self, "Accounts", outcome.warning)
        else:
            self._retry_timer.start(_RETRY_POLL_MS)

    def _poll_mutation_done(self) -> None:
        if self._closed:
            return
        with self._mutation_lock:
            done = self._mutation_state["done"]
            outcome = self._mutation_state.get("outcome")
        if done:
            self._finish_mutation(outcome)
        else:
            self._mutation_timer.start(_MUTATION_POLL_MS)

    def _busy(self) -> bool:
        return bool(
            self._retry_in_flight
            or self._mutation_in_flight
            or self._validation_in_flight
            or self._pending_hook_failure
        )

    # ---- window ---------------------------------------------------------

    def raise_window(self) -> None:
        """Bring it up in front: show, lift and ask for focus. The stay-on-top
        hint (host.register_window) is what keeps it above a topmost widget."""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self._fit()
        self.show()
        macos.activate_app()
        self.raise_()
        self.activateWindow()

    def focus_usage_section(self) -> None:
        """Bring the usage controls into view.

        Called on the ACTION_USAGE_SETUP first run: the user has transcripts
        and no credentials, so the account rows are not the part of this
        window they need.
        """
        entry = getattr(self, "_budget_entry", None)
        if entry is not None:
            entry.setFocus()

    def _on_close(self) -> None:
        self.close()

    def closeEvent(self, event) -> None:
        self._release()
        super().closeEvent(event)

    def _release(self) -> None:
        global _instance
        if self._closed:
            return
        self._closed = True
        for timer in (self._retry_timer, self._mutation_timer, self._validation_timer):
            timer.stop()
        self.host.forget_window(self)
        if _instance is self:
            _instance = None
        for callback in list(self.closed):
            callback()

    def _fit(self) -> None:
        """Rows come and go and labels wrap, so the height follows the content
        at the fixed width."""
        self.setFixedHeight(self.layout().totalHeightForWidth(_WIDTH))

    # ---- build ----------------------------------------------------------

    def _build(self) -> None:
        kit = self.kit
        column = vbox(self)
        column.setContentsMargins(20, 18, 20, 16)
        kit.page_header(self, "Accounts", "Add, rename or remove the profiles tokitty watches.")
        self.rows_box = QWidget()
        vbox(self.rows_box, spacing=3)
        column.addWidget(self.rows_box)
        self._build_usage_section()
        notice = text(
            "Tokitty restart needed for new panes. Claude Code session restart needed for hooks.",
            color=DIM_COLOR, font=FONT_SMALL)
        notice.setWordWrap(True)
        notice.setContentsMargins(0, 12, 0, 10)
        self.restart_notice = notice
        column.addWidget(notice)
        # Add is hidden on the virtual-row path (see _refresh_rows): there is
        # nothing to add to on macOS when the only account is the Keychain.
        self._build_harness_picker()
        self._refresh_rows()

    def _build_harness_picker(self) -> None:
        """Which harness "Add by path…" adds. Claude Code is preselected, so
        the old flow is still one click."""
        self.add_box = QWidget()
        row = hbox(self.add_box, spacing=10)
        row.addWidget(text("Harness", font=FONT_MEDIUM))
        self.provider_picker = self.kit.segmented(
            [(kind, get_provider(kind).display_name) for kind in supported_kinds()], compact=True)
        self.provider_picker.set(DEFAULT_PROVIDER)
        row.addWidget(self.provider_picker)
        row.addStretch(1)
        self.add_button = self.kit.button("Add by path…", "primary", self._on_add, compact=True)
        row.addWidget(self.add_button)
        self.layout().addWidget(self.add_box)

    def _selected_add_provider(self) -> str:
        picker = getattr(self, "provider_picker", None)
        return picker.get() if picker is not None else DEFAULT_PROVIDER

    def _build_usage_section(self) -> None:
        """The same three global controls the right-click menu carries, plus
        a budget, so a user who never opens a menu still finds them.

        Writes through the host seams the menu and the Settings window use:
        no second code path and no second source of truth.
        """
        kit, host = self.kit, self.host
        self.usage_section = QWidget()
        section = vbox(self.usage_section)
        kit.section_header(self.usage_section, "Usage", top=14)
        self.usage_groups: Dict[str, tuple] = {}
        for key, title, items, getter, setter in (
            ("view", "Show", VIEW_MODE_ITEMS, host.view_mode, host.on_view_mode),
            ("window", "Window", USAGE_WINDOW_ITEMS, host.usage_window, host.on_usage_window),
            ("readout", "Readout", USAGE_READOUT_ITEMS, host.usage_readout, host.on_usage_readout),
        ):
            if getter is None or setter is None:
                continue
            row = QWidget()
            layout = hbox(row, spacing=10)
            caption = text(title, color=DIM_COLOR, font=FONT_BODY)
            caption.setFixedWidth(60)
            layout.addWidget(caption)
            control = kit.segmented(items, on_change=lambda v, s=setter: self._write_usage(s, v),
                                    compact=True)
            layout.addWidget(control)
            layout.addStretch(1)
            section.addWidget(row)
            section.addSpacing(5)
            self.usage_groups[key] = (control, getter)

        self._has_budget = host.budget_for_pane is not None and host.set_budget_for_pane is not None
        if self._has_budget:
            row = QWidget()
            layout = hbox(row, spacing=6)
            caption = text("Budget", color=DIM_COLOR, font=FONT_BODY)
            caption.setFixedWidth(60)
            layout.addWidget(caption)
            layout.addWidget(text("$", color=DIM_COLOR))
            self._budget_holder, self._budget_entry = kit.entry(width=10)
            self._budget_entry.returnPressed.connect(self._apply_budget)
            layout.addWidget(self._budget_holder)
            layout.addWidget(kit.button("Set", "secondary", self._apply_budget, compact=True))
            layout.addWidget(text("(blank clears)", color=DIM_COLOR, font=FONT_SMALL))
            layout.addStretch(1)
            section.addWidget(row)
        note = text(
            "Per-model usage is read from Claude Code's own transcripts, so it works without a subscription.",
            color=DIM_COLOR, font=FONT_SMALL)
        note.setWordWrap(True)
        note.setContentsMargins(0, 6, 0, 0)
        section.addWidget(note)
        self.layout().addWidget(self.usage_section)
        self._refresh_usage()

    def _refresh_usage(self) -> None:
        """Re-read the usage controls. Writes nothing."""
        for control, getter in self.usage_groups.values():
            control.set(getter())
        if self._has_budget:
            current_budget = self.host.budget_for_pane(0)
            self._budget_entry.setText("" if current_budget is None else f"{current_budget:g}")

    def _write_usage(self, setter: Callable[[str], None], value: str) -> None:
        setter(value)
        self.host.notify_state_changed()

    def _apply_budget(self) -> None:
        # Blank clears. That has to be expressible, which is why this is an
        # entry rather than a numeric prompt that cannot tell a cancel from a
        # submitted blank. The seam parses and validates, as the Settings
        # window's budget entry does; the first pane's budget is the one
        # this section has always edited.
        error = self.host.set_budget_for_pane(0, self._budget_entry.text())
        if error is not None:
            QMessageBox.critical(self, "Budget", error)
            return
        self.host.notify_state_changed()

    # ---- rows -----------------------------------------------------------

    def _showing_virtual_macos_row(self) -> bool:
        return (
            load_accounts_result(self.state_dir).state == "absent"
            and sys.platform == "darwin"
            and not self._has_local_credentials_file()
        )

    def _has_local_credentials_file(self) -> bool:
        return (Path.home() / ".claude" / ".credentials.json").is_file()

    def _new_row(self) -> Tuple[QFrame, QWidget]:
        """A raised strip in the rows box. Returns it and a vertical column
        for its text, with the strip's buttons added after the column."""
        frame = self.kit.surface(rows=False)
        layout = hbox(frame, spacing=6)
        layout.setContentsMargins(11, 8, 8, 8)
        info = QWidget()
        vbox(info, spacing=2)
        layout.addWidget(info, 1)
        self.rows_box.layout().addWidget(frame)
        return frame, info

    def _mutation_button(self, frame: QFrame, title: str, style: str, command,
                         enabled: bool = True) -> QPushButton:
        button = self.kit.button(title, style, command, compact=True)
        frame.layout().addWidget(button)
        self._mutation_controls.append((button, enabled))
        return button

    def _refresh_rows(self) -> None:
        clear_layout(self.rows_box.layout())
        self._mutation_controls = []
        virtual = self._showing_virtual_macos_row()
        self.add_box.setVisible(not virtual)
        if virtual:
            self._render_virtual_macos_row()
            self._update_mutation_controls()
            self._fit()
            return
        result = load_accounts_result(self.state_dir)
        if result.state == "malformed":
            self._render_malformed_row()
            self._update_mutation_controls()
            self._fit()
            return
        accounts = result.accounts
        store = load_customization(self.state_dir)
        for row in build_row_specs(accounts, store):
            frame, info = self._new_row()
            info.layout().addWidget(text(row.display_label, font=FONT_MEDIUM))
            self._render_capability_facts(info, row.config_dir, row.provider)
            frame.layout().addWidget(self.kit.button(
                "Rename…", "secondary", lambda s=row.slug: self._on_rename(s), compact=True))
            self._mutation_button(
                frame, "Remove", "danger",
                lambda s=row.slug, c=row.config_dir, p=row.provider: self._on_remove(s, c, p),
                enabled=row.remove_enabled)
        self._render_discovered_rows()
        self._render_discovered_codex_row(accounts)
        self._update_mutation_controls()
        self._fit()

    def _found_row(self, label: str, command) -> None:
        frame, info = self._new_row()
        found = text(label, font=FONT_BODY)
        found.setWordWrap(True)
        info.layout().addWidget(found)
        self._mutation_button(frame, "Add", "secondary", command)

    def _render_discovered_rows(self) -> None:
        for discovered in self._discovered_paths:
            self._found_row(
                f"Found in {discovered.distro}: {discovered.config_dir}",
                lambda path=discovered.config_dir: self._on_add_discovered(path))

    def _render_discovered_codex_row(self, accounts: Sequence[Account]) -> None:
        home = discover_local_codex_home(accounts)
        if home is None:
            return
        self._found_row(
            f"Found {get_provider(CODEX_KIND).display_name}: {home}",
            lambda path=home: self._on_add_discovered(path, provider=CODEX_KIND))

    def _render_capability_facts(self, info: QWidget, config_dir: str,
                                 provider: str = DEFAULT_PROVIDER) -> None:
        facts = capability_text(config_dir, provider, self.state_dir)
        if facts is None:
            return
        label = text(facts, color=DIM_COLOR, font=FONT_SMALL)
        label.setWordWrap(True)
        info.layout().addWidget(label)

    def _render_malformed_row(self) -> None:
        _frame, info = self._new_row()
        message = text("accounts.json is malformed. Fix or delete it before managing accounts.",
                       font=FONT_BODY)
        message.setWordWrap(True)
        info.layout().addWidget(message)

    def _render_virtual_macos_row(self) -> None:
        frame, info = self._new_row()
        info.layout().addWidget(text("Default macOS account (Keychain)", font=FONT_MEDIUM))
        frame.layout().addWidget(self.kit.button(
            "Rename…", "secondary", self._on_rename_default_key, compact=True))
        frame.layout().addWidget(self.kit.button("Remove", "danger", compact=True, disabled=True))

    def _update_mutation_controls(self) -> None:
        busy = self._busy()
        for button, enabled in self._mutation_controls:
            self._set_enabled(button, enabled and not busy)
        add = getattr(self, "add_button", None)
        if add is not None:
            self._set_enabled(add, not busy)

    @staticmethod
    def _set_enabled(button: QPushButton, enabled: bool) -> None:
        button.setEnabled(enabled)
        button.setCursor(Qt.CursorShape.PointingHandCursor if enabled
                         else Qt.CursorShape.ArrowCursor)

    # ---- mutations ------------------------------------------------------

    def _load_accounts_for_mutation(self) -> Optional[AccountsLoadResult]:
        result = load_accounts_result(self.state_dir)
        if result.state == "malformed":
            QMessageBox.critical(
                self, "Accounts",
                "accounts.json is malformed. Fix or delete it before adding or removing accounts.")
            return None
        backfill_identity_history(self.state_dir, result.accounts)
        return result

    def _finish_mutation(self, outcome) -> None:
        operation = getattr(self, "_mutation_operation", None)
        if operation is not None:
            _complete_operation(self._state_key, operation, outcome)
        failed = isinstance(outcome, Exception) or not outcome.ok
        self._mutation_in_flight = False
        self._pending_hook_failure = failed
        self._refresh_rows()
        if isinstance(outcome, Exception):
            QMessageBox.critical(
                self, "Accounts",
                "The hook update failed. Reopen Accounts to retry it before "
                f"making another change. {outcome}")
        elif not outcome.ok:
            QMessageBox.critical(
                self, "Accounts",
                "The hook update failed. Reopen Accounts to retry it before "
                f"making another change. {outcome.message}")
        elif getattr(outcome, "warning", None):
            # One box, not two: the note (such as "waiting for approval in
            # Codex") rides along with the warning.
            body = outcome.warning
            if getattr(outcome, "note", None):
                body = f"{body}\n\n{outcome.note}"
            QMessageBox.warning(self, "Accounts", body)
        elif getattr(outcome, "note", None):
            QMessageBox.information(self, "Accounts", outcome.note)

    def _start_mutation(
        self, accounts: List[Account], op: str, config_dir: str,
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        if self._mutation_in_flight or self._retry_in_flight:
            return
        state_key, operation, claimed = _claim_operation(self.state_dir, "mutation")
        self._state_key = state_key
        self._watch_operation(operation)
        if not claimed:
            return

        def on_done(outcome):
            _complete_operation(state_key, operation, outcome)

        try:
            _run_mutation_off_thread(
                self.state_dir, accounts, op, config_dir, on_done, provider=provider
            )
        except Exception as exc:
            _complete_operation(state_key, operation, exc)

    def _ask_text(self, title: str, label: str) -> Optional[str]:
        """The typed text, or None when the dialog was cancelled."""
        value, accepted = QInputDialog.getText(self, title, label)
        return value if accepted else None

    def _on_rename_default_key(self) -> None:
        result = self._ask_text("Rename", "Cat name:")
        if result is None:
            return
        store = load_customization(self.state_dir)
        current_custom = store.get(SINGLE_KEY, Customization())
        save_customization_entry(self.state_dir, SINGLE_KEY, replace(current_custom, label=result))
        self._refresh_rows()

    def _on_rename(self, slug: str) -> None:
        result = self._ask_text("Rename", "Cat name:")
        if result is not None:
            rename_account(self.state_dir, slug, result)
            self._refresh_rows()

    def _on_remove(self, slug: str, config_dir: str, provider: str = DEFAULT_PROVIDER) -> None:
        if self._busy():
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is None:
            return
        accounts = load_result.accounts
        if len(accounts) <= 1:
            return
        # The row may be stale: another window can have removed or replaced
        # this account since it was drawn. Acting on it anyway would run a
        # hook op chosen for whatever the row used to be.
        if not any(
            a.name == slug and a.provider == provider and _same_dir(a.config_dir, config_dir)
            for a in accounts
        ):
            self._refresh_rows()
            return
        remaining = [a for a in accounts if a.name != slug]
        try:
            remaining = reconcile_before_save(self.state_dir, remaining)
        except ValueError as exc:
            QMessageBox.critical(self, "Accounts", str(exc))
            return
        remaining = [a for a in remaining if a.name != slug]

        self._start_mutation(remaining, "remove", config_dir, provider)

    def _on_add(self) -> None:
        if self._busy():
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is None:
            return
        provider = self._selected_add_provider()
        if provider == DEFAULT_PROVIDER:
            prompt = "Claude config directory:"
        else:
            try:
                prompt = f"{get_provider(provider).display_name} home directory:"
            except UnknownProviderError:
                return
        raw = self._ask_text("Add account", prompt)
        if not raw:
            return
        self._add_path(raw, load_result, provider)

    def _on_add_discovered(self, config_dir: str, provider: str = DEFAULT_PROVIDER) -> None:
        if self._busy():
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is not None:
            self._add_path(config_dir, load_result, provider)

    def _add_path(
        self, raw: str, load_result: AccountsLoadResult, provider: str = DEFAULT_PROVIDER
    ) -> None:
        active_dirs = [a.config_dir for a in load_result.accounts]
        if _needs_off_thread_validation(provider, raw):
            self._start_validation(raw, active_dirs, load_result, provider)
            return
        validation = validate_account_path(provider, raw, active_dirs)
        self._finish_add(validation, load_result, provider)

    def _start_validation(
        self, raw: str, active_dirs: List[str], load_result: AccountsLoadResult, provider: str
    ) -> None:
        """Check a path through wsl.exe off the GUI thread, then finish the
        add back on it. Same producer/consumer shape as the mutation: the
        worker only writes the shared state, and only the GUI thread polls."""
        lock = threading.Lock()
        state: Dict[str, object] = {"done": False}

        def on_done(outcome):
            with lock:
                state["outcome"] = outcome
                state["done"] = True

        self._validation = (lock, state, load_result, provider)
        self._validation_in_flight = True
        self._update_mutation_controls()
        try:
            _run_validation_off_thread(provider, raw, active_dirs, on_done)
        except Exception as exc:
            on_done(PathValidationResult(ok=False, error=str(exc)))
        self._validation_timer.start(_VALIDATION_POLL_MS)

    def _poll_validation_done(self) -> None:
        if self._closed:
            return
        lock, state, load_result, provider = self._validation
        with lock:
            done = state["done"]
            outcome = state.get("outcome")
        if not done:
            self._validation_timer.start(_VALIDATION_POLL_MS)
            return
        self._validation_in_flight = False
        self._update_mutation_controls()
        self._finish_add(outcome, load_result, provider)

    def _finish_add(
        self, validation: PathValidationResult, load_result: AccountsLoadResult, provider: str
    ) -> None:
        accounts = load_result.accounts
        if not validation.ok:
            QMessageBox.critical(self, "Add account", validation.error)
            return

        try:
            accounts = reconcile_before_save(self.state_dir, accounts)
        except ValueError as exc:
            QMessageBox.critical(self, "Accounts", str(exc))
            return
        # Validation may have run off the GUI thread, long enough for another
        # window to add the same directory in the meantime.
        if any(_same_dir(a.config_dir, validation.config_dir) for a in accounts):
            QMessageBox.critical(self, "Add account", "This account is already added.")
            return

        history = load_identity_history(self.state_dir)
        locator = canonicalize_locator(validation.config_dir)
        taken = set(history.values()) | {account.name for account in accounts}
        slug, history = assign_identity_slug(locator, taken, history)
        save_identity_history(self.state_dir, history)

        was_implicit_only = load_result.state == "absent"
        new_account = Account(name=slug, config_dir=validation.config_dir, provider=provider)
        new_accounts = accounts + [new_account]

        store = load_customization(self.state_dir)
        if was_implicit_only:
            absorbed = absorb_implicit_default(store, slug)
            if slug in absorbed:
                save_customization_entry(self.state_dir, slug, absorbed[slug])
        elif slug not in store:
            colorway, pattern = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))
            save_customization_entry(
                self.state_dir, slug, Customization(colorway=colorway, pattern=pattern)
            )

        self._start_mutation(new_accounts, "install", validation.config_dir, provider)
