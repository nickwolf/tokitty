"""The Settings window's Accounts tab: per-account hook status with Install
and Remove.

Everything that can block (reading \\\\wsl$ paths, resolving the default
config dir, writing hooks) runs on a worker thread. Workers only publish into
a Mailbox; settings_async.Poller applies the results on the Tk thread. See
settings_async for why.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional, Tuple

from tokitty import hook_guard
from tokitty.accounts_rows import (  # noqa: F401  (re-exported)
    BUSY_MESSAGE, DEFAULT_PROVIDER, INSTALL_STATES, NO_BUTTON_STATES, PILLS,
    REMOVE_STATES, RowData, Snapshot, _still_listed, collect_rows,
    elide_middle, run_finish_pending, run_hook_op,
)
from tokitty.hooks_install import _same_config_dir
from tokitty.settings_ui import _Tab
from tokitty.settings_async import Mailbox, Poller, run_worker
from tokitty.settings_widgets import (
    BAD, BG_COLOR, DIM_COLOR, FONT_SMALL, MUTED, SURFACE, WARN, text,
)


def render_banner(kit, holder: tk.Frame, banner: Optional[Tuple[str, str, Optional[str]]], *,
                  name_for: Callable[[str], str], on_finish: Callable[[], None],
                  finish_disabled: bool) -> Tuple[Optional[tk.Label], Optional[tk.Button]]:
    """Paint a (kind, message, blocked_by) banner into `holder`, replacing
    what was there. Returns (message label, Finish it button or None); both
    None when there is no banner. Shared with the first-run walkthrough."""
    for child in holder.winfo_children():
        child.destroy()
    if banner is None:
        return None, None
    kind, message, blocked_by = banner
    px = kit.px
    colour = {"bad": ("#3c272e", BAD)}.get(kind, ("#393128", WARN))
    frame = tk.Frame(holder, bg=colour[0])
    frame.pack(fill="x", pady=(0, px(9)))
    glyph = "◌" if message == BUSY_MESSAGE else "!"
    text(frame, glyph, bg=colour[0], color=colour[1], font=("Segoe UI", 12)).pack(
        side="left", padx=(px(10), px(7)), pady=px(6))
    if kind == "blocked":
        message = f"{message} Pending change for {name_for(blocked_by)}."
    label = text(frame, message, bg=colour[0], color=colour[1], font=FONT_SMALL,
                 wraplength=px(430), justify="left")
    label.pack(side="left", pady=px(6))
    finish_button = None
    if kind == "blocked":
        finish_button = kit.button(frame, "Finish it", "secondary", compact=True,
                                   command=on_finish, disabled=finish_disabled)
        finish_button.pack(side="right", padx=px(8))
    return label, finish_button


class _RowView:
    """Handles to one rendered row, for tests and for repainting."""

    def __init__(self, data: RowData):
        self.data = data
        self.pill: Optional[tk.Label] = None
        self.install: Optional[tk.Button] = None
        self.remove: Optional[tk.Button] = None


class AccountsTab(_Tab):
    def build(self) -> None:
        kit = self.kit
        self.kit.page_header(self.frame, "Accounts", "Connections and usage hooks for each profile.")
        self.state_dir = self.win.state_dir
        self.banner_holder = tk.Frame(self.frame, bg=BG_COLOR)
        self.banner_holder.pack(fill="x")
        self.columns = tk.Frame(self.frame, bg=BG_COLOR)
        self.columns.pack(fill="x", pady=(0, kit.px(4)))
        text(self.columns, "ACCOUNT / CONFIG DIRECTORY", color=DIM_COLOR,
             font=FONT_SMALL).pack(side="left")
        text(self.columns, "HOOK ACTIONS", color=DIM_COLOR,
             font=FONT_SMALL).pack(side="right", padx=kit.px(7))
        self.table = tk.Frame(self.frame, bg=BG_COLOR)
        self.table.pack(fill="x")
        if self.win.on_open_accounts is not None:
            kit.button(self.frame, "Manage accounts…", "secondary", compact=True,
                       command=self._manage).pack(anchor="w", pady=(kit.px(8), 0))

        self.mailbox = Mailbox()
        self.rows: List[_RowView] = []
        self.snapshot: Optional[Snapshot] = None
        self.banner: Optional[Tuple[str, str, Optional[str]]] = None
        self._pending = 0
        self._checking = False
        self._requery = False
        self._op_row: Optional[RowData] = None
        self._finishing = False
        self._watching_manager = False
        self._guard_busy = False
        self.poller = Poller(self.owner.toplevel, self.mailbox, self._on_result,
                             self._keep_alive, on_tick=self._on_tick)

    # -- lifecycle -----------------------------------------------------

    def refresh(self) -> None:
        # Status is a worker round trip: only pay it while the tab is shown.
        if self.owner.current == "accounts":
            self._query()

    def close(self) -> None:
        self.poller.cancel()

    def _keep_alive(self) -> bool:
        return self._pending > 0 or self._watching_manager or self._guard_busy

    def _launch(self, tag: str, fn) -> None:
        self._pending += 1
        run_worker(self.mailbox, tag, fn)
        self.poller.start()

    def _on_tick(self) -> None:
        if self._watching_manager and not self._manager_open():
            self._watching_manager = False
            self._query()
        elif self._guard_busy and not hook_guard.held(self.state_dir):
            self._query()

    def _manager_open(self) -> bool:
        from tokitty import accounts_ui

        manager = accounts_ui._manager_instances.get(id(self.win.root))
        if manager is None:
            return False
        try:
            return bool(manager.toplevel.winfo_exists())
        except tk.TclError:
            return False

    def _manage(self) -> None:
        self.win.on_open_accounts()
        self._watching_manager = True
        self.poller.start()

    # -- queries and operations ---------------------------------------

    def _distro_running(self):
        probe = getattr(self.win, "running_distros", None)
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
        state_dir, probe = self.state_dir, getattr(self.win, "running_distros", None)
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
        for child in self.table.winfo_children():
            child.destroy()
        self.rows = []
        own = self._op_row is not None or self._finishing
        held_elsewhere = hook_guard.held(self.state_dir) and not own and self._pending == 0
        self._guard_busy = held_elsewhere
        self._render_banner(held_elsewhere)
        locked = self._locked()
        snapshot = self.snapshot
        if snapshot is None:
            text(self.table, "Checking…", color=MUTED).pack(anchor="w")
            return
        if snapshot.note:
            text(self.table, snapshot.note, color=MUTED).pack(anchor="w")
        for data in snapshot.rows:
            self._render_row(data, locked)

    def _render_banner(self, held_elsewhere: bool) -> None:
        banner = self.banner
        if banner is None and held_elsewhere:
            banner = ("warn", BUSY_MESSAGE, None)
        label, finish_button = render_banner(
            self.kit, self.banner_holder, banner, name_for=self._account_name_for,
            on_finish=self._finish, finish_disabled=self._locked())
        if label is not None:
            self.banner_label = label
        if finish_button is not None:
            self.finish_button = finish_button

    def _render_row(self, data: RowData, locked: bool) -> None:
        kit = self.kit
        px = kit.px
        view = _RowView(data)
        row = tk.Frame(self.table, bg=SURFACE)
        row.pack(fill="x", pady=(0, px(3)))
        info = tk.Frame(row, bg=SURFACE)
        info.pack(side="left", fill="both", expand=True, padx=(px(10), 0), pady=px(7))
        head = tk.Frame(info, bg=SURFACE)
        head.pack(fill="x")
        text(head, data.name, bg=SURFACE, font=("Segoe UI", 9, "bold")).pack(side="left")
        provider = data.provider.capitalize()
        text(head, f"  {provider}", bg=SURFACE, color=DIM_COLOR, font=FONT_SMALL).pack(side="left")
        path = elide_middle(data.config_dir) if data.config_dir else "macOS Keychain (no config directory)"
        status = data.status
        working = self._checking or (self._op_row is not None and self._op_row is data)
        if data.config_dir is None:
            label, kind = "Read-only", "neutral"
        elif working or (self._finishing and False):
            label, kind = ("Working…" if self._op_row is data else "Checking…"), "neutral"
        else:
            label, kind = PILLS.get(status.state, (status.state, "neutral"))
        view.pill = kit.pill(head, label, kind)
        view.pill.pack(side="right", padx=(px(4), px(3)))
        text(info, path, bg=SURFACE, color=DIM_COLOR, font=FONT_SMALL).pack(
            anchor="w", pady=(px(4), 0))
        if status is not None and status.detail and status.state != "installed" and not working:
            text(info, status.detail, bg=SURFACE, font=FONT_SMALL, wraplength=px(330),
                 color=BAD if status.state == "error" else DIM_COLOR,
                 justify="left").pack(anchor="w", pady=(px(3), 0))
        if status is not None and data.config_dir and status.state not in NO_BUTTON_STATES:
            actions = tk.Frame(row, bg=SURFACE)
            actions.pack(side="right", padx=px(9))
            can_install = status.state in INSTALL_STATES and not locked
            can_remove = status.state in REMOVE_STATES and not locked
            view.install = kit.button(
                actions, "Install", "secondary", compact=True, disabled=not can_install,
                command=lambda d=data: self._start_op(d, "install"))
            view.install.pack(side="left", padx=(0, px(4)))
            view.remove = kit.button(
                actions, "Remove", "secondary", compact=True, disabled=not can_remove,
                command=lambda d=data: self._start_op(d, "uninstall"))
            view.remove.pack(side="left")
        self.rows.append(view)

