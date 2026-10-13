"""The update confirm dialog and the menu actions that lead to it (#77).

Tk thread only. `UpdateUi` turns the "Update to vX.Y.Z…" and "Check for
updates" items into dialogs; `UpdateDialog` is the one window that offers the
install and shows its progress. The rules and texts are `update_flow`'s, shared
with the Qt dialog; these classes only draw them. The install itself is
`UpdateController`'s.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from typing import List, Optional

from tokitty.ui import BG_COLOR, FG_COLOR
from tokitty.update_flow import RELEASES_URL, WRAP_PX, Buttons, UpdateActions, UpdateFlow  # noqa: F401  (re-exported)
from tokitty.updater import Release


class UpdateDialog:
    _current: Optional["UpdateDialog"] = None

    @classmethod
    def open(cls, root, release: Release, running: str, controller, open_url=webbrowser.open) -> "UpdateDialog":
        """Show the dialog, or raise the one already open: there is only ever one."""
        existing = cls._current
        if existing is not None and existing.alive:
            try:
                existing.toplevel.lift()
                existing.toplevel.focus_force()
                return existing
            except tk.TclError:
                pass  # its root is gone; build a fresh one
        dialog = cls(root, release, running, controller, open_url)
        cls._current = dialog
        return dialog

    def __init__(self, root, release: Release, running: str, controller, open_url=webbrowser.open):
        self.toplevel = tk.Toplevel(root)
        self.toplevel.title("Update Tokitty")
        self.toplevel.transient(root)
        self.toplevel.configure(bg=BG_COLOR)
        self.toplevel.resizable(False, False)
        self.toplevel.protocol("WM_DELETE_WINDOW", self._close_requested)
        self._info = tk.Label(self.toplevel, fg=FG_COLOR, bg=BG_COLOR, justify="left")
        self._info.pack(anchor="w", padx=12, pady=(12, 4))
        self._note = tk.Label(self.toplevel, fg=FG_COLOR, bg=BG_COLOR, justify="left", wraplength=WRAP_PX)
        self._note.pack(anchor="w", padx=12, pady=4)
        self._row = tk.Frame(self.toplevel, bg=BG_COLOR)
        self._row.pack(anchor="e", padx=12, pady=(4, 12))
        # The flow shows its first choices as it is built, so the widgets come first.
        self._flow = UpdateFlow(release, running, controller, self, open_url)
        self._info.configure(text=self._flow.info_text)

    @property
    def alive(self) -> bool:
        return self._flow.alive

    # What the dialog shows, for tests and for the controller callbacks.
    @property
    def note(self) -> str:
        return self._note.cget("text")

    def button_labels(self) -> List[str]:
        return [button.cget("text") for button in self._row.winfo_children()]

    def invoke(self, label: str) -> None:
        for button in self._row.winfo_children():
            if button.cget("text") == label:
                button.invoke()
                return
        raise LookupError(label)

    # The renderer the flow draws on.
    def show(self, note: str, buttons: Optional[Buttons]) -> None:
        self._note.configure(text=note)
        if buttons is None:
            return
        for old in self._row.winfo_children():
            old.destroy()
        for text, command in buttons:
            tk.Button(self._row, text=text, command=command).pack(side="left", padx=4)

    def close(self) -> None:
        if UpdateDialog._current is self:
            UpdateDialog._current = None
        try:
            self.toplevel.destroy()
        except tk.TclError:
            pass

    def _close_requested(self) -> None:
        self._flow.close_requested()

    def destroy(self) -> None:
        self._flow.close()


class UpdateUi:
    """The menu actions. Result callbacks arrive from tick(), so the modal
    boxes are deferred with root.after(0, ...) to keep tick() from stalling."""

    def __init__(self, root, checker, controller, open_url=webbrowser.open):
        self._root = root
        self._controller = controller
        self._open_url = open_url
        self._actions = UpdateActions(
            checker,
            controller,
            defer=lambda fn: root.after(0, fn),
            error=self._error,
            info=self._info,
            offer=self._offer,
        )

    def install(self) -> None:
        self._actions.install()

    def check_now(self) -> None:
        self._actions.check_now()

    def _error(self, title: str, text: str) -> None:
        from tkinter import messagebox

        messagebox.showerror(title, text, parent=self._root)

    def _info(self, title: str, text: str) -> None:
        from tkinter import messagebox

        messagebox.showinfo(title, text, parent=self._root)

    def _offer(self, release: Release) -> None:
        UpdateDialog.open(self._root, release, self._controller.running.version, self._controller, self._open_url)
