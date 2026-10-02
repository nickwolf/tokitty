import random
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokitty.__main__ import (
    _display_state_for,
    _next_last_good,
    _projection_text_for,
    build_fetch_fn,
    initial_customization,
    initial_label,
    resolve_activity_sessions,
    resolve_projects_dir,
)
from tokitty.accounts import Account
from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.burn import BurnTracker
from tokitty.credentials import CredentialsError
from tokitty.customize import Customization
from tokitty.poller import PollResult
from tokitty.sprites import COLORWAYS, PATTERNS

# Captured at collection time, before _no_real_hook_refresh_at_startup (below)
# patches hooks_install.ensure_current for every test in this file. The one
# test that wants the real function (test_startup_hook_warnings_reach_
# messagebox_via_real_ensure_current) restores this reference over that
# per-test stub; a lookup of hooks_install.ensure_current done inside a test
# body would see the already-patched stub instead, since fixtures run before
# the test body.
from tokitty.hooks_install import ensure_current as _real_ensure_current

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_hook_refresh_at_startup(monkeypatch):
    """run_discovery calls hooks_install.ensure_current(state_dir) on every
    launch. ensure_current reads <state_dir>/accounts.json itself (it
    never falls back to get_config_dirs's default-dir resolution), but a
    state_dir with no accounts.json is exactly what nearly every run_gui
    test in this file uses, and a stray real
    accounts.json on the machine running the suite (or a future change
    that reintroduces a default-dir fallback here) would make this read
    (and, if anything ever drifted, try to rewrite) whoever's real
    ~/.claude/settings.json runs it: the same class of bug
    _no_real_autostart_registration in conftest.py already guards against
    for autostart. Scoped to this file rather than conftest.py, and
    patching ensure_current itself rather than _default_config_dir/
    get_config_dirs, so it never interferes with test_hooks_install.py's
    own dedicated tests for those functions. A test that cares about
    ensure_current's output patches it back itself (see
    test_startup_hook_warnings_show_once_via_messagebox below)."""
    from tokitty import hooks_install

    monkeypatch.setattr(hooks_install, "ensure_current", lambda state_dir, refresh_fn=None: [])


def _limit(kind="session", percent=100.0, severity="normal", is_active=True, resets_at=None):
    return LimitInfo(kind=kind, percent=percent, severity=severity, resets_at=resets_at, is_active=is_active)


def _snapshot(session_pct=40.0, weekly_pct=20.0, limits=None, session_resets_at=None, weekly_resets_at=None):
    return UsageSnapshot(
        session_pct=session_pct,
        session_resets_at=session_resets_at or (NOW + timedelta(hours=3)),
        weekly_pct=weekly_pct,
        weekly_resets_at=weekly_resets_at or (NOW + timedelta(days=3)),
        limits=limits or [],
    )


def _ok(snapshot):
    return PollResult(status="ok", snapshot=snapshot, message=None, fetched_at=NOW)


def _error(status="stale_token"):
    return PollResult(status=status, snapshot=None, message="access token expired", fetched_at=NOW)


def test_ok_result_with_no_previous_uses_live_data():
    display = _display_state_for(_ok(_snapshot(session_pct=56.0, weekly_pct=50.0)), previous=None, now=NOW)

    assert display["session_pct"] == 56.0
    assert display["weekly_pct"] == 50.0
    assert display["hint_text"] is None
    assert display["dimmed"] is False


def test_ok_result_still_detects_activate_against_last_good_snapshot():
    capped_limit = _limit(kind="session", resets_at=NOW + timedelta(minutes=5))
    capped_snapshot = _snapshot(session_pct=100.0, limits=[capped_limit])
    cleared_snapshot = _snapshot(session_pct=0.0, limits=[])

    previous = _ok(capped_snapshot)
    display = _display_state_for(_ok(cleared_snapshot), previous=previous, now=NOW)

    assert display["state"] == "activate"


def test_non_ok_with_no_good_snapshot_shows_blocking_fallback():
    display = _display_state_for(_error("stale_token"), previous=None, now=NOW)

    assert display["state"] == "confused"
    assert display["session_reset_text"] == "—"
    assert display["dimmed"] is True
    assert display["hint_text"]


def test_no_good_snapshot_credentials_unreachable_matches_generic_fallback():
    """Boot-race requirement: a failed FIRST poll (no previous ok result
    yet -- e.g. WSL not answering yet at login) must render through the
    exact same generic non-ok/no-cache path as any other transient
    failure. No special-cased "still booting" state that could get
    stuck exists anywhere in _display_state_for."""
    display = _display_state_for(_error("credentials_unreachable"), previous=None, now=NOW)
    assert display["state"] == "confused"
    assert display["dimmed"] is True
    assert display["hint_text"] == "can't find credentials"


def test_boot_race_recovers_without_manual_refresh(monkeypatch):
    """End-to-end: resolve_credentials_source fails on the first two
    calls (the same CredentialsError build_fetch_fn already maps to
    credentials_unreachable) and succeeds on the third. A real Poller,
    with its real backoff loop, must reach "ok" on its own -- the whole
    point of autostart making a first-poll failure routine instead of
    rare -- with request_refresh() never called."""
    from tokitty.credentials import CredentialsError, LocalCredentialsSource
    from tokitty.poller import Poller

    attempts = {"n": 0}

    def flaky_resolve(config_dir=None):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise CredentialsError("WSL not answering yet")
        return LocalCredentialsSource(path=Path("/does/not/matter"))

    monkeypatch.setattr("tokitty.providers.claude.resolve_credentials_source", flaky_resolve)
    monkeypatch.setattr(
        "tokitty.providers.claude.load_credentials", lambda src: {"expiresAt": 4102444800000, "accessToken": "tok"}
    )
    monkeypatch.setattr("tokitty.providers.claude.fetch_usage", lambda token: {"raw": "doesn't matter, parse is stubbed"})
    monkeypatch.setattr("tokitty.providers.claude.parse_usage_response", lambda raw: _snapshot())

    fetch_fn = build_fetch_fn()
    done = threading.Event()
    # sleep_fn never actually blocks (see tests/test_poller.py for the same
    # pattern), so once flaky_resolve starts succeeding the background
    # thread keeps looping past recovery. Reading attempts["n"] after
    # poller.stop() would race against those extra iterations, so capture
    # the count at the moment recovery happens instead -- done.wait()
    # returning True guarantees this write already happened, since it runs
    # before done.set() on the same (background) thread.
    recovered_at = {}

    def wrapped_fetch():
        result = fetch_fn()
        if result.status == "ok" and "n" not in recovered_at:
            recovered_at["n"] = attempts["n"]
            done.set()
        return result

    poller = Poller(fetch_fn=wrapped_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        assert done.wait(timeout=3), "poller never reached ok on its own"
        assert poller.get_latest().status == "ok"
    finally:
        poller.stop()
    assert recovered_at["n"] == 3  # 2 simulated failures + the recovering success


def test_non_ok_with_cached_uncapped_snapshot_shows_resting_look():
    previous = _ok(_snapshot(session_pct=56.0, weekly_pct=50.0))

    display = _display_state_for(_error("stale_token"), previous=previous, now=NOW)

    assert display["session_pct"] == 56.0
    assert display["weekly_pct"] == 50.0
    assert display["state"] == "sleeping"
    assert display["dimmed"] is True
    assert "last seen" in display["hint_text"]


def test_non_ok_with_cached_capped_snapshot_keeps_counting_down_silently():
    capped_limit = _limit(kind="session", resets_at=NOW + timedelta(minutes=30))
    previous = _ok(_snapshot(session_pct=100.0, limits=[capped_limit]))

    later = NOW + timedelta(minutes=10)  # token went stale mid-countdown
    display = _display_state_for(_error("stale_token"), previous=previous, now=later)

    assert display["hint_text"] is None
    assert display["dimmed"] is False
    assert "20m" in display["session_reset_text"]  # still ticks down using the live clock


def test_non_ok_with_cached_capped_snapshot_overdue_shows_small_warning():
    capped_limit = _limit(kind="session", resets_at=NOW + timedelta(minutes=5))
    previous = _ok(_snapshot(session_pct=100.0, limits=[capped_limit]))

    later = NOW + timedelta(minutes=20)  # well past the cached reset time
    display = _display_state_for(_error("stale_token"), previous=previous, now=later)

    assert display["hint_text"] is not None
    assert display["dimmed"] is True
    assert display["session_pct"] == 100.0  # still shows cached data, not blanked to "-"


def test_next_last_good_keeps_previous_on_error():
    good = _ok(_snapshot())
    bad = _error("stale_token")

    assert _next_last_good(bad, good) is good


def test_next_last_good_replaces_on_new_success():
    good = _ok(_snapshot())
    newer = _ok(_snapshot(session_pct=5.0))

    assert _next_last_good(newer, good) is newer


def test_next_last_good_stays_none_until_first_success():
    bad = _error("stale_token")

    assert _next_last_good(bad, None) is None


def test_stale_token_with_cache_shows_resting_look():
    # Use the file's existing helper for an ok PollResult
    good = _ok(_snapshot(session_pct=42.0))
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime(2026, 7, 18, 20, 0, tzinfo=timezone.utc))
    display = _display_state_for(stale, previous=good)
    assert display["state"] == "sleeping"
    assert display["dimmed"] is True
    assert display["hint_text"].startswith("last seen ")
    assert display["session_pct"] == 42.0  # last-good numbers still shown


