"""Shared look and widgets for the Settings window.

Plain tk widgets on purpose: no ttk theme is ever selected, because
ttk.Style().theme_use() is global to the interpreter and would restyle the
Stream Dock window and the presets Treeview. Colors come from the widget's
own palette; sizes are logical pixels at 96 dpi, scaled by Kit.px.

Every control here separates two paths. A user interaction calls its
`on_change` callback; `set()` only repaints. Settings code re-reads
controls from the live getters with set(), so a programmatic update can
never write back.
"""
from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from tokitty.sprites import PALETTE, get_frames, resolve_palette

BG_COLOR = "#1c1c22"
FG_COLOR = "#f0f0f0"
DIM_COLOR = "#8a8a92"
BAR_BG = "#333340"
ACCENT_BG = "#3a1620"
ACCENT_FG = "#ffb4a8"
RAIL = "#17171c"
SURFACE = "#25252d"
SURFACE_HOVER = "#303039"
BORDER = "#393943"
MUTED = "#aaaab2"
GOOD = "#a9d7bb"
WARN = "#e5c78d"
BAD = "#f3a1a9"
BLUE = "#9fbedd"

FONT_FAMILY = "Segoe UI"
FONT_BODY = (FONT_FAMILY, 9)
FONT_MEDIUM = (FONT_FAMILY, 9, "bold")
FONT_SMALL = (FONT_FAMILY, 8)
FONT_TITLE = (FONT_FAMILY, 17, "bold")
FONT_SECTION = (FONT_FAMILY, 9, "bold")
FONT_MONO = ("Consolas", 8)

BASE_WIDTH, BASE_HEIGHT = 640, 540

_PILL_COLORS = {
    "good": (GOOD, "#24372f"),
    "warn": (WARN, "#393128"),
    "bad": (BAD, "#3c272e"),
    "neutral": (MUTED, "#34343d"),
    "blue": (BLUE, "#293442"),
}


def text(parent, value, *, color=FG_COLOR, font=FONT_BODY, bg=None, **kwargs) -> tk.Label:
    return tk.Label(parent, text=value, fg=color, bg=bg or parent.cget("bg"),
                    font=font, anchor="w", **kwargs)


def draw_sprite(canvas: tk.Canvas, palette: dict, cell: int, x0: int = 0, y0: int = 0,
                state: str = "content") -> None:
    """Paint the first frame of a sprite state with `palette` (a full
    char -> color map; the base PALETTE fills any gap)."""
    colors = {**PALETTE, **palette}
    for y, row in enumerate(get_frames(state)[0]):
        for x, ch in enumerate(row):
            color = colors.get(ch)
            if color:
                canvas.create_rectangle(x0 + x * cell, y0 + y * cell,
                                        x0 + (x + 1) * cell, y0 + (y + 1) * cell,
                                        fill=color, outline=color)


def sprite_size(state: str = "content") -> Tuple[int, int]:
    frame = get_frames(state)[0]
    return len(frame[0]), len(frame)


def build_rail(kit: "Kit", rail: tk.Frame, entries: Sequence[Tuple[str, str]], version: str,
               on_select: Optional[Callable[[str], None]] = None) -> Dict[str, tuple]:
    """Fill a window's left rail: the brand block, one strip per entry and
    the version text. Returns key -> (strip, marker, inner, name) for
    paint_rail. With on_select None the strips are plain labels."""
    px = kit.px
    brand = tk.Frame(rail, bg=RAIL)
    brand.pack(fill="x", padx=px(17), pady=(px(21), px(30)))
    logo = tk.Canvas(brand, width=px(28), height=px(26), bg=RAIL, highlightthickness=0)
    logo.pack(side="left", padx=(0, px(7)))
    draw_sprite(logo, resolve_palette("orange", "tabby"), px(1))
    text(brand, "tokitty", font=("Segoe UI", 11, "bold"), bg=RAIL).pack(side="left")

    nav: Dict[str, tuple] = {}
    for key, title in entries:
        strip = tk.Frame(rail, bg=RAIL, height=px(38), cursor="hand2" if on_select else "arrow")
        strip.pack(fill="x", pady=px(1))
        strip.pack_propagate(False)
        marker = tk.Frame(strip, bg=RAIL, width=px(3))
        marker.pack(side="left", fill="y")
        inner = tk.Frame(strip, bg=RAIL)
        inner.pack(side="left", fill="both", expand=True, padx=(px(14), px(8)))
        name = text(inner, title, bg=RAIL, font=FONT_MEDIUM, color=MUTED)
        name.pack(side="left", fill="y")
        nav[key] = (strip, marker, inner, name)
        if on_select is not None:
            for widget in (strip, marker, inner, name):
                widget.bind("<Button-1>", lambda _e, k=key: on_select(k))

    bottom = tk.Frame(rail, bg=RAIL)
    bottom.pack(side="bottom", fill="x", padx=px(18), pady=px(19))
    tk.Frame(bottom, bg=BORDER, height=px(1)).pack(fill="x", pady=(0, px(13)))
    text(bottom, version, bg=RAIL, color=DIM_COLOR, font=FONT_SMALL).pack(anchor="w")
    return nav


