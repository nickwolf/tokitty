"""Reads Claude Code's own transcripts (<config_dir>/projects/**/*.jsonl)
into per-model token and cost totals.

This is the data source that works with no subscription, no OAuth
credentials, and no network: every Claude Code install writes a full
billing ledger to disk regardless of how it is paid for.

Four properties of the real data drive almost every rule below. They were
measured against 5,817 assistant entries on 2026-09-08 (Claude Code
2.1.263) and are documented in full in
docs/superpowers/specs/2026-09-08-per-model-usage-design.md:

1. message.usage.iterations, when present, is authoritative -- top-level
   usage OMITS iterations whose type is "advisor_message", under-reporting
   one measured turn's input by 94%.
2. The same message.id is rewritten several times inside ONE transcript
   (4,037 of 5,817 entries). The last occurrence is the complete one.
3. usage.cache_creation splits 5m and 1h writes, which are priced
   differently (1.25x vs 2x input).
4. Only files whose mtime falls inside the retention window need opening.

Pure except for the file reads in TranscriptScanner. Everything that turns
bytes into numbers is a module-level function over an iterable, so the
tests are fixture files rather than mocks.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from tokitty.pricing import cost_usd, price_for

# Model ids the scanner synthesizes when the transcript does not name a
# model it can trust. Both are honest unknowns: they carry real tokens and
# no price, rather than attributing spend to a model that may be wrong.
ADVISOR_UNKNOWN = "<advisor-unknown>"
UNATTRIBUTED = "<unattributed>"

# Windows the UI can display. Retention always covers the widest reach of
# all of them -- see retention_start().
WINDOWS = ("24h", "7d", "month")
DEFAULT_WINDOW = "7d"

# Scan outcomes. "no usage in this window" is only ever shown for "ok":
# without this distinction, "the projects directory is unreachable" and
# "you genuinely have not used Claude Code" render identically, and the
# pane confidently reports zero when it actually failed to look.
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"

# Slack on the retention boundary, so a record that is inside a displayed
# window is never pruned by a scan that ran a moment earlier.
RETENTION_MARGIN = timedelta(hours=1)

# Bytes hashed immediately before a stored offset to detect that the
# prefix we already consumed has been rewritten underneath us.
TAIL_DIGEST_BYTES = 256

# The token classes we recognize. Reconciliation between iterations and
# top-level usage is per field (see _occurrence_records), so this tuple is
# the definition of "a field we know how to count".
_COUNTERS = ("input_tokens", "output_tokens", "cache_read_input_tokens")


@dataclass(frozen=True)
class BillingRecord:
    """One model's share of one API call. An assistant entry produces
    several of these when its turn made several calls."""

    model: str
    timestamp: datetime
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0


@dataclass(frozen=True)
class Occurrence:
    """One appearance of one message.id in one file, kept whole.

    The dedup unit is deliberately the whole occurrence rather than a
    single iteration. A corrected occurrence carrying one iteration
    replacing an earlier one carrying three must drop the other two; keyed
    per (message_id, iteration_index) they would be stranded and still
    charged.
    """

    key: str
    timestamp: datetime
    records: Tuple[BillingRecord, ...]


@dataclass(frozen=True)
class ModelUsage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    cost_usd: Optional[float]

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )


@dataclass(frozen=True)
class UsageBreakdown:
    status: str
    window: str
    window_start: datetime
    models: Tuple[ModelUsage, ...] = ()
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    unpriced_models: Tuple[str, ...] = ()
    failed_files: int = 0
    failed_rows: int = 0
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_partial_cost(self) -> bool:
        """True when total_cost_usd is a lower bound rather than a total,
        because at least one model on screen has no known price."""
        return bool(self.unpriced_models)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Same shape as api._parse_iso: naive input is assumed UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def window_start(window: str, now: datetime) -> datetime:
    """The inclusive lower bound of a display window.

    The month boundary is derived in LOCAL time, because that is how a
    calendar is read. Transcript timestamps are UTC; in UTC-6 a
    UTC-derived boundary would push the first six hours of every month
    into the previous one.
    """
    if window == "24h":
        return now - timedelta(hours=24)
    if window == "month":
        local_now = now.astimezone()
        first = local_now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return first.astimezone(timezone.utc)
    return now - timedelta(days=7)


def retention_start(now: datetime) -> datetime:
    """How far back the scanner keeps records, across every window the
    user could switch to without a rescan.

    Deliberately NOT "the calendar month, which is the longest window":
    early in a month the trailing 7-day window reaches further back than
    the 1st does, so assuming the month is widest would prune records the
    7d view still needs and silently undercount it.
    """
    return min(window_start(w, now) for w in WINDOWS) - RETENTION_MARGIN


def _cache_writes(usage: dict) -> Tuple[int, int]:
    """(5m, 1h) cache-creation tokens.

    Prefers the split object, since the two tiers are priced differently.
    Falls back to the flat cache_creation_input_tokens as 5m only when the
    split is absent -- charging an unknown-tier write at the cheaper rate
    understates rather than inventing a 2x charge.
    """
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        return (
            _as_int(creation.get("ephemeral_5m_input_tokens")),
            _as_int(creation.get("ephemeral_1h_input_tokens")),
        )
    return _as_int(usage.get("cache_creation_input_tokens")), 0


def _as_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


def _record_from_usage(model: str, usage: dict, timestamp: datetime) -> BillingRecord:
    write_5m, write_1h = _cache_writes(usage)
    return BillingRecord(
        model=model,
        timestamp=timestamp,
        input_tokens=_as_int(usage.get("input_tokens")),
        output_tokens=_as_int(usage.get("output_tokens")),
        cache_read_tokens=_as_int(usage.get("cache_read_input_tokens")),
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
    )


def _occurrence_records(
    entry: dict, message: dict, usage: dict, timestamp: datetime
) -> Tuple[BillingRecord, ...]:
    """Expand one assistant entry into per-model billing records.

    Uses iterations when present, because top-level usage omits
    advisor_message iterations entirely. Reconciliation with top level is
    then PER FIELD: a counter that appears in no iteration is real spend
    nobody accounted for and is carried as a correction record, while
    adding top level wholesale would double-count the fields the
    iterations already cover.
    """
    iterations = usage.get("iterations")
    model = message.get("model")
    if not isinstance(model, str) or not model:
        model = UNATTRIBUTED

    if not isinstance(iterations, list) or not iterations:
        return (_record_from_usage(model, usage, timestamp),)

    advisor_model = entry.get("advisorModel")
    records: List[BillingRecord] = []
    for iteration in iterations:
        if not isinstance(iteration, dict):
            continue
        if iteration.get("type") == "advisor_message":
            # Never fall back to message.model here. The advisor runs on a
            # model that is KNOWN to potentially differ, so guessing would
            # attribute its tokens to the wrong row; an honest unknown with
            # no price is worth more than a confident wrong number.
            iteration_model = (
                advisor_model
                if isinstance(advisor_model, str) and advisor_model
                else ADVISOR_UNKNOWN
            )
        else:
            iteration_model = model
        records.append(_record_from_usage(iteration_model, iteration, timestamp))

    if not records:
        return (_record_from_usage(model, usage, timestamp),)

    correction = _top_level_correction(records, usage, model, timestamp)
    if correction is not None:
        records.append(correction)
    return tuple(records)


def _top_level_correction(
    records: List[BillingRecord], usage: dict, model: str, timestamp: datetime
) -> Optional[BillingRecord]:
    """A record for counters that exist only at top level.

    Attributed to message.model when the occurrence names exactly one real
    model, and to <unattributed> when it spans several -- there is no way
    to know which of them the leftover belongs to.
    """
    summed = {
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
        "cache_read_input_tokens": sum(r.cache_read_tokens for r in records),
    }
    leftovers = {}
    for counter in _COUNTERS:
        top = _as_int(usage.get(counter))
        if top > summed[counter] and summed[counter] == 0:
            # Present at top level and in no iteration at all. A field the
            # iterations DO cover but disagree on is the advisor case, and
            # there the iteration sum is authoritative by construction.
            leftovers[counter] = top

    write_5m, write_1h = _cache_writes(usage)
    summed_5m = sum(r.cache_write_5m_tokens for r in records)
    summed_1h = sum(r.cache_write_1h_tokens for r in records)
    leftover_5m = write_5m if summed_5m == 0 and write_5m > 0 else 0
    leftover_1h = write_1h if summed_1h == 0 and write_1h > 0 else 0

    if not leftovers and not leftover_5m and not leftover_1h:
        return None

    distinct = {r.model for r in records if r.model not in (ADVISOR_UNKNOWN, UNATTRIBUTED)}
    target = model if len(distinct) <= 1 else UNATTRIBUTED
    return BillingRecord(
        model=target,
        timestamp=timestamp,
        input_tokens=leftovers.get("input_tokens", 0),
        output_tokens=leftovers.get("output_tokens", 0),
        cache_read_tokens=leftovers.get("cache_read_input_tokens", 0),
        cache_write_5m_tokens=leftover_5m,
        cache_write_1h_tokens=leftover_1h,
    )


def classify_line(line: str) -> Tuple[Optional[Occurrence], bool]:
    """(occurrence, failed) for one transcript line.

    The two Nones are NOT the same thing, and conflating them was a real
    bug: most lines in a transcript are user turns and other non-billing
    entries, so counting every skip as a failure pins a perfectly healthy
    install at "partial" forever (measured: 6,577 "failures" on a clean
    tree). `failed` is True only for a line that looked like a billing
    entry and could not be used, which is the signal that a schema change
    is quietly shrinking the totals.

    Skipped as normal: non-assistant entries, entries with no usage, and
    API-error entries. Kept: isSidechain, since subagent tokens are billed.
    """
    try:
        entry = json.loads(line)
    except (ValueError, TypeError):
        # A mid-write fragment reaches here, but so would a real entry the
        # parser cannot read. Both deserve to be visible.
        return None, True
    if not isinstance(entry, dict):
        return None, False
    if entry.get("type") != "assistant":
        return None, False
    if entry.get("isApiErrorMessage"):
        return None, False

    message = entry.get("message")
    if not isinstance(message, dict):
        return None, False
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None, False

    # Past this point the line IS a billing entry, so anything that stops
    # us from using it is a failure worth surfacing.
    key = message.get("id") or entry.get("requestId") or entry.get("uuid")
    if not isinstance(key, str) or not key:
        return None, True

    timestamp = _parse_iso(entry.get("timestamp"))
    if timestamp is None:
        # Better to drop the record than to count it into the wrong window.
        return None, True

    records = _occurrence_records(entry, message, usage, timestamp)
    if not records:
        return None, True
    return Occurrence(key=key, timestamp=timestamp, records=records), False


def parse_entry(line: str) -> Optional[Occurrence]:
    """One transcript line -> one Occurrence, or None to skip it."""
    return classify_line(line)[0]


def aggregate(
    occurrences: Iterable[Occurrence],
    window: str,
    now: datetime,
    status: str = STATUS_OK,
    failed_files: int = 0,
    failed_rows: int = 0,
) -> UsageBreakdown:
    """Fold retained occurrences into one window's breakdown.

    Pure and cheap: switching windows is another call to this function
    over the same retained set, with no I/O behind it.
    """
    start = window_start(window, now)
    totals: Dict[str, Dict[str, int]] = {}
    for occurrence in occurrences:
        for record in occurrence.records:
            if record.timestamp < start:
                continue
            bucket = totals.setdefault(
                record.model,
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_5m_tokens": 0,
                    "cache_write_1h_tokens": 0,
                },
            )
            bucket["input_tokens"] += record.input_tokens
            bucket["output_tokens"] += record.output_tokens
            bucket["cache_read_tokens"] += record.cache_read_tokens
            bucket["cache_write_5m_tokens"] += record.cache_write_5m_tokens
            bucket["cache_write_1h_tokens"] += record.cache_write_1h_tokens

    models: List[ModelUsage] = []
    for model, bucket in totals.items():
        price = price_for(model)
        models.append(
            ModelUsage(
                model=model,
                cost_usd=cost_usd(
                    price,
                    bucket["input_tokens"],
                    bucket["output_tokens"],
                    bucket["cache_read_tokens"],
                    bucket["cache_write_5m_tokens"],
                    bucket["cache_write_1h_tokens"],
                ),
                **bucket,
            )
        )

    # Priced models first by cost, then unpriced by tokens: an unpriced row
    # has no cost to sort on, and pushing it to the end keeps the dollar
    # ordering meaningful.
    models.sort(
        key=lambda m: (m.cost_usd is None, -(m.cost_usd or 0.0), -m.total_tokens)
    )
    return UsageBreakdown(
        status=status,
        window=window,
        window_start=start,
        models=tuple(models),
        total_cost_usd=sum(m.cost_usd for m in models if m.cost_usd is not None),
        total_tokens=sum(m.total_tokens for m in models),
        unpriced_models=tuple(m.model for m in models if m.cost_usd is None),
        failed_files=failed_files,
        failed_rows=failed_rows,
        scanned_at=now,
    )


@dataclass
class _Cursor:
    """What we must know to safely resume a file mid-way.

    (size, mtime, offset) alone is not enough and fails silently: a line
    rewritten in place at the same size, a truncate-and-regrow past the old
    size, a file swapped for a same-size same-mtime one, and WSL/Windows
    mtime granularity all let the scanner seek into unrelated content and
    publish a blend of two files.
    """

    file_id: object
    size: int
    mtime_ns: int
    # Always points just past the last COMPLETE newline. Claude Code
    # appends while we read, so a pass routinely ends mid-line; committing
    # past that fragment would lose the record forever, because the
    # remainder lands behind the new offset and is never revisited. The
    # fragment is simply left unconsumed and re-read next pass -- cheaper
    # and safer than buffering it, which double-counts it against the
    # bytes still sitting at that offset in the file.
    offset: int
    tail_digest: str


def _file_id(stat_result) -> object:
    """Identity that survives a rewrite. st_ino can be 0 over a
    \\\\wsl.localhost UNC stat, so fall back to the path in that case."""
    if getattr(stat_result, "st_ino", 0):
        return (stat_result.st_dev, stat_result.st_ino)
    return None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TranscriptScanner:
    """Incremental, per-account scan of one projects tree.

    The store is nested {path: {key: Occurrence}} rather than one flat map.
    That nesting is what lets a full re-read REMOVE records: replacing a
    path's whole inner map drops occurrences that compaction or deletion
    took away, which a flat map keyed only by message id cannot express.
    """

    def __init__(self, projects_dir, now_fn=None):
        self._projects_dir = Path(projects_dir) if projects_dir else None
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._cursors: Dict[Path, _Cursor] = {}
        self._store: Dict[Path, Dict[str, Occurrence]] = {}

    def scan(self) -> Tuple[str, int, int]:
        """One pass. Returns (status, failed_files, failed_rows)."""
        if self._projects_dir is None:
            return STATUS_UNAVAILABLE, 0, 0
        try:
            if not self._projects_dir.is_dir():
                return STATUS_UNAVAILABLE, 0, 0
            paths = sorted(self._projects_dir.glob("*/*.jsonl"))
        except OSError:
            return STATUS_UNAVAILABLE, 0, 0

        now = self._now_fn()
        keep_after = retention_start(now)
        failed_files = 0
        failed_rows = 0
        seen: set = set()

        for path in paths:
            seen.add(path)
            try:
                rows = self._scan_file(path, keep_after)
            except OSError:
                failed_files += 1
                continue
            failed_rows += rows

        for gone in set(self._store) - seen:
            self._store.pop(gone, None)
            self._cursors.pop(gone, None)

        self._prune(keep_after)

        if failed_files and failed_files == len(paths):
            return STATUS_UNAVAILABLE, failed_files, failed_rows
        if failed_files or failed_rows:
            return STATUS_PARTIAL, failed_files, failed_rows
        return STATUS_OK, failed_files, failed_rows

    def _scan_file(self, path: Path, keep_after: datetime) -> int:
        stat_result = path.stat()
        mtime = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)
        cursor = self._cursors.get(path)

        # An untouched-since-retention file can never contribute. This is
        # the check that keeps a cold scan cheap: 20 MB of recent
        # transcripts instead of the whole 134 MB tree.
        if mtime < keep_after and cursor is None:
            return 0

        resume = self._can_resume(path, cursor, stat_result)
        if not resume:
            self._store.pop(path, None)
            cursor = _Cursor(
                file_id=_file_id(stat_result),
                size=0,
                mtime_ns=0,
                offset=0,
                tail_digest=_digest(b""),
            )
            self._cursors[path] = cursor

        failed_rows = 0
        bucket = self._store.setdefault(path, {})
        with open(path, "rb") as handle:
            handle.seek(cursor.offset)
            chunk = handle.read()

        # Commit only through the last complete newline; leave the rest.
        cut = chunk.rfind(b"\n") + 1
        complete = chunk[:cut]

        for raw in complete.split(b"\n"):
            if not raw.strip():
                continue
            occurrence, failed = classify_line(raw.decode("utf-8", errors="replace"))
            if failed:
                failed_rows += 1
            if occurrence is None:
                continue
            # Last occurrence of a key wins, whole. Replacing the entry
            # rather than merging is what drops iterations a correction
            # removed.
            bucket[occurrence.key] = occurrence

        # If the file moved under us mid-read, throw the cursor away rather
        # than trust a torn read; the next pass re-reads from zero.
        after = path.stat()
        if after.st_size != stat_result.st_size or after.st_mtime_ns != stat_result.st_mtime_ns:
            self._cursors.pop(path, None)
            return failed_rows

        new_offset = cursor.offset + cut
        self._cursors[path] = _Cursor(
            file_id=_file_id(stat_result),
            size=stat_result.st_size,
            mtime_ns=stat_result.st_mtime_ns,
            offset=new_offset,
            tail_digest=self._tail_digest(path, new_offset),
        )
        return failed_rows

    def _can_resume(self, path: Path, cursor: Optional[_Cursor], stat_result) -> bool:
        if cursor is None:
            return False
        if _file_id(stat_result) != cursor.file_id:
            return False
        if stat_result.st_size < cursor.size:
            return False
        # Same size but a moved mtime means an in-place rewrite: the bytes
        # we already consumed are not the bytes that are there now.
        if stat_result.st_size == cursor.size and stat_result.st_mtime_ns != cursor.mtime_ns:
            return False
        return self._tail_digest(path, cursor.offset) == cursor.tail_digest

    def _tail_digest(self, path: Path, offset: int) -> str:
        """Hash of the bytes immediately before `offset`. A mismatch means
        the prefix we already consumed was rewritten."""
        if offset <= 0:
            return _digest(b"")
        start = max(offset - TAIL_DIGEST_BYTES, 0)
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                return _digest(handle.read(offset - start))
        except OSError:
            return ""

    def _prune(self, keep_after: datetime) -> None:
        """Drop records that fell out of retention.

        Runs AFTER replacement, never before: a corrected occurrence that
        moves out of the window has to remove the earlier retained version
        rather than leave it charged.
        """
        for bucket in self._store.values():
            for key in [k for k, occ in bucket.items() if occ.timestamp < keep_after]:
                del bucket[key]

    def occurrences(self) -> Iterator[Occurrence]:
        for bucket in self._store.values():
            yield from bucket.values()

    def breakdown(self, window: str, status: str, failed_files: int, failed_rows: int) -> UsageBreakdown:
        return aggregate(
            self.occurrences(),
            window,
            self._now_fn(),
            status=status,
            failed_files=failed_files,
            failed_rows=failed_rows,
        )
