"""Pure helpers behind the new-session presets dialog.

No Tk here, so the rules the dialog enforces are testable anywhere.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from tokitty.accounts import DEFAULT_PROVIDER, Account, parse_wsl_unc
from tokitty.settings import PRESET_NAME_MAX, _preset
from tokitty.streamdock.launch import build_command


def claude_accounts(accounts: List[Optional[Account]]) -> List[Tuple[int, Account]]:
    """(index, account) for the accounts a preset can launch. The index is the
    position in the full list, which is what `account_index` means."""
    return [(i, a) for i, a in enumerate(accounts) if a is not None and a.provider == DEFAULT_PROVIDER]


def is_wsl(account: Account) -> bool:
    return parse_wsl_unc(account.config_dir) is not None


def preset_for(name: str, account: Account, cwd: str, accounts: List[Optional[Account]]) -> dict:
    """The preset dict for a name, an account and a folder. `env` and `distro`
    follow from where the account's config dir lives."""
    preset = {"name": name.strip(), "account": account.name, "account_index": accounts.index(account)}
    unc = parse_wsl_unc(account.config_dir)
    if unc is None:
        preset.update(env="native", cwd=cwd.strip())
    else:
        preset.update(env="wsl", distro=unc[0], cwd=cwd.strip())
    return preset


def check_preset(preset: dict, account: Account, others: Iterable[dict] = ()) -> Optional[str]:
    """A short reason the preset cannot launch, or None. `others` is every
    other saved preset, so a repeated name is refused."""
    name = str(preset.get("name", "")).strip()
    if not name:
        return "Name is required"
    if len(name) > PRESET_NAME_MAX:
        return f"Name is longer than {PRESET_NAME_MAX} characters"
    if any(isinstance(o, dict) and o.get("name") == name for o in others):
        return "Another preset already has that name"
    if not str(preset.get("cwd", "")).strip():
        return "Folder is required"
    if _preset(preset) is None:
        return "Preset is not valid"
    try:
        build_command(preset, account)
    except ValueError as exc:
        text = str(exc)
        return text[:1].upper() + text[1:]
    return None
