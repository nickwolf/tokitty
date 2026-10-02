"""The "New-session presets" dialog. Thin Tk over tokitty.streamdock.presets."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Dict, List, Optional

from tokitty.accounts import Account
from tokitty.settings import load_settings
from tokitty.streamdock.presets import (
    account_label,
    check_preset,
    claude_accounts,
    is_wsl,
    preset_for,
    save_presets,
)

WSL_HINT = "A path inside WSL, such as /mnt/c/Tools or ~/repo"

_instances: Dict[int, "PresetsDialog"] = {}


class PresetsDialog:
    """One Toplevel per root. open() raises the existing one."""

    def __init__(
        self,
        root: tk.Tk,
        state_dir: Path,
        accounts_fn: Callable[[], List[Optional[Account]]],
        label_fn: Callable[[int], Optional[str]],
        on_saved: Callable[[List[dict]], None],
    ) -> None:
        self.root = root
        self.state_dir = state_dir
        self._accounts_fn = accounts_fn
        self._label_fn = label_fn
        self._on_saved = on_saved
        self._presets: List[dict] = load_settings(state_dir).streamdock_presets
        self._form: Optional[tk.Frame] = None
        self.toplevel = tk.Toplevel(root)
        self.toplevel.title("New-session presets")
        self.toplevel.transient(root)
        self.toplevel.resizable(False, False)
        columns = ("name", "account", "folder")
        self.tree = ttk.Treeview(self.toplevel, columns=columns, show="headings", height=6, selectmode="browse")
        for column, width in zip(columns, (120, 220, 220)):
            self.tree.heading(column, text=column.capitalize())
            self.tree.column(column, width=width, anchor="w")
        self.tree.grid(row=0, column=0, columnspan=4, padx=8, pady=8)
        tk.Button(self.toplevel, text="Add", command=self._add).grid(row=1, column=0, padx=8, pady=(0, 8))
        tk.Button(self.toplevel, text="Edit", command=self._edit).grid(row=1, column=1, pady=(0, 8))
        tk.Button(self.toplevel, text="Remove", command=self._remove).grid(row=1, column=2, pady=(0, 8))
        tk.Button(self.toplevel, text="Close", command=self.toplevel.destroy).grid(
            row=1, column=3, padx=8, pady=(0, 8)
        )
        self._refresh()

    @classmethod
    def open(cls, root, state_dir, accounts_fn, label_fn, on_saved) -> "PresetsDialog":
        existing = _instances.get(id(root))
        if existing is not None and existing.toplevel.winfo_exists():
            existing.toplevel.lift()
            existing.toplevel.focus_force()
            return existing
        dialog = cls(root, state_dir, accounts_fn, label_fn, on_saved)
        _instances[id(root)] = dialog
        return dialog

    def _account_for(self, preset: dict) -> Optional[Account]:
        accounts = self._accounts_fn()
        for index, account in enumerate(accounts):
            if account is not None and account.name == preset.get("account"):
                return account
        index = preset.get("account_index")
        if "account" not in preset and isinstance(index, int) and 0 <= index < len(accounts):
            return accounts[index]
        return None

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        accounts = self._accounts_fn()
        for position, preset in enumerate(self._presets):
            account = self._account_for(preset)
            if account is None:
                shown = "(account missing)"
            else:
                shown = account_label(accounts.index(account), account, self._label_fn(accounts.index(account)))
            self.tree.insert("", "end", iid=str(position), values=(preset["name"], shown, preset["cwd"]))

    def _selected(self) -> Optional[int]:
        picked = self.tree.selection()
        return int(picked[0]) if picked else None

    def _add(self) -> None:
        self._open_form(None)

    def _edit(self) -> None:
        position = self._selected()
        if position is not None:
            self._open_form(position)

    def _remove(self) -> None:
        position = self._selected()
        if position is None:
            return
        self._save(self._presets[:position] + self._presets[position + 1:])

    def _save(self, presets: List[dict]) -> None:
        self._presets = save_presets(self.state_dir, presets)
        self._on_saved(list(self._presets))
        self._refresh()

    def _open_form(self, position: Optional[int]) -> None:
        if self._form is not None and self._form.winfo_exists():
            self._form.destroy()
        accounts = self._accounts_fn()
        offered = claude_accounts(accounts)
        existing = self._presets[position] if position is not None else None
        labels = [account_label(i, a, self._label_fn(i)) for i, a in offered]
        form = tk.Toplevel(self.toplevel)
        self._form = form
        form.title("Edit preset" if existing else "Add preset")
        form.transient(self.toplevel)
        form.resizable(False, False)

        name_var = tk.StringVar(value=existing["name"] if existing else "")
        cwd_var = tk.StringVar(value=existing["cwd"] if existing else "")
        account_var = tk.StringVar()
        error_var = tk.StringVar()
        if labels:
            current = self._account_for(existing) if existing else None
            account_var.set(labels[0])
            for label, (_, account) in zip(labels, offered):
                if account is current:
                    account_var.set(label)

        tk.Label(form, text="Name").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        tk.Entry(form, textvariable=name_var, width=40).grid(row=0, column=1, columnspan=2, padx=8, pady=6)
        tk.Label(form, text="Account").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        combo = ttk.Combobox(form, textvariable=account_var, values=labels, state="readonly", width=38)
        combo.grid(row=1, column=1, columnspan=2, padx=8, pady=6)
        tk.Label(form, text="Folder").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        tk.Entry(form, textvariable=cwd_var, width=32).grid(row=2, column=1, padx=(8, 0), pady=6)
        browse = tk.Button(form, text="Browse…", command=lambda: self._browse(form, cwd_var))
        browse.grid(row=2, column=2, padx=8, pady=6)
        hint = tk.Label(form, text=WSL_HINT, wraplength=320, justify="left")
        hint.grid(row=3, column=1, columnspan=2, sticky="w", padx=8)
        tk.Label(form, textvariable=error_var, fg="#c0392b", wraplength=320, justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=8, pady=4
        )

        def chosen() -> Optional[Account]:
            if account_var.get() in labels:
                return offered[labels.index(account_var.get())][1]
            return None

        def sync_account(*_args) -> None:
            account = chosen()
            wsl = account is not None and is_wsl(account)
            browse.configure(state="disabled" if wsl else "normal")
            if wsl:
                hint.grid()
            else:
                hint.grid_remove()

        combo.bind("<<ComboboxSelected>>", sync_account)
        sync_account()

        def save() -> None:
            account = chosen()
            if account is None:
                error_var.set("No Claude account to launch on")
                return
            preset = preset_for(name_var.get(), account, cwd_var.get(), accounts)
            others = [p for i, p in enumerate(self._presets) if i != position]
            problem = check_preset(preset, account, others)
            if problem:
                error_var.set(problem)
                return
            updated = list(self._presets)
            if position is None:
                updated.append(preset)
            else:
                updated[position] = preset
            self._save(updated)
            form.destroy()

        tk.Button(form, text="Save", command=save).grid(row=5, column=1, sticky="e", padx=8, pady=(4, 10))
        tk.Button(form, text="Cancel", command=form.destroy).grid(row=5, column=2, padx=8, pady=(4, 10))

    @staticmethod
    def _browse(parent: tk.Misc, var: tk.StringVar) -> None:
        picked = filedialog.askdirectory(parent=parent, initialdir=var.get() or None)
        if picked:
            var.set(picked.replace("/", "\\"))
