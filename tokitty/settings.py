"""App-level (not per-account) persisted settings.

settings.json in the per-user state dir (see paths.py), robust-loaded
like customize.py: a missing, unparseable, or wrong-shape file degrades
to defaults instead of crashing the app.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

SETTINGS_FILENAME = "settings.json"


@dataclass(frozen=True)
class Settings:
    tray_enabled: bool = True


def load_settings(state_dir) -> Settings:
    path = Path(state_dir) / SETTINGS_FILENAME
    if not path.is_file():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    tray_enabled = data.get("tray_enabled", True)
    if not isinstance(tray_enabled, bool):
        tray_enabled = True
    return Settings(tray_enabled=tray_enabled)


def save_settings(state_dir, settings: Settings) -> None:
    path = Path(state_dir) / SETTINGS_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
