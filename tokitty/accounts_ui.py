"""The persistent Accounts manager dialog: add/rename/remove accounts
without hand-editing accounts.json. Modeled on
TokittyWindow._open_customize_dialog (ui.py:429-463) for the Toplevel
shape, and _open_rename_dialog (ui.py:465-471) for simpledialog use.
See docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md.
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Dict, List

from tokitty.accounts import (
    Account,
    canonicalize_locator,
    load_accounts,
    load_identity_history,
    save_identity_history,
    assign_identity_slug,
)
from tokitty.customize import Customization, SINGLE_KEY, rename_account, load_customization, save_customization
from tokitty.hooks_install import apply_account_mutation, retry_pending_hook_op
from tokitty.manual_path import validate_manual_path
from tokitty.migration import absorb_implicit_default
from tokitty.randomize import random_look
from tokitty import sprites

_manager_instances: Dict[int, "AccountsManager"] = {}

# How often __init__'s Tk-thread-owned poll checks whether the background
# pending-hook-op retry has finished (see AccountsManager.__init__). Short,
# since this only gates a one-shot row-refresh right after the dialog opens,
# not a continuous UI loop like __main__.py's UI_REFRESH_MS.
_RETRY_POLL_MS = 100


@dataclass(frozen=True)
class RowSpec:
    slug: str
    config_dir: str
    display_label: str
    remove_enabled: bool


def _fallback_label(index: int) -> str:
    return f"Cat {index + 1}"


def build_row_specs(accounts: List[Account], customization_store: Dict[str, Customization]) -> List[RowSpec]:
    """Pure, Tk-free: one display row per account. Never shows the raw
    slug as a fallback label -- it's an opaque SHA-256-derived string."""
    remove_enabled = len(accounts) > 1
    rows = []
    for index, account in enumerate(accounts):
        custom = customization_store.get(account.name)
        label = custom.label if custom and custom.label else _fallback_label(index)
        rows.append(RowSpec(
            slug=account.name, config_dir=account.config_dir,
            display_label=label, remove_enabled=remove_enabled,
        ))
    return rows


def reconcile_before_save(state_dir: Path, in_memory_accounts: List[Account]) -> List[Account]:
    """Reload accounts.json immediately before every save, so two
    independently opened dialogs cannot silently clobber each other's
    changes with a stale in-memory list."""
    on_disk = load_accounts(state_dir)
    return on_disk if on_disk is not None else in_memory_accounts


def _run_mutation_off_thread(state_dir: Path, accounts: List[Account], op: str,
                              config_dir: str, on_done) -> None:
    def worker():
        result = apply_account_mutation(state_dir, accounts, op, config_dir)
        on_done(result)

    threading.Thread(target=worker, daemon=True).start()


