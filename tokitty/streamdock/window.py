"""The in-window fallback: a small always-on-top Tk view of one permission request.

Opened when the deck can not show the full request itself (tab not focused, or too
few keys for the overlay). It shows the complete tool input, which is what makes
Allow on the deck safe, and answers through the runtime. Tk thread only.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Optional, Set

from tokitty.streamdock.pending import PendingRequest


def format_tool_input(tool_input: Any) -> str:
    """The whole input, pretty-printed. Never truncated: the reader is deciding on it."""
    try:
        return json.dumps(tool_input, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(tool_input)


def title_text(account_name: str, request: PendingRequest) -> str:
    return f"{account_name}: {request.tool_name}"


def always_label(request: PendingRequest) -> Optional[str]:
    """Text for the Always button, or None when the request has no narrow rule."""
    return f"Always: {request.always_rule}" if request.always_rule else None


def nonces_to_close(open_nonces: Iterable[str], pending_nonces: Iterable[str]) -> Set[str]:
    """Open windows whose request has left pending."""
    return set(open_nonces) - set(pending_nonces)


class InWindowViews:
    """One Toplevel per nonce, closed when its request leaves pending."""

    def __init__(
        self,
        root: Any,
        runtime_fn: Callable[[], Any],
        name_fn: Callable[[int], str],
        anchor_fn: Callable[[int], Optional[tuple]],
    ) -> None:
        self._root = root
        self._runtime_fn = runtime_fn
        self._name = name_fn
        self._anchor = anchor_fn
        self._open: dict = {}

    def open(self, request: PendingRequest) -> None:
        """The runtime's open_in_window hook."""
        runtime = self._runtime_fn()
        if runtime is None or request.nonce in self._open:
            return
        import tkinter as tk
        from tkinter import ttk

        win = tk.Toplevel(self._root)
        win.title(title_text(self._name(request.account_index), request))
        win.attributes("-topmost", True)
        anchor = self._anchor(request.account_index)
        if anchor:
            win.geometry(f"+{anchor[0]}+{anchor[1]}")
        win.protocol("WM_DELETE_WINDOW", lambda: self.close(request.nonce))
        self._open[request.nonce] = win

        ttk.Label(win, text=title_text(self._name(request.account_index), request)).pack(anchor="w", padx=8, pady=(8, 2))
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=8)
        text = tk.Text(body, width=64, height=14, wrap="word")
        scroll = ttk.Scrollbar(body, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", format_tool_input(request.tool_input))
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=8, pady=8)

        def answer(behavior: str) -> None:
            runtime.decide_from_window(request.nonce, behavior)
            self.close(request.nonce)

        ttk.Button(buttons, text="Allow", command=lambda: answer("allow")).pack(side="left")
        ttk.Button(buttons, text="Deny", command=lambda: answer("deny")).pack(side="left", padx=4)
        label = always_label(request)
        if label:
            ttk.Button(buttons, text=label, command=lambda: answer("always")).pack(side="left")
        runtime.in_window_opened(request.nonce)

    def sync(self) -> None:
        """Close the windows whose request is gone. Called from the Tk tick."""
        runtime = self._runtime_fn()
        if runtime is None:
            return
        for nonce in nonces_to_close(self._open, runtime.pending_nonces()):
            self.close(nonce)

    def close(self, nonce: str) -> None:
        win = self._open.pop(nonce, None)
        if win is None:
            return
        runtime = self._runtime_fn()
        if runtime is not None:
            runtime.in_window_closed(nonce)
        try:
            win.destroy()
        except Exception:
            pass

    def close_all(self) -> None:
        for nonce in list(self._open):
            self.close(nonce)
