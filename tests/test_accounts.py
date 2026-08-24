import hashlib
import json
import os
from pathlib import Path

import pytest

from tokitty.accounts import (
    Account,
    AccountsLoadResult,
    assign_identity_slug,
    canonicalize_locator,
    env_conflict_warning,
    load_accounts,
    load_accounts_result,
    load_identity_history,
    parse_wsl_unc,
    save_accounts,
    save_identity_history,
)


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


def test_load_accounts_result_absent(tmp_path):
    result = load_accounts_result(tmp_path)
    assert result.state == "absent"
    assert result.accounts == []


def test_load_accounts_result_malformed_json(tmp_path):
    (tmp_path / "accounts.json").write_text("{not json", encoding="utf-8")
    result = load_accounts_result(tmp_path)
    assert result.state == "malformed"
    assert result.accounts == []


def test_load_accounts_result_accounts_not_a_list(tmp_path):
    write_accounts(tmp_path, {"accounts": "nope"})
    result = load_accounts_result(tmp_path)
    assert result.state == "malformed"


def test_load_accounts_result_valid_but_empty(tmp_path):
    write_accounts(tmp_path, {"accounts": [{"name": "x"}]})  # no config_dir
    result = load_accounts_result(tmp_path)
    assert result.state == "valid_empty"
    assert result.accounts == []


def test_load_accounts_result_valid_non_empty_three_accounts(tmp_path):
    write_accounts(tmp_path, {"accounts": [
        {"name": "a", "config_dir": "/home/u/.claude-a"},
        {"name": "b", "config_dir": "/home/u/.claude-b"},
        {"name": "c", "config_dir": "/home/u/.claude-c"},
    ]})
    result = load_accounts_result(tmp_path)
    assert result.state == "valid_non_empty"
    assert [a.name for a in result.accounts] == ["a", "b", "c"]


def test_save_accounts_round_trip_n1(tmp_path):
    accounts = [Account(name="solo", config_dir="/home/u/.claude")]
    save_accounts(tmp_path, accounts)
    assert load_accounts(tmp_path) == accounts


def test_save_accounts_round_trip_n3(tmp_path):
    accounts = [
        Account(name="a", config_dir="/home/u/.claude-a"),
        Account(name="b", config_dir="/home/u/.claude-b"),
        Account(name="c", config_dir="/home/u/.claude-c"),
    ]
    save_accounts(tmp_path, accounts)
    assert load_accounts(tmp_path) == accounts


def test_save_accounts_round_trip_n5(tmp_path):
    accounts = [Account(name=f"acct{i}", config_dir=f"/home/u/.claude-{i}") for i in range(5)]
    save_accounts(tmp_path, accounts)
    assert load_accounts(tmp_path) == accounts


def test_save_accounts_never_writes_coat_key(tmp_path):
    save_accounts(tmp_path, [Account(name="a", config_dir="/home/u/.claude", coat="orange_tabby")])
    raw = (tmp_path / "accounts.json").read_text(encoding="utf-8")
    assert "coat" not in raw


def test_save_accounts_uses_tmp_file_and_replace(tmp_path, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("tokitty.accounts.os.replace", spy_replace)
    save_accounts(tmp_path, [Account(name="a", config_dir="/home/u/.claude")])
    assert len(calls) == 1
    assert calls[0][0].endswith("accounts.json.tmp")
    assert calls[0][1].endswith("accounts.json")


def test_canonicalize_locator_wsl_aliases_match():
    a = canonicalize_locator("\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude")
    b = canonicalize_locator("\\\\wsl$\\Ubuntu\\home\\nick\\.claude")
    assert a == b == "wsl:ubuntu:/home/nick/.claude"


def test_canonicalize_locator_windows_local():
    assert canonicalize_locator("C:\\Users\\nick\\.claude") == "c:\\users\\nick\\.claude"


def test_canonicalize_locator_posix(tmp_path):
    real = tmp_path / ".claude"
    real.mkdir()
    assert canonicalize_locator(str(real)) == str(real.resolve())


def test_canonicalize_locator_rejects_relative_path():
    for bad in ("relative/.claude", ".claude", "..\\relative"):
        try:
            canonicalize_locator(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_assign_identity_slug_deterministic_full_sha256():
    locator = "wsl:ubuntu:/home/nick/.claude"
    slug, history = assign_identity_slug(locator, taken_slugs=set(), history={})
    expected_digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()
    assert slug == f"acct-v1-{expected_digest}"
    assert history[locator] == slug


def test_assign_identity_slug_reuses_history_on_second_call():
    locator = "wsl:ubuntu:/home/nick/.claude"
    slug1, history = assign_identity_slug(locator, taken_slugs=set(), history={})
    slug2, history2 = assign_identity_slug(locator, taken_slugs={slug1}, history=history)
    assert slug1 == slug2
    assert history2 == history


def test_assign_identity_slug_resolves_collision_with_counter():
    locator_a = "wsl:ubuntu:/home/nick/.claude"
    slug_a, _ = assign_identity_slug(locator_a, taken_slugs=set(), history={})

    locator_b = "wsl:ubuntu:/home/nick/.claude-work"
    slug_b, history_b = assign_identity_slug(locator_b, taken_slugs={slug_a}, history={})
    assert slug_b != slug_a
    expected_digest = hashlib.sha256(f"{locator_b}\x002".encode("utf-8")).hexdigest()
    # Only true if slug_a happens to equal the un-collided hash of locator_b,
    # which it won't in practice -- this test instead asserts the mechanism:
    # forcing taken_slugs to already contain the un-collided digest forces
    # the counter path.
    forced_taken = {hashlib.sha256(locator_b.encode("utf-8")).hexdigest()}
    forced_taken = {f"acct-v1-{d}" for d in forced_taken}
    slug_c, history_c = assign_identity_slug(locator_b, taken_slugs=forced_taken, history={})
    assert slug_c == f"acct-v1-{expected_digest}"
    assert history_c[locator_b] == slug_c


def test_identity_history_round_trip(tmp_path):
    save_identity_history(tmp_path, {"wsl:ubuntu:/home/nick/.claude": "acct-v1-abc"})
    assert load_identity_history(tmp_path) == {"wsl:ubuntu:/home/nick/.claude": "acct-v1-abc"}


def test_identity_history_absent_file_returns_empty(tmp_path):
    assert load_identity_history(tmp_path) == {}
