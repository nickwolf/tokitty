"""Pure, injectable startup decisions for run_gui: whether to auto-open
the Accounts manager, kept separate from TokittyWindow so the 5
gui-marked tests that construct TokittyWindow directly never touch WSL.
See docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md,
First-run auto-open.
"""
from __future__ import annotations

from typing import Optional

ACTION_ACCOUNTS = "accounts"
ACTION_USAGE_SETUP = "usage_setup"


def resolve_first_run_action(
    accounts_state: str,
    env_override_set: bool,
    home_relative_exists: bool,
    keychain_available: bool,
    platform: str,
    wsl_match_count: int,
    transcripts_found: bool = False,
) -> Optional[str]:
    """What, if anything, to open on first run.

    ACTION_ACCOUNTS is the original behavior, unchanged: several usable
    credential sources and no accounts.json, so tokitty must not guess
    which one was meant.

    ACTION_USAGE_SETUP is new, and covers the case that previously got
    nothing at all. An API-key user has no OAuth credentials anywhere, so
    every arm below used to fall through to None while the pane showed
    "can't find credentials" forever, with nothing on screen suggesting
    the widget could still work for them. If transcripts exist, it can.
    """
    if accounts_state != "absent":
        return None
    if env_override_set or home_relative_exists:
        return None
    if platform == "darwin" and keychain_available:
        return None
    if wsl_match_count > 1:
        return ACTION_ACCOUNTS
    if wsl_match_count == 0 and transcripts_found:
        return ACTION_USAGE_SETUP
    return None


# Bumped when an onboarding step runs, so it runs exactly once per user
# and a later step can be introduced deliberately.
ONBOARDING_MODELS_AUTOSELECT = 1


def should_auto_select_models(
    poll_status: Optional[str],
    scan_status: Optional[str],
    has_records: bool,
    onboarding_version: int,
) -> bool:
    """Whether to switch this install into the per-model view, once.

    "credentials_unreachable plus a successful scan with records" is an
    unambiguous signature: no OAuth credential resolves anywhere in
    credentials.py's precedence order, and yet Claude Code has been
    writing billing records. That is an API-key user, and no subscription
    account can produce it, because a subscription account has credentials
    by definition.

    Three lookalike states must NOT trigger it:

    - "stale_token" with no capped binding is the documented resting look.
      A work account's token expires about an hour after that account's
      Claude Code last ran, so it is a subscriber's normal overnight
      state; switching their view because they stopped working at 6pm
      would be a bug.
    - "api_error" is transient by construction and already has backoff.
    - "keychain_denied" is a permission the user can still grant, and its
      recovery hint has to stay on screen.

    An unavailable scan is also excluded: "we could not read the
    transcripts" is not evidence of anything, and switching on it would
    trade one broken pane for a differently broken one.
    """
    if onboarding_version >= ONBOARDING_MODELS_AUTOSELECT:
        return False
    if poll_status != "credentials_unreachable":
        return False
    if scan_status not in ("ok", "partial"):
        return False
    return has_records