def test_stale_token_resting_uses_last_good_fetch_time():
    good = _ok(_snapshot())
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime.now(timezone.utc))
    display = _display_state_for(stale, previous=good)
    expected = good.fetched_at.astimezone().strftime("%H:%M")
    assert display["hint_text"] == f"last seen {expected}"


def test_stale_token_without_cache_keeps_v1_hint():
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime.now(timezone.utc))
    display = _display_state_for(stale, previous=None)
    assert display["state"] == "confused"
    assert display["hint_text"] == "token stale, open Claude Code"


def test_overdue_capped_beats_resting():
    # last-good has an active capped limit whose resets_at is already past:
    # the "can't confirm" warning must win over the resting look.
    capped_limit = _limit(kind="session", resets_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    capped_snapshot = _snapshot(session_pct=100.0, limits=[capped_limit])
    good = _ok(capped_snapshot)
    stale = PollResult(status="stale_token", snapshot=None, message="expired",
                       fetched_at=datetime.now(timezone.utc))
    display = _display_state_for(stale, previous=good)
    assert display["hint_text"] == "token expired, reopen Claude Code"
    assert display["dimmed"] is True


def test_resolve_activity_sessions_explicit_posix_dir(monkeypatch):
    monkeypatch.setattr("tokitty.__main__.sys.platform", "linux")
    sessions_dir, distro = resolve_activity_sessions("/home/u/.claude-work")
    assert sessions_dir == "/home/u/.claude-work/tokitty/sessions"
    assert distro is None


def test_resolve_activity_sessions_explicit_unc_dir(monkeypatch):
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    sessions_dir, distro = resolve_activity_sessions(
        "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work")
    assert sessions_dir == "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work\\tokitty\\sessions"
    assert distro == "Ubuntu"


def test_resolve_activity_sessions_unc_dir_on_linux_translates(monkeypatch):
    monkeypatch.setattr("tokitty.__main__.sys.platform", "linux")
    sessions_dir, distro = resolve_activity_sessions(
        "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work")
    assert sessions_dir == "/home/u/.claude-work/tokitty/sessions"
    assert distro is None


def test_build_fetch_fn_passes_config_dir(monkeypatch, tmp_path):
    seen = {}

    def fake_resolve(config_dir=None):
        seen["config_dir"] = config_dir
        raise CredentialsError("stop here")

    monkeypatch.setattr("tokitty.providers.claude.resolve_credentials_source", fake_resolve)
    result = build_fetch_fn(config_dir="/home/u/.claude-work")()
    assert seen["config_dir"] == "/home/u/.claude-work"
    assert result.status == "credentials_unreachable"


def test_initial_customization_seeds_from_account_coat():
    account = Account(name="Work", config_dir="/x", coat="black")
    result = initial_customization(account, None)
    assert (result.colorway, result.pattern) == ("black", "tabby")


def test_initial_customization_stored_beats_seed():
    account = Account(name="Work", config_dir="/x", coat="black")
    stored = Customization(colorway="white", pattern="calico", label="Work Cat")
    assert initial_customization(account, stored) == stored


def test_initial_customization_no_stored_no_seed_rolls_random():
    account = Account(name="Work", config_dir="/x")
    result = initial_customization(account, None, rng=random.Random(0))
    assert result.colorway in COLORWAYS and result.pattern in PATTERNS


def test_initial_customization_invalid_seed_coat_rolls_random():
    account = Account(name="Work", config_dir="/x", coat="not_a_real_coat")
    result = initial_customization(account, None, rng=random.Random(0))
    assert result.colorway in COLORWAYS and result.pattern in PATTERNS


def test_initial_customization_no_account_no_stored_rolls_random():
    result = initial_customization(None, None, rng=random.Random(0))
    assert result.colorway in COLORWAYS and result.pattern in PATTERNS


def test_initial_label_defaults_empty():
    account = Account(name="Work", config_dir="/x")
    custom = Customization()
    assert initial_label(account, custom) == ""


def test_initial_label_never_falls_back_to_account_name():
    # Since the identity slug scheme, account.name is an opaque
    # SHA-256-derived string and must never be shown to the user.
    account = Account(name="acct-v1-deadbeef", config_dir="/x")
    custom = Customization()
    assert initial_label(account, custom) == ""


def test_initial_label_explicit_stored_label_wins():
    account = Account(name="Work", config_dir="/x")
    custom = Customization(label="Fluffy")
    assert initial_label(account, custom) == "Fluffy"


def test_initial_label_explicit_stored_label_wins_no_account():
    custom = Customization(label="Fluffy")
    assert initial_label(None, custom) == "Fluffy"


def test_initial_label_no_account_defaults_empty():
    custom = Customization()
    assert initial_label(None, custom) == ""


def test_label_field_roundtrips_through_dataclasses_replace():
    # Mirrors handle_customization_changed's "label" branch: a rename
    # dialog result is stored via dataclasses.replace(custom, label=value).
    custom = Customization(colorway="white", pattern="calico", overrides={"card_bg": "#112233"})
    renamed = replace(custom, label="Whiskers")
    assert renamed.label == "Whiskers"
    assert renamed.colorway == "white"
    assert renamed.pattern == "calico"
    assert renamed.overrides == {"card_bg": "#112233"}


def test_label_field_can_be_cleared_back_to_empty():
    custom = Customization(label="Whiskers")
    cleared = replace(custom, label="")
    assert cleared.label == ""
    # Clearing the stored label returns to blank -- initial_label never
    # falls back to account.name, regardless of account.
    account = Account(name="Work", config_dir="/x")
    assert initial_label(account, cleared) == ""
    assert initial_label(None, cleared) == ""


def test_build_fetch_fn_reports_keychain_denied(monkeypatch):
    from tokitty.credentials import KeychainAccessError, KeychainCredentialsSource

    source = KeychainCredentialsSource(service="Claude Code-credentials")
    monkeypatch.setattr("tokitty.providers.claude.resolve_credentials_source", lambda config_dir=None: source)
    monkeypatch.setattr(
        "tokitty.providers.claude.load_credentials",
        lambda src: (_ for _ in ()).throw(KeychainAccessError("denied")),
    )

    result = build_fetch_fn()()

    # Not credentials_unreachable: the credentials were found, access was
    # refused. Its hint ("can't find credentials") would be the wrong remedy.
    assert result.status == "keychain_denied"


def test_build_fetch_fn_uses_the_injected_loader_and_caches_across_calls(monkeypatch):
    from tokitty.api import ApiError
    from tokitty.credentials import CredentialLoader, KeychainCredentialsSource

    source = KeychainCredentialsSource(service="Claude Code-credentials")
    monkeypatch.setattr("tokitty.providers.claude.resolve_credentials_source", lambda config_dir=None: source)

    load_calls = []

    def counting_load(src):
        load_calls.append(src)
        # Far-future expiresAt -> not expired, so the second fetch() call is
        # a cache hit rather than a fresh Keychain read.
        return {"expiresAt": 4102444800000, "accessToken": "tok"}

    monkeypatch.setattr("tokitty.providers.claude.load_credentials", counting_load)
    # Stops the poll right after the token check, so no real network call is
    # made -- getting past that check is all this test needs from fetch_usage.
    monkeypatch.setattr(
        "tokitty.providers.claude.fetch_usage",
        lambda token: (_ for _ in ()).throw(ApiError("boom", status_code=500)),
    )

    loader = CredentialLoader()
    fetch = build_fetch_fn(loader=loader)

    first = fetch()
    second = fetch()

    # Both calls reach the API-call stage, proving neither was short-circuited
    # by an unrelated early return.
    assert first.status == "api_error"
    assert second.status == "api_error"
    # This is what the test name promises: the loader passed into
    # build_fetch_fn is the one actually used inside fetch(), and it caches
    # -- a second call must not re-read the Keychain. If `loader = ...`
    # ever moved inside fetch() (silently disabling caching and reintroducing
    # a macOS prompt on every poll), this would catch it by failing here.
    assert len(load_calls) == 1


def test_keychain_denied_has_hint_text_in_both_dicts():
    from tokitty.__main__ import _STALE_HINTS

    assert "keychain_denied" in _STALE_HINTS
    # The user-facing hint must name the recovery action, since PollResult.message
    # is never rendered anywhere in the UI.
    display = _display_state_for(_error("keychain_denied"), previous=None, now=NOW)
    assert "Refresh" in display["hint_text"]


def test_keychain_denied_falls_back_to_cached_countdown(monkeypatch):
    # A denied Keychain is a transient fetch failure like a stale token: the
    # cached countdown should keep showing rather than blanking out.
    good = _ok(_snapshot(session_pct=42.0, weekly_pct=20.0))
    display = _display_state_for(_error("keychain_denied"), previous=good, now=NOW)

    assert display["session_pct"] == 42.0
    # Unlike a stale token (which self-heals and can rest as "healthy"),
    # keychain_denied is sticky until "Refresh now" is used -- so the cached
    # numbers must stay dimmed with a hint naming that recovery action,
    # never render as a normal, healthy-looking card.
    assert display["hint_text"] == "Keychain denied, Refresh to retry"
    assert display["dimmed"] is True


def test_ambiguous_credentials_hint_without_cache_points_at_accounts_not_env_var():
    # Cold start (no previous successful poll) reads the local `hints` dict
    # inside _display_state_for.
    display = _display_state_for(_error("ambiguous_credentials"), previous=None, now=NOW)
    assert "TOKITTY_CREDENTIALS" not in display["hint_text"]
    assert "Accounts" in display["hint_text"]


def test_ambiguous_credentials_hint_overdue_cache_points_at_accounts_not_env_var():
    # A cached countdown that's gone overdue reads _STALE_HINTS instead --
    # same status, different dict, so both need repointing away from the
    # now-removed env var advice.
    capped_limit = _limit(kind="session", resets_at=NOW + timedelta(minutes=5))
    previous = _ok(_snapshot(session_pct=100.0, limits=[capped_limit]))

    later = NOW + timedelta(minutes=20)  # well past the cached reset time
    display = _display_state_for(_error("ambiguous_credentials"), previous=previous, now=later)

    assert "TOKITTY_CREDENTIALS" not in display["hint_text"]
    assert "Accounts" in display["hint_text"]


def _usage(offset_seconds=0, session_pct=10.0, weekly_pct=5.0):
    return UsageSnapshot(
        session_pct=session_pct,
        session_resets_at=NOW + timedelta(hours=3),
        weekly_pct=weekly_pct,
        weekly_resets_at=NOW + timedelta(days=4),
        limits=[],
        fetched_at=NOW + timedelta(seconds=offset_seconds),
    )


def _burning_tracker():
    tracker = BurnTracker()
    tracker.add(_usage(offset_seconds=0, session_pct=10.0))
    tracker.add(_usage(offset_seconds=600, session_pct=40.0))
    return tracker


def test_projection_text_for_formats_a_live_projection():
    text = _projection_text_for(_burning_tracker(), {"dimmed": False},
                                NOW + timedelta(seconds=600))
    assert text is not None
    assert text.startswith("session caps ~")


def test_projection_text_for_is_none_when_the_display_is_dimmed():
    """Dimmed means we cannot confirm the numbers -- do not layer a
    confident prediction on top of them."""
    text = _projection_text_for(_burning_tracker(), {"dimmed": True},
                                NOW + timedelta(seconds=600))
    assert text is None


def test_projection_text_for_is_none_during_warm_up():
    tracker = BurnTracker()
    tracker.add(_usage(offset_seconds=0, session_pct=10.0))
    text = _projection_text_for(tracker, {"dimmed": False}, NOW)
    assert text is None


def test_projection_text_for_tolerates_a_display_without_a_dimmed_key():
    text = _projection_text_for(_burning_tracker(), {}, NOW + timedelta(seconds=600))
    assert text is not None


def _capture_spawned_threads(monkeypatch):
    """Subclass threading.Thread for the duration of the test so every
    background thread run_gui spawns (discovery, pollers, watchers) is
    recorded without changing its real behavior -- same technique as
    test_accounts_ui.py's _run_and_wait_for_mutation.

    Identifies each thread by its target's __name__, captured at
    construction time rather than read back afterward: CPython's
    Thread.run() does `del self._target, self._args, self._kwargs` in a
    finally clause once the target returns, so a completed thread's
    _target is already gone by the time a test gets around to inspecting
    it -- capturing the name up front avoids that race entirely."""
    real_thread_cls = threading.Thread
    spawned = []

    class _RecordingThread(real_thread_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            target = k.get("target") if "target" in k else (a[0] if a else None)
            self.recorded_target_name = getattr(target, "__name__", None)
            spawned.append(self)

    monkeypatch.setattr(threading, "Thread", _RecordingThread)
    return spawned


@pytest.mark.gui
def test_run_gui_retries_pending_hook_op_at_startup(tmp_path, monkeypatch):
    """retry_pending_hook_op (hooks_install.py) must fire once from the
    startup sequence, off the Tk thread alongside WSL discovery
    (run_discovery)."""
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)

    calls = []
    monkeypatch.setattr(main_module, "retry_pending_hook_op", lambda state_dir: calls.append(state_dir))
    spawned = _capture_spawned_threads(monkeypatch)

    result = main_module.run_gui()
    assert result == 0

    discovery_threads = [t for t in spawned if t.recorded_target_name == "run_discovery"]
    assert len(discovery_threads) == 1, "expected exactly one discovery thread"
    discovery_threads[0].join(timeout=5.0)
    assert not discovery_threads[0].is_alive(), "discovery thread did not finish in time"

    assert calls == [tmp_path]


@pytest.mark.gui
def test_run_gui_debug_accounts_mode_skips_retry_and_discovery(tmp_path, monkeypatch):
    """TOKITTY_DEBUG_ACCOUNTS bypasses first-run resolution and discovery
    entirely (acceptance criteria); the retry call rides along with that
    same bypass since debug mode already skips normal account
    resolution -- verified here rather than assumed."""
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)
    monkeypatch.setenv("TOKITTY_DEBUG_ACCOUNTS", "2")

    calls = []
    monkeypatch.setattr(main_module, "retry_pending_hook_op", lambda state_dir: calls.append(state_dir))
    spawned = _capture_spawned_threads(monkeypatch)

    result = main_module.run_gui()
    assert result == 0

    discovery_threads = [t for t in spawned if t.recorded_target_name == "run_discovery"]
    assert discovery_threads == [], "debug-accounts mode must not launch the discovery thread"
    assert calls == [], "debug-accounts mode must not retry the pending hook op"


