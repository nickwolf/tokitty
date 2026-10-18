"""The toolkit-free half of the app: unit construction, the per-unit tick step,
onboarding and background discovery.

The Qt entry point builds its units here and calls step_unit from its own
tick, so what a pane shows is decided in one place. This module imports no GUI toolkit, and never imports from
tokitty.__main__: under `python -m tokitty` the entry module is loaded as
__main__, so importing it by its package name would load a second copy.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from tokitty.accounts import Account
from tokitty.activity import ActivityTracker
from tokitty.activity_watcher import ActivityWatcher
from tokitty.behaviour import drives
from tokitty.burn import PACE_UNKNOWN, BurnTracker, Pace
from tokitty.credentials import CredentialLoader
from tokitty.customize import SINGLE_KEY, Customization, save_customization
from tokitty.display import (
    format_countdown,
    format_observed_at,
    format_pane_label,
    format_projection,
    format_reset_day,
    format_reset_time,
)
from tokitty.hooks_install import retry_pending_hook_op
from tokitty.hover import LimitHover, LimitsHover
from tokitty.mood import compute_capped_substate, compute_mood, detect_activate, select_binding_capped_limit
from tokitty.poller import PollResult, Poller
from tokitty.pose import resolve_pose
from tokitty.providers import (
    DEFAULT_KIND,
    NULL_PROVIDER,
    STATUS_UNSUPPORTED,
    UnknownProviderError,
    get_provider,
)
from tokitty.randomize import random_look
from tokitty.settings import Settings
from tokitty.startup import ONBOARDING_MODELS_AUTOSELECT, should_auto_select_models
from tokitty.streamdock.wiring import usage_from_display
from tokitty.usage_display import build_view
from tokitty.usage_watcher import UsageWatcher
from tokitty import sprites

UI_REFRESH_MS = 500

# Shown only once our own clock says a cached countdown should already
# have hit zero and we still can't confirm it -- see _display_state_for.
_STALE_HINTS = {
    "stale_token": "token expired, reopen Claude Code",
    "credentials_unreachable": "can't confirm, credentials unreachable",
    "ambiguous_credentials": "can't confirm, use Accounts…",
    "api_error": "can't confirm, API hiccup",
    "keychain_denied": "can't confirm, Keychain denied",
    "source_unreachable": "can't confirm, no recent sessions",
    STATUS_UNSUPPORTED: "no limits published",
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
        # Derived from the snapshot rather than the poll, so it reports
        # the age of the numbers on screen whether they came from this
        # poll, a cached one, or a transcript written hours ago.
        "stale_text": format_observed_at(snapshot.fetched_at, now),
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
        "credentials_unreachable": (
            "can't find credentials. Right-click ▸ Settings… ▸ Accounts "
            "to read usage from transcripts."
        ),
        "ambiguous_credentials": "multiple installs. Right-click ▸ Settings… ▸ Accounts to choose.",
        "api_error": "API hiccup, retrying",
        "keychain_denied": _KEYCHAIN_DENIED_HINT,
        "source_unreachable": "no recent sessions found",
        STATUS_UNSUPPORTED: "no limits published",
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
        "stale_text": None,
    }


def _usage_render_args(unit, latest, breakdown, activity, usage_state, now: Optional[float] = None) -> dict:
    """Arguments for Pane.render_usage.

    Deliberately ignores every FAILED poll result: a non-subscriber must
    never see "token expired" or a confused cat in the view built for
    them. A CONFIRMED cap from a fresh successful poll is NOT suppressed,
    though -- hiding it would show a cat happily working against a wall,
    which is the exact confusion the capped/waking vocabulary exists to
    prevent.
    """
    from tokitty.settings import budget_for

    state = "content"
    driving_tag = ""
    if latest is not None and latest.status == "ok" and latest.snapshot is not None:
        binding = select_binding_capped_limit(latest.snapshot.limits)
        if binding is not None:
            capped = compute_capped_substate(binding)
            state = capped.substate
            driving_tag = capped.driving_tag

    pose = resolve_pose(state, activity, now)
    budget = budget_for(
        Settings(usage_budgets=usage_state["budgets"]), unit["key"], usage_state["window"]
    )
    return {
        "state": pose["sprite_state"],
        "usage_view": build_view(breakdown, usage_state["readout"], budget),
        "driving_tag": driving_tag,
        "tool_label": pose["tool_label"],
        "accent": pose["accent"],
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


def _limits_hover_for(snapshot, tracker: BurnTracker, display: dict, now: datetime) -> Optional[LimitsHover]:
    """The raw numbers behind the limits popup, or None when no snapshot was
    ever read. The pace is left out whenever the numbers are dimmed or old,
    for the same reason the status line's projection is."""
    if snapshot is None:
        return None
    binding = select_binding_capped_limit(snapshot.limits)
    trusted = not display.get("dimmed") and not display.get("stale_text")

    def row(title: str, kind: str, pct: float, resets_at) -> LimitHover:
        return LimitHover(
            title=title, kind=kind, pct=pct, resets_at=resets_at,
            capped=binding is not None and (binding.kind == "session") == (kind == "session"),
            pace=tracker.pace(kind, now) if trusted else Pace(PACE_UNKNOWN),
        )

    return LimitsHover(
        session=row("Session", "session", snapshot.session_pct, snapshot.session_resets_at),
        weekly=row("Week", "weekly", snapshot.weekly_pct, snapshot.weekly_resets_at),
    )


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


