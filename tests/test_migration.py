from dataclasses import replace

from tokitty.accounts import Account
from tokitty.customize import Customization, SINGLE_KEY
from tokitty.migration import (
    CUSTOMIZATION_MIGRATION_KEY,
    LEGACY_ACCOUNT_LABELS_MIGRATION_KEY,
    absorb_implicit_default,
    load_migration_state,
    mark_customization_migration_complete,
    migrate_default_customization,
    migrate_legacy_account_labels,
)


def test_row1_always_one_account_migrates_default_to_slug(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo")}
    accounts = [Account(name="acct-v1-abc", config_dir="/home/u/.claude")]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result["acct-v1-abc"] == Customization(colorway="black", pattern="tuxedo")
    assert SINGLE_KEY not in result


def test_row2_two_to_one_keeps_stale_second_entry_orphaned(tmp_path):
    current_look = Customization(colorway="black", pattern="tuxedo")
    stale_look = Customization(colorway="orange", pattern="tabby")
    store = {SINGLE_KEY: current_look, "acct-v1-removed": stale_look}
    accounts = [Account(name="acct-v1-remaining", config_dir="/home/u/.claude")]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result["acct-v1-remaining"] == current_look
    assert result["acct-v1-removed"] == stale_look
    assert SINGLE_KEY not in result


def test_row3_absorb_implicit_default_on_first_explicit_add():
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo")}
    result = absorb_implicit_default(store, "acct-v1-new")
    assert result["acct-v1-new"] == Customization(colorway="black", pattern="tuxedo")
    assert SINGLE_KEY in result  # left in place, not deleted


def test_row3_absorb_is_a_noop_if_slug_already_has_an_entry():
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo"),
             "acct-v1-new": Customization(colorway="orange", pattern="tabby")}
    result = absorb_implicit_default(store, "acct-v1-new")
    assert result["acct-v1-new"] == Customization(colorway="orange", pattern="tabby")


def test_row4_two_new_accounts_startup_migration_is_noop(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo"),
             "acct-v1-one": Customization(colorway="grey", pattern="calico"),
             "acct-v1-two": Customization(colorway="orange", pattern="tabby")}
    accounts = [
        Account(name="acct-v1-one", config_dir="/home/u/.claude-1"),
        Account(name="acct-v1-two", config_dir="/home/u/.claude-2"),
    ]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result == store


def test_row5_historical_1_to_2_leaves_default_unconsumed(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo"),
             "acct-v1-one": Customization(colorway="grey", pattern="calico"),
             "acct-v1-two": Customization(colorway="orange", pattern="tabby")}
    accounts = [
        Account(name="acct-v1-one", config_dir="/home/u/.claude-1"),
        Account(name="acct-v1-two", config_dir="/home/u/.claude-2"),
    ]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result[SINGLE_KEY] == Customization(colorway="black", pattern="tuxedo")


def test_migration_is_idempotent_across_repeated_calls(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo")}
    accounts = [Account(name="acct-v1-abc", config_dir="/home/u/.claude")]
    first = migrate_default_customization(tmp_path, accounts, store)
    # Simulate a manual edit to the slug entry between launches.
    edited = dict(first)
    edited["acct-v1-abc"] = replace(edited["acct-v1-abc"], label="Personal")
    second = migrate_default_customization(tmp_path, accounts, edited)
    assert second["acct-v1-abc"].label == "Personal"


def test_default_migration_does_not_mark_itself_before_caller_persists(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo")}
    accounts = [Account(name="acct-v1-abc", config_dir="/home/u/.claude")]

    migrated = migrate_default_customization(tmp_path, accounts, store)

    assert migrated["acct-v1-abc"] == store[SINGLE_KEY]
    assert load_migration_state(tmp_path) == {}
    mark_customization_migration_complete(tmp_path, CUSTOMIZATION_MIGRATION_KEY)
    assert load_migration_state(tmp_path)[CUSTOMIZATION_MIGRATION_KEY] is True


def test_legacy_multi_account_migration_seeds_blank_visible_labels(tmp_path):
    accounts = [
        Account(name="Personal", config_dir="/home/u/.claude-personal"),
        Account(name="Work", config_dir="/home/u/.claude-work"),
    ]
    store = {
        "Personal": Customization(colorway="gray", pattern="solid"),
        "Work": Customization(colorway="black", pattern="tuxedo", label="Office"),
    }

    migrated = migrate_legacy_account_labels(tmp_path, accounts, store)

    assert migrated["Personal"].label == "Personal"
    assert migrated["Personal"].colorway == "gray"
    assert migrated["Work"].label == "Office"
    assert load_migration_state(tmp_path) == {}


def test_legacy_label_migration_is_independently_marked_and_skips_opaque_slugs(tmp_path):
    accounts = [
        Account(name="acct-v1-abc", config_dir="/home/u/.claude-a"),
        Account(name="Legacy", config_dir="/home/u/.claude-b"),
    ]
    store = {
        "acct-v1-abc": Customization(),
        "Legacy": Customization(),
    }

    migrated = migrate_legacy_account_labels(tmp_path, accounts, store)
    assert migrated["acct-v1-abc"].label == ""
    assert migrated["Legacy"].label == "Legacy"

    mark_customization_migration_complete(tmp_path, LEGACY_ACCOUNT_LABELS_MIGRATION_KEY)
    edited = dict(migrated)
    edited["Legacy"] = replace(edited["Legacy"], label="")
    assert migrate_legacy_account_labels(tmp_path, accounts, edited)["Legacy"].label == ""
