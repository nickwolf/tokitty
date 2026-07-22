"""Tkinter window: chrome, drag, always-on-top, right-click menu,
position persistence, and rendering (bars, animated cat frames). The
only module in this package that imports tkinter.
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, simpledialog
from typing import Callable, List, Optional

from tokitty.display import bar_color
from tokitty.geometry import clamp_position
from tokitty.menu import MenuItem, build_menu
from tokitty.sprites import COATS, PALETTE, SCALE, get_frames

CARD_WIDTH = 300
PANE_HEIGHT = 128  # was CARD_HEIGHT; one cat+bars unit
CAT_CANVAS_SIZE = 112
STATS_X = 132
BAR_WIDTH = 158
BG_COLOR = "#1c1c22"
FG_COLOR = "#f0f0f0"
DIM_COLOR = "#8a8a92"
BAR_BG = "#333340"

# Permission accent: shifts the card's background so a pending permission
# prompt registers in peripheral vision without a modal. Reverts to
# BG_COLOR the moment the activity state clears.
ACCENT_BG = "#3a1620"
ACCENT_FG = "#ffb4a8"

POSITION_FILENAME = "position.json"
FRAME_INTERVAL_MS = 800


def card_height(pane_count: int) -> int:
    return PANE_HEIGHT * pane_count


def pane_index_at(y: int, pane_count: int) -> int:
    """Map a y coordinate (relative to the window's top edge) to a pane
    index, clamped to the valid range [0, pane_count - 1]."""
    y = max(y, 0)
    return min(y // PANE_HEIGHT, pane_count - 1)


def resolve_bar_fill(pct: float, override: Optional[str]) -> str:
    """Return the bar fill color: the override if set, else the
    threshold-based bar_color(pct)."""
    return override or bar_color(pct)


class Pane:
    """One cat + bars unit. Owns its widgets inside a parent Frame; knows
    nothing about window chrome, drag, or position."""

    def __init__(self, parent, palette=None, card_bg=None, bar_fill=None, label="", coat=None):
        self.parent = parent
        self._current_state = "sleeping"
        self._frame_index = 0
        self._driving_tag = ""
        self._tool_label = ""
        self._accent = False
        self._palette = palette if palette is not None else PALETTE
        self._card_bg = card_bg if card_bg is not None else BG_COLOR
        self._bar_fill = bar_fill
        self._label = label
        self._coat = coat if coat is not None else "orange_tabby"
        self._build_widgets()

    def set_appearance(self, palette=None, card_bg=None, bar_fill=None, label=None, coat=None) -> None:
        """Live re-style without rebuilding widgets. Each parameter left as
        None keeps that slot's current value unchanged -- to reset a slot
        back to the preset/default, pass the preset's/default value
        explicitly (e.g. module PALETTE, BG_COLOR, or "" for no label)."""
        if palette is not None:
            self._palette = palette
        if card_bg is not None:
            self._card_bg = card_bg
        if bar_fill is not None:
            self._bar_fill = bar_fill
        if label is not None:
            self._label = label
        if coat is not None:
            self._coat = coat

        bg = ACCENT_BG if self._accent else self._card_bg
        self.parent.configure(bg=bg)
        self.canvas.configure(bg=bg)
        for widget in (
            self.session_label,
            self.session_reset_label,
            self.weekly_label,
            self.weekly_reset_label,
            self.status_label,
            self.label_widget,
        ):
            widget.configure(bg=bg)

        self.session_bar_bg.delete("fill")
        self.session_bar_bg.create_rectangle(
            0, 0, self._last_session_pct_px, 8, fill=resolve_bar_fill(self._last_session_pct, self._bar_fill),
            width=0, tags="fill",
        )
        self.weekly_bar_bg.delete("fill")
        self.weekly_bar_bg.create_rectangle(
            0, 0, self._last_weekly_pct_px, 8, fill=resolve_bar_fill(self._last_weekly_pct, self._bar_fill),
            width=0, tags="fill",
        )

        self._update_label()

    def _update_label(self) -> None:
        self.label_widget.configure(text=self._label, bg=self._card_bg if not self._accent else ACCENT_BG)

    def _build_widgets(self) -> None:
        self._last_session_pct = 0.0
        self._last_weekly_pct = 0.0
        self._last_session_pct_px = 0
        self._last_weekly_pct_px = 0

        self.canvas = tk.Canvas(
            self.parent, width=CAT_CANVAS_SIZE, height=CAT_CANVAS_SIZE, bg=self._card_bg, highlightthickness=0
        )
        self.canvas.place(x=8, y=8)

        self.session_label = tk.Label(self.parent, text="SESSION", fg=FG_COLOR, bg=self._card_bg, font=("Segoe UI", 9, "bold"))
        self.session_label.place(x=STATS_X, y=12)
        self.session_bar_bg = tk.Canvas(self.parent, width=BAR_WIDTH, height=8, bg=BAR_BG, highlightthickness=0)
        self.session_bar_bg.place(x=STATS_X, y=30)
        self.session_reset_label = tk.Label(self.parent, text="", fg=DIM_COLOR, bg=self._card_bg, font=("Segoe UI", 8))
        self.session_reset_label.place(x=STATS_X, y=42)

        self.weekly_label = tk.Label(self.parent, text="WEEK", fg=FG_COLOR, bg=self._card_bg, font=("Segoe UI", 9, "bold"))
        self.weekly_label.place(x=STATS_X, y=60)
        self.weekly_bar_bg = tk.Canvas(self.parent, width=BAR_WIDTH, height=8, bg=BAR_BG, highlightthickness=0)
        self.weekly_bar_bg.place(x=STATS_X, y=78)
        self.weekly_reset_label = tk.Label(self.parent, text="", fg=DIM_COLOR, bg=self._card_bg, font=("Segoe UI", 8))
        self.weekly_reset_label.place(x=STATS_X, y=90)

        # One widget for both credits and the error hint -- _display_state_for
        # only ever populates one at a time (credits on ok status, hint on
        # every non-ok status), so they never need to show together. Two
        # separate Labels stacked at the same coordinates looked like this
        # instead: even with text="", the widget on top still paints its own
        # background over the widget underneath's left edge, clipping the
        # first character (the "$" in the credits line) -- found via a real
        # screenshot, not guessed.
        self.status_label = tk.Label(
            self.parent, text="", fg=DIM_COLOR, bg=self._card_bg, font=("Segoe UI", 8), wraplength=CARD_WIDTH - STATS_X - 8
        )
        self.status_label.place(x=STATS_X, y=108)

        # Small dim label at the pane's top-right (cat name / identifier).
        # Empty text renders as an empty Label -- takes no visible space.
        self.label_widget = tk.Label(
            self.parent, text=self._label, fg=DIM_COLOR, bg=self._card_bg, font=("Segoe UI", 8)
        )
        self.label_widget.place(x=CARD_WIDTH - 6, y=4, anchor="ne")

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
        tool_label: str = "",
        accent: bool = False,
    ) -> None:
        self._current_state = state
        self._driving_tag = driving_tag
        self._tool_label = tool_label
        self._accent = accent
        self._last_session_pct = session_pct
        self._last_weekly_pct = weekly_pct
        self._last_session_pct_px = BAR_WIDTH * min(session_pct, 100) / 100
        self._last_weekly_pct_px = BAR_WIDTH * min(weekly_pct, 100) / 100

        bg = ACCENT_BG if accent else self._card_bg
        self.parent.configure(bg=bg)
        self.canvas.configure(bg=bg)
        for label in (
            self.session_label,
            self.session_reset_label,
            self.weekly_label,
            self.weekly_reset_label,
            self.status_label,
            self.label_widget,
        ):
            label.configure(bg=bg)

        fg = DIM_COLOR if dimmed else (ACCENT_FG if accent else FG_COLOR)
        self.session_label.configure(fg=fg)
        self.weekly_label.configure(fg=fg)

        self.session_bar_bg.delete("fill")
        self.session_bar_bg.create_rectangle(
            0, 0, self._last_session_pct_px, 8, fill=resolve_bar_fill(session_pct, self._bar_fill), width=0, tags="fill"
        )
        self.session_reset_label.configure(text=f"{session_pct:.0f}% · {session_reset_text}")

        self.weekly_bar_bg.delete("fill")
        self.weekly_bar_bg.create_rectangle(
            0, 0, self._last_weekly_pct_px, 8, fill=resolve_bar_fill(weekly_pct, self._bar_fill), width=0, tags="fill"
        )
        self.weekly_reset_label.configure(text=f"{weekly_pct:.0f}% · {weekly_reset_text}")

        self.status_label.configure(text=hint_text if hint_text else (credits_text or ""))

    def draw_next_frame(self) -> None:
        frames = get_frames(self._current_state)
        frame = frames[self._frame_index % len(frames)]
        self._draw_frame(frame)
        self._frame_index += 1

    def _draw_frame(self, frame) -> None:
        self.canvas.delete("cat")
        frame_w = len(frame[0]) * SCALE
        frame_h = len(frame) * SCALE
        x_off = max((CAT_CANVAS_SIZE - frame_w) // 2, 0)
        y_off = max((CAT_CANVAS_SIZE - frame_h) // 2, 0)
        for row_index, row in enumerate(frame):
            for col_index, ch in enumerate(row):
                color = self._palette.get(ch, "")
                if not color:
                    continue
                x0 = x_off + col_index * SCALE
                y0 = y_off + row_index * SCALE
                self.canvas.create_rectangle(x0, y0, x0 + SCALE, y0 + SCALE, fill=color, width=0, tags="cat")

        if self._driving_tag:
            self.canvas.create_text(
                6, CAT_CANVAS_SIZE - 6, text=self._driving_tag, anchor="sw",
                fill=DIM_COLOR, font=("Segoe UI", 8), tags="cat",
            )

        if self._tool_label:
            self.canvas.create_text(
                6, 6, text=self._tool_label, anchor="nw",
                fill=FG_COLOR, font=("Segoe UI", 8), tags="cat",
            )


class TokittyWindow:
    def __init__(self, root: tk.Tk, state_dir: Path, pane_count: int = 1):
        self.root = root
        self.state_dir = state_dir
        self._pane_count = pane_count
        self._height = card_height(pane_count)
        self._position_path = state_dir / POSITION_FILENAME
        self._drag_offset = (0, 0)
        self._always_on_top_bool = True
        self.on_quit: Callable[[], None] = self.root.destroy
        self.on_toggle_tray: Optional[Callable[[], None]] = None
        self.tray_enabled: Optional[Callable[[], bool]] = None
        self._menu_vars: List = []
        self.on_refresh_requested = None  # set externally by __main__.py
        # (pane_index, field, value) -- set externally by __main__.py. field
        # is one of "coat", "coat_base", "coat_shade", "card_bg", "bar_fill",
        # "label", or "reset" (value ignored for "reset"). For "label", an
        # empty string clears the stored name back to its default.
        self.on_customization_changed: Optional[Callable[[int, str, Optional[str]], None]] = None
        self._menu_pane_index = 0

        self._configure_window()
        self.panes = []
        for i in range(pane_count):
            frame = tk.Frame(root, width=CARD_WIDTH, height=PANE_HEIGHT, bg=BG_COLOR)
            frame.place(x=0, y=i * PANE_HEIGHT)
            self.panes.append(Pane(frame))
        self._restore_position()
        self._bind_drag()
        self._build_context_menu()
        self._animate()

    def _configure_window(self) -> None:
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG_COLOR)
        self.root.geometry(f"{CARD_WIDTH}x{self._height}")

        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

    def _bind_drag(self) -> None:
        # Bound on root only: every pane Frame and its child widgets carry
        # the toplevel (root) in their default bindtags, so a Button-1
        # press anywhere in the window -- including inside a pane -- already
        # reaches these handlers without per-widget binding.
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
        self._rebuild_context_menu()
        self.root.bind_all("<Button-3>", self._show_context_menu)

    def build_menu_model(self, pane_index: int) -> List[MenuItem]:
        """The single-source menu model for a given pane. Rendered here as
        a tk.Menu and (for pane 0) by tray.py as a pystray menu. Getters
        read plain-Python shadow state so tray.py may evaluate them off the
        main thread; `on_toggle_tray`/`tray_enabled` are None unless a tray
        backend is available, which omits the "Show tray icon" item."""
        pane = self.panes[pane_index]
        return build_menu(
            coats=list(COATS.keys()),
            current_coat=(lambda p=pane: p._coat),
            on_coat=(lambda name, i=pane_index: self._select_coat(i, name)),
            on_customize=(lambda i=pane_index: self._open_customize_dialog(i)),
            on_rename=(lambda i=pane_index: self._open_rename_dialog(i)),
            on_refresh=self._on_refresh_now,
            always_on_top=(lambda: self._always_on_top_bool),
            on_toggle_always_on_top=self._toggle_always_on_top,
            on_quit=self.on_quit,
            tray_enabled=self.tray_enabled,
            on_toggle_tray=self.on_toggle_tray,
        )

    def _render_tk_menu(self, menu: tk.Menu, items: List[MenuItem]) -> None:
        radio_var: Optional[tk.StringVar] = None
        for item in items:
            if item.separator:
                menu.add_separator()
            elif item.submenu is not None:
                child = tk.Menu(menu, tearoff=0)
                self._render_tk_menu(child, item.submenu)
                menu.add_cascade(label=item.label, menu=child)
            elif item.radio_selected is not None:
                if radio_var is None:
                    radio_var = tk.StringVar()
                    self._menu_vars.append(radio_var)
                if item.radio_selected():
                    radio_var.set(item.label)
                menu.add_radiobutton(label=item.label, value=item.label,
                                     variable=radio_var, command=item.action)
            elif item.checkbox is not None:
                var = tk.BooleanVar(value=item.checkbox())
                self._menu_vars.append(var)
                menu.add_checkbutton(label=item.label, variable=var, command=item.action)
            else:
                menu.add_command(label=item.label, command=item.action)

    def _rebuild_context_menu(self) -> None:
        if getattr(self, "menu", None) is not None:
            self.menu.destroy()
        self._menu_vars = []
        self.menu = tk.Menu(self.root, tearoff=0)
        self._render_tk_menu(self.menu, self.build_menu_model(self._menu_pane_index))

    def _show_context_menu(self, event: tk.Event) -> None:
        y_relative = event.y_root - self.root.winfo_rooty()
        self._menu_pane_index = pane_index_at(y_relative, len(self.panes))
        self._rebuild_context_menu()
        self.menu.tk_popup(event.x_root, event.y_root)

    def _select_coat(self, pane_index: int, coat_name: str) -> None:
        self._fire_customization_changed(pane_index, "coat", coat_name)

    def _fire_customization_changed(self, pane_index: int, field: str, value: Optional[str]) -> None:
        if self.on_customization_changed is not None:
            self.on_customization_changed(pane_index, field, value)

    def _open_customize_dialog(self, pane_index: int) -> None:
        pane = self.panes[pane_index]
        label = pane._label or f"Cat {pane_index + 1}"

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Customize {label}")
        dialog.transient(self.root)
        dialog.configure(bg=BG_COLOR)
        dialog.resizable(False, False)

        rows = [
            ("Coat base", "coat_base"),
            ("Coat shading", "coat_shade"),
            ("Card background", "card_bg"),
            ("Bar color", "bar_fill"),
        ]
        for row_index, (row_label, field) in enumerate(rows):
            tk.Label(dialog, text=row_label, fg=FG_COLOR, bg=BG_COLOR).grid(
                row=row_index, column=0, sticky="w", padx=8, pady=6
            )
            tk.Button(
                dialog,
                text="Choose…",
                command=lambda f=field: self._pick_color(pane_index, dialog, f),
            ).grid(row=row_index, column=1, padx=8, pady=6)

        button_row = len(rows)
        tk.Button(
            dialog,
            text="Reset to preset",
            command=lambda: self._fire_customization_changed(pane_index, "reset", None),
        ).grid(row=button_row, column=0, padx=8, pady=(4, 10))
        tk.Button(dialog, text="Close", command=dialog.destroy).grid(
            row=button_row, column=1, padx=8, pady=(4, 10)
        )

    def _open_rename_dialog(self, pane_index: int) -> None:
        pane = self.panes[pane_index]
        result = simpledialog.askstring(
            "Rename", "Cat name:", parent=self.root, initialvalue=pane._label
        )
        if result is not None:
            self._fire_customization_changed(pane_index, "label", result)

    def _pick_color(self, pane_index: int, dialog: tk.Toplevel, field: str) -> None:
        _rgb, hex_color = colorchooser.askcolor(parent=dialog)
        if hex_color:
            self._fire_customization_changed(pane_index, field, hex_color)

    def _on_refresh_now(self) -> None:
        if self.on_refresh_requested is not None:
            self.on_refresh_requested()

    def _toggle_always_on_top(self) -> None:
        self._always_on_top_bool = not self._always_on_top_bool
        self.root.attributes("-topmost", self._always_on_top_bool)

    def _restore_position(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x, y = screen_w - CARD_WIDTH - 24, screen_h - self._height - 24

        if self._position_path.is_file():
            try:
                saved = json.loads(self._position_path.read_text(encoding="utf-8"))
                x, y = clamp_position(int(saved["x"]), int(saved["y"]), CARD_WIDTH, self._height, screen_w, screen_h)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

        self.root.geometry(f"{CARD_WIDTH}x{self._height}+{x}+{y}")

    def _save_position(self) -> None:
        try:
            self._position_path.write_text(
                json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}), encoding="utf-8"
            )
        except OSError:
            pass

    def render(self, **kwargs) -> None:
        # v1 compatibility: single-pane callers (debug-state path) untouched
        self.panes[0].render(**kwargs)

    def _animate(self) -> None:
        for pane in self.panes:
            pane.draw_next_frame()
        self.root.after(FRAME_INTERVAL_MS, self._animate)
