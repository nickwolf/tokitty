"""The Claude Code provider: the OAuth usage endpoint plus the on-disk
transcript ledger.

Every function below moved here verbatim from __main__, which still
re-exports them -- they were always Claude-specific, and naming them so is
most of what the provider seam is. Behavior is unchanged.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from tokitty.api import ApiError, fetch_usage, parse_usage_response
from tokitty.credentials import (
    AmbiguousCredentialsError,
    CredentialLoader,
    CredentialsError,
    KeychainAccessError,
    describe_source,
    is_token_expired,
    load_credentials,
    resolve_credentials_source,
)
from tokitty.poller import PollResult
from tokitty.providers.base import LedgerSource, ProviderCapabilities, _config_root_from, _join


def build_fetch_fn(config_dir: Optional[str] = None, loader: Optional[CredentialLoader] = None):
    # One loader per closure, i.e. per account: it caches that account's
    # Keychain reads and holds its sticky-denial state.
    loader = loader if loader is not None else CredentialLoader()

    def fetch() -> PollResult:
        now = datetime.now(timezone.utc)
        try:
            source = resolve_credentials_source(config_dir=config_dir)
        except AmbiguousCredentialsError as exc:
            return PollResult(status="ambiguous_credentials", snapshot=None, message=str(exc), fetched_at=now)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)

        try:
            creds = loader.load(source, load_fn=load_credentials)
        except KeychainAccessError as exc:
            # Must precede the CredentialsError branch -- it is a subclass.
            return PollResult(status="keychain_denied", snapshot=None, message=str(exc), fetched_at=now)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)

        if is_token_expired(creds):
            return PollResult(
                status="stale_token",
                snapshot=None,
                message="access token expired",
                fetched_at=now,
                source_description=describe_source(source),
            )

        try:
            raw = fetch_usage(creds["accessToken"])
        except ApiError as exc:
            status = "stale_token" if exc.status_code == 401 else "api_error"
            return PollResult(status=status, snapshot=None, message=str(exc), fetched_at=now)

        snapshot = parse_usage_response(raw)
        return PollResult(
            status="ok", snapshot=snapshot, message=None, fetched_at=now, source_description=describe_source(source)
        )

    return fetch


def resolve_activity_sessions(config_dir: Optional[str] = None, credentials=None) -> Tuple[Optional[str], Optional[str]]:
    """Return (sessions_dir, distro_name) for the ActivityWatcher.

    distro_name is None on Linux/macOS (no WSL check needed) and on any
    resolution failure -- resolution failure always means "run without
    activity" (sessions_dir=None too), never a crash. Single default
    account for now (issue #7's scope); a future multi-account watcher
    would resolve one of these per account.

    With an explicit config_dir (from accounts.json): a WSL UNC dir stays
    UNC on win32 (with the distro name parsed out for the running-distro
    check) and is translated to its posix path on Linux; a plain dir is
    used as-is on either platform. Without one: v1 behavior below.
    """
    if config_dir:
        from tokitty.accounts import parse_wsl_unc

        unc = parse_wsl_unc(config_dir)
        if sys.platform == "win32":
            if unc is not None:
                distro = unc[0]
                sessions = config_dir.rstrip("\\/") + "\\tokitty\\sessions"
                return sessions, distro
            return str(Path(config_dir) / "tokitty" / "sessions"), None
        base = unc[1] if unc is not None else config_dir
        # This branch's result is always a Linux/WSL sessions path. Build it with
        # explicit "/" rather than pathlib, which emits host-native separators
        # (backslashes when Tokitty itself runs on Windows) -- mirroring the
        # win32 branch above, which likewise concatenates its separators.
        return base.rstrip("/") + "/tokitty/sessions", None

    if sys.platform != "win32":
        try:
            from tokitty.hooks_install import get_config_dirs

            config_dir = get_config_dirs()[0]
        except Exception:
            config_dir = str(Path.home() / ".claude")
        return str(Path(config_dir) / "tokitty" / "sessions"), None

    from tokitty.wsl_probe import find_wsl_credentials, wsl_sessions_dir_from_credentials

    # `credentials` is the process-wide WslCredentialsCache when run_gui
    # wired one up (issue #52). Falling back to the bare function keeps the
    # tests that call this resolver directly working unchanged.
    try:
        if credentials is not None:
            distro, wsl_credentials_path = credentials.single()
        else:
            distro, wsl_credentials_path = find_wsl_credentials()
    except CredentialsError:
        return None, None

    sessions_dir = wsl_sessions_dir_from_credentials(distro, wsl_credentials_path)
    return sessions_dir, distro


def resolve_projects_dir(config_dir: Optional[str] = None, credentials=None):
    """(projects_dir, distro_name) for the transcript scanner.

    Unlike resolve_activity_sessions, the no-config_dir fallback here must
    NOT be credential-gated. An API-key user has a full billing ledger on
    disk and no OAuth credentials anywhere, so a credentials probe reports
    that they have no Claude Code install at all -- which would make the
    per-model view permanently empty for exactly the people it exists for.
    Credentials are still tried first, so a subscriber's resolution is
    unchanged; the transcript probe is a fallback, not a replacement.
    """
    if config_dir:
        root, distro = _config_root_from(config_dir)
        return _join(root, "projects"), distro

    if sys.platform != "win32":
        try:
            from tokitty.hooks_install import get_config_dirs

            resolved = get_config_dirs()[0]
        except Exception:
            resolved = str(Path.home() / ".claude")
        return str(Path(resolved) / "projects"), None

    from tokitty.wsl_probe import (
        find_all_wsl_claude_dirs,
        find_wsl_credentials,
        wsl_config_dir_from_credentials,
    )

    try:
        if credentials is not None:
            distro, wsl_credentials_path = credentials.single()
        else:
            distro, wsl_credentials_path = find_wsl_credentials()
    except CredentialsError:
        try:
            matches = find_all_wsl_claude_dirs()
        except CredentialsError:
            matches = []
        if len(matches) != 1:
            # Zero means nothing to read; more than one is the ambiguity
            # the Accounts dialog exists to resolve, and guessing would
            # silently cost the wrong account.
            return None, None
        distro, posix_dir = matches[0]
        unc = "\\\\wsl.localhost\\" + distro + "\\" + posix_dir.lstrip("/").replace("/", "\\")
        return unc + "\\projects", distro

    config_root = wsl_config_dir_from_credentials(distro, wsl_credentials_path)
    return config_root.rstrip("\\/") + "\\projects", distro


class ClaudeProvider:
    """The v1 behavior, now named. Claude Code is the only harness that
    answers all three questions, which is why the rest of the app was
    written directly against it."""

    kind = "claude"
    display_name = "Claude Code"
    capabilities = ProviderCapabilities(rate_limits=True, token_ledger=True, activity=True)

    def build_fetch_fn(self, config_dir: Optional[str] = None, loader=None):
        return build_fetch_fn(config_dir, loader=loader)

    def resolve_activity_sessions(self, config_dir: Optional[str] = None, credentials=None):
        return resolve_activity_sessions(config_dir, credentials=credentials)

    def resolve_ledger(self, config_dir: Optional[str] = None, credentials=None):
        projects_dir, distro = resolve_projects_dir(config_dir, credentials=credentials)
        if not projects_dir:
            return None
        from tokitty.usage_scan import TranscriptScanner

        return LedgerSource(
            root=projects_dir,
            distro_name=distro,
            make_scanner=lambda now_fn=None: TranscriptScanner(projects_dir, now_fn=now_fn),
        )
