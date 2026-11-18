"""The Qt Settings window's Accounts tab, the one place for accounts: add,
rename, reorder and remove them, and see and repair each one's hooks (#268).

Everything that can block (reading \\\\wsl$ paths, resolving the default
config dir, writing hooks, validating a path) runs on a worker thread. Workers
only publish into a Mailbox; a QtPoller applies the results on the GUI thread.
The pill and banner rendering are plain functions, so the walkthrough's hooks
step reuses them.

The add, move and remove logic was the Manage accounts window's. A mutation
claims hook_guard's process-wide slot, so the tab's existing "held" handling
covers it too.
"""
from __future__ import annotations

import posixpath
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QWidget,
)

from tokitty import hook_guard, sprites
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
    _run_validation_off_thread, _same_dir, build_discovered_path_specs, build_row_specs,
    discover_local_codex_home, move_account, reconcile_before_save, validate_account_path,
)
from tokitty.accounts_rows import (
    BUSY_MESSAGE, INSTALL_STATES, PILLS, REMOVE_STATES, RowData, Snapshot,
    collect_rows, elide_middle, run_finish_pending, run_hook_op,
)
from tokitty.customize import SINGLE_KEY, Customization, load_customization, save_customization_entry
from tokitty.hook_guard import (
    claim_operation as _claim_operation,
    complete_operation as _complete_operation,
)
from tokitty.hooks_install import _same_config_dir, load_pending_hook_op
from tokitty.manual_path import PathValidationResult
from tokitty.migration import absorb_implicit_default
from tokitty.providers import UnknownProviderError, get_provider, supported_kinds
from tokitty.qt.kit import Kit, clear_layout, hbox, text, vbox
from tokitty.qt.poller import QtPoller
from tokitty.qt.settings import _Tab
from tokitty.randomize import random_eyes, random_look, random_tail
from tokitty.settings_style import BAD, DIM_COLOR, FONT_MEDIUM, FONT_SMALL, MUTED, WARN
from tokitty.worker_mailbox import Mailbox, run_worker

# (kind, message, blocked_by): the banner above the table.
Banner = Tuple[str, str, Optional[str]]


def pill_spec(data: RowData, working: Optional[str] = None) -> Tuple[str, str]:
    """(label, kind) of a row's status pill. `working` is the transient label
    ("Checking…", "Working…") shown instead of the status while a query or an
    operation is under way."""
    if data.config_dir is None or data.status is None:
        return "Read-only", "neutral"
    if working:
        return working, "neutral"
    return PILLS.get(data.status.state, (data.status.state, "neutral"))


def render_pill(kit: Kit, data: RowData, working: Optional[str] = None) -> QLabel:
    label, kind = pill_spec(data, working)
    return kit.pill(label, kind)


def render_banner(kit: Kit, holder: QWidget, banner: Optional[Banner], *,
                  name_for: Callable[[str], str], on_finish: Callable[[], None],
                  finish_disabled: bool) -> Tuple[Optional[QLabel], Optional[QPushButton]]:
    """Paint a (kind, message, blocked_by) banner into `holder`, replacing what
    was there. Returns (message label, Finish it button or None); both None
    when there is no banner. Shared with the first-run walkthrough."""
    clear_layout(holder.layout())
    if banner is None:
        return None, None
    kind, message, blocked_by = banner
    tone, colour = ("bad", BAD) if kind == "bad" else ("warn", WARN)
    frame = QFrame()
    frame.setProperty("kit", "banner")
    frame.setProperty("tone", tone)
    row = hbox(frame)
    row.setContentsMargins(10, 6, 8, 6)
    glyph = "◌" if message == BUSY_MESSAGE else "!"
    row.addWidget(text(glyph, color=colour, font=("Segoe UI", 12)))
    row.addSpacing(7)
    if kind == "blocked":
        message = f"{message} Pending change for {name_for(blocked_by)}."
    label = text(message, color=colour, font=FONT_SMALL)
    label.setWordWrap(True)
    row.addWidget(label, 1)
    finish_button = None
    if kind == "blocked":
        finish_button = kit.button("Finish it", "secondary", on_finish, compact=True,
                                   disabled=finish_disabled)
        row.addWidget(finish_button)
    holder.layout().addWidget(frame)
    holder.layout().addSpacing(9)
    return label, finish_button


