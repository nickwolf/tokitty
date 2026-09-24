"""Support for the PyInstaller build (#48)."""
from __future__ import annotations

import sys
from typing import Optional

MOVE_TO_APPLICATIONS = (
    "Tokitty is running from a temporary copy macOS makes of downloaded apps. "
    "Move Tokitty to your Applications folder, open it from there, and try again."
)


class AppTranslocatedError(OSError):
    def __init__(self):
        super().__init__(MOVE_TO_APPLICATIONS)

    def __str__(self):
        return MOVE_TO_APPLICATIONS


def is_translocated(executable: Optional[str] = None) -> bool:
    """macOS runs a quarantined app from a random read-only path that vanishes;
    nothing may be registered against it."""
    executable = sys.executable if executable is None else executable
    return "/AppTranslocation/" in executable.replace("\\", "/")
