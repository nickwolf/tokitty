"""Files PyInstaller must ship as plain files under tokitty/ in the bundle.

All are found through Path(__file__) at runtime (hooks_install copies
hook_writer.py, pricing reads prices.json, the Stream Dock installer copies
its plugin folder), so they have to exist on disk next to the frozen modules,
not only inside the archive. Paths are relative to tokitty/ and keep their
folders in the bundle.
"""

PLUGIN_DIR = "streamdock/plugin/com.tokitty.deck.sdPlugin"
PLUGIN_FILES = [f"{PLUGIN_DIR}/{name}" for name in ("manifest.json", "index.html", "pi.html", "icon.png")]

DATA_FILES = ["hook_writer.py", "prices.json", *PLUGIN_FILES]
