import json
import sys
import time
from pathlib import Path

import pytest

import tokitty.credentials as credentials
from tokitty.credentials import (
    ENV_OVERRIDE,
    CredentialsError,
    LocalCredentialsSource,
    WslDistroCredentialsSource,
    describe_source,
    is_token_expired,
    load_credentials,
    resolve_credentials_source,
)


def test_resolve_prefers_override_when_set(tmp_path, monkeypatch):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text('{"claudeAiOauth": {}}', encoding="utf-8")
    monkeypatch.setenv(ENV_OVERRIDE, str(creds_file))

    source = resolve_credentials_source()

    assert isinstance(source, LocalCredentialsSource)
    assert source.path == creds_file


def test_resolve_override_raises_if_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDE, str(tmp_path / "missing.json"))

    with pytest.raises(CredentialsError):
        resolve_credentials_source()


def test_resolve_falls_back_to_home_relative_path(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text('{"claudeAiOauth": {}}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    source = resolve_credentials_source()

    assert isinstance(source, LocalCredentialsSource)
    assert source.path == claude_dir / ".credentials.json"


def test_resolve_raises_when_nothing_found_on_non_windows(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(CredentialsError):
        resolve_credentials_source()


def test_load_credentials_returns_oauth_dict(tmp_path):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "abc"}}), encoding="utf-8")

    result = load_credentials(LocalCredentialsSource(path=creds_file))

    assert result == {"accessToken": "abc"}


def test_load_credentials_raises_on_invalid_json(tmp_path):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text("not json", encoding="utf-8")

    with pytest.raises(CredentialsError):
        load_credentials(LocalCredentialsSource(path=creds_file))


def test_load_credentials_raises_when_oauth_key_missing(tmp_path):
    creds_file = tmp_path / "creds.json"
    creds_file.write_text(json.dumps({"somethingElse": {}}), encoding="utf-8")

    with pytest.raises(CredentialsError):
        load_credentials(LocalCredentialsSource(path=creds_file))


def test_describe_source_for_local():
    source = LocalCredentialsSource(path=Path("/tmp/x.json"))
    assert describe_source(source) == "/tmp/x.json"


def test_is_token_expired_true_when_past():
    now_ms = int(time.time() * 1000)
    assert is_token_expired({"expiresAt": now_ms - 1000}, now_ms=now_ms) is True


def test_is_token_expired_false_when_future():
    now_ms = int(time.time() * 1000)
    assert is_token_expired({"expiresAt": now_ms + 60_000}, now_ms=now_ms) is False


def test_is_token_expired_true_when_missing():
    assert is_token_expired({}) is True


def test_explicit_config_dir_posix(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDE, "/should/be/ignored")
    creds = tmp_path / ".credentials.json"
    creds.write_text("{}", encoding="utf-8")
    source = resolve_credentials_source(config_dir=str(tmp_path))
    assert isinstance(source, LocalCredentialsSource)
    assert source.path == creds


def test_explicit_config_dir_missing_file_raises(tmp_path):
    with pytest.raises(CredentialsError):
        resolve_credentials_source(config_dir=str(tmp_path / "nope"))


def test_explicit_config_dir_wsl_unc(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    source = resolve_credentials_source(config_dir="\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work")
    assert isinstance(source, WslDistroCredentialsSource)
    assert source.distro == "Ubuntu"
    assert source.wsl_path == "/home/u/.claude-work/.credentials.json"


def test_no_config_dir_keeps_v1_override_behavior(tmp_path, monkeypatch):
    creds = tmp_path / "c.json"
    creds.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(ENV_OVERRIDE, str(creds))
    source = resolve_credentials_source()
    assert isinstance(source, LocalCredentialsSource)
    assert source.path == creds
