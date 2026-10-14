"""Account-dialog logic shared by the Qt Accounts window (accounts_logic.py)."""
import time

from tokitty import accounts_logic
from tokitty.accounts import Account
from tokitty.accounts_logic import (
    build_discovered_path_specs,
    build_row_specs,
    reconcile_before_save,
)
from tokitty.accounts_rows import elide_middle
from tokitty.customize import Customization


def test_build_row_specs_remove_disabled_at_one_account():
    accounts = [Account(name="acct-v1-a", config_dir="/home/u/.claude")]
    rows = build_row_specs(accounts, {})
    assert len(rows) == 1
    assert rows[0].remove_enabled is False


def test_build_row_specs_remove_enabled_above_one_account():
    accounts = [
        Account(name="acct-v1-a", config_dir="/home/u/.claude-a"),
        Account(name="acct-v1-b", config_dir="/home/u/.claude-b"),
    ]
    rows = build_row_specs(accounts, {})
    assert all(row.remove_enabled for row in rows)


def test_build_row_specs_label_falls_back_without_showing_the_slug():
    accounts = [Account(name="acct-v1-deadbeef", config_dir="/home/u/.claude")]
    rows = build_row_specs(accounts, {})
    assert "acct-v1-deadbeef" not in rows[0].display_label


def test_build_row_specs_uses_stored_label_when_present():
    accounts = [Account(name="acct-v1-a", config_dir="/home/u/.claude")]
    store = {"acct-v1-a": Customization(colorway="black", pattern="tuxedo", label="Personal")}
    rows = build_row_specs(accounts, store)
    assert rows[0].display_label == "Personal"


def test_build_discovered_path_specs_converts_credentials_to_config_and_sessions_dirs():
    specs = build_discovered_path_specs([
        ("Ubuntu", "/home/nick/.claude-work/.credentials.json")
    ])

    assert specs[0].config_dir == r"\\wsl.localhost\Ubuntu\home\nick\.claude-work"
    assert specs[0].sessions_dir == (
        r"\\wsl.localhost\Ubuntu\home\nick\.claude-work\tokitty\sessions"
    )


def test_reconcile_before_save_reloads_from_disk(tmp_path):
    from tokitty.accounts import save_accounts

    save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")])
    stale_in_memory = []  # a second dialog that never saw the first dialog's write
    reconciled = reconcile_before_save(tmp_path, stale_in_memory)
    assert [a.name for a in reconciled] == ["acct-v1-a"]


def test_reconcile_before_save_falls_back_to_in_memory_when_disk_empty(tmp_path):
    stale_in_memory = [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")]
    reconciled = reconcile_before_save(tmp_path, stale_in_memory)
    assert reconciled is stale_in_memory


def test_reconcile_before_save_respects_present_valid_empty_file(tmp_path):
    from tokitty.accounts import save_accounts

    save_accounts(tmp_path, [])
    stale_in_memory = [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")]
    assert reconcile_before_save(tmp_path, stale_in_memory) == []


def test_describe_capabilities_never_calls_a_transcripts_only_account_broken():
    from tokitty.accounts_logic import describe_capabilities

    subscription, local = describe_capabilities(has_credentials=False, has_transcripts=True)
    assert "not available (no credentials found)" in subscription
    assert "transcripts found" in local
    assert "invalid" not in subscription.lower()
    assert "error" not in subscription.lower()


def test_describe_capabilities_reports_a_full_account():
    from tokitty.accounts_logic import describe_capabilities

    subscription, local = describe_capabilities(True, True)
    assert subscription == "subscription usage: available"
    assert local == "local usage: transcripts found"


def _codex_home(tmp_path, with_rollout=True):
    home = tmp_path / ".codex"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    if with_rollout:
        (sessions / "2026").mkdir()
    return home


def test_describe_codex_capabilities():
    from tokitty.accounts_logic import describe_codex_capabilities

    assert describe_codex_capabilities(True) == (
        "rate limits: read from rollouts", "local usage: rollouts found"
    )
    assert describe_codex_capabilities(False)[1] == "local usage: no rollouts found"


def test_build_row_specs_carries_the_provider():
    rows = build_row_specs(
        [Account(name="a", config_dir="/a"), Account(name="c", config_dir="/c", provider="codex")],
        {},
    )
    assert [row.provider for row in rows] == ["claude", "codex"]


def test_discover_local_codex_home(tmp_path, monkeypatch):
    
    home = _codex_home(tmp_path)
    monkeypatch.setattr(accounts_logic, "default_codex_home", lambda: str(home))
    assert accounts_logic.discover_local_codex_home([]) == str(home)
    assert accounts_logic.discover_local_codex_home(
        [Account(name="c", config_dir=str(home), provider="codex")]
    ) is None

    monkeypatch.setattr(accounts_logic, "default_codex_home", lambda: str(tmp_path / "missing"))
    assert accounts_logic.discover_local_codex_home([]) is None


def test_only_a_codex_wsl_path_is_validated_off_thread():
    from tokitty.accounts_logic import _needs_off_thread_validation

    unc = r"\\wsl.localhost\Ubuntu\home\nick\.codex"
    assert _needs_off_thread_validation("codex", unc)
    assert not _needs_off_thread_validation("codex", "/home/nick/.codex")
    # The Claude path is left exactly as it was.
    assert not _needs_off_thread_validation("claude", unc)


def _codex_hook_text(tmp_path, home):
    from tokitty import hooks_install

    # The record is read from the state dir; installs write it to
    # hooks_install's (a temp dir under conftest), the same one in a real run.
    return accounts_logic.codex_hook_text(str(home), hooks_install.state_dir_path())


def test_codex_hook_text_for_each_status(tmp_path):
    from tokitty import codex_trust as ct
    from tokitty import hooks_install as hi

    home = _codex_home(tmp_path)
    assert _codex_hook_text(tmp_path, home) is None

    hi.install_hooks_for_dir(str(home), "codex")
    assert _codex_hook_text(tmp_path, home) == "hooks: waiting for approval in Codex"

    lines = []
    for event, _m in hi.CODEX_EVENTS:
        key = f"{hi.codex_paths(str(home))[1]}:{ct.EVENT_SNAKE[event]}:0:0"
        lines += [f"[hooks.state.'{key}']", 'trusted_hash = "sha256:x"', ""]
    (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")
    assert _codex_hook_text(tmp_path, home) == "hooks: approved, no activity from Codex hooks yet"

    sessions = home / "tokitty" / "sessions"
    sessions.mkdir()
    future = time.time() + 3600
    import os
    os.utime(sessions, (future, future))
    assert _codex_hook_text(tmp_path, home) == "hooks: approved"

    (home / "config.toml").unlink()
    (home / "config.toml").mkdir()
    assert _codex_hook_text(tmp_path, home) == "hooks: can't read Codex hook state"


def test_elide_middle():
    assert elide_middle("C:\\a\\b") == "C:\\a\\b"
    long = "C:\\Users\\nickw\\AppData\\Roaming\\profiles\\Claude\\personal"
    assert elide_middle(long) == "C:\\Users\\nickw\\…\\Claude\\personal"
    out = elide_middle("x" * 80)
    assert len(out) <= 38 and "…" in out

