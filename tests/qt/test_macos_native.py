"""The real widget under the cocoa platform plugin, so the macOS helper runs
against AppKit itself. A subprocess, because an AppKit NSException aborts
the whole process rather than raising into Python."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent("""
    import tempfile
    from PySide6.QtWidgets import QApplication
    from tokitty import theme
    from tokitty.customize import Customization
    from tokitty.qt import macos
    from tokitty.qt.widget import TokittyQtWidget

    app = QApplication([])
    widget = TokittyQtWidget(theme.load_theme(tempfile.mkdtemp()), [Customization()], 1)
    widget.show()
    app.processEvents()
    # The toggle recreates the native window and reapplies the helper.
    widget.set_always_on_top(False)
    widget.set_always_on_top(True)
    app.processEvents()
    objc = macos.ObjC()
    window = objc.send_id(int(widget.winId()), objc.sel("window"))
    print(objc.send_ulong(window, objc.sel("collectionBehavior")))
""")


@pytest.mark.skipif(sys.platform != "darwin", reason="needs AppKit")
def test_widget_shows_under_cocoa_and_joins_every_space():
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa")
    proc = subprocess.run([sys.executable, "-c", SCRIPT], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    behavior = int(proc.stdout.strip().splitlines()[-1])
    assert behavior & (1 << 0)  # CanJoinAllSpaces
    assert behavior & (1 << 8)  # FullScreenAuxiliary
    assert not behavior & (1 << 1)  # MoveToActiveSpace