@pytest.mark.gui
def test_auto_open_fires_via_tick_even_when_discovery_finishes_before_mainloop(tmp_path, monkeypatch):
    """root.after(0, maybe_auto_open) called directly from run_discovery's
    background thread, before root.mainloop() has actually started,
    raises "main thread is not in main loop" and the scheduled callback
    is silently DROPPED FOREVER, not merely delayed, even once mainloop()
    eventually starts. Since run_discovery starts well before the
    synchronous unit-building loop even begins, it is entirely plausible
    for discovery to finish before mainloop() is reached on a real
    launch.

    The fix: run_discovery only ever writes discovery_result under a lock;
    maybe_auto_open is invoked exclusively from tick(), which polls that
    flag on the Tk thread via its own self-rescheduling
    root.after(UI_REFRESH_MS, tick) -- the same mechanism this file
    already uses for Poller/ActivityWatcher results.

    This test proves the fix holds under that exact adversarial ordering,
    deterministically rather than hoping a race lands right:
    threading.Thread is patched so specifically run_discovery's thread
    executes synchronously, in-place, the instant .start() is called --
    i.e. discovery_result["done"] becomes True before run_gui() even
    reaches the unit-building loop, let alone root.mainloop().
    resolve_first_run_action is forced to return True (bypassing the real
    accounts.json/WSL-count precedence logic covered separately by
    test_startup.py) and AccountsManager.open is replaced with a spy, so
    this test only has to prove the wiring -- discovery-finishes-first
    still reaches AccountsManager.open() -- once mainloop() actually
    starts pumping real Tcl events."""
    tk = pytest.importorskip("tkinter")

    from tokitty import __main__ as main_module
    from tokitty import accounts_ui as accounts_ui_module
    from tokitty import startup as startup_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    # run_gui's gate is resolve_first_run_action now; should_auto_open is a
    # thin wrapper over it and is no longer the seam run_gui consults.
    monkeypatch.setattr(
        startup_module, "resolve_first_run_action", lambda **kwargs: startup_module.ACTION_ACCOUNTS
    )

    opened = []
    monkeypatch.setattr(
        accounts_ui_module.AccountsManager, "open",
        classmethod(
            lambda cls, root, state_dir, discovered_matches=None, focus_usage=False: opened.append(state_dir)
        ),
    )

    # threading.Thread is deliberately left real (not patched to run
    # synchronously): the bug this test guards against is specifically a
    # *cross-thread* Tk call, so run_discovery has to actually run on a
    # genuinely different OS thread than the one that will call
    # mainloop() for this test to mean anything. It gets there first on
    # its own in practice -- its real work here (a mocked retry, no win32
    # branch on this platform) is a handful of dict/lock operations,
    # while the main thread still has substantial synchronous setup left
    # (load_customization, migrate_default_customization, building one
    # Poller/ActivityWatcher/CredentialLoader per account, save_settings,
    # TrayManager) before it ever reaches root.mainloop() below.

    def _pumping_mainloop(self):
        # A real (bounded) event pump, not a no-op: tick()'s first
        # self-scheduled root.after(UI_REFRESH_MS, tick) has to actually
        # fire for this test to mean anything.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not opened:
            self.update()
            time.sleep(0.01)

    monkeypatch.setattr(tk.Tk, "mainloop", _pumping_mainloop)

    result = main_module.run_gui()
    assert result == 0
    assert opened == [tmp_path], "maybe_auto_open must still fire via tick() once mainloop starts pumping"


