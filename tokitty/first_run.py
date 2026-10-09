"""Backend for the first-run walkthrough (#88). No Tk: the UI calls in here,
and the discovery and save functions are meant for worker threads.

A state dir counts as fresh when it holds nothing but the single-instance
lock, because every GUI launch since v0.1.0 writes customization.json.
"""
from __future__ import annotations

import os
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Mapping, Optional

from tokitty.accounts import (
    ACCOUNTS_FILENAME,
    DEFAULT_PROVIDER,
    Account,
    assign_identity_slug,
    canonicalize_locator,
    load_identity_history,
    parse_wsl_unc,
    save_accounts,
    save_identity_history,
)
from tokitty.credentials import (
    ENV_OVERRIDE,
    CredentialsError,
    KeychainCredentialsSource,
    resolve_credentials_source,
)
from tokitty.customize import (
    Customization,
    load_customization,
    save_customization_entry,
)
from tokitty.lock import LOCK_FILENAME
from tokitty.migration import absorb_implicit_default
from tokitty.providers.codex import default_codex_home
from tokitty.randomize import random_look
from tokitty import sprites
from tokitty.settings import Settings, update_settings
from tokitty.wsl_probe import wsl_config_dir_from_credentials

FORCE_ENV = "TOKITTY_FIRST_RUN"
CODEX_PROVIDER = "codex"

LABEL_KEYCHAIN = "Signed in (Keychain)"
LABEL_HOME = "Signed in (~/.claude)"
LABEL_NONE = "Not signed in yet"


@dataclass(frozen=True)
class Candidate:
    provider: str
    # In the form accounts.json stores: a native path, or the
    # \\wsl.localhost\<distro>\... UNC for a WSL install on Windows.
    config_dir: str
    where: str
    signed_in: bool
    # macOS has no account picking (accounts.json is file-only there), so
    # its one implicit row is shown but never saved.
    read_only: bool = False
    # Extra display text, e.g. the macOS sign-in source.
    detail: str = ""

    @property
    def default_checked(self) -> bool:
        # A transcripts-only row gives a pane with history and no limits,
        # so it starts unchecked.
        return self.signed_in


def is_fresh_state_dir(state_dir) -> bool:
    path = Path(state_dir)
    if not path.exists():
        return True
    return all(entry.name == LOCK_FILENAME for entry in path.iterdir())


