import random
import threading
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
    assert display["hint_text"] == (
        "can't find credentials. Right-click ▸ Settings… ▸ Accounts "
        "to read usage from transcripts."
    )


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
    assert display["hint_text"].endswith("Right-click ▸ Settings… ▸ Accounts to choose.")


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


def test_main_dispatches_self_check(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.frozen.self_check", lambda: calls.append("self-check") or 0)

    assert main_module.main(["--self-check"]) == 0
    assert calls == ["self-check"]


def test_main_applies_the_ca_bundle_fallback_before_dispatching(monkeypatch):
    """The fallback has to run before any HTTPS request, so before every
    command main() dispatches, --self-check and --check-for-update included."""
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.tls.use_system_ca_bundle", lambda: calls.append("tls"))
    monkeypatch.setattr("tokitty.updater.check_for_update_cli", lambda: calls.append("check") or 0)

    assert main_module.main(["--check-for-update"]) == 0
    assert calls == ["tls", "check"]


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


def test_both_resolvers_share_one_credential_sweep(tmp_path, monkeypatch):
    # Issue #52: a launch with no accounts.json used to sweep every WSL
    # distro once per caller. Both resolvers now read the same cache, so
    # whichever runs first pays for the only sweep there is.
    from tokitty.wsl_probe import WslCredentialsCache

    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    # No native credentials, whatever this machine happens to have.
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path / "nohome"))
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
    monkeypatch.setattr("tokitty.qt.app.run_qt", lambda **kw: calls.append(kw) or 7)

    assert main_module.main(["--apply-update"]) == 7
    assert main_module.main(["--after-update", "tok-1_a"]) == 7
    assert calls == [
        {"after_update_token": None, "apply_update": True},
        {"after_update_token": "tok-1_a", "apply_update": False},
    ]


def test_main_runs_the_plain_gui_without_update_flags(monkeypatch):
    from tokitty import __main__ as main_module

    calls = []
    monkeypatch.setattr("tokitty.qt.app.run_qt", lambda **kw: calls.append(kw) or 0)

    assert main_module.main([]) == 0
    assert calls == [{"after_update_token": None, "apply_update": False}]


@pytest.mark.parametrize("argv", [["--after-update"], ["--after-update", ""], ["--after-update", "../x"]])
def test_main_rejects_a_missing_or_bad_update_token(monkeypatch, capsys, argv):
    from tokitty import __main__ as main_module

    monkeypatch.setattr("tokitty.qt.app.run_qt", lambda *a, **k: pytest.fail("must not start"))

    assert main_module.main(argv) == 2
    assert "--after-update needs a token" in capsys.readouterr().err


def test_projects_dir_uses_env_override_dir_without_sweeping(tmp_path, monkeypatch):
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    creds = tmp_path / "cfg" / ".credentials.json"
    monkeypatch.setenv("TOKITTY_CREDENTIALS", str(creds))
    monkeypatch.setattr(
        "tokitty.wsl_probe.list_wsl_distros",
        lambda *a, **k: pytest.fail("native credentials must not sweep WSL"),
    )
    projects_dir, distro = resolve_projects_dir(None, credentials=_NoSweepCache())
    assert projects_dir.replace("\\", "/") == str(tmp_path / "cfg" / "projects").replace("\\", "/")
    assert distro is None


def test_projects_dir_env_override_unc_keeps_distro(monkeypatch):
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    monkeypatch.setenv(
        "TOKITTY_CREDENTIALS",
        "\\\\wsl.localhost\\Ubuntu\\home\\n\\.claude-work\\.credentials.json",
    )
    projects_dir, distro = resolve_projects_dir(None, credentials=_NoSweepCache())
    assert distro == "Ubuntu"
    assert projects_dir.replace("\\", "/").endswith(".claude-work/projects")


def test_projects_dir_uses_home_relative_dir_without_sweeping(tmp_path, monkeypatch):
    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    projects_dir, distro = resolve_projects_dir(None, credentials=_NoSweepCache())
    assert projects_dir.replace("\\", "/") == str(home / ".claude" / "projects").replace("\\", "/")
    assert distro is None


class _NoSweepCache:
    def single(self):
        raise AssertionError("native credentials must not consult the WSL cache")

    def claude_dirs(self):
        raise AssertionError("native credentials must not consult the WSL cache")


def test_transcript_fallback_shares_one_sweep_with_discovery(tmp_path, monkeypatch):
    # Issue #87: with no native credentials and none in WSL, the ledger's
    # transcript fallback and run_discovery's sweep read the same cache.
    from tokitty.wsl_probe import WslCredentialsCache

    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path / "nohome"))
    sweeps = {"n": 0}

    def fake_dirs():
        sweeps["n"] += 1
        return [("Ubuntu", "/home/n/.claude")]

    cache = WslCredentialsCache(scan=lambda: [], claude_dirs_scan=fake_dirs)
    projects_dir, distro = resolve_projects_dir(None, credentials=cache)
    assert cache.claude_dirs() == [("Ubuntu", "/home/n/.claude")]  # the discovery thread's call
    assert sweeps["n"] == 1
    assert distro == "Ubuntu"
    assert projects_dir == "\\\\wsl.localhost\\Ubuntu\\home\\n\\.claude\\projects"


def test_projects_dir_ambiguous_credentials_skip_the_transcript_sweep(tmp_path, monkeypatch):
    from tokitty.credentials import AmbiguousCredentialsError

    monkeypatch.setattr("tokitty.__main__.sys.platform", "win32")
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path / "home"))

    class Ambiguous:
        def single(self):
            raise AmbiguousCredentialsError("two installs")

        def claude_dirs(self):
            raise AssertionError("ambiguous credentials must not sweep for transcripts")

    assert resolve_projects_dir(None, credentials=Ambiguous()) == (None, None)
