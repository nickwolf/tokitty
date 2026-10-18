import json
import subprocess
import sys

from dataclasses import replace

import pytest

from tokitty import theme as T
from tokitty.theme import (
    BarStyle, CardStyle, TextStyle, Theme,
    bar_fill, card_alpha, halo_active, halo_applies, load_theme, save_theme,
    theme_from_dict, rejected_fields,
)


def _write(tmp_path, obj):
    (tmp_path / "theme.json").write_text(json.dumps(obj), encoding="utf-8")


def test_defaults():
    t = Theme()
    assert t.card == CardStyle(design="refined", background="#1b1b21", opacity=100, radius=10,
                               border_width=1, border_color="#2e2e38")
    assert t.bars.height == 6 and (t.bars.mid_at, t.bars.high_at) == (50, 80)
    assert t.bars.layout == "beside_cat" and t.bars.shape == "rounded"
    assert t == T.REFINED
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
    assert t.card.radius == 10 and t.card.opacity == 100
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
    assert t.card.radius == 10 and t.card.opacity == 100
    assert t.bars.height == 6 and t.text.halo_below == 60


@pytest.mark.parametrize("value", [55, 5, -10, 110, 1.5, "50"])
def test_opacity_must_be_multiple_of_ten_in_range(tmp_path, value):
    _write(tmp_path, {"card": {"opacity": value}})
    assert load_theme(tmp_path).card.opacity == 100


@pytest.mark.parametrize("section,key,value,default", [
    ("card", "radius", 17, 10), ("card", "radius", -1, 10),
    ("card", "border_width", 4, 1),
    ("bars", "height", 3, 6), ("bars", "height", 11, 6),
    ("bars", "mid_at", 101, 50), ("bars", "high_at", -1, 80),
    ("text", "halo_below", 101, 60),
])
def test_out_of_range_rejected_not_clamped(tmp_path, section, key, value, default):
    _write(tmp_path, {section: {key: value}})
    assert getattr(getattr(load_theme(tmp_path), section), key) == default


@pytest.mark.parametrize("bad", ["#fff", "#12345g", "123456", 5, None, "#1234567"])
def test_bad_hex_rejected(tmp_path, bad):
    _write(tmp_path, {"card": {"background": bad}})
    assert load_theme(tmp_path).card.background == "#1b1b21"


def test_bad_enums_fall_back(tmp_path):
    _write(tmp_path, {"bars": {"layout": "wide", "shape": 3}, "text": {"halo": "yes"}})
    t = load_theme(tmp_path)
    assert (t.bars.layout, t.bars.shape, t.text.halo) == ("beside_cat", "rounded", "off")


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
    assert T.SPOTS == ("inside", "inside_right", "on_top")
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


# --- card layout ---

from itertools import combinations  # noqa: E402

from tokitty.theme import CARD_HEIGHT, CARD_WIDTH, CLASSIC, Rect, layout_card  # noqa: E402


def _classic(**parts):
    """The classic preset with some sections replaced: the old geometry."""
    return replace(CLASSIC, **parts)


def _xywh(layout, name):
    r = layout[name]
    return (r.x, r.y, r.w, r.h)


def test_rect_geometry():
    a = Rect(0, 0, 10, 10)
    assert (a.right, a.bottom) == (10, 10)
    assert a.intersects(Rect(5, 5, 10, 10))
    assert not a.intersects(Rect(10, 0, 5, 5))  # touching edges only
    assert a.contains(Rect(2, 2, 8, 8)) and not a.contains(Rect(2, 2, 9, 8))


def test_limits_defaults_match_tk():
    lay = layout_card(CLASSIC, "limits", "inside")
    assert lay.layout == "beside_cat"
    assert _xywh(lay, "cat_area") == (8, 8, 112, 112)
    assert [lay[n].y for n in ("session_label", "session_bar", "session_percent",
                               "weekly_label", "weekly_bar", "weekly_percent",
                               "status")] == [12, 30, 42, 60, 78, 90, 108]
    assert lay["session_bar"].x == 132 and lay["session_bar"].w == 158
    assert lay["session_bar"].h == 8
    assert lay["session_percent"].x == 132
    assert lay["session_reset"].x == lay["session_percent"].right
    assert lay["session_reset"].right == 290
    assert lay.lines["weekly"] == Rect(132, 90, 158, 14)
    assert _xywh(lay, "account_label")[:2] == (194, 4)
    assert lay["account_label"].right == 294


