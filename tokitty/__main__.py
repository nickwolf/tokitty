"""Entry point: python -m tokitty."""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from tokitty.accounts import Account, load_accounts_result
from tokitty.activity import ActivityTracker
from tokitty.activity_watcher import ActivityWatcher
from tokitty.api import ApiError, fetch_usage, parse_usage_response
from tokitty.burn import BurnTracker
from tokitty.credentials import (
    AmbiguousCredentialsError,
    CredentialLoader,
    CredentialsError,
    KeychainAccessError,
    describe_source,
    is_token_expired,
    load_credentials,
    resolve_credentials_source,
)
from tokitty.customize import (
    Customization,
    SINGLE_KEY,
    effective_palette,
    load_customization,
    save_customization,
    save_customization_entry,
)
from tokitty.display import format_countdown, format_projection, format_reset_day, format_reset_time
from tokitty.distro_probe import RunningDistroProbe
from tokitty.hooks_install import retry_pending_hook_op
from tokitty.lock import LockAcquisitionError, SingleInstanceLock
from tokitty.mood import compute_capped_substate, compute_mood, detect_activate, select_binding_capped_limit
from tokitty.paths import get_state_dir
from tokitty.pose import resolve_pose
from tokitty.poller import PollResult, Poller
from tokitty.randomize import random_look
from tokitty import sprites

# tkinter (and tokitty.ui, which imports it) is deliberately NOT imported
# at module level -- --debug-print must keep working on systems without a
# GUI toolkit installed (e.g. this project's own WSL dev environment).
# run_gui() imports both lazily, only when the GUI path actually runs.

DEBUG_STATE_ENV = "TOKITTY_DEBUG_STATE"
UI_REFRESH_MS = 500


def build_fetch_fn(config_dir: Optional[str] = None, loader: Optional[CredentialLoader] = None):
    # One loader per closure, i.e. per account: it caches that account's
    # Keychain reads and holds its sticky-denial state.
    loader = loader if loader is not None else CredentialLoader()

    def fetch() -> PollResult:
        now = datetime.now(timezone.utc)
        try:
            source = resolve_credentials_source(config_dir=config_dir)
        except AmbiguousCredentialsError as exc:
            return PollResult(status="ambiguous_credentials", snapshot=None, message=str(exc), fetched_at=now)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)

        try:
            creds = loader.load(source, load_fn=load_credentials)
        except KeychainAccessError as exc:
            # Must precede the CredentialsError branch -- it is a subclass.
            return PollResult(status="keychain_denied", snapshot=None, message=str(exc), fetched_at=now)
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
    "ambiguous_credentials": "can't confirm, use Accounts…",
    "api_error": "can't confirm, API hiccup",
    "keychain_denied": "can't confirm, Keychain denied",
}

# Unlike every other status, a Keychain denial cannot self-heal: once
# CredentialLoader._blocked is set, every subsequent poll short-circuits
# without touching the Keychain until "Refresh now" calls clear_block(). So
# wherever this status is shown, the hint must always name that recovery
# action -- never fall back to a "healthy" or silent look.
_KEYCHAIN_DENIED_HINT = "Keychain denied, Refresh to retry"


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
        elif result.status == "keychain_denied":
            # Not self-healing like the statuses that fall through to the
            # `else` below: the loader stays sticky-blocked until "Refresh
            # now" is used, so the cached numbers must stay visibly dimmed
            # with a hint that names the recovery action, not look healthy.
            display["hint_text"] = _KEYCHAIN_DENIED_HINT
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
        "ambiguous_credentials": "multiple installs, use Accounts…",
        "api_error": "API hiccup, retrying",
        "keychain_denied": _KEYCHAIN_DENIED_HINT,
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


def _projection_text_for(tracker: BurnTracker, display: dict, now: datetime) -> Optional[str]:
    """Format the burn projection for a pane, or None to leave the status
    line to credits/hints.

    Gated on `dimmed` -- the app's existing "these numbers are not
    confirmed" signal -- rather than on poll status, so an ordinary
    transient API hiccup does not make the line blink off.
    """
    if display.get("dimmed"):
        return None
    projection = tracker.project(now)
    if projection is None:
        return None
    return format_projection(projection.kind, projection.caps_at)


def _seed_from_account(account: Optional[Account]) -> Tuple[Optional[str], Optional[str]]:
    """Translate a legacy accounts.json `coat` seed to (colorway, pattern)."""
    coat = account.coat if account is not None else None
    if isinstance(coat, str) and coat in sprites.LEGACY_COAT_MAP:
        return sprites.LEGACY_COAT_MAP[coat]
    return None, None


