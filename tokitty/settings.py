"""App-level (not per-account) persisted settings.

settings.json in the per-user state dir (see paths.py), robust-loaded
like customize.py: a missing, unparseable, or wrong-shape file degrades
to defaults instead of crashing the app.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tokitty.transparency import DEFAULT_LEVEL, LEVELS
from tokitty.usage_scan import DEFAULT_WINDOW, WINDOWS

SETTINGS_FILENAME = "settings.json"

VIEW_MODES = ("limits", "models")
DEFAULT_VIEW_MODE = "limits"
READOUTS = ("cost", "tokens")
DEFAULT_READOUT = "cost"
# "" = never classified (every existing user), "pending" = the first-run
# walkthrough is owed, "done" = finished or skipped.
FIRST_RUN_STATES = ("", "pending", "done")
PRESET_ENVS = ("wsl", "native")
PRESET_NAME_MAX = 40
MIN_PORT = 1024
MAX_PORT = 65535
TOKEN_MIN = 32
TOKEN_MAX = 128
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Settings:
    tray_enabled: bool = True
    surprise_me: bool = False
    update_check: bool = True
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
    # New-session presets for the Stream Dock, edited by hand. Stored as
    # normalised dicts: name, account_index, env, cwd, and distro for WSL, plus
    # `account` (the account's name slug) when the dialog wrote it.
    streamdock_presets: List[dict] = field(default_factory=list)
    # Loopback port and shared secret for the Stream Dock plugin, chosen once by
    # --install-streamdock. 0 and "" mean not installed.
    streamdock_port: int = 0
    streamdock_token: str = ""
    # Account name slugs whose sessions get no key on the deck. The default
    # no-Account unit has no slug and cannot be hidden.
    streamdock_hidden_accounts: List[str] = field(default_factory=list)
    first_run: str = ""
    # Tell running Claude Code sessions when a usage limit passes these
    # percentages (see usage_notes.py). Claude accounts only.
    usage_notes_enabled: bool = True
    usage_note_session_pct: int = 90
    usage_note_weekly_pct: int = 95


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
    update_check = data.get("update_check", True)
    if not isinstance(update_check, bool):
        update_check = True
    opacity = data.get("opacity", DEFAULT_LEVEL)
    if isinstance(opacity, bool) or opacity not in LEVELS:
        opacity = DEFAULT_LEVEL
    usage_notes_enabled = data.get("usage_notes_enabled", True)
    if not isinstance(usage_notes_enabled, bool):
        usage_notes_enabled = True
    return Settings(
        tray_enabled=tray_enabled,
        surprise_me=surprise_me,
        update_check=update_check,
        opacity=opacity,
        view_mode=_one_of(data.get("view_mode"), VIEW_MODES, DEFAULT_VIEW_MODE),
        usage_window=_one_of(data.get("usage_window"), WINDOWS, DEFAULT_WINDOW),
        usage_readout=_one_of(data.get("usage_readout"), READOUTS, DEFAULT_READOUT),
        usage_budgets=_budgets(data.get("usage_budgets")),
        onboarding_version=_non_negative_int(data.get("onboarding_version")),
        streamdock_presets=_presets(data.get("streamdock_presets")),
        streamdock_port=_port(data.get("streamdock_port")),
        streamdock_token=_token(data.get("streamdock_token")),
        streamdock_hidden_accounts=_names(data.get("streamdock_hidden_accounts")),
        first_run=_one_of(data.get("first_run"), FIRST_RUN_STATES, ""),
        usage_notes_enabled=usage_notes_enabled,
        usage_note_session_pct=_percent(data.get("usage_note_session_pct"), 90),
        usage_note_weekly_pct=_percent(data.get("usage_note_weekly_pct"), 95),
    )


def _one_of(value, allowed, default):
    """Each field degrades independently: one hand-edited typo must not
    take the rest of the file's settings down with it."""
    return value if value in allowed else default


def _non_negative_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _percent(value, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        return default
    return value


def parse_percent(text: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse a threshold entry into (percent, error): a whole number 1-100."""
    answer = (text or "").strip().rstrip("%").strip()
    try:
        value = int(answer)
    except ValueError:
        return None, "Enter a whole number from 1 to 100."
    if not 1 <= value <= 100:
        return None, "Enter a whole number from 1 to 100."
    return value, None


def _port(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_PORT <= value <= MAX_PORT:
        return 0
    return value


def _token(value) -> str:
    if not isinstance(value, str) or not TOKEN_MIN <= len(value) <= TOKEN_MAX:
        return ""
    return value if _TOKEN_RE.fullmatch(value) else ""


def _names(value) -> List[str]:
    """Keep the non-empty strings, once each, in order."""
    if not isinstance(value, list):
        return []
    cleaned: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item not in cleaned:
            cleaned.append(item)
    return cleaned


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


def _presets(value) -> List[dict]:
    """Keep the valid presets and drop the rest one by one. Unknown keys
    are dropped, `distro` is dropped for native presets, and a repeated
    name keeps its first entry."""
    if not isinstance(value, list):
        return []
    cleaned: List[dict] = []
    seen = set()
    for entry in value:
        preset = _preset(entry)
        if preset is None or preset["name"] in seen:
            continue
        seen.add(preset["name"])
        cleaned.append(preset)
    return cleaned


def _preset(entry):
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or len(name) > PRESET_NAME_MAX:
        return None
    index = entry.get("account_index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return None
    env = entry.get("env")
    if env not in PRESET_ENVS:
        return None
    cwd = entry.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    preset = {"name": name, "account_index": index, "env": env, "cwd": cwd}
    account = entry.get("account")
    if isinstance(account, str) and account.strip():
        preset["account"] = account
    if env == "wsl":
        distro = entry.get("distro")
        if not isinstance(distro, str) or not distro.strip():
            return None
        preset["distro"] = distro
    return preset


def budget_for(settings: Settings, slug: str, window: str):
    """The budget that applies to one account in one window, or None."""
    return settings.usage_budgets.get(slug, {}).get(window)


def parse_budget(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Parse a budget entry into (amount, error). Blank clears (None, None);
    an optional leading "$" is stripped; anything else must be a finite
    number above zero."""
    answer = (text or "").strip()
    if not answer:
        return None, None
    try:
        amount = float(answer.lstrip("$").strip())
    except ValueError:
        return None, f"'{answer}' is not a number."
    if not math.isfinite(amount):
        return None, f"'{answer}' is not a number."
    if amount <= 0:
        return None, "Enter an amount greater than zero."
    return amount, None


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