def initial_label(account: Optional[Account], custom: Customization,
                  provider_kind: Optional[str] = None) -> str:
    """Default label: an explicit stored label always wins; otherwise
    blank. Never falls back to account.name -- since the identity slug
    scheme, account.name is an opaque SHA-256-derived string and must
    never be shown to the user.

    `provider_kind` prefixes the harness when the window holds more than
    one of them (see provider_tag_kind). A window of Claude accounts is
    unchanged: naming the same harness on every pane is noise, and the
    tag has to earn its pixels on a label that is often blank."""
    return format_pane_label(custom.label, provider_kind)


def publish_usage_notes(unit: dict, latest: Optional[PollResult], notes, removed: set) -> None:
    """Write (or, when the feature is off, remove once) the account's usage.json
    for the Claude Code hook. Claude accounts only; never raises."""
    try:
        if unit["provider"].kind != DEFAULT_KIND:
            return
        from tokitty.streamdock.wiring import _resolve, parent_dir
        from tokitty.usage_notes import alerts_for, remove_usage_file, write_usage_file

        tokitty_dir = parent_dir(_resolve(unit.get("sessions_dir")))
        if not tokitty_dir:
            return
        enabled, session_pct, weekly_pct = notes
        if not enabled:
            if tokitty_dir not in removed:
                removed.add(tokitty_dir)
                remove_usage_file(tokitty_dir)
            return
        removed.discard(tokitty_dir)
        if latest is None or latest.snapshot is None:
            return
        snapshot = latest.snapshot
        write_usage_file(tokitty_dir, snapshot.fetched_at or latest.fetched_at,
                         alerts_for(snapshot, session_pct, weekly_pct))
    except Exception as exc:
        print(f"tokitty: usage notes: {exc}", file=sys.stderr)

def publish_ntfy(unit: dict, latest: Optional[PollResult], usage_state: dict, notifier,
                 customization_store: dict) -> None:
    """Hand the account's threshold alerts to the ntfy notifier. Every
    provider; never raises."""
    try:
        enabled, url, topic, token = usage_state["ntfy"]
        if not enabled or latest is None or latest.snapshot is None:
            return
        from tokitty.usage_notes import alerts_for

        _, session_pct, weekly_pct = usage_state["notes"]
        alerts = alerts_for(latest.snapshot, session_pct, weekly_pct)
        custom = customization_store.get(unit["key"])
        label = (custom.label.strip() if custom is not None else "") or unit["provider"].kind.capitalize()
        notifier.check(unit["key"], label, alerts, (url, topic, token))
    except Exception as exc:
        print(f"tokitty: ntfy: {exc}", file=sys.stderr)


def provider_tag_kind(provider, providers) -> Optional[str]:
    """The kind to show on one pane, or None when every pane in the window
    is the same harness and there is nothing to tell apart."""
    if len({each.kind for each in providers}) < 2:
        return None
    return provider.kind


def customization_key(account: Optional[Account]) -> str:
    return account.name if account is not None else SINGLE_KEY


