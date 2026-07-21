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
    scoped = [lim for lim in snapshot.limits if lim.kind == "weekly_scoped"][0]
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


def test_fetch_usage_sends_bearer_token_and_beta_header():
    from unittest.mock import MagicMock, patch

    from tokitty.api import BASE_URL, fetch_usage

    fake_response = MagicMock()
    fake_response.read.return_value = b'{"ok": true}'
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with patch("tokitty.api.urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        result = fetch_usage("test-token-123")

    assert result == {"ok": True}
    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url == BASE_URL
    assert sent_request.get_header("Authorization") == "Bearer test-token-123"
    assert sent_request.get_header("Anthropic-beta") == "oauth-2025-04-20"


def test_fetch_usage_raises_api_error_with_status_code_on_http_error():
    import urllib.error
    from unittest.mock import patch

    from tokitty.api import BASE_URL, ApiError, fetch_usage

    http_error = urllib.error.HTTPError(BASE_URL, 401, "Unauthorized", {}, None)

    with patch("tokitty.api.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(ApiError) as exc_info:
            fetch_usage("expired-token")

    assert exc_info.value.status_code == 401


def test_fetch_usage_raises_api_error_on_network_error():
    import urllib.error
    from unittest.mock import patch

    from tokitty.api import ApiError, fetch_usage

    with patch("tokitty.api.urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        with pytest.raises(ApiError) as exc_info:
            fetch_usage("some-token")

    assert exc_info.value.status_code is None


def test_fetch_usage_raises_api_error_on_invalid_json():
    from unittest.mock import MagicMock, patch

    from tokitty.api import ApiError, fetch_usage

    fake_response = MagicMock()
    fake_response.read.return_value = b"not json"
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with patch("tokitty.api.urllib.request.urlopen", return_value=fake_response):
        with pytest.raises(ApiError):
            fetch_usage("some-token")
