"""Files PyInstaller must ship as plain files under tokitty/ in the bundle.

All are found through Path(__file__) at runtime (hooks_install copies
hook_writer.py, pricing reads prices.json, the Stream Dock installer copies
its plugin folder), so they have to exist on disk next to the frozen modules,
not only inside the archive. Paths are relative to tokitty/ and keep their
folders in the bundle.
"""

import re
from pathlib import Path
from typing import Optional

PLUGIN_DIR = "streamdock/plugin/com.tokitty.deck.sdPlugin"
PLUGIN_FILES = [f"{PLUGIN_DIR}/{name}" for name in ("manifest.json", "index.html", "pi.html", "icon.png")]

# The Stream Dock key font and its notice (#142). tokitty/streamdock/render.py
# reads the TTF through importlib.resources, so it has to sit on disk next to
# the frozen modules.
FONT_FILES = ["streamdock/fonts/Aileron-Regular.ttf", "streamdock/fonts/NOTICE.txt"]

DATA_FILES = ["hook_writer.py", "prices.json", *PLUGIN_FILES, *FONT_FILES]

# The Qt trim (#115). tokitty/ imports only these three PySide6 bindings, and
# QtDBus has to stay: QtGui links it on macOS and the xcb plugin on Linux.
QT_KEPT_BINDINGS = ("QtCore", "QtGui", "QtWidgets")

# Every other PySide6.Qt* binding in the PySide6-Essentials 6.12.0 wheel, hard
# coded so a wheel change shows up as a diff. Excluded from the GUI Analysis.
QT_EXCLUDED_MODULES = tuple(
    f"PySide6.Qt{name}"
    for name in (
        "Concurrent", "Designer", "Help", "LabsStyleKit", "Network", "OpenGL", "OpenGLWidgets",
        "PrintSupport", "Qml", "QmlFeatures", "Quick", "QuickControls2", "QuickTest",
        "QuickWidgets", "Sql", "Svg", "SvgWidgets", "Test", "UiTools", "Xml",
    )
)

# Plugin directories dropped whole. The wayland-* directories are matched by
# prefix: Linux ships the xcb platform only.
_QT_DROPPED_DIRS = (
    "imageformats", "iconengines", "generic", "networkinformation", "tls", "qmltooling", "translations",
    # Embedded Linux backends, for the eglfs platform the bundle leaves out.
    "egldeviceintegrations",
)
# Qt libraries (and their PySide6 bindings) dropped by name.
_QT_DROPPED_LIBS = (
    "Network", "Pdf", "Qml", "QmlModels", "QmlMeta", "QmlWorkerScript", "Quick", "OpenGL",
    "OpenGLWidgets", "Svg", "VirtualKeyboard",
    # Only the Wayland platform plugin loads these, and Linux ships xcb only.
    "WaylandClient", "WlShellIntegration",
    # Only the eglfs platform plugin loads these.
    "EglFSDeviceIntegration", "EglFsKmsSupport", "EglFsKmsGbmSupport",
)
# Linux keeps xcb alone: eglfs, linuxfb, vkkhrdisplay and vnc are for
# embedded and remote displays, not a desktop session.
_QT_DROPPED_PLATFORMS = ("offscreen", "direct2d", "eglfs", "linuxfb", "vkkhrdisplay", "vnc")
# The GTK 3 platform theme only gives Qt's colour and folder dialogs a GTK
# look, and it pulls GTK, GDK, cairo, pango and atk into the Linux bundle.
_QT_DROPPED_THEMES = ("gtk3",)
_QT_DROPPED_PLATFORM_PREFIXES = ("minimal", "wayland")

# System libraries PyInstaller collects on Linux only because the GTK 3 theme
# plugin links them (checked with ldd on a trimmed build: nothing else in the
# bundle needs them). They sit at the top of _internal/, outside PySide6/, so
# they are named exactly. The X11 extension and glib libraries that GTK also
# pulls in stay: libX11 and Qt can dlopen those, which ldd does not show.
GTK_THEME_LIBS = frozenset((
    "libgtk-3.so.0", "libgdk-3.so.0", "libgdk_pixbuf-2.0.so.0", "libatk-1.0.so.0",
    "libatk-bridge-2.0.so.0", "libatspi.so.0", "libcairo.so.2", "libcairo-gobject.so.2",
    "libpango-1.0.so.0", "libpangocairo-1.0.so.0", "libpangoft2-1.0.so.0", "libepoxy.so.0",
    "libharfbuzz.so.0", "libgraphite2.so.3", "libfribidi.so.0", "libthai.so.0", "libdatrie.so.1",
    "libpixman-1.so.0", "libjpeg.so.8",
))

# LGPL-3.0 Python packages, collected as source (PyInstaller's
# module_collection_mode "py") so the copy in the bundle can be replaced.
LGPL_SOURCE_PACKAGES = {"PySide6": "py", "shiboken6": "py"}

