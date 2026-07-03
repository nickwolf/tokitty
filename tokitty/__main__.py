"""Entry point: python -m tokitty."""
from __future__ import annotations

import os
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
from tokitty.display import format_countdown, format_reset_day, format_reset_time
from tokitty.lock import LockAcquisitionError, SingleInstanceLock
from tokitty.mood import compute_capped_substate, compute_mood, detect_activate, select_binding_capped_limit
from tokitty.paths import get_state_dir
from tokitty.poller import PollResult, Poller

# tkinter (and tokitty.ui, which imports it) is deliberately NOT imported
# at module level -- --debug-print must keep working on systems without a
# GUI toolkit installed (e.g. this project's own WSL dev environment).
# run_gui() imports both lazily, only when the GUI path actually runs.

DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"
UI_REFRESH_MS = 500


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


def _display_state_for(result: PollResult, previous: Optional[PollResult]) -> dict:
    """Translate a PollResult into what the UI should show: cat state,
    percentages, reset text (or a live countdown when capped), driving
    tag, credits line, hint, and whether to render dimmed.
    """
    if result.status != "ok" or result.snapshot is None:
        hints = {
            "stale_token": "token stale — open Claude Code",
            "credentials_unreachable": "can't find credentials",
            "ambiguous_credentials": "multiple installs — set TOKITTY_CREDENTIALS",
            "api_error": "API hiccup, retrying",
        }
        last_good = previous.snapshot if previous and previous.snapshot else None
        return {
            "state": "confused",
            "session_pct": last_good.session_pct if last_good else 0.0,
            "weekly_pct": last_good.weekly_pct if last_good else 0.0,
            "session_reset_text": "—",
            "weekly_reset_text": "—",
            "driving_tag": "",
            "credits_text": None,
            "hint_text": hints.get(result.status, "unknown error"),
            "dimmed": True,
        }

    snapshot = result.snapshot
    now = datetime.now(timezone.utc)
    binding = select_binding_capped_limit(snapshot.limits)

    if binding is not None:
        capped = compute_capped_substate(binding, now=now)
        countdown = format_countdown(capped.time_to_reset.total_seconds())
        if binding.kind == "session":
            session_text = countdown
            weekly_text = format_reset_day(snapshot.weekly_resets_at) if snapshot.weekly_resets_at else "—"
        else:
            session_text = format_reset_time(snapshot.session_resets_at) if snapshot.session_resets_at else "—"
            weekly_text = countdown
        state = capped.substate
        driving_tag = capped.driving_tag
    else:
        mood, driving_tag = compute_mood(snapshot.session_pct, snapshot.weekly_pct)
        state = mood
        session_text = format_reset_time(snapshot.session_resets_at) if snapshot.session_resets_at else "—"
        weekly_text = format_reset_day(snapshot.weekly_resets_at) if snapshot.weekly_resets_at else "—"

    if previous and previous.snapshot and detect_activate(previous.snapshot, snapshot):
        state = "activate"

    credits_text = None
    if snapshot.credits_used is not None and snapshot.credits_used > 0 and snapshot.credits_limit is not None:
        credits_text = f"${snapshot.credits_used:.2f} / ${snapshot.credits_limit:.2f}"

    return {
        "state": state,
        "session_pct": snapshot.session_pct,
        "weekly_pct": snapshot.weekly_pct,
        "session_reset_text": session_text,
        "weekly_reset_text": weekly_text,
        "driving_tag": driving_tag,
        "credits_text": credits_text,
        "hint_text": None,
        "dimmed": False,
    }


def run_gui() -> int:
    import tkinter as tk

    from tokitty.ui import TokittyWindow

    state_dir = get_state_dir()
    lock = SingleInstanceLock(state_dir)
    try:
        lock.acquire()
    except LockAcquisitionError:
        print("Tokitty is already running.", file=sys.stderr)
        return 1

    root = tk.Tk()
    window = TokittyWindow(root, state_dir)

    debug_state = os.environ.get(DEBUG_STATE_ENV)
    if debug_state:
        window.render(
            state=debug_state, session_pct=0.0, weekly_pct=0.0,
            session_reset_text="—", weekly_reset_text="—",
            driving_tag="debug", credits_text=None, hint_text=None, dimmed=False,
        )
        root.mainloop()
        lock.release()
        return 0

    poller = Poller(fetch_fn=build_fetch_fn())
    window.on_refresh_requested = poller.request_refresh
    previous_holder = {"result": None}

    def tick():
        latest = poller.get_latest()
        if latest is not None:
            display = _display_state_for(latest, previous_holder["result"])
            window.render(**display)
            previous_holder["result"] = latest
        root.after(UI_REFRESH_MS, tick)

    poller.start()
    root.after(UI_REFRESH_MS, tick)

    try:
        root.mainloop()
    finally:
        poller.stop()
        lock.release()

    return 0


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--debug-print" in argv:
        return debug_print()
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