def test_models_defaults_match_tk():
    lay = layout_card(CLASSIC, "models", "inside")
    assert [lay[f"model_name_{i}"].y for i in range(3)] == [20, 48, 76]
    assert [lay[f"model_bar_{i}"].y for i in range(3)] == [34, 62, 90]
    assert all(lay[f"model_name_{i}"].x == 132 for i in range(3))
    assert all(lay[f"model_value_{i}"].right == CARD_WIDTH - 8 for i in range(3))
    assert all(lay[f"model_bar_{i}"].w == 158 and lay[f"model_bar_{i}"].h == 8
               for i in range(3))
    assert lay["status"].y == 108


def test_taller_bar_pushes_rows_down():
    th = _classic(bars=replace(CLASSIC.bars, height=10))
    lay = layout_card(th, "limits", "inside")
    assert lay["session_bar"].y == 30 and lay["session_bar"].h == 10
    assert lay["session_percent"].y == 44
    assert lay["weekly_label"].y == 62
    assert lay["status"].y == 112
    mod = layout_card(th, "models", "inside")
    assert [mod[f"model_name_{i}"].y for i in range(3)] == [20, 50, 80]


def test_hiding_section_labels_collapses_the_step():
    th = _classic(text=TextStyle(show=frozenset(T.TEXT_ELEMENTS) - {"section_labels"}))
    lay = layout_card(th, "limits", "inside")
    assert "session_label" not in lay and "weekly_label" not in lay
    assert lay["session_bar"].y == 12
    assert lay["weekly_bar"].y == 42
    assert lay["status"].y == 72


def test_percent_hidden_reset_takes_line_start():
    th = _classic(text=TextStyle(show=frozenset(T.TEXT_ELEMENTS) - {"percent"}))
    lay = layout_card(th, "limits", "inside")
    assert "session_percent" not in lay
    assert lay["session_reset"] == Rect(132, 42, 158, 14)


def test_both_runs_hidden_drops_line_and_step():
    th = _classic(text=TextStyle(show=frozenset(T.TEXT_ELEMENTS) - {"percent", "reset"}))
    lay = layout_card(th, "limits", "inside")
    assert not lay.lines
    assert lay["weekly_label"].y == 30 + 8 + 4


def test_hidden_status_and_account_label_absent():
    th = Theme(text=TextStyle(show=frozenset()))
    lay = layout_card(th, "limits", "inside")
    assert "status" not in lay and "account_label" not in lay


def test_models_hidden_names_drop_name_and_value():
    th = _classic(text=TextStyle(show=frozenset({"status"})))
    lay = layout_card(th, "models", "inside")
    assert "model_name_0" not in lay and "model_value_0" not in lay
    assert lay["model_bar_0"].y == 20


def test_full_width_needs_outside_spot():
    th = Theme(bars=BarStyle(layout="full_width"))
    wide = layout_card(th, "limits", "on_top")
    assert wide.layout == "full_width" and "cat_area" not in wide
    assert (wide["session_bar"].x, wide["session_bar"].w) == (8, 284)
    back = layout_card(th, "limits", "inside")
    assert back.layout == "beside_cat" and "cat_area" in back
    assert back["session_bar"].x == 132


@pytest.mark.parametrize("view,spot", [("per_model", "inside"), ("limits", "nowhere")])
def test_layout_card_rejects_bad_args(view, spot):
    with pytest.raises(ValueError):
        layout_card(Theme(), view, spot)


def _subsets(items):
    for n in range(len(items) + 1):
        for combo in combinations(items, n):
            yield frozenset(combo)


@pytest.mark.parametrize("design", T.DESIGNS)
@pytest.mark.parametrize("view", T.VIEWS)
@pytest.mark.parametrize("layout,spot", [("beside_cat", "inside"),
                                         ("full_width", "on_top"),
                                         ("beside_cat", "inside_right"),
                                         ("full_width", "inside"),
                                         ("full_width", "inside_right")])
def test_every_combination_fits_and_does_not_overlap(design, view, layout, spot):
    card = Rect(0, 0, CARD_WIDTH, CARD_HEIGHT)
    for height in range(4, 11):
        for show in _subsets(T.TEXT_ELEMENTS):
            base = T.design_preset(design)
            th = replace(base, bars=replace(base.bars, layout=layout, height=height),
                         text=TextStyle(show=show))
            lay = layout_card(th, view, spot)
            where = (design, view, layout, spot, height, sorted(show))
            for name, r in lay.rects.items():
                assert card.contains(r), (name, where)
            solid = [(n, r) for n, r in lay.rects.items() if n != "account_label"]
            for i, (n1, r1) in enumerate(solid):
                for n2, r2 in solid[i + 1:]:
                    assert not r1.intersects(r2), (n1, n2, where)


# --- spots, window geometry, pane style ---

import itertools  # noqa: E402

