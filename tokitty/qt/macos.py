"""macOS-only window fixes Qt does not expose, through ctypes and the
Objective-C runtime (no pyobjc).

From the widget's winId(), which is an NSView, it reaches the NSWindow and
then makes two changes: stop a click from activating the app (the private
_setPreventsActivation:, proven in the #109 spike) and let the window join
every Space and sit over full-screen apps (collectionBehavior, untried). Each
step is isolated, so a failure prints one line and the other still runs.
Nothing here imports PySide6; the caller checks the Qt platform is cocoa.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import List, Optional

CAN_JOIN_ALL_SPACES = 1 << 0
FULL_SCREEN_AUXILIARY = 1 << 8
COLLECTION_BEHAVIOR = CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY

STEP_PREVENT_ACTIVATION = "prevent_activation"
STEP_COLLECTION_BEHAVIOR = "collection_behavior"


class ObjC:
    """The libobjc calls apply() needs. arm64 passes arguments by the exact
    prototype, so every call shape gets its own function type, all made from
    the one objc_msgSend address."""

    def __init__(self) -> None:
        path = ctypes.util.find_library("objc")
        if not path:
            raise OSError("libobjc not found")
        lib = ctypes.CDLL(path)
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        self._lib = lib
        address = ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value
        v = ctypes.c_void_p
        self._id = ctypes.CFUNCTYPE(v, v, v)(address)
        self._bool_sel = ctypes.CFUNCTYPE(ctypes.c_bool, v, v, v)(address)
        self._void_bool = ctypes.CFUNCTYPE(None, v, v, ctypes.c_bool)(address)
        self._ulong = ctypes.CFUNCTYPE(ctypes.c_ulong, v, v)(address)
        self._void_ulong = ctypes.CFUNCTYPE(None, v, v, ctypes.c_ulong)(address)

    def sel(self, name: str):
        return self._lib.sel_registerName(name.encode())

    def send_id(self, obj, sel):
        return self._id(obj, sel)

    def send_bool_sel(self, obj, sel, arg_sel) -> bool:
        return self._bool_sel(obj, sel, arg_sel)

    def send_void_bool(self, obj, sel, value: bool) -> None:
        self._void_bool(obj, sel, value)

    def send_ulong(self, obj, sel) -> int:
        return self._ulong(obj, sel)

    def send_void_ulong(self, obj, sel, value: int) -> None:
        self._void_ulong(obj, sel, value)


def _warn(message: str) -> None:
    print(f"tokitty: macOS window helper: {message}", file=sys.stderr)


def apply(win_id: int, objc: Optional[ObjC] = None, platform: str = sys.platform) -> List[str]:
    """Run both steps on the window behind win_id. Returns the names of the
    steps that succeeded. Does nothing off macOS."""
    if platform != "darwin":
        return []
    try:
        objc = objc or ObjC()
        window = objc.send_id(win_id, objc.sel("window"))
    except Exception as exc:
        _warn(f"cannot reach the NSWindow: {exc}")
        return []
    if not window:
        _warn("the view has no NSWindow yet")
        return []

    done: List[str] = []
    try:
        setter = objc.sel("_setPreventsActivation:")
        # A private method: if a later macOS drops it, clicking the widget
        # activates the app again, as it did before this helper.
        if objc.send_bool_sel(window, objc.sel("respondsToSelector:"), setter):
            objc.send_void_bool(window, setter, True)
            done.append(STEP_PREVENT_ACTIVATION)
    except Exception as exc:
        _warn(f"_setPreventsActivation: failed: {exc}")
    try:
        # OR into what Qt set rather than replacing it.
        current = objc.send_ulong(window, objc.sel("collectionBehavior"))
        objc.send_void_ulong(window, objc.sel("setCollectionBehavior:"),
                             current | COLLECTION_BEHAVIOR)
        done.append(STEP_COLLECTION_BEHAVIOR)
    except Exception as exc:
        _warn(f"setCollectionBehavior: failed: {exc}")
    return done
