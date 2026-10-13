"""Modules the Qt port shares must import with tkinter blocked.

Same method as test_theme.test_import_isolation: a subprocess puts None in
sys.modules for every tkinter entry point, so any import of them raises.
"""
import os
import subprocess
import sys

import pytest

_BLOCKED = (
    "tkinter", "_tkinter", "tkinter.ttk", "tkinter.messagebox",
    "tkinter.simpledialog", "tkinter.colorchooser", "tkinter.filedialog",
)

# What Qt modules must also never reach: the Tk tray's backend and pyobjc.
_QT_BLOCKED = _BLOCKED + ("pystray", "AppKit", "Foundation", "objc")
_LEAK_ROOTS = ("tkinter", "_tkinter", "pystray", "AppKit", "Foundation", "objc")

_MODULES = (
    # split out of the Tk modules for the Qt port
    "tokitty.worker_mailbox",
    "tokitty.app_version",
    "tokitty.settings_style",
    "tokitty.usage_items",
    "tokitty.accounts_rows",
    "tokitty.accounts_logic",
    # already toolkit-free
    "tokitty.tray_icon",
    "tokitty.theme",
    "tokitty.app_core",
    "tokitty.app_controls",
    "tokitty.first_run",
    "tokitty.update_flow",
    "tokitty.update_controller",
    "tokitty.update_check",
    "tokitty.updater",
    "tokitty.update_install",
    "tokitty.update_swap",
    "tokitty.streamdock.gui_state",
    "tokitty.streamdock.wiring",
    "tokitty.streamdock.presets",
    "tokitty.streamdock.window",
)


@pytest.mark.parametrize("module", _MODULES)
def test_imports_without_tkinter(module):
    code = (
        "import sys\n"
        f"for name in {_BLOCKED!r}:\n"
        "    sys.modules[name] = None\n"
        f"import {module}\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


_QT_MODULES = (
    "tokitty.qt.accounts",
    "tokitty.qt.app",
    "tokitty.qt.deck_views",
    "tokitty.qt.host",
    "tokitty.qt.kit",
    "tokitty.qt.macos",
    "tokitty.qt.menu",
    "tokitty.qt.pane",
    "tokitty.qt.poller",
    "tokitty.qt.position",
    "tokitty.qt.presets",
    "tokitty.qt.settings",
    "tokitty.qt.settings_accounts",
    "tokitty.qt.settings_streamdock",
    "tokitty.qt.tray",
    "tokitty.qt.update_dialog",
    "tokitty.qt.update_host",
    "tokitty.qt.walkthrough",
    "tokitty.qt.widget",
)


@pytest.mark.qt
@pytest.mark.parametrize("module", _QT_MODULES)
def test_qt_modules_import_without_tkinter(module):
    """The Qt windows share the toolkit-free modules and never reach a Tk one."""
    code = (
        "import sys\n"
        f"for name in {_QT_BLOCKED!r}:\n"
        "    sys.modules[name] = None\n"
        f"import {module}\n"
        f"leaked = [m for m in sys.modules if m.split('.')[0] in {_LEAK_ROOTS!r}"
        " and sys.modules[m] is not None]\n"
        "assert not leaked, leaked\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert r.returncode == 0, r.stderr
