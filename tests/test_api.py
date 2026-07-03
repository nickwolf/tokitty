import pytest

from tokitty.api import parse_usage_response

# Trimmed from a real captured response (2026-07-02).
FIXTURE = {
    "five_hour": {
        "utilization": 66.0,
        "resets_at": "2026-07-03T07:29:59.950281+00:00",
    },
    "seven_day": {
        "utilization": 32.0,
        "resets_at": "2026-07-06T23:59:59.950314+00:00",
    },
    "limits": [
        {
            "kind": "session",
            "percent": 66,
            "severity": "normal",
            "resets_at": "2026-07-03T07:29:59.950281+00:00",
            "scope": None,
            "is_active": True,
        },
        {
            "kind": "weekly_all",
            "percent": 32,
            "severity": "normal",
            "resets_at": "2026-07-06T23:59:59.950314+00:00",
            "scope": None,
            "is_active": False,
        },
        {
            "kind": "weekly_scoped",
            "percent": 33,
            "severity": "normal",
            "resets_at": "2026-07-06T23:59:59.950584+00:00",
            "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
            "is_active": False,
        },
    ],
    "spend": {
        "used": {"amount_minor": 362, "currency": "USD", "exponent": 2},
        "limit": {"amount_minor": 2000, "currency": "USD", "exponent": 2},
        "percent": 18,
        "enabled": True,
    },
}


def test_parse_usage_response_extracts_session_and_weekly_percent():
    snapshot = parse_usage_response(FIXTURE)

    assert snapshot.session_pct == 66.0
    assert snapshot.weekly_pct == 32.0


def test_parse_usage_response_converts_reset_times_to_aware_datetimes():
    snapshot = parse_usage_response(FIXTURE)

    assert snapshot.session_resets_at.tzinfo is not None
    assert snapshot.session_resets_at.year == 2026
    assert snapshot.session_resets_at.month == 7
    assert snapshot.session_resets_at.day == 3


def test_parse_usage_response_extracts_limits():
    snapshot = parse_usage_response(FIXTURE)

    assert len(snapshot.limits) == 3
    scoped = [l for l in snapshot.limits if l.kind == "weekly_scoped"][0]
    assert scoped.model_display_name == "Fable"
    assert scoped.percent == 33.0


def test_parse_usage_response_converts_credits_from_minor_units():
    snapshot = parse_usage_response(FIXTURE)

    assert snapshot.credits_used == pytest.approx(3.62)
    assert snapshot.credits_limit == pytest.approx(20.00)


def test_parse_usage_response_handles_missing_fields_gracefully():
    snapshot = parse_usage_response({})

    assert snapshot.session_pct == 0.0
    assert snapshot.weekly_pct == 0.0
    assert snapshot.limits == []
    assert snapshot.credits_used is None


def test_parse_usage_response_ignores_malformed_limit_entries():
    raw = {"limits": ["not-a-dict", {"kind": "session", "percent": 10}]}

    snapshot = parse_usage_response(raw)

    assert len(snapshot.limits) == 1
    assert snapshot.limits[0].kind == "session"