def initial_customization(account: Optional[Account], stored: Optional[Customization],
                          rng=None) -> Customization:
    """Stored (customization.json) always wins; else seed from the account's
    legacy `coat`; else roll a random curated look so a fresh install/account
    gets a unique cat (only-if-unset -- never overrides an explicit pick)."""
    if stored is not None:
        return stored
    colorway, pattern = _seed_from_account(account)
    if colorway is not None:
        return Customization(colorway=colorway, pattern=pattern)
    colorway, pattern = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS), rng=rng)
    return Customization(colorway=colorway, pattern=pattern)


def initial_label(account: Optional[Account], custom: Customization) -> str:
    """Default label: an explicit stored label always wins; otherwise
    blank. Never falls back to account.name -- since the identity slug
    scheme, account.name is an opaque SHA-256-derived string and must
    never be shown to the user."""
    return custom.label


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

    # First-run auto-open + the pending-hook-op retry both belong here, not in
    # TokittyWindow.__init__: they must run only after tk.Tk() has succeeded
    # (a headless launch should fail for lack of a display before ever
    # probing WSL), and the gui-marked tests that construct TokittyWindow
    # directly (never through run_gui) must keep seeing zero WSL calls.
    from tokitty.startup import should_auto_open

    # Written by run_discovery() on a background thread, read by tick() on
    # the Tk thread -- discovery_lock guards every access from either side.
    # maybe_auto_open() itself must only ever be called from the Tk thread
    # (it can construct a Toplevel via AccountsManager.open()), which is why
    # it is invoked from inside tick() rather than from run_discovery
    # directly: calling anything Tk-related (root.after included) from a
    # background thread before root.mainloop() has actually started raises
    # "main thread is not in main loop" *and the call is silently dropped
    # forever*, not merely delayed -- confirmed by direct reproduction.
    # run_discovery starts (just below) before the synchronous unit-building
    # loop below even begins, so it can easily finish before mainloop() is
    # reached. tick()'s existing root.after(UI_REFRESH_MS, tick) polling
    # loop is the Tk-thread-owned mechanism this file already uses for
    # exactly this producer/consumer shape (Poller/ActivityWatcher results),
    # so first-run auto-open reuses it instead of introducing a new one.
    discovery_lock = threading.Lock()
    discovery_result = {"wsl_matches": [], "done": False, "consumed": False}
    discovery_accounts_state = load_accounts_result(state_dir).state
    env_override_set = bool(os.environ.get("TOKITTY_CREDENTIALS"))
    home_relative_exists = (
        Path.home() / ".claude" / ".credentials.json"
    ).is_file()

    def maybe_auto_open() -> None:
        accounts_result = load_accounts_result(state_dir)
        keychain_available = False
        if sys.platform == "darwin":
            from tokitty.keychain import KEYCHAIN_SERVICE, keychain_item_exists

            keychain_available = keychain_item_exists(KEYCHAIN_SERVICE)
        with discovery_lock:
            wsl_matches = list(discovery_result["wsl_matches"])
            wsl_match_count = len(wsl_matches)
        if should_auto_open(
            accounts_state=accounts_result.state,
            env_override_set=env_override_set,
            home_relative_exists=home_relative_exists,
            keychain_available=keychain_available,
            platform=sys.platform,
            wsl_match_count=wsl_match_count,
        ):
            from tokitty.accounts_ui import AccountsManager

            AccountsManager.open(root, state_dir, discovered_matches=wsl_matches)

    def run_discovery() -> None:
        # Best-effort, silent unless it matters (see hooks_install.py's
        # pending-op journal): retries a hook install/uninstall left
        # incomplete by a prior crash. Runs here, off the Tk thread,
        # alongside WSL discovery -- not in the TOKITTY_DEBUG_ACCOUNTS
        # branch, which bypasses normal account resolution entirely.
        #
        # Every step below is wrapped so a failure here can never crash
        # this thread silently before "done" is set: an OSError/
        # PermissionError from retry_pending_hook_op (the underlying hook
        # install/uninstall functions don't convert filesystem exceptions
        # to a result object -- see the design spec's Write ordering and
        # crash consistency section) and a CredentialsError from the WSL
        # scan (e.g. wsl.exe missing from PATH entirely) both mean "nothing
        # to report here", mirroring resolve_activity_sessions's existing
        # philosophy of "resolution failure means run without it, never a
        # crash" -- never "auto-open silently never evaluates again."
        try:
            try:
                retry_pending_hook_op(state_dir)
            except (OSError, PermissionError):
                pass

            wsl_matches = []
            if (
                sys.platform == "win32"
                and discovery_accounts_state == "absent"
                and not env_override_set
                and not home_relative_exists
            ):
                from tokitty.wsl_probe import find_all_wsl_credentials

                try:
                    wsl_matches = find_all_wsl_credentials()
                except CredentialsError:
                    wsl_matches = []

            with discovery_lock:
                discovery_result["wsl_matches"] = wsl_matches
        finally:
            # Unconditional: tick() below is waiting on this flag to decide
            # when to call maybe_auto_open(), exactly once. If an
            # unanticipated exception ever slipped past the narrower
            # excepts above, leaving this unset would silently drop
            # auto-open for the whole launch -- worse than never trying.
            with discovery_lock:
                discovery_result["done"] = True

    if not (debug_state or debug_accounts == "2"):
        threading.Thread(target=run_discovery, daemon=True).start()

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

    from tokitty.migration import (
        CUSTOMIZATION_MIGRATION_KEY,
        LEGACY_ACCOUNT_LABELS_MIGRATION_KEY,
        mark_customization_migration_complete,
        migrate_default_customization,
        migrate_legacy_account_labels,
    )

    customization_store = migrate_default_customization(state_dir, accounts, customization_store)
    customization_store = migrate_legacy_account_labels(state_dir, accounts, customization_store)
    # The transformed data must be durable before either migration marker.
    # If the process stops after this save but before a marker, both
    # transforms are safe to retry on the next launch.
    save_customization(state_dir, customization_store)
    mark_customization_migration_complete(state_dir, CUSTOMIZATION_MIGRATION_KEY)
    mark_customization_migration_complete(state_dir, LEGACY_ACCOUNT_LABELS_MIGRATION_KEY)

    def customization_key(account: Optional[Account]) -> str:
        return account.name if account is not None else SINGLE_KEY

    def apply_customization(pane, custom: Customization) -> None:
        pane.set_appearance(
            palette=effective_palette(custom),
            card_bg=custom.overrides.get("card_bg", BG_COLOR),
            bar_fill=custom.overrides.get("bar_fill", ""),
            colorway=custom.colorway, pattern=custom.pattern,
        )

    distro_probe = RunningDistroProbe()

    units = []
    for index, account in enumerate(accounts or [None]):
        config_dir = account.config_dir if account else None
        cred_loader = CredentialLoader()
        poller = Poller(fetch_fn=build_fetch_fn(config_dir, loader=cred_loader))
        sessions_dir, distro_name = resolve_activity_sessions(config_dir)
        watcher = ActivityWatcher(
            sessions_dir, ActivityTracker(), distro_name=distro_name,
            list_running_distros_fn=distro_probe.get_running,
        )

        key = customization_key(account)
        custom = initial_customization(account, customization_store.get(key))
        customization_store[key] = custom
        label = initial_label(account, custom)
        pane = window.panes[index]
        apply_customization(pane, custom)
        pane.set_appearance(label=label)

        units.append({"pane": pane, "poller": poller, "watcher": watcher,
                      "last_good": None, "key": key, "account": account,
                      "cred_loader": cred_loader, "burn": BurnTracker()})

    # Persist first-run seeds (and re-write loaded entries idempotently) so a
    # random seed becomes a STABLE identity instead of re-rolling each launch.
    # Creates customization.json on first run -- intended; it is the per-account
    # look file, never accounts.json, so there is no credential-mode impact.
    save_customization(state_dir, customization_store)

    def refresh_all():
        for unit in units:
            # Clearing first means "Refresh now" is the recovery path after a
            # denied Keychain prompt: grant access, click Refresh, done. No
            # restart needed, which is what makes the sticky block safe.
            unit["cred_loader"].clear_block()
            unit["poller"].request_refresh()

    window.on_refresh_requested = refresh_all

    def handle_customization_changed(pane_index: int, field: str, value: Optional[str]) -> None:
        unit = units[pane_index]
        key = unit["key"]
        # Start from this identity's latest persisted value, not the
        # process-lifetime startup snapshot.  Accounts... may have changed
        # its label (or added other identities) since run_gui initialized.
        custom = load_customization(state_dir).get(key, customization_store[key])

        if field == "colorway":
            if value in sprites.COLORWAYS:
                custom = replace(custom, colorway=value)
        elif field == "pattern":
            if value in sprites.PATTERNS:
                custom = replace(custom, pattern=value)
        elif field == "randomize":
            cw, pat = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))
            custom = replace(custom, colorway=cw, pattern=pat)
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
        save_customization_entry(state_dir, key, custom)
        apply_customization(unit["pane"], custom)
        if field == "label":
            label = initial_label(unit["account"], custom)
            unit["pane"].set_appearance(label=label)

    window.on_customization_changed = handle_customization_changed
    from tokitty.settings import load_settings
    from tokitty.tray import TrayManager

    settings = load_settings(state_dir)

    from tokitty.settings import Settings, save_settings

    surprise_state = {"on": settings.surprise_me}
    window.surprise_me = lambda: surprise_state["on"]

    def randomize(pane_index: int) -> None:
        handle_customization_changed(pane_index, "randomize", None)

    window.on_randomize = randomize

    def toggle_surprise() -> None:
        surprise_state["on"] = not surprise_state["on"]
        save_settings(state_dir, Settings(tray_enabled=settings.tray_enabled,
                                          surprise_me=surprise_state["on"]))
        if surprise_state["on"]:
            for i in range(len(units)):
                handle_customization_changed(i, "randomize", None)

    window.on_toggle_surprise = toggle_surprise

    from tokitty.accounts_ui import AccountsManager

    def open_accounts() -> None:
        with discovery_lock:
            matches = list(discovery_result["wsl_matches"])
        AccountsManager.open(root, state_dir, discovered_matches=matches)

    window.on_open_accounts = open_accounts

    if settings.surprise_me:
        for index in range(len(units)):
            handle_customization_changed(index, "randomize", None)

    pane0 = window.panes[0]
    tray = TrayManager(root, lambda: window.build_menu_model(0), state_dir,
                       colorway=pane0._colorway, pattern=pane0._pattern)

    window.on_quit = lambda: (tray.stop(), root.destroy())
    if tray.available:
        tray_state = {"enabled": settings.tray_enabled}
        window.tray_enabled = lambda: tray_state["enabled"]

        def toggle_tray():
            tray_state["enabled"] = not tray_state["enabled"]
            tray.set_enabled(tray_state["enabled"])

        window.on_toggle_tray = toggle_tray

    from tokitty.autostart import ensure_current, get_backend, resolve_launch_command, write_launcher_file

    autostart_backend = get_backend()
    if autostart_backend is not None:
        try:
            ensure_current(state_dir, autostart_backend)
            autostart_registered = autostart_backend.is_registered()
        except (OSError, ImportError):
            # get_backend() decides purely from sys.platform, so a test (or
            # a genuinely odd install) that reports "win32" without the
            # winreg module actually being importable reaches here rather
            # than at get_backend() itself -- see the sys.platform-spoofing
            # tests in test_main.py (test_run_discovery_survives_wsl_scan_
            # raising_credentials_error and neighbors) that force sys.
            # platform to "win32" on non-Windows CI to exercise unrelated
            # WSL logic. Degrade exactly like get_backend() returning None:
            # leave the seam at its None default instead of crashing
            # startup, mirroring ensure_current's own "never crash startup"
            # OSError guard.
            autostart_registered = None
        if autostart_registered is not None:
            autostart_state = {"enabled": autostart_registered}
            window.autostart_enabled = lambda: autostart_state["enabled"]

            def toggle_autostart() -> None:
                if autostart_state["enabled"]:
                    autostart_backend.deregister()
                else:
                    write_launcher_file(state_dir)
                    autostart_backend.register(resolve_launch_command(state_dir))
                autostart_state["enabled"] = autostart_backend.is_registered()

            window.on_toggle_autostart = toggle_autostart
    if warning:
        window.panes[0].render(state="confused", session_pct=0.0, weekly_pct=0.0,
                               session_reset_text="—", weekly_reset_text="—", driving_tag="",
                               credits_text=None, hint_text=warning, dimmed=True)

    def tick():
        # Consume run_discovery's result here, on the Tk thread, exactly
        # once -- see the discovery_lock comment above for why this can't
        # be done from run_discovery itself via root.after().
        with discovery_lock:
            ready = discovery_result["done"] and not discovery_result["consumed"]
            if ready:
                discovery_result["consumed"] = True
        if ready:
            maybe_auto_open()

        for unit in units:
            latest = unit["poller"].get_latest()
            if latest is None:
                continue
            display = _display_state_for(latest, unit["last_good"])
            if latest.status == "ok" and latest.snapshot is not None:
                unit["burn"].add(latest.snapshot)
            display["projection_text"] = _projection_text_for(
                unit["burn"], display, datetime.now(timezone.utc)
            )
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
    if tray.available and settings.tray_enabled:
        tray.start()
    root.after(UI_REFRESH_MS, tick)

    try:
        root.mainloop()
    finally:
        tray.stop()
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
