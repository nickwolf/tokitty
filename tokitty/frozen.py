"""Support for the PyInstaller build (#48)."""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MOVE_TO_APPLICATIONS = (
    "Tokitty is running from a temporary copy macOS makes of downloaded apps. "
    "Move Tokitty to your Applications folder, open it from there, and try again."
)

CRASH_LOG_FILENAME = "crash.log"


class AppTranslocatedError(OSError):
    def __init__(self):
        super().__init__(MOVE_TO_APPLICATIONS)

    def __str__(self):
        return MOVE_TO_APPLICATIONS


def is_translocated(executable: Optional[str] = None) -> bool:
    """macOS runs a quarantined app from a random read-only path that vanishes;
    nothing may be registered against it."""
    executable = sys.executable if executable is None else executable
    return "/AppTranslocation/" in executable.replace("\\", "/")


def run_gui_entry(main_fn, state_dir_fn=None) -> int:
    """Run the frozen GUI. A windowed PyInstaller build would show an escaped
    exception as a modal "Unhandled exception" dialog, so log it to
    <state dir>/crash.log instead."""
    try:
        return main_fn()
    except Exception:
        try:
            from tokitty.paths import get_state_dir

            state_dir = (state_dir_fn or get_state_dir)()
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(Path(state_dir) / CRASH_LOG_FILENAME, "a", encoding="utf-8") as f:
                f.write(f"--- {stamp}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        return 1


def _check(fn) -> dict:
    try:
        return {"ok": True, "detail": str(fn())}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _check_tkinter():
    import tkinter

    return tkinter.Tcl().eval("info patchlevel")


def _check_tk_root():
    """tkinter.Tcl() above only starts the Tcl interpreter; it never opens
    a window, so a bundle whose Tk can't actually initialise (missing
    display, broken Tcl/Tk libs in the onedir layout) would still pass.
    Gated behind TOKITTY_SELF_CHECK_TK so headless unit tests stay headless."""
    import tkinter

    root = tkinter.Tk()
    try:
        root.update_idletasks()
        return root.eval("info patchlevel")
    finally:
        root.destroy()


def _check_pil():
    from PIL import Image

    return Image.new("RGBA", (1, 1))


def _check_pystray():
    import pystray

    return pystray


def _check_prices():
    from tokitty import pricing

    packaged = json.loads(pricing.PACKAGED_PRICES.read_text(encoding="utf-8"))
    table = pricing.build_table(packaged)
    if not table.prices:
        raise ValueError("no priced models")
    return f"{len(table.prices)} priced models"


def _check_hook_writer():
    from tokitty import hooks_install

    path = Path(hooks_install.__file__).with_name("hook_writer.py")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return str(path)


def _check_hook_runner():
    from tokitty import hooks_install

    path = hooks_install.hook_runner_path(os.path.realpath(sys.executable), sys.platform)
    if not Path(path).exists():
        raise FileNotFoundError(path)
    return path


def self_check() -> int:
    """Hidden --self-check: prove a bundle carries what the app needs.
    The release CI verifier depends on this, including the state_dir
    field, to fail closed rather than trust a bundle that merely
    launched."""
    from tokitty import paths

    checks = {
        "tkinter": _check(_check_tkinter),
        "pil": _check(_check_pil),
        "pystray": _check(_check_pystray),
        "prices": _check(_check_prices),
        "hook_writer": _check(_check_hook_writer),
    }
    if getattr(sys, "frozen", False):
        checks["hook_runner"] = _check(_check_hook_runner)
    if os.environ.get("TOKITTY_SELF_CHECK_TK") == "1":
        checks["tk_root"] = _check(_check_tk_root)

    report = {
        "checks": checks,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "build_id": os.environ.get("TOKITTY_BUILD_ID"),
        "state_dir": str(paths.state_dir_path()),
    }
    if sys.stdout is not None:
        print(json.dumps(report))
    return 0 if all(check["ok"] for check in checks.values()) else 1