class AccountsManager:
    """Singleton Toplevel per root: AccountsManager.open() raises the
    existing dialog instead of creating a second one."""

    def __init__(self, root: tk.Tk, state_dir: Path):
        self.root = root
        self.state_dir = state_dir
        self.toplevel = tk.Toplevel(root)
        self.toplevel.title("Accounts")
        self.toplevel.transient(root)
        self.toplevel.resizable(False, False)
        self.toplevel.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        # Best-effort, silent unless it matters: retries a hook
        # install/uninstall left incomplete by a prior crash, one more
        # chance to complete whenever the user opens the manager. Off the
        # Tk thread -- per the design spec, a stuck wsl.exe call or a slow
        # filesystem here must not freeze the UI -- so _build() above runs
        # first with whatever pre-retry state is already on disk.
        #
        # The background thread below only ever writes _retry_state under
        # a lock; it never touches Tk itself. A small Tk-thread-owned poll
        # (_poll_retry_done, scheduled here via self.toplevel.after, which
        # is always safe since it's called from the same thread that owns
        # this Toplevel) picks up the result and refreshes rows once --
        # the same producer/consumer shape __main__.py's run_gui uses for
        # its own WSL-discovery result, and for the same reason: a
        # background thread calling anything Tk-related itself (the
        # original shape here was self.toplevel.after(0, self._refresh_rows)
        # called from the worker) can race a *second* such thread -- e.g.
        # apply_account_mutation's own off-thread add/remove below -- with
        # no live mainloop to serialize the two, which reproduced as a
        # hard interpreter abort (not a catchable exception) under this
        # file's own test harness the moment both were in flight at once.
        self._retry_lock = threading.Lock()
        self._retry_state = {"done": False}
        threading.Thread(target=self._run_retry_off_thread, daemon=True).start()
        self.toplevel.after(_RETRY_POLL_MS, self._poll_retry_done)

    def _run_retry_off_thread(self) -> None:
        try:
            retry_pending_hook_op(self.state_dir)
        except (OSError, PermissionError):
            # Same documented hazard as apply_account_mutation: the
            # underlying hook install/uninstall functions don't convert
            # filesystem exceptions to a result object. A failed retry
            # just leaves the pending-op record in place for next time,
            # exactly like any other failed retry_pending_hook_op call.
            pass
        with self._retry_lock:
            self._retry_state["done"] = True

    def _poll_retry_done(self) -> None:
        if not self.toplevel.winfo_exists():
            return  # manager was closed before the retry finished
        with self._retry_lock:
            done = self._retry_state["done"]
        if done:
            self._refresh_rows()
        else:
            self.toplevel.after(_RETRY_POLL_MS, self._poll_retry_done)

    @classmethod
    def open(cls, root: tk.Tk, state_dir: Path) -> "AccountsManager":
        key = id(root)
        existing = _manager_instances.get(key)
        if existing is not None and existing.toplevel.winfo_exists():
            existing.toplevel.lift()
            existing.toplevel.focus_force()
            return existing
        manager = cls(root, state_dir)
        _manager_instances[key] = manager
        return manager

    def _on_close(self) -> None:
        _manager_instances.pop(id(self.root), None)
        self.toplevel.destroy()

    def _build(self) -> None:
        self._refresh_rows()
        tk.Label(
            self.toplevel,
            text="Tokitty restart needed for new panes. Claude Code session restart needed for hooks.",
            wraplength=360, justify="left",
        ).pack(padx=8, pady=(4, 10))
        # Add… is disabled on the virtual-row path: there is nothing to
        # add to on macOS when the only account is the Keychain itself.
        if not self._showing_virtual_macos_row():
            tk.Button(self.toplevel, text="Add…", command=self._on_add).pack(padx=8, pady=(0, 10))

    def _showing_virtual_macos_row(self) -> bool:
        return (
            load_accounts(self.state_dir) is None
            and sys.platform == "darwin"
            and not self._has_local_credentials_file()
        )

    def _has_local_credentials_file(self) -> bool:
        return (Path.home() / ".claude" / ".credentials.json").is_file()

    def _refresh_rows(self) -> None:
        for child in self.toplevel.winfo_children():
            if getattr(child, "_accounts_row", False):
                child.destroy()
        if self._showing_virtual_macos_row():
            self._render_virtual_macos_row()
            return
        accounts = load_accounts(self.state_dir) or []
        store = load_customization(self.state_dir)
        for row in build_row_specs(accounts, store):
            frame = tk.Frame(self.toplevel)
            frame._accounts_row = True
            tk.Label(frame, text=row.display_label).pack(side="left", padx=4)
            tk.Button(frame, text="Rename…", command=lambda s=row.slug: self._on_rename(s)).pack(side="left")
            remove_state = "normal" if row.remove_enabled else "disabled"
            tk.Button(frame, text="Remove", state=remove_state,
                      command=lambda s=row.slug, c=row.config_dir: self._on_remove(s, c)).pack(side="left")
            frame.pack(fill="x", padx=8, pady=2)

    def _render_virtual_macos_row(self) -> None:
        frame = tk.Frame(self.toplevel)
        frame._accounts_row = True
        tk.Label(frame, text="Default macOS account (Keychain)").pack(side="left", padx=4)
        tk.Button(frame, text="Rename…", command=self._on_rename_default_key).pack(side="left")
        tk.Button(frame, text="Remove", state="disabled").pack(side="left")
        frame.pack(fill="x", padx=8, pady=2)

    def _on_rename_default_key(self) -> None:
        result = simpledialog.askstring("Rename", "Cat name:", parent=self.toplevel)
        if result is None:
            return
        store = load_customization(self.state_dir)
        current = store.get(SINGLE_KEY, Customization())
        store[SINGLE_KEY] = replace(current, label=result)
        save_customization(self.state_dir, store)
        self._refresh_rows()

    def _on_rename(self, slug: str) -> None:
        result = simpledialog.askstring("Rename", "Cat name:", parent=self.toplevel)
        if result is not None:
            rename_account(self.state_dir, slug, result)
            self._refresh_rows()

    def _on_remove(self, slug: str, config_dir: str) -> None:
        accounts = load_accounts(self.state_dir) or []
        if len(accounts) <= 1:
            return
        remaining = [a for a in accounts if a.name != slug]
        remaining = reconcile_before_save(self.state_dir, remaining)
        remaining = [a for a in remaining if a.name != slug]

        def on_done(result):
            self.toplevel.after(0, self._refresh_rows)

        _run_mutation_off_thread(self.state_dir, remaining, "remove", config_dir, on_done)

    def _on_add(self) -> None:
        raw = simpledialog.askstring("Add account", "Claude config directory:", parent=self.toplevel)
        if not raw:
            return
        accounts = load_accounts(self.state_dir) or []
        active_dirs = [a.config_dir for a in accounts]
        validation = validate_manual_path(raw, active_config_dirs=active_dirs)
        if not validation.ok:
            messagebox.showerror("Add account", validation.error, parent=self.toplevel)
            return

        history = load_identity_history(self.state_dir)
        locator = canonicalize_locator(validation.config_dir)
        taken = set(history.values())
        slug, history = assign_identity_slug(locator, taken, history)
        save_identity_history(self.state_dir, history)

        was_implicit_only = not accounts
        new_account = Account(name=slug, config_dir=validation.config_dir)
        new_accounts = reconcile_before_save(self.state_dir, accounts) + [new_account]

        store = load_customization(self.state_dir)
        if was_implicit_only:
            store = absorb_implicit_default(store, slug)
        else:
            colorway, pattern = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))
            store[slug] = Customization(colorway=colorway, pattern=pattern)
        save_customization(self.state_dir, store)

        def on_done(result):
            self.toplevel.after(0, self._refresh_rows)

        _run_mutation_off_thread(self.state_dir, new_accounts, "install", validation.config_dir, on_done)
