"""Client for Claude Code's usage endpoint."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class LimitInfo:
    kind: str
    percent: float
    severity: str
    resets_at: Optional[datetime]
    is_active: bool
    model_display_name: Optional[str] = None


@dataclass(frozen=True)
class UsageSnapshot:
    session_pct: float
    session_resets_at: Optional[datetime]
    weekly_pct: float
    weekly_resets_at: Optional[datetime]
    limits: List[LimitInfo] = field(default_factory=list)
    credits_used: Optional[float] = None
    credits_limit: Optional[float] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def parse_usage_response(raw: dict) -> UsageSnapshot:
    """Defensively parse the usage endpoint's response into a UsageSnapshot.

    Every field is read with .get() and a safe default -- this is an
    undocumented endpoint that can add, remove, or rename fields at any
    time.
    """
    five_hour = raw.get("five_hour") or {}
    seven_day = raw.get("seven_day") or {}

    limits = []
    for entry in raw.get("limits") or []:
        if not isinstance(entry, dict):
            continue
        scope = entry.get("scope") or {}
        model = scope.get("model") or {}
        limits.append(
            LimitInfo(
                kind=entry.get("kind", "unknown"),
                percent=float(entry.get("percent") or 0.0),
                severity=str(entry.get("severity") or "normal"),
                resets_at=_parse_iso(entry.get("resets_at")),
                is_active=bool(entry.get("is_active", False)),
                model_display_name=model.get("display_name"),
            )
        )

    spend = raw.get("spend") or {}
    spend_used = spend.get("used") or {}
    spend_limit = spend.get("limit") or {}
    exponent = spend_used.get("exponent", spend_limit.get("exponent", 2))

    credits_used = None
    credits_limit = None
    used_minor = spend_used.get("amount_minor")
    limit_minor = spend_limit.get("amount_minor")
    if used_minor is not None:
        credits_used = used_minor / (10 ** exponent)
    if limit_minor is not None:
        credits_limit = limit_minor / (10 ** exponent)

    return UsageSnapshot(
        session_pct=float(five_hour.get("utilization") or 0.0),
        session_resets_at=_parse_iso(five_hour.get("resets_at")),
        weekly_pct=float(seven_day.get("utilization") or 0.0),
        weekly_resets_at=_parse_iso(seven_day.get("resets_at")),
        limits=limits,
        credits_used=credits_used,
        credits_limit=credits_limit,
    )
