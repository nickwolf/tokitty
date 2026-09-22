import json
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.pricing import LONG_CONTEXT_SUFFIX
from tokitty.providers import get_provider
from tokitty.providers.codex_ledger import CodexLedgerScanner, classify_rollout_line
from tokitty.usage_display import build_view
from tokitty.usage_scan import STATUS_OK, STATUS_PARTIAL, STATUS_UNAVAILABLE, UNATTRIBUTED
from tokitty.usage_watcher import UsageWatcher

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _ts(offset_minutes=0):
    return (NOW - timedelta(minutes=offset_minutes)).isoformat().replace("+00:00", "Z")


def turn_context(turn_id, model, minutes_ago=5):
    return json.dumps(
        {
            "timestamp": _ts(minutes_ago),
            "type": "turn_context",
            "payload": {"turn_id": turn_id, "model": model, "effort": "high"},
        }
    )


def usage_record(response_id, turn_id, input_tokens=1000, cached=0, output=100, reasoning=0, minutes_ago=5):
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output,
    }
    return json.dumps(
        {
            "timestamp": _ts(minutes_ago),
            "type": "token_usage_record",
            "payload": {
                "turn_id": turn_id,
                "response_id": response_id,
                "usage": usage,
                "turn_token_usage": usage,
                "thread_token_usage": usage,
            },
        }
    )


def token_count(input_tokens=1000, output=100, minutes_ago=5, running=None, cached=0):
    """The duplicate stream where records exist, and the only ledger in a
    file from a CLI that writes none. `running` is the thread's cumulative
    total_tokens, which a duplicate event repeats unchanged."""
    last = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": output,
        "reasoning_output_tokens": 0,
        "total_tokens": input_tokens + output,
    }
    total = {**last, "total_tokens": running if running is not None else input_tokens + output}
    return json.dumps(
        {
            "timestamp": _ts(minutes_ago),
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"total_token_usage": total, "last_token_usage": last}},
        }
    )


def rate_limits_only(minutes_ago=5):
    return json.dumps(
        {"timestamp": _ts(minutes_ago), "type": "event_msg", "payload": {"type": "token_count", "info": None, "rate_limits": {}}}
    )


def rollout(home, name, lines, archived=False):
    folder = home / "archived_sessions" if archived else home / "sessions" / "2026" / "09" / "22"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def scan(home, window="7d"):
    scanner = CodexLedgerScanner(home, now_fn=lambda: NOW)
    status, failed_files, failed_rows = scanner.scan()
    return scanner, scanner.breakdown(window, status, failed_files, failed_rows)


def by_model(breakdown):
    return {m.model: m for m in breakdown.models}


def test_token_count_events_are_not_counted_a_second_time(tmp_path):
    rollout(
        tmp_path,
        "rollout-a.jsonl",
        [turn_context("t1", "gpt-5.6-sol"), token_count(), usage_record("resp_1", "t1")],
    )
    _, breakdown = scan(tmp_path)
    assert breakdown.status == STATUS_OK
    assert breakdown.total_tokens == 1100


def test_cached_input_is_a_portion_of_input_not_an_extra_class(tmp_path):
    """input_tokens + output_tokens == total_tokens on every measured
    record, so the cached share is carved out of input, never added."""
    rollout(
        tmp_path,
        "rollout-a.jsonl",
        [
            turn_context("t1", "gpt-5.6-sol"),
            usage_record("resp_1", "t1", input_tokens=200_000, cached=180_000, output=50_000, reasoning=40_000),
        ],
    )
    _, breakdown = scan(tmp_path)
    row = by_model(breakdown)["gpt-5.6-sol"]
    assert row.input_tokens == 20_000
    assert row.cache_read_tokens == 180_000
    # Reasoning is inside output_tokens and is billed at the output rate.
    assert row.output_tokens == 50_000
    assert row.total_tokens == 250_000
    assert row.cost_usd == pytest.approx((20_000 * 4.0 + 180_000 * 0.4 + 50_000 * 20.0) / 1_000_000)


def test_a_record_written_before_its_turn_context_still_joins(tmp_path):
    """Measured: a compaction record lands before the turn_context of the
    turn it opens. An in-order join called 3 real records unattributed."""
    rollout(
        tmp_path,
        "rollout-a.jsonl",
        [usage_record("resp_1", "t1"), turn_context("t1", "gpt-5.6-luna")],
    )
    _, breakdown = scan(tmp_path)
    assert set(by_model(breakdown)) == {"gpt-5.6-luna"}


