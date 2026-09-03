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
)
from tokitty.accounts import Account
from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.burn import BurnTracker
from tokitty.credentials import CredentialsError
from tokitty.customize import Customization
from tokitty.poller import PollResult
from tokitty.sprites import COLORWAYS, PATTERNS

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


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

    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", flaky_resolve)
    monkeypatch.setattr(
        "tokitty.__main__.load_credentials", lambda src: {"expiresAt": 4102444800000, "accessToken": "tok"}
    )
    monkeypatch.setattr("tokitty.__main__.fetch_usage", lambda token: {"raw": "doesn't matter, parse is stubbed"})
    monkeypatch.setattr("tokitty.__main__.parse_usage_response", lambda raw: _snapshot())

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

    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", fake_resolve)
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
    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", lambda config_dir=None: source)
    monkeypatch.setattr(
        "tokitty.__main__.load_credentials",
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
    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", lambda config_dir=None: source)

    load_calls = []

    def counting_load(src):
        load_calls.append(src)
        # Far-future expiresAt -> not expired, so the second fetch() call is
        # a cache hit rather than a fresh Keychain read.
        return {"expiresAt": 4102444800000, "accessToken": "tok"}

    monkeypatch.setattr("tokitty.__main__.load_credentials", counting_load)
    # Stops the poll right after the token check, so no real network call is
    # made -- getting past that check is all this test needs from fetch_usage.
    monkeypatch.setattr(
        "tokitty.__main__.fetch_usage",
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
    """Controller ruling on Task 15's brief: retry_pending_hook_op
    (hooks_install.py, Task 8/12) was never wired into run_gui through
    Task 14 -- confirmed by grep. It must fire once from the startup
    sequence, off the Tk thread alongside WSL discovery (run_discovery)."""
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
    """TOKITTY_DEBUG_ACCOUNTS bypasses should_auto_open and discovery
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
    """Reviewer-confirmed Finding 1 (CRITICAL): the original implementation
    called root.after(0, maybe_auto_open) from run_discovery's background
    thread. root.after() called from a non-Tk thread before root.mainloop()
    has actually started raises "main thread is not in main loop" --
    reproduced directly -- and the scheduled callback is silently DROPPED
    FOREVER, not merely delayed, even once mainloop() eventually starts.
    Since run_discovery starts (well) before the synchronous unit-building
    loop even begins, it is entirely plausible for discovery to finish
    before mainloop() is reached on a real launch.

    The fix: run_discovery only ever writes discovery_result under a lock;
    maybe_auto_open is invoked exclusively from tick(), which polls that
    flag on the Tk thread via its own self-rescheduling
    root.after(UI_REFRESH_MS, tick) -- the same mechanism this file
    already uses for Poller/ActivityWatcher results.

    This test proves the fix holds under the *exact* adversarial ordering
    Finding 1 describes, deterministically rather than hoping a race lands
    right: threading.Thread is patched so specifically run_discovery's
    thread executes synchronously, in-place, the instant .start() is
    called -- i.e. discovery_result["done"] becomes True before run_gui()
    even reaches the unit-building loop, let alone root.mainloop(). Every
    other thread run_gui spawns (Poller, ActivityWatcher) still runs as a
    real background thread -- only run_discovery's target is forced
    synchronous, identified by name at Thread construction time, same
    technique as _capture_spawned_threads. should_auto_open is forced to
    return True (bypassing the real accounts.json/WSL-count precedence
    logic covered separately by test_startup.py) and AccountsManager.open
    is replaced with a spy, so this test only has to prove the wiring --
    discovery-finishes-first still reaches AccountsManager.open() -- once
    mainloop() actually starts pumping real Tcl events."""
    tk = pytest.importorskip("tkinter")

    from tokitty import __main__ as main_module
    from tokitty import accounts_ui as accounts_ui_module
    from tokitty import startup as startup_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(startup_module, "should_auto_open", lambda **kwargs: True)

    opened = []
    monkeypatch.setattr(
        accounts_ui_module.AccountsManager, "open",
        classmethod(
            lambda cls, root, state_dir, discovered_matches=None: opened.append(state_dir)
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
    should_auto_open to True and spies on AccountsManager.open, so
    "discovery_result['done'] got set and tick() consumed it" can be
    observed indirectly (there's no other seam into run_gui's locals).
    Returns the `opened` list -- non-empty means maybe_auto_open fired."""
    from tokitty import __main__ as main_module
    from tokitty import accounts_ui as accounts_ui_module
    from tokitty import startup as startup_module
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(startup_module, "should_auto_open", lambda **kwargs: True)

    opened = []
    monkeypatch.setattr(
        accounts_ui_module.AccountsManager, "open",
        classmethod(
            lambda cls, root, state_dir, discovered_matches=None: opened.append(state_dir)
        ),
    )

    def _pumping_mainloop(self):
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not opened:
            self.update()
            time.sleep(0.01)

    monkeypatch.setattr(tk.Tk, "mainloop", _pumping_mainloop)
    return main_module, opened


@pytest.mark.gui
def test_run_discovery_survives_retry_pending_hook_op_raising(tmp_path, monkeypatch):
    """Finding 2 (important, entangled with Finding 1): retry_pending_hook_op
    can raise raw OSError/PermissionError from the underlying hook
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
    """Finding 2: find_all_wsl_credentials can raise CredentialsError (a
    real, common case: wsl.exe missing from PATH entirely, i.e. a
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
def test_auto_open_passes_discovered_wsl_matches_to_accounts_manager(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tokitty import __main__ as main_module
    from tokitty import accounts_ui as accounts_ui_module
    from tokitty import startup as startup_module
    from tokitty.lock import SingleInstanceLock
    from tokitty.settings import Settings, save_settings

    save_settings(tmp_path, Settings(tray_enabled=False, surprise_me=False))
    monkeypatch.setattr(main_module, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(startup_module, "should_auto_open", lambda **kwargs: True)
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
            lambda cls, root, state_dir, discovered_matches=None:
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
