"""The Accounts dialog's pure logic and worker launchers, toolkit-free.

Shared by the Qt Accounts window. Tests that fake default_codex_home or
apply_account_mutation patch this module, where those names are looked up.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from tokitty.accounts import (
    DEFAULT_PROVIDER,
    Account,
    canonicalize_locator,
    load_accounts_result,
    parse_wsl_unc,
)
from tokitty.customize import Customization
from tokitty.hooks_install import apply_account_mutation, retry_pending_hook_op
from tokitty.manual_path import PathValidationResult, validate_codex_path, validate_manual_path
from tokitty.providers.codex import default_codex_home
from tokitty.wsl_probe import (
    wsl_config_dir_from_credentials,
    wsl_sessions_dir_from_credentials,
)

CODEX_KIND = "codex"


@dataclass(frozen=True)
class RowSpec:
    slug: str
    config_dir: str
    display_label: str
    remove_enabled: bool
    provider: str = DEFAULT_PROVIDER


@dataclass(frozen=True)
class DiscoveredPathSpec:
    distro: str
    credentials_path: str
    config_dir: str
    sessions_dir: str


def _fallback_label(index: int) -> str:
    return f"Cat {index + 1}"


def _parses_as_oauth_file(path: Path) -> bool:
    from tokitty.manual_path import _parses_as_oauth

    try:
        return _parses_as_oauth(path.read_text(encoding="utf-8"))
    except OSError:
        return False


def local_codex_facts(config_dir: str):
    """(has_rollouts,) for a Codex home, or None when answering would touch a
    WSL UNC path from the GUI thread. Same rules as local_capabilities."""
    if parse_wsl_unc(config_dir) is not None and sys.platform == "win32":
        return None
    try:
        path = Path(config_dir)
        for name in ("sessions", "archived_sessions"):
            folder = path / name
            if folder.is_dir() and next(folder.iterdir(), None) is not None:
                return (True,)
        return (False,)
    except OSError:
        return None


def local_capabilities(config_dir: str):
    """(has_credentials, has_transcripts) for a config dir, or None when
    answering would need a subprocess.

    Deliberately cheap and local-only. A WSL UNC path would need wsl.exe
    calls, and this runs on the GUI thread while the window is being built.
    Unknown is rendered as nothing rather than as a guess.
    """
    if parse_wsl_unc(config_dir) is not None and sys.platform == "win32":
        return None
    try:
        path = Path(config_dir)
        credentials = path / ".credentials.json"
        has_credentials = credentials.is_file() and _parses_as_oauth_file(credentials)
        return has_credentials, (path / "projects").is_dir()
    except OSError:
        return None


def codex_hook_text(config_dir: str, state_dir: Path) -> Optional[str]:
    """The approval line for a Codex row, or None when no hooks are installed
    or the home is not read from here. Called only after _local_codex_facts
    accepted the home, so a WSL UNC path on Windows never gets here."""
    from tokitty import codex_trust

    try:
        status = codex_trust.codex_hook_status(config_dir, state_dir)
        if status == codex_trust.APPROVED:
            if codex_trust.codex_activity_hint(config_dir, state_dir):
                return "hooks: approved, no activity from Codex hooks yet"
            return "hooks: approved"
    except OSError:
        return None
    if status == codex_trust.NEEDS_APPROVAL:
        return "hooks: waiting for approval in Codex"
    if status == codex_trust.UNREADABLE:
        return "hooks: can't read Codex hook state"
    return None


def describe_capabilities(has_credentials: bool, has_transcripts: bool) -> Tuple[str, str]:
    """The two fact lines shown under an account row.

    Worded so the target user is not told they are broken: "not available
    (no credentials found)" sitting next to a populated local-usage line
    tells the whole story, where the old dialog just refused the directory
    as "not a valid Claude Code credentials file" and turned them away.
    """
    subscription = (
        "subscription usage: available"
        if has_credentials
        else "subscription usage: not available (no credentials found)"
    )
    local = (
        "local usage: transcripts found"
        if has_transcripts
        else "local usage: no transcripts found"
    )
    return subscription, local


def describe_codex_capabilities(has_rollouts: bool) -> Tuple[str, str]:
    """The Codex row's fact lines. Codex has no credentials to check: its
    rate limits ride along inside the same rollouts its ledger is read
    from, so one directory answers both."""
    local = (
        "local usage: rollouts found"
        if has_rollouts
        else "local usage: no rollouts found"
    )
    return "rate limits: read from rollouts", local


def build_row_specs(accounts: List[Account], customization_store: Dict[str, Customization]) -> List[RowSpec]:
    """Pure, toolkit-free: one display row per account. Never shows the raw
    slug as a fallback label -- it's an opaque SHA-256-derived string."""
    remove_enabled = len(accounts) > 1
    rows = []
    for index, account in enumerate(accounts):
        custom = customization_store.get(account.name)
        label = custom.label if custom and custom.label else _fallback_label(index)
        rows.append(RowSpec(
            slug=account.name, config_dir=account.config_dir,
            display_label=label, remove_enabled=remove_enabled,
            provider=account.provider,
        ))
    return rows