def _run_gui_with_forced_auto_open(tmp_path, monkeypatch, tk):
    """Shared setup for the two exception-containment tests below: forces
    resolve_first_run_action to ACTION_ACCOUNTS and spies on AccountsManager.open, so
    "discovery_result['done'] got set and tick() consumed it" can be
    observed indirectly (there's no other seam into run_gui's locals).
    Returns the `opened` list -- non-empty means maybe_auto_open fired."""
    from tokitty import __main__ as main_module
    from tokitty import accounts_ui as accounts_ui_module
    from tokitty import startup as startup_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    # run_gui's gate is resolve_first_run_action now; should_auto_open is a
    # thin wrapper over it and is no longer the seam run_gui consults.
    monkeypatch.setattr(
        startup_module, "resolve_first_run_action", lambda **kwargs: startup_module.ACTION_ACCOUNTS
    )

    opened = []
    monkeypatch.setattr(
        accounts_ui_module.AccountsManager, "open",
        classmethod(
            lambda cls, root, state_dir, discovered_matches=None, focus_usage=False: opened.append(state_dir)
        ),
    )

    def _pumping_mainloop(self):
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not opened:
            self.update()
            time.sleep(0.01)
        # tick() shows a startup warning (if any) via root.after(0, ...)
        # rather than synchronously, so it fires on a later pass of the
        # event loop than the one that populated `opened`. A handful of
        # extra pumps here gives it that chance; harmless for the tests
        # that don't care about it.
        for _ in range(5):
            self.update()
            time.sleep(0.01)

    monkeypatch.setattr(tk.Tk, "mainloop", _pumping_mainloop)
    return main_module, opened


@pytest.mark.gui
def test_startup_hook_warnings_show_once_via_messagebox(tmp_path, monkeypatch):
    """retry_pending_hook_op's warning and an ensure_current result's
    warning both reach one messagebox.showwarning call on the Tk thread,
    exactly once per launch. messagebox.showwarning is patched on the
    actual submodule object, not on tokitty.__main__, because tick()'s
    `from tkinter
    import messagebox` is a local import that binds to that same
    submodule at call time (see test_run_gui_toggle_autostart_shows_
    warning_on_translocation's identical reasoning)."""
    tk = pytest.importorskip("tkinter")
    import tkinter.messagebox as messagebox_module

    from tokitty import hooks_install as hooks_install_module

    main_module, opened = _run_gui_with_forced_auto_open(tmp_path, monkeypatch, tk)

    monkeypatch.setattr(
        main_module, "retry_pending_hook_op",
        lambda state_dir: hooks_install_module.ConfigDirResult(
            str(tmp_path / "a"), True, "installed", warning="retry warning"
        ),
    )
    monkeypatch.setattr(
        hooks_install_module, "ensure_current",
        lambda state_dir, refresh_fn=None: [
            hooks_install_module.ConfigDirResult(
                str(tmp_path / "b"), True, "refreshed", warning="ensure warning"
            )
        ],
    )

    warnings = []
    monkeypatch.setattr(
        messagebox_module, "showwarning", lambda *a, **k: warnings.append((a, k))
    )

    result = main_module.run_gui()

    assert result == 0
    assert opened == [tmp_path]
    assert len(warnings) == 1
    args, kwargs = warnings[0]
    assert args[0] == "Tokitty"
    assert "retry warning" in args[1]
    assert "ensure warning" in args[1]
    assert kwargs.get("parent") is not None


@pytest.mark.gui
def test_startup_hook_warnings_reach_messagebox_via_real_ensure_current(tmp_path, monkeypatch):
    """Every other messagebox test in this file (including the one
    directly above) stubs hooks_install.
    ensure_current outright via the file's autouse fixture
    (_no_real_hook_refresh_at_startup), so none of them actually exercises
    ensure_current's own account resolution (reading accounts.json,
    filtering by provider, the per-account try/except) through
    run_discovery. This test does: a real accounts.json is written to
    tmp_path via save_accounts, and only refresh_hooks_for_dir (what the
    real ensure_current calls per account) is faked, to get a
    deterministic warning without a full frozen-build/link fixture.
    Overrides the autouse stub for this test only, restoring the real
    ensure_current captured at module import time, before any per-test
    patching happened."""
    tk = pytest.importorskip("tkinter")
    import tkinter.messagebox as messagebox_module

    from tokitty import hooks_install as hooks_install_module
    from tokitty.accounts import Account, save_accounts

    main_module, opened = _run_gui_with_forced_auto_open(tmp_path, monkeypatch, tk)

    monkeypatch.setattr(hooks_install_module, "ensure_current", _real_ensure_current)

    config_dir = tmp_path / "acct" / ".claude"
    save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir=str(config_dir))])

    monkeypatch.setattr(
        hooks_install_module, "refresh_hooks_for_dir",
        lambda cd, provider: hooks_install_module.ConfigDirResult(
            cd, True, "refreshed", warning="real ensure_current warning"
        ),
    )

    warnings = []
    monkeypatch.setattr(
        messagebox_module, "showwarning", lambda *a, **k: warnings.append((a, k))
    )

    result = main_module.run_gui()

    assert result == 0
    assert opened == [tmp_path]
    assert len(warnings) == 1
    args, kwargs = warnings[0]
    assert args[0] == "Tokitty"
    assert "real ensure_current warning" in args[1]
    assert kwargs.get("parent") is not None