from tokitty.customize import Customization, effective_palette  # noqa: E402
from tokitty.theme import (  # noqa: E402
    SCRATCH_SPOTS, SPRITE_H, SPRITE_W, Placement, Track, cell_size, grid_shape,
    hit_region, layout_window, placement, reachable_spots, resolve_home,
    resolve_pane_style, snap, top_edge_track,
)

FULL = Theme(bars=BarStyle(layout="full_width"))


def test_sprite_size():
    assert (SPRITE_W, SPRITE_H) == (112, 104)


def test_placements():
    assert placement("inside") == Placement((8, 12), False)
    assert placement("on_top") == Placement((8, -104), False)
    assert placement("inside_right") == Placement((180, 12), True)
    assert SCRATCH_SPOTS == {"inside", "inside_right"}
    assert set(T.SPOTS) == {"inside", "inside_right", "on_top"}


def test_top_edge_track():
    t = top_edge_track()
    assert t == Track(y=-104, x_min=0, x_max=188)
    assert t.x_min <= placement("on_top").origin[0] <= t.x_max
    assert placement("on_top").origin[1] == t.y


@pytest.mark.parametrize("n,shape", [(1, (1, 1)), (4, (1, 4)), (5, (2, 3)), (8, (2, 4)),
                                     (9, (3, 3)), (12, (3, 4))])
def test_grid_shape(n, shape):
    assert grid_shape(n) == shape


@pytest.mark.parametrize("n", [0, -1])
def test_grid_shape_rejects_empty(n):
    with pytest.raises(ValueError):
        grid_shape(n)


def _outside(n, i):
    return reachable_spots(Theme(), "inside", i, n) - {"inside", "inside_right"}


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_single_column_on_top_only_from_first_row(n):
    for i in range(n):
        assert _outside(n, i) == ({"on_top"} if i == 0 else set())


def test_reachable_includes_both_inside_spots_by_default():
    for n in (1, 5, 9):
        for i in range(n):
            assert {"inside", "inside_right"} <= reachable_spots(Theme(), "inside", i, n)


def test_on_top_is_the_first_grid_row():
    # 2 cols x 3 rows: indices 0 1 / 2 3 / 4 .
    assert [bool(_outside(5, i)) for i in range(5)] == [True, True, False, False, False]
    assert [bool(_outside(8, i)) for i in range(8)] == [True, True] + [False] * 6
    # 3 cols x 3 rows
    assert [bool(_outside(9, i)) for i in range(9)] == [True] * 3 + [False] * 6


def test_full_width_inside_keeps_inside_spots():
    assert {"inside", "inside_right"} <= reachable_spots(FULL, "inside", 0, 1)
    assert {"inside", "inside_right"} <= reachable_spots(FULL, "inside_right", 0, 1)


def test_full_width_on_top_home_drops_inside_spots():
    r = reachable_spots(FULL, "on_top", 0, 1)
    assert r == {"on_top"}


def test_full_width_unreachable_home_keeps_inside():
    # on_top is not reachable from pane 1, so it resolves to inside
    assert "inside" in reachable_spots(FULL, "on_top", 1, 3)


def test_beside_cat_always_includes_inside():
    for home in T.SPOTS:
        assert "inside" in reachable_spots(Theme(), home, 0, 1)


def test_resolve_home_fallback():
    assert resolve_home("inside_right", 0, 3) == "inside_right"
    assert resolve_home("inside_right", 2, 3) == "inside_right"
    assert resolve_home("on_top", 0, 3) == "on_top"
    assert resolve_home("on_top", 1, 3) == "inside"
    assert resolve_home("on_top", 4, 5) == "inside"
    assert resolve_home("inside", 0, 1) == "inside"
    assert resolve_home("beside_left", 0, 1) == "inside"
    assert resolve_home("garbage", 0, 1) == "inside"


def test_resolve_home_agrees_with_reachable():
    for n in (1, 2, 5, 8, 9):
        for i in range(n):
            for spot in T.SPOTS:
                if spot != "inside" and resolve_home(spot, i, n) == spot:
                    assert spot in reachable_spots(Theme(), "inside", i, n)


def test_window_all_inside_is_just_the_grid():
    w = layout_window(["inside"] * 3)
    assert w.margins == (0, 0, 0, 0)
    assert w.size == (300, 384)
    assert w.pane_origins == ((0, 0), (0, 128), (0, 256))


def test_window_margins_homes_only():
    w = layout_window(["inside_right", "inside", "inside"])
    assert w.margins == (0, 0, 0, 0)
    assert w.size == (300, 384)
    w = layout_window(["on_top", "inside"])
    assert w.margins == (0, 116, 0, 0)
    assert w.size == (300, 116 + 256)


