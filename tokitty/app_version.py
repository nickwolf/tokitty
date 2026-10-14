"""The running version for display, toolkit-free (shared by the Qt windows)."""
from __future__ import annotations


def version_text() -> str:
    """The running version for display, e.g. "v0.2.2"."""
    try:
        from tokitty.updater import running_version

        version = running_version().version
    except Exception:
        return "dev"
    if version[:1].isdigit():
        return "v" + version
    return version
