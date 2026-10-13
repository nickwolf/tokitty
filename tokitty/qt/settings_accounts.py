"""The Qt Settings window's Accounts tab: per-account hook status with Install
and Remove. The counterpart of settings_accounts.

Everything that can block (reading \\\\wsl$ paths, resolving the default
config dir, writing hooks) runs on a worker thread. Workers only publish into
a Mailbox; a QtPoller applies the results on the GUI thread. The pill and
banner rendering are plain functions, so the walkthrough's hooks step reuses
them.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QWidget

from tokitty import hook_guard
from tokitty.accounts_rows import (
    BUSY_MESSAGE, INSTALL_STATES, NO_BUTTON_STATES, PILLS, REMOVE_STATES, RowData, Snapshot,
    collect_rows, elide_middle, run_finish_pending, run_hook_op,
)
from tokitty.hooks_install import _same_config_dir
from tokitty.qt import accounts as qt_accounts
from tokitty.qt.kit import Kit, clear_layout, hbox, text, vbox
from tokitty.qt.poller import QtPoller
from tokitty.qt.settings import _Tab
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
        self.pill: Optional[QLabel] = None
        self.install: Optional[QPushButton] = None
        self.remove: Optional[QPushButton] = None


class AccountsTab(_Tab):
    def build(self) -> None:
        kit = self.kit
        kit.page_header(self.frame, "Accounts", "Connections and usage hooks for each profile.")
        self.state_dir = self.host.state_dir
        self.banner_holder = QWidget()
        vbox(self.banner_holder)
        self.add(self.banner_holder)
        columns = QWidget()
        columns_row = hbox(columns)
        columns_row.setContentsMargins(0, 0, 7, 4)
        columns_row.addWidget(text("ACCOUNT / CONFIG DIRECTORY", color=DIM_COLOR, font=FONT_SMALL))
        columns_row.addStretch(1)
        columns_row.addWidget(text("HOOK ACTIONS", color=DIM_COLOR, font=FONT_SMALL))
        self.add(columns)
        self.table = QWidget()
        vbox(self.table, spacing=3)
        self.add(self.table)
        self.manage_button = None
        if self.host.on_open_accounts is not None:
            self.manage_button = kit.button("Manage accounts…", "secondary", self._manage,
                                            compact=True)
            holder = QWidget()
            holder_row = hbox(holder)
            holder_row.setContentsMargins(0, 8, 0, 0)
            holder_row.addWidget(self.manage_button)
            holder_row.addStretch(1)
            self.add(holder)

        self.mailbox = Mailbox()
        self.rows: List[_RowView] = []
        self.snapshot: Optional[Snapshot] = None
        self.banner: Optional[Banner] = None
        self._pending = 0
        self._checking = False
        self._requery = False
        self._op_row: Optional[RowData] = None
        self._finishing = False
        self._guard_busy = False
        self._closed = False
        self._watched = None  # the manager whose close callback is registered
        self.poller = QtPoller(self.owner, self.mailbox, self._on_result,
                               self._keep_alive, on_tick=self._on_tick)

    # -- lifecycle -----------------------------------------------------

    def refresh(self) -> None:
        # Status is a worker round trip: only pay it while the tab is shown.
        if self.owner.current == "accounts":
            self._query()

    def close(self) -> None:
        self._closed = True
        self.poller.cancel()
        self._unwatch_manager()

    def _keep_alive(self) -> bool:
        return self._pending > 0 or self._guard_busy

    def _launch(self, tag: str, fn) -> None:
        self._pending += 1
        run_worker(self.mailbox, tag, fn)
        self.poller.start()

    def _on_tick(self) -> None:
        if self._guard_busy and not hook_guard.held(self.state_dir):
            self._query()

    def _manage(self) -> None:
        manager = self.host.on_open_accounts()
        if manager is None:
            manager = qt_accounts.current()
        if manager is None:
            return
        self._unwatch_manager()
        self._watched = manager
        manager.closed.append(self._on_manager_closed)

    def _unwatch_manager(self) -> None:
        manager, self._watched = self._watched, None
        if manager is not None and self._on_manager_closed in manager.closed:
            manager.closed.remove(self._on_manager_closed)

    def _on_manager_closed(self) -> None:
        """The manager may have added or removed accounts: rebuild the rows."""
        self._watched = None
        if not self._closed:
            self._query()

    # -- queries and operations ---------------------------------------

    def _distro_running(self):
        probe = getattr(self.host, "running_distros", None)
        if probe is None:
            return None

        def running(name: str) -> bool:
            return name.casefold() in {d.casefold() for d in probe()}

        return running

    def _query(self) -> None:
        if self._checking:
            self._requery = True
            return
        self._checking = True
        self._render()
        state_dir, running = self.state_dir, self._distro_running()
        self._launch("status", lambda: collect_rows(state_dir, running))

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

    # -- rendering -----------------------------------------------------

    def _locked(self) -> bool:
        return (self._checking or self._op_row is not None or self._finishing
                or hook_guard.held(self.state_dir))

    def _account_name_for(self, config_dir: str) -> str:
        if self.snapshot is not None:
            for row in self.snapshot.rows:
                if row.config_dir and _same_config_dir(row.config_dir, config_dir):
                    return row.name
        return elide_middle(config_dir)

    def _render(self) -> None:
        clear_layout(self.table.layout())
        self.rows = []
        own = self._op_row is not None or self._finishing
        held_elsewhere = hook_guard.held(self.state_dir) and not own and self._pending == 0
        self._guard_busy = held_elsewhere
        self._render_banner(held_elsewhere)
        locked = self._locked()
        snapshot = self.snapshot
        if snapshot is None:
            self.table.layout().addWidget(text("Checking…", color=MUTED))
            return
        if snapshot.note:
            self.table.layout().addWidget(text(snapshot.note, color=MUTED))
        for data in snapshot.rows:
            self._render_row(data, locked)

    def _render_banner(self, held_elsewhere: bool) -> None:
        banner = self.banner
        if banner is None and held_elsewhere:
            banner = ("warn", BUSY_MESSAGE, None)
        label, finish_button = render_banner(
            self.kit, self.banner_holder, banner, name_for=self._account_name_for,
            on_finish=self._finish, finish_disabled=self._locked())
        self.banner_label = label
        self.finish_button = finish_button

    def _render_row(self, data: RowData, locked: bool) -> None:
        kit = self.kit
        view = _RowView(data)
        row = kit.surface(rows=False)
        row_layout = hbox(row)
        row_layout.setContentsMargins(10, 7, 9, 7)
        info = QWidget()
        info_col = vbox(info)
        row_layout.addWidget(info, 1)
        head = QWidget()
        head_row = hbox(head)
        head_row.addWidget(text(data.name, font=FONT_MEDIUM))
        head_row.addWidget(text(f"  {data.provider.capitalize()}", color=DIM_COLOR, font=FONT_SMALL))
        head_row.addStretch(1)
        status = data.status
        working = self._checking or (self._op_row is not None and self._op_row is data)
        label = ("Working…" if self._op_row is data else "Checking…") if working else None
        view.pill = render_pill(kit, data, label)
        head_row.addWidget(view.pill)
        info_col.addWidget(head)
        path = text(elide_middle(data.config_dir) if data.config_dir
                    else "macOS Keychain (no config directory)", color=DIM_COLOR, font=FONT_SMALL)
        path.setContentsMargins(0, 4, 0, 0)
        info_col.addWidget(path)
        if status is not None and status.detail and status.state != "installed" and not working:
            detail = text(status.detail, color=BAD if status.state == "error" else DIM_COLOR,
                          font=FONT_SMALL)
            detail.setWordWrap(True)
            detail.setContentsMargins(0, 3, 0, 0)
            info_col.addWidget(detail)
        if status is not None and data.config_dir and status.state not in NO_BUTTON_STATES:
            actions = QWidget()
            actions_row = hbox(actions, spacing=4)
            can_install = status.state in INSTALL_STATES and not locked
            can_remove = status.state in REMOVE_STATES and not locked
            view.install = kit.button(
                "Install", "secondary", lambda d=data: self._start_op(d, "install"),
                compact=True, disabled=not can_install)
            actions_row.addWidget(view.install)
            view.remove = kit.button(
                "Remove", "secondary", lambda d=data: self._start_op(d, "uninstall"),
                compact=True, disabled=not can_remove)
            actions_row.addWidget(view.remove)
            row_layout.addWidget(actions, 0, Qt.AlignmentFlag.AlignTop)
        self.table.layout().addWidget(row)
        self.rows.append(view)