def test_window_margins_wander_sizes_for_reachable():
    w = layout_window(["inside", "inside"], wander=True)
    assert w.margins == (0, 116, 0, 0)
    assert w.size == (300, 116 + 256)
    # no pane in the first row, so nothing can reach the top
    assert layout_window(["inside"] * 9, wander=True).margins == (0, 116, 0, 0)


@pytest.mark.parametrize("n", [1, 2, 5, 9])
def test_window_never_has_side_margins(n):
    for homes in itertools.product(T.SPOTS, repeat=min(n, 3)):
        homes = list(homes) + ["inside"] * (n - len(homes))
        for wander in (False, True):
            m = layout_window(homes, wander).margins
            assert m in ((0, 0, 0, 0), (0, 116, 0, 0)), (homes, wander)
            assert (m[1] == 116) == ("on_top" in homes or wander)


def test_window_origins_two_columns():
    w = layout_window(["on_top"] + ["inside"] * 4)
    assert w.pane_origins == ((0, 116), (300, 116), (0, 244), (300, 244), (0, 372))
    assert w.size == (600, 116 + 384)


def test_round_up4():
    assert [T._round_up4(v) for v in (0, 1, 4, 5, 104, 105)] == [0, 4, 4, 8, 104, 108]


def test_hit_region_leaves_out_empty_cell():
    w = layout_window(["inside"] * 5)
    cat = Rect(10, 20, 112, 104)
    region = hit_region(w, [cat])
    cards = [r for r in region if r != cat]
    assert len(cards) == 5 and cat in region
    assert Rect(300, 256, 300, 128) not in region  # the empty sixth cell
    assert Rect(0, 256, 300, 128) in region


@pytest.mark.parametrize("dpr", [1, 1.25, 1.5, 1.75, 2])
def test_snap_lands_on_device_pixels(dpr):
    for v in (0, 8.3, 12.5, 100.01, 187.9):
        device = snap(v, dpr) * dpr
        assert abs(device - round(device)) < 1e-9


def test_snap_values():
    assert snap(8.3, 1) == 8
    assert snap(10.0, 1.5) == 10.0
    assert snap(10.1, 1.5) == pytest.approx(10.0)


def test_cell_size():
    assert [cell_size(d) for d in (1, 1.25, 1.5, 1.75, 2)] == [4, 5, 6, 7, 8]


def test_pane_style_theme_only():
    th = Theme()
    ps = resolve_pane_style(th, Customization(), False, 0, 1)
    assert (ps.background, ps.foreground, ps.dim) == ("#1b1b21", "#ececf1", "#8e8e98")
    assert ps.opacity == 100 and ps.bar_fill_override is None and ps.home == "inside"
    assert ps.palette == effective_palette(Customization())


def test_pane_style_account_override():
    th = Theme(card=CardStyle(opacity=70))
    cust = Customization(colorway="gray", overrides={"card_bg": "#112233", "bar_fill": "#445566",
                                                    "coat_base": "#010101"},
                         cat_home="inside_right")
    ps = resolve_pane_style(th, cust, False, 0, 2)
    assert ps.background == "#112233" and ps.opacity == 70
    assert ps.bar_fill_override == "#445566"
    assert ps.palette == effective_palette(cust) and ps.palette["o"] == "#010101"
    assert ps.home == "inside_right"


def test_pane_style_accent_wins():
    th = Theme(card=CardStyle(opacity=30))
    cust = Customization(overrides={"card_bg": "#112233"})
    ps = resolve_pane_style(th, cust, True, 0, 1)
    assert (ps.background, ps.foreground, ps.opacity) == (T.ACCENT_BG, T.ACCENT_FG, 100)
    assert ps.dim == th.text.dim


def test_pane_style_unreachable_home_falls_back():
    cust = Customization(cat_home="on_top")
    assert resolve_pane_style(Theme(), cust, False, 1, 3).home == "inside"


def _shifted(lay, dx):
    return ({n: Rect(r.x + dx, r.y, r.w, r.h) for n, r in lay.rects.items()},
            {n: Rect(r.x + dx, r.y, r.w, r.h) for n, r in lay.lines.items()})


@pytest.mark.parametrize("view", T.VIEWS)
@pytest.mark.parametrize("hidden", [(), ("percent", "reset"), ("section_labels", "status"),
                                    ("model_rows", "account_label")])
def test_inside_right_swaps_the_columns(view, hidden):
    th = Theme(text=TextStyle(show=frozenset(T.TEXT_ELEMENTS) - set(hidden)))
    left = layout_card(th, view, "inside")
    right = layout_card(th, view, "inside_right")
    assert right.layout == "beside_cat"
    assert right["cat_area"] == Rect(180, 8, 112, 112)
    rects, lines = _shifted(left, -122)
    rects["cat_area"] = right["cat_area"]
    assert dict(right.rects) == rects
    assert dict(right.lines) == lines


