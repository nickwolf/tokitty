"""Persistence for per-account colorway/pattern, color overrides, and labels.

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
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Dict

from tokitty import sprites

CUSTOMIZATION_FILENAME = "customization.json"

SINGLE_KEY = "default"

_OVERRIDE_KEYS = frozenset({"coat_base", "coat_shade", "card_bg", "bar_fill"})
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass(frozen=True)
class Customization:
    colorway: str = "orange"
    pattern: str = "tabby"
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


def _resolve_colorway_pattern(entry: dict) -> "tuple[str, str]":
    """New files carry colorway+pattern; legacy files carry a single `coat`
    name. Invalid/missing values fall back through legacy translation to the
    orange+tabby default."""
    colorway = entry.get("colorway")
    pattern = entry.get("pattern")
    if not (isinstance(colorway, str) and colorway in sprites.COLORWAYS):
        colorway = None
    if not (isinstance(pattern, str) and pattern in sprites.PATTERNS):
        pattern = None
    if colorway is None or pattern is None:
        coat = entry.get("coat")
        if isinstance(coat, str) and coat in sprites.LEGACY_COAT_MAP:
            legacy_cw, legacy_pat = sprites.LEGACY_COAT_MAP[coat]
            colorway = colorway or legacy_cw
            pattern = pattern or legacy_pat
    return colorway or "orange", pattern or "tabby"


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
        colorway, pattern = _resolve_colorway_pattern(entry)
        label = entry.get("label")
        if not isinstance(label, str):
            label = ""
        result[key] = Customization(
            colorway=colorway,
            pattern=pattern,
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


def save_customization_entry(
    state_dir: Path, key: str, customization: Customization
) -> Dict[str, Customization]:
    """Merge one changed identity into the latest on-disk store.

    The main window and Accounts manager keep separate in-memory views of
    customization.json.  Single-account edits must not write either stale
    whole-file snapshot back over changes made by the other UI.
    """
    store = load_customization(state_dir)
    store[key] = customization
    save_customization(state_dir, store)
    return store


def rename_account(state_dir: Path, slug: str, label: str) -> None:
    """Rename operates on the stable identity slug, never on row
    position or accounts.json's "name" field -- see the Accounts
    manager's Rename flow, which must not confuse a live pane's index
    with a manager row's index."""
    store = load_customization(state_dir)
    current = store.get(slug, Customization())
    save_customization_entry(state_dir, slug, replace(current, label=label))


def effective_palette(custom: Customization) -> Dict[str, str]:
    palette = dict(sprites.resolve_palette(custom.colorway, custom.pattern))
    if "coat_base" in custom.overrides:
        palette["o"] = custom.overrides["coat_base"]
    if "coat_shade" in custom.overrides:
        palette["O"] = custom.overrides["coat_shade"]
    return palette
