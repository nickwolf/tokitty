import json

import pytest

from tokitty import colorways, sprites
from tokitty.app_controls import make_controls
from tokitty.app_core import make_usage_state
from tokitty.customize import Customization, effective_palette, load_customization, save_customization
from tokitty.migration import (
    COAT_OVERRIDES_MIGRATION_KEY,
    load_migration_state,
    migrate_coat_overrides,
    run_coat_overrides_migration,
)
from tokitty.settings import load_settings

TONES = {"coat": "#7d8aa6", "shade": "#626e88", "mark": "#4a5468", "light": "#e0e5ef", "ear": "#f2a7b8"}
OTHER = {"coat": "#112233", "shade": "#223344", "mark": "#334455", "light": "#445566", "ear": "#556677"}


def write(tmp_path, colorway_map):
    (tmp_path / colorways.COLORWAYS_FILENAME).write_text(
        json.dumps({"version": 1, "colorways": colorway_map}), encoding="utf-8")


# --- load / save / validation ---------------------------------------------

def test_missing_and_corrupt_file_are_empty(tmp_path):
    assert colorways.load_colorways(tmp_path) == {}
    (tmp_path / colorways.COLORWAYS_FILENAME).write_text("{nope", encoding="utf-8")
    assert colorways.load_colorways(tmp_path) == {}
    (tmp_path / colorways.COLORWAYS_FILENAME).write_text("[1]", encoding="utf-8")
    assert colorways.load_colorways(tmp_path) == {}


def test_round_trip_and_unknown_keys_ignored(tmp_path):
    write(tmp_path, {"custom-1": {"name": "Slate", **TONES, "extra": 1}})
    store = colorways.load_colorways(tmp_path)
    assert store == {"custom-1": {"name": "Slate", **TONES}}
    colorways.save_colorways(tmp_path, store)
    assert colorways.load_colorways(tmp_path) == store
    assert json.loads((tmp_path / colorways.COLORWAYS_FILENAME).read_text())["version"] == 1


def test_bad_entries_are_dropped(tmp_path):
    write(tmp_path, {
        "custom-1": {"name": "Good", **TONES},
        "custom-2": {"name": "BadHex", **TONES, "coat": "red"},
        "custom-3": {"name": "Missing", "coat": "#000000"},
        "orange": {"name": "BadId", **TONES},
        "custom-x": {"name": "BadId2", **TONES},
        "custom-4": {"name": "   ", **TONES},
        "custom-5": {"name": "good", **TONES},          # duplicate of custom-1
        "custom-6": {"name": "Orange", **TONES},        # built-in name
        "custom-7": {"name": "x" * 25, **TONES},
        "custom-8": "nope",
    })
    store = colorways.load_colorways(tmp_path)
    # Only a bad id, a bad tone or a non-dict entry drops an entry; bad names are repaired.
    assert list(store) == ["custom-1", "custom-4", "custom-5", "custom-6", "custom-7"]


def test_a_bad_or_colliding_name_is_repaired_not_dropped(tmp_path):
    write(tmp_path, {
        "custom-1": {"name": "Good", **TONES},
        "custom-2": {"name": "   ", **TONES},
        "custom-3": {"name": "good", **TONES},
        "custom-4": {"name": "Orange", **TONES},
        "custom-5": {"name": "orange", **TONES},
        "custom-6": {"name": "x" * 25, **TONES},
        "custom-7": {"name": 7, **TONES},
        "custom-8": {**TONES},
    })
    got = {cid: e["name"] for cid, e in colorways.load_colorways(tmp_path).items()}
    assert got == {
        "custom-1": "Good", "custom-2": "Custom 1", "custom-3": "good (custom)",
        "custom-4": "Orange (custom)", "custom-5": "orange (custom 2)",
        "custom-6": "x" * 15 + " (custom)", "custom-7": "Custom 2", "custom-8": "Custom 3",
    }
    assert all(len(n) <= colorways.MAX_NAME_LEN for n in got.values())
    assert colorways.load_colorways(tmp_path) == colorways.load_colorways(tmp_path)


@pytest.mark.parametrize("builtin", ["blue", "lilac", "cinnamon", "silver", "golden", "auburn", "seal",
                                       "midnight"])
def test_a_custom_named_like_a_new_builtin_survives(tmp_path, builtin):
    assert builtin in sprites.COLORWAYS
    write(tmp_path, {"custom-1": {"name": builtin.capitalize(), **TONES}})
    store = colorways.load_colorways(tmp_path)
    assert store["custom-1"]["name"] == f"{builtin.capitalize()} (custom)"
    assert colorways.validate_name(builtin.capitalize(), store) is not None  # still strict for saves


