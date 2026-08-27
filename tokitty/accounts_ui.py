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
from typing import Dict, List, Optional, Sequence, Tuple

from tokitty.accounts import (
    Account,
    AccountsLoadResult,
    backfill_identity_history,
    canonicalize_locator,
    load_accounts_result,
    load_identity_history,
    save_identity_history,
    assign_identity_slug,
)
from tokitty.customize import (
    Customization,
    SINGLE_KEY,
    load_customization,
    rename_account,
    save_customization_entry,
)
from tokitty.hooks_install import apply_account_mutation, retry_pending_hook_op
from tokitty.manual_path import validate_manual_path
from tokitty.migration import absorb_implicit_default
from tokitty.randomize import random_look
from tokitty import sprites
from tokitty.wsl_probe import (
    wsl_config_dir_from_credentials,
    wsl_sessions_dir_from_credentials,
)

_manager_instances: Dict[int, "AccountsManager"] = {}


@dataclass
class _InFlightOperation:
    kind: str
    lock: threading.Lock
    state: Dict[str, object]


_in_flight_operations: Dict[Path, _InFlightOperation] = {}
_in_flight_operations_lock = threading.Lock()

# How often __init__'s Tk-thread-owned poll checks whether the background
# pending-hook-op retry has finished (see AccountsManager.__init__). Short,
# since this only gates a one-shot row-refresh right after the dialog opens,
# not a continuous UI loop like __main__.py's UI_REFRESH_MS.
_RETRY_POLL_MS = 100
_MUTATION_POLL_MS = 100


@dataclass(frozen=True)
class RowSpec:
    slug: str
    config_dir: str
    display_label: str
    remove_enabled: bool


@dataclass(frozen=True)
class DiscoveredPathSpec:
    distro: str
    credentials_path: str
    config_dir: str
    sessions_dir: str


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


def build_discovered_path_specs(
    matches: Sequence[Tuple[str, str]],
) -> List[DiscoveredPathSpec]:
    """Convert discovery's WSL-side matches into manager-ready paths."""
    return [
        DiscoveredPathSpec(
            distro=distro,
            credentials_path=credentials_path,
            config_dir=wsl_config_dir_from_credentials(distro, credentials_path),
            sessions_dir=wsl_sessions_dir_from_credentials(distro, credentials_path),
        )
        for distro, credentials_path in matches
    ]


def reconcile_before_save(state_dir: Path, in_memory_accounts: List[Account]) -> List[Account]:
    """Reload accounts.json immediately before every save, so two
    independently opened dialogs cannot silently clobber each other's
    changes with a stale in-memory list."""
    result = load_accounts_result(state_dir)
    if result.state == "malformed":
        raise ValueError("accounts.json became malformed before it could be saved")
    if result.state in ("valid_empty", "valid_non_empty"):
        return result.accounts
    return in_memory_accounts


def _state_dir_key(state_dir: Path) -> Path:
    return state_dir.resolve()


def _claim_operation(
    state_dir: Path, kind: str
) -> Tuple[Path, _InFlightOperation, bool]:
    """Claim the single process-wide hook-operation slot for state_dir."""
    key = _state_dir_key(state_dir)
    with _in_flight_operations_lock:
        existing = _in_flight_operations.get(key)
        if existing is not None:
            return key, existing, False
        operation = _InFlightOperation(
            kind=kind,
            lock=threading.Lock(),
            state={"done": False},
        )
        _in_flight_operations[key] = operation
        return key, operation, True


def _complete_operation(
    key: Path, operation: _InFlightOperation, outcome: object
) -> None:
    """Publish an outcome and release the shared slot from the worker."""
    with operation.lock:
        if operation.state["done"]:
            return
        operation.state["outcome"] = outcome
        operation.state["done"] = True
    with _in_flight_operations_lock:
        if _in_flight_operations.get(key) is operation:
            _in_flight_operations.pop(key)


def _run_mutation_off_thread(state_dir: Path, accounts: List[Account], op: str,
                              config_dir: str, on_done) -> None:
    def worker():
        try:
            outcome = apply_account_mutation(state_dir, accounts, op, config_dir)
        except Exception as exc:
            outcome = exc
        on_done(outcome)

    threading.Thread(target=worker, daemon=True).start()


def _run_retry_off_thread(state_dir: Path, on_done) -> None:
    try:
        outcome = retry_pending_hook_op(state_dir)
    except Exception as exc:
        # A failed retry leaves the pending-op record in place for next time,
        # exactly like any other failed retry_pending_hook_op call.
        outcome = exc
    on_done(outcome)