def paint_rail(nav: Dict[str, tuple], current: str) -> None:
    """Mark `current` with the accent strip and 3 px marker."""
    for key, (strip, marker, inner, name) in nav.items():
        active = key == current
        bg = ACCENT_BG if active else RAIL
        for widget in (strip, inner, name):
            widget.configure(bg=bg)
        marker.configure(bg=ACCENT_FG if active else RAIL)
        name.configure(fg=FG_COLOR if active else MUTED)


class Toggle:
    """A row with a title, optional detail line and an on/off switch."""

    def __init__(self, kit: "Kit", parent, title: str, *, detail: Optional[str] = None,
                 bg: str = SURFACE, compact: bool = False,
                 on_change: Optional[Callable[[bool], None]] = None):
        px = kit.px
        self.value = False
        self.enabled = True
        self._on_change = on_change
        self._bg = bg
        self.frame = tk.Frame(parent, bg=bg)
        box = tk.Frame(self.frame, bg=bg)
        box.pack(side="left", fill="both", expand=True)
        self.title = text(box, title, bg=bg, font=FONT_BODY if compact else FONT_MEDIUM)
        self.title.pack(anchor="w")
        self.detail = None
        if detail:
            self.detail = text(box, detail, bg=bg, color=DIM_COLOR, font=FONT_SMALL)
            self.detail.pack(anchor="w", pady=(px(2), 0))
        self._px = px
        self.switch = tk.Canvas(self.frame, width=px(34), height=px(20), bg=bg,
                                highlightthickness=0, cursor="hand2")
        self.switch.pack(side="right", padx=(px(10), 0))
        self._paint()
        for widget in (self.frame, box, self.title, self.detail, self.switch):
            if widget is not None:
                widget.bind("<Button-1>", self._clicked)

    def _clicked(self, _event=None) -> None:
        if not self.enabled:
            return
        self.value = not self.value
        self._paint()
        if self._on_change is not None:
            self._on_change(self.value)

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self.enabled:
            self.enabled = enabled
            self.switch.configure(cursor="hand2" if enabled else "arrow")
            self._paint()

    def set(self, value: bool) -> None:
        value = bool(value)
        if value != self.value:
            self.value = value
            self._paint()

    def get(self) -> bool:
        return self.value

    def _paint(self) -> None:
        px, on = self._px, self.value
        sw = self.switch
        sw.delete("all")
        x0, y0, x1, y1 = map(px, (1, 2, 33, 18))
        track = ACCENT_FG if on else "#555560"
        if not self.enabled:
            track = "#6b4f4a" if on else "#3f3f48"
        sw.create_oval(x0, y0, x0 + px(16), y1, fill=track, outline=track)
        sw.create_oval(x1 - px(16), y0, x1, y1, fill=track, outline=track)
        sw.create_rectangle(x0 + px(8), y0, x1 - px(8), y1, fill=track, outline=track)
        cx, r = px(24 if on else 10), px(6)
        sw.create_oval(cx - r, px(4), cx + r, px(16),
                       fill=ACCENT_BG if on else FG_COLOR, outline="")


