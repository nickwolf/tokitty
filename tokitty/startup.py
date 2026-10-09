"""Pure, injectable startup decisions for run_gui, kept separate from
TokittyWindow so the gui-marked tests that construct it directly never
touch WSL. The first-run decision lives in first_run.py.
"""
from __future__ import annotations

from typing import Optional

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
