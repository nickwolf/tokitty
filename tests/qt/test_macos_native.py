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


POSITION_SCRIPT = textwrap.dedent("""
    import json, sys, tempfile
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from tokitty import theme
    from tokitty.customize import Customization
    from tokitty.qt.widget import TokittyQtWidget

    app = QApplication([])
    path = Path(tempfile.mkdtemp()) / "position.json"
    path.write_text(json.dumps({"x": 400, "y": 300, "unit": "logical"}))
    widget = TokittyQtWidget(theme.load_theme(tempfile.mkdtemp()), [Customization()], 1,
                             position_path=path)
    widget.restore_position()
    widget.show()
    for _ in range(10):
        app.processEvents()
    print(json.dumps(widget.first_card_pos()))
""")


@pytest.mark.skipif(sys.platform != "darwin", reason="needs AppKit")
def test_restored_position_survives_the_first_show():
    """Qt's cocoa plugin moved a frameless window 20 points up when it was
    moved before its native window existed, and the next drag saved the
    shifted spot, so the widget crept up a little on every launch."""
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa")
    proc = subprocess.run([sys.executable, "-c", POSITION_SCRIPT], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "[400, 300]"


MENU_SCRIPT = textwrap.dedent("""
    import tempfile
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QMenu
    from tokitty import theme
    from tokitty.customize import Customization
    from tokitty.menu import build_menu
    from tokitty.qt import macos
    from tokitty.qt.widget import TokittyQtWidget

    app = QApplication([])
    applied = []
    real_apply = macos.apply
    macos.apply = lambda win_id, *a, **k: applied.append(win_id) or real_apply(win_id, *a, **k)

    def model(_i):
        return build_menu(on_refresh=lambda: None, always_on_top=lambda: True,
                          on_toggle_always_on_top=lambda: None, on_quit=lambda: None,
                          view_modes=[("Usage", "usage"), ("Models", "models")],
                          current_view_mode=lambda: "usage", on_view_mode=lambda mode: None)

    widget = TokittyQtWidget(theme.load_theme(tempfile.mkdtemp()), [Customization()], 1,
                             build_menu_model=model)
    widget.show()
    app.processEvents()
    applied.clear()
    menu = widget.context_menu(0)
    submenu = menu.findChildren(QMenu)[0]
    for m in (menu, submenu):
        m.popup(QPoint(300, 300))
        app.processEvents()
        m.hide()
    print(int(menu.winId()) in applied, int(submenu.winId()) in applied)
""")


@pytest.mark.skipif(sys.platform != "darwin", reason="needs AppKit")
def test_menus_get_the_macos_helper_before_they_show():
    """A menu is its own native window. Without the prevent-activation step
    on it, clicking any item made Tokitty the active app and took focus from
    whatever the user was typing in; submenus are windows of their own."""
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa")
    proc = subprocess.run([sys.executable, "-c", MENU_SCRIPT], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "True True"


LEVEL_SCRIPT = textwrap.dedent("""
    import subprocess, tempfile, time
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from tokitty import theme
    from tokitty.customize import Customization
    from tokitty.qt import macos
    from tokitty.qt.widget import TokittyQtWidget

    app = QApplication([])
    widget = TokittyQtWidget(theme.load_theme(tempfile.mkdtemp()), [Customization()], 1)
    widget.show()
    objc = macos.ObjC()

    def pump(seconds=1.0, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not (until and until()):
            app.processEvents()
            time.sleep(0.02)
        pump_rest = time.monotonic() + 0.3
        while time.monotonic() < pump_rest:
            app.processEvents()
            time.sleep(0.02)

    def state_is(state):
        return lambda: app.applicationState() == state

    def level():
        window = objc.send_id(int(widget.winId()), objc.sel("window"))
        return objc.send_long(window, objc.sel("level"))

    pump()
    levels = [level()]
    widget.set_always_on_top(False)
    pump()
    levels.append(level())
    # Qt puts a Qt.Tool window back to the floating level whenever the app
    # becomes active; the widget has to drop it again. Finder takes the
    # activation away first, through Launch Services, so no Apple events.
    subprocess.run(["open", "-a", "Finder"], check=True)
    pump(10.0, state_is(Qt.ApplicationState.ApplicationInactive))
    macos.activate_app()
    pump(10.0, state_is(Qt.ApplicationState.ApplicationActive))
    levels.append(level())
    widget.set_always_on_top(True)
    pump()
    levels.append(level())
    print(*levels)
""")


@pytest.mark.skipif(sys.platform != "darwin", reason="needs AppKit")
def test_always_in_front_off_keeps_the_normal_level_across_activation():
    """Off has to mean NSNormalWindowLevel (0), or the widget stays above
    other apps' windows and Tokitty's own Settings. On is Qt's
    NSModalPanelWindowLevel (8)."""
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa")
    proc = subprocess.run([sys.executable, "-c", LEVEL_SCRIPT], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "8 0 0 8"
