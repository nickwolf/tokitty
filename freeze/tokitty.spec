# PyInstaller spec for Tokitty. Build from the repo root:
#   python -m PyInstaller freeze/tokitty.spec --noconfirm --distpath <dist> --workpath <work>
# TOKITTY_BUILD_ID (optional) is baked into both executables; see --self-check.
# TOKITTY_QT_TRIM=0 builds without the Qt trim, to measure what it saves (#115).
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "freeze"))
from bundle_data import (  # noqa: E402
    DATA_FILES, LGPL_SOURCE_PACKAGES, LICENSE_FILES, NOTICES_FILE, QT_EXCLUDED_MODULES, info_plist,
    project_version, qt_toc_entry_dropped,
)
from bundle_notices import write_notices  # noqa: E402

APP_NAME = "tokitty" if sys.platform.startswith("linux") else "Tokitty"
datas = [(str(ROOT / "tokitty" / name), str(Path("tokitty") / Path(name).parent)) for name in DATA_FILES]
QT_TRIM = os.environ.get("TOKITTY_QT_TRIM", "1") != "0"


# Licence notices (#115). macOS keeps them inside the .app; Windows and Linux
# get a folder beside the executable, copied after COLLECT, since data files
# would land in _internal/.
if sys.platform == "darwin":
    datas += [(str(ROOT / "freeze" / "licenses" / name), "licenses") for name in LICENSE_FILES]

# The app icon is drawn from the sprites at build time (#100), so it can't
# drift from the cat the build ships.
sys.path.insert(0, str(ROOT))
from tokitty import app_icon  # noqa: E402

icns_path = Path(workpath) / "Tokitty.icns"
ico_path = Path(workpath) / "Tokitty.ico"
icns_path.parent.mkdir(parents=True, exist_ok=True)
gui_icon = None
if sys.platform == "darwin":
    app_icon.write_icns(icns_path)
elif sys.platform == "win32":
    app_icon.write_ico(ico_path)
    gui_icon = str(ico_path)

rthook = Path(workpath) / "rthook_build_id.py"
rthook.parent.mkdir(parents=True, exist_ok=True)
rthook.write_text(
    "import os\n"
    f"os.environ['TOKITTY_BUILD_ID'] = {os.environ.get('TOKITTY_BUILD_ID', 'dev')!r}\n",
    encoding="utf-8",
)

# Qt is the only toolkit (#142). Pillow stays installed in the build venv for
# app_icon above, so it is excluded here along with everything else that must
# not reach the bundle; a stray import then fails at run time in CI.
GUI_EXCLUDES = ["tkinter", "_tkinter", "PIL", "pystray", "objc", "AppKit", "Foundation", "Xlib"]

gui = Analysis(
    [str(ROOT / "freeze" / "gui_entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    runtime_hooks=[str(rthook)],
    excludes=GUI_EXCLUDES + (list(QT_EXCLUDED_MODULES) if QT_TRIM else []),
    # The LGPL packages ship as plain .py files beside their binaries rather
    # than inside the PYZ archive, so a user can replace them (#115).
    module_collection_mode=LGPL_SOURCE_PACKAGES,
)
hook = Analysis(
    [str(ROOT / "freeze" / "hook_entry.py")],
    pathex=[str(ROOT)],
    runtime_hooks=[str(rthook)],
    excludes=["tkinter", "PIL", "pystray", "PySide6", "shiboken6"],
)

# The Qt trim works on Analysis destinations under PySide6/, by path component
# and library name (bundle_data.qt_entry_dropped, with symlink targets via qt_toc_entry_dropped),
# before COLLECT and BUNDLE.
if QT_TRIM:
    gui.binaries = [e for e in gui.binaries if not qt_toc_entry_dropped(e)]
    gui.datas = [e for e in gui.datas if not qt_toc_entry_dropped(e)]

# The third-party notices (#152) come from what is left after the trim, so
# they name only what this build ships.
notices = write_notices(
    Path(workpath) / NOTICES_FILE,
    [a.pure + a.binaries + a.datas + a.scripts for a in (gui, hook)],
    own_dirs=[ROOT, workpath],
)
if sys.platform == "darwin":
    gui.datas.append((f"licenses/{NOTICES_FILE}", str(notices), "DATA"))

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=gui_icon,
)
hook_exe = EXE(
    PYZ(hook.pure),
    hook.scripts,
    [],
    exclude_binaries=True,
    name="tokitty-hook",
    console=False,
)

coll = COLLECT(
    gui_exe, gui.binaries, gui.datas,
    hook_exe, hook.binaries, hook.datas,
    name=APP_NAME,
)

if sys.platform != "darwin":
    licenses_dir = Path(DISTPATH) / APP_NAME / "licenses"
    licenses_dir.mkdir(parents=True, exist_ok=True)
    for name in LICENSE_FILES:
        shutil.copyfile(ROOT / "freeze" / "licenses" / name, licenses_dir / name)
    shutil.copyfile(notices, licenses_dir / NOTICES_FILE)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Tokitty.app",
        bundle_identifier="com.nickwolf.tokitty",
        version=project_version(ROOT),
        info_plist=info_plist(project_version(ROOT)),
        icon=str(icns_path),
    )