@pytest.mark.gui
def test_run_discovery_notes_translocation_with_no_eligible_accounts(tmp_path, monkeypatch):
    """With no accounts.json (so ensure_current, stubbed by this file's
    autouse fixture to mirror its real "nothing to do" behaviour, never
    reports anything), a frozen launch whose own ensure_runner_link call
    raises AppTranslocatedError must still surface it, since no
    per-account path covers this case. The fake release lives entirely
    under tmp_path and is never touched: is_translocated only inspects
    the path string, so no files need to exist for ensure_runner_link to
    raise before any filesystem access."""
    tk = pytest.importorskip("tkinter")
    import tkinter.messagebox as messagebox_module

    from tokitty.frozen import MOVE_TO_APPLICATIONS

    main_module, opened = _run_gui_with_forced_auto_open(tmp_path, monkeypatch, tk)

    translocated_exe = tmp_path / "AppTranslocation" / "abc123" / "Tokitty.app" / "Contents" / "MacOS" / "tokitty"
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main_module.sys, "executable", str(translocated_exe))

    warnings = []
    monkeypatch.setattr(
        messagebox_module, "showwarning", lambda *a, **k: warnings.append((a, k))
    )

    result = main_module.run_gui()

    assert result == 0
    assert opened == [tmp_path]
    assert len(warnings) == 1
    args, kwargs = warnings[0]
    assert args[0] == "Tokitty"
    assert args[1].count(MOVE_TO_APPLICATIONS) == 1
    assert kwargs.get("parent") is not None


@pytest.mark.gui
def test_run_discovery_survives_retry_pending_hook_op_raising(tmp_path, monkeypatch):
    """retry_pending_hook_op can raise raw OSError/PermissionError from
    the underlying hook
    install/uninstall functions (documented in the design spec's Write
    ordering and crash consistency section -- they don't convert
    filesystem exceptions to a result object). Uncaught, this would abort
    run_discovery's thread before discovery_result["done"] is ever set --
    worse than the original bug, since then auto-open would silently
    never fire for the whole launch, not just misfire once. Confirms the
    thread survives and discovery_result["done"] still gets set (proven
    indirectly: maybe_auto_open still fires via tick())."""
    tk = pytest.importorskip("tkinter")

    main_module, opened = _run_gui_with_forced_auto_open(tmp_path, monkeypatch, tk)
    monkeypatch.setattr(
        main_module, "retry_pending_hook_op",
        lambda state_dir: (_ for _ in ()).throw(OSError("disk on fire")),
    )

    result = main_module.run_gui()
    assert result == 0
    assert opened == [tmp_path], (
        "discovery_result['done'] must still get set despite retry_pending_hook_op raising OSError"
    )


@pytest.mark.gui
def test_run_discovery_survives_wsl_scan_raising_credentials_error(tmp_path, monkeypatch):
    """find_all_wsl_credentials can raise CredentialsError (a real,
    common case: wsl.exe missing from PATH entirely, i.e. a
    native-Windows Claude Code install with no WSL at all). Uncaught, this
    would abort run_discovery's thread before discovery_result["done"] is
    ever set, mirroring the exact "resolution failure means run without
    it, never a crash" philosophy resolve_activity_sessions already
    applies to this same exception (__main__.py's existing
    `except CredentialsError: return None, None`).

    sys.platform is forced globally to "win32" here (there's no narrower
    seam -- every win32 branch in this codebase reads the real sys module
    directly), which also flips SingleInstanceLock.acquire()/release()
    onto their msvcrt path; msvcrt doesn't exist on this non-Windows test
    runner, so the lock is stubbed out here too. Every other sys.platform
    check reachable from run_gui on this path (resolve_activity_sessions's
    own WSL branch, ui.py's DPI-awareness call, TrayManager._probe) is
    already independently guarded against exactly this (verified by
    reading each) -- only the lock is not, since acquire()'s ImportError
    isn't a subclass of the OSError it already catches."""
    tk = pytest.importorskip("tkinter")
    from tokitty.lock import SingleInstanceLock

    monkeypatch.setattr(SingleInstanceLock, "acquire", lambda self: None)
    monkeypatch.setattr(SingleInstanceLock, "release", lambda self: None)

    main_module, opened = _run_gui_with_forced_auto_open(tmp_path, monkeypatch, tk)
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    monkeypatch.setattr(
        "tokitty.wsl_probe.find_all_wsl_credentials",
        lambda: (_ for _ in ()).throw(CredentialsError("wsl.exe not found on PATH")),
    )

    result = main_module.run_gui()
    assert result == 0
    assert opened == [tmp_path], (
        "discovery_result['done'] must still get set despite the WSL scan raising CredentialsError"
    )


@pytest.mark.gui
def test_run_discovery_repoints_runner_link_when_frozen(tmp_path, monkeypatch):
    """A frozen launch must repoint <state dir>/current at the running
    release before retry_pending_hook_op runs, with no --install-hooks
    call involved at all. Uses a fake host-native release under
    tmp_path -- never a real directory outside it."""
    import os
    import sys

    tk = pytest.importorskip("tkinter")
    from tokitty import runner_link

    exe_name = "tokitty.exe" if sys.platform == "win32" else "tokitty"
    runner_name = "tokitty-hook.exe" if sys.platform == "win32" else "tokitty-hook"
    release = tmp_path / "release-a"
    release.mkdir()
    exe = release / exe_name
    exe.write_text("gui", encoding="utf-8")
    (release / runner_name).write_text("hook", encoding="utf-8")

    main_module, opened = _run_gui_with_forced_auto_open(tmp_path, monkeypatch, tk)
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main_module.sys, "executable", str(exe))

    try:
        result = main_module.run_gui()
        assert result == 0
        assert opened == [tmp_path]
        assert os.path.realpath(tmp_path / "current") == os.path.realpath(release)
    finally:
        link = tmp_path / "current"
        if runner_link._is_link(str(link)):
            (os.rmdir if sys.platform == "win32" else os.unlink)(link)


@pytest.mark.gui
def test_auto_open_passes_discovered_wsl_matches_to_accounts_manager(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import accounts_ui as accounts_ui_module
    from tokitty import startup as startup_module
    from tokitty.lock import SingleInstanceLock
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    # run_gui's gate is resolve_first_run_action now; should_auto_open is a
    # thin wrapper over it and is no longer the seam run_gui consults.
    monkeypatch.setattr(
        startup_module, "resolve_first_run_action", lambda **kwargs: startup_module.ACTION_ACCOUNTS
    )
    monkeypatch.setattr(SingleInstanceLock, "acquire", lambda self: None)
    monkeypatch.setattr(SingleInstanceLock, "release", lambda self: None)
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    monkeypatch.setattr(
        main_module.Path, "home", classmethod(lambda cls: tmp_path / "home")
    )
    matches = [
        ("Ubuntu", "/home/a/.claude/.credentials.json"),
        ("Debian", "/home/b/.claude-work/.credentials.json"),
    ]
    monkeypatch.setattr("tokitty.wsl_probe.find_all_wsl_credentials", lambda: matches)

    opened = []
    monkeypatch.setattr(
        accounts_ui_module.AccountsManager,
        "open",
        classmethod(
            lambda cls, root, state_dir, discovered_matches=None, focus_usage=False:
                opened.append((state_dir, list(discovered_matches or [])))
        ),
    )

    def _pumping_mainloop(self):
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not opened:
            self.update()
            time.sleep(0.01)

    monkeypatch.setattr(tk.Tk, "mainloop", _pumping_mainloop)

    assert main_module.run_gui() == 0
    assert opened == [(tmp_path, matches)]


@pytest.mark.gui
def test_run_discovery_skips_wsl_scan_when_accounts_file_is_present(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty.accounts import Account, save_accounts
    from tokitty.lock import SingleInstanceLock
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir=str(tmp_path / "claude"))])
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)
    monkeypatch.setattr(SingleInstanceLock, "acquire", lambda self: None)
    monkeypatch.setattr(SingleInstanceLock, "release", lambda self: None)
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    scans = []
    retries = []
    monkeypatch.setattr(
        "tokitty.wsl_probe.find_all_wsl_credentials", lambda: scans.append(1) or []
    )
    monkeypatch.setattr(
        main_module, "retry_pending_hook_op", lambda state_dir: retries.append(state_dir)
    )
    spawned = _capture_spawned_threads(monkeypatch)

    assert main_module.run_gui() == 0
    discovery_threads = [t for t in spawned if t.recorded_target_name == "run_discovery"]
    assert len(discovery_threads) == 1
    discovery_threads[0].join(timeout=5.0)

    assert scans == []
    assert retries == [tmp_path]