def build_discovered_path_specs(
    matches: Sequence[Tuple[str, str]],
) -> List[DiscoveredPathSpec]:
    """Convert discovery's WSL-side matches into manager-ready paths."""
    return [
        DiscoveredPathSpec(
            distro=distro,
            credentials_path=credentials_path,
            config_dir=wsl_config_dir_from_credentials(distro, credentials_path),
            sessions_dir=wsl_sessions_dir_from_credentials(distro, credentials_path),
        )
        for distro, credentials_path in matches
    ]


def discover_local_codex_home(accounts: Sequence[Account]) -> Optional[str]:
    """The local Codex home, when it has rollouts and is not an account yet.

    Local only. Probing each WSL distro for a .codex dir would need
    wsl.exe, which boots stopped distros; the Claude discovery already pays
    that cost off the GUI thread, and this runs on it.
    """
    home = default_codex_home()
    try:
        if not any((Path(home) / name).is_dir() for name in ("sessions", "archived_sessions")):
            return None
        locator = canonicalize_locator(home)
    except (OSError, ValueError):
        return None
    for account in accounts:
        try:
            if canonicalize_locator(account.config_dir) == locator:
                return None
        except ValueError:
            continue
    return home


def _same_dir(a: str, b: str) -> bool:
    try:
        return canonicalize_locator(a) == canonicalize_locator(b)
    except ValueError:
        return False


def _needs_off_thread_validation(provider: str, raw: str) -> bool:
    """A Codex UNC path is validated through wsl.exe. The Claude path keeps
    validating inline, exactly as it did before providers existed."""
    return provider != DEFAULT_PROVIDER and parse_wsl_unc(raw.strip()) is not None


def validate_account_path(provider: str, raw: str, active_config_dirs: List[str]) -> PathValidationResult:
    if provider == CODEX_KIND:
        return validate_codex_path(raw, active_config_dirs=active_config_dirs)
    if provider == DEFAULT_PROVIDER:
        return validate_manual_path(raw, active_config_dirs=active_config_dirs)
    return PathValidationResult(ok=False, error=f"Adding a {provider} account is not supported.")


def reconcile_before_save(state_dir: Path, in_memory_accounts: List[Account]) -> List[Account]:
    """Reload accounts.json immediately before every save, so two
    independently opened dialogs cannot silently clobber each other's
    changes with a stale in-memory list."""
    result = load_accounts_result(state_dir)
    if result.state == "malformed":
        raise ValueError("accounts.json became malformed before it could be saved")
    if result.state in ("valid_empty", "valid_non_empty"):
        return result.accounts
    return in_memory_accounts


def _run_mutation_off_thread(state_dir: Path, accounts: List[Account], op: str,
                              config_dir: str, on_done, provider: str = DEFAULT_PROVIDER) -> None:
    def worker():
        try:
            outcome = apply_account_mutation(
                state_dir, accounts, op, config_dir, provider=provider
            )
        except Exception as exc:
            outcome = exc
        on_done(outcome)

    threading.Thread(target=worker, daemon=True).start()


def _run_validation_off_thread(provider: str, raw: str, active_dirs: List[str], on_done) -> None:
    def worker():
        try:
            outcome = validate_account_path(provider, raw, active_dirs)
        except Exception as exc:
            outcome = PathValidationResult(ok=False, error=str(exc))
        on_done(outcome)

    threading.Thread(target=worker, daemon=True).start()


def _run_retry_off_thread(state_dir: Path, on_done) -> None:
    try:
        outcome = retry_pending_hook_op(state_dir)
    except Exception as exc:
        # A failed retry leaves the pending-op record in place for next time,
        # exactly like any other failed retry_pending_hook_op call.
        outcome = exc
    on_done(outcome)
