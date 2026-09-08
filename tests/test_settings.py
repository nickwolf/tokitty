import json

import pytest

from tokitty.settings import (
    Settings,
    budget_for,
    load_settings,
    save_settings,
    update_settings,
    with_budget,
)


def test_default_tray_enabled_true(tmp_path):
    assert load_settings(tmp_path).tray_enabled is True


def test_roundtrip(tmp_path):
    save_settings(tmp_path, Settings(tray_enabled=False))
    assert load_settings(tmp_path).tray_enabled is False


def test_unparseable_file_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{ not json", encoding="utf-8")
    assert load_settings(tmp_path).tray_enabled is True


def test_wrong_shape_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("[]", encoding="utf-8")
    assert load_settings(tmp_path).tray_enabled is True


def test_non_bool_value_defaults(tmp_path):
    (tmp_path / "settings.json").write_text('{"tray_enabled": "yes"}', encoding="utf-8")
    assert load_settings(tmp_path).tray_enabled is True


def test_surprise_me_default_false(tmp_path):
    assert load_settings(tmp_path).surprise_me is False


def test_surprise_me_roundtrip(tmp_path):
    save_settings(tmp_path, Settings(tray_enabled=True, surprise_me=True))
    assert load_settings(tmp_path).surprise_me is True


def test_surprise_me_non_bool_defaults(tmp_path):
    (tmp_path / "settings.json").write_text('{"surprise_me": "yes"}', encoding="utf-8")
    assert load_settings(tmp_path).surprise_me is False


def test_update_leaves_other_fields_alone(tmp_path):
    save_settings(tmp_path, Settings(tray_enabled=True, surprise_me=True))
    update_settings(tmp_path, tray_enabled=False)
    loaded = load_settings(tmp_path)
    assert loaded.tray_enabled is False
    assert loaded.surprise_me is True


def test_update_returns_the_saved_settings(tmp_path):
    assert update_settings(tmp_path, surprise_me=True) == load_settings(tmp_path)


def test_update_on_a_missing_file_starts_from_defaults(tmp_path):
    update_settings(tmp_path, surprise_me=True)
    loaded = load_settings(tmp_path)
    assert loaded.surprise_me is True
    assert loaded.tray_enabled is True


def test_opacity_defaults_to_fully_opaque(tmp_path):
    assert load_settings(tmp_path).opacity == 100


def test_opacity_roundtrip(tmp_path):
    update_settings(tmp_path, opacity=60)
    assert load_settings(tmp_path).opacity == 60


@pytest.mark.parametrize("value", ['"60"', "true", "55", "0", "null"])
def test_unsupported_opacity_falls_back_to_fully_opaque(tmp_path, value):
    (tmp_path / "settings.json").write_text('{"opacity": %s}' % value, encoding="utf-8")
    assert load_settings(tmp_path).opacity == 100


# --- per-model usage settings ------------------------------------------


def test_usage_fields_round_trip(tmp_path):
    save_settings(
        tmp_path,
        Settings(
            view_mode="models",
            usage_window="month",
            usage_readout="tokens",
            usage_budgets={"acct-v1-abc": {"month": 40.0}},
            onboarding_version=1,
        ),
    )
    loaded = load_settings(tmp_path)
    assert loaded.view_mode == "models"
    assert loaded.usage_window == "month"
    assert loaded.usage_readout == "tokens"
    assert loaded.usage_budgets == {"acct-v1-abc": {"month": 40.0}}
    assert loaded.onboarding_version == 1


def test_each_usage_field_degrades_independently(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "view_mode": "nonsense",
                "usage_window": "5h",
                "usage_readout": 7,
                "onboarding_version": -3,
                "opacity": 100,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_settings(tmp_path)
    assert (loaded.view_mode, loaded.usage_window, loaded.usage_readout) == (
        "limits",
        "7d",
        "cost",
    )
    assert loaded.onboarding_version == 0
    # A bad neighbor must not take a good value down with it.
    assert loaded.opacity == 100


def test_malformed_budget_entries_are_dropped_individually(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "usage_budgets": {
                    "good": {"month": 40, "5h": 10, "24h": "ten", "7d": -1, "month2": 5},
                    "bad": "not a dict",
                    "empty": {"7d": 0},
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_settings(tmp_path).usage_budgets == {"good": {"month": 40.0}}


def test_absent_budget_is_distinguishable_from_zero(tmp_path):
    settings = Settings(usage_budgets={"a": {"7d": 5.0}})
    assert budget_for(settings, "a", "7d") == 5.0
    assert budget_for(settings, "a", "24h") is None
    assert budget_for(settings, "missing", "7d") is None


def test_with_budget_sets_clears_and_leaves_siblings_alone():
    settings = Settings(usage_budgets={"a": {"7d": 5.0, "month": 9.0}, "b": {"7d": 1.0}})

    updated = with_budget(settings, "a", "24h", 12.5)
    assert updated["a"] == {"7d": 5.0, "month": 9.0, "24h": 12.5}
    assert updated["b"] == {"7d": 1.0}

    cleared = with_budget(settings, "a", "7d", None)
    assert cleared["a"] == {"month": 9.0}

    # Clearing the last budget for an account drops the account entirely.
    assert "b" not in with_budget(Settings(usage_budgets={"b": {"7d": 1.0}}), "b", "7d", 0)


def test_budgets_survive_an_unrelated_settings_update(tmp_path):
    save_settings(tmp_path, Settings(usage_budgets={"a": {"7d": 5.0}}))
    update_settings(tmp_path, opacity=80)
    assert load_settings(tmp_path).usage_budgets == {"a": {"7d": 5.0}}
