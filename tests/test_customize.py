
from tokitty import sprites
from tokitty.customize import (
    SINGLE_KEY,
    Customization,
    effective_palette,
    load_customization,
    save_customization,
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
