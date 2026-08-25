import pytest

from tokitty.accounts import Account
from tokitty.accounts_ui import build_row_specs, reconcile_before_save
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


@pytest.mark.gui
def test_accounts_manager_open_is_singleton_per_root(tmp_path):
    tk = pytest.importorskip("tkinter")
    from tokitty.accounts_ui import AccountsManager
    from tokitty.accounts import save_accounts

    root = tk.Tk()
    try:
        save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")])
        first = AccountsManager.open(root, tmp_path)
        second = AccountsManager.open(root, tmp_path)
        assert first is second
        first._on_close()
        third = AccountsManager.open(root, tmp_path)
        assert third is not first
        third._on_close()
    finally:
        root.destroy()
