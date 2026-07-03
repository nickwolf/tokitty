"""Entry point: python -m tokitty."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from tokitty.api import ApiError, fetch_usage, parse_usage_response
from tokitty.credentials import (
    AmbiguousCredentialsError,
    CredentialsError,
    describe_source,
    is_token_expired,
    load_credentials,
    resolve_credentials_source,
)
from tokitty.poller import PollResult


def build_fetch_fn():
    def fetch() -> PollResult:
        now = datetime.now(timezone.utc)
        try:
            source = resolve_credentials_source()
        except AmbiguousCredentialsError as exc:
            return PollResult(status="ambiguous_credentials", snapshot=None, message=str(exc), fetched_at=now)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)

        try:
            creds = load_credentials(source)
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


def debug_print() -> int:
    result = build_fetch_fn()()
    print(f"status: {result.status}")
    if result.message:
        print(f"message: {result.message}")
    if result.source_description:
        print(f"credentials source: {result.source_description}")
    if result.snapshot is not None:
        s = result.snapshot
        print(f"session: {s.session_pct:.1f}% (resets {s.session_resets_at})")
        print(f"weekly:  {s.weekly_pct:.1f}% (resets {s.weekly_resets_at})")
        if s.credits_used is not None and s.credits_limit is not None:
            print(f"credits: ${s.credits_used:.2f} / ${s.credits_limit:.2f}")
    return 0


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--debug-print" in argv:
        return debug_print()
    print("GUI not wired up yet -- run with --debug-print for now.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
