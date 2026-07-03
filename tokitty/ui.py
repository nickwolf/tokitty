"""Tkinter window: chrome, drag, always-on-top, right-click menu,
position persistence, and rendering (bars, animated cat frames). The
only module in this package that imports tkinter.
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path
from typing import Optional

from tokitty.display import bar_color
from tokitty.geometry import clamp_position
from tokitty.sprites import PALETTE, SCALE, get_frames

CARD_WIDTH = 300
CARD_HEIGHT = 110
CAT_CANVAS_SIZE = 100
BG_COLOR = "#1c1c22"
FG_COLOR = "#f0f0f0"
DIM_COLOR = "#8a8a92"
BAR_BG = "#333340"

POSITION_FILENAME = "position.json"
FRAME_INTERVAL_MS = 800


class TokittyWindow:
    def __init__(self, root: tk.Tk, state_dir: Path):
        self.root = root
        self.state_dir = state_dir
        self._position_path = state_dir / POSITION_FILENAME
        self._drag_offset = (0, 0)
        self._always_on_top = tk.BooleanVar(value=True)
        self.on_refresh_requested = None  # set externally by __main__.py
        self._current_state = "sleeping"
        self._frame_index = 0

        self._configure_window()
        self._build_widgets()
        self._restore_position()
        self._bind_drag()
        self._build_context_menu()
        self._animate()

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

    def render(
        self,
        state: str,
        session_pct: float,
        weekly_pct: float,
        session_reset_text: str,
        weekly_reset_text: str,
        driving_tag: str,
        credits_text: Optional[str],
        hint_text: Optional[str],
        dimmed: bool,
    ) -> None:
        self._current_state = state
        self.tag_label.configure(text=driving_tag)

        fg = DIM_COLOR if dimmed else FG_COLOR
        self.session_label.configure(fg=fg)
        self.weekly_label.configure(fg=fg)

        self.session_bar_bg.delete("fill")
        self.session_bar_bg.create_rectangle(
            0, 0, 170 * min(session_pct, 100) / 100, 8, fill=bar_color(session_pct), width=0, tags="fill"
        )
        self.session_reset_label.configure(text=f"{session_pct:.0f}% · {session_reset_text}")

        self.weekly_bar_bg.delete("fill")
        self.weekly_bar_bg.create_rectangle(
            0, 0, 170 * min(weekly_pct, 100) / 100, 8, fill=bar_color(weekly_pct), width=0, tags="fill"
        )
        self.weekly_reset_label.configure(text=f"{weekly_pct:.0f}% · {weekly_reset_text}")

        self.credits_label.configure(text=credits_text or "")

        if hint_text:
            self.hint_label.configure(text=hint_text)
            self.hint_label.lift()
        else:
            self.hint_label.configure(text="")

    def _animate(self) -> None:
        frames = get_frames(self._current_state)
        frame = frames[self._frame_index % len(frames)]
        self._draw_frame(frame)
        self._frame_index += 1
        self.root.after(FRAME_INTERVAL_MS, self._animate)

    def _draw_frame(self, frame) -> None:
        self.canvas.delete("cat")
        frame_w = len(frame[0]) * SCALE
        frame_h = len(frame) * SCALE
        x_off = max((CAT_CANVAS_SIZE - frame_w) // 2, 0)
        y_off = max((CAT_CANVAS_SIZE - frame_h) // 2, 0)
        for row_index, row in enumerate(frame):
            for col_index, ch in enumerate(row):
                color = PALETTE.get(ch, "")
                if not color:
                    continue
                x0 = x_off + col_index * SCALE
                y0 = y_off + row_index * SCALE
                self.canvas.create_rectangle(x0, y0, x0 + SCALE, y0 + SCALE, fill=color, width=0, tags="cat")
