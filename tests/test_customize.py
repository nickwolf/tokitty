from pathlib import Path

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
            coat="gray_tabby",
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
    assert loaded[SINGLE_KEY].coat == "orange_tabby"


def test_unknown_override_key_dropped(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"coat": "orange_tabby", '
        '"overrides": {"coat_base": "#112233", "bogus_key": "#ffffff"}, "label": ""}}',
        encoding="utf-8",
    )
    loaded = load_customization(tmp_path)
    assert loaded[SINGLE_KEY].overrides == {"coat_base": "#112233"}


def test_invalid_hex_value_dropped(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"coat": "orange_tabby", '
        '"overrides": {"coat_base": "not-a-color", "coat_shade": "#abcdef"}, "label": ""}}',
        encoding="utf-8",
    )
    loaded = load_customization(tmp_path)
    assert loaded[SINGLE_KEY].overrides == {"coat_shade": "#abcdef"}


def test_effective_palette_no_overrides_matches_get_palette(tmp_path):
    custom = Customization(coat="black")
    assert effective_palette(custom) == sprites.get_palette("black")


def test_effective_palette_applies_coat_base_and_shade():
    custom = Customization(
        coat="orange_tabby",
        overrides={"coat_base": "#111111", "coat_shade": "#222222"},
    )
    palette = effective_palette(custom)
    assert palette["o"] == "#111111"
    assert palette["O"] == "#222222"


def test_effective_palette_ignores_card_bg_and_bar_fill():
    custom = Customization(
        coat="orange_tabby",
        overrides={"card_bg": "#333333", "bar_fill": "#444444"},
    )
    palette = effective_palette(custom)
    assert palette == sprites.get_palette("orange_tabby")