@dataclass(frozen=True)
class PaneUpdate:
    """What one unit's pane should draw this tick: `kind` is "limits" (Pane.render)
    or "models" (Pane.render_usage), `kwargs` are that method's keyword arguments."""

    kind: str
    kwargs: dict


def build_units(*, accounts, state_dir, customization_store: dict, wsl_credentials,
                distro_probe, usage_state: dict) -> List[dict]:
    """One unit per account (one default unit with no accounts), without a pane.

    The caller sets unit["pane"] to its own pane and applies unit["custom"] and
    unit["label"] to it before wiring any callback that reads the pane.
    `customization_store` is updated in place and saved.
    """
    # Providers are resolved in their own pass because the label rule needs
    # to know every harness in the window before the first pane is built.
    resolved_accounts = list(accounts or [None])
    providers = []
    for account in resolved_accounts:
        try:
            providers.append(get_provider(account.provider if account else None))
        except UnknownProviderError as exc:
            # A pane that polls the wrong harness is worse than a pane that
            # says it can't: name the problem on stderr and give this one a
            # provider that reports nothing.
            print(f"tokitty: {exc}", file=sys.stderr)
            providers.append(NULL_PROVIDER)

    units = []
    for index, account in enumerate(resolved_accounts):
        config_dir = account.config_dir if account else None
        provider = providers[index]
        tag_kind = provider_tag_kind(provider, providers)
        cred_loader = CredentialLoader()
        poller = Poller(fetch_fn=provider.build_fetch_fn(config_dir, loader=cred_loader),
                        name=f"poller-{index}")
        sessions_dir, distro_name = provider.resolve_activity_sessions(
            config_dir, credentials=wsl_credentials
        )
        watcher = ActivityWatcher(
            sessions_dir, ActivityTracker(), distro_name=distro_name,
            list_running_distros_fn=distro_probe.get_running, name=f"activity-{index}",
        )
        ledger = provider.resolve_ledger(config_dir, credentials=wsl_credentials)
        usage_watcher = UsageWatcher(
            ledger.root if ledger else None,
            distro_name=ledger.distro_name if ledger else None,
            window=lambda: usage_state["window"],
            list_running_distros_fn=distro_probe.get_running,
            scanner_factory=ledger.make_scanner if ledger else None,
            name=f"usage-{index}",
        )

        key = customization_key(account)
        custom = initial_customization(account, customization_store.get(key))
        customization_store[key] = custom
        label = initial_label(account, custom, tag_kind)

        units.append({"poller": poller, "watcher": watcher,
                      "provider": provider, "tag_kind": tag_kind,
                      "last_good": None, "key": key, "account": account,
                      "cred_loader": cred_loader, "burn": BurnTracker(),
                      "usage": usage_watcher, "custom": custom, "label": label,
                      # Kept for the Stream Dock, which derives the hook's tokitty dir from it.
                      "sessions_dir": sessions_dir, "distro_name": distro_name})

    # Persist first-run seeds (and re-write loaded entries idempotently) so a
    # random seed becomes a STABLE identity instead of re-rolling each launch.
    # Creates customization.json on first run -- intended; it is the per-account
    # look file, never accounts.json, so there is no credential-mode impact.
    save_customization(state_dir, customization_store)
    return units


def make_usage_state(settings: Settings) -> dict:
    """Plain-Python shadow state, never a toolkit object: UsageWatcher reads
    usage_state["window"] from its worker thread, and the menu getters
    (menu.py) read the rest whenever a menu is built."""
    return {
        "view": settings.view_mode,
        "window": settings.usage_window,
        "readout": settings.usage_readout,
        "budgets": settings.usage_budgets,
        "onboarding": settings.onboarding_version,
        "notes": (settings.usage_notes_enabled, settings.usage_note_session_pct,
                  settings.usage_note_weekly_pct),
        # (enabled, url, topic, token) for ntfy alerts.
        "ntfy": (settings.ntfy_enabled, settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token),
    }


def refresh_units(units: List[dict]) -> None:
    """The "Refresh now" action."""
    for unit in units:
        # Clearing first means "Refresh now" is the recovery path after a
        # denied Keychain prompt: grant access, click Refresh, done. No
        # restart needed, which is what makes the sticky block safe.
        unit["cred_loader"].clear_block()
        unit["poller"].request_refresh()
        unit["usage"].request_refresh()


