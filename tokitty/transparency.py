"""Opacity for the card background, and the Windows colour-key plumbing
that keeps the cat and the bars opaque while it fades.

Two mechanisms, picked by platform (see the design doc,
docs/superpowers/specs/2026-09-03-transparency-options-design.md):

  win32  two windows. A card toplevel carries the background at -alpha,
         a content toplevel keyed on KEY_COLOR carries the cat and the
         bars, and the content window is made a native owned window of
         the card so a click on the card can never raise itself above it.
  other  one window at -alpha, which fades everything together. On X11
         with no compositor this is accepted as a silent no-op.

No tkinter here, so the level arithmetic and the platform choice stay
testable on every CI OS.
"""
from __future__ import annotations

import sys
from typing import Optional, Tuple

# Every pixel painted exactly this colour on the content window is punched
# out by Windows. Nothing else in the app may produce it: it is absent from
# all 44 sprite palette colours and from BG_COLOR, BAR_BG, ACCENT_BG,
# FG_COLOR and DIM_COLOR. User-picked colours are checked at render time by
# avoid_key(), because customize.py lets the colour chooser return anything.
KEY_COLOR = "#010203"

LEVELS: Tuple[int, ...] = (100, 90, 80, 70, 60, 50)
DEFAULT_LEVEL = 100
MIN_LEVEL = min(LEVELS)

# MIN_LEVEL stays well above zero on purpose: a keyed pixel is
# click-through, so a fully transparent card would be unrecoverable without
# the tray icon, which is itself optional.


def clamp_level(value) -> int:
    """Nearest supported level, or DEFAULT_LEVEL for anything unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_LEVEL
    return min(LEVELS, key=lambda level: (abs(level - value), level))


def level_label(level: int) -> str:
    return f"{level}%"


def alpha_for(level: int) -> float:
    return clamp_level(level) / 100.0


def effective_level(opacity: int, accented: bool) -> int:
    """The level actually applied to the card window.

    A pending permission prompt forces full opacity: the accent exists to be
    noticed, and a faded version of it is quieter than the state it
    announces. Opacity is a property of the whole toplevel while the accent
    is per-pane, so with several panes one accented pane makes all of them
    opaque.
    """
    return 100 if accented else clamp_level(opacity)


def uses_color_key(platform: Optional[str] = None) -> bool:
    """True where -transparentcolor exists, which is Windows only. Linux Tk
    rejects the attribute outright and macOS has never been tested here."""
    return (platform if platform is not None else sys.platform) == "win32"


def _rgb(color: str) -> Optional[Tuple[int, int, int]]:
    text = color.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def collides_with_key(color: str) -> bool:
    """Colour-key matching is exact RGB equality, so "close to the key" is
    irrelevant and "#010203" must match "#010203 " and "#010203".upper()."""
    key = _rgb(KEY_COLOR)
    return color is not None and _rgb(color) == key


def avoid_key(color: str) -> str:
    """Shift a colour off the key by one unit of blue.

    Applied when painting, never written back to customization.json: a user
    who picks the key colour keeps the colour they picked, and only the one
    pixel value they cannot have is substituted.
    """
    if not collides_with_key(color):
        return color
    red, green, blue = _rgb(KEY_COLOR)
    return "#{:02x}{:02x}{:02x}".format(red, green, min(blue + 1, 255))


def set_content_owner(content_hwnd: int, card_hwnd: int) -> bool:
    """Make the content window a native owned window of the card window.

    Windows keeps an owned window above its owner, so a click that
    activates the card cannot raise the card over the cat. Without this a
    single click on the card background hides the cat permanently: measured
    at 3600 visible cat pixels before the click and 0 after. WS_EX_NOACTIVATE
    on the card was tried and does not prevent it.
    """
    import ctypes
    from ctypes import wintypes

    GWLP_HWNDPARENT = -8
    user32 = ctypes.windll.user32
    setter = user32.SetWindowLongPtrW
    setter.restype = ctypes.c_void_p
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    getter = user32.GetWindowLongPtrW
    getter.restype = ctypes.c_void_p
    getter.argtypes = [wintypes.HWND, ctypes.c_int]

    setter(wintypes.HWND(content_hwnd), GWLP_HWNDPARENT, ctypes.c_void_p(card_hwnd))
    return getter(wintypes.HWND(content_hwnd), GWLP_HWNDPARENT) == card_hwnd


def root_hwnd(widget) -> int:
    """The toplevel HWND for a Tk widget. winfo_id gives the Tk frame
    window, which is a child of the real toplevel on Windows."""
    import ctypes
    from ctypes import wintypes

    GA_ROOT = 2
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    return user32.GetAncestor(wintypes.HWND(widget.winfo_id()), GA_ROOT)
