import json
import subprocess
import sys

import pytest

from tokitty import theme as T
from tokitty.theme import (
    BarStyle, CardStyle, TextStyle, Theme,
    bar_fill, card_alpha, halo_active, halo_applies, load_theme, save_theme,
)


def _write(tmp_path, obj):
    (tmp_path / "theme.json").write_text(json.dumps(obj), encoding="utf-8")


def test_defaults():
    t = Theme()
    assert t.card == CardStyle(background="#1c1c22", opacity=100, radius=0,
                               border_width=0, border_color="#333340")
    assert t.bars.height == 8 and (t.bars.mid_at, t.bars.high_at) == (50, 80)
    assert t.bars.layout == "beside_cat" and t.bars.shape == "flat"
    assert t.text.show == frozenset(T.TEXT_ELEMENTS)
    assert (t.text.halo, t.text.halo_below) == ("off", 60)
    assert (T.CARD_WIDTH, T.CARD_HEIGHT) == (300, 128)


def test_missing_file_gives_defaults(tmp_path):
    assert load_theme(tmp_path) == Theme()


@pytest.mark.parametrize("opacity,expected", [(0, 1), (10, 26), (100, 255), (1, 3)])
def test_card_alpha(opacity, expected):
    assert card_alpha(opacity) == expected


def test_card_alpha_never_zero():
    assert min(card_alpha(o) for o in range(0, 101, 10)) == 1


# --- migration ---

@pytest.mark.parametrize("level", [100, 90, 80, 70, 60, 50])
def test_migrates_each_tk_level(tmp_path, level):
    (tmp_path / "settings.json").write_text(json.dumps({"opacity": level}))
    assert load_theme(tmp_path).card.opacity == level


@pytest.mark.parametrize("raw", ['{"tray_enabled": true}', '{"opacity": "80"}',
                                 '{"opacity": 45}', '{"opacity": true}',
                                 '{"opacity": 0}', "not json", "[1, 2]"])
def test_migration_falls_back_to_100(tmp_path, raw):
    (tmp_path / "settings.json").write_text(raw)
    assert load_theme(tmp_path).card.opacity == 100


def test_migration_unparseable_theme_still_migrates(tmp_path):
    (tmp_path / "settings.json").write_text('{"opacity": 70}')
    (tmp_path / "theme.json").write_text("{nope")
    assert load_theme(tmp_path).card.opacity == 70


def test_non_object_theme_still_migrates(tmp_path):
    (tmp_path / "settings.json").write_text('{"opacity": 60}')
    _write(tmp_path, [1, 2, 3])
    assert load_theme(tmp_path) == Theme(card=CardStyle(opacity=60))


def test_existing_theme_wins_over_settings(tmp_path):
    (tmp_path / "settings.json").write_text('{"opacity": 60}')
    _write(tmp_path, {"card": {"opacity": 90}})
    assert load_theme(tmp_path).card.opacity == 90


# --- degradation ---

def test_one_bad_field_loses_only_that_field(tmp_path):
    _write(tmp_path, {"card": {"background": "#102030", "radius": 99, "opacity": 45},
                      "bars": {"height": 6, "low": "green"}})
    t = load_theme(tmp_path)
    assert t.card.background == "#102030"
    assert t.card.radius == 0 and t.card.opacity == 100
    assert t.bars.height == 6 and t.bars.low == BarStyle().low


def test_valid_values_load(tmp_path):
    _write(tmp_path, {
        "card": {"opacity": 0, "radius": 16, "border_width": 3},
        "bars": {"layout": "full_width", "shape": "rounded", "height": 10,
                 "mid_at": 0, "high_at": 100},
        "text": {"halo": "auto", "halo_below": 0},
    })
    t = load_theme(tmp_path)
    assert (t.card.opacity, t.card.radius, t.card.border_width) == (0, 16, 3)
    assert (t.bars.layout, t.bars.shape, t.bars.height) == ("full_width", "rounded", 10)
    assert (t.bars.mid_at, t.bars.high_at) == (0, 100)
    assert (t.text.halo, t.text.halo_below) == ("auto", 0)


def test_bool_rejected_as_int(tmp_path):
    _write(tmp_path, {"card": {"radius": True, "opacity": False},
                      "bars": {"height": True}, "text": {"halo_below": True}})
    t = load_theme(tmp_path)
    assert t.card.radius == 0 and t.card.opacity == 100
    assert t.bars.height == 8 and t.text.halo_below == 60


@pytest.mark.parametrize("value", [55, 5, -10, 110, 1.5, "50"])
def test_opacity_must_be_multiple_of_ten_in_range(tmp_path, value):
    _write(tmp_path, {"card": {"opacity": value}})
    assert load_theme(tmp_path).card.opacity == 100


@pytest.mark.parametrize("section,key,value,default", [
    ("card", "radius", 17, 0), ("card", "radius", -1, 0),
    ("card", "border_width", 4, 0),
    ("bars", "height", 3, 8), ("bars", "height", 11, 8),
    ("bars", "mid_at", 101, 50), ("bars", "high_at", -1, 80),
    ("text", "halo_below", 101, 60),
])
def test_out_of_range_rejected_not_clamped(tmp_path, section, key, value, default):
    _write(tmp_path, {section: {key: value}})
    assert getattr(getattr(load_theme(tmp_path), section), key) == default


@pytest.mark.parametrize("bad", ["#fff", "#12345g", "123456", 5, None, "#1234567"])
def test_bad_hex_rejected(tmp_path, bad):
    _write(tmp_path, {"card": {"background": bad}})
    assert load_theme(tmp_path).card.background == "#1c1c22"


