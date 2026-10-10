# PyInstaller spec for the PySide6 spike, modelled on freeze/tokitty.spec.
# Build from the repo root:
#   python -m PyInstaller spike/qt_card.spec --noconfirm --distpath <dist> --workpath <work>
# SPIKE_TRIM=0 builds without the Qt exclusions, to measure what they save.
# SPIKE_LSUIELEMENT=0 leaves LSUIElement out of the macOS Info.plist (#44 comparison).
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
TRIM = os.environ.get("SPIKE_TRIM", "1") != "0"
APP_NAME = "tokitty-qt-spike" if sys.platform.startswith("linux") else "TokittyQtSpike"

# Qt modules the spike never imports. PySide6-Essentials already leaves out
# the Addons; these are the Essentials pieces PyInstaller's hooks can still
# drag in.
QT_EXCLUDES = [
    "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2", "PySide6.QtSql",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtXml",
    "PySide6.QtConcurrent", "PySide6.QtDBus", "PySide6.QtPrintSupport", "PySide6.QtHelp",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtMultimedia", "PySide6.QtPdf",
]
# Shipped files to drop by name fragment: software OpenGL fallback, Qt
# translations, and plugins the spike doesn't load. Keep platforms (the
# window system), styles, and the platform theme plugins.
DROP = [
    "opengl32sw", "d3dcompiler", "Qt6Pdf", "Qt6Qml", "Qt6Quick", "Qt6Network", "Qt6OpenGL",
    "Qt6Svg", "Qt6VirtualKeyboard", "translations", "imageformats", "iconengines",
    "networkinformation", "tls", "qmltooling", "generic", "platforminputcontexts",
    "libQt6Pdf", "libQt6Qml", "libQt6Quick", "libQt6Network", "libQt6OpenGL", "libQt6Svg",
]

a = Analysis(
    [str(ROOT / "spike" / "qt_card.py")],
    pathex=[str(ROOT), str(ROOT / "spike")],
    excludes=["tkinter", "PIL", "pystray", "probe_win", "psutil"] + (QT_EXCLUDES if TRIM else []),
)


def keep(entry):
    dest = entry[0].replace("\\", "/")
    return not any(frag in dest for frag in DROP)


if TRIM:
    a.binaries = [b for b in a.binaries if keep(b)]
    a.datas = [d for d in a.datas if keep(d)]

exe = EXE(
    PYZ(a.pure),
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

if sys.platform == "darwin":
    plist = {"CFBundleShortVersionString": "0.0.0", "CFBundleVersion": "0.0.0"}
    if os.environ.get("SPIKE_LSUIELEMENT", "1") != "0":
        plist["LSUIElement"] = True
    app = BUNDLE(coll, name="TokittyQtSpike.app", bundle_identifier="com.nickwolf.tokitty.qtspike",
                 info_plist=plist)