def test_full_width_inside_right_keeps_its_cat_area():
    lay = layout_card(FULL, "limits", "inside_right")
    assert lay.layout == "beside_cat" and "cat_area" in lay
    assert lay == layout_card(Theme(), "limits", "inside_right")
    assert layout_card(FULL, "limits", "on_top").layout == "full_width"


# --- theme_from_dict / theme_to_dict / rejected_fields --------------------

def test_theme_dict_round_trip():
    t = Theme(
        card=T.CardStyle(background="#101010", opacity=40, radius=6, border_width=2),
        bars=T.BarStyle(shape="rounded", height=6, mid_at=30, high_at=70),
        text=T.TextStyle(show=frozenset({"percent", "status"}), halo="on", halo_below=30),
    )
    raw = T.theme_to_dict(t)
    assert raw["text"]["show"] == ["percent", "status"]
    assert T.theme_from_dict(raw, Theme()) == t
    assert T.rejected_fields(raw, Theme()) == set()


def test_theme_to_dict_matches_what_save_writes(tmp_path):
    t = Theme(text=T.TextStyle(show=frozenset({"reset", "percent"})))
    T.save_theme(tmp_path, t)
    assert json.loads((tmp_path / T.THEME_FILENAME).read_text(encoding="utf-8")) == T.theme_to_dict(t)


def test_one_bad_field_falls_back_alone_and_is_reported():
    fallback = Theme(card=T.CardStyle(radius=4))
    raw = T.theme_to_dict(Theme(card=T.CardStyle(radius=99, border_width=3)))
    assert T.theme_from_dict(raw, fallback).card == T.CardStyle(radius=4, border_width=3)
    assert T.rejected_fields(raw, fallback) == {"card.radius"}


def test_bool_and_off_step_opacity_are_rejected():
    for bad in (True, 55, "50", None, 110):
        raw = {"card": {"opacity": bad}}
        assert T.theme_from_dict(raw, Theme(card=T.CardStyle(opacity=70))).card.opacity == 70
        assert T.rejected_fields(raw, Theme()) == {"card.opacity"}


def test_missing_fields_fall_back_without_being_rejected():
    fallback = Theme(card=T.CardStyle(radius=4))
    assert T.theme_from_dict({}, fallback) == fallback
    assert T.rejected_fields({"card": {}}, fallback) == set()


def test_inverted_thresholds_are_reported_on_both_fields():
    raw = {"bars": {"mid_at": 90, "high_at": 40}}
    bars = T.theme_from_dict(raw, Theme(bars=T.BarStyle(mid_at=20, high_at=60))).bars
    assert (bars.mid_at, bars.high_at) == (20, 60)
    assert T.rejected_fields(raw, Theme()) == {"bars.mid_at", "bars.high_at"}
    assert T.rejected_fields({"bars": {"mid_at": 50, "high_at": 50}}, Theme()) == {
        "bars.mid_at", "bars.high_at"}


def test_unknown_show_names_are_dropped_but_not_rejected():
    raw = {"text": {"show": ["percent", "nope", 3]}}
    assert T.theme_from_dict(raw, Theme()).text.show == frozenset({"percent"})
    assert T.rejected_fields(raw, Theme()) == set()


def test_non_list_show_and_non_dict_section_are_rejected():
    raw = {"text": {"show": "percent"}, "card": [1]}
    t = T.theme_from_dict(raw, Theme())
    assert t.text.show == Theme().text.show and t.card == T.CardStyle()
    assert T.rejected_fields(raw, Theme()) == {"text.show", "card"}


def test_theme_from_dict_ignores_a_non_dict():
    assert T.theme_from_dict(None, Theme()) == Theme()
    assert T.rejected_fields([], Theme()) == set()


def test_on_top_margin_holds_the_sprite_and_the_hop_headroom():
    from tokitty.theme import HOP_HEADROOM

    assert HOP_HEADROOM == 12
    assert layout_window(["on_top"]).margins[1] == SPRITE_H + HOP_HEADROOM
    for homes in (["inside"], ["inside_right", "inside"]):
        assert layout_window(homes).margins[1] == 0
    # reachable only: the headroom comes with the spot, not on top of it
    assert layout_window(["inside", "inside"], wander=True).margins[1] == SPRITE_H + HOP_HEADROOM
    assert layout_window(["on_top"]).pane_origins == ((0, SPRITE_H + HOP_HEADROOM),)