def classify(
    state_dir,
    settings: Settings,
    *,
    excluded: bool,
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    """Whether this launch shows the walkthrough. Call it once the
    single-instance lock is held, before anything else touches the state
    dir. `excluded` covers debug launches, --after-update and
    --apply-update: they neither show it nor write the marker.

    A fresh dir is marked "pending" right here, so a walkthrough killed
    midway shows again. TOKITTY_FIRST_RUN=1 forces it without changing what
    is persisted (finishing still writes "done")."""
    if excluded:
        return False
    env = os.environ if environ is None else environ
    if env.get(FORCE_ENV) == "1":
        return True
    if is_fresh_state_dir(state_dir):
        update_settings(state_dir, first_run="pending")
        return True
    return settings.first_run == "pending"


def mark_done(state_dir) -> None:
    update_settings(state_dir, first_run="done")


def macos_source_label(resolve: Callable = resolve_credentials_source) -> str:
    """Where macOS sign-in comes from. resolve_credentials_source already
    lets a local credentials file win over the Keychain. Probes the
    filesystem and the Keychain (attribute-only, no prompt), so call it on a
    worker."""
    try:
        source = resolve()
    except (CredentialsError, OSError):
        return LABEL_NONE
    return LABEL_KEYCHAIN if isinstance(source, KeychainCredentialsSource) else LABEL_HOME


def discover_candidates(
    credentials_cache,
    *,
    platform: str = sys.platform,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    codex_home: Optional[str] = None,
    macos_label: Callable[[], str] = macos_source_label,
) -> List[Candidate]:
    """Claude Code and Codex installs worth watching, native Claude first,
    then WSL Claude, then Codex. Runs on a worker: the WSL sweeps wake
    distros, and go through `credentials_cache` (the process's
    WslCredentialsCache) so each runs at most once per process."""
    env = os.environ if environ is None else environ
    base = Path(home) if home is not None else Path.home()
    if platform == "darwin":
        label = macos_label()
        return [Candidate(
            provider=DEFAULT_PROVIDER, config_dir=str(base / ".claude"), where="This PC",
            signed_in=label != LABEL_NONE, read_only=True, detail=label,
        )]
    found: List[Candidate] = []
    native = _native_claude(env, base)
    if native is not None:
        found.append(native)
    if platform == "win32":
        found.extend(_wsl_claude(credentials_cache))
    codex = _codex(codex_home if codex_home is not None else (
        str(base / ".codex") if home is not None else default_codex_home()
    ))
    if codex is not None:
        found.append(codex)
    return found


def _native_claude(env: Mapping[str, str], base: Path) -> Optional[Candidate]:
    override = env.get(ENV_OVERRIDE)
    if override:
        if parse_wsl_unc(override) is not None:
            # Python never opens a WSL UNC path (it would boot the distro),
            # so the file is taken on trust: the user pointed at it.
            parent = override.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[0]
            distro = parse_wsl_unc(parent)
            where = f"WSL: {distro[0]}" if distro else "This PC"
            return Candidate(DEFAULT_PROVIDER, parent, where, signed_in=True)
        parent_dir = Path(override).parent
        signed_in = Path(override).is_file()
        if signed_in or (parent_dir / "projects").is_dir():
            return Candidate(DEFAULT_PROVIDER, str(parent_dir), "This PC", signed_in)
        return None
    config = base / ".claude"
    signed_in = (config / ".credentials.json").is_file()
    if signed_in or (config / "projects").is_dir():
        return Candidate(DEFAULT_PROVIDER, str(config), "This PC", signed_in)
    return None


def _wsl_claude(credentials_cache) -> List[Candidate]:
    merged = {}
    for distro, credentials_path in credentials_cache.all_matches():
        config_dir = wsl_config_dir_from_credentials(distro, credentials_path)
        merged[(distro, config_dir)] = True
    for distro, posix_dir in credentials_cache.claude_dirs():
        # The helper wants a credentials path and returns its parent's UNC,
        # which is exactly what accounts.json stores for this dir.
        config_dir = wsl_config_dir_from_credentials(
            distro, posixpath.join(posix_dir, ".credentials.json")
        )
        merged.setdefault((distro, config_dir), False)
    return [
        Candidate(DEFAULT_PROVIDER, config_dir, f"WSL: {distro}", signed_in)
        for (distro, config_dir), signed_in in merged.items()
    ]


def _codex(home: str) -> Optional[Candidate]:
    # Local only, like accounts_ui.discover_local_codex_home (which imports
    # Tk, so it cannot run here): probing WSL for .codex would boot distros.
    if any((Path(home) / name).is_dir() for name in ("sessions", "archived_sessions")):
        return Candidate(CODEX_PROVIDER, home, "This PC", signed_in=True)
    return None


def save_picked_accounts(state_dir, picked: List[Candidate]) -> List[Account]:
    """Write accounts.json for the picked candidates the way the Accounts
    dialog's Add does (slug, identity history, look), but with no hook
    install and no pending-op journal: the walkthrough asks about hooks on
    its next step. An empty pick writes nothing.

    Only valid on a fresh install. An existing accounts.json would be
    overwritten, so it raises instead."""
    if not picked:
        return []
    if any(candidate.read_only for candidate in picked):
        raise ValueError("a read-only candidate cannot be saved")
    state = Path(state_dir)
    if (state / ACCOUNTS_FILENAME).exists():
        raise FileExistsError(f"{ACCOUNTS_FILENAME} already exists; the walkthrough is for fresh installs")

    accounts: List[Account] = []
    absorbed_default = False
    for candidate in picked:
        history = load_identity_history(state)
        locator = canonicalize_locator(candidate.config_dir)
        taken = set(history.values()) | {account.name for account in accounts}
        slug, history = assign_identity_slug(locator, taken, history)
        save_identity_history(state, history)
        accounts.append(Account(name=slug, config_dir=candidate.config_dir, provider=candidate.provider))

        store = load_customization(state)
        if candidate.provider == DEFAULT_PROVIDER and not absorbed_default:
            # The first Claude account inherits the running default look.
            absorbed_default = True
            absorbed = absorb_implicit_default(store, slug)
            if slug in absorbed:
                save_customization_entry(state, slug, absorbed[slug])
        elif slug not in store:
            colorway, pattern = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))
            save_customization_entry(state, slug, Customization(colorway=colorway, pattern=pattern))
    save_accounts(state, accounts)
    return accounts
