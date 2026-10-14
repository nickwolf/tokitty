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

# Variables a PyInstaller bootloader may set for a frozen child. --self-check
# lists their names (never values) so the release verifier can record what
# the built bootloader really does on each runner (#77).
ENV_DUMP_PREFIXES = ("_PYI", "PYINSTALLER", "TCL", "TK", "LD_", "DYLD_")


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


def _check_qt():
    from PySide6 import QtCore, QtWidgets  # noqa: F401

    return QtCore.qVersion()


def _check_qt_root():
    """The Qt counterpart of _check_tk_root: a QApplication on the real
    platform plugin (not forced offscreen), one widget shown and its events
    processed, so a bundle whose plugin can't load fails here."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QWidget

    qt_platform_env()
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.resize(64, 64)
    try:
        widget.show()
        app.processEvents()
        return QGuiApplication.platformName()
    finally:
        widget.close()


def qt_platform_env(environ=None, *, frozen=None, platform=None) -> Optional[str]:
    """The frozen Linux build leaves out Qt's Wayland plugin (#115), and an
    explicit QT_QPA_PLATFORM replaces Qt's fallback list, so a session that
    exports only Wayland platforms would make Qt abort. Map that to xcb when
    DISPLAY says XWayland or X is there. Returns the new value, or None when
    nothing was changed. Toolkit-free: it only touches the environment."""
    environ = os.environ if environ is None else environ
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    if not frozen or not platform.startswith("linux"):
        return None
    requested = environ.get("QT_QPA_PLATFORM")
    if not requested or not environ.get("DISPLAY"):
        return None
    names = [entry.split(":", 1)[0].strip().lower() for entry in requested.split(";") if entry.strip()]
    if not names or not all(name.startswith("wayland") for name in names):
        return None
    environ["QT_QPA_PLATFORM"] = "xcb"
    print(
        f"tokitty: QT_QPA_PLATFORM={requested} needs the Wayland plugin, which this build leaves out; using xcb",
        file=sys.stderr,
    )
    return "xcb"


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


def _check_tls():
    """A default context with no trusted roots fails every HTTPS request,
    which is what a frozen Mac build did when its OpenSSL's compiled-in
    cert path was missing (tokitty.tls)."""
    import ssl

    paths = ssl.get_default_verify_paths()
    count = ssl.create_default_context().cert_store_stats()["x509_ca"]
    # A cert directory loads lazily, so it shows no roots until a handshake.
    if count == 0 and not paths.capath:
        raise ValueError(f"no trusted CA certificates (OpenSSL looked for {paths.openssl_cafile})")
    source = os.environ.get(paths.openssl_cafile_env) or paths.cafile or paths.capath or "system store"
    return f"{count} CA certificates from {source}"


def build_toolkits(environ=None) -> set:
    """The GUI toolkits this build declares (TOKITTY_BUILD_TOOLKITS, a comma
    list baked in by the runtime hook, read from the environment in a source
    run). Unset or empty means Tk only. Declared rather than guessed: a build
    that merely has PySide6 installed must not be failed for it."""
    environ = os.environ if environ is None else environ
    names = {part.strip().lower() for part in environ.get("TOKITTY_BUILD_TOOLKITS", "").split(",")}
    names.discard("")
    return names or {"tk"}


def env_names(environ=None) -> list:
    """Sorted names of the environment variables the bootloader may have set."""
    environ = os.environ if environ is None else environ
    return sorted(name for name in environ if name.startswith(ENV_DUMP_PREFIXES))


def self_check() -> int:
    """Hidden --self-check: prove a bundle carries what the app needs.
    The release CI verifier depends on this, including the state_dir
    field, to fail closed rather than trust a bundle that merely
    launched."""
    from tokitty import paths

    toolkits = build_toolkits()
    checks = {}
    if "tk" in toolkits:
        checks["tkinter"] = _check(_check_tkinter)
    # A build that carries Tk runs the Qt checks only for a Qt-mode launch:
    # every shipped updater runs this report with the window switch on and
    # refuses any failed entry, and on Linux a missing xcb library makes Qt
    # abort the whole process, which would block a Tk user's updates (#115).
    run_qt_checks = "qt" in toolkits and ("tk" not in toolkits or os.environ.get("TOKITTY_QT") == "1")
    if run_qt_checks:
        checks["qt"] = _check(_check_qt)
    checks["pil"] = _check(_check_pil)
    if "tk" in toolkits:
        # Only the Tk tray uses pystray; a build without Tk does not carry it,
        # and a failed entry would make every shipped updater refuse it (#114).
        checks["pystray"] = _check(_check_pystray)
    checks.update(
        {
            "prices": _check(_check_prices),
            "hook_writer": _check(_check_hook_writer),
            "tls": _check(_check_tls),
        }
    )
    if getattr(sys, "frozen", False):
        checks["hook_runner"] = _check(_check_hook_runner)
    if os.environ.get("TOKITTY_SELF_CHECK_TK") == "1":
        if "tk" in toolkits:
            checks["tk_root"] = _check(_check_tk_root)
        if run_qt_checks:
            checks["qt_root"] = _check(_check_qt_root)
            if "tk" not in toolkits:
                # Every shipped updater requires a tk_root entry in the report
                # of the build it installs, so a Qt-only build carries the Qt
                # window result under that name (#142).
                checks["tk_root"] = checks["qt_root"]

    report = {
        "checks": checks,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "build_id": os.environ.get("TOKITTY_BUILD_ID"),
        "state_dir": str(paths.state_dir_path()),
        "env": env_names(),
    }
    if toolkits != {"tk"}:
        report["toolkits"] = sorted(toolkits)
    if sys.stdout is not None:
        print(json.dumps(report))
    return 0 if all(check["ok"] for check in checks.values()) else 1