def test_cat_shadow_is_on_by_default_and_for_a_file_without_a_cat_section(tmp_path):
    from tokitty.theme import CatStyle, load_theme

    assert Theme().cat == CatStyle(shadow=True, wander=True)
    (tmp_path / "theme.json").write_text(json.dumps({"card": {"radius": 3}}), encoding="utf-8")
    loaded = load_theme(tmp_path)
    assert loaded.card.radius == 3 and loaded.cat.shadow is True


def test_cat_shadow_off_round_trips(tmp_path):
    from tokitty.theme import CatStyle, load_theme, save_theme, theme_to_dict

    theme = Theme(cat=CatStyle(shadow=False))
    assert theme_to_dict(theme)["cat"] == {"shadow": False, "wander": True, "time_of_day": True}
    save_theme(tmp_path, theme)
    assert json.loads((tmp_path / "theme.json").read_text(encoding="utf-8"))["cat"] == {"shadow": False, "wander": True, "time_of_day": True}
    assert load_theme(tmp_path).cat.shadow is False
    assert theme_from_dict({"cat": {"shadow": False}}, Theme()).cat.shadow is False


@pytest.mark.parametrize("bad", [1, 0, "no", None, [True]])
def test_cat_shadow_of_the_wrong_type_is_rejected_and_reported(bad):
    raw = {"cat": {"shadow": bad}}
    assert theme_from_dict(raw, Theme()).cat.shadow is True
    assert rejected_fields(raw, Theme()) == {"cat.shadow"}
    off = Theme(cat=replace(Theme().cat, shadow=False))
    assert theme_from_dict(raw, off).cat.shadow is False  # the fallback's value


def test_cat_wander_off_round_trips(tmp_path):
    from tokitty.theme import CatStyle, load_theme, save_theme, theme_to_dict

    theme = Theme(cat=CatStyle(wander=False))
    assert theme_to_dict(theme)["cat"] == {"shadow": True, "wander": False, "time_of_day": True}
    save_theme(tmp_path, theme)
    assert load_theme(tmp_path).cat.wander is False
    assert theme_from_dict({"cat": {"shadow": False}}, Theme()).cat.wander is True


def test_cat_time_of_day_defaults_on_and_off_round_trips(tmp_path):
    from tokitty.theme import CatStyle, load_theme, save_theme

    assert theme_from_dict({"cat": {"shadow": False}}, Theme()).cat.time_of_day is True
    save_theme(tmp_path, Theme(cat=CatStyle(time_of_day=False)))
    assert load_theme(tmp_path).cat.time_of_day is False


@pytest.mark.parametrize("bad", [1, 0, "no", None, [True]])
def test_cat_time_of_day_of_the_wrong_type_is_rejected_and_reported(bad):
    raw = {"cat": {"time_of_day": bad}}
    assert theme_from_dict(raw, Theme()).cat.time_of_day is True
    assert rejected_fields(raw, Theme()) == {"cat.time_of_day"}


@pytest.mark.parametrize("bad", [1, 0, "no", None, [True]])
def test_cat_wander_of_the_wrong_type_is_rejected_and_reported(bad):
    raw = {"cat": {"wander": bad}}
    assert theme_from_dict(raw, Theme()).cat.wander is True
    assert rejected_fields(raw, Theme()) == {"cat.wander"}


def test_cat_section_that_is_not_a_dict_is_reported():
    assert rejected_fields({"cat": True}, Theme()) == {"cat"}
    assert theme_from_dict({"cat": True}, Theme()).cat.shadow is True


# --- card design (#139) ---

def _look(theme):
    return {s: {k: getattr(getattr(theme, s), k) for k in keys} for s, keys in T.LOOK_FIELDS.items()}


def test_presets_hold_the_agreed_looks():
    assert _look(T.CLASSIC) == {
        "card": {"design": "classic", "background": "#1c1c22", "radius": 0, "border_width": 0,
                 "border_color": "#333340"},
        "bars": {"shape": "flat", "height": 8, "track": "#333340", "low": "#4caf6b",
                 "mid": "#e0a838", "high": "#e05252", "neutral": "#5a7fa8"},
        "text": {"foreground": "#f0f0f0", "dim": "#8a8a92"},
    }
    assert _look(T.REFINED)["card"] == {
        "design": "refined", "background": "#1b1b21", "radius": 10, "border_width": 1,
        "border_color": "#2e2e38"}
    assert T.design_preset("refined") is T.REFINED and T.design_preset("classic") is T.CLASSIC
    with pytest.raises(ValueError):
        T.design_preset("glass")


def test_design_is_validated_like_other_enums(tmp_path):
    _write(tmp_path, {"card": {"design": "classic"}})
    assert load_theme(tmp_path).card.design == "classic"
    for bad in ("glass", 3, None, True):
        raw = {"card": {"design": bad}}
        assert theme_from_dict(raw, Theme()).card.design == "refined"
        assert rejected_fields(raw, Theme()) == {"card.design"}
    assert theme_from_dict(raw, T.CLASSIC).card.design == "classic"


