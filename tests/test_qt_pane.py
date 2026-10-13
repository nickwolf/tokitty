"""QtPane is plain state, so it runs headless with no PySide6."""
import subprocess
import sys

from tokitty import sprites
from tokitty.customize import Customization, effective_palette
from tokitty.qt.pane import QtPane, fit_tag


def _render(pane, **kw):
    pane.render("working", 42, 67, "2h", "Mon", "tag", None, None, False, **kw)


def test_import_has_no_toolkit():
    code = (
        "import sys\n"
        "sys.modules['tkinter'] = None\n"
        "sys.modules['PySide6'] = None\n"
        "import tokitty.qt.pane\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_state_change_calls_on_change():
    calls = []
    pane = QtPane(on_change=lambda: calls.append(1))
    _render(pane)
    pane.render_usage("working", object())
    pane.set_appearance(label="x")
    pane.draw_next_frame()
    assert len(calls) == 4


def test_render_records_state_and_status_priority():
    pane = QtPane()
    pane.render("working", 42, 67, "2h", "Mon", "drv", "credits", "hint", True,
                stale_text="stale", tool_label="Bash", accent=True, projection_text="proj")
    assert (pane._current_state, pane._driving_tag, pane._tool_label) == ("working", "drv", "Bash")
    assert pane._accent and pane._dimmed and pane._view_mode == "limits"
    assert (pane.session_pct, pane.weekly_pct) == (42, 67)
    assert pane.status_text == "hint"


def test_accent_callback_fires_on_render():
    seen = []
    pane = QtPane()
    pane.on_accent_changed = lambda: seen.append(pane._accent)
    _render(pane, accent=True)
    assert seen == [True]


def test_set_appearance_none_leaves_slots_unchanged():
    pane = QtPane(Customization(overrides={"card_bg": "#112233", "bar_fill": "#445566"}),
                  label="cat")
    palette = pane.palette
    pane.set_appearance()
    assert pane.palette == palette and pane._label == "cat"
    assert pane.customization.overrides == {"card_bg": "#112233", "bar_fill": "#445566"}


def test_set_appearance_updates_the_customization():
    pane = QtPane()
    pane.set_appearance(card_bg="#112233", bar_fill="#445566", label="Ada",
                        colorway="gray", pattern="tuxedo")
    c = pane.customization
    assert c.overrides == {"card_bg": "#112233", "bar_fill": "#445566"}
    assert (c.label, c.colorway, c.pattern) == ("Ada", "gray", "tuxedo")
    assert (pane._label, pane._colorway, pane._pattern) == ("Ada", "gray", "tuxedo")
    assert pane.palette == effective_palette(c)
    pane.set_appearance(palette={"o": "#000000"}, bar_fill="")
    assert pane.palette == {"o": "#000000"}
    assert "bar_fill" not in pane.customization.overrides


def test_tk_default_card_bg_clears_the_override():
    pane = QtPane(Customization(overrides={"card_bg": "#112233"}))
    pane.set_appearance(card_bg="#1c1c22")
    assert "card_bg" not in pane.customization.overrides


def test_draw_next_frame_cycles_like_the_tk_pane():
    pane = QtPane()
    state = next(s for s in sprites.ALL_STATES if len(sprites.get_frames(s)) > 1)
    _render(pane)
    pane._current_state = state
    frames = sprites.get_frames(state)
    shown = []
    for _ in range(len(frames) + 1):
        pane.draw_next_frame()
        shown.append(pane.current_frame()[0])
    assert shown == [frames[i % len(frames)] for i in range(len(frames) + 1)]


def test_fit_tag():
    assert fit_tag("Bash") == "Bash"
    assert fit_tag("a" * 20) == "a" * 11 + "…"


def test_set_customization_replaces_the_whole_customization_and_keeps_the_label():
    calls = []
    pane = QtPane(Customization(cat_home="inside_right", overrides={"coat_base": "#112233"}),
                  on_change=lambda: calls.append(1))
    pane.set_appearance(label="Shown")
    custom = Customization(colorway="gray", pattern="solid", cat_home="on_top")
    pane.set_customization(custom)

    assert pane.customization is custom
    assert pane.customization.cat_home == "on_top"
    assert (pane._colorway, pane._pattern) == ("gray", "solid")
    assert pane.palette == effective_palette(custom)  # the old coat override is gone
    assert pane._label == "Shown"                      # custom.label is "", the shown label stays
    assert calls

    pane.set_customization(custom, label="Other")
    assert pane._label == "Other"
