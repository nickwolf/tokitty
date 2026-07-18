"""Parsing of the optional multi-account config file, accounts.json.

Lives in the same per-user state dir as position.json (see paths.py).
Absent, unparseable, or empty => None: callers must fall back to v1
single-account behavior. The Phase 2 installer (hooks_install.get_config_dirs)
already reads the same file; this module is the UI/poller-side consumer.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

ACCOUNTS_FILENAME = "accounts.json"


@dataclass(frozen=True)
class Account:
    name: str
    config_dir: str
    coat: Optional[str] = None  # parsed now, rendered in Phase 4


def load_accounts(state_dir: Path) -> Optional[List[Account]]:
    path = Path(state_dir) / ACCOUNTS_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None

    accounts: List[Account] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or not entry.get("config_dir"):
            continue
        accounts.append(
            Account(
                name=str(entry.get("name") or f"account {index}"),
                config_dir=str(entry["config_dir"]),
                coat=entry.get("coat"),
            )
        )
    return accounts or None


def env_conflict_warning(accounts: Optional[List[Account]]) -> Optional[str]:
    if accounts and os.environ.get("TOKITTY_CREDENTIALS"):
        return (
            "Both accounts.json and TOKITTY_CREDENTIALS are set; "
            "accounts.json wins and the env var is ignored."
        )
    return None


def parse_wsl_unc(config_dir: str) -> Optional[Tuple[str, str]]:
    """(distro, posix_path) for \\\\wsl.localhost\\<d>\\... and \\\\wsl$\\<d>\\...
    UNC forms (either slash direction); None for anything else."""
    normalized = config_dir.replace("/", "\\")
    for prefix in ("\\\\wsl.localhost\\", "\\\\wsl$\\"):
        if normalized.lower().startswith(prefix.lower()):
            parts = [p for p in normalized[len(prefix):].split("\\") if p]
            if len(parts) < 2:
                return None
            return parts[0], "/" + "/".join(parts[1:])
    return None
