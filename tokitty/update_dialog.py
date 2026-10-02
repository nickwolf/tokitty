"""The update confirm dialog and the menu actions that lead to it (#77).

Tk thread only. `UpdateUi` turns the "Update to vX.Y.Z…" and "Check for
updates" items into dialogs; `UpdateDialog` is the one window that offers the
install and shows its progress. The install itself is `UpdateController`'s.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from typing import Callable, List, Optional, Tuple

from tokitty.ui import BG_COLOR, FG_COLOR
from tokitty.update_check import CheckResult
from tokitty.updater import REPO, Release

RELEASES_URL = f"https://github.com/{REPO}/releases"
WRAP_PX = 340


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
        self._release = release
        self._controller = controller
        self._open_url = open_url
        # refusal() writes and removes a probe file, so ask once, here.
        self._reason: Optional[str] = controller.refusal(release)
        self.alive = True

        self.toplevel = tk.Toplevel(root)
        self.toplevel.title("Update Tokitty")
        self.toplevel.transient(root)
        self.toplevel.configure(bg=BG_COLOR)
        self.toplevel.resizable(False, False)
        self.toplevel.protocol("WM_DELETE_WINDOW", self._close_requested)
        tk.Label(
            self.toplevel, fg=FG_COLOR, bg=BG_COLOR, justify="left",
            text=f"Tokitty {running} is installed.\n{release.tag} is available.",
        ).pack(anchor="w", padx=12, pady=(12, 4))
        self._note = tk.Label(self.toplevel, fg=FG_COLOR, bg=BG_COLOR, justify="left", wraplength=WRAP_PX)
        self._note.pack(anchor="w", padx=12, pady=4)
        self._row = tk.Frame(self.toplevel, bg=BG_COLOR)
        self._row.pack(anchor="e", padx=12, pady=(4, 12))
        self._show_choices(self._reason or "")

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

    def _set(self, note: str, buttons: List[Tuple[str, Callable[[], None]]]) -> None:
        self._note.configure(text=note)
        for old in self._row.winfo_children():
            old.destroy()
        for text, command in buttons:
            tk.Button(self._row, text=text, command=command).pack(side="left", padx=4)

    def _show_choices(self, note: str) -> None:
        if self._reason:
            # Release notes would open the same page, so it isn't offered twice.
            choices = [("Open release page", self._release_page)]
        else:
            choices = [("Install", self._install), ("Release notes", self._release_page)]
        self._set(note, choices + [("Later", self._close_requested)])

    def _release_page(self) -> None:
        self._open_url(self._release.html_url or RELEASES_URL)

    def _install(self) -> None:
        if not self._controller.start_install(self._release, self._on_progress, self._on_done):
            self._show_choices("An update is already in progress.")
            return
        self._set("Preparing…", [("Cancel", self._cancel)])

    def _cancel(self) -> None:
        self._controller.cancel()
        self._set("Cancelling…", [])

    def _on_progress(self, done: int, total: int) -> None:
        if not self.alive or self.note == "Cancelling…":
            return
        if total <= 0:
            text = "Downloading…"
        elif done >= total:
            text = "Checking the download…"
        else:
            text = f"Downloading… {done * 100 // total}%"
        self._note.configure(text=text)

    def _on_done(self, result) -> None:
        if not self.alive:
            return
        if result.outcome == "installed":
            self._set(f"Updated to {result.tag}. Restarting…", [])
        elif result.outcome == "rolled_back":
            # The controller's own notice says what happened.
            self.destroy()
        elif result.outcome == "cancelled":
            self._show_choices("Cancelled.")
        else:
            self._show_choices(result.message or "The update failed.")

    def _close_requested(self) -> None:
        if not self._controller.busy:
            self.destroy()

    def destroy(self) -> None:
        self.alive = False
        if UpdateDialog._current is self:
            UpdateDialog._current = None
        try:
            self.toplevel.destroy()
        except tk.TclError:
            pass


class UpdateUi:
    """The menu actions. Result callbacks arrive from tick(), so the modal
    boxes are deferred with root.after(0, ...) to keep tick() from stalling."""

    def __init__(self, root, checker, controller, open_url=webbrowser.open):
        self._root = root
        self._checker = checker
        self._controller = controller
        self._open_url = open_url
        self._checking = False

    def install(self) -> None:
        """The "Update to vX.Y.Z…" item. With no release in hand (the tag came
        from an earlier run) it checks first."""
        release = self._checker.latest_release
        if release is not None and self._checker.update_available:
            self._offer(release)
        else:
            self.check_now()

    def check_now(self) -> None:
        if self._checking:
            return
        self._checking = True
        self._checker.check_now(self._on_result)

    def _on_result(self, result: CheckResult) -> None:
        self._checking = False
        self._root.after(0, lambda: self._present(result))

    def _present(self, result: CheckResult) -> None:
        from tkinter import messagebox

        if result.error:
            messagebox.showerror("Check for updates", f"Couldn't check for updates: {result.error}", parent=self._root)
        elif result.newer and result.release is not None:
            self._offer(result.release)
        else:
            messagebox.showinfo(
                "Check for updates",
                f"Tokitty {self._controller.running.version} is the latest version.",
                parent=self._root,
            )

    def _offer(self, release: Release) -> None:
        UpdateDialog.open(self._root, release, self._controller.running.version, self._controller, self._open_url)
