"""Codex's per-model token ledger, read out of its rollout transcripts.

The output is the same UsageBreakdown of ModelUsage rows the Claude
scanner produces, so the Per-model view renders it unchanged. Everything
else differs, and the rules below come from measuring the real files
rather than from the format. The measurement is written up in
docs/superpowers/specs/2026-09-22-codex-token-ledger-design.md; the parts
that drive code here:

1. Every API call is reported twice, as a `token_count` event and as a
   `token_usage_record`. Only `token_usage_record` is read: it is the one
   carrying `turn_id` (the model join key) and `response_id` (the dedup
   key). Reading both doubles the bill.
2. `input_tokens` is a TOTAL that contains `cached_input_tokens` and
   `cache_write_input_tokens`, and `output_tokens` contains
   `reasoning_output_tokens`. The opposite of Anthropic's disjoint classes,
   so uncached input is the difference.
3. The record names no model. `turn_context` does, keyed by `turn_id`, and
   it can come AFTER the records it covers: a compaction record is written
   before the turn_context of the turn it opens. So the join is file-wide
   and resolved when the breakdown is built, never at read time, which
   also covers a turn_context that only arrives in a later incremental
   read. A record whose turn never gets one is an honest <unattributed>.
4. Rollouts live under sessions/YYYY/MM/DD/, and archiving a thread MOVES
   its file to archived_sessions/. That is real spend inside the window
   (2,538 records there on 2026-09-22, the newest a day old), so both
   trees are read.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from tokitty.pricing import LONG_CONTEXT_SUFFIX, long_context_threshold
from tokitty.usage_scan import (
    UNATTRIBUTED,
    BillingRecord,
    Occurrence,
    TranscriptScanner,
    _as_int,
    _parse_iso,
)

SESSIONS_SUBDIR = "sessions"
ARCHIVED_SUBDIR = "archived_sessions"

# Substring gates applied before any JSON parsing. Most rollout lines are
# message content; parsing them all to find the few that matter costs far
# more than scanning bytes for a marker. A content line that merely
# mentions a marker still parses and is then rejected on its real type.
_RECORD_MARKER = '"token_usage_record"'
_CONTEXT_MARKER = '"turn_context"'


@dataclass(frozen=True)
class _PendingRecord:
    """One token_usage_record, held until its model can be joined in.

    Stored where TranscriptScanner keeps Occurrences so the retention prune
    and the per-file replace-on-reread apply unchanged; `timestamp` is the
    only attribute those need.
    """

    key: str
    turn_id: Optional[str]
    timestamp: datetime
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int


def billing_model(model: str, input_tokens: int) -> str:
    """The id a request is priced under. A context-tiered model over the
    threshold gets its own id, so it lands on the long-context row where
    one is published and stays unpriced where one is not."""
    threshold = long_context_threshold(model)
    if threshold is not None and input_tokens > threshold:
        return model + LONG_CONTEXT_SUFFIX
    return model


def to_billing_record(pending: _PendingRecord, model: Optional[str]) -> BillingRecord:
    """Codex's nested classes mapped onto the scanner's disjoint ones.

    Uncached input is what is left of input_tokens once the cached and
    cache-write portions are taken out. Clamped at zero, because a record
    that claims more cached than total input is malformed, and a negative
    count would quietly subtract spend.
    """
    uncached = max(
        pending.input_tokens - pending.cached_input_tokens - pending.cache_write_input_tokens, 0
    )
    resolved = billing_model(model, pending.input_tokens) if model else UNATTRIBUTED
    return BillingRecord(
        model=resolved,
        timestamp=pending.timestamp,
        input_tokens=uncached,
        output_tokens=pending.output_tokens,
        cache_read_tokens=pending.cached_input_tokens,
        # OpenAI has a single cache-write rate; pricing.py carries it in
        # the 5m slot.
        cache_write_5m_tokens=pending.cache_write_input_tokens,
    )


def classify_rollout_line(line: str):
    """(kind, value, failed) for one rollout line.

    kind is "record" with a _PendingRecord, "context" with a
    (turn_id, model) pair, or None. `failed` has the same meaning as in
    usage_scan.classify_line: the line was a billing record we could not
    use, which is the signal that a format change is shrinking the totals.
    """
    has_record = _RECORD_MARKER in line
    if not has_record and _CONTEXT_MARKER not in line:
        return None, None, False
    try:
        entry = json.loads(line)
    except (ValueError, TypeError):
        # Only a line carrying a record marker counts: a torn line that
        # merely mentioned turn_context was never going to be billed.
        return None, None, has_record
    if not isinstance(entry, dict):
        return None, None, False
    payload = entry.get("payload")
    kind = entry.get("type")

    if kind == "turn_context":
        if not isinstance(payload, dict):
            return None, None, False
        turn_id = payload.get("turn_id")
        model = payload.get("model")
        if isinstance(turn_id, str) and turn_id and isinstance(model, str) and model:
            return "context", (turn_id, model), False
        return None, None, False

    if kind != "token_usage_record":
        return None, None, False

    # Past here the line IS a billing record.
    if not isinstance(payload, dict):
        return None, None, True
    usage = payload.get("usage")
    key = payload.get("response_id")
    timestamp = _parse_iso(entry.get("timestamp"))
    if not isinstance(usage, dict) or not isinstance(key, str) or not key or timestamp is None:
        return None, None, True
    turn_id = payload.get("turn_id")
    return (
        "record",
        _PendingRecord(
            key=key,
            turn_id=turn_id if isinstance(turn_id, str) and turn_id else None,
            timestamp=timestamp,
            input_tokens=_as_int(usage.get("input_tokens")),
            cached_input_tokens=_as_int(usage.get("cached_input_tokens")),
            cache_write_input_tokens=_as_int(usage.get("cache_write_input_tokens")),
            output_tokens=_as_int(usage.get("output_tokens")),
        ),
        False,
    )


class CodexLedgerScanner(TranscriptScanner):
    """TranscriptScanner's incremental offsets and tail digests, pointed at
    a Codex home instead of a Claude projects tree.

    `projects_dir` is the Codex home itself (the directory holding
    sessions/ and archived_sessions/). The per-file turn map lives beside
    the record store and is dropped with it whenever a file is re-read from
    zero, so a rewritten rollout cannot keep a stale join.
    """

    def __init__(self, codex_home, now_fn=None):
        super().__init__(codex_home, now_fn=now_fn)
        self._turns: Dict[Path, Dict[str, str]] = {}

    def _list_paths(self) -> List[Path]:
        home = self._projects_dir
        sessions = home / SESSIONS_SUBDIR
        if not sessions.is_dir():
            # A Codex home with no sessions tree has never run a turn, or
            # is not a Codex home. Either way there is nothing to claim
            # zero about.
            raise OSError(f"no Codex sessions directory at {sessions}")
        paths: List[Path] = []
        for root, _dirs, files in os.walk(sessions):
            paths.extend(Path(root) / name for name in files if name.endswith(".jsonl"))
        archived = home / ARCHIVED_SUBDIR
        if archived.is_dir():
            paths.extend(archived.glob("*.jsonl"))
        return sorted(paths)

    def _forget(self, path: Path) -> None:
        super()._forget(path)
        self._turns.pop(path, None)

    def _ingest(self, path: Path, line: str) -> bool:
        kind, value, failed = classify_rollout_line(line)
        if kind == "record":
            self._store.setdefault(path, {})[value.key] = value
        elif kind == "context":
            turn_id, model = value
            self._turns.setdefault(path, {})[turn_id] = model
        return failed

    def occurrences(self) -> Iterator[Occurrence]:
        """Resolve every retained record against its file's turn map.

        Deduped on response_id across files as well as within one. None
        repeat in the measured data, but an archive is a move, and a scan
        that lands mid-move would otherwise see the same file under both
        paths and charge it twice.
        """
        seen = set()
        for path, bucket in self._store.items():
            turns = self._turns.get(path, {})
            for pending in bucket.values():
                if pending.key in seen:
                    continue
                seen.add(pending.key)
                model = turns.get(pending.turn_id) if pending.turn_id else None
                yield Occurrence(
                    key=pending.key,
                    timestamp=pending.timestamp,
                    records=(to_billing_record(pending, model),),
                )
