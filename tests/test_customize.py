
import pytest

from tokitty import sprites
from tokitty.customize import (
    SINGLE_KEY,
    Customization,
    effective_palette,
    load_customization,
    rename_account,
    save_customization,
    save_customization_entry,
)


def test_absent_file_returns_empty_dict(tmp_path):
    assert load_customization(tmp_path) == {}


def test_corrupt_json_returns_empty_dict(tmp_path):
    (tmp_path / "customization.json").write_text("{not json", encoding="utf-8")
    assert load_customization(tmp_path) == {}


def test_roundtrip_save_and_load(tmp_path):
    data = {
        SINGLE_KEY: Customization(
            colorway="gray",
            pattern="tabby",
            overrides={"coat_base": "#112233", "card_bg": "#445566"},
            label="Work",
        )
    }
    save_customization(tmp_path, data)
    loaded = load_customization(tmp_path)
    assert loaded == data


def test_save_customization_entry_merges_into_latest_on_disk_store(tmp_path):
    save_customization(tmp_path, {
        "acct-b": Customization(colorway="gray", pattern="solid", label="Bee")
    })

    save_customization_entry(
        tmp_path,
        "acct-a",
        Customization(colorway="black", pattern="tuxedo", label="Ay"),
    )

    loaded = load_customization(tmp_path)
    assert loaded["acct-a"].label == "Ay"
    assert loaded["acct-b"].label == "Bee"


def test_unknown_coat_falls_back_to_orange_tabby(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"coat": "invisible_pink_unicorn", "overrides": {}, "label": ""}}',
        encoding="utf-8",
    )
    loaded = load_customization(tmp_path)
    assert (loaded[SINGLE_KEY].colorway, loaded[SINGLE_KEY].pattern) == ("orange", "tabby")


def test_unknown_override_key_dropped(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"colorway": "orange", "pattern": "tabby", '
        '"overrides": {"coat_base": "#112233", "bogus_key": "#ffffff"}, "label": ""}}',
        encoding="utf-8",
    )
    loaded = load_customization(tmp_path)
    assert loaded[SINGLE_KEY].overrides == {"coat_base": "#112233"}


def test_invalid_hex_value_dropped(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"colorway": "orange", "pattern": "tabby", '
        '"overrides": {"coat_base": "not-a-color", "coat_shade": "#abcdef"}, "label": ""}}',
        encoding="utf-8",
    )
    loaded = load_customization(tmp_path)
    assert loaded[SINGLE_KEY].overrides == {"coat_shade": "#abcdef"}


def test_effective_palette_no_overrides_matches_get_palette(tmp_path):
    custom = Customization(colorway="black", pattern="tabby")
    assert effective_palette(custom) == sprites.get_palette("black")


def test_effective_palette_applies_coat_base_and_shade():
    custom = Customization(
        colorway="orange",
        pattern="tabby",
        overrides={"coat_base": "#111111", "coat_shade": "#222222"},
    )
    palette = effective_palette(custom)
    assert palette["o"] == "#111111"
    assert palette["O"] == "#222222"


def test_effective_palette_ignores_card_bg_and_bar_fill():
    custom = Customization(
        colorway="orange",
        pattern="tabby",
        overrides={"card_bg": "#333333", "bar_fill": "#444444"},
    )
    palette = effective_palette(custom)
    assert palette == sprites.get_palette("orange_tabby")


def test_load_migrates_legacy_coat(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"coat": "calico", "label": "Mimi"}}', encoding="utf-8")
    got = load_customization(tmp_path)["default"]
    assert (got.colorway, got.pattern, got.label) == ("white", "calico", "Mimi")


def test_load_prefers_new_fields_over_legacy_coat(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"coat": "calico", "colorway": "gray", "pattern": "solid"}}', encoding="utf-8")
    got = load_customization(tmp_path)["default"]
    assert (got.colorway, got.pattern) == ("gray", "solid")


def test_load_unknown_colorway_pattern_defaults(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"colorway": "nope", "pattern": "nope"}}', encoding="utf-8")
    got = load_customization(tmp_path)["default"]
    assert (got.colorway, got.pattern) == ("orange", "tabby")


def test_save_writes_colorway_pattern_not_coat(tmp_path):
    save_customization(tmp_path, {"default": Customization(colorway="black", pattern="solid")})
    import json
    data = json.loads((tmp_path / "customization.json").read_text(encoding="utf-8"))
    assert data["default"]["colorway"] == "black"
    assert data["default"]["pattern"] == "solid"
    assert "coat" not in data["default"]


def test_effective_palette_uses_colorway_pattern(tmp_path):
    from tokitty.sprites import resolve_palette
    custom = Customization(colorway="gray", pattern="tabby")
    assert effective_palette(custom)["s"] == resolve_palette("gray", "tabby")["s"]


