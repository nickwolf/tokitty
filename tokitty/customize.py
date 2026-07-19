"""Persistence for per-account coat, color overrides, and labels.

Lives in the same per-user state dir as position.json and accounts.json
(see paths.py). customization.json is optional: absent or unparseable
=> {}, and callers fall back to the default Customization() per account.

Override keys are a closed set:
  - "coat_base"  -> sprite char "o" (coat fill)
  - "coat_shade" -> sprite char "O" (coat shading)
  - "card_bg"    -> consumed by the UI, not the sprite palette
  - "bar_fill"   -> consumed by the UI, not the sprite palette
All override values must be "#rrggbb"; unknown keys and invalid hex
values are silently dropped on load so a hand-edited file degrades
gracefully instead of crashing the poller/UI.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict

from tokitty import sprites

CUSTOMIZATION_FILENAME = "customization.json"

SINGLE_KEY = "default"

_OVERRIDE_KEYS = frozenset({"coat_base", "coat_shade", "card_bg", "bar_fill"})
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass(frozen=True)
class Customization:
    coat: str = "orange_tabby"
    overrides: Dict[str, str] = field(default_factory=dict)
    label: str = ""


def _clean_overrides(raw) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    cleaned: Dict[str, str] = {}
    for key, value in raw.items():
        if key not in _OVERRIDE_KEYS:
            continue
        if not isinstance(value, str) or not _HEX_RE.fullmatch(value):
            continue
        cleaned[key] = value
    return cleaned


def load_customization(state_dir: Path) -> Dict[str, Customization]:
    path = Path(state_dir) / CUSTOMIZATION_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    result: Dict[str, Customization] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        coat = entry.get("coat")
        if not isinstance(coat, str) or coat not in sprites.COATS:
            coat = "orange_tabby"
        label = entry.get("label")
        if not isinstance(label, str):
            label = ""
        result[key] = Customization(
            coat=coat,
            overrides=_clean_overrides(entry.get("overrides")),
            label=label,
        )
    return result


def save_customization(state_dir: Path, data: Dict[str, Customization]) -> None:
    path = Path(state_dir) / CUSTOMIZATION_FILENAME
    payload = {key: asdict(value) for key, value in data.items()}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def effective_palette(custom: Customization) -> Dict[str, str]:
    palette = sprites.get_palette(custom.coat)
    palette = dict(palette)
    if "coat_base" in custom.overrides:
        palette["o"] = custom.overrides["coat_base"]
    if "coat_shade" in custom.overrides:
        palette["O"] = custom.overrides["coat_shade"]
    return palette
