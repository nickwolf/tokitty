"""Entry point: python -m tokitty."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from tokitty.activity import ActivityTracker
from tokitty.activity_watcher import ActivityWatcher
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
from tokitty.pose import resolve_pose
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


def resolve_activity_sessions() -> Tuple[Optional[str], Optional[str]]:
    """Return (sessions_dir, distro_name) for the ActivityWatcher.

    distro_name is None on Linux/macOS (no WSL check needed) and on any
    resolution failure -- resolution failure always means "run without
    activity" (sessions_dir=None too), never a crash. Single default
    account for now (issue #7's scope); a future multi-account watcher
    would resolve one of these per account.
    """
    if sys.platform != "win32":
        try:
            from tokitty.hooks_install import get_config_dirs

            config_dir = get_config_dirs()[0]
        except Exception:
            config_dir = str(Path.home() / ".claude")
        return str(Path(config_dir) / "tokitty" / "sessions"), None

    from tokitty.wsl_probe import find_wsl_credentials, wsl_sessions_dir_from_credentials

    try:
        distro, wsl_credentials_path = find_wsl_credentials()
    except CredentialsError:
        return None, None

    sessions_dir = wsl_sessions_dir_from_credentials(distro, wsl_credentials_path)
    return sessions_dir, distro


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

    sessions_dir, distro_name = resolve_activity_sessions()
    if sessions_dir is not None:
        watcher = ActivityWatcher(sessions_dir, ActivityTracker(), distro_name=distro_name)
        watcher._tick_once()  # one-shot snapshot; no background thread for a single debug print
        activity = watcher.get_latest()
        if activity is not None:
            label = f" ({activity.tool_label})" if activity.tool_label else ""
            print(f"activity: {activity.state}{label}")

    return 0


# Shown only once our own clock says a cached countdown should already
# have hit zero and we still can't confirm it -- see _display_state_for.
_STALE_HINTS = {
    "stale_token": "token expired, reopen Claude Code",
    "credentials_unreachable": "can't confirm, credentials unreachable",
    "ambiguous_credentials": "can't confirm, set TOKITTY_CREDENTIALS",
    "api_error": "can't confirm, API hiccup",
}


def _display_from_snapshot(snapshot, now: datetime) -> dict:
    """Compute state/percentages/reset text/credits from a snapshot as of
    `now`. A countdown only needs a resets_at timestamp and a clock, so
    this works equally well for a fresh snapshot or a cached one from an
    earlier successful poll.
    """
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
    }


def _display_state_for(result: PollResult, previous: Optional[PollResult], now: Optional[datetime] = None) -> dict:
    """Translate a PollResult into what the UI should show: cat state,
    percentages, reset text (or a live countdown when capped), driving
    tag, credits line, hint, and whether to render dimmed.

    `previous` is expected to be the last *successful* PollResult (see
    _next_last_good), not just whatever the previous tick saw -- a
    countdown to a known resets_at only needs a clock, not a live
    connection, so a stale token (or any other transient fetch failure)
    keeps showing that same cached countdown instead of blanking out.
    A small warning only appears once our own clock says the cached
    countdown should already be done and we still can't confirm it.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if result.status == "ok" and result.snapshot is not None:
        display = _display_from_snapshot(result.snapshot, now)
        if previous and previous.snapshot and detect_activate(previous.snapshot, result.snapshot):
            display["state"] = "activate"
        display["hint_text"] = None
        display["dimmed"] = False
        return display

    last_good = previous.snapshot if previous and previous.snapshot else None
    if last_good is not None:
        display = _display_from_snapshot(last_good, now)
        binding = select_binding_capped_limit(last_good.limits)
        overdue = binding is not None and compute_capped_substate(binding, now=now).time_to_reset.total_seconds() <= 0
        display["hint_text"] = _STALE_HINTS.get(result.status, "can't confirm, reconnect") if overdue else None
        display["dimmed"] = overdue
        return display

    hints = {
        "stale_token": "token stale, open Claude Code",
        "credentials_unreachable": "can't find credentials",
        "ambiguous_credentials": "multiple installs, set TOKITTY_CREDENTIALS",
        "api_error": "API hiccup, retrying",
    }
    return {
        "state": "confused",
        "session_pct": 0.0,
        "weekly_pct": 0.0,
        "session_reset_text": "—",
        "weekly_reset_text": "—",
        "driving_tag": "",
        "credits_text": None,
        "hint_text": hints.get(result.status, "unknown error"),
        "dimmed": True,
    }


def _next_last_good(latest: PollResult, last_good: Optional[PollResult]) -> Optional[PollResult]:
    """Track the most recent *successful* poll, independent of how many
    failed polls land in between -- so a stale token doesn't wipe out the
    cached snapshot _display_state_for needs for its countdown fallback.
    """
    return latest if latest.status == "ok" else last_good


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
    last_good_holder = {"result": None}

    sessions_dir, distro_name = resolve_activity_sessions()
    watcher = ActivityWatcher(sessions_dir, ActivityTracker(), distro_name=distro_name)

    def tick():
        latest = poller.get_latest()
        if latest is not None:
            display = _display_state_for(latest, last_good_holder["result"])
            activity = watcher.get_latest()
            pose = resolve_pose(display["state"], activity)
            display["state"] = pose["sprite_state"]
            display["tool_label"] = pose["tool_label"]
            display["accent"] = pose["accent"]
            window.render(**display)
            last_good_holder["result"] = _next_last_good(latest, last_good_holder["result"])
        root.after(UI_REFRESH_MS, tick)

    poller.start()
    watcher.start()
    root.after(UI_REFRESH_MS, tick)

    try:
        root.mainloop()
    finally:
        poller.stop()
        watcher.stop()
        lock.release()

    return 0


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--debug-print" in argv:
        return debug_print()
    if "--install-hooks" in argv:
        from tokitty.hooks_install import install_hooks

        return install_hooks()
    if "--uninstall-hooks" in argv:
        from tokitty.hooks_install import uninstall_hooks

        return uninstall_hooks()
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
