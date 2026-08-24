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


@dataclass(frozen=True)
class AccountsLoadResult:
    state: str  # "absent" | "valid_non_empty" | "valid_empty" | "malformed"
    accounts: List[Account]


def load_accounts_result(state_dir: Path) -> AccountsLoadResult:
    path = Path(state_dir) / ACCOUNTS_FILENAME
    if not path.is_file():
        return AccountsLoadResult(state="absent", accounts=[])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AccountsLoadResult(state="malformed", accounts=[])
    entries = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return AccountsLoadResult(state="malformed", accounts=[])

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
    if not accounts:
        return AccountsLoadResult(state="valid_empty", accounts=[])
    return AccountsLoadResult(state="valid_non_empty", accounts=accounts)


def load_accounts(state_dir: Path) -> Optional[List[Account]]:
    """Backward-compatible accessor: every existing caller keeps seeing
    None for absent, malformed, and valid-but-empty files, and the
    account list only for valid_non_empty. New code that needs to tell
    those apart (customization_key's migration, first-run auto-open)
    calls load_accounts_result directly."""
    result = load_accounts_result(state_dir)
    return result.accounts if result.state == "valid_non_empty" else None


def env_conflict_warning(accounts: Optional[List[Account]]) -> Optional[str]:
    if accounts and os.environ.get("TOKITTY_CREDENTIALS"):
        return (
            "Both accounts.json and TOKITTY_CREDENTIALS are set; "
            "accounts.json wins and the env var is ignored."
        )
    return None


def save_accounts(state_dir: Path, accounts: List[Account]) -> None:
    """First writer accounts.json has ever had. Never writes the legacy
    "coat" key -- see Account.coat's docstring: it is parsed for
    backward compatibility only, translated via sprites.LEGACY_COAT_MAP
    on read, and this writer's job is to persist the new identity-slug
    scheme's accounts, not to round-trip legacy coats."""
    path = Path(state_dir) / ACCOUNTS_FILENAME
    payload = {
        "accounts": [
            {"name": account.name, "config_dir": account.config_dir}
            for account in accounts
        ]
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


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
