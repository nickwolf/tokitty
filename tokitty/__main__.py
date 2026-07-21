"""Entry point: python -m tokitty."""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from tokitty.accounts import Account
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
from tokitty.customize import Customization, SINGLE_KEY, effective_palette, load_customization, save_customization
from tokitty.display import format_countdown, format_reset_day, format_reset_time
from tokitty.lock import LockAcquisitionError, SingleInstanceLock
from tokitty.mood import compute_capped_substate, compute_mood, detect_activate, select_binding_capped_limit
from tokitty.paths import get_state_dir
from tokitty.pose import resolve_pose
from tokitty.poller import PollResult, Poller
from tokitty import sprites

# tkinter (and tokitty.ui, which imports it) is deliberately NOT imported
# at module level -- --debug-print must keep working on systems without a
# GUI toolkit installed (e.g. this project's own WSL dev environment).
# run_gui() imports both lazily, only when the GUI path actually runs.

DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"
UI_REFRESH_MS = 500


def build_fetch_fn(config_dir: Optional[str] = None):
    def fetch() -> PollResult:
        now = datetime.now(timezone.utc)
        try:
            source = resolve_credentials_source(config_dir=config_dir)
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


def resolve_activity_sessions(config_dir: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Return (sessions_dir, distro_name) for the ActivityWatcher.

    distro_name is None on Linux/macOS (no WSL check needed) and on any
    resolution failure -- resolution failure always means "run without
    activity" (sessions_dir=None too), never a crash. Single default
    account for now (issue #7's scope); a future multi-account watcher
    would resolve one of these per account.

    With an explicit config_dir (from accounts.json): a WSL UNC dir stays
    UNC on win32 (with the distro name parsed out for the running-distro
    check) and is translated to its posix path on Linux; a plain dir is
    used as-is on either platform. Without one: v1 behavior below.
    """
    if config_dir:
        from tokitty.accounts import parse_wsl_unc

        unc = parse_wsl_unc(config_dir)
        if sys.platform == "win32":
            if unc is not None:
                distro = unc[0]
                sessions = config_dir.rstrip("\\/") + "\\tokitty\\sessions"
                return sessions, distro
            return str(Path(config_dir) / "tokitty" / "sessions"), None
        base = unc[1] if unc is not None else config_dir
        # This branch's result is always a Linux/WSL sessions path. Build it with
        # explicit "/" rather than pathlib, which emits host-native separators
        # (backslashes when Tokitty itself runs on Windows) -- mirroring the
        # win32 branch above, which likewise concatenates its separators.
        return base.rstrip("/") + "/tokitty/sessions", None

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
    from tokitty.accounts import load_accounts

    accounts = load_accounts(get_state_dir())
    for account in accounts or [None]:
        if account is not None:
            print(f"— {account.name} ({account.config_dir})")
        config_dir = account.config_dir if account else None

        result = build_fetch_fn(config_dir)()
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

        sessions_dir, distro_name = resolve_activity_sessions(config_dir)
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
        if overdue:
            display["hint_text"] = _STALE_HINTS.get(result.status, "can't confirm, reconnect")
            display["dimmed"] = True
        elif result.status == "stale_token" and binding is None:
            # Resting look: a work account's token expires ~1h after that
            # account's Claude Code last ran, so outside work hours this is
            # the pane's normal steady state -- not an error. Dim the
            # last-good numbers, sleep the cat, timestamp it quietly.
            last_seen = previous.fetched_at.astimezone().strftime("%H:%M")
            display["state"] = "sleeping"
            display["hint_text"] = f"last seen {last_seen}"
            display["dimmed"] = True
        else:
            display["hint_text"] = None
            display["dimmed"] = False
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


def initial_customization(account: Optional[Account], stored: Optional[Customization]) -> Customization:
    """Resolve the Customization to open a pane with. Stored (loaded from
    customization.json) always wins when present; otherwise seed from the
    account's accounts.json `coat` field when it names a valid preset,
    else fall back to the Customization() default (orange_tabby)."""
    if stored is not None:
        return stored
    coat = account.coat if account is not None else None
    if isinstance(coat, str) and coat in sprites.COATS:
        return Customization(coat=coat)
    return Customization()


def initial_label(account: Optional[Account], custom: Customization, dual: bool) -> str:
    """Default label: an explicit stored label always wins; otherwise dual
    mode defaults to the account name (single mode stays blank)."""
    if custom.label:
        return custom.label
    if dual and account is not None:
        return account.name
    return ""


def run_gui() -> int:
    import tkinter as tk

    from tokitty.ui import BG_COLOR, TokittyWindow

    state_dir = get_state_dir()
    lock = SingleInstanceLock(state_dir)
    try:
        lock.acquire()
    except LockAcquisitionError:
        print("Tokitty is already running.", file=sys.stderr)
        return 1

    from tokitty.accounts import env_conflict_warning, load_accounts

    accounts = load_accounts(state_dir)
    warning = env_conflict_warning(accounts)
    if warning:
        print(f"tokitty: {warning}", file=sys.stderr)

    debug_accounts = os.environ.get("TOKITTY_DEBUG_ACCOUNTS")
    pane_count = 2 if debug_accounts == "2" else (len(accounts) if accounts else 1)

    root = tk.Tk()
    window = TokittyWindow(root, state_dir, pane_count=pane_count)

    debug_state = os.environ.get(DEBUG_STATE_ENV)
    if debug_state or debug_accounts == "2":
        fake = dict(
            state=debug_state or "content", session_pct=37.0, weekly_pct=62.0,
            session_reset_text="resets 9pm", weekly_reset_text="resets Fri",
            driving_tag="debug", credits_text=None, hint_text=None, dimmed=False,
        )
        for index, pane in enumerate(window.panes):
            resting = dict(fake, state="sleeping", dimmed=True,
                           hint_text="last seen 17:40")
            pane.render(**(fake if index == 0 else resting))
        root.mainloop()
        lock.release()
        return 0

    customization_store = load_customization(state_dir)
    dual = bool(accounts) and len(accounts) > 1

    def customization_key(account: Optional[Account]) -> str:
        return account.name if (dual and account is not None) else SINGLE_KEY

    def apply_customization(pane, custom: Customization) -> None:
        pane.set_appearance(
            palette=effective_palette(custom),
            card_bg=custom.overrides.get("card_bg", BG_COLOR),
            bar_fill=custom.overrides.get("bar_fill", ""),
            coat=custom.coat,
        )

    units = []
    for index, account in enumerate(accounts or [None]):
        config_dir = account.config_dir if account else None
        poller = Poller(fetch_fn=build_fetch_fn(config_dir))
        sessions_dir, distro_name = resolve_activity_sessions(config_dir)
        watcher = ActivityWatcher(sessions_dir, ActivityTracker(), distro_name=distro_name)

        key = customization_key(account)
        custom = initial_customization(account, customization_store.get(key))
        customization_store[key] = custom
        label = initial_label(account, custom, dual)
        pane = window.panes[index]
        apply_customization(pane, custom)
        pane.set_appearance(label=label)

        units.append({"pane": pane, "poller": poller, "watcher": watcher,
                      "last_good": None, "key": key, "account": account})

    def refresh_all():
        for unit in units:
            unit["poller"].request_refresh()

    window.on_refresh_requested = refresh_all

    def handle_customization_changed(pane_index: int, field: str, value: Optional[str]) -> None:
        unit = units[pane_index]
        key = unit["key"]
        custom = customization_store[key]

        if field == "coat":
            if value in sprites.COATS:
                custom = replace(custom, coat=value)
        elif field == "reset":
            custom = replace(custom, overrides={})
        elif field in ("coat_base", "coat_shade", "card_bg", "bar_fill"):
            if value:
                overrides = dict(custom.overrides)
                overrides[field] = value
                custom = replace(custom, overrides=overrides)
        elif field == "label":
            if value is not None:
                custom = replace(custom, label=value)
        else:
            return

        customization_store[key] = custom
        save_customization(state_dir, customization_store)
        apply_customization(unit["pane"], custom)
        if field == "label":
            label = initial_label(unit["account"], custom, dual)
            unit["pane"].set_appearance(label=label)

    window.on_customization_changed = handle_customization_changed
    if warning:
        window.panes[0].render(state="confused", session_pct=0.0, weekly_pct=0.0,
                               session_reset_text="—", weekly_reset_text="—", driving_tag="",
                               credits_text=None, hint_text=warning, dimmed=True)

    def tick():
        for unit in units:
            latest = unit["poller"].get_latest()
            if latest is None:
                continue
            display = _display_state_for(latest, unit["last_good"])
            activity = unit["watcher"].get_latest()
            pose = resolve_pose(display["state"], activity)
            display["state"] = pose["sprite_state"]
            display["tool_label"] = pose["tool_label"]
            display["accent"] = pose["accent"]
            unit["pane"].render(**display)
            unit["last_good"] = _next_last_good(latest, unit["last_good"])
        root.after(UI_REFRESH_MS, tick)

    for unit in units:
        unit["poller"].start()
        unit["watcher"].start()
    root.after(UI_REFRESH_MS, tick)

    try:
        root.mainloop()
    finally:
        for unit in units:
            unit["poller"].stop()
            unit["watcher"].stop()
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