@pytest.mark.gui
@pytest.mark.parametrize("credential_source", ["env_override", "home_relative"])
def test_run_discovery_skips_wsl_scan_when_native_credentials_exist(
    tmp_path, monkeypatch, credential_source
):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty.lock import SingleInstanceLock
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    credentials_path = tmp_path / "home" / ".claude" / ".credentials.json"
    credentials_path.parent.mkdir(parents=True)
    credentials_path.write_text('{"claudeAiOauth": {}}', encoding="utf-8")

    if credential_source == "env_override":
        monkeypatch.setenv("TOKITTY_CREDENTIALS", str(credentials_path))
    else:
        monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
        monkeypatch.setattr(
            main_module.Path,
            "home",
            classmethod(lambda cls: tmp_path / "home"),
        )

    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)
    monkeypatch.setattr(SingleInstanceLock, "acquire", lambda self: None)
    monkeypatch.setattr(SingleInstanceLock, "release", lambda self: None)
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    scans = []
    retries = []
    monkeypatch.setattr(
        "tokitty.wsl_probe.find_all_wsl_credentials",
        lambda: scans.append(1) or [],
    )
    monkeypatch.setattr(
        main_module,
        "retry_pending_hook_op",
        lambda state_dir: retries.append(state_dir),
    )
    spawned = _capture_spawned_threads(monkeypatch)

    assert main_module.run_gui() == 0
    discovery_threads = [
        thread
        for thread in spawned
        if thread.recorded_target_name == "run_discovery"
    ]
    assert len(discovery_threads) == 1
    discovery_threads[0].join(timeout=5.0)
    assert not discovery_threads[0].is_alive()

    assert scans == []
    assert retries == [tmp_path]


@pytest.mark.gui
def test_run_gui_persists_migration_before_marking_it_complete(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import migration
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)
    events = []
    real_save = main_module.save_customization
    real_mark = migration.mark_customization_migration_complete

    def recording_save(state_dir, store):
        events.append("save")
        return real_save(state_dir, store)

    def recording_mark(state_dir, key):
        events.append(f"mark:{key}")
        return real_mark(state_dir, key)

    monkeypatch.setattr(main_module, "save_customization", recording_save)
    monkeypatch.setattr(migration, "mark_customization_migration_complete", recording_mark)

    assert main_module.run_gui() == 0
    assert events[:3] == [
        "save",
        f"mark:{migration.CUSTOMIZATION_MIGRATION_KEY}",
        f"mark:{migration.LEGACY_ACCOUNT_LABELS_MIGRATION_KEY}",
    ]


@pytest.mark.gui
def test_live_customization_change_preserves_manager_added_key(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import ui
    from tokitty.customize import Customization, load_customization, save_customization
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    holder = {}
    real_window = ui.TokittyWindow

    class CapturingWindow(real_window):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["window"] = self

    monkeypatch.setattr(ui, "TokittyWindow", CapturingWindow)

    def _mainloop(self):
        latest = load_customization(tmp_path)
        latest["default"] = replace(latest["default"], label="Renamed in manager")
        latest["acct-v1-added"] = Customization(
            colorway="gray", pattern="solid", label="Added in manager"
        )
        save_customization(tmp_path, latest)
        holder["window"].on_customization_changed(0, "randomize", None)

    monkeypatch.setattr(tk.Tk, "mainloop", _mainloop)

    assert main_module.run_gui() == 0
    stored = load_customization(tmp_path)
    assert stored["default"].label == "Renamed in manager"
    assert stored["acct-v1-added"].label == "Added in manager"


class _FakeToggleBackend:
    def __init__(self, registered=False):
        self.registered = registered
        self.last_registered_command = None

    def is_registered(self):
        return self.registered

    def is_current(self, command):
        return self.registered and self.last_registered_command == command

    def register(self, command):
        self.registered = True
        self.last_registered_command = command

    def deregister(self):
        self.registered = False


@pytest.mark.gui
def test_run_gui_wires_autostart_seam_and_toggle(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import ui
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)

    fake_backend = _FakeToggleBackend(registered=True)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: fake_backend)

    holder = {}
    real_window = ui.TokittyWindow

    class CapturingWindow(real_window):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["window"] = self

    monkeypatch.setattr(ui, "TokittyWindow", CapturingWindow)

    def _mainloop(self):
        window = holder["window"]
        assert window.autostart_enabled() is True
        window.on_toggle_autostart()
        assert fake_backend.registered is False
        assert window.autostart_enabled() is False

    monkeypatch.setattr(tk.Tk, "mainloop", _mainloop)
    assert main_module.run_gui() == 0


@pytest.mark.gui
def test_run_gui_leaves_autostart_seam_none_on_unsupported_platform(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import ui
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: None)

    holder = {}
    real_window = ui.TokittyWindow

    class CapturingWindow(real_window):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["window"] = self

    monkeypatch.setattr(ui, "TokittyWindow", CapturingWindow)

    def _mainloop(self):
        window = holder["window"]
        assert window.autostart_enabled is None
        assert window.on_toggle_autostart is None

    monkeypatch.setattr(tk.Tk, "mainloop", _mainloop)
    assert main_module.run_gui() == 0


@pytest.mark.gui
def test_run_gui_calls_ensure_current_at_startup(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)

    fake_backend = _FakeToggleBackend(registered=True)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: fake_backend)
    calls = []
    monkeypatch.setattr(
        "tokitty.autostart.ensure_current", lambda state_dir, backend: calls.append((state_dir, backend))
    )

    assert main_module.run_gui() == 0
    assert calls == [(tmp_path, fake_backend)]


def test_main_dispatches_self_check(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.frozen.self_check", lambda: calls.append("self-check") or 0)

    assert main_module.main(["--self-check"]) == 0
    assert calls == ["self-check"]


def test_main_dispatches_check_for_update(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.updater.check_for_update_cli", lambda: calls.append("check") or 0)

    assert main_module.main(["--check-for-update"]) == 0
    assert calls == ["check"]


def test_main_dispatches_install_autostart(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.autostart.install_autostart", lambda: calls.append("install") or 0)

    assert main_module.main(["--install-autostart"]) == 0
    assert calls == ["install"]


def test_main_dispatches_uninstall_autostart(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.autostart.uninstall_autostart", lambda: calls.append("uninstall") or 0)

    assert main_module.main(["--uninstall-autostart"]) == 0
    assert calls == ["uninstall"]


@pytest.mark.gui
def test_run_gui_toggle_autostart_registers_via_shared_path(tmp_path, monkeypatch):
    """Companion to test_run_gui_wires_autostart_seam_and_toggle, which
    only exercises the deregister branch. This covers the other half:
    turning the checkbox on goes through write_launcher_and_register,
    the same shared helper install_autostart uses, so the menu and the
    CLI can never register autostart two different ways."""
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import ui
    from tokitty.autostart import LAUNCHER_FILENAME, resolve_launch_command
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)

    fake_backend = _FakeToggleBackend(registered=False)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: fake_backend)

    holder = {}
    real_window = ui.TokittyWindow

    class CapturingWindow(real_window):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["window"] = self

    monkeypatch.setattr(ui, "TokittyWindow", CapturingWindow)

    def _mainloop(self):
        window = holder["window"]
        assert window.autostart_enabled() is False
        window.on_toggle_autostart()
        assert fake_backend.registered is True
        assert fake_backend.last_registered_command == resolve_launch_command(tmp_path)
        assert (tmp_path / LAUNCHER_FILENAME).is_file()
        assert window.autostart_enabled() is True

    monkeypatch.setattr(tk.Tk, "mainloop", _mainloop)
    assert main_module.run_gui() == 0


@pytest.mark.gui
def test_run_gui_toggle_autostart_shows_warning_on_translocation(tmp_path, monkeypatch):
    """Third branch of toggle_autostart, alongside the register and
    deregister cases above: a frozen build running from a macOS
    App Translocation path must never reach backend.register, and the
    user has to be told why the checkbox didn't move. write_launcher_
    and_register is patched at its source in tokitty.autostart -- the
    same module run_gui's local `from tokitty.autostart import ...`
    resolves against on every call -- to raise AppTranslocatedError
    without needing a real frozen executable. tkinter.messagebox.
    showwarning is patched on the actual submodule object, not on
    tokitty.__main__, because toggle_autostart's `from tkinter import
    messagebox` is a local import inside the except branch and binds to
    that same submodule at call time (see test_accounts_ui.py's
    identical reasoning for messagebox.showerror)."""
    tk = pytest.importorskip("tkinter")
    import tkinter.messagebox as messagebox_module

    from tokitty import __main__ as main_module
    from tokitty import ui
    from tokitty.autostart import AppTranslocatedError
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)

    fake_backend = _FakeToggleBackend(registered=False)
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda: fake_backend)

    def _raise_translocated(state_dir, backend, **kwargs):
        raise AppTranslocatedError()

    monkeypatch.setattr("tokitty.autostart.write_launcher_and_register", _raise_translocated)

    warnings = []
    monkeypatch.setattr(
        messagebox_module, "showwarning", lambda *a, **k: warnings.append((a, k))
    )

    holder = {}
    real_window = ui.TokittyWindow

    class CapturingWindow(real_window):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["window"] = self

    monkeypatch.setattr(ui, "TokittyWindow", CapturingWindow)

    def _mainloop(self):
        window = holder["window"]
        assert window.autostart_enabled() is False
        window.on_toggle_autostart()
        assert fake_backend.registered is False
        assert window.autostart_enabled() is False
        assert len(warnings) == 1
        args, kwargs = warnings[0]
        assert args[0] == "Start at login"
        assert "Applications" in args[1]
        assert kwargs.get("parent") is not None

    monkeypatch.setattr(tk.Tk, "mainloop", _mainloop)
    assert main_module.run_gui() == 0


