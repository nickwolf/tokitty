"""Files PyInstaller must ship as plain files under tokitty/ in the bundle.

Both are found through Path(__file__) at runtime (hooks_install copies
hook_writer.py, pricing reads prices.json), so they have to exist on disk
next to the frozen modules, not only inside the archive.
"""

DATA_FILES = ["hook_writer.py", "prices.json"]
