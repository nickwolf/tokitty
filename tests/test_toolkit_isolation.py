"""Modules the Qt port shares must import with tkinter blocked.

Same method as test_theme.test_import_isolation: a subprocess puts None in
sys.modules for every tkinter entry point, so any import of them raises.
"""
import subprocess
import sys

import pytest

_BLOCKED = (
    "tkinter", "_tkinter", "tkinter.ttk", "tkinter.messagebox",
    "tkinter.simpledialog", "tkinter.colorchooser", "tkinter.filedialog",
)

_MODULES = (
    # split out of the Tk modules for the Qt port
    "tokitty.worker_mailbox",
    "tokitty.app_version",
    "tokitty.settings_style",
    "tokitty.accounts_rows",
    "tokitty.accounts_logic",
    # already toolkit-free
    "tokitty.theme",
    "tokitty.app_core",
    "tokitty.first_run",
    "tokitty.streamdock.gui_state",
    "tokitty.streamdock.wiring",
    "tokitty.streamdock.presets",
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