def test_validate_name_rules():
    store = {"custom-1": {"name": "Slate", **TONES}}
    assert colorways.validate_name("Fresh", store) is None
    assert colorways.validate_name(" ", store)
    assert colorways.validate_name("x" * 25, store)
    assert colorways.validate_name("x" * 24, store) is None
    assert colorways.validate_name("slate", store)
    assert colorways.validate_name("slate", store, exclude_id="custom-1") is None
    assert colorways.validate_name("GRAY", store)


def test_id_allocation_uses_lowest_free(tmp_path):
    assert colorways.add_colorway(tmp_path, "A", TONES) == "custom-1"
    assert colorways.add_colorway(tmp_path, "B", TONES) == "custom-2"
    colorways.add_colorway(tmp_path, "C", TONES)
    colorways.delete_colorway(tmp_path, "custom-2")
    assert colorways.add_colorway(tmp_path, "D", TONES) == "custom-2"


def test_add_and_update_validate(tmp_path):
    cid = colorways.add_colorway(tmp_path, " Slate ", TONES)
    assert colorways.load_colorways(tmp_path)[cid]["name"] == "Slate"
    with pytest.raises(ValueError):
        colorways.add_colorway(tmp_path, "slate", TONES)
    with pytest.raises(ValueError):
        colorways.add_colorway(tmp_path, "Other", {**TONES, "coat": "#12"})
    colorways.update_colorway(tmp_path, cid, name="Slate", tones=OTHER)
    assert colorways.load_colorways(tmp_path)[cid] == {"name": "Slate", **OTHER}
    with pytest.raises(KeyError):
        colorways.update_colorway(tmp_path, "custom-9", name="Z")
    with pytest.raises(ValueError):
        colorways.update_colorway(tmp_path, cid, name="brown")


def test_delete_rewrites_references_to_orange(tmp_path):
    a = colorways.add_colorway(tmp_path, "A", TONES)
    b = colorways.add_colorway(tmp_path, "B", OTHER)
    save_customization(tmp_path, {
        "x": Customization(colorway=a, pattern="tuxedo", label="X"),
        "y": Customization(colorway=b),
        "z": Customization(colorway="gray"),
    })
    assert colorways.delete_colorway(tmp_path, a) == 1
    assert list(colorways.load_colorways(tmp_path)) == [b]
    store = load_customization(tmp_path)
    assert store["x"] == Customization(colorway="orange", pattern="tuxedo", label="X")
    assert store["y"].colorway == b and store["z"].colorway == "gray"


# --- derive_from_coat -------------------------------------------------------

def test_derive_from_coat_is_deterministic_valid_hex():
    import re
    for cw in sprites.COLORWAYS.values():
        first = colorways.derive_from_coat(cw["coat"])
        assert first == colorways.derive_from_coat(cw["coat"])
        assert set(first) == {"shade", "mark", "light"}
        assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in first.values())
    assert colorways.derive_from_coat("#000000")["light"] != "#000000"
    assert colorways.derive_from_coat("#FFFFFF")["shade"] != "#ffffff"
    with pytest.raises(ValueError):
        colorways.derive_from_coat("nope")


# --- sprites registry and caches -------------------------------------------

def test_builtin_palettes_unchanged_by_registry():
    before = {(c, p): sprites.resolve_palette(c, p) for c in sprites.COLORWAYS for p in sprites.PATTERNS}
    sprites.set_custom_colorways({"custom-1": TONES})
    assert {(c, p): sprites.resolve_palette(c, p) for c in sprites.COLORWAYS for p in sprites.PATTERNS} == before
    assert list(sprites.COLORWAYS) == ["orange", "gray", "black", "white", "cream", "brown",
                                       "blue", "lilac", "cinnamon", "silver", "golden", "auburn", "seal",
                                       "midnight"]


def test_custom_id_resolves_and_dangling_falls_back_to_orange():
    sprites.set_custom_colorways({"custom-1": TONES})
    assert sprites.resolve_palette("custom-1", "solid")["o"] == TONES["coat"]
    assert sprites.resolve_palette("custom-1", "solid")["p"] == TONES["ear"]
    assert sprites.resolve_palette("custom-2", "solid") == sprites.resolve_palette("orange", "solid")
    assert sprites.colorway_tones("custom-2") == sprites.COLORWAYS["orange"]
    assert sprites.colorway_tones("custom-1") == TONES


def test_registry_names_and_membership():
    sprites.set_custom_colorways({"custom-10": OTHER, "custom-2": TONES, "bogus": TONES})
    assert sprites.colorway_names() == list(sprites.COLORWAYS) + ["custom-2", "custom-10"]
    assert sprites.is_colorway("custom-2") and sprites.is_colorway("gray")
    assert not sprites.is_colorway("custom-3") and not sprites.is_colorway("bogus") and not sprites.is_colorway(None)


