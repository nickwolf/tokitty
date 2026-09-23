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


# What a config directory can actually drive. A directory with OAuth
# credentials can answer the usage endpoint ("limits"); a directory with
# transcripts can be costed per model ("models"). They are independent:
# an API-key user has the second and not the first, which is exactly the
# case this validator used to reject outright.
CAP_LIMITS = "limits"
CAP_MODELS = "models"


@dataclass(frozen=True)
class PathValidationResult:
    ok: bool
    config_dir: Optional[str] = None
    error: Optional[str] = None
    capabilities: frozenset = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


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
    """Capabilities of a WSL-side config dir.

    Only an empty capability set is a rejection. A directory with
    transcripts and no credentials is a real account: it drives the
    per-model view, which needs no OAuth at all.
    """
    from tokitty.wsl_probe import read_wsl_credentials, wsl_dir_exists

    capabilities = set()
    creds_path = posix_dir.rstrip("/") + "/.credentials.json"
    credentials_error = f"No .credentials.json found at {distro}:{posix_dir}."
    try:
        text = read_wsl_credentials(distro, creds_path, run=run)
    except Exception:
        text = None
    if text is not None:
        if _parses_as_oauth(text):
            capabilities.add(CAP_LIMITS)
        else:
            credentials_error = (
                f"{distro}:{creds_path} is not a valid Claude Code credentials file."
            )

    if wsl_dir_exists(distro, posix_dir.rstrip("/") + "/projects", run=run):
        capabilities.add(CAP_MODELS)

    if not capabilities:
        # An unreadable or non-OAuth credentials file is still a Claude marker.
        if text is None and any(wsl_dir_exists(distro, posix_dir.rstrip("/") + "/" + name, run=run)
               for name in _CODEX_ROLLOUT_DIRS):
            return PathValidationResult(
                ok=False, error=_codex_picked_as_claude(f"{distro}:{posix_dir}")
            )
        return PathValidationResult(ok=False, error=credentials_error)
    return PathValidationResult(ok=True, capabilities=frozenset(capabilities))


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
        capabilities = set(wsl_result.capabilities)
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
        capabilities = set()
        creds = path / ".credentials.json"
        credentials_error = f"No .credentials.json found in {candidate}."
        if creds.is_file():
            if _parses_as_oauth(creds.read_text(encoding="utf-8")):
                capabilities.add(CAP_LIMITS)
            else:
                credentials_error = f"{creds} is not a valid Claude Code credentials file."
        if (path / "projects").is_dir():
            capabilities.add(CAP_MODELS)
        if not capabilities:
            if looks_like_codex_home(str(path)):
                return PathValidationResult(ok=False, error=_codex_picked_as_claude(candidate))
            return PathValidationResult(ok=False, error=credentials_error)
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

    return PathValidationResult(
        ok=True, config_dir=candidate, capabilities=frozenset(capabilities)
    )


# Codex keeps its rollouts in sessions/, and moves old ones to
# archived_sessions/. Either one is enough to read rate limits and a ledger
# from. Claude Code also has a sessions/ folder, which is why a Codex home
# is recognized by the absence of Claude's own markers as well.
_CODEX_ROLLOUT_DIRS = ("sessions", "archived_sessions")


def _codex_picked_as_claude(where: str) -> str:
    return (
        f"{where} looks like a Codex home, not a Claude Code config directory. "
        "Pick Codex as the harness to add it."
    )


def _claude_picked_as_codex(where: str) -> str:
    return (
        f"{where} looks like a Claude Code config directory, not a Codex home. "
        "Pick Claude Code as the harness to add it."
    )


def _has_claude_markers(path: Path) -> bool:
    return (path / ".credentials.json").is_file() or (path / "projects").is_dir()


def looks_like_codex_home(local_path: str) -> bool:
    """Whether a directory reachable from this process is a Codex home.

    Local filesystem checks only. The caller decides whether the path is
    safe to stat from its thread.
    """
    try:
        path = Path(local_path)
        if _has_claude_markers(path):
            return False
        return any((path / name).is_dir() for name in _CODEX_ROLLOUT_DIRS)
    except OSError:
        return False


def _strip_rollout_dir(path: str) -> str:
    """Drop a trailing sessions or archived_sessions segment, so a user who
    picks the folder the rollouts are actually in still gets the home.
    Separator style is preserved the same way _strip_credentials_filename
    does it."""
    trimmed = path.rstrip("\\/")
    normalized = trimmed.replace("\\", "/")
    for name in _CODEX_ROLLOUT_DIRS:
        if normalized.endswith("/" + name):
            return trimmed[: len(normalized) - len("/" + name)]
    return path


def _check_wsl_codex_home(distro: str, posix_dir: str, run: Callable) -> PathValidationResult:
    from tokitty.wsl_probe import wsl_dir_exists, wsl_file_exists

    base = posix_dir.rstrip("/")
    if wsl_dir_exists(distro, base + "/projects", run=run) or wsl_file_exists(
        distro, base + "/.credentials.json", run=run
    ):
        return PathValidationResult(ok=False, error=_claude_picked_as_codex(f"{distro}:{posix_dir}"))
    if not any(wsl_dir_exists(distro, base + "/" + name, run=run) for name in _CODEX_ROLLOUT_DIRS):
        return PathValidationResult(
            ok=False, error=f"No sessions folder found at {distro}:{posix_dir}. Pick your Codex home."
        )
    return PathValidationResult(ok=True, capabilities=frozenset({CAP_LIMITS, CAP_MODELS}))


def validate_codex_path(
    raw: str,
    active_config_dirs: List[str],
    run: Callable = subprocess.run,
) -> PathValidationResult:
    """The Codex counterpart of validate_manual_path, with the same result
    shape. A WSL UNC path is checked through wsl.exe, which makes this
    unsafe to call on the Tk thread for that shape."""
    expanded = os.path.expanduser(raw.strip())
    if not expanded:
        return PathValidationResult(ok=False, error="Enter a Codex home directory.")

    candidate = _strip_rollout_dir(expanded)

    unc = parse_wsl_unc(candidate)
    if unc is not None:
        distro, posix_dir = unc
        if not posixpath.isabs(posix_dir):
            return PathValidationResult(ok=False, error="Path must be absolute.")
        wsl_result = _check_wsl_codex_home(distro, posix_dir, run=run)
        if not wsl_result.ok:
            return wsl_result
        capabilities = wsl_result.capabilities
    else:
        path = Path(candidate)
        # Same leading-separator allowance as validate_manual_path.
        if not (path.is_absolute() or candidate.startswith(("/", "\\"))):
            return PathValidationResult(
                ok=False,
                error=f"'{raw}' is not an absolute path. Enter a full Codex home directory.",
            )
        if not path.is_dir():
            return PathValidationResult(ok=False, error=f"{candidate} does not exist.")
        if _has_claude_markers(path):
            return PathValidationResult(ok=False, error=_claude_picked_as_codex(candidate))
        if not any((path / name).is_dir() for name in _CODEX_ROLLOUT_DIRS):
            return PathValidationResult(
                ok=False,
                error=f"No sessions folder found in {candidate}. Pick your Codex home, usually ~/.codex.",
            )
        capabilities = frozenset({CAP_LIMITS, CAP_MODELS})
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

    return PathValidationResult(ok=True, config_dir=candidate, capabilities=capabilities)
