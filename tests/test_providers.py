"""The registry and the seam's contract, plus the Codex reader."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.__main__ import _display_state_for, initial_label, provider_tag_kind
from tokitty.accounts import Account, DEFAULT_PROVIDER, load_accounts_result, save_accounts
from tokitty.customize import Customization
from tokitty.display import format_observed_at, format_pane_label, resolve_status_text
from tokitty.poller import PollResult
from tokitty.providers import (
    DEFAULT_KIND,
    LedgerSource,
    NULL_PROVIDER,
    STATUS_UNSUPPORTED,
    UnknownProviderError,
    get_provider,
    supported_kinds,
)
from tokitty.providers import codex as codex_mod
from tokitty.providers.claude import ClaudeProvider
from tokitty.providers.codex import (
    CodexError,
    CodexProvider,
    kind_for_window,
    newest_snapshot,
    parse_token_count_event,
    read_latest_snapshot,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _event(timestamp, primary_pct, secondary_pct, reached=None, credits=None):
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 1}},
            "rate_limits": {
                "primary": {
                    "used_percent": primary_pct,
                    "window_minutes": 300,
                    "resets_at": int((NOW + timedelta(hours=2)).timestamp()),
                },
                "secondary": {
                    "used_percent": secondary_pct,
                    "window_minutes": 10080,
                    "resets_at": int((NOW + timedelta(days=3)).timestamp()),
                },
                "credits": credits or {"has_credits": False, "unlimited": False, "balance": "0"},
                "plan_type": "plus",
                "rate_limit_reached_type": reached,
            },
        },
    }


def _write_rollout(directory, name, events):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


# --- registry -------------------------------------------------------------


def test_registry_default_is_claude_and_matches_the_accounts_parser():
    assert get_provider().kind == "claude"
    assert get_provider(None).kind == "claude"
    assert DEFAULT_PROVIDER == DEFAULT_KIND


def test_registry_is_case_insensitive_and_trims():
    assert get_provider("  CODEX ").kind == "codex"


def test_unknown_kind_raises_rather_than_falling_back_to_claude():
    with pytest.raises(UnknownProviderError) as excinfo:
        get_provider("gemini")
    # The message has to name what IS supported: the whole point of
    # refusing is that the user can act on it.
    assert "claude" in str(excinfo.value) and "codex" in str(excinfo.value)


def test_every_registered_provider_satisfies_the_protocol():
    for kind in supported_kinds():
        provider = get_provider(kind)
        assert callable(provider.build_fetch_fn(None))
        assert provider.resolve_activity_sessions(None) is not None
        # None is a valid answer ("no ledger here"), e.g. Claude on a
        # Windows machine with no WSL install; it just must not raise.
        ledger = provider.resolve_ledger(None)
        assert ledger is None or isinstance(ledger, LedgerSource)
        assert provider.display_name


def test_null_provider_reports_unsupported_without_raising():
    result = NULL_PROVIDER.build_fetch_fn("/anywhere")()
    assert result.status == STATUS_UNSUPPORTED
    assert result.snapshot is None
    assert NULL_PROVIDER.resolve_ledger("/anywhere") is None


def test_capabilities_declare_what_each_harness_can_do():
    assert ClaudeProvider.capabilities.rate_limits
    assert ClaudeProvider.capabilities.activity
    # Codex has no hook mechanism, so no live poses -- a declared gap, not
    # a runtime failure.
    assert CodexProvider.capabilities.rate_limits
    assert not CodexProvider.capabilities.activity


# --- accounts.json migration ---------------------------------------------


def test_account_without_a_provider_key_reads_as_claude(tmp_path):
    (tmp_path / "accounts.json").write_text(
        json.dumps({"accounts": [{"name": "acct-v1-abc", "config_dir": "/home/u/.claude"}]}),
        encoding="utf-8",
    )
    result = load_accounts_result(tmp_path)
    assert result.accounts[0].provider == "claude"


def test_an_unknown_provider_string_survives_a_round_trip(tmp_path):
    """A file written by a newer build must not be silently rewritten to
    claude by an older one -- that would repoint the account at the wrong
    harness the next time the newer build reads it."""
    save_accounts(tmp_path, [Account(name="a", config_dir="/x", provider="gemini")])
    assert load_accounts_result(tmp_path).accounts[0].provider == "gemini"


def test_saving_writes_the_provider_key(tmp_path):
    save_accounts(tmp_path, [Account(name="a", config_dir="/x", provider="codex")])
    payload = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert payload["accounts"][0]["provider"] == "codex"


# --- codex reader ---------------------------------------------------------


def test_parses_a_real_shaped_token_count_event():
    snapshot = parse_token_count_event(_event("2026-09-22T12:00:00.000Z", 71.0, 74.0))
    assert snapshot.session_pct == 71.0
    assert snapshot.weekly_pct == 74.0
    assert snapshot.session_resets_at == NOW + timedelta(hours=2)
    assert {limit.kind for limit in snapshot.limits} == {"session", "weekly"}


def test_fetched_at_is_the_events_own_timestamp_not_the_read_time():
    """The numbers are only as fresh as the last Codex turn. Stamping them
    with the read time would make a week-old snapshot look live."""
    snapshot = parse_token_count_event(_event("2026-09-20T08:30:00.000Z", 10.0, 20.0))
    assert snapshot.fetched_at == datetime(2026, 9, 20, 8, 30, tzinfo=timezone.utc)


def test_the_blocked_window_is_marked_capped_so_the_cat_flops():
    snapshot = parse_token_count_event(_event("2026-09-22T12:00:00.000Z", 100.0, 40.0, reached="session"))
    session = next(limit for limit in snapshot.limits if limit.kind == "session")
    weekly = next(limit for limit in snapshot.limits if limit.kind == "weekly")
    assert session.severity == "exceeded"
    assert weekly.severity == "normal"


def test_an_event_without_rate_limits_is_skipped_not_fatal():
    event = _event("2026-09-22T12:00:00.000Z", 1.0, 2.0)
    del event["payload"]["rate_limits"]
    assert parse_token_count_event(event) is None


def test_window_kinds_come_from_the_declared_length_with_a_positional_fallback():
    assert kind_for_window(300, "weekly") == "session"
    assert kind_for_window(10080, "session") == "weekly"
    # An unseen window keeps its positional name rather than claiming to
    # be the 5-hour one.
    assert kind_for_window(43200, "weekly") == "weekly"
    assert kind_for_window(None, "session") == "session"


def test_freshest_event_wins_across_files_not_the_newest_filename(tmp_path):
    """A guardian sub-agent thread is written under the same account and is
    routinely the newest file on disk while carrying older numbers."""
    day = tmp_path / "sessions" / "2026" / "09" / "22"
    _write_rollout(day, "rollout-2026-09-22T09-00-00-main.jsonl",
                   [_event("2026-09-22T11:00:00.000Z", 71.0, 74.0)])
    _write_rollout(day, "rollout-2026-09-22T10-00-00-zzz-guardian.jsonl",
                   [_event("2026-09-22T09:00:00.000Z", 5.0, 5.0)])
    snapshot = newest_snapshot(codex_mod.recent_rollouts(str(tmp_path / "sessions"), NOW))
    assert snapshot.session_pct == 71.0


def test_the_last_token_count_in_a_file_wins():
    """Rollouts append, so an early event in the same file is superseded."""
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        day = pathlib.Path(tmp) / "sessions" / "2026" / "09" / "22"
        _write_rollout(day, "rollout-a.jsonl", [
            _event("2026-09-22T09:00:00.000Z", 10.0, 10.0),
            {"timestamp": "2026-09-22T09:30:00.000Z", "type": "response_item", "payload": {"type": "message"}},
            _event("2026-09-22T11:00:00.000Z", 71.0, 74.0),
        ])
        snapshot = read_latest_snapshot(tmp, now=NOW)
    assert snapshot.session_pct == 71.0


def test_a_missing_sessions_dir_is_a_message_not_a_crash(tmp_path):
    with pytest.raises(CodexError) as excinfo:
        read_latest_snapshot(str(tmp_path / "nope"), now=NOW)
    assert "sessions" in str(excinfo.value)


def test_rollouts_older_than_the_lookback_are_not_read(tmp_path):
    day = tmp_path / "sessions" / "2026" / "09" / "01"
    path = _write_rollout(day, "rollout-old.jsonl", [_event("2026-09-01T09:00:00.000Z", 71.0, 74.0)])
    import os
    stale = (NOW - timedelta(days=30)).timestamp()
    os.utime(path, (stale, stale))
    with pytest.raises(CodexError):
        read_latest_snapshot(str(tmp_path), now=NOW)


def test_a_corrupt_line_does_not_sink_the_whole_file(tmp_path):
    day = tmp_path / "sessions" / "2026" / "09" / "22"
    day.mkdir(parents=True)
    good = json.dumps(_event("2026-09-22T11:00:00.000Z", 71.0, 74.0))
    (day / "rollout-a.jsonl").write_text(
        '{"type":"token_count" truncated\n' + good + "\n", encoding="utf-8"
    )
    assert read_latest_snapshot(str(tmp_path), now=NOW).session_pct == 71.0


def test_the_fetch_fn_reports_a_readable_failure_rather_than_raising(tmp_path):
    result = CodexProvider().build_fetch_fn(str(tmp_path / "missing"))()
    assert result.status == "source_unreachable"
    assert result.snapshot is None
    assert result.message


# --- display: provider tag and staleness ----------------------------------



class _FakeProvider:
    def __init__(self, kind):
        self.kind = kind


def test_no_tag_when_every_pane_is_the_same_harness():
    """Naming the same harness on every pane is noise, and the label is
    often blank, so the tag has to earn its pixels."""
    claude = [_FakeProvider("claude"), _FakeProvider("claude")]
    assert provider_tag_kind(claude[0], claude) is None


def test_tag_appears_as_soon_as_two_harnesses_share_a_window():
    mixed = [_FakeProvider("claude"), _FakeProvider("codex")]
    assert provider_tag_kind(mixed[0], mixed) == "claude"
    assert provider_tag_kind(mixed[1], mixed) == "codex"


def test_label_joins_the_tag_and_survives_a_blank_name():
    assert format_pane_label("nibbles", "codex") == "codex·nibbles"
    assert format_pane_label("", "codex") == "codex"
    assert format_pane_label("mittens", None) == "mittens"


def test_initial_label_threads_the_tag_through():
    custom = Customization(colorway="ash", pattern=None, label="nibbles")
    assert initial_label(None, custom, "codex") == "codex·nibbles"
    assert initial_label(None, custom, None) == "nibbles"


def test_a_fresh_snapshot_says_nothing_about_its_age():
    now = datetime(2026, 9, 22, 16, 30, tzinfo=timezone.utc)
    assert format_observed_at(now - timedelta(minutes=2), now) is None


def test_an_old_snapshot_reports_when_it_was_taken():
    now = datetime(2026, 9, 22, 16, 30, tzinfo=timezone.utc)
    assert format_observed_at(now - timedelta(hours=2), now).startswith("as of ")


def test_a_snapshot_from_another_day_carries_its_weekday():
    now = datetime(2026, 9, 22, 16, 30, tzinfo=timezone.utc)
    text = format_observed_at(now - timedelta(days=2), now)
    # "as of Sun 10:30 AM" -- the weekday is what separates it from a
    # time earlier today, which reads identically without one.
    assert text.startswith("as of Sun ")


def test_a_future_timestamp_is_a_clock_disagreement_not_an_age():
    now = datetime(2026, 9, 22, 16, 30, tzinfo=timezone.utc)
    assert format_observed_at(now + timedelta(minutes=30), now) is None


def test_age_outranks_the_projection_it_would_otherwise_qualify():
    """A cap projection computed from a snapshot hours old is the most
    confidently wrong thing the pane can say."""
    assert resolve_status_text(None, "$1 / $2", "session caps ~6:20 PM", "as of 4:12 PM") == "as of 4:12 PM"
    assert resolve_status_text("hint", None, None, "as of 4:12 PM") == "hint"
    assert resolve_status_text(None, "$1 / $2", "proj", None) == "$1 / $2"


def test_a_stale_codex_snapshot_reaches_the_pane_with_its_age():
    now = datetime(2026, 9, 22, 16, 30, tzinfo=timezone.utc)
    snapshot = parse_token_count_event(_event("2026-09-22T12:00:00.000Z", 27.0, 83.0))
    result = PollResult(status="ok", snapshot=snapshot, message=None, fetched_at=now)
    display = _display_state_for(result, None, now=now)
    assert display["stale_text"].startswith("as of ")
    # The numbers themselves are still shown: they are the real ones, just
    # old, and blanking them would lose the only data Codex publishes.
    assert display["session_pct"] == 27.0
