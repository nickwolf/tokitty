"""One-time, versioned migration of the pre-slug-key "default"
customization entry into its slug-keyed home. See
docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md, The
identity-key fix and migration, for the five upgrade-history rows this
covers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from tokitty.accounts import Account, IDENTITY_PREFIX
from tokitty.customize import Customization, SINGLE_KEY

MIGRATION_STATE_FILENAME = "migration_state.json"
CUSTOMIZATION_MIGRATION_KEY = "customization_default_key_v1"
LEGACY_ACCOUNT_LABELS_MIGRATION_KEY = "legacy_account_labels_v1"


def load_migration_state(state_dir: Path) -> Dict[str, bool]:
    path = Path(state_dir) / MIGRATION_STATE_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_migration_state(state_dir: Path, state: Dict[str, bool]) -> None:
    path = Path(state_dir) / MIGRATION_STATE_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def migrate_default_customization(
    state_dir: Path,
    accounts: Optional[List[Account]],
    customization_store: Dict[str, Customization],
) -> Dict[str, Customization]:
    """Compute the one-time transform when CUSTOMIZATION_MIGRATION_KEY is
    not yet set in migration_state.json.  The caller persists this result
    before marking the key complete; the transform never marks itself.
    Never use "does a slug entry already exist" as the completion test --
    the 2-to-1 row shows that check is unsound, since a stale entry from
    a REMOVED account can already occupy a slug key that has nothing to
    do with the current singleton).

    Only acts when accounts is a single-element list: that is the one
    case where "default"'s ownership is unambiguous. Two or more
    accounts (rows 4 and 5) leaves "default" alone rather than guess.
    """
    state = load_migration_state(state_dir)
    if state.get(CUSTOMIZATION_MIGRATION_KEY):
        return customization_store

    store = dict(customization_store)
    default_entry = store.get(SINGLE_KEY)
    if default_entry is not None and accounts and len(accounts) == 1:
        slug = accounts[0].name
        store[slug] = default_entry
        del store[SINGLE_KEY]

    return store


def migrate_legacy_account_labels(
    state_dir: Path,
    accounts: Optional[List[Account]],
    customization_store: Dict[str, Customization],
) -> Dict[str, Customization]:
    """Preserve the labels shown by the pre-slug multi-account UI.

    Before ``initial_label`` stopped falling back to ``account.name``, a
    legacy two-or-more-account configuration showed that name whenever its
    stored label was blank.  Seed only those legacy, human-readable names;
    new opaque identity slugs must never become visible labels.
    """
    state = load_migration_state(state_dir)
    if state.get(LEGACY_ACCOUNT_LABELS_MIGRATION_KEY):
        return customization_store

    store = dict(customization_store)
    if accounts and len(accounts) >= 2:
        for account in accounts:
            if account.name.startswith(IDENTITY_PREFIX):
                continue
            current = store.get(account.name, Customization())
            if not current.label:
                store[account.name] = Customization(
                    colorway=current.colorway,
                    pattern=current.pattern,
                    overrides=dict(current.overrides),
                    label=account.name,
                )
    return store


def mark_customization_migration_complete(state_dir: Path, key: str) -> None:
    """Durably mark one migration after its transformed store is saved."""
    state = load_migration_state(state_dir)
    state[key] = True
    save_migration_state(state_dir, state)


def absorb_implicit_default(
    customization_store: Dict[str, Customization], new_slug: str
) -> Dict[str, Customization]:
    """Called by accounts_ui.py's Add flow, exactly once, only when
    accounts.json did not exist before this Add: the brand-new first
    explicit account inherits the running "default" look instead of a
    random one. "default" is left in place afterward (harmless, unused
    once the pane's key changes) rather than deleted, so a second Add in
    the same session does not re-trigger absorption into the wrong
    account."""
    store = dict(customization_store)
    default_entry = store.get(SINGLE_KEY)
    if default_entry is not None and new_slug not in store:
        store[new_slug] = default_entry
    return store