def test_a_turn_context_arriving_in_a_later_pass_joins(tmp_path):
    path = rollout(tmp_path, "rollout-a.jsonl", [usage_record("resp_1", "t1")])
    scanner, breakdown = scan(tmp_path)
    assert set(by_model(breakdown)) == {UNATTRIBUTED}

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(turn_context("t1", "gpt-5.6-sol") + "\n")
    status, failed_files, failed_rows = scanner.scan()
    breakdown = scanner.breakdown("7d", status, failed_files, failed_rows)
    assert set(by_model(breakdown)) == {"gpt-5.6-sol"}
    assert breakdown.total_tokens == 1100


def test_a_record_with_no_turn_context_is_an_honest_unknown(tmp_path):
    rollout(tmp_path, "rollout-a.jsonl", [usage_record("resp_1", "t-missing")])
    _, breakdown = scan(tmp_path)
    row = by_model(breakdown)[UNATTRIBUTED]
    assert row.total_tokens == 1100
    assert row.cost_usd is None
    assert breakdown.is_partial_cost


def test_codex_auto_review_keeps_its_tokens_and_has_no_price(tmp_path):
    rollout(
        tmp_path,
        "rollout-a.jsonl",
        [
            turn_context("t1", "codex-auto-review"),
            usage_record("resp_1", "t1"),
            turn_context("t2", "gpt-5.6-sol"),
            usage_record("resp_2", "t2"),
        ],
    )
    _, breakdown = scan(tmp_path)
    rows = by_model(breakdown)
    assert rows["codex-auto-review"].total_tokens == 1100
    assert rows["codex-auto-review"].cost_usd is None
    assert rows["gpt-5.6-sol"].cost_usd is not None

    view = build_view(breakdown)
    texts = {row.label: row.value_text for row in view.rows}
    assert texts["codex-auto-review"] == "--"
    assert view.status_text.startswith("7d · >= $")


def test_archived_rollouts_are_counted(tmp_path):
    rollout(tmp_path, "rollout-a.jsonl", [turn_context("t1", "gpt-5.6-sol"), usage_record("resp_1", "t1")])
    rollout(
        tmp_path,
        "rollout-b.jsonl",
        [turn_context("t2", "gpt-5.6-sol"), usage_record("resp_2", "t2")],
        archived=True,
    )
    _, breakdown = scan(tmp_path)
    assert breakdown.total_tokens == 2200


def test_a_file_caught_mid_archive_is_not_charged_twice(tmp_path):
    lines = [turn_context("t1", "gpt-5.6-sol"), usage_record("resp_1", "t1")]
    rollout(tmp_path, "rollout-a.jsonl", lines)
    rollout(tmp_path, "rollout-a.jsonl", lines, archived=True)
    _, breakdown = scan(tmp_path)
    assert breakdown.total_tokens == 1100


def test_long_context_request_lands_on_the_long_context_row(tmp_path):
    rollout(
        tmp_path,
        "rollout-a.jsonl",
        [
            turn_context("t1", "gpt-5.6-sol"),
            usage_record("resp_1", "t1", input_tokens=300_000, output=0),
            turn_context("t2", "gpt-5.5"),
            usage_record("resp_2", "t2", input_tokens=300_000, output=0),
        ],
    )
    _, breakdown = scan(tmp_path)
    rows = by_model(breakdown)
    assert rows["gpt-5.6-sol" + LONG_CONTEXT_SUFFIX].cost_usd == pytest.approx(300_000 * 8.0 / 1_000_000)
    # No long-context rate is published for gpt-5.5: unpriced, not charged
    # the short-context rate.
    assert rows["gpt-5.5" + LONG_CONTEXT_SUFFIX].cost_usd is None


def test_records_outside_the_window_are_excluded(tmp_path):
    rollout(
        tmp_path,
        "rollout-a.jsonl",
        [
            turn_context("t1", "gpt-5.6-sol"),
            usage_record("resp_old", "t1", minutes_ago=60 * 30),
            usage_record("resp_new", "t1"),
        ],
    )
    _, breakdown = scan(tmp_path, window="24h")
    assert breakdown.total_tokens == 1100


def test_no_sessions_directory_is_unavailable_not_zero(tmp_path):
    _, breakdown = scan(tmp_path)
    assert breakdown.status == STATUS_UNAVAILABLE


def test_an_unusable_record_marks_the_scan_partial(tmp_path):
    broken = json.dumps({"timestamp": _ts(), "type": "token_usage_record", "payload": {"turn_id": "t1"}})
    rollout(tmp_path, "rollout-a.jsonl", [turn_context("t1", "gpt-5.6-sol"), broken, usage_record("resp_1", "t1")])
    _, breakdown = scan(tmp_path)
    assert breakdown.status == STATUS_PARTIAL
    assert breakdown.failed_rows == 1
    assert breakdown.total_tokens == 1100