def test_retone_never_returns_stale_palette():
    sprites.set_custom_colorways({"custom-1": TONES})
    custom = Customization(colorway="custom-1", pattern="solid")
    assert sprites.resolve_palette("custom-1", "solid")["o"] == TONES["coat"]
    assert effective_palette(custom)["o"] == TONES["coat"]
    sprites.set_custom_colorways({"custom-1": OTHER})
    assert sprites.resolve_palette("custom-1", "solid")["o"] == OTHER["coat"]
    assert effective_palette(custom)["o"] == OTHER["coat"]
    sprites.set_custom_colorways({})
    assert effective_palette(custom)["o"] == sprites.COLORWAYS["orange"]["coat"]


def test_load_customization_keeps_custom_id_before_registry_load(tmp_path):
    save_customization(tmp_path, {"a": Customization(colorway="custom-4"), "b": Customization(colorway="custom-x")})
    store = load_customization(tmp_path)
    assert store["a"].colorway == "custom-4"
    assert store["b"].colorway == "orange"
    save_customization(tmp_path, store)
    assert load_customization(tmp_path)["a"].colorway == "custom-4"


# --- migration --------------------------------------------------------------

def old(colorway="orange", **overrides):
    return Customization(colorway=colorway, pattern="solid", overrides=dict(overrides))


def test_migration_single_override(tmp_path):
    store, cws = migrate_coat_overrides(tmp_path, {"a": old("gray", coat_base="#112233", card_bg="#000000")})
    assert store["a"].colorway == "custom-1"
    assert store["a"].overrides == {"card_bg": "#000000"}
    assert cws["custom-1"] == {"name": "Gray custom", **{**sprites.COLORWAYS["gray"], "coat": "#112233"}}


def test_migration_both_overrides_and_bar_fill(tmp_path):
    store, cws = migrate_coat_overrides(
        tmp_path, {"a": old("brown", coat_base="#112233", coat_shade="#445566", bar_fill="#ffffff")})
    t = cws["custom-1"]
    assert (t["coat"], t["shade"]) == ("#112233", "#445566")
    assert (t["mark"], t["light"], t["ear"]) == tuple(sprites.COLORWAYS["brown"][k] for k in ("mark", "light", "ear"))
    assert store["a"].overrides == {"bar_fill": "#ffffff"}


def test_migration_dedupes_and_names_uniquely(tmp_path):
    store, cws = migrate_coat_overrides(tmp_path, {
        "a": old(coat_base="#112233"), "b": old(coat_base="#112233"), "c": old(coat_base="#445566"),
        "d": old("gray"),
    })
    assert store["a"].colorway == store["b"].colorway == "custom-1"
    assert store["c"].colorway == "custom-2"
    assert store["d"].colorway == "gray"
    assert [cws[c]["name"] for c in cws] == ["Orange custom", "Orange custom 2"]


def test_migration_reuses_existing_identical_colorway(tmp_path):
    cid = colorways.add_colorway(tmp_path, "Mine", {**sprites.COLORWAYS["orange"], "coat": "#112233"})
    store, cws = migrate_coat_overrides(tmp_path, {"a": old(coat_base="#112233")})
    assert store["a"].colorway == cid and list(cws) == [cid]


def test_migration_non_builtin_source_uses_orange(tmp_path):
    store, cws = migrate_coat_overrides(tmp_path, {"a": old("custom-7", coat_shade="#445566")})
    assert cws["custom-1"] == {"name": "Orange custom", **{**sprites.COLORWAYS["orange"], "shade": "#445566"}}


def test_run_migration_persists_marks_and_is_idempotent(tmp_path):
    save_customization(tmp_path, {"a": old(coat_base="#112233", card_bg="#000000")})
    store = run_coat_overrides_migration(tmp_path, load_customization(tmp_path))
    assert load_migration_state(tmp_path)[COAT_OVERRIDES_MIGRATION_KEY] is True
    assert load_customization(tmp_path) == store
    assert load_customization(tmp_path)["a"].overrides == {"card_bg": "#000000"}
    before = colorways.load_colorways(tmp_path)
    again = run_coat_overrides_migration(tmp_path, store)
    assert again == store and colorways.load_colorways(tmp_path) == before


def test_migration_rerun_after_crash_creates_no_duplicate(tmp_path):
    original = {"a": old(coat_base="#112233")}
    save_customization(tmp_path, original)
    # Crash after colorways.json was written but before customization.json.
    _, cws = migrate_coat_overrides(tmp_path, original)
    colorways.save_colorways(tmp_path, cws)
    store = run_coat_overrides_migration(tmp_path, load_customization(tmp_path))
    assert list(colorways.load_colorways(tmp_path)) == ["custom-1"]
    assert store["a"].colorway == "custom-1"


