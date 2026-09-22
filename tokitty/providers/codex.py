"""The Codex provider: rate limits read off disk, with nothing to poll.

Codex writes a full `rate_limits` block into every `token_count` event of
every rollout transcript, so the numbers Claude Code makes us fetch over
OAuth are already sitting in `<codex-home>/sessions/YYYY/MM/DD/rollout-*.jsonl`.
That makes this provider a reader, not a client: no credentials, no
network, no token expiry.

Three properties of the real data drive the rules below. They were
measured against the local install on 2026-09-22 (Codex 0.155.0-alpha.9):

1. Sub-agent threads (guardian reviews, and anything else with a
   `thread_source`) get their own rollout files under the same account and
   carry their own `rate_limits`. The newest file by NAME is routinely one
   of those, so freshness has to be decided by the event timestamp across
   several recent files, never by filename or mtime alone.
2. `primary` is the 5-hour window and `secondary` the 7-day one, stated as
   `window_minutes` (300 and 10080) rather than by name. The mapping is
   read from that field rather than assumed, because the day Codex adds a
   third window an assumption here would silently mislabel it.
3. `resets_at` is a unix epoch in seconds, not the ISO-8601 string the
   Claude endpoint returns.

The freshness caveat this provider cannot fix: the numbers only advance
when Codex takes a turn, so between sessions the pane shows a snapshot of
some age. `UsageSnapshot.fetched_at` is therefore set to the event's own
timestamp, not to the moment we read the file -- the app must never be
told this data is newer than it is.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.poller import PollResult
from tokitty.providers.base import ProviderCapabilities, _config_root_from, _join, no_directory

CODEX_DIRNAME = ".codex"
SESSIONS_SUBDIR = "sessions"

# Only files touched inside this window are opened. A week covers the
# 7-day limit's own window, which is the longest-lived number on the card.
LOOKBACK = timedelta(days=8)

# How many of the most recently modified rollouts to actually read. The
# newest rate_limits block is in one of the last few files written, and
# a busy week leaves hundreds of them on disk.
MAX_FILES_READ = 12

# Cheap substring gate applied before any JSON parsing: most lines in a
# rollout are message content, and parsing all of them to find the few
# token_count events costs far more than scanning bytes for a marker.
_MARKER = '"token_count"'

# window_minutes -> the kind name mood.py's wake windows are keyed on.
_WINDOW_KINDS = {300: "session", 10080: "weekly"}


class CodexError(Exception):
    """Raised when the Codex home exists but nothing readable is in it."""


def default_codex_home() -> str:
    return str(Path.home() / CODEX_DIRNAME)


def sessions_dir_for(config_dir: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(sessions_dir, distro_name) for a Codex home, in the separator style
    the dir is already written in."""
    if not config_dir:
        return str(Path(default_codex_home()) / SESSIONS_SUBDIR), None
    root, distro = _config_root_from(config_dir)
    return _join(root, SESSIONS_SUBDIR), distro


def kind_for_window(window_minutes: Optional[int], fallback: str) -> str:
    """Name a limit from its own declared window length, falling back to
    the block's position (primary/secondary) when the length is one we
    have never seen. An unknown window is still worth showing; it just
    cannot claim to be the 5-hour one."""
    if window_minutes is None:
        return fallback
    return _WINDOW_KINDS.get(int(window_minutes), fallback)


def _epoch_to_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _limit_from_block(block, fallback_kind: str, reached: Optional[str]) -> Optional[LimitInfo]:
    if not isinstance(block, dict):
        return None
    percent = block.get("used_percent")
    if percent is None:
        return None
    kind = kind_for_window(block.get("window_minutes"), fallback_kind)
    # Codex states which window is blocking in one field on the parent
    # block rather than per-limit, so severity is derived: the named
    # window is blocked, every other one is normal. mood.is_capped also
    # treats >= 100% as capped, which covers the case where Codex reports
    # a full window without naming it.
    severity = "exceeded" if reached and str(reached) == fallback_kind else "normal"
    return LimitInfo(
        kind=kind,
        percent=float(percent),
        severity=severity,
        resets_at=_epoch_to_datetime(block.get("resets_at")),
        is_active=True,
        model_display_name=None,
    )