def make_onboarder(state_dir, usage_state: dict, on_changed: Callable[[], None]) -> Callable:
    """The one-shot onboarding step. `on_changed` runs after the switch so the
    caller can refresh whatever shows the view mode (the tray menu)."""
    from tokitty.settings import update_settings

    def maybe_onboard(unit, latest, breakdown) -> None:
        """One-shot: land an API-key user in the view that works for them.

        Runs on the UI thread from the tick, so the settings write and the
        shadow-state update can never interleave with a menu action.
        """
        if usage_state["onboarding"] >= ONBOARDING_MODELS_AUTOSELECT:
            return
        if not should_auto_select_models(
            poll_status=latest.status if latest is not None else None,
            scan_status=breakdown.status if breakdown is not None else None,
            has_records=bool(breakdown.models) if breakdown is not None else False,
            onboarding_version=usage_state["onboarding"],
        ):
            return
        usage_state["onboarding"] = ONBOARDING_MODELS_AUTOSELECT
        usage_state["view"] = "models"
        update_settings(
            state_dir, view_mode="models", onboarding_version=ONBOARDING_MODELS_AUTOSELECT
        )
        on_changed()

    return maybe_onboard


def step_unit(unit: dict, *, usage_state: dict, notifier, customization_store: dict,
              notes_removed: set, onboard: Callable, now: datetime,
              time_of_day: bool = False, tz=None) -> Optional[PaneUpdate]:
    """One tick for one unit: read the workers, run the side effects, update the
    unit's bookkeeping and say what to draw. None means there is no poll result
    yet, so the pane stays as it is. With `time_of_day` the cat's drives follow
    the hour of `now` in `tz` (None is local time)."""
    latest = unit["poller"].get_latest()
    breakdown = unit["usage"].get_latest()
    onboard(unit, latest, breakdown)

    publish_usage_notes(unit, latest, usage_state["notes"], notes_removed)
    publish_ntfy(unit, latest, usage_state, notifier, customization_store)
    if latest is None:
        return None
    display = _display_state_for(latest, unit["last_good"], now)
    if latest.status == "ok" and latest.snapshot is not None:
        unit["burn"].add(latest.snapshot)
    unit["deck_usage"] = usage_from_display(display)
    display["projection_text"] = _projection_text_for(unit["burn"], display, now)
    activity = unit["watcher"].get_latest()

    cat_drives = drives(now.astimezone(tz).hour if time_of_day else None)
    if usage_state["view"] == "models":
        args = _usage_render_args(unit, latest, breakdown, activity, usage_state, now.timestamp())
        update = PaneUpdate("models", {**args, "drives": cat_drives})
    else:
        pose = resolve_pose(display["state"], activity, now.timestamp())
        display["state"] = pose["sprite_state"]
        display["tool_label"] = pose["tool_label"]
        display["accent"] = pose["accent"]
        display["drives"] = cat_drives
        shown = latest.snapshot if latest.status == "ok" and latest.snapshot is not None else (
            unit["last_good"].snapshot if unit["last_good"] is not None else None)
        display["hover"] = _limits_hover_for(shown, unit["burn"], display, now)
        update = PaneUpdate("limits", display)
    unit["last_good"] = _next_last_good(latest, unit["last_good"])
    return update