class _RowView:
    """Handles to one rendered row, for tests and for repainting."""

    def __init__(self, data: RowData):
        self.data = data
        self.label = ""
        self.pill: Optional[QLabel] = None
        self.install: Optional[QPushButton] = None
        self.more: Optional[QPushButton] = None
        self.menu: Optional[QMenu] = None
        self.menu_actions: Dict[str, QAction] = {}


def _harness_name(kind: str) -> str:
    try:
        return get_provider(kind).display_name
    except UnknownProviderError:
        return kind.capitalize()


def _this_computer() -> str:
    return "Windows" if sys.platform == "win32" else "This computer"


class AccountsTab(_Tab):
    def build(self) -> None:
        kit = self.kit
        kit.page_header(self.frame, "Accounts", "Each account is one cat on the card.")
        self.state_dir = self.host.state_dir
        self.banner_holder = QWidget()
        vbox(self.banner_holder)
        self.add(self.banner_holder)
        self.table = QWidget()
        vbox(self.table, spacing=3)
        self.add(self.table)
        self.add_header = kit.section_header(self.frame, "Add an account",
                                             "Found on this computer", top=18)
        self.found_box = QWidget()
        vbox(self.found_box, spacing=3)
        self.add(self.found_box)
        self.other_card = self._build_other_card()
        self.frame.layout().addSpacing(6)
        self.add(self.other_card)

        self.mailbox = Mailbox()
        self.rows: List[_RowView] = []
        self.found_rows: List[Tuple[str, QPushButton]] = []
        self.snapshot: Optional[Snapshot] = None
        self.banner: Optional[Banner] = None
        self._pending = 0
        self._checking = False
        self._requery = False
        self._op_row: Optional[RowData] = None
        self._finishing = False
        self._mutating = False
        self._validating = False
        # host.found_homes runs wsl.exe, so it is read on a worker too.
        self._found_raw: list = []
        self._finding = False
        self._guard_busy = False
        self._closed = False
        self._was_shown = False
        self.poller = QtPoller(self.owner, self.mailbox, self._on_result,
                               self._keep_alive, on_tick=self._on_tick)

    def _build_other_card(self) -> QFrame:
        """Any config folder the scan did not find. Claude Code is
        preselected, so the common case is one click."""
        kit = self.kit
        card = kit.surface(rows=False)
        column = vbox(card)
        column.setContentsMargins(10, 8, 9, 9)
        column.addWidget(text("Somewhere else", font=FONT_MEDIUM))
        hint = text("Pick the account's config folder, e.g. .claude-work for Claude Code "
                    "or .codex for Codex.", color=DIM_COLOR, font=FONT_SMALL)
        hint.setWordWrap(True)
        hint.setContentsMargins(0, 3, 0, 7)
        column.addWidget(hint)
        controls = QWidget()
        controls_row = hbox(controls, spacing=8)
        self.provider_picker = kit.segmented(
            [(kind, get_provider(kind).display_name) for kind in supported_kinds()])
        self.provider_picker.set(DEFAULT_PROVIDER)
        controls_row.addWidget(self.provider_picker)
        controls_row.addStretch(1)
        self.choose_button = kit.button("Choose a folder…", "secondary", self._on_choose_folder,
                                        compact=True)
        controls_row.addWidget(self.choose_button)
        column.addWidget(controls)
        return card

    # -- lifecycle -----------------------------------------------------

    def refresh(self) -> None:
        # Status is a worker round trip: only pay it while the tab is shown.
        shown = self.owner.current == "accounts"
        if shown:
            # The sweep lists listed homes too (_found_homes filters them),
            # so a move, rename or remove needs no new one: only showing the
            # tab does.
            self._query(sweep=not self._was_shown)
        self._was_shown = shown

    def close(self) -> None:
        self._closed = True
        self.poller.cancel()

    def close_blocked(self) -> bool:
        # A mutation ends in host.accounts_changed(); closing first would
        # leave the panes out of step with accounts.json.
        return self._mutating

    def _keep_alive(self) -> bool:
        return self._pending > 0 or self._guard_busy

    def _launch(self, tag: str, fn) -> None:
        self._pending += 1
        run_worker(self.mailbox, tag, fn)
        self.poller.start()

    def _on_tick(self) -> None:
        if self._guard_busy and not hook_guard.held(self.state_dir):
            self._query()

    # -- queries and operations ---------------------------------------

    def _distro_running(self):
        probe = getattr(self.host, "running_distros", None)
        if probe is None:
            return None

        # One probe per status pass, not one per account: each is a wsl.exe
        # call (about 200 ms on Windows), and the pass holds every row at
        # "Checking…" until it ends. _query makes a fresh one per pass.
        seen: List[set] = []

        def running(name: str) -> bool:
            if not seen:
                seen.append({d.casefold() for d in probe()})
            return name.casefold() in seen[0]

        return running

    def _query(self, sweep: bool = False) -> None:
        if sweep:
            self._sweep()
        if self._checking:
            self._requery = True
            return
        self._checking = True
        self._render()
        state_dir, running = self.state_dir, self._distro_running()
        self._launch("status", lambda: collect_rows(state_dir, running))

    def _sweep(self) -> None:
        probe = getattr(self.host, "found_homes", None)
        if probe is not None and not self._finding:
            self._finding = True
            self._launch("found", probe)

    def _start_op(self, row: RowData, op: str) -> None:
        if self._locked():
            return
        self.banner = None
        self._op_row = row
        self._render()
        state_dir = self.state_dir
        self._launch("op", lambda: run_hook_op(state_dir, row, op))

    def _finish(self) -> None:
        if self._locked():
            return
        self.banner = None
        self._finishing = True
        self._render()
        state_dir, probe = self.state_dir, getattr(self.host, "running_distros", None)
        self._launch("finish", lambda: run_finish_pending(state_dir, probe))

    def _on_result(self, tag: str, ok: bool, value) -> None:
        self._pending -= 1
        if tag == "status":
            self._checking = False
            self.snapshot = value if ok else Snapshot((), f"Could not read accounts: {value}")
            self._render()
            if self._requery:
                self._requery = False
                self._query()
        elif tag == "op":
            self._op_row = None
            self._after_op(ok, value)
        elif tag == "finish":
            self._finishing = False
            self._after_finish(ok, value)
        elif tag == "mutation":
            self._mutating = False
            self._after_mutation(*value)
        elif tag == "validate":
            self._validating = False
            self._after_validation(*value)
        elif tag == "found":
            self._finding = False
            self._found_raw = list(value) if ok else []
            self._render()

    def _after_op(self, ok: bool, value) -> None:
        if not ok:
            self.banner = ("bad", f"Could not change hooks: {value}", None)
        else:
            kind, result = value
            if kind == "busy":
                self.banner = ("warn", BUSY_MESSAGE, None)
            elif kind == "gone":
                self.banner = ("warn", "That account is no longer in the list.", None)
            elif result.blocked_by:
                self.banner = ("blocked", result.message, result.blocked_by)
            elif not result.ok:
                self.banner = ("bad", result.message, None)
        self._query()

    def _after_finish(self, ok: bool, value) -> None:
        if not ok:
            self.banner = ("bad", f"Could not finish the change: {value}", None)
        else:
            kind, result = value
            if kind == "busy":
                self.banner = ("warn", BUSY_MESSAGE, None)
            elif result is None:
                self.banner = ("warn", "Nothing to finish right now. If its WSL distro is "
                                       "stopped, start it and try again.", None)
            elif not result.ok:
                self.banner = ("bad", result.message, None)
        self._query()

    # -- mutations: add, move, remove ----------------------------------

    def _load_accounts_for_mutation(self) -> Optional[AccountsLoadResult]:
        result = load_accounts_result(self.state_dir)
        if result.state == "malformed":
            QMessageBox.critical(
                self.owner, "Accounts",
                "accounts.json is malformed. Fix or delete it before adding or removing accounts.")
            return None
        backfill_identity_history(self.state_dir, result.accounts)
        return result

    def _start_mutation(self, accounts: List[Account], op: str, config_dir: str,
                        provider: str = DEFAULT_PROVIDER) -> None:
        # apply_account_mutation overwrites the pending-op record, so a failed
        # change has to be finished (or retried) before the next one starts.
        pending = load_pending_hook_op(self.state_dir)
        if pending is not None:
            self.banner = ("blocked", "Finish the pending hook change before changing accounts.",
                           pending["config_dir"])
            self._render()
            return
        state_key, operation, claimed = _claim_operation(self.state_dir, "mutation")
        if not claimed:
            self.banner = ("warn", BUSY_MESSAGE, None)
            self._render()
            return
        self.banner = None
        self._mutating = True
        self._pending += 1
        self._render()

        def on_done(outcome):
            # Free the slot first so the GUI sees it released with the result.
            _complete_operation(state_key, operation, outcome)
            self.mailbox.put("mutation", True, (op, config_dir, outcome))

        try:
            _run_mutation_off_thread(
                self.state_dir, accounts, op, config_dir, on_done, provider=provider)
        except Exception as exc:
            on_done(exc)
        self.poller.start()

    def _after_mutation(self, op: str, config_dir: str, outcome) -> None:
        if isinstance(outcome, Exception) and op == "move":
            # No hook work ran, so a failure is only the save: nothing to retry.
            self.banner = ("bad", f"Couldn't save the new order: {outcome}", None)
        elif isinstance(outcome, Exception) or not outcome.ok:
            # The pending-op record is still there: Finish it retries it.
            reason = outcome if isinstance(outcome, Exception) else outcome.message
            self.banner = ("blocked", f"The hook update failed. {reason}", config_dir)
        elif getattr(outcome, "warning", None):
            # One banner, not two: the note (such as "waiting for approval in
            # Codex") rides along with the warning.
            body = outcome.warning
            if getattr(outcome, "note", None):
                body = f"{body} {outcome.note}"
            self.banner = ("warn", body, None)
        elif getattr(outcome, "note", None):
            self.banner = ("warn", outcome.note, None)
        # accounts.json is written before the hooks, so it may have changed
        # even when the hook side failed.
        self.host.accounts_changed()
        if not self._checking and not self._requery:
            self._query()

    def _on_move(self, slug: str, config_dir: str, provider: str, step: int) -> None:
        """Swap the account with its neighbour and save. No hook work."""
        if self._locked():
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is None:
            return
        try:
            accounts = reconcile_before_save(self.state_dir, load_result.accounts)
        except ValueError as exc:
            QMessageBox.critical(self.owner, "Accounts", str(exc))
            return
        # Moved on the reconciled list: the row may be stale, and another
        # window's change must not be overwritten by this one's order.
        moved = move_account(accounts, slug, step)
        if moved is None:
            self._query()
            return
        self._start_mutation(moved, "move", config_dir, provider)

    def _confirm_remove(self, label: str, harness: str, config_dir: str) -> bool:
        # The question goes in the text, not only the title: macOS shows no
        # title on a message box.
        box = QMessageBox(QMessageBox.Icon.NoIcon, "Remove account", f"Remove {label}?",
                          QMessageBox.StandardButton.NoButton, self.owner)
        box.setInformativeText(
            f"Its cat leaves the card and Tokitty removes its hooks from "
            f"{elide_middle(config_dir)}. Your {harness} login, history and "
            f"settings stay. You can add it back from Add an account.")
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        remove = box.addButton("Remove account", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is remove

    def _on_remove(self, view: _RowView) -> None:
        data = view.data
        if self._locked():
            return
        if not self._confirm_remove(view.label, _harness_name(data.provider), data.config_dir):
            return
        self._remove_account(data.name, data.config_dir, data.provider)

    def _remove_account(self, slug: str, config_dir: str, provider: str) -> None:
        if self._locked():
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
            self._query()
            return
        remaining = [a for a in accounts if a.name != slug]
        try:
            remaining = reconcile_before_save(self.state_dir, remaining)
        except ValueError as exc:
            QMessageBox.critical(self.owner, "Accounts", str(exc))
            return
        remaining = [a for a in remaining if a.name != slug]

        self._start_mutation(remaining, "remove", config_dir, provider)

    def _on_rename(self, view: _RowView) -> None:
        """Rename the cat through the seam the Look tab's name entry uses, so
        the running pane follows. Pane i is accounts.json's i-th account, or
        pane 0 for the implicit default."""
        host = self.host
        result = load_accounts_result(self.state_dir)
        if result.state == "valid_non_empty":
            index = next((i for i, a in enumerate(result.accounts) if a.name == view.data.name), None)
        else:
            index = 0 if view.data.is_default else None
        if index is None or index >= len(host.panes):
            return
        current = host.pane_name(index)[0]
        value, accepted = QInputDialog.getText(self.owner, "Rename", "Cat name:", text=current)
        new = value.strip()
        if accepted and new != current:
            host.on_customization_changed(index, "label", new)
            host.notify_state_changed()

    # -- adding --------------------------------------------------------

    def _on_choose_folder(self) -> None:
        if self._locked():
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is None:
            return
        raw = QFileDialog.getExistingDirectory(
            self.owner, "Choose the account's config folder", str(Path.home()))
        if raw:
            self._add_path(raw, load_result, self.provider_picker.get())

    def _on_add_found(self, config_dir: str, provider: str) -> None:
        if self._locked():
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is not None:
            self._add_path(config_dir, load_result, provider)

    def _add_path(self, raw: str, load_result: AccountsLoadResult,
                  provider: str = DEFAULT_PROVIDER) -> None:
        active_dirs = [a.config_dir for a in load_result.accounts]
        if _needs_off_thread_validation(provider, raw):
            self._start_validation(raw, active_dirs, load_result, provider)
            return
        validation = validate_account_path(provider, raw, active_dirs)
        self._finish_add(validation, load_result, provider)

    def _start_validation(self, raw: str, active_dirs: List[str],
                          load_result: AccountsLoadResult, provider: str) -> None:
        """Check a path through wsl.exe off the GUI thread, then finish the
        add back on it."""
        self._validating = True
        self._pending += 1
        self._render()

        def on_done(outcome):
            self.mailbox.put("validate", True, (outcome, load_result, provider))

        try:
            _run_validation_off_thread(provider, raw, active_dirs, on_done)
        except Exception as exc:
            on_done(PathValidationResult(ok=False, error=str(exc)))
        self.poller.start()

    def _after_validation(self, outcome, load_result: AccountsLoadResult, provider: str) -> None:
        self._render()
        self._finish_add(outcome, load_result, provider)

    def _finish_add(self, validation: PathValidationResult, load_result: AccountsLoadResult,
                    provider: str) -> None:
        accounts = load_result.accounts
        if not validation.ok:
            QMessageBox.critical(self.owner, "Add account", validation.error)
            return

        try:
            accounts = reconcile_before_save(self.state_dir, accounts)
        except ValueError as exc:
            QMessageBox.critical(self.owner, "Accounts", str(exc))
            return
        # Validation may have run off the GUI thread, long enough for another
        # window to add the same directory in the meantime.
        if any(_same_dir(a.config_dir, validation.config_dir) for a in accounts):
            QMessageBox.critical(self.owner, "Add account", "This account is already added.")
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
            colorway, pattern = random_look(sprites.colorway_names(), list(sprites.PATTERNS))
            save_customization_entry(
                self.state_dir, slug,
                Customization(colorway=colorway, pattern=pattern, tail=random_tail(),
                              eyes=random_eyes(pattern)),
            )

        self._start_mutation(new_accounts, "install", validation.config_dir, provider)

    # -- rendering -----------------------------------------------------

    def _locked(self) -> bool:
        return (self._checking or self._op_row is not None or self._finishing
                or self._mutating or self._validating or hook_guard.held(self.state_dir))

    def _account_name_for(self, config_dir: str) -> str:
        if self.snapshot is not None:
            for row in self.snapshot.rows:
                if row.config_dir and _same_config_dir(row.config_dir, config_dir):
                    return row.name
        return elide_middle(config_dir)

    def _labels(self, loaded: AccountsLoadResult) -> Dict[str, str]:
        """slug -> the label the cat shows, never the raw slug."""
        store = load_customization(self.state_dir)
        return {row.slug: row.display_label for row in build_row_specs(loaded.accounts, store)}

    def _render(self) -> None:
        clear_layout(self.table.layout())
        self.rows = []
        own = (self._op_row is not None or self._finishing or self._mutating
               or self._validating)
        held_elsewhere = hook_guard.held(self.state_dir) and not own and self._pending == 0
        self._guard_busy = held_elsewhere
        self._render_banner(held_elsewhere)
        locked = self._locked()
        loaded = load_accounts_result(self.state_dir)
        snapshot = self.snapshot
        if snapshot is None:
            self.table.layout().addWidget(text("Checking…", color=MUTED))
        else:
            if snapshot.note:
                self.table.layout().addWidget(text(snapshot.note, color=MUTED))
            labels = self._labels(loaded)
            store = load_customization(self.state_dir)
            for index, data in enumerate(snapshot.rows):
                if data.is_default:
                    label = (store.get(SINGLE_KEY) or Customization()).label or "Default"
                else:
                    label = labels.get(data.name) or f"Cat {index + 1}"
                self._render_row(data, label, index, len(snapshot.rows), locked)
        self._render_found(loaded, locked)

    def _render_banner(self, held_elsewhere: bool) -> None:
        banner = self.banner
        if banner is None and held_elsewhere:
            banner = ("warn", BUSY_MESSAGE, None)
        label, finish_button = render_banner(
            self.kit, self.banner_holder, banner, name_for=self._account_name_for,
            on_finish=self._finish, finish_disabled=self._locked())
        self.banner_label = label
        self.finish_button = finish_button

    def _render_row(self, data: RowData, label: str, index: int, count: int,
                    locked: bool) -> None:
        kit = self.kit
        view = _RowView(data)
        view.label = label
        row = kit.surface(rows=False)
        row_layout = hbox(row)
        row_layout.setContentsMargins(10, 7, 6, 7)
        info = QWidget()
        info_col = vbox(info)
        row_layout.addWidget(info, 1)
        head = QWidget()
        head_row = hbox(head)
        head_row.addWidget(text(label, font=FONT_MEDIUM))
        head_row.addWidget(text(f"  {_harness_name(data.provider)}", color=DIM_COLOR,
                                font=FONT_SMALL))
        head_row.addStretch(1)
        info_col.addWidget(head)
        path = text(elide_middle(data.config_dir) if data.config_dir
                    else "macOS Keychain (no config directory)", color=DIM_COLOR, font=FONT_SMALL)
        path.setContentsMargins(0, 4, 0, 0)
        info_col.addWidget(path)
        status = data.status
        working = self._checking or (self._op_row is not None and self._op_row is data)
        text_label = ("Working…" if self._op_row is data else "Checking…") if working else None
        if status is not None and status.detail and status.state != "installed" and not working:
            detail = text(status.detail, color=BAD if status.state == "error" else DIM_COLOR,
                          font=FONT_SMALL)
            detail.setWordWrap(True)
            detail.setContentsMargins(0, 3, 0, 0)
            info_col.addWidget(detail)

        actions = QWidget()
        actions_row = hbox(actions, spacing=4)
        view.pill = render_pill(kit, data, text_label)
        actions_row.addWidget(view.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        if status is not None and data.config_dir and status.state in INSTALL_STATES:
            view.install = kit.button(
                "Repair" if status.state == "outdated" else "Install", "secondary",
                lambda d=data: self._start_op(d, "install"), compact=True, disabled=locked)
            actions_row.addWidget(view.install)
        view.more = kit.button("⋯", "quiet", compact=True)
        view.menu = self._build_menu(view, index, count, locked)
        view.more.clicked.connect(lambda _checked=False, v=view: self._popup(v))
        actions_row.addWidget(view.more)
        row_layout.addWidget(actions, 0, Qt.AlignmentFlag.AlignTop)
        self.table.layout().addWidget(row)
        self.rows.append(view)

    def _build_menu(self, view: _RowView, index: int, count: int, locked: bool) -> QMenu:
        data = view.data
        menu = QMenu(self.owner)
        menu.setProperty("kit", "dropdown-menu")
        menu.setToolTipsVisible(True)
        listed = bool(data.config_dir) and not data.is_default

        def add(title: str, command, enabled: bool = True, tip: str = "") -> None:
            action = menu.addAction(title)
            action.triggered.connect(lambda _checked=False: command())
            action.setEnabled(enabled)
            if tip:
                action.setToolTip(tip)
            view.menu_actions[title] = action

        # Locked too: a move or remove in flight changes which pane is whose.
        add("Rename…", lambda v=view: self._on_rename(v), enabled=not locked)
        for title, step, enabled in (("Move up", -1, index > 0),
                                     ("Move down", 1, index < count - 1)):
            add(title, lambda s=step, d=data: self._on_move(d.name, d.config_dir, d.provider, s),
                enabled=listed and enabled and not locked)
        menu.addSeparator()
        status = data.status
        add("Remove hooks only", lambda d=data: self._start_op(d, "uninstall"),
            enabled=bool(data.config_dir) and status is not None
            and status.state in REMOVE_STATES and not locked)
        add("Remove account…", lambda v=view: self._on_remove(v),
            enabled=listed and count > 1 and not locked,
            tip="Tokitty needs at least one account. Removing every account would "
                "silently fall back to a default ~/.claude pane." if count <= 1 else "")
        return menu

    @staticmethod
    def _popup(view: _RowView) -> None:
        more, menu = view.more, view.menu
        menu.popup(more.mapToGlobal(QPoint(more.width() - menu.sizeHint().width(),
                                           more.height() + 2)))

    def _found_homes(self, loaded: AccountsLoadResult) -> List[Tuple[str, str, str, str]]:
        """(provider, where, shown path, config dir) of every home found and not
        yet an account. A WSL home shows its in-distro path.
        The sweep may predate the last change, so it is filtered against the
        accounts.json of this moment."""
        current = loaded.accounts
        found = []
        for spec in build_discovered_path_specs(self._found_raw):
            if not any(_same_config_dir(spec.config_dir, a.config_dir) for a in current):
                found.append((DEFAULT_PROVIDER, spec.distro,
                              posixpath.dirname(spec.credentials_path), spec.config_dir))
        codex = discover_local_codex_home(current)
        if codex is not None:
            found.append((CODEX_KIND, _this_computer(), codex, codex))
        return found

    def _render_found(self, loaded: AccountsLoadResult, locked: bool) -> None:
        clear_layout(self.found_box.layout())
        self.found_rows = []
        keychain_only = self.snapshot is not None and any(
            row.config_dir is None for row in self.snapshot.rows)
        found = [] if keychain_only else self._found_homes(loaded)
        self.add_header.hint.setVisible(bool(found))
        for provider, where, shown_path, path in found:
            row = self.kit.surface(rows=False)
            layout = hbox(row)
            layout.setContentsMargins(10, 7, 9, 7)
            info = QWidget()
            info_col = vbox(info)
            info_col.addWidget(text(f"{_harness_name(provider)}  ·  {where}", font=FONT_MEDIUM))
            shown = text(shown_path, color=DIM_COLOR, font=FONT_SMALL)
            shown.setContentsMargins(0, 4, 0, 0)
            info_col.addWidget(shown)
            layout.addWidget(info, 1)
            button = self.kit.button("Add", "secondary",
                                     lambda p=path, k=provider: self._on_add_found(p, k),
                                     compact=True, disabled=locked)
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.found_box.layout().addWidget(row)
            self.found_rows.append((path, button))
        self.add_header.setVisible(not keychain_only)
        self.other_card.setVisible(not keychain_only)
        self.choose_button.setEnabled(not locked)