def parse_token_count_event(event: dict) -> Optional[UsageSnapshot]:
    """Turn one `token_count` event into a UsageSnapshot, or None if it
    carries no rate_limits (Codex emits token_count events without one).

    Every field is read defensively for the same reason api.parse_usage_response
    is: this is an internal transcript format with no compatibility promise.
    """
    payload = event.get("payload") if isinstance(event, dict) else None
    if not isinstance(payload, dict):
        return None
    limits_raw = payload.get("rate_limits")
    if not isinstance(limits_raw, dict):
        return None

    reached = limits_raw.get("rate_limit_reached_type")
    limits: List[LimitInfo] = []
    for fallback_kind, key in (("session", "primary"), ("weekly", "secondary")):
        limit = _limit_from_block(limits_raw.get(key), fallback_kind, reached)
        if limit is not None:
            limits.append(limit)
    if not limits:
        return None

    by_kind = {limit.kind: limit for limit in limits}
    session = by_kind.get("session")
    weekly = by_kind.get("weekly")

    credits_used = None
    credits_limit = None
    credits = limits_raw.get("credits")
    if isinstance(credits, dict) and not credits.get("unlimited"):
        try:
            # Codex reports the balance remaining as a decimal string;
            # Tokitty's card wants used-against-limit, so an unknown limit
            # stays None rather than being invented from the balance.
            credits_used = float(credits.get("balance"))
        except (TypeError, ValueError):
            credits_used = None

    return UsageSnapshot(
        session_pct=session.percent if session else 0.0,
        session_resets_at=session.resets_at if session else None,
        weekly_pct=weekly.percent if weekly else 0.0,
        weekly_resets_at=weekly.resets_at if weekly else None,
        limits=limits,
        credits_used=credits_used,
        credits_limit=credits_limit,
        fetched_at=_parse_iso(event.get("timestamp")) or datetime.now(timezone.utc),
    )


def latest_event_in_file(path) -> Optional[dict]:
    """The last `token_count` event in one rollout, read backwards.

    Rollouts run to hundreds of KB and the newest rate_limits block is
    always near the end, so lines are walked in reverse and the first
    match wins.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return None

    for line in reversed(lines):
        if _MARKER not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            return event
    return None


def recent_rollouts(sessions_dir, now: datetime, lookback: timedelta = LOOKBACK) -> List[str]:
    """The most recently modified rollout files inside the lookback window,
    newest first and capped at MAX_FILES_READ."""
    cutoff = (now - lookback).timestamp()
    found: List[Tuple[float, str]] = []
    try:
        for root, _dirs, files in os.walk(sessions_dir):
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                full = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                if mtime >= cutoff:
                    found.append((mtime, full))
    except OSError:
        return []
    found.sort(reverse=True)
    return [path for _mtime, path in found[:MAX_FILES_READ]]


def newest_snapshot(paths: Iterable[str]) -> Optional[UsageSnapshot]:
    """The freshest snapshot across several rollouts.

    Files are ranked by the event timestamp rather than by mtime or name
    because a sub-agent thread started later can finish -- and therefore be
    written -- with older rate-limit numbers than the main thread's.
    """
    best: Optional[UsageSnapshot] = None
    for path in paths:
        event = latest_event_in_file(path)
        if event is None:
            continue
        snapshot = parse_token_count_event(event)
        if snapshot is None:
            continue
        if best is None or snapshot.fetched_at > best.fetched_at:
            best = snapshot
    return best


def read_latest_snapshot(config_dir: Optional[str] = None, now: Optional[datetime] = None) -> UsageSnapshot:
    """The whole read, end to end. Raises CodexError with a message the
    pane can show rather than returning None, so every failure reaches the
    user as a sentence instead of a blank card."""
    now = now or datetime.now(timezone.utc)
    sessions_dir, _distro = sessions_dir_for(config_dir)
    if not sessions_dir or not os.path.isdir(sessions_dir):
        raise CodexError(f"no Codex sessions directory at {sessions_dir}")

    paths = recent_rollouts(sessions_dir, now)
    if not paths:
        raise CodexError("no Codex activity in the last 8 days")

    snapshot = newest_snapshot(paths)
    if snapshot is None:
        raise CodexError("Codex transcripts carry no rate-limit data")
    return snapshot


class CodexProvider:
    """Rate limits and a token ledger, both off disk. No activity: Codex
    has no hook equivalent to the one that drives the thinking and working
    poses, so those stay Claude-only until it does."""

    kind = "codex"
    display_name = "Codex"
    capabilities = ProviderCapabilities(rate_limits=True, token_ledger=True, activity=False)

    def build_fetch_fn(self, config_dir: Optional[str] = None, loader=None):
        def fetch() -> PollResult:
            now = datetime.now(timezone.utc)
            try:
                snapshot = read_latest_snapshot(config_dir, now=now)
            except CodexError as exc:
                return PollResult(
                    status="source_unreachable",
                    snapshot=None,
                    message=str(exc),
                    fetched_at=now,
                )
            sessions_dir, _distro = sessions_dir_for(config_dir)
            return PollResult(
                status="ok",
                snapshot=snapshot,
                message=None,
                fetched_at=now,
                source_description=sessions_dir,
            )

        return fetch

    def resolve_activity_sessions(self, config_dir: Optional[str] = None, credentials=None):
        return no_directory(config_dir, credentials)

    def resolve_projects_dir(self, config_dir: Optional[str] = None, credentials=None):
        # Codex's token ledger lives in the same rollouts as its rate
        # limits, not in a separate projects tree. Wiring that into a
        # scanner is its own change; until then the pane shows bars
        # without a cost readout rather than a wrong one.
        return no_directory(config_dir, credentials)
