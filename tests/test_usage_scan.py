import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.usage_scan import (
    ADVISOR_UNKNOWN,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    UNATTRIBUTED,
    TranscriptScanner,
    aggregate,
    classify_line,
    parse_entry,
    retention_start,
    window_start,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def entry(
    *,
    model="claude-opus-5",
    msg_id="msg_1",
    ts=None,
    usage=None,
    iterations=None,
    advisor_model=None,
    entry_type="assistant",
    **extra,
):
    usage = dict(usage or {"input_tokens": 10, "output_tokens": 20})
    if iterations is not None:
        usage["iterations"] = iterations
    payload = {
        "type": entry_type,
        "timestamp": (ts or NOW).isoformat().replace("+00:00", "Z"),
        "requestId": "req_1",
        "uuid": "uuid-1",
        "message": {"id": msg_id, "model": model, "usage": usage},
    }
    if advisor_model is not None:
        payload["advisorModel"] = advisor_model
    payload.update(extra)
    return json.dumps(payload)


def write_transcript(root, name, lines):
    project = root / "-mnt-c-Tools"
    project.mkdir(parents=True, exist_ok=True)
    path = project / name
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


# --- parsing -----------------------------------------------------------


def test_top_level_usage_is_used_when_there_are_no_iterations():
    occurrence = parse_entry(entry(usage={"input_tokens": 5, "output_tokens": 7}))
    assert [(r.model, r.input_tokens, r.output_tokens) for r in occurrence.records] == [
        ("claude-opus-5", 5, 7)
    ]


def test_iterations_win_over_top_level_and_advisor_gets_its_own_model():
    """The measured shape: top-level omits the advisor_message iteration,
    under-reporting input by 54,066 tokens."""
    occurrence = parse_entry(
        entry(
            usage={"input_tokens": 3, "output_tokens": 961},
            advisor_model="claude-opus-5",
            model="claude-sonnet-5",
            iterations=[
                {"type": "message", "input_tokens": 1, "output_tokens": 545},
                {"type": "advisor_message", "input_tokens": 54066, "output_tokens": 7123},
                {"type": "message", "input_tokens": 2, "output_tokens": 416},
            ],
        )
    )
    by_model = {}
    for record in occurrence.records:
        by_model.setdefault(record.model, [0, 0])
        by_model[record.model][0] += record.input_tokens
        by_model[record.model][1] += record.output_tokens

    assert by_model["claude-sonnet-5"] == [3, 961]
    assert by_model["claude-opus-5"] == [54066, 7123]


def test_advisor_iteration_without_advisor_model_is_an_honest_unknown():
    """Falling back to message.model would attribute the advisor's tokens
    to a model known to potentially differ."""
    occurrence = parse_entry(
        entry(
            model="claude-sonnet-5",
            iterations=[
                {"type": "message", "input_tokens": 1},
                {"type": "advisor_message", "input_tokens": 999},
            ],
        )
    )
    advisor = [r for r in occurrence.records if r.model == ADVISOR_UNKNOWN]
    assert [r.input_tokens for r in advisor] == [999]


def test_counter_present_only_at_top_level_is_carried_as_a_correction():
    occurrence = parse_entry(
        entry(
            usage={"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 4242},
            iterations=[{"type": "message", "input_tokens": 5, "output_tokens": 6}],
        )
    )
    assert sum(r.cache_read_tokens for r in occurrence.records) == 4242
    # Single real model in the occurrence, so it is attributable.
    assert all(r.model == "claude-opus-5" for r in occurrence.records)


def test_top_level_only_counter_is_unattributed_when_the_turn_spans_models():
    occurrence = parse_entry(
        entry(
            model="claude-sonnet-5",
            advisor_model="claude-opus-5",
            usage={"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 100},
            iterations=[
                {"type": "message", "input_tokens": 1},
                {"type": "advisor_message", "input_tokens": 2},
            ],
        )
    )
    leftover = [r for r in occurrence.records if r.cache_read_tokens]
    assert [r.model for r in leftover] == [UNATTRIBUTED]


def test_cache_write_tiers_are_read_from_the_split():
    occurrence = parse_entry(
        entry(
            usage={
                "cache_creation_input_tokens": 34248,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 34248,
                },
            }
        )
    )
    record = occurrence.records[0]
    assert (record.cache_write_5m_tokens, record.cache_write_1h_tokens) == (0, 34248)


def test_flat_cache_creation_falls_back_to_the_cheaper_tier():
    occurrence = parse_entry(entry(usage={"cache_creation_input_tokens": 500}))
    record = occurrence.records[0]
    assert (record.cache_write_5m_tokens, record.cache_write_1h_tokens) == (500, 0)


def test_sidechain_entries_are_counted():
    occurrence = parse_entry(entry(isSidechain=True))
    assert occurrence is not None


def test_api_error_entries_are_skipped():
    assert parse_entry(entry(isApiErrorMessage=True)) is None


def test_non_assistant_and_corrupt_lines_are_skipped_without_raising():
    assert parse_entry(entry(entry_type="user")) is None
    assert parse_entry("{not json") is None
    assert parse_entry("null") is None


def test_unparseable_timestamp_drops_the_record():
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": "not-a-date",
            "message": {"id": "m", "model": "claude-opus-5", "usage": {"input_tokens": 1}},
        }
    )
    assert parse_entry(line) is None


