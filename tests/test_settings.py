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


def _preset(**overrides):
    base = {"name": "tokitty", "account_index": 0, "env": "wsl",
            "distro": "Ubuntu", "cwd": "/home/u/tokitty"}
    base.update(overrides)
    return base


def _load_presets(tmp_path, value):
    (tmp_path / "settings.json").write_text(
        json.dumps({"streamdock_presets": value}), encoding="utf-8")
    return load_settings(tmp_path).streamdock_presets


def test_presets_default_empty(tmp_path):
    assert load_settings(tmp_path).streamdock_presets == []


def test_valid_presets_load(tmp_path):
    native = {"name": "win", "account_index": 2, "env": "native", "cwd": "C:\\src"}
    assert _load_presets(tmp_path, [_preset(), native]) == [_preset(), native]


def test_preset_keeps_account_slug(tmp_path):
    p = _preset(account="work")
    assert _load_presets(tmp_path, [p]) == [p]


@pytest.mark.parametrize("bad", [None, "", "  ", 5])
def test_preset_drops_unusable_account_slug(tmp_path, bad):
    assert _load_presets(tmp_path, [_preset(account=bad)]) == [_preset()]


def _missing_name():
    p = _preset()
    del p["name"]
    return p


@pytest.mark.parametrize("bad", [
    _missing_name(),
    _preset(name=""),
    _preset(name="   "),
    _preset(name="x" * 41),
    _preset(name=5),
    _preset(account_index=True),
    _preset(account_index=-1),
    _preset(account_index="0"),
    _preset(env="docker"),
    _preset(cwd=""),
    _preset(cwd=None),
    _preset(distro=""),
    _preset(distro=None),
    "not a dict",
    None,
])
def test_malformed_preset_dropped_alone(tmp_path, bad):
    good = _preset(name="good")
    assert _load_presets(tmp_path, [bad, good]) == [good]


def test_wsl_without_distro_dropped(tmp_path):
    p = _preset()
    del p["distro"]
    assert _load_presets(tmp_path, [p]) == []


@pytest.mark.parametrize("value", ["x", {"name": "a"}, 3, None])
def test_non_list_presets_field_gives_empty(tmp_path, value):
    assert _load_presets(tmp_path, value) == []


def test_preset_extras_and_native_distro_dropped(tmp_path):
    native = _preset(env="native", extra=1)
    wsl = _preset(name="w", junk="x")
    assert _load_presets(tmp_path, [native, wsl]) == [
        {"name": "tokitty", "account_index": 0, "env": "native", "cwd": "/home/u/tokitty"},
        _preset(name="w"),
    ]


def test_preset_name_is_stripped_and_max_length_ok(tmp_path):
    loaded = _load_presets(tmp_path, [_preset(name="  " + "x" * 40 + "  ")])
    assert loaded[0]["name"] == "x" * 40


def test_duplicate_preset_names_keep_first(tmp_path):
    first = _preset(cwd="/a")
    assert _load_presets(tmp_path, [first, _preset(cwd="/b")]) == [first]


def test_presets_roundtrip(tmp_path):
    presets = [_preset(), {"name": "win", "account_index": 1, "env": "native", "cwd": "C:\\src"}]
    save_settings(tmp_path, Settings(streamdock_presets=presets))
    assert load_settings(tmp_path).streamdock_presets == presets


def test_update_check_default_true(tmp_path):
    assert load_settings(tmp_path).update_check is True


def test_update_check_roundtrip(tmp_path):
    save_settings(tmp_path, Settings(update_check=False))
    assert load_settings(tmp_path).update_check is False


def test_update_check_non_bool_defaults(tmp_path):
    (tmp_path / "settings.json").write_text('{"update_check": "no"}', encoding="utf-8")
    assert load_settings(tmp_path).update_check is True


def test_hidden_accounts_default_empty(tmp_path):
    assert load_settings(tmp_path).streamdock_hidden_accounts == []


def test_hidden_accounts_roundtrip(tmp_path):
    save_settings(tmp_path, Settings(streamdock_hidden_accounts=["a", "b"]))
    assert load_settings(tmp_path).streamdock_hidden_accounts == ["a", "b"]


@pytest.mark.parametrize("value", ["a", 3, {"a": 1}, None, True])
def test_hidden_accounts_non_list_gives_empty(tmp_path, value):
    (tmp_path / "settings.json").write_text(json.dumps({"streamdock_hidden_accounts": value}))
    assert load_settings(tmp_path).streamdock_hidden_accounts == []


def test_hidden_accounts_keeps_valid_strings_once(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"streamdock_hidden_accounts": ["a", "", "  ", 3, None, "a", "b"]})
    )
    assert load_settings(tmp_path).streamdock_hidden_accounts == ["a", "b"]


@pytest.mark.parametrize("raw,expected", [
    ("", None), ("   ", None), ("12.5", 12.5), ("$12.50", 12.5), (" $ 7 ", 7.0), ("1e2", 100.0),
])
def test_parse_budget_accepts(raw, expected):
    from tokitty.settings import parse_budget
    assert parse_budget(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["0", "-1", "$-3", "abc", "inf", "-inf", "nan", "$nan", "1,5"])
def test_parse_budget_rejects(raw):
    from tokitty.settings import parse_budget
    amount, error = parse_budget(raw)
    assert amount is None and error
