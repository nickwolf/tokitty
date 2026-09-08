"""App-level (not per-account) persisted settings.

settings.json in the per-user state dir (see paths.py), robust-loaded
like customize.py: a missing, unparseable, or wrong-shape file degrades
to defaults instead of crashing the app.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Dict

from tokitty.transparency import DEFAULT_LEVEL, LEVELS
from tokitty.usage_scan import DEFAULT_WINDOW, WINDOWS

SETTINGS_FILENAME = "settings.json"

VIEW_MODES = ("limits", "models")
DEFAULT_VIEW_MODE = "limits"
READOUTS = ("cost", "tokens")
DEFAULT_READOUT = "cost"


@dataclass(frozen=True)
class Settings:
    tray_enabled: bool = True
    surprise_me: bool = False
    opacity: int = DEFAULT_LEVEL
    view_mode: str = DEFAULT_VIEW_MODE
    usage_window: str = DEFAULT_WINDOW
    usage_readout: str = DEFAULT_READOUT
    # {identity slug: {window: dollars}}. Keyed per window because one
    # scalar has no coherent meaning across three of them: $40 set while
    # viewing the month would silently become a daily budget on a switch
    # to 24h, and show a comfortable green bar against the wrong
    # denominator. An absent key is "no budget", which is a different
    # state from zero.
    usage_budgets: Dict[str, Dict[str, float]] = field(default_factory=dict)
    onboarding_version: int = 0


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
    surprise_me = data.get("surprise_me", False)
    if not isinstance(surprise_me, bool):
        surprise_me = False
    opacity = data.get("opacity", DEFAULT_LEVEL)
    if isinstance(opacity, bool) or opacity not in LEVELS:
        opacity = DEFAULT_LEVEL
    return Settings(
        tray_enabled=tray_enabled,
        surprise_me=surprise_me,
        opacity=opacity,
        view_mode=_one_of(data.get("view_mode"), VIEW_MODES, DEFAULT_VIEW_MODE),
        usage_window=_one_of(data.get("usage_window"), WINDOWS, DEFAULT_WINDOW),
        usage_readout=_one_of(data.get("usage_readout"), READOUTS, DEFAULT_READOUT),
        usage_budgets=_budgets(data.get("usage_budgets")),
        onboarding_version=_non_negative_int(data.get("onboarding_version")),
    )


def _one_of(value, allowed, default):
    """Each field degrades independently: one hand-edited typo must not
    take the rest of the file's settings down with it."""
    return value if value in allowed else default


def _non_negative_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _budgets(value) -> Dict[str, Dict[str, float]]:
    """Drop anything that is not a positive number for a known window,
    keeping the rest. A malformed entry for one account must not discard
    another account's valid budget."""
    if not isinstance(value, dict):
        return {}
    cleaned: Dict[str, Dict[str, float]] = {}
    for slug, per_window in value.items():
        if not isinstance(slug, str) or not isinstance(per_window, dict):
            continue
        kept = {}
        for window, amount in per_window.items():
            if window not in WINDOWS:
                continue
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                continue
            if amount <= 0:
                continue
            kept[window] = float(amount)
        if kept:
            cleaned[slug] = kept
    return cleaned


def budget_for(settings: Settings, slug: str, window: str):
    """The budget that applies to one account in one window, or None."""
    return settings.usage_budgets.get(slug, {}).get(window)


def with_budget(settings: Settings, slug: str, window: str, amount) -> Dict[str, Dict[str, float]]:
    """A new usage_budgets mapping with one (slug, window) set or cleared.
    `amount` of None (or <= 0) clears, which is how the dialog's blank
    submission is expressed."""
    budgets = {k: dict(v) for k, v in settings.usage_budgets.items()}
    per_window = budgets.setdefault(slug, {})
    if amount is None or amount <= 0:
        per_window.pop(window, None)
    else:
        per_window[window] = float(amount)
    if not per_window:
        budgets.pop(slug, None)
    return budgets


def save_settings(state_dir, settings: Settings) -> None:
    path = Path(state_dir) / SETTINGS_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def update_settings(state_dir, **changes) -> Settings:
    """Change named fields and leave the rest at whatever is on disk."""
    updated = replace(load_settings(state_dir), **changes)
    save_settings(state_dir, updated)
    return updated
