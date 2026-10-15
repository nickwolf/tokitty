"""Every module under tokitty imports with the old GUI stack unavailable.

A subprocess puts None in sys.modules for each blocked name, so any import of
one raises, then imports the module and checks that nothing from the blocked
roots got in. The tokitty.qt modules need PySide6 and are marked qt (the qt CI
job runs them); they are skipped when PySide6 cannot be imported.
"""
import importlib.util
import os
import pkgutil
import subprocess
import sys

import pytest

import tokitty

_BLOCKED = (
    "tkinter", "_tkinter", "tkinter.ttk", "tkinter.messagebox",
    "tkinter.simpledialog", "tkinter.colorchooser", "tkinter.filedialog",
    "tkinter.font", "tkinter.constants", "tkinter.commondialog",
    "pystray", "AppKit", "Foundation", "objc", "Xlib", "PIL", "PIL.Image",
)
_LEAK_ROOTS = ("tkinter", "_tkinter", "pystray", "AppKit", "Foundation", "objc", "Xlib", "PIL")


def _module_names():
    names = ["tokitty"]
    names += [info.name for info in pkgutil.walk_packages(tokitty.__path__, "tokitty.", onerror=lambda name: None)]
    return sorted(names)


def _param(name):
    marks = [pytest.mark.qt] if name.startswith("tokitty.qt.") else []
    return pytest.param(name, marks=marks, id=name)


_MODULES = [_param(name) for name in _module_names()]


def test_the_walk_found_the_package():
    names = _module_names()
    assert "tokitty.__main__" in names
    assert "tokitty.streamdock.window" in names
    assert "tokitty.qt.app" in names


@pytest.mark.parametrize("module", _MODULES)
def test_imports_without_tkinter_or_its_helpers(module):
    if module.startswith("tokitty.qt.") and importlib.util.find_spec("PySide6") is None:
        pytest.skip("PySide6 is not installed")
    code = (
        "import sys\n"
        f"for name in {_BLOCKED!r}:\n"
        "    sys.modules[name] = None\n"
        f"import {module}\n"
        f"leaked = [m for m in sys.modules if m.split('.')[0] in {_LEAK_ROOTS!r}"
        " and sys.modules[m] is not None]\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert result.returncode == 0, result.stderr