def test_design_round_trips(tmp_path):
    assert T.theme_to_dict(T.CLASSIC)["card"]["design"] == "classic"
    save_theme(tmp_path, T.CLASSIC)
    assert load_theme(tmp_path) == T.CLASSIC
    assert theme_from_dict(T.theme_to_dict(T.CLASSIC), Theme()) == T.CLASSIC


def _classic_file():
    """What a pre-#139 save of the all-default theme wrote: no design key."""
    raw = T.theme_to_dict(T.CLASSIC)
    del raw["card"]["design"]
    return raw


def test_a_pre_139_file_with_classic_values_moves_to_refined(tmp_path):
    _write(tmp_path, _classic_file())
    assert load_theme(tmp_path) == T.REFINED


def test_a_pre_139_file_keeps_what_the_user_changed(tmp_path):
    raw = _classic_file()
    raw["card"]["background"] = "#102030"
    raw["card"]["opacity"] = 70
    raw["bars"]["height"] = 5
    raw["bars"]["layout"] = "full_width"
    raw["text"]["show"] = ["percent"]
    raw["text"]["dim"] = "#8A8A92"  # classic, in another case
    _write(tmp_path, raw)
    t = load_theme(tmp_path)
    assert t.card.background == "#102030" and t.bars.height == 5
    assert (t.card.opacity, t.bars.layout, t.text.show) == (70, "full_width", frozenset({"percent"}))
    assert t.card.design == "refined" and t.card.radius == 10 and t.card.border_width == 1
    assert t.bars.shape == "rounded" and t.bars.track == "#2c2c35"
    assert t.text.dim == "#8e8e98" and t.text.foreground == "#ececf1"


def test_a_pre_139_file_with_only_some_sections(tmp_path):
    _write(tmp_path, {"cat": {"shadow": False}})
    assert load_theme(tmp_path) == replace(T.REFINED, cat=replace(T.REFINED.cat, shadow=False))
    _write(tmp_path, {"card": {"radius": 0}, "bars": []})
    t = load_theme(tmp_path)
    assert t.card.radius == 10 and t.bars == T.REFINED.bars  # bars: not a dict, rejected


def test_a_pre_139_bad_value_falls_back_to_refined(tmp_path):
    _write(tmp_path, {"card": {"background": "red", "border_width": False}})
    t = load_theme(tmp_path)
    assert t.card.background == "#1b1b21" and t.card.border_width == 1


def test_a_file_with_a_design_loads_as_written(tmp_path):
    raw = T.theme_to_dict(T.CLASSIC)
    _write(tmp_path, raw)
    assert load_theme(tmp_path) == T.CLASSIC
    _write(tmp_path, {"card": {"design": "classic"}})  # missing keys take the refined fallback
    t = load_theme(tmp_path)
    assert t.card.design == "classic" and t.card.radius == 10 and t.bars.height == 6
    _write(tmp_path, {"card": {"design": "refined", "radius": 0}})
    assert load_theme(tmp_path).card.radius == 0  # no migration once a design is saved


def test_loading_does_not_rewrite_the_file(tmp_path):
    _write(tmp_path, _classic_file())
    before = (tmp_path / "theme.json").read_bytes()
    load_theme(tmp_path)
    assert (tmp_path / "theme.json").read_bytes() == before


def test_apply_design_swaps_the_look_and_keeps_the_rest():
    mine = Theme(
        card=CardStyle(opacity=40, background="#102030"),
        bars=BarStyle(layout="full_width", mid_at=20, high_at=70, height=9, low="#00ff00"),
        text=TextStyle(show=frozenset({"percent"}), halo="on", halo_below=30, dim="#999999"),
        cat=T.CatStyle(shadow=False, wander=False))
    classic = T.apply_design(mine, "classic")
    assert _look(classic) == _look(T.CLASSIC)
    assert classic.card.opacity == 40
    assert classic.bars.layout == "full_width" and (classic.bars.mid_at, classic.bars.high_at) == (20, 70)
    assert classic.text.show == mine.text.show and classic.text.halo == "on"
    assert classic.text.halo_below == 30 and classic.cat == mine.cat
    assert T.apply_design(classic, "refined") == replace(mine, card=replace(
        T.REFINED.card, opacity=40), bars=replace(T.REFINED.bars, layout="full_width", mid_at=20,
                                                  high_at=70), text=replace(
        T.REFINED.text, show=mine.text.show, halo="on", halo_below=30), cat=mine.cat)
    assert T.apply_design(T.REFINED, "refined") == T.REFINED
    with pytest.raises(ValueError):
        T.apply_design(mine, "nope")


