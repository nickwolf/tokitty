"""macOS-only window and app fixes Qt does not expose, through ctypes and the
Objective-C runtime (no pyobjc). apply() fixes one window; activate_app() acts
on the application.

From the widget's winId(), which is an NSView, it reaches the NSWindow and
then makes two changes: stop a click from activating the app (the private
_setPreventsActivation:, proven in the #109 spike) and let the window join
every Space and sit over full-screen apps (collectionBehavior). With Always
in front off it also drops the window to the normal level. Each step is
isolated, so a failure prints one line and the others still run.
Nothing here imports PySide6; the caller checks the Qt platform is cocoa.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import List, Optional

CAN_JOIN_ALL_SPACES = 1 << 0
MOVE_TO_ACTIVE_SPACE = 1 << 1
FULL_SCREEN_PRIMARY = 1 << 7
FULL_SCREEN_AUXILIARY = 1 << 8
FULL_SCREEN_NONE = 1 << 9
COLLECTION_BEHAVIOR = CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY
# AppKit raises an NSException, which aborts the process, when a window has
# two of these: MoveToActiveSpace with CanJoinAllSpaces, or two full-screen
# roles. Qt sets MoveToActiveSpace | FullScreenAuxiliary on a Qt.Tool window
# and FullScreenPrimary on others, so these come off before ours go on.
CONFLICTING_BEHAVIOR = MOVE_TO_ACTIVE_SPACE | FULL_SCREEN_PRIMARY | FULL_SCREEN_NONE
NORMAL_WINDOW_LEVEL = 0  # NSNormalWindowLevel

STEP_PREVENT_ACTIVATION = "prevent_activation"
STEP_COLLECTION_BEHAVIOR = "collection_behavior"
STEP_NORMAL_LEVEL = "normal_level"


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
        self._long = ctypes.CFUNCTYPE(ctypes.c_long, v, v)(address)
        self._void_long = ctypes.CFUNCTYPE(None, v, v, ctypes.c_long)(address)
        lib.objc_getClass.restype = v
        lib.objc_getClass.argtypes = [ctypes.c_char_p]

    def sel(self, name: str):
        return self._lib.sel_registerName(name.encode())

    def get_class(self, name: str):
        return self._lib.objc_getClass(name.encode())

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

    def send_long(self, obj, sel) -> int:
        return self._long(obj, sel)

    def send_void_long(self, obj, sel, value: int) -> None:
        self._void_long(obj, sel, value)


def _warn(message: str) -> None:
    print(f"tokitty: macOS window helper: {message}", file=sys.stderr)


def apply(win_id: int, objc: Optional[ObjC] = None, platform: str = sys.platform,
          on_top: bool = True) -> List[str]:
    """Run the steps on the window behind win_id. Returns the names of the
    steps that succeeded. Does nothing off macOS.

    on_top=False is the widget with Always in front off. Qt gives a Qt.Tool
    window NSFloatingWindowLevel, and only the stays-on-top hint changes it,
    so without this step the widget floats above other apps' windows. Qt
    puts the floating level back whenever the app becomes active, so the
    caller has to run this again then. With on_top=True (and for any other
    window) the level stays Qt's."""
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
        # Keep what Qt set, apart from the bits ours conflict with.
        current = objc.send_ulong(window, objc.sel("collectionBehavior"))
        objc.send_void_ulong(window, objc.sel("setCollectionBehavior:"),
                             (current & ~CONFLICTING_BEHAVIOR) | COLLECTION_BEHAVIOR)
        done.append(STEP_COLLECTION_BEHAVIOR)
    except Exception as exc:
        _warn(f"setCollectionBehavior: failed: {exc}")
    if not on_top:
        try:
            objc.send_void_long(window, objc.sel("setLevel:"), NORMAL_WINDOW_LEVEL)
            done.append(STEP_NORMAL_LEVEL)
        except Exception as exc:
            _warn(f"setLevel: failed: {exc}")
    return done


def activate_app(objc: Optional[ObjC] = None, platform: str = sys.platform) -> bool:
    """[NSApp activateIgnoringOtherApps:YES], so a window opened from a menu
    comes to the front. An agent app (LSUIElement) is not activated by opening
    a window. Does nothing off macOS, and prints one line and returns False if
    the runtime cannot be reached. GUI thread only."""
    if platform != "darwin":
        return False
    try:
        objc = objc or ObjC()
        ns_app = objc.get_class("NSApplication")
        app = objc.send_id(ns_app, objc.sel("sharedApplication")) if ns_app else None
        if not app:
            raise OSError("sharedApplication returned null")
        objc.send_void_bool(app, objc.sel("activateIgnoringOtherApps:"), True)
        return True
    except Exception as exc:
        _warn(f"cannot activate the app: {exc}")
        return False
