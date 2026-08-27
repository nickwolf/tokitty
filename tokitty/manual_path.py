"""Validation for the Accounts manager's manual "add by path" row. See
docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md, Manual
path validation.
"""
from __future__ import annotations

import json
import os
import posixpath
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from tokitty.accounts import canonicalize_locator, parse_wsl_unc


@dataclass(frozen=True)
class PathValidationResult:
    ok: bool
    config_dir: Optional[str] = None
    error: Optional[str] = None


def _strip_credentials_filename(path: str) -> str:
    """Drop a trailing .credentials.json, preserving whatever separator
    style the input used. Detection has to normalize separators first
    to catch both "...\\.credentials.json" and ".../.credentials.json",
    but replace() never changes string length, so the cut position
    found in the normalized copy applies unchanged to the original."""
    normalized = path.replace("\\", "/")
    if normalized.endswith("/.credentials.json"):
        return path[: len(normalized) - len("/.credentials.json")]
    return path


def _parses_as_oauth(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("claudeAiOauth"), dict)


def _check_wsl_credentials(distro: str, posix_dir: str, run: Callable) -> PathValidationResult:
    from tokitty.wsl_probe import read_wsl_credentials

    creds_path = posix_dir.rstrip("/") + "/.credentials.json"
    try:
        text = read_wsl_credentials(distro, creds_path, run=run)
    except Exception:
        return PathValidationResult(ok=False, error=f"No .credentials.json found at {distro}:{posix_dir}.")
    if not _parses_as_oauth(text):
        return PathValidationResult(
            ok=False, error=f"{distro}:{creds_path} is not a valid Claude Code credentials file."
        )
    return PathValidationResult(ok=True)


def validate_manual_path(
    raw: str,
    active_config_dirs: List[str],
    run: Callable = subprocess.run,
) -> PathValidationResult:
    """Normalize, canonicalize, and check a manually entered "Claude
    config directory" before any persistence or hook call."""
    expanded = os.path.expanduser(raw.strip())
    if not expanded:
        return PathValidationResult(ok=False, error="Enter a Claude config directory.")

    candidate = _strip_credentials_filename(expanded)

    unc = parse_wsl_unc(candidate)
    if unc is not None:
        distro, posix_dir = unc
        if not posixpath.isabs(posix_dir):
            return PathValidationResult(ok=False, error="Path must be absolute.")
        wsl_result = _check_wsl_credentials(distro, posix_dir, run=run)
        if not wsl_result.ok:
            return wsl_result
    else:
        path = Path(candidate)
        # On real Windows, `Path` is `WindowsPath`, and a leading-slash
        # path with no drive letter (e.g. "/home/nick/.claude-work") is
        # NOT considered absolute by pathlib -- even though the spec
        # explicitly requires this exact POSIX-shaped input to be
        # accepted and routed to local validation, since only \\wsl$\ /
        # \\wsl.localhost\ UNC forms are recognized as WSL. Fall back to
        # a plain leading-separator check so this shape still passes.
        if not (path.is_absolute() or candidate.startswith(("/", "\\"))):
            return PathValidationResult(
                ok=False,
                error=f"'{raw}' is not an absolute path. Enter a full Claude config directory.",
            )
        creds = path / ".credentials.json"
        if not creds.is_file():
            return PathValidationResult(ok=False, error=f"No .credentials.json found in {candidate}.")
        if not _parses_as_oauth(creds.read_text(encoding="utf-8")):
            return PathValidationResult(
                ok=False, error=f"{creds} is not a valid Claude Code credentials file."
            )
        # os.path.expanduser only substitutes the "~" segment; it leaves
        # whatever separator style followed it untouched, so "~/foo" on
        # Windows becomes a mixed "C:\Users\you/foo". Route the local
        # branch's result through Path's own string form so the stored
        # config_dir always comes out in the platform's native style.
        candidate = str(path)

    try:
        locator = canonicalize_locator(candidate)
    except ValueError as exc:
        return PathValidationResult(ok=False, error=str(exc))

    for existing in active_config_dirs:
        try:
            if canonicalize_locator(existing) == locator:
                return PathValidationResult(ok=False, error="This account is already added.")
        except ValueError:
            continue

    return PathValidationResult(ok=True, config_dir=candidate)
