"""Tkinter window: chrome, drag, always-on-top, right-click menu,
position persistence. Rendering (bars, cat frames) is added on top of
this shell in a follow-up commit. The only module in this package that
imports tkinter.
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path

from tokitty.geometry import clamp_position

CARD_WIDTH = 300
CARD_HEIGHT = 110
BG_COLOR = "#1c1c22"
FG_COLOR = "#f0f0f0"
DIM_COLOR = "#8a8a92"
BAR_BG = "#333340"

POSITION_FILENAME = "position.json"


class TokittyWindow:
    def __init__(self, root: tk.Tk, state_dir: Path):
        self.root = root
        self.state_dir = state_dir
        self._position_path = state_dir / POSITION_FILENAME
        self._drag_offset = (0, 0)
        self._always_on_top = tk.BooleanVar(value=True)
        self.on_refresh_requested = None  # set externally by __main__.py

        self._configure_window()
        self._build_widgets()
        self._restore_position()
        self._bind_drag()
        self._build_context_menu()

    def _configure_window(self) -> None:
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG_COLOR)
        self.root.geometry(f"{CARD_WIDTH}x{CARD_HEIGHT}")

        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

    def _build_widgets(self) -> None:
        self.canvas = tk.Canvas(self.root, width=100, height=100, bg=BG_COLOR, highlightthickness=0)
        self.canvas.place(x=6, y=5)

        self.tag_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.tag_label.place(x=8, y=108)

        self.session_label = tk.Label(self.root, text="SESSION", fg=FG_COLOR, bg=BG_COLOR, font=("Segoe UI", 9, "bold"))
        self.session_label.place(x=112, y=8)
        self.session_bar_bg = tk.Canvas(self.root, width=170, height=8, bg=BAR_BG, highlightthickness=0)
        self.session_bar_bg.place(x=112, y=26)
        self.session_reset_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.session_reset_label.place(x=112, y=38)

        self.weekly_label = tk.Label(self.root, text="WEEK", fg=FG_COLOR, bg=BG_COLOR, font=("Segoe UI", 9, "bold"))
        self.weekly_label.place(x=112, y=54)
        self.weekly_bar_bg = tk.Canvas(self.root, width=170, height=8, bg=BAR_BG, highlightthickness=0)
        self.weekly_bar_bg.place(x=112, y=72)
        self.weekly_reset_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.weekly_reset_label.place(x=112, y=84)

        self.credits_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.credits_label.place(x=112, y=96)

        self.hint_label = tk.Label(self.root, text="", fg=DIM_COLOR, bg=BG_COLOR, font=("Segoe UI", 8))
        self.hint_label.place(x=8, y=86)

    def _bind_drag(self) -> None:
        self.root.bind("<Button-1>", self._on_drag_start)
        self.root.bind("<B1-Motion>", self._on_drag_move)
        self.root.bind("<ButtonRelease-1>", self._on_drag_end)

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_offset = (event.x, event.y)

    def _on_drag_move(self, event: tk.Event) -> None:
        x = self.root.winfo_x() + event.x - self._drag_offset[0]
        y = self.root.winfo_y() + event.y - self._drag_offset[1]
        self.root.geometry(f"+{x}+{y}")

    def _on_drag_end(self, _event: tk.Event) -> None:
        self._save_position()

    def _build_context_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Refresh now", command=self._on_refresh_now)
        self.menu.add_checkbutton(label="Always in front", variable=self._always_on_top, command=self._toggle_always_on_top)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.root.destroy)
        self.root.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _on_refresh_now(self) -> None:
        if self.on_refresh_requested is not None:
            self.on_refresh_requested()

    def _toggle_always_on_top(self) -> None:
        self.root.attributes("-topmost", self._always_on_top.get())

    def _restore_position(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x, y = screen_w - CARD_WIDTH - 24, screen_h - CARD_HEIGHT - 24

        if self._position_path.is_file():
            try:
                saved = json.loads(self._position_path.read_text(encoding="utf-8"))
                x, y = clamp_position(int(saved["x"]), int(saved["y"]), CARD_WIDTH, CARD_HEIGHT, screen_w, screen_h)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

        self.root.geometry(f"{CARD_WIDTH}x{CARD_HEIGHT}+{x}+{y}")

    def _save_position(self) -> None:
        try:
            self._position_path.write_text(
                json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}), encoding="utf-8"
            )
        except OSError:
            pass
