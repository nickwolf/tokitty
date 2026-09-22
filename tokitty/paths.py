"""Cross-platform resolution of Tokitty's per-user state directory."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def get_state_dir() -> Path:
    """Return the directory Tokitty should use for state (window position,
    lock file), creating it if it doesn't exist. Never inside the
    repo/install directory -- see the design spec's State/config location
    section for why.
    """
    state_dir = state_dir_path()
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def state_dir_path() -> Path:
    """Where get_state_dir() points, without creating it. For readers of
    optional files, which should not leave an empty directory behind."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        state_dir = Path(base) / "Tokitty"
    elif sys.platform == "darwin":
        state_dir = Path.home() / "Library" / "Application Support" / "Tokitty"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        if not base:
            base = str(Path.home() / ".config")
        state_dir = Path(base) / "tokitty"
    return state_dir
