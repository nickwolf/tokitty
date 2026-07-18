import json
from pathlib import Path

import pytest

from tokitty.accounts import Account, env_conflict_warning, load_accounts, parse_wsl_unc


def write_accounts(tmp_path: Path, payload) -> Path:
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    return p


def test_absent_file_returns_none(tmp_path):
    assert load_accounts(tmp_path) is None


def test_two_accounts_parsed_in_order(tmp_path):
    write_accounts(tmp_path, {"accounts": [
        {"name": "personal", "config_dir": "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude", "coat": "orange_tabby"},
        {"name": "work", "config_dir": "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work"},
    ]})
    accounts = load_accounts(tmp_path)
    assert accounts == [
        Account(name="personal", config_dir="\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude", coat="orange_tabby"),
        Account(name="work", config_dir="\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude-work", coat=None),
    ]


def test_missing_name_defaults_to_account_n(tmp_path):
    write_accounts(tmp_path, {"accounts": [{"config_dir": "/home/u/.claude"}]})
    accounts = load_accounts(tmp_path)
    assert accounts[0].name == "account 1"


def test_invalid_json_returns_none(tmp_path):
    write_accounts(tmp_path, "{not json")
    assert load_accounts(tmp_path) is None


def test_entries_without_config_dir_are_skipped(tmp_path):
    write_accounts(tmp_path, {"accounts": [{"name": "broken"}, {"config_dir": "/home/u/.claude"}]})
    accounts = load_accounts(tmp_path)
    assert len(accounts) == 1


def test_empty_accounts_list_returns_none(tmp_path):
    write_accounts(tmp_path, {"accounts": []})
    assert load_accounts(tmp_path) is None


def test_env_conflict_warning_fires_only_when_both_present(monkeypatch):
    accounts = [Account(name="a", config_dir="/x")]
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    assert env_conflict_warning(accounts) is None
    monkeypatch.setenv("TOKITTY_CREDENTIALS", "/some/path")
    warning = env_conflict_warning(accounts)
    assert "TOKITTY_CREDENTIALS" in warning and "accounts.json" in warning
    assert env_conflict_warning(None) is None  # env var alone, v1 mode: no warning


@pytest.mark.parametrize("unc,expected", [
    ("\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude", ("Ubuntu", "/home/u/.claude")),
    ("\\\\wsl$\\Debian\\home\\u\\.claude-work", ("Debian", "/home/u/.claude-work")),
    ("//wsl.localhost/Ubuntu/home/u/.claude", ("Ubuntu", "/home/u/.claude")),
])
def test_parse_wsl_unc_matches(unc, expected):
    assert parse_wsl_unc(unc) == expected


@pytest.mark.parametrize("not_unc", ["/home/u/.claude", "C:\\Users\\u\\.claude", ""])
def test_parse_wsl_unc_passthrough(not_unc):
    assert parse_wsl_unc(not_unc) is None