def test_bad_enums_fall_back(tmp_path):
    _write(tmp_path, {"bars": {"layout": "wide", "shape": 3}, "text": {"halo": "yes"}})
    t = load_theme(tmp_path)
    assert (t.bars.layout, t.bars.shape, t.text.halo) == ("beside_cat", "flat", "off")


def test_unknown_show_names_dropped(tmp_path):
    _write(tmp_path, {"text": {"show": ["percent", "bogus", 3, "status"]}})
    assert load_theme(tmp_path).text.show == frozenset({"percent", "status"})


def test_empty_show_hides_everything(tmp_path):
    _write(tmp_path, {"text": {"show": []}})
    assert load_theme(tmp_path).text.show == frozenset()


def test_non_list_show_falls_back(tmp_path):
    _write(tmp_path, {"text": {"show": "percent"}})
    assert load_theme(tmp_path).text.show == frozenset(T.TEXT_ELEMENTS)


def test_unknown_keys_dropped(tmp_path):
    _write(tmp_path, {"extra": 1, "card": {"radius": 4, "sparkle": True}})
    t = load_theme(tmp_path)
    assert t == Theme(card=CardStyle(radius=4))
    assert not hasattr(t.card, "sparkle")


def test_non_dict_sections_ignored(tmp_path):
    _write(tmp_path, {"card": 5, "bars": [1], "text": "x"})
    assert load_theme(tmp_path) == Theme()


def test_non_object_top_level(tmp_path):
    _write(tmp_path, "hello")
    assert load_theme(tmp_path) == Theme()


@pytest.mark.parametrize("mid,high", [(80, 50), (60, 60)])
def test_inverted_thresholds_revert_both(tmp_path, mid, high):
    _write(tmp_path, {"bars": {"mid_at": mid, "high_at": high}})
    b = load_theme(tmp_path).bars
    assert (b.mid_at, b.high_at) == (50, 80)


# --- save / load ---

def test_round_trip(tmp_path):
    t = Theme(
        card=CardStyle(background="#010203", opacity=30, radius=8,
                       border_width=2, border_color="#aabbcc"),
        bars=BarStyle(layout="full_width", shape="rounded", height=6, mid_at=20, high_at=70),
        text=TextStyle(show=frozenset({"percent", "cat_tags"}), halo="auto", halo_below=40),
    )
    save_theme(tmp_path, t)
    assert load_theme(tmp_path) == t
    assert not (tmp_path / "theme.json.tmp").exists()
    raw = json.loads((tmp_path / "theme.json").read_text())
    assert raw["text"]["show"] == ["cat_tags", "percent"]


def test_round_trip_default(tmp_path):
    save_theme(tmp_path, Theme())
    assert load_theme(tmp_path) == Theme()


# --- bar_fill / halo ---

def test_bar_fill_override_wins():
    assert bar_fill(Theme(), 95, "#123456") == "#123456"


def test_bar_fill_ramp():
    th = Theme()
    assert bar_fill(th, 0, None) == th.bars.low
    assert bar_fill(th, 49.9, None) == th.bars.low
    assert bar_fill(th, 50, None) == th.bars.mid
    assert bar_fill(th, 79.9, None) == th.bars.mid
    assert bar_fill(th, 80, None) == th.bars.high


def test_bar_fill_uses_theme_thresholds_and_colours():
    th = Theme(bars=BarStyle(mid_at=10, high_at=20, low="#000001", mid="#000002", high="#000003"))
    assert [bar_fill(th, p, None) for p in (9, 10, 20)] == ["#000001", "#000002", "#000003"]


def test_bar_fill_neutral_without_ramp():
    th = Theme()
    assert bar_fill(th, None, None) == th.bars.neutral
    assert bar_fill(th, None, "") == th.bars.neutral


def test_halo_applies_only_to_measured_elements():
    assert {e for e in T.TEXT_ELEMENTS if halo_applies(e)} == {"section_labels", "percent"}


def test_halo_active():
    off, on, auto = (TextStyle(halo=h, halo_below=60) for h in ("off", "on", "auto"))
    assert not halo_active(off, 0) and not halo_active(off, 100)
    assert halo_active(on, 100) and halo_active(on, 0)
    assert halo_active(auto, 50) and not halo_active(auto, 60) and not halo_active(auto, 100)


def test_constants():
    assert T.SPOTS == ("inside", "on_top", "beside_left", "beside_right")
    assert T.VIEWS == ("limits", "models")
    assert len(T.TEXT_ELEMENTS) == 7


def test_import_isolation():
    code = (
        "import sys\n"
        "sys.modules['tkinter'] = None\n"
        "sys.modules['PySide6'] = None\n"
        "import tokitty.theme\n"
        "import tokitty.customize\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.gui
def test_defaults_match_tk_constants():
    pytest.importorskip("tkinter")
    from tokitty import display, ui

    t = Theme()
    assert t.card.background == ui.BG_COLOR
    assert t.text.foreground == ui.FG_COLOR
    assert t.text.dim == ui.DIM_COLOR
    assert t.bars.track == ui.BAR_BG
    assert t.bars.neutral == ui.NEUTRAL_BAR
    assert t.bars.height == ui.BAR_HEIGHT
    assert T.ACCENT_BG == ui.ACCENT_BG and T.ACCENT_FG == ui.ACCENT_FG
    assert T.CARD_WIDTH == ui.CARD_WIDTH and T.CARD_HEIGHT == ui.PANE_HEIGHT
    assert (t.bars.low, t.bars.mid, t.bars.high) == (display.GREEN, display.AMBER, display.RED)
    # thresholds: drive display.bar_color across the boundaries
    for p in (0, 49.9, 50, 79.9, 80, 100):
        assert bar_fill(t, p, None) == display.bar_color(p)