class Segmented:
    """A row of mutually exclusive options."""

    def __init__(self, kit: "Kit", parent, options: Sequence[Tuple[str, str]],
                 on_change: Optional[Callable[[str], None]] = None):
        px = kit.px
        self.value: Optional[str] = None
        self._on_change = on_change
        self.frame = tk.Frame(parent, bg=BORDER, padx=px(1), pady=px(1))
        self._items: List[Tuple[str, tk.Label]] = []
        for value, title in options:
            item = tk.Label(self.frame, text=title, font=FONT_SMALL,
                            padx=px(10), pady=px(7), cursor="hand2")
            item.pack(side="left", fill="x", expand=True, padx=px(1))
            item.bind("<Button-1>", lambda _e, v=value: self._clicked(v))
            self._items.append((value, item))
        self._paint()

    def _clicked(self, value: str) -> None:
        self.value = value
        self._paint()
        if self._on_change is not None:
            self._on_change(value)

    def set(self, value) -> None:
        self.value = value
        self._paint()

    def get(self) -> Optional[str]:
        return self.value

    def _paint(self) -> None:
        for value, item in self._items:
            active = self.value == value
            item.configure(bg=ACCENT_BG if active else SURFACE,
                           fg=ACCENT_FG if active else MUTED)


class Dropdown:
    """A dark dropdown built from a Menubutton and a tk.Menu, so the popup
    stays dark where a ttk.Combobox list would not."""

    def __init__(self, kit: "Kit", parent, choices: Iterable[str], *, width: Optional[int] = None,
                 on_change: Optional[Callable[[str], None]] = None):
        px = kit.px
        self._on_change = on_change
        self.var = tk.StringVar(value="")
        self.frame = tk.Frame(parent, bg=BORDER, padx=px(1), pady=px(1))
        self.button = tk.Menubutton(
            self.frame, textvariable=self.var, anchor="w", indicatoron=False,
            relief="flat", bd=0, font=FONT_BODY, fg=FG_COLOR, bg=SURFACE,
            activebackground=SURFACE_HOVER, activeforeground=FG_COLOR,
            padx=px(9), pady=px(6), cursor="hand2")
        self.button.pack(fill="both", expand=True)
        if width is not None:
            self.button.configure(width=width)
        self.menu = tk.Menu(self.button, tearoff=False, bg=SURFACE, fg=FG_COLOR,
                            activebackground=ACCENT_BG, activeforeground=ACCENT_FG,
                            font=FONT_BODY, bd=1, relief="solid",
                            activeborderwidth=0, selectcolor=ACCENT_FG)
        self.button.configure(menu=self.menu)
        arrow = tk.Label(self.button, text="▾", bg=SURFACE, fg=MUTED, font=FONT_BODY)
        arrow.place(relx=1, x=-px(17), rely=.5, anchor="center")
        # Clicks on the arrow label would not reach the Menubutton binding.
        arrow.bind("<Button-1>", lambda _e: self.menu.tk_popup(
            self.button.winfo_rootx(), self.button.winfo_rooty() + self.button.winfo_height()))
        self.set_choices(choices)

    def set_choices(self, choices: Iterable[str]) -> None:
        self.menu.delete(0, "end")
        for choice in choices:
            self.menu.add_command(label=choice, command=lambda c=choice: self._picked(c))

    def _picked(self, choice: str) -> None:
        self.var.set(choice)
        if self._on_change is not None:
            self._on_change(choice)

    def set(self, value: str) -> None:
        self.var.set(value)

    def get(self) -> str:
        return self.var.get()


class FlatButton(tk.Label):
    """A button drawn as a Label, for macOS: Aqua Tk renders tk.Button as a
    native bezel and ignores bg, which puts the theme's light text on white.
    Offers what callers use of tk.Button: text, state, and invoke()."""

    def __init__(self, parent, *, text: str, command: Optional[Callable[[], None]], fg: str, bg: str,
                 hover_fg: str, hover_bg: str, disabled: bool, **options):
        super().__init__(parent, text=text, fg=fg, bg=bg, disabledforeground=fg,
                         state="disabled" if disabled else "normal", **options)
        self._command = command
        self._colors = (fg, bg, hover_fg, hover_bg)
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _hover(self, inside: bool) -> None:
        if str(self.cget("state")) == "disabled":
            return
        fg, bg, hover_fg, hover_bg = self._colors
        self.configure(fg=hover_fg if inside else fg, bg=hover_bg if inside else bg)

    def _on_release(self, event) -> None:
        # Like a real button, letting go outside it cancels the click.
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if str(self.cget("state")) == "disabled" or self._command is None:
            return None
        return self._command()


