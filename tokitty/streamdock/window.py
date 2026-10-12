"""The in-window fallback: a small always-on-top Tk view of one permission request.

Opened when the deck can not show the full request itself (tab not focused, or too
few keys for the overlay). It shows the complete tool input, which is what makes
Allow on the deck safe, and answers through the runtime. The bookkeeping here is
toolkit-free; tk_view_factory builds the Tk windows. Tk thread only.
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
    return f"Tokitty: {account_name} permission request"


_REASONS = {
    "ambiguous": "Tokitty can't tell which Windows Terminal tab is this session's{title}, because another tab or session has the same title.",
    "not_found": "Tokitty couldn't find this session's tab{title} in Windows Terminal.",
    "no_title": "This session has no title yet, so Tokitty can't find its tab.",
    "not_front": "Tokitty found this session's tab{title} but couldn't bring it to the front.",
    "unavailable": "Tokitty can't switch Windows Terminal tabs on this machine.",
    "few_keys": "The Stream Dock page has too few free keys to show this request.",
}


def explain_text(reason: str, session_title: Optional[str], request: PendingRequest) -> str:
    """Why this window opened and what the reader is being asked to do."""
    title = f' ("{session_title}")' if session_title else ""
    why = _REASONS.get(reason, "Tokitty couldn't show this request on the Stream Dock.").format(title=title)
    return (
        f"{why} So it is asking here instead.\n\n"
        f"Claude Code wants to use {request.tool_name} with the input below. "
        "Allow or Deny it here, or answer in the terminal."
    )


def always_label(request: PendingRequest) -> Optional[str]:
    """Text for the Always button, or None when the request has no narrow rule."""
    return f"Always: {request.always_rule}" if request.always_rule else None


def nonces_to_close(open_nonces: Iterable[str], pending_nonces: Iterable[str]) -> Set[str]:
    """Open windows whose request has left pending."""
    return set(open_nonces) - set(pending_nonces)


class InWindowViews:
    """One view per nonce, closed when its request leaves pending.

    make_view(request, title, explain, anchor, on_answer, on_close) returns an object
    with close(). on_answer(behavior) is for the buttons, on_close() for the window's
    own close button.
    """

    def __init__(
        self,
        runtime_fn: Callable[[], Any],
        name_fn: Callable[[int], str],
        anchor_fn: Callable[[int], Optional[tuple]],
        make_view: Callable[..., Any],
    ) -> None:
        self._runtime_fn = runtime_fn
        self._name = name_fn
        self._anchor = anchor_fn
        self._make_view = make_view
        self._open: dict = {}

    def open(self, request: PendingRequest, reason: str = "") -> None:
        """The runtime's open_in_window hook."""
        runtime = self._runtime_fn()
        if runtime is None or request.nonce in self._open:
            return
        nonce = request.nonce

        def on_answer(behavior: str) -> None:
            runtime.decide_from_window(nonce, behavior)
            self.close(nonce)

        self._open[nonce] = self._make_view(
            request,
            title_text(self._name(request.account_index), request),
            explain_text(reason, runtime.session_title(request), request),
            self._anchor(request.account_index),
            on_answer,
            lambda: self.close(nonce),
        )
        runtime.in_window_opened(nonce)

    def sync(self) -> None:
        """Close the windows whose request is gone. Called from the Tk tick."""
        runtime = self._runtime_fn()
        if runtime is None:
            return
        for nonce in nonces_to_close(self._open, runtime.pending_nonces()):
            self.close(nonce)

    def close(self, nonce: str) -> None:
        view = self._open.pop(nonce, None)
        if view is None:
            return
        runtime = self._runtime_fn()
        if runtime is not None:
            runtime.in_window_closed(nonce)
        try:
            view.close()
        except Exception:
            pass

    def close_all(self) -> None:
        for nonce in list(self._open):
            self.close(nonce)


class _TkView:
    def __init__(self, win: Any) -> None:
        self._win = win

    def close(self) -> None:
        self._win.destroy()


def tk_view_factory(root: Any) -> Callable[..., Any]:
    """A make_view for InWindowViews that builds an always-on-top Toplevel."""

    def make_view(request: PendingRequest, title: str, explain: str, anchor: Optional[tuple],
                  on_answer: Callable[[str], None], on_close: Callable[[], None]) -> _TkView:
        import tkinter as tk
        from tkinter import ttk

        win = tk.Toplevel(root)
        win.title(title)
        win.attributes("-topmost", True)
        if anchor:
            win.geometry(f"+{anchor[0]}+{anchor[1]}")
        win.protocol("WM_DELETE_WINDOW", on_close)
        view = _TkView(win)

        ttk.Label(win, text=explain, wraplength=440, justify="left").pack(anchor="w", padx=8, pady=(8, 6))
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
        ttk.Button(buttons, text="Allow", command=lambda: on_answer("allow")).pack(side="left")
        ttk.Button(buttons, text="Deny", command=lambda: on_answer("deny")).pack(side="left", padx=4)
        label = always_label(request)
        if label:
            ttk.Button(buttons, text=label, command=lambda: on_answer("always")).pack(side="left")
        return view

    return make_view