LICENSE_FILES = ("LICENSE", "LGPL-3.0.txt", "GPL-3.0.txt", "Qt-NOTICE.txt")

# Written into the licenses folder at build time by freeze/bundle_notices.py
# (#152), from what the build collected, so it is not in freeze/licenses/.
NOTICES_FILE = "THIRD-PARTY-NOTICES.txt"


# The Universal CRT: ucrtbase.dll and its api-ms-win-* forwarders. Windows 10
# and later ship it, and Python 3.13 needs Windows 10, but PyInstaller copies
# it from whatever folder on PATH has a copy (on the CI runner, a JDK's bin)
# (#152).
_UCRT = re.compile(r"(ucrtbase|api-ms-win-[a-z0-9-]+)\.dll", re.I)


def ucrt_entry_dropped(entry) -> bool:
    """Whether a TOC entry is a Universal CRT library at the top of the bundle."""
    return entry[2] == "BINARY" and _UCRT.fullmatch(entry[0]) is not None


def qt_toc_entry_dropped(entry) -> bool:
    """qt_entry_dropped for a PyInstaller TOC entry (dest, src, typecode).
    Linux also lists a SYMLINK at the top of the bundle for each Qt library,
    named without the PySide6/ prefix, whose target is the PySide6 path, so
    those are judged by their target; a dropped library otherwise leaves a
    dangling link behind."""
    dest, src, kind = entry[0], entry[1], entry[2]
    if kind == "BINARY" and dest in GTK_THEME_LIBS:
        return True
    return qt_entry_dropped(dest) or (kind == "SYMLINK" and qt_entry_dropped(src))


def qt_entry_dropped(dest: str) -> bool:
    """Whether a collected file under PySide6/ is one the trim drops. Matches
    whole path components and library base names, never substrings, and only
    under PySide6/, so Tokitty's and Python's own files are never touched."""
    import re

    parts = dest.replace("\\", "/").split("/")
    if parts[0] != "PySide6":
        return False
    for part in parts[1:-1]:
        if part in _QT_DROPPED_DIRS or part.startswith("wayland-"):
            return True
        match = re.fullmatch(r"Qt(\w+)\.framework", part)
        if match and match.group(1) in _QT_DROPPED_LIBS:
            return True
    name = parts[-1]
    if name == "opengl32sw.dll" or re.fullmatch(r"d3dcompiler_\d+\.dll", name):
        return True
    # Library and binding names: Qt6Name.dll, libQt6Name.so.6, QtName.pyd,
    # QtName.abi3.so, with the name ended by the first dot.
    stem = name.split(".", 1)[0]
    lib = re.fullmatch(r"(?:lib)?Qt6(\w+)", stem) or re.fullmatch(r"Qt([A-Za-z]\w*)", stem)
    if lib and lib.group(1) in _QT_DROPPED_LIBS:
        return True
    # Platform plugins: qoffscreen.dll, libqoffscreen.so, libqoffscreen.dylib.
    plugin = stem[3:] if stem.startswith("lib") else stem
    if plugin.startswith("q") and "platforms" in parts[1:-1]:
        base = plugin[1:]
        if base in _QT_DROPPED_PLATFORMS or base.startswith(_QT_DROPPED_PLATFORM_PREFIXES):
            return True
    if plugin.startswith("q") and "platformthemes" in parts[1:-1] and plugin[1:] in _QT_DROPPED_THEMES:
        return True
    return False


def project_version(root) -> str:
    """The version in pyproject.toml, read without tomllib so the spec runs on
    any build Python. The release tag must match it, which verify_artifact.py
    checks against the bundle's Info.plist."""
    import re

    text = (Path(root) / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise ValueError("no version in pyproject.toml")
    return match.group(1)


def qt_version(root) -> str:
    """The Qt version the bundle ships: the PySide6 pin in the dependencies
    list of pyproject.toml, so a pin bump moves the copyright line and the notices."""
    import re

    text = (Path(root) / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^dependencies\s*=\s*\[[^\]]*?PySide6[\w-]*==([\d.]+)', text, re.MULTILINE)
    if match is None:
        raise ValueError("no PySide6 pin in the dependencies of pyproject.toml")
    return match.group(1)


def info_plist(version: str, qt: Optional[str] = None) -> dict:
    """Info.plist keys for Tokitty.app. Finder's Get Info shows the version
    and copyright, so an unset version would show PyInstaller's 0.0.0 there
    (#98); the Qt line is LGPL-3.0 section 4(c), as in the About window.
    LSUIElement makes Tokitty an agent app: no Dock icon, no app menu (#142)."""
    if qt is None:
        qt = qt_version(Path(__file__).resolve().parent.parent)
    return {
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSUIElement": True,
        "NSHumanReadableCopyright": (
            "Copyright © 2026 Nick Wolf. MIT License.\n"
            f"Includes Qt {qt} and PySide6 {qt} under the LGPL-3.0, see Contents/Resources/licenses."
        ),
    }