class Kit:
    """Scale-aware factory for the Settings window's widgets."""

    def __init__(self, scale: float = 1.0, *, platform: str = sys.platform):
        self.scale = max(1.0, float(scale))
        self.platform = platform

    def px(self, logical: float) -> int:
        return round(logical * self.scale)

    def page_header(self, parent, title: str, subtitle: str) -> None:
        text(parent, title, font=FONT_TITLE).pack(anchor="w")
        text(parent, subtitle, color=MUTED, font=FONT_SMALL).pack(
            anchor="w", pady=(self.px(1), self.px(14)))

    def section_header(self, parent, title: str, hint: Optional[str] = None, *, top: int = 12):
        row = tk.Frame(parent, bg=BG_COLOR)
        row.pack(fill="x", pady=(self.px(top), self.px(6)))
        text(row, title, font=FONT_SECTION).pack(side="left")
        hint_label = None
        if hint:
            hint_label = text(row, hint, font=FONT_SMALL, color=DIM_COLOR)
            hint_label.pack(side="right")
        row.hint = hint_label
        return row

    def button(self, parent, title: str, style: str = "secondary",
               command: Optional[Callable[[], None]] = None, *, compact: bool = False,
               disabled: bool = False) -> tk.Widget:
        colors = {
            "primary": (ACCENT_FG, ACCENT_BG, "#ffd0c7"),
            "secondary": (FG_COLOR, SURFACE, "#ffffff"),
            "danger": (BAD, "#3b252b", "#ffd0d3"),
            "quiet": (MUTED, BG_COLOR, FG_COLOR),
        }
        fg, bg, hover_fg = colors[style]
        if disabled:
            fg, bg, hover_fg = ("#686872", "#292930", "#686872")
        font = FONT_SMALL if compact else FONT_MEDIUM
        padx = self.px(10 if compact else 13)
        pady = self.px(5 if compact else 7)
        hover_bg = SURFACE_HOVER if style == "secondary" else bg
        cursor = "arrow" if disabled else "hand2"
        if self.platform == "darwin":
            return FlatButton(parent, text=title, command=command, fg=fg, bg=bg, hover_fg=hover_fg,
                              hover_bg=hover_bg, disabled=disabled, font=font, padx=padx, pady=pady,
                              cursor=cursor)
        return tk.Button(
            parent, text=title, command=None if disabled else command,
            font=font, fg=fg, bg=bg,
            activeforeground=hover_fg,
            activebackground=hover_bg,
            relief="flat", bd=0, highlightthickness=0,
            padx=padx, pady=pady,
            cursor=cursor,
            state="disabled" if disabled else "normal", disabledforeground=fg)

    def pill(self, parent, title: str, kind: str = "good") -> tk.Label:
        fg, bg = _PILL_COLORS[kind]
        return tk.Label(parent, text=title, fg=fg, bg=bg, font=FONT_SMALL,
                        padx=self.px(7), pady=self.px(3))

    @staticmethod
    def set_pill(label: tk.Label, title: str, kind: str) -> None:
        fg, bg = _PILL_COLORS[kind]
        label.configure(text=title, fg=fg, bg=bg)

    def toggle(self, parent, title: str, **kwargs) -> Toggle:
        return Toggle(self, parent, title, **kwargs)

    def segmented(self, parent, options, on_change=None) -> Segmented:
        return Segmented(self, parent, options, on_change)

    def dropdown(self, parent, choices, **kwargs) -> Dropdown:
        return Dropdown(self, parent, choices, **kwargs)

    def entry(self, parent, *, width: Optional[int] = None):
        """(holder frame, tk.Entry). Call set_entry_error on the holder."""
        holder = tk.Frame(parent, bg=BORDER, padx=self.px(1), pady=self.px(1))
        widget = tk.Entry(holder, font=FONT_BODY, bg=SURFACE, fg=FG_COLOR,
                          insertbackground=FG_COLOR, selectbackground=ACCENT_BG,
                          selectforeground=FG_COLOR, relief="flat", bd=0,
                          highlightthickness=0)
        widget.pack(fill="both", expand=True, ipady=self.px(6), padx=self.px(8))
        if width:
            widget.configure(width=width)
        return holder, widget

    @staticmethod
    def set_entry_error(holder: tk.Frame, error: bool) -> None:
        holder.configure(bg=BAD if error else BORDER)
