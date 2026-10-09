"""Usage-limit alerts published for the Claude Code hook.

The widget already polls each Claude account's usage. This module turns a
snapshot into the list of limits that are at or over a threshold and writes
it to <claude config>/tokitty/usage.json, where hook_writer.py reads it and
tells running sessions. `alerts_for` is pure so other threshold consumers
can reuse it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

USAGE_FILENAME = "usage.json"
SCHEMA_VERSION = 1

# Last content written per tokitty dir by this process, so the 500 ms tick
# does not rewrite an unchanged file.
_last_written: Dict[str, str] = {}


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return _utc(moment).replace(microsecond=0).isoformat()


def window_key(kind: str, resets_at: Optional[datetime]) -> str:
    """Dedupe key for one limit window. resets_at is rounded to the nearest
    minute because the API's value jitters by fractions of a second between
    polls (20:00:00.906 vs 19:59:59.690) and both must be the same window."""
    if resets_at is None:
        return f"{kind}:unknown"
    rounded = (_utc(resets_at) + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return f"{kind}:{rounded.strftime('%Y-%m-%dT%H:%MZ')}"


def alerts_for(snapshot, session_pct_threshold: int, weekly_pct_threshold: int) -> List[dict]:
    """The limits in `snapshot` at or over their threshold, session first."""
    alerts: List[dict] = []
    for kind, pct, resets_at, threshold in (
        ("session", snapshot.session_pct, snapshot.session_resets_at, session_pct_threshold),
        ("weekly", snapshot.weekly_pct, snapshot.weekly_resets_at, weekly_pct_threshold),
    ):
        if pct is None or pct < threshold:
            continue
        alerts.append({
            "kind": kind,
            "pct": float(pct),
            "threshold": int(threshold),
            "resets_at": _iso(resets_at) if resets_at is not None else None,
            "window": window_key(kind, resets_at),
        })
    return alerts


def _path(tokitty_dir: str) -> str:
    return os.path.join(tokitty_dir, USAGE_FILENAME)


def write_usage_file(tokitty_dir: str, fetched_at: datetime, alerts: List[dict]) -> None:
    """Atomically write usage.json. Never raises: the dir may be a WSL UNC
    path that is briefly unavailable, and the next change retries."""
    payload = json.dumps({"v": SCHEMA_VERSION, "fetched_at": _iso(fetched_at), "alerts": alerts})
    if _last_written.get(tokitty_dir) == payload:
        return
    path = _path(tokitty_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return
    _last_written[tokitty_dir] = payload


def remove_usage_file(tokitty_dir: str) -> None:
    _last_written.pop(tokitty_dir, None)
    try:
        os.unlink(_path(tokitty_dir))
    except OSError:
        pass
