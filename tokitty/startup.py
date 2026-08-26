"""Pure, injectable startup decisions for run_gui: whether to auto-open
the Accounts manager, kept separate from TokittyWindow so the 5
gui-marked tests that construct TokittyWindow directly never touch WSL.
See docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md,
First-run auto-open.
"""
from __future__ import annotations


def should_auto_open(
    accounts_state: str,
    env_override_set: bool,
    home_relative_exists: bool,
    keychain_available: bool,
    platform: str,
    wsl_match_count: int,
) -> bool:
    """True only when accounts.json is absent AND nothing earlier in
    credentials.py's resolution precedence (TOKITTY_CREDENTIALS, then
    ~/.claude, then Keychain on darwin, then WSL) would already resolve
    unambiguously before WSL is even consulted, AND the async WSL
    discovery found more than one usable credential source."""
    if accounts_state != "absent":
        return False
    if env_override_set or home_relative_exists:
        return False
    if platform == "darwin" and keychain_available:
        return False
    return wsl_match_count > 1
