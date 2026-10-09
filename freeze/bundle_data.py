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


def project_version(root) -> str:
    """The version in pyproject.toml, read without tomllib so the spec runs on
    any build Python. The release tag must match it, which verify_artifact.py
    checks against the bundle's Info.plist."""
    import re
    from pathlib import Path

    text = (Path(root) / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise ValueError("no version in pyproject.toml")
    return match.group(1)


def info_plist(version: str) -> dict:
    """Info.plist keys for Tokitty.app. The native About panel reads all
    three, so an unset version shows PyInstaller's 0.0.0 there (#98)."""
    return {
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHumanReadableCopyright": "Copyright © 2026 Nick Wolf. MIT License.",
    }