def test_rename_account_sets_label_only(tmp_path):
    save_customization(tmp_path, {"acct-v1-abc": Customization(colorway="black", pattern="tuxedo")})
    rename_account(tmp_path, "acct-v1-abc", "Personal")
    result = load_customization(tmp_path)
    assert result["acct-v1-abc"].label == "Personal"
    assert result["acct-v1-abc"].colorway == "black"


def test_rename_account_creates_entry_when_slug_absent(tmp_path):
    rename_account(tmp_path, "acct-v1-new", "Work")
    result = load_customization(tmp_path)
    assert result["acct-v1-new"].label == "Work"
    assert result["acct-v1-new"].colorway == "orange"


def test_cat_home_defaults_inside_when_absent(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"colorway": "gray", "pattern": "solid", "label": "x"}}', encoding="utf-8")
    got = load_customization(tmp_path)["default"]
    assert got.cat_home == "inside"
    assert (got.colorway, got.pattern, got.label) == ("gray", "solid", "x")


def test_cat_home_unknown_spot_falls_back(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"cat_home": "on_the_moon"}}', encoding="utf-8")
    assert load_customization(tmp_path)["default"].cat_home == "inside"


def test_cat_home_retired_side_spots_fall_back(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"a": {"cat_home": "beside_left"}, "b": {"cat_home": "beside_right"}}',
        encoding="utf-8")
    got = load_customization(tmp_path)
    assert {k: v.cat_home for k, v in got.items()} == {"a": "inside", "b": "inside"}


def test_cat_home_non_string_falls_back(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"a": {"cat_home": 3}, "b": {"cat_home": ["on_top"]}, "c": {"cat_home": null}}',
        encoding="utf-8")
    got = load_customization(tmp_path)
    assert {k: v.cat_home for k, v in got.items()} == {"a": "inside", "b": "inside", "c": "inside"}


def test_cat_home_roundtrip(tmp_path):
    data = {
        "a": Customization(cat_home="inside"),
        "b": Customization(cat_home="on_top"),
        "c": Customization(cat_home="inside_right"),
        "d": Customization(),
    }
    save_customization(tmp_path, data)
    assert load_customization(tmp_path) == data


def test_coat_override_carries_into_the_shut_eye_tone():
    custom = Customization(overrides={"coat_base": "#4080c0"})
    palette = effective_palette(custom)
    assert palette[sprites.LID_CHAR] == sprites.lid_tone("#4080c0") == "#204060"


def test_tail_round_trips(tmp_path):
    save_customization(tmp_path, {SINGLE_KEY: Customization(tail="ringed_pale")})
    assert load_customization(tmp_path)[SINGLE_KEY].tail == "ringed_pale"


@pytest.mark.parametrize("raw", ['"tail": "nope"', '"tail": 3', '"tail": null', '"tail": ["ringed"]', '"x": 1'])
def test_missing_or_invalid_tail_loads_plain(tmp_path, raw):
    (tmp_path / "customization.json").write_text(
        '{"default": {"colorway": "gray", "pattern": "solid", ' + raw + '}}', encoding="utf-8")
    got = load_customization(tmp_path)[SINGLE_KEY]
    assert got.tail == "plain"
    assert (got.colorway, got.pattern) == ("gray", "solid")


def test_legacy_coat_file_loads_plain_tail(tmp_path):
    (tmp_path / "customization.json").write_text('{"default": {"coat": "calico"}}', encoding="utf-8")
    assert load_customization(tmp_path)[SINGLE_KEY].tail == "plain"


def test_effective_palette_uses_the_tail():
    plain = effective_palette(Customization())
    ringed = effective_palette(Customization(tail="ringed"))
    assert plain == dict(sprites.resolve_palette("orange", "tabby"))
    assert ringed == dict(sprites.resolve_palette("orange", "tabby", "ringed"))
    assert (ringed["y"], ringed["Y"]) != (plain["y"], plain["Y"])


def test_coat_override_still_rederives_the_lid_with_a_tail():
    palette = effective_palette(Customization(tail="ringed", overrides={"coat_base": "#4080c0"}))
    assert palette["o"] == "#4080c0"
    assert palette[sprites.LID_CHAR] == sprites.lid_tone("#4080c0")
    assert palette["y"] == sprites.resolve_palette("orange", "tabby", "ringed")["y"]


def test_coat_overrides_carry_into_every_derived_tone():
    custom = Customization(overrides={"coat_base": "#4080c0", "coat_shade": "#204060"})
    palette = effective_palette(custom)
    assert palette[sprites.GLOW_CHAR] == sprites.glow_tone("#4080c0")
    assert palette[sprites.DIM_CHARS["o"]] == sprites.dim_tone("#4080c0")
    assert palette[sprites.DIM_CHARS["O"]] == sprites.dim_tone("#204060")