def test_refined_limits_rects():
    lay = layout_card(T.REFINED, "limits", "inside")  # bars 6 high
    assert lay.layout == "beside_cat"
    assert _xywh(lay, "session_label") == (132, 12, 118, 16)
    assert _xywh(lay, "session_percent") == (250, 12, 40, 16)
    assert _xywh(lay, "session_bar") == (132, 31, 158, 6)
    assert _xywh(lay, "session_reset") == (132, 40, 158, 14)
    assert _xywh(lay, "weekly_label") == (132, 58, 118, 16)
    assert lay["weekly_bar"].y == 77 and lay["weekly_reset"].y == 86
    assert lay.lines["session"] == Rect(132, 12, 158, 42)
    assert _xywh(lay, "status") == (132, 104, 158, 14)
    assert lay["account_label"] == Rect(132, 104, 158, 14) and lay["account_label"].right == 290
    # the stats column alone: the cat's area is as before
    assert _xywh(lay, "cat_area") == (8, 8, 112, 112)


def test_refined_row_steps_match_the_agreed_arrangement():
    lay = layout_card(replace(T.REFINED, bars=replace(T.REFINED.bars, height=8)), "limits", "inside")
    assert [lay[n].y for n in ("session_label", "session_bar", "session_reset", "weekly_label",
                               "weekly_bar", "weekly_reset", "status")] == [12, 31, 42, 60, 79, 90, 108]


def test_refined_models_rects():
    th = replace(T.REFINED, bars=replace(T.REFINED.bars, height=6))
    lay = layout_card(th, "models", "inside")
    assert [lay[f"model_name_{i}"].y for i in range(3)] == [12, 44, 76]
    assert [lay[f"model_bar_{i}"].y for i in range(3)] == [29, 61, 93]
    assert _xywh(lay, "model_name_0") == (132, 12, 98, 14)
    assert _xywh(lay, "model_value_0") == (230, 12, 60, 14)
    assert lay["status"].y == 108 and lay["account_label"].y == 108


def test_refined_hidden_elements_remove_their_rows():
    def lay_for(*hidden, view="limits"):
        th = replace(T.REFINED, text=TextStyle(show=frozenset(T.TEXT_ELEMENTS) - set(hidden)))
        return layout_card(th, view, "inside")

    full = lay_for()
    no_reset = lay_for("reset")
    assert "session_reset" not in no_reset
    assert no_reset["weekly_label"].y == full["weekly_label"].y - 18
    only_percent = lay_for("section_labels")
    assert "session_label" not in only_percent and only_percent["session_percent"] == Rect(250, 12, 40, 16)
    only_label = lay_for("percent")
    assert only_label["session_label"] == Rect(132, 12, 158, 16) and "session_percent" not in only_label
    bare = lay_for("section_labels", "percent")
    assert bare["session_bar"].y == 12
    assert bare["weekly_bar"].y == 12 + 6 + 3 + 18
    no_status = lay_for("status")
    assert "status" not in no_status and no_status["account_label"].y == full["account_label"].y
    no_account = lay_for("account_label")
    assert "account_label" not in no_account and no_account["status"] == full["status"]
    models = lay_for("model_rows", view="models")
    assert "model_name_0" not in models and models["model_bar_0"].y == 12
    assert models["model_bar_1"].y == 12 + 6 + 9


@pytest.mark.parametrize("view", T.VIEWS)
def test_refined_fits_the_card_for_every_bar_height(view):
    card = Rect(0, 0, CARD_WIDTH, CARD_HEIGHT)
    for height in range(4, 11):
        th = replace(T.REFINED, bars=replace(T.REFINED.bars, height=height))
        lay = layout_card(th, view, "inside")
        assert lay["status"].bottom <= CARD_HEIGHT, height
        leaves = [(n, r) for n, r in lay.rects.items() if n not in ("account_label", "cat_area")]
        for name, r in leaves:
            assert card.contains(r), (name, height)
        for i, (n1, r1) in enumerate(leaves):
            for n2, r2 in leaves[i + 1:]:
                assert not r1.intersects(r2), (n1, n2, height)


def test_refined_full_width_and_inside_right():
    th = replace(T.REFINED, bars=replace(T.REFINED.bars, layout="full_width"))
    wide = layout_card(th, "limits", "on_top")
    assert (wide["session_bar"].x, wide["session_bar"].w) == (8, 284)
    assert wide["account_label"].right == CARD_WIDTH - 10
    right = layout_card(T.REFINED, "limits", "inside_right")
    assert right["session_bar"].x == 10 and right["cat_area"].x == 180
