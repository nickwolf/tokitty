# PyInstaller spec for Tokitty. Build from the repo root:
#   python -m PyInstaller freeze/tokitty.spec --noconfirm --distpath <dist> --workpath <work>
# TOKITTY_BUILD_ID (optional) is baked into both executables; see --self-check.
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "freeze"))
from bundle_data import DATA_FILES  # noqa: E402

APP_NAME = "tokitty" if sys.platform.startswith("linux") else "Tokitty"
datas = [(str(ROOT / "tokitty" / name), str(Path("tokitty") / Path(name).parent)) for name in DATA_FILES]

rthook = Path(workpath) / "rthook_build_id.py"
rthook.parent.mkdir(parents=True, exist_ok=True)
rthook.write_text(
    "import os\n"
    f"os.environ['TOKITTY_BUILD_ID'] = {os.environ.get('TOKITTY_BUILD_ID', 'dev')!r}\n",
    encoding="utf-8",
)

gui = Analysis(
    [str(ROOT / "freeze" / "gui_entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    runtime_hooks=[str(rthook)],
)
hook = Analysis(
    [str(ROOT / "freeze" / "hook_entry.py")],
    pathex=[str(ROOT)],
    runtime_hooks=[str(rthook)],
    excludes=["tkinter", "PIL", "pystray"],
)

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
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

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Tokitty.app",
        bundle_identifier="com.nickwolf.tokitty",
    )