def test_message_text_that_mentions_a_marker_is_not_a_failure():
    """Rollouts about Codex quote its own event names. That is content."""
    line = json.dumps({"type": "response_item", "payload": {"text": 'look for "token_usage_record" lines'}})
    assert classify_rollout_line(line) == (None, None, False)


def test_a_rewritten_rollout_drops_its_old_turn_map(tmp_path):
    rollout(tmp_path, "rollout-a.jsonl", [turn_context("t1", "gpt-5.6-sol"), usage_record("resp_1", "t1")])
    scanner, _ = scan(tmp_path)
    # Shorter than before, so the cursor cannot resume.
    rollout(tmp_path, "rollout-a.jsonl", [usage_record("resp_1", "t1")])
    status, failed_files, failed_rows = scanner.scan()
    breakdown = scanner.breakdown("7d", status, failed_files, failed_rows)
    assert set(by_model(breakdown)) == {UNATTRIBUTED}


def test_codex_provider_resolves_a_ledger_on_the_codex_home(tmp_path):
    rollout(tmp_path, "rollout-a.jsonl", [turn_context("t1", "gpt-5.6-sol"), usage_record("resp_1", "t1")])
    ledger = get_provider("codex").resolve_ledger(str(tmp_path))
    assert ledger.root == str(tmp_path)
    assert ledger.distro_name is None

    watcher = UsageWatcher(
        ledger.root,
        distro_name=ledger.distro_name,
        now_fn=lambda: NOW,
        scanner_factory=ledger.make_scanner,
    )
    watcher._tick_once()
    latest = watcher.get_latest()
    assert latest.status == STATUS_OK
    assert by_model(latest)["gpt-5.6-sol"].total_tokens == 1100


def test_a_file_with_no_records_is_billed_from_token_count(tmp_path):
    """Codex CLI 0.147.0 writes no token_usage_record at all. Reading only
    records dropped 33.3M tokens across 6 recent files, reported as ok."""
    rollout(
        tmp_path,
        "rollout-old.jsonl",
        [
            rate_limits_only(),
            turn_context("t1", "gpt-5.6-sol"),
            token_count(input_tokens=1000, output=100, running=1100),
            # A duplicate event repeats the running total unchanged.
            token_count(input_tokens=1000, output=100, running=1100),
            turn_context("t2", "gpt-5.6-luna"),
            token_count(input_tokens=2000, output=200, running=3300),
        ],
    )
    _, breakdown = scan(tmp_path)
    assert breakdown.status == STATUS_OK
    rows = by_model(breakdown)
    assert rows["gpt-5.6-sol"].total_tokens == 1100
    assert rows["gpt-5.6-luna"].total_tokens == 2200
    assert breakdown.total_tokens == 3300


def test_token_count_before_any_turn_context_takes_the_first_one(tmp_path):
    rollout(tmp_path, "rollout-old.jsonl", [token_count(running=1100), turn_context("t1", "gpt-5.6-sol")])
    _, breakdown = scan(tmp_path)
    assert set(by_model(breakdown)) == {"gpt-5.6-sol"}


def test_token_count_fallback_survives_an_archive_move(tmp_path):
    lines = [turn_context("t1", "gpt-5.6-sol"), token_count(running=1100)]
    rollout(tmp_path, "rollout-old.jsonl", lines)
    rollout(tmp_path, "rollout-old.jsonl", lines, archived=True)
    _, breakdown = scan(tmp_path)
    assert breakdown.total_tokens == 1100


def test_token_count_fallback_reads_incrementally(tmp_path):
    path = rollout(tmp_path, "rollout-old.jsonl", [turn_context("t1", "gpt-5.6-sol"), token_count(running=1100)])
    scanner, _ = scan(tmp_path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(turn_context("t2", "gpt-5.6-luna") + "\n")
        handle.write(token_count(input_tokens=2000, output=200, running=3300) + "\n")
    status, failed_files, failed_rows = scanner.scan()
    rows = by_model(scanner.breakdown("7d", status, failed_files, failed_rows))
    assert rows["gpt-5.6-sol"].total_tokens == 1100
    assert rows["gpt-5.6-luna"].total_tokens == 2200


def test_a_dated_model_id_still_gets_the_long_context_row(tmp_path):
    rollout(
        tmp_path,
        "rollout-a.jsonl",
        [turn_context("t1", "gpt-5.6-sol-20260901"), usage_record("resp_1", "t1", input_tokens=300_000, output=0)],
    )
    _, breakdown = scan(tmp_path)
    row = by_model(breakdown)["gpt-5.6-sol-20260901" + LONG_CONTEXT_SUFFIX]
    assert row.cost_usd == pytest.approx(300_000 * 8.0 / 1_000_000)