# --- windows -----------------------------------------------------------


def test_window_boundaries():
    assert window_start("24h", NOW) == NOW - timedelta(hours=24)
    assert window_start("7d", NOW) == NOW - timedelta(days=7)


def test_month_boundary_is_local_not_utc():
    """In UTC-6, a UTC-derived boundary puts the first six hours of the
    month in the previous one.

    The zone is injected rather than set through TZ + time.tzset(), which
    does not exist on Windows and took CI red there.
    """
    minus_six = timezone(timedelta(hours=-6))
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    assert window_start("month", now, minus_six) == datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)


def test_month_boundary_east_of_greenwich_lands_before_the_utc_first():
    """The mirror case, so the test cannot pass by ignoring the zone."""
    plus_two = timezone(timedelta(hours=2))
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    assert window_start("month", now, plus_two) == datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)


def test_retention_early_in_a_month_reaches_past_the_first():
    """On the 6th, the trailing 7-day window is wider than the month."""
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    assert retention_start(now) <= now - timedelta(days=7)


def test_aggregate_excludes_records_outside_the_window():
    inside = parse_entry(entry(msg_id="a", ts=NOW - timedelta(hours=1)))
    outside = parse_entry(entry(msg_id="b", ts=NOW - timedelta(hours=25)))
    breakdown = aggregate([inside, outside], "24h", NOW)
    assert breakdown.total_tokens == 30


def test_unpriced_model_counts_tokens_and_is_excluded_from_the_dollar_total():
    known = parse_entry(entry(msg_id="a", model="claude-opus-5"))
    unknown = parse_entry(entry(msg_id="b", model="claude-opus-99"))
    breakdown = aggregate([known, unknown], "7d", NOW)
    assert breakdown.unpriced_models == ("claude-opus-99",)
    assert breakdown.is_partial_cost
    assert breakdown.total_tokens == 60
    assert breakdown.total_cost_usd == pytest.approx(
        (10 * 5.0 + 20 * 25.0) / 1_000_000
    )


def test_unpriced_models_sort_last():
    unknown = parse_entry(entry(msg_id="b", model="zzz", usage={"output_tokens": 10**9}))
    known = parse_entry(entry(msg_id="a", model="claude-opus-5"))
    breakdown = aggregate([unknown, known], "7d", NOW)
    assert breakdown.models[-1].model == "zzz"


# --- incremental scanning ---------------------------------------------


def test_missing_projects_dir_is_unavailable_not_empty(tmp_path):
    scanner = TranscriptScanner(tmp_path / "nope", now_fn=lambda: NOW)
    assert scanner.scan()[0] == STATUS_UNAVAILABLE


def test_none_projects_dir_is_unavailable():
    assert TranscriptScanner(None, now_fn=lambda: NOW).scan()[0] == STATUS_UNAVAILABLE


def test_clean_empty_window_is_ok(tmp_path):
    write_transcript(tmp_path, "a.jsonl", [])
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    status, _, _ = scanner.scan()
    assert status == STATUS_OK
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 0


def test_corrupt_row_makes_the_scan_partial(tmp_path):
    write_transcript(tmp_path, "a.jsonl", [entry(), "{broken"])
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    status, failed_files, failed_rows = scanner.scan()
    assert (status, failed_files, failed_rows) == (STATUS_PARTIAL, 0, 1)


def test_duplicate_message_id_resolves_last_wins(tmp_path):
    """The measured shape: line 29 output_tokens=1, line 30 = 167."""
    write_transcript(
        tmp_path,
        "a.jsonl",
        [
            entry(msg_id="dup", usage={"output_tokens": 1}),
            entry(msg_id="dup", usage={"output_tokens": 167}),
        ],
    )
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    status, _, _ = scanner.scan()
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 167


def test_correction_with_fewer_iterations_does_not_strand_the_others(tmp_path):
    """Keyed per (message_id, iteration_index) the removed iterations would
    survive and stay charged."""
    path = write_transcript(
        tmp_path,
        "a.jsonl",
        [
            entry(
                msg_id="dup",
                usage={"input_tokens": 0, "output_tokens": 0},
                iterations=[
                    {"type": "message", "output_tokens": 100},
                    {"type": "message", "output_tokens": 200},
                    {"type": "message", "output_tokens": 300},
                ],
            )
        ],
    )
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    scanner.scan()

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            entry(
                msg_id="dup",
                usage={"input_tokens": 0, "output_tokens": 0},
                iterations=[{"type": "message", "output_tokens": 7}],
            )
            + "\n"
        )
    status, _, _ = scanner.scan()
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 7