@pytest.mark.gui
def test_usage_setup_first_run_opens_the_dialog_focused_on_usage(tmp_path, monkeypatch):
    """The API-key user's first run: no credentials anywhere, transcripts
    on disk. Previously this path opened nothing at all and the pane just
    said "can't find credentials" forever."""
    import tokitty.accounts_ui as accounts_ui_module
    from tokitty.startup import ACTION_USAGE_SETUP, resolve_first_run_action

    assert (
        resolve_first_run_action(
            accounts_state="absent",
            env_override_set=False,
            home_relative_exists=False,
            keychain_available=False,
            platform="win32",
            wsl_match_count=0,
            transcripts_found=True,
        )
        == ACTION_USAGE_SETUP
    )

    calls = []
    monkeypatch.setattr(
        accounts_ui_module.AccountsManager,
        "open",
        classmethod(
            lambda cls, root, state_dir, discovered_matches=None, focus_usage=False: calls.append(
                focus_usage
            )
        ),
    )
    accounts_ui_module.AccountsManager.open(None, tmp_path, focus_usage=True)
    assert calls == [True]


def test_both_resolvers_share_one_credential_sweep(monkeypatch):
    # Issue #52: a launch with no accounts.json used to sweep every WSL
    # distro once per caller. Both resolvers now read the same cache, so
    # whichever runs first pays for the only sweep there is.
    from tokitty.wsl_probe import WslCredentialsCache

    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    sweeps = {"n": 0}

    def fake_scan():
        sweeps["n"] += 1
        return [("Ubuntu", "/home/n/.claude/.credentials.json")]

    cache = WslCredentialsCache(scan=fake_scan)

    sessions_dir, sessions_distro = resolve_activity_sessions(None, credentials=cache)
    projects_dir, projects_distro = resolve_projects_dir(None, credentials=cache)

    assert sweeps["n"] == 1
    assert sessions_distro == "Ubuntu"
    assert projects_distro == "Ubuntu"
    assert sessions_dir.endswith("\\tokitty\\sessions")
    assert projects_dir.endswith("\\projects")


def test_resolvers_still_work_without_a_cache(monkeypatch):
    # debug_print and the existing direct-call tests pass no cache; the
    # bare-function path has to stay intact.
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    monkeypatch.setattr(
        "tokitty.wsl_probe.find_wsl_credentials",
        lambda *a, **k: ("Ubuntu", "/home/n/.claude/.credentials.json"),
    )
    sessions_dir, distro = resolve_activity_sessions(None)
    assert distro == "Ubuntu"
    assert sessions_dir.endswith("\\tokitty\\sessions")