class Discovery:
    """Background startup work: the pending hook-op retry, the hook reconcile and,
    when it applies, the WSL credential sweep behind the Manage accounts dialog.

    Written by run_discovery() on a background thread, read by the UI thread
    (the tick and the accounts dialog); the lock guards every access from either
    side. run_discovery must never touch the UI toolkit (not even a scheduled
    callback such as a timer): a GUI call from a background thread before the
    event loop has started can be dropped, or touch widgets from the wrong
    thread. The UI thread's own tick is the mechanism already used for exactly
    this producer/consumer shape (Poller/ActivityWatcher results), so the hook
    warnings reuse it through take_hook_warnings().
    """

    def __init__(self, state_dir, *, accounts_state: str, env_override_set: bool,
                 home_relative_exists: bool, wsl_credentials):
        self._state_dir = state_dir
        self._accounts_state = accounts_state
        self._env_override_set = env_override_set
        self._home_relative_exists = home_relative_exists
        self._wsl_credentials = wsl_credentials
        self._lock = threading.Lock()
        self._result = {"wsl_matches": [], "hook_warnings": []}

    def start(self) -> None:
        # A bound method, so the thread's target is named run_discovery.
        threading.Thread(target=self.run_discovery, daemon=True, name="discovery").start()

    def take_hook_warnings(self) -> list:
        """The warnings found so far, each handed out once."""
        with self._lock:
            warnings = self._result["hook_warnings"]
            self._result["hook_warnings"] = []
        return warnings

    def wsl_matches(self) -> list:
        with self._lock:
            return list(self._result["wsl_matches"])

    def run_discovery(self) -> None:
        # Best-effort, silent unless it matters (see hooks_install.py's
        # pending-op journal): retries a hook install/uninstall left
        # incomplete by a prior crash. Runs here, off the UI thread,
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
            # De-duplicated, in order: a link failure's own message, the
            # retry result's warning (and its message if it failed), then
            # each ensure_current result's warning (and message if
            # failed). tick() shows the union in one messagebox, at most
            # once per launch.
            hook_warnings = []
            seen_hook_warnings = set()

            def _note(text):
                if text and text not in seen_hook_warnings:
                    seen_hook_warnings.add(text)
                    hook_warnings.append(text)

            from tokitty import hooks_install

            if getattr(sys, "frozen", False):
                # Repoints <state dir>/current at this release before the
                # retry below, so a stale link from a previous release
                # never lingers between launches (spec Q2a). Reported the
                # same way ensure_current's own per-account reconcile
                # reports the identical failure, so the de-duplication
                # above catches it instead of showing both a raw and a
                # wrapped paragraph for the same lock timeout or failed
                # repoint. AppTranslocatedError is noted here too: with no
                # eligible account in accounts.json, ensure_current below
                # never reports MOVE_TO_APPLICATIONS itself, so this is
                # the only place it surfaces. Doesn't skip the retry below
                # either way.
                try:
                    from tokitty import runner_link
                    from tokitty.frozen import MOVE_TO_APPLICATIONS, AppTranslocatedError

                    runner_link.ensure_runner_link(self._state_dir)
                except AppTranslocatedError:
                    _note(MOVE_TO_APPLICATIONS)
                except FileNotFoundError:
                    _note("Tokitty can't set up its hooks: this copy of Tokitty has no tokitty-hook next to it")
                except OSError as exc:
                    _note(hooks_install.LINK_FALLBACK_WARNING.format(reason=str(exc)))

            # Hold the state_dir hook guard so the Accounts dialog and the
            # Settings Accounts tab see this writer. If something already
            # holds it, that writer owns the journal: skip both calls.
            from tokitty import hook_guard

            guard_token = hook_guard.try_acquire(self._state_dir, "startup")
            refresh_results = []
            try:
                if guard_token is not None:
                    try:
                        retry_result = retry_pending_hook_op(self._state_dir)
                    except (OSError, PermissionError):
                        retry_result = None
                    if retry_result is not None:
                        _note(retry_result.warning)
                        if not retry_result.ok:
                            _note(retry_result.message)

                    try:
                        refresh_results = hooks_install.ensure_current(self._state_dir)
                    except Exception:
                        # Broad on purpose: a reconcile call that raises anything
                        # must not lose the retry warning just collected above,
                        # or skip the WSL/transcript discovery below.
                        refresh_results = []
            finally:
                if guard_token is not None:
                    hook_guard.release(guard_token)
            for refresh_result in refresh_results:
                _note(refresh_result.warning)
                if not refresh_result.ok:
                    _note(refresh_result.message)

            with self._lock:
                self._result["hook_warnings"] = hook_warnings

            # Only feeds the Manage accounts dialog (open_accounts below).
            wsl_matches = []
            if (
                sys.platform == "win32"
                and self._accounts_state == "absent"
                and not self._env_override_set
                and not self._home_relative_exists
            ):
                # Served from the shared cache, so this is the same sweep
                # the unit loop below resolves from rather than a second
                # one -- whichever thread gets here first pays for it once
                # (issue #52).
                wsl_matches = self._wsl_credentials.all_matches()
            with self._lock:
                self._result["wsl_matches"] = wsl_matches
        except Exception:
            # Best-effort, like everything above: no matches just means the
            # dialog opens without discovered paths.
            pass
