"""Process DPI awareness, and the single factor the UI multiplies its
pixel geometry by.

Awareness has to be set before the process owns any window. Windows
refuses the change afterwards and reports the refusal in the return value
rather than by raising, which is how the previous attempt (inside
TokittyWindow._configure_window, after tk.Tk()) stayed a silent no-op for
so long. So `init()` belongs at the very top of run_gui, above tk.Tk().

System-aware rather than per-monitor. Per-monitor awareness turns off
Windows' bitmap stretching entirely and makes the app responsible for
rebuilding its layout on every WM_DPICHANGED; Tk 8.6 has no handler for
that message and this app computes its scale once at startup. Asking for
per-monitor would trade blur for a card that is physically half-size on a
second monitor at a different scale, which is worse. System-aware gets
real resolution on the system-DPI monitor and keeps Windows' stretching
as the fallback elsewhere.
"""
from __future__ import annotations

import sys
from typing import Optional, Tuple

LOGICAL_DPI = 96

# DPI_AWARENESS_CONTEXT_SYSTEM_AWARE. A HANDLE-typed sentinel, not an
# enum value, which is why it goes through c_void_p below.
_SYSTEM_AWARE_CONTEXT = -2
_PROCESS_SYSTEM_DPI_AWARE = 1

_S_OK = 0
_E_ACCESSDENIED = -2147024891  # 0x80070005, as a signed 32-bit HRESULT
_ERROR_ACCESS_DENIED = 5
_LOGPIXELSX = 88


def _win32() -> Optional[Tuple[object, object]]:
    """(ctypes, user32), or None when the Windows API is not reachable.

    Keyed on ctypes.windll rather than on sys.platform. The two normally
    agree, but several run_gui tests set sys.platform to "win32" on Linux
    to exercise the Windows branches, and an unguarded ctypes.windll turns
    that into an AttributeError at import-adjacent call time.
    """
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        return ctypes, ctypes.windll.user32
    except AttributeError:
        return None


def set_awareness() -> str:
    """Make the process system-DPI-aware. Returns a short status string for
    the caller to log; never raises.

    Three APIs, newest first, because the older ones are still the only
    thing available on older Windows. "Access denied" is not a reason to
    try the next one: it means awareness is already set for this process
    (Windows uses it for "too late" as well as for a manifest that already
    declared one), and re-asking through an older API cannot succeed
    either.
    """
    win32 = _win32()
    if win32 is None:
        return "not-windows"
    ctypes, user32 = win32

    try:
        fn = user32.SetProcessDpiAwarenessContext
    except AttributeError:
        fn = None
    if fn is not None:
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.c_bool
        if fn(ctypes.c_void_p(_SYSTEM_AWARE_CONTEXT)):
            return "system-aware"
        if ctypes.GetLastError() == _ERROR_ACCESS_DENIED:
            return "already-set"

    try:
        shcore = ctypes.windll.shcore
    except (AttributeError, OSError):
        shcore = None
    if shcore is not None:
        fn = shcore.SetProcessDpiAwareness
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_long
        hresult = fn(_PROCESS_SYSTEM_DPI_AWARE)
        if hresult == _S_OK:
            return "system-aware"
        if hresult == _E_ACCESSDENIED:
            return "already-set"

    try:
        fn = user32.SetProcessDPIAware
    except AttributeError:
        return "unavailable"
    fn.argtypes = []
    fn.restype = ctypes.c_bool
    return "system-aware" if fn() else "failed"


def system_dpi() -> int:
    """The system DPI, or LOGICAL_DPI when it can't be read. Meaningful
    only once set_awareness has run: an unaware process is lied to and
    told 96 whatever the display is actually set to."""
    win32 = _win32()
    if win32 is None:
        return LOGICAL_DPI
    ctypes, user32 = win32

    try:
        fn = user32.GetDpiForSystem
    except AttributeError:
        fn = None
    if fn is not None:
        fn.argtypes = []
        fn.restype = ctypes.c_uint
        dpi = fn()
        if dpi:
            return int(dpi)

    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetDC(None)
    if not hdc:
        return LOGICAL_DPI
    try:
        gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        dpi = gdi32.GetDeviceCaps(hdc, _LOGPIXELSX)
    finally:
        user32.ReleaseDC(None, hdc)
    return int(dpi) if dpi else LOGICAL_DPI


def scale_for(dpi: int) -> float:
    return dpi / float(LOGICAL_DPI)


def init() -> float:
    """Set awareness and return the scale factor the UI should use. Safe to
    call on any platform; everywhere but Windows it is a no-op returning
    1.0."""
    set_awareness()
    return scale_for(system_dpi())
