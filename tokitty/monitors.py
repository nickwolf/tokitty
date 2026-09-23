"""Monitor work areas, for placing the card on the monitor it was saved on.

Tk only knows the primary monitor: winfo_screenwidth and
winfo_screenheight are its full bounds, taskbar included. A position
saved on any other monitor is a virtual-desktop coordinate outside that
rectangle, so it has to be clamped against its own monitor instead.

Windows only. Everywhere else these return None and the caller keeps
using Tk's screen size, which is what it did before.
"""
from __future__ import annotations

import sys
from typing import Optional

from tokitty.geometry import Area

_MONITOR_DEFAULTTOPRIMARY = 1
_MONITOR_DEFAULTTONEAREST = 2


def _work_area_of(ctypes, user32, monitor) -> Optional[Area]:
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    work = info.rcWork
    return work.left, work.top, work.right, work.bottom


def work_area_for(x: int, y: int, width: int, height: int) -> Optional[Area]:
    """Work area of the monitor a window rectangle mostly lies on, or of
    the nearest monitor when it lies on none (the monitor it was saved on
    has been unplugged). None off Windows or if the lookup fails; never
    raises."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT(x, y, x + width, y + height)
        user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
        user32.MonitorFromRect.restype = wintypes.HMONITOR
        monitor = user32.MonitorFromRect(ctypes.byref(rect), _MONITOR_DEFAULTTONEAREST)
        return _work_area_of(ctypes, user32, monitor)
    except (AttributeError, OSError):
        return None


def primary_work_area() -> Optional[Area]:
    """Work area of the primary monitor, or None as for work_area_for."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        monitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), _MONITOR_DEFAULTTOPRIMARY)
        return _work_area_of(ctypes, user32, monitor)
    except (AttributeError, OSError):
        return None