class AccountsManager:
    """Singleton Toplevel per root: AccountsManager.open() raises the
    existing dialog instead of creating a second one."""

    def __init__(
        self,
        root: tk.Tk,
        state_dir: Path,
        discovered_matches: Optional[Sequence[Tuple[str, str]]] = None,
    ):
        self.root = root
        self.state_dir = state_dir
        self._pending_hook_failure = False
        self._retry_after_id = None
        self._mutation_after_id = None
        self._discovered_paths = build_discovered_path_specs(discovered_matches or [])
        initial_accounts = load_accounts_result(state_dir)
        if initial_accounts.state != "malformed":
            backfill_identity_history(state_dir, initial_accounts.accounts)
        self.toplevel = tk.Toplevel(root)
        self.toplevel.title("Accounts")
        self.toplevel.transient(root)
        self.toplevel.resizable(False, False)
        self.toplevel.protocol("WM_DELETE_WINDOW", self._on_close)
        self.toplevel.bind("<Destroy>", self._on_toplevel_destroy, add="+")
        state_key, operation, claimed = _claim_operation(state_dir, "retry")
        self._state_key = state_key
        self._mutation_in_flight = operation.kind == "mutation"
        self._retry_in_flight = operation.kind == "retry"
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

    def _watch_operation(self, operation: _InFlightOperation) -> None:
        """Poll an operation, including one started by an old dialog."""
        self._mutation_in_flight = operation.kind == "mutation"
        self._retry_in_flight = operation.kind == "retry"
        if operation.kind == "mutation":
            self._mutation_operation = operation
            self._mutation_lock = operation.lock
            self._mutation_state = operation.state
            if getattr(self, "_mutation_after_id", None) is None:
                self._mutation_after_id = self.toplevel.after(
                    _MUTATION_POLL_MS, self._poll_mutation_done
                )
        else:
            self._retry_lock = operation.lock
            self._retry_state = operation.state
            if getattr(self, "_retry_after_id", None) is None:
                self._retry_after_id = self.toplevel.after(
                    _RETRY_POLL_MS, self._poll_retry_done
                )
        self._update_mutation_controls()

    def _poll_retry_done(self) -> None:
        self._retry_after_id = None
        if not self.toplevel.winfo_exists():
            return  # manager was closed before the retry finished
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
                messagebox.showerror(
                    "Accounts",
                    "A pending hook update could not be completed. "
                    f"Reopen Accounts to retry it before making another change. {detail}",
                    parent=self.toplevel,
                )
        else:
            self._retry_after_id = self.toplevel.after(
                _RETRY_POLL_MS, self._poll_retry_done
            )

    @classmethod
    def open(
        cls,
        root: tk.Tk,
        state_dir: Path,
        discovered_matches: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> "AccountsManager":
        key = id(root)
        existing = _manager_instances.get(key)
        if existing is not None and existing.toplevel.winfo_exists():
            if discovered_matches is not None:
                existing._discovered_paths = build_discovered_path_specs(discovered_matches)
                existing._refresh_rows()
            existing.toplevel.lift()
            existing.toplevel.focus_force()
            return existing
        manager = cls(root, state_dir, discovered_matches=discovered_matches)
        _manager_instances[key] = manager
        return manager

    def _on_close(self) -> None:
        self.toplevel.destroy()

    def _cancel_pending_callback(self, attribute: str) -> None:
        after_id = getattr(self, attribute, None)
        if after_id is None:
            return
        setattr(self, attribute, None)
        try:
            self.toplevel.after_cancel(after_id)
        except tk.TclError:
            # Destruction may already have removed the Tcl command.  The
            # callback identifier is still cleared so cleanup is idempotent.
            pass

    def _on_toplevel_destroy(self, event) -> None:
        if event.widget is not self.toplevel:
            return
        self._cancel_pending_callback("_retry_after_id")
        self._cancel_pending_callback("_mutation_after_id")
        _manager_instances.pop(id(self.root), None)

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
            button = tk.Button(self.toplevel, text="Add by path…", command=self._on_add)
            button._account_mutation_control = True
            button._account_enabled = True
            button.pack(padx=8, pady=(0, 10))
        self._update_mutation_controls()

    def _showing_virtual_macos_row(self) -> bool:
        return (
            load_accounts_result(self.state_dir).state == "absent"
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
        result = load_accounts_result(self.state_dir)
        if result.state == "malformed":
            self._render_malformed_row()
            self._update_mutation_controls()
            return
        accounts = result.accounts
        store = load_customization(self.state_dir)
        for row in build_row_specs(accounts, store):
            frame = tk.Frame(self.toplevel)
            frame._accounts_row = True
            tk.Label(frame, text=row.display_label).pack(side="left", padx=4)
            tk.Button(frame, text="Rename…", command=lambda s=row.slug: self._on_rename(s)).pack(side="left")
            remove_state = "normal" if row.remove_enabled else "disabled"
            remove = tk.Button(frame, text="Remove", state=remove_state,
                               command=lambda s=row.slug, c=row.config_dir: self._on_remove(s, c))
            remove._account_mutation_control = True
            remove._account_enabled = row.remove_enabled
            remove.pack(side="left")
            frame.pack(fill="x", padx=8, pady=2)
        self._render_discovered_rows()
        self._update_mutation_controls()

    def _render_discovered_rows(self) -> None:
        for discovered in self._discovered_paths:
            frame = tk.Frame(self.toplevel)
            frame._accounts_row = True
            tk.Label(
                frame,
                text=f"Found in {discovered.distro}: {discovered.config_dir}",
                wraplength=300,
                justify="left",
            ).pack(side="left", padx=4)
            button = tk.Button(
                frame,
                text="Add",
                command=lambda path=discovered.config_dir: self._on_add_discovered(path),
            )
            button._account_mutation_control = True
            button._account_enabled = True
            button.pack(side="left")
            frame.pack(fill="x", padx=8, pady=2)

    def _render_malformed_row(self) -> None:
        frame = tk.Frame(self.toplevel)
        frame._accounts_row = True
        tk.Label(
            frame,
            text="accounts.json is malformed. Fix or delete it before managing accounts.",
            wraplength=340,
            justify="left",
        ).pack(side="left", padx=4)
        frame.pack(fill="x", padx=8, pady=2)

    def _update_mutation_controls(self) -> None:
        def visit(widget) -> None:
            for child in widget.winfo_children():
                if getattr(child, "_account_mutation_control", False):
                    enabled = getattr(child, "_account_enabled", True)
                    child.configure(
                        state="normal"
                        if enabled
                        and not self._mutation_in_flight
                        and not self._retry_in_flight
                        and not self._pending_hook_failure
                        else "disabled"
                    )
                visit(child)

        visit(self.toplevel)

    def _load_accounts_for_mutation(self) -> Optional[AccountsLoadResult]:
        result = load_accounts_result(self.state_dir)
        if result.state == "malformed":
            messagebox.showerror(
                "Accounts",
                "accounts.json is malformed. Fix or delete it before adding or removing accounts.",
                parent=self.toplevel,
            )
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
            messagebox.showerror(
                "Accounts",
                "The hook update failed. Reopen Accounts to retry it before "
                f"making another change. {outcome}",
                parent=self.toplevel,
            )
        elif not outcome.ok:
            messagebox.showerror(
                "Accounts",
                "The hook update failed. Reopen Accounts to retry it before "
                f"making another change. {outcome.message}",
                parent=self.toplevel,
            )

    def _poll_mutation_done(self) -> None:
        self._mutation_after_id = None
        if not self.toplevel.winfo_exists():
            return
        with self._mutation_lock:
            done = self._mutation_state["done"]
            outcome = self._mutation_state.get("outcome")
        if done:
            self._finish_mutation(outcome)
        else:
            self._mutation_after_id = self.toplevel.after(
                _MUTATION_POLL_MS, self._poll_mutation_done
            )

    def _start_mutation(
        self, accounts: List[Account], op: str, config_dir: str
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
                self.state_dir, accounts, op, config_dir, on_done
            )
        except Exception as exc:
            _complete_operation(state_key, operation, exc)

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
        save_customization_entry(self.state_dir, SINGLE_KEY, replace(current, label=result))
        self._refresh_rows()

    def _on_rename(self, slug: str) -> None:
        result = simpledialog.askstring("Rename", "Cat name:", parent=self.toplevel)
        if result is not None:
            rename_account(self.state_dir, slug, result)
            self._refresh_rows()

    def _on_remove(self, slug: str, config_dir: str) -> None:
        if (
            self._retry_in_flight
            or self._mutation_in_flight
            or self._pending_hook_failure
        ):
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is None:
            return
        accounts = load_result.accounts
        if len(accounts) <= 1:
            return
        remaining = [a for a in accounts if a.name != slug]
        try:
            remaining = reconcile_before_save(self.state_dir, remaining)
        except ValueError as exc:
            messagebox.showerror("Accounts", str(exc), parent=self.toplevel)
            return
        remaining = [a for a in remaining if a.name != slug]

        self._start_mutation(remaining, "remove", config_dir)

    def _on_add(self) -> None:
        if (
            self._retry_in_flight
            or self._mutation_in_flight
            or self._pending_hook_failure
        ):
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is None:
            return
        raw = simpledialog.askstring("Add account", "Claude config directory:", parent=self.toplevel)
        if not raw:
            return
        self._add_path(raw, load_result)

    def _on_add_discovered(self, config_dir: str) -> None:
        if (
            self._retry_in_flight
            or self._mutation_in_flight
            or self._pending_hook_failure
        ):
            return
        load_result = self._load_accounts_for_mutation()
        if load_result is not None:
            self._add_path(config_dir, load_result)

    def _add_path(self, raw: str, load_result: AccountsLoadResult) -> None:
        accounts = load_result.accounts
        active_dirs = [a.config_dir for a in accounts]
        validation = validate_manual_path(raw, active_config_dirs=active_dirs)
        if not validation.ok:
            messagebox.showerror("Add account", validation.error, parent=self.toplevel)
            return

        try:
            accounts = reconcile_before_save(self.state_dir, accounts)
        except ValueError as exc:
            messagebox.showerror("Accounts", str(exc), parent=self.toplevel)
            return

        history = load_identity_history(self.state_dir)
        locator = canonicalize_locator(validation.config_dir)
        taken = set(history.values()) | {account.name for account in accounts}
        slug, history = assign_identity_slug(locator, taken, history)
        save_identity_history(self.state_dir, history)

        was_implicit_only = load_result.state == "absent"
        new_account = Account(name=slug, config_dir=validation.config_dir)
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

        self._start_mutation(new_accounts, "install", validation.config_dir)
