"""macOS Keychain access for Claude Code's OAuth credentials.

Deliberately subprocess-only (mirrors wsl_probe.py): both entry points take an
injectable `run` so tests never invoke a real `security` binary.

The two functions are split on purpose. Asking for a secret (`-w`) can raise a
macOS authorization dialog; asking only for an item's attributes cannot.
resolve_credentials_source() runs on every poll, so it may only ever call
keychain_item_exists(); read_keychain_secret() is reached solely from
load_credentials(). That keeps the prompt confined to one call site.

Imports from tokitty.credentials at module scope, and credentials.py imports
this module lazily inside functions -- same direction as wsl_probe.py, and
reversing it would be a circular import.
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Optional

from tokitty.credentials import CredentialsError, KeychainAccessError

KEYCHAIN_SERVICE = "Claude Code-credentials"

# `security` exits 44 (errSecItemNotFound) when no matching item exists.
EXIT_ITEM_NOT_FOUND = 44


def _base_command(service: str, account: Optional[str]) -> List[str]:
    cmd = ["security", "find-generic-password", "-s", service]
    if account:
        cmd += ["-a", account]
    return cmd


def keychain_item_exists(service: str, account: Optional[str] = None, run: Callable = subprocess.run) -> bool:
    """True if a matching generic-password item exists.

    Attribute-only query -- no `-w`, so it never prompts. Any exit code other
    than 44 counts as "exists": on an unexpected failure the item's existence
    is not what is in doubt, only access to its secret, and reporting False
    would send the caller down the misleading "can't find credentials" path.
    """
    try:
        result = run(_base_command(service, account), capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        # No `security` binary, or it hung: we cannot claim the item exists.
        return False
    return result.returncode != EXIT_ITEM_NOT_FOUND


def read_keychain_secret(service: str, account: Optional[str] = None, run: Callable = subprocess.run) -> str:
    """Return the item's secret. May raise a macOS authorization dialog."""
    try:
        result = run(_base_command(service, account) + ["-w"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not run `security` to read the Keychain: {exc}") from exc

    if result.returncode == EXIT_ITEM_NOT_FOUND:
        raise CredentialsError(f"No '{service}' item in the login Keychain")

    if result.returncode != 0:
        detail = _decode(result.stderr).strip() or f"exit {result.returncode}"
        raise KeychainAccessError(f"Could not read '{service}' from the login Keychain: {detail}")

    return _decode(result.stdout).strip()


def _decode(raw) -> str:
    return raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else (raw or "")
