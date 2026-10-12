import importlib.util
import os

import pytest

# Qt tests run offscreen unless the caller picked a platform. Set before any
# QApplication exists.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Without PySide6 these modules cannot even be imported, so leave them out of
# collection (a conftest-level importorskip would be a collection error).
collect_ignore_glob = [] if importlib.util.find_spec("PySide6") else ["*.py"]


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