def test_main_dispatches_streamdock_install_and_uninstall(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.streamdock.install.run_install", lambda: calls.append("install") or 0)
    monkeypatch.setattr("tokitty.streamdock.install.run_uninstall", lambda: calls.append("uninstall") or 0)

    assert main_module.main(["--install-streamdock"]) == 0
    assert main_module.main(["--uninstall-streamdock"]) == 0
    assert calls == ["install", "uninstall"]


def test_main_dispatches_apply_update_and_after_update(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr(main_module, "run_gui", lambda **kw: calls.append(kw) or 7)

    assert main_module.main(["--apply-update"]) == 7
    assert main_module.main(["--after-update", "tok-1_a"]) == 7
    assert calls == [
        {"after_update_token": None, "apply_update": True},
        {"after_update_token": "tok-1_a", "apply_update": False},
    ]


def test_main_runs_the_plain_gui_without_update_flags(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr(main_module, "run_gui", lambda *a, **k: calls.append((a, k)) or 0)

    assert main_module.main([]) == 0
    assert calls == [((), {})]


@pytest.mark.parametrize("argv", [["--after-update"], ["--after-update", ""], ["--after-update", "../x"]])
def test_main_rejects_a_missing_or_bad_update_token(monkeypatch, capsys, argv):
    from tokitty import __main__ as main_module

    monkeypatch.setattr(main_module, "run_gui", lambda *a, **k: pytest.fail("must not start"))

    assert main_module.main(argv) == 2
    assert "--after-update needs a token" in capsys.readouterr().err


# --- the update flow inside run_gui -------------------------------------------


def _update_gui(tmp_path, monkeypatch, mainloop):
    """A run_gui environment: state in tmp_path, tray off, Tk.mainloop replaced."""
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.Tk, "mainloop", mainloop)
    return tk, main_module


def _pump_until_destroyed(tk, seconds=15.0):
    def mainloop(self):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self.update()
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            time.sleep(0.01)
        raise AssertionError("the app never closed")

    return mainloop


def _release_listing(tag):
    name = f"tokitty-{tag}-linux-x86_64.tar.gz"
    return [
        {
            "tag_name": tag,
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": name, "browser_download_url": f"https://example.test/{name}", "size": 10},
                {"name": "SHA256SUMS", "browser_download_url": "https://example.test/SHA256SUMS"},
            ],
        }
    ]


class _UpdateEnv:
    """Fakes for every process-specific seam of the update controller."""

    def __init__(self, tmp_path, monkeypatch, listing=None, acked=True):
        from tokitty import update_controller
        from tokitty.update_install import Staged
        from tokitty.updater import RunningVersion

        self.tmp_path, self.events, self.acked = tmp_path, [], acked
        self.instances = []
        (tmp_path / "releases").mkdir()
        monkeypatch.setattr(update_controller, "platform_target", lambda *a, **k: "linux-x86_64")
        monkeypatch.setattr("tokitty.runner_link.ensure_runner_link", lambda sd: self.events.append("link"))
        real = update_controller.UpdateController

        def stage(release, target, state_dir, **kw):
            self.events.append("stage")
            top = target.parent / ".stage" / target.top
            return Staged(target.parent / ".stage", top, top / "gui")

        def promote(staged, target, tag, state_dir, **kw):
            self.events.append("promote")
            return target.final_path(tag)

        def launch(argv, env, sys_platform):
            from tokitty.lock import SingleInstanceLock

            probe = SingleInstanceLock(tmp_path)
            probe.acquire()  # raises unless the old copy let go of the lock
            probe.release()
            self.events.append(("launch", argv[1:]))
            return type("C", (), {"poll": lambda s: None, "kill": lambda s: self.events.append("kill"),
                                  "wait": lambda s, timeout=None: 0})()

        def wait_for_ack(path, timeout, abort=None):
            self.events.append("wait")
            return self.acked

        def factory(state_dir, host):
            controller = real(
                state_dir,
                host,
                running=RunningVersion("v0.1.0", True),
                executable=str(tmp_path / "releases" / "v0.1.0" / "tokitty" / "tokitty"),
                sys_platform="linux",
                environ={},
                stage=stage,
                self_check=lambda gui, tag: None,
                promote=promote,
                launch=launch,
                wait_for_ack=wait_for_ack,
                fetch=lambda: _release_listing("v0.2.0") if listing is None else listing,
                new_token=lambda: "tok9",
            )
            self.instances.append(controller)
            return controller

        monkeypatch.setattr(update_controller, "UpdateController", factory)


@pytest.mark.gui
def test_apply_update_hands_over_and_exits_zero(tmp_path, monkeypatch):
    tk, main_module = _update_gui(tmp_path, monkeypatch, None)
    monkeypatch.setattr(tk.Tk, "mainloop", _pump_until_destroyed(tk))
    env = _UpdateEnv(tmp_path, monkeypatch)

    assert main_module.run_gui(apply_update=True) == 0

    assert env.events == ["stage", "promote", ("launch", ["--after-update", "tok9"]), "wait"]
    from tokitty.lock import SingleInstanceLock
    from tokitty.updater import load_update_state

    assert load_update_state(tmp_path).pending is None
    SingleInstanceLock(tmp_path).acquire()  # the old copy is gone and the lock is free


@pytest.mark.gui
def test_apply_update_exits_two_after_a_clean_no_ack_rollback(tmp_path, monkeypatch, capsys):
    tk, main_module = _update_gui(tmp_path, monkeypatch, None)
    monkeypatch.setattr(tk.Tk, "mainloop", _pump_until_destroyed(tk))
    env = _UpdateEnv(tmp_path, monkeypatch, acked=False)

    assert main_module.run_gui(apply_update=True) == 2

    assert env.events == ["stage", "promote", ("launch", ["--after-update", "tok9"]), "wait", "kill", "link"]
    assert "didn't start. Still running v0.1.0." in capsys.readouterr().err


@pytest.mark.gui
def test_apply_update_exits_one_when_there_is_nothing_to_install(tmp_path, monkeypatch, capsys):
    tk, main_module = _update_gui(tmp_path, monkeypatch, None)
    monkeypatch.setattr(tk.Tk, "mainloop", _pump_until_destroyed(tk))
    env = _UpdateEnv(tmp_path, monkeypatch, listing=_release_listing("v0.1.0"))

    assert main_module.run_gui(apply_update=True) == 1

    assert env.events == []
    assert "no release newer than v0.1.0" in capsys.readouterr().err


@pytest.mark.gui
def test_a_rollback_outside_apply_mode_shows_the_message_and_keeps_running(tmp_path, monkeypatch):
    from tokitty.updater import Release

    pytest.importorskip("tkinter")
    shown = []

    def mainloop(self):
        controller = env.instances[0]
        done = []
        controller.start_install(Release("v0.2.0", None, "https://x/a", 10, "https://x/s"), None, done.append)
        end = time.monotonic() + 15
        while (not done or not shown) and time.monotonic() < end:
            self.update()
            time.sleep(0.01)
        assert done and done[0].outcome == "rolled_back"
        assert self.state() == "normal"  # withdrawn by the handover, shown again by the rollback

    _, main_module = _update_gui(tmp_path, monkeypatch, mainloop)
    env = _UpdateEnv(tmp_path, monkeypatch, acked=False)
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda title, text, parent=None: shown.append((title, text)))

    assert main_module.run_gui() == 0

    assert shown == [("Tokitty", "Tokitty v0.2.0 didn't start. Still running v0.1.0.")]
    assert env.events[-2:] == ["kill", "link"]


@pytest.mark.gui
def test_after_update_writes_the_ack_once_the_mainloop_is_running(tmp_path, monkeypatch):
    seen = []

    def mainloop(self):
        seen.append((tmp_path / "update-ack-tok1").exists())  # not before the mainloop
        for _ in range(3):
            self.update()

    tk, main_module = _update_gui(tmp_path, monkeypatch, mainloop)

    assert main_module.run_gui(after_update_token="tok1") == 0

    assert seen == [False]
    assert (tmp_path / "update-ack-tok1").read_text() == "tok1"


@pytest.mark.gui
def test_after_update_waits_for_the_old_copy_to_release_the_lock(tmp_path, monkeypatch):
    from tokitty.lock import SingleInstanceLock

    tk, main_module = _update_gui(tmp_path, monkeypatch, lambda self: self.update())
    old_copy = SingleInstanceLock(tmp_path)
    old_copy.acquire()
    timer = threading.Thread(target=lambda: (time.sleep(0.4), old_copy.release()))
    timer.start()
    try:
        assert main_module.run_gui(after_update_token="tok1") == 0
    finally:
        timer.join()

    assert (tmp_path / "update-ack-tok1").exists()


@pytest.mark.gui
def test_after_update_exits_quietly_without_an_ack_when_the_lock_stays_held(tmp_path, monkeypatch, capsys):
    from tokitty.lock import SingleInstanceLock, acquire_with_retry

    tk, main_module = _update_gui(tmp_path, monkeypatch, lambda self: pytest.fail("no window expected"))
    monkeypatch.setattr(main_module, "acquire_with_retry", lambda lock, timeout: acquire_with_retry(lock, 0.2))
    holder = SingleInstanceLock(tmp_path)
    holder.acquire()
    try:
        assert main_module.run_gui(after_update_token="tok1") == 1
    finally:
        holder.release()

    assert not (tmp_path / "update-ack-tok1").exists()
    assert capsys.readouterr().err == ""


@pytest.mark.gui
def test_no_ack_switch_exits_before_acking_only_alongside_the_api_url(tmp_path, monkeypatch):
    tk, main_module = _update_gui(tmp_path, monkeypatch, lambda self: self.update())
    monkeypatch.setenv("TOKITTY_UPDATE_TEST_NO_ACK", "1")

    # Alone, the switch is ignored: a normal start that acks.
    monkeypatch.delenv("TOKITTY_UPDATE_API_URL", raising=False)
    assert main_module.run_gui(after_update_token="tok1") == 0
    assert (tmp_path / "update-ack-tok1").exists()
    (tmp_path / "update-ack-tok1").unlink()

    # With the API URL set it exits 0 without starting up, and frees the lock.
    monkeypatch.setenv("TOKITTY_UPDATE_API_URL", "https://localhost:1")
    monkeypatch.setattr(tk, "Tk", lambda *a, **k: pytest.fail("must exit before building a window"))
    assert main_module.run_gui(after_update_token="tok1") == 0
    assert not (tmp_path / "update-ack-tok1").exists()
    from tokitty.lock import SingleInstanceLock

    SingleInstanceLock(tmp_path).acquire()


@pytest.mark.gui
def test_run_gui_clears_a_stale_pending_but_keeps_a_fresh_one(tmp_path, monkeypatch):
    from tokitty.update_swap import write_pending
    from tokitty.updater import load_update_state

    tk, main_module = _update_gui(tmp_path, monkeypatch, lambda self: None)
    fields = dict(old_version="v0.1.0", new_version="v0.2.0", old_path="a", new_path="b", token="t", staging="s")
    now = datetime.now(timezone.utc)

    write_pending(tmp_path, now=now - timedelta(minutes=10), **fields)
    assert main_module.run_gui() == 0
    assert load_update_state(tmp_path).pending is None

    write_pending(tmp_path, now=now - timedelta(minutes=1), **fields)
    assert main_module.run_gui() == 0
    assert load_update_state(tmp_path).pending is not None


@pytest.mark.gui
def test_run_gui_starts_no_cleanup_thread_in_a_source_run(tmp_path, monkeypatch):
    tk, main_module = _update_gui(tmp_path, monkeypatch, lambda self: None)
    cleanups = []
    monkeypatch.setattr("tokitty.update_controller.cleanup", lambda *a, **k: cleanups.append(1))
    spawned = _capture_spawned_threads(monkeypatch)

    assert main_module.run_gui() == 0
    for thread in spawned:
        thread.join(timeout=5.0)

    assert cleanups == []