def test_migration_with_nothing_to_do_still_marks(tmp_path):
    store = {"a": old("gray")}
    assert run_coat_overrides_migration(tmp_path, store) == store
    assert load_migration_state(tmp_path)[COAT_OVERRIDES_MIGRATION_KEY] is True
    assert not (tmp_path / colorways.COLORWAYS_FILENAME).exists()


# --- app_controls -----------------------------------------------------------

def _controls(tmp_path):
    log = []
    store = {"k0": Customization()}
    units = [{"key": "k0", "account": None, "tag_kind": None, "pane": object()}]
    controls = make_controls(
        tmp_path, units, make_usage_state(load_settings(tmp_path)), store, load_settings(tmp_path),
        apply_customization=lambda pane, custom: log.append(("apply", custom.colorway)),
        on_layout_changed=lambda i, custom: log.append(("layout", custom.colorway)),
        distro_probe=type("P", (), {"get_running": staticmethod(lambda: [])})(),
        deck_fn=lambda: None, label_fn=lambda i: None, warn=lambda *a: None,
    )
    return controls, store, log


def test_controls_accept_custom_id_only_when_registered(tmp_path):
    controls, store, _ = _controls(tmp_path)
    controls.on_customization_changed(0, "colorway", "custom-1")
    assert store["k0"].colorway == "orange"
    cid = colorways.add_colorway(tmp_path, "Slate", TONES)
    controls.on_colorways_changed()
    controls.on_customization_changed(0, "colorway", cid)
    assert store["k0"].colorway == cid
    assert controls.colorway_store()[cid]["name"] == "Slate"


def test_on_colorways_changed_restyles_panes_after_delete(tmp_path):
    controls, store, log = _controls(tmp_path)
    cid = colorways.add_colorway(tmp_path, "Slate", TONES)
    controls.on_colorways_changed()
    controls.on_customization_changed(0, "colorway", cid)
    log.clear()
    colorways.delete_colorway(tmp_path, cid)
    controls.on_colorways_changed()
    assert store["k0"].colorway == "orange"
    assert log == [("apply", "orange"), ("layout", "orange")]
    assert not sprites.is_colorway(cid)


def test_randomize_draws_built_ins_only(tmp_path, monkeypatch):
    from tokitty import app_controls
    seen = []
    monkeypatch.setattr(app_controls, "random_look", lambda cws, pats: seen.append(list(cws)) or (cws[0], pats[0]))
    controls, _, _ = _controls(tmp_path)
    colorways.add_colorway(tmp_path, "Slate", TONES)
    controls.on_colorways_changed()
    controls.on_randomize(0)
    assert seen == [list(sprites.COLORWAYS)]


@pytest.mark.parametrize("pattern", list(sprites.PATTERNS))
@pytest.mark.parametrize("overrides", [
    {"coat_base": "#112233", "coat_shade": "#445566"}, {"coat_base": "#112233"}, {"coat_shade": "#445566"},
])
def test_migration_keeps_the_coat_cells_in_every_pattern(tmp_path, pattern, overrides):
    # coat_base painted o and coat_shade painted O, whatever tone the pattern
    # gave them; the migrated cat paints both the same as before.
    before = Customization(colorway="gray", pattern=pattern, overrides=overrides)
    expected = effective_palette(before)
    store, cws = migrate_coat_overrides(tmp_path, {"a": before})
    sprites.set_custom_colorways(cws)
    after = effective_palette(store["a"])
    assert (after["o"], after["O"]) == (expected["o"], expected["O"])


def test_migration_keeps_an_override_with_no_tone_to_move_into(tmp_path):
    # Van draws o with the fixed white, and colorpoint draws o and O with light.
    store, cws = migrate_coat_overrides(tmp_path, {
        "van": Customization(colorway="gray", pattern="van", overrides={"coat_base": "#112233"}),
        "point": Customization(colorway="gray", pattern="colorpoint",
                               overrides={"coat_base": "#112233", "coat_shade": "#445566"}),
    })
    assert store["van"].colorway == "gray" and store["van"].overrides == {"coat_base": "#112233"}
    assert cws[store["point"].colorway]["light"] == "#112233"
    assert store["point"].overrides == {"coat_shade": "#445566"}


def test_new_ids_skip_ids_customizations_still_point_at(tmp_path):
    # A colorways.json that lost custom-1 leaves the cat pointing at it.
    save_customization(tmp_path, {"a": Customization(colorway="custom-1")})
    assert colorways.add_colorway(tmp_path, "New", TONES) == "custom-2"
    store, cws = migrate_coat_overrides(tmp_path / "other", {
        "a": Customization(colorway="custom-1"), "b": old(coat_base="#112233")})
    assert store["b"].colorway == "custom-2"
