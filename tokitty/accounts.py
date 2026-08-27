"""Parsing of the optional multi-account config file, accounts.json.

Lives in the same per-user state dir as position.json (see paths.py).
Absent, unparseable, or empty => None: callers must fall back to v1
single-account behavior. The Phase 2 installer (hooks_install.get_config_dirs)
already reads the same file; this module is the UI/poller-side consumer.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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


IDENTITY_PREFIX = "acct-v1-"
IDENTITY_HISTORY_FILENAME = "identity_history.json"


def _is_windows_local(config_dir: str) -> bool:
    return len(config_dir) >= 2 and config_dir[1] == ":" and config_dir[0].isalpha()


def canonicalize_locator(config_dir: str) -> str:
    """Canonical locator string for a config dir, used as the identity
    slug's hash input. A WSL UNC path (either \\\\wsl$ or \\\\wsl.localhost
    alias) becomes wsl:<casefolded distro>:<normalized posix dir>, so both
    aliases of the same real directory hash to the same slug. A
    Windows-local path becomes its normcase'd, normpath'd absolute form. A
    POSIX path becomes its absolute, symlink-resolved real path. A
    relative path in any of the three shapes raises ValueError -- there is
    no safe canonical form for "relative to what" outside a live process.
    """
    unc = parse_wsl_unc(config_dir)
    if unc is not None:
        distro, posix_dir = unc
        normalized = str(PurePosixPath(posix_dir))
        return f"wsl:{distro.casefold()}:{normalized}"
    if _is_windows_local(config_dir):
        if not ntpath.isabs(config_dir):
            raise ValueError(f"relative path not allowed: {config_dir}")
        return ntpath.normcase(ntpath.normpath(config_dir))
    if not posixpath.isabs(config_dir):
        raise ValueError(f"relative path not allowed: {config_dir}")
    return os.path.realpath(config_dir)


def assign_identity_slug(locator: str, taken_slugs: "set", history: dict) -> "Tuple[str, dict]":
    """Return (slug, updated_history) for a canonical locator. A history
    hit always wins, so a re-add after removal recovers its exact old
    slug. On a fresh locator, the slug is acct-v1-<full lowercase
    SHA-256 of the locator>; on a collision with a slug already in
    taken_slugs (which the caller populates from every slug in history,
    not just the currently active accounts -- a removed account's old
    slug is invisible to an active-only scan), the digest input becomes
    "<locator>\\0<counter>" starting at counter=2, tried until free."""
    if locator in history:
        return history[locator], history

    digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()
    slug = f"{IDENTITY_PREFIX}{digest}"
    counter = 2
    while slug in taken_slugs:
        digest = hashlib.sha256(f"{locator}\x00{counter}".encode("utf-8")).hexdigest()
        slug = f"{IDENTITY_PREFIX}{digest}"
        counter += 1

    new_history = dict(history)
    new_history[locator] = slug
    return slug, new_history


def load_identity_history(state_dir: Path) -> dict:
    path = Path(state_dir) / IDENTITY_HISTORY_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def save_identity_history(state_dir: Path, history: dict) -> None:
    path = Path(state_dir) / IDENTITY_HISTORY_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def backfill_identity_history(state_dir: Path, accounts: List[Account]) -> dict:
    """Register the stable names of accounts created before identity
    history existed.

    An active account is authoritative for its locator.  Recording it before
    remove/add mutations lets a later re-add recover the same customization
    key instead of allocating a new hash-derived slug.
    """
    history = load_identity_history(state_dir)
    covered_names = set(history.values())
    updated = dict(history)
    for account in accounts:
        if account.name in covered_names:
            continue
        try:
            locator = canonicalize_locator(account.config_dir)
        except ValueError:
            # Existing hand-written files predate manual-path validation;
            # do not make the manager unusable because one legacy locator
            # cannot be made stable safely.
            continue
        updated[locator] = account.name
        covered_names.add(account.name)
    if updated != history:
        save_identity_history(state_dir, updated)
    return updated