def test_append_resumes_from_the_stored_offset(tmp_path):
    path = write_transcript(tmp_path, "a.jsonl", [entry(msg_id="a")])
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    scanner.scan()
    first_offset = scanner._cursors[path].offset
    assert first_offset == path.stat().st_size

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(entry(msg_id="b") + "\n")
    scanner.scan()

    # Both records present, and the cursor advanced rather than restarting.
    assert scanner.breakdown("7d", STATUS_OK, 0, 0).total_tokens == 60
    assert scanner._cursors[path].offset == path.stat().st_size > first_offset


def test_half_written_trailing_line_is_recovered_on_the_next_pass(tmp_path):
    """Skipping the fragment AND committing past it loses the record
    forever, because the remainder lands behind the new offset."""
    complete = entry(msg_id="a", usage={"output_tokens": 5})
    tail = entry(msg_id="b", usage={"output_tokens": 500})
    path = write_transcript(tmp_path, "a.jsonl", [complete])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(tail[: len(tail) // 2])

    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    status, _, _ = scanner.scan()
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 5

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(tail[len(tail) // 2 :] + "\n")
    status, _, _ = scanner.scan()
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 505


def test_truncated_file_is_reread_from_zero(tmp_path):
    write_transcript(tmp_path, "a.jsonl", [entry(msg_id="a"), entry(msg_id="b")])
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    scanner.scan()
    assert scanner.breakdown("7d", STATUS_OK, 0, 0).total_tokens == 60

    write_transcript(tmp_path, "a.jsonl", [entry(msg_id="c", usage={"output_tokens": 1})])
    status, _, _ = scanner.scan()
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 1


def test_in_place_rewrite_at_the_same_size_is_reread(tmp_path):
    """(size, mtime, offset) alone would skip this and keep stale numbers."""
    path = write_transcript(tmp_path, "a.jsonl", [entry(msg_id="aaa", usage={"output_tokens": 11})])
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    scanner.scan()
    assert scanner.breakdown("7d", STATUS_OK, 0, 0).total_tokens == 11

    replacement = entry(msg_id="bbb", usage={"output_tokens": 11})
    assert len(replacement) == len(entry(msg_id="aaa", usage={"output_tokens": 11}))
    path.write_text(replacement + "\n", encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))

    status, _, _ = scanner.scan()
    breakdown = scanner.breakdown("7d", status, 0, 0)
    assert breakdown.total_tokens == 11
    assert len(breakdown.models) == 1


def test_deleted_file_drops_its_records(tmp_path):
    path = write_transcript(tmp_path, "a.jsonl", [entry(msg_id="a")])
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    scanner.scan()
    assert scanner.breakdown("7d", STATUS_OK, 0, 0).total_tokens == 30

    path.unlink()
    status, _, _ = scanner.scan()
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 0


def test_records_outside_retention_are_pruned(tmp_path):
    write_transcript(
        tmp_path,
        "a.jsonl",
        [entry(msg_id="old", ts=NOW - timedelta(days=90)), entry(msg_id="new")],
    )
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    status, _, _ = scanner.scan()
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 30


def test_dash_prefixed_project_directory_is_handled(tmp_path):
    """Project dirs are cwd-derived, so /mnt/c/Tools becomes "-mnt-c-Tools"."""
    write_transcript(tmp_path, "a.jsonl", [entry()])
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    status, _, _ = scanner.scan()
    assert status == STATUS_OK
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 30


def test_switching_windows_needs_no_rescan(tmp_path):
    write_transcript(
        tmp_path,
        "a.jsonl",
        [
            entry(msg_id="recent", ts=NOW - timedelta(hours=1)),
            entry(msg_id="older", ts=NOW - timedelta(days=3)),
        ],
    )
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    status, _, _ = scanner.scan()
    assert scanner.breakdown("24h", status, 0, 0).total_tokens == 30
    assert scanner.breakdown("7d", status, 0, 0).total_tokens == 60


def test_normal_non_billing_lines_are_not_failures(tmp_path):
    """Most lines in a transcript are user turns. Counting them as
    failures pinned a healthy tree at "partial" (6,577 of them on the
    reference machine)."""
    write_transcript(
        tmp_path,
        "a.jsonl",
        [entry(), entry(entry_type="user"), entry(isApiErrorMessage=True)],
    )
    scanner = TranscriptScanner(tmp_path, now_fn=lambda: NOW)
    assert scanner.scan() == (STATUS_OK, 0, 0)


def test_billing_entry_with_an_unusable_timestamp_is_a_failure():
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": "not-a-date",
            "message": {"id": "m", "model": "claude-opus-5", "usage": {"input_tokens": 1}},
        }
    )
    assert classify_line(line) == (None, True)


def test_snapshot_suffixed_model_from_real_data_is_priced():
    """haiku-4-5-20251001 appears verbatim in real transcripts."""
    occurrence = parse_entry(entry(model="claude-haiku-4-5-20251001"))
    breakdown = aggregate([occurrence], "7d", NOW)
    assert breakdown.unpriced_models == ()
    assert breakdown.total_cost_usd > 0
