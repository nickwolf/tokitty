"""Cross-platform resolution and loading of Claude Code's OAuth credentials."""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

ENV_OVERRIDE = "TOKITTY_CREDENTIALS"


class CredentialsError(Exception):
    """Raised when the credentials file cannot be found or read."""


class AmbiguousCredentialsError(CredentialsError):
    """Raised when more than one candidate credentials file is found."""


@dataclass(frozen=True)
class LocalCredentialsSource:
    path: Path


@dataclass(frozen=True)
class WslDistroCredentialsSource:
    distro: str
    wsl_path: str


CredentialsSource = Union[LocalCredentialsSource, WslDistroCredentialsSource]


def describe_source(source: CredentialsSource) -> str:
    if isinstance(source, LocalCredentialsSource):
        return str(source.path)
    return f"WSL:{source.distro}:{source.wsl_path}"


def _override_source() -> Optional[LocalCredentialsSource]:
    value = os.environ.get(ENV_OVERRIDE)
    if not value:
        return None
    return LocalCredentialsSource(path=Path(value))


def _home_relative_source() -> Optional[LocalCredentialsSource]:
    candidate = Path.home() / ".claude" / ".credentials.json"
    if candidate.is_file():
        return LocalCredentialsSource(path=candidate)
    return None


def resolve_credentials_source() -> CredentialsSource:
    """Return the credentials source to use.

    Resolution order: explicit override, then home-relative, then (on
    Windows only) a WSL fallback probe. Raises CredentialsError if nothing
    is found, or AmbiguousCredentialsError if more than one WSL distro has
    a credentials file and no override is set.
    """
    override = _override_source()
    if override is not None:
        if not override.path.is_file():
            raise CredentialsError(f"{ENV_OVERRIDE} points to a missing file: {override.path}")
        return override

    home_relative = _home_relative_source()
    if home_relative is not None:
        return home_relative

    if sys.platform == "win32":
        from tokitty.wsl_probe import find_wsl_credentials

        distro, wsl_path = find_wsl_credentials()
        return WslDistroCredentialsSource(distro=distro, wsl_path=wsl_path)

    raise CredentialsError(
        "No Claude Code credentials found at ~/.claude/.credentials.json. "
        f"Set {ENV_OVERRIDE} to the correct path."
    )


def load_credentials(source: CredentialsSource) -> dict:
    """Read and parse the credentials file, returning the claudeAiOauth dict."""
    if isinstance(source, LocalCredentialsSource):
        try:
            raw = source.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CredentialsError(f"Could not read credentials file at {source.path}: {exc}") from exc
    else:
        from tokitty.wsl_probe import read_wsl_credentials

        raw = read_wsl_credentials(source.distro, source.wsl_path)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"Credentials file is not valid JSON: {exc}") from exc

    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise CredentialsError("Credentials file is missing 'claudeAiOauth'")

    return oauth


def is_token_expired(creds: dict, now_ms: Optional[int] = None) -> bool:
    """True if the access token's expiresAt (epoch ms) is in the past."""
    expires_at = creds.get("expiresAt")
    if expires_at is None:
        return True
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return now_ms >= expires_at
