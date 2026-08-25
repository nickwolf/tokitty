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

from tokitty.accounts import Account
from tokitty.customize import Customization, SINGLE_KEY

MIGRATION_STATE_FILENAME = "migration_state.json"
CUSTOMIZATION_MIGRATION_KEY = "customization_default_key_v1"


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
    """Run once (tracked by CUSTOMIZATION_MIGRATION_KEY in
    migration_state.json, never by "does a slug entry already exist" --
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

    state[CUSTOMIZATION_MIGRATION_KEY] = True
    save_migration_state(state_dir, state)
    return store


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
