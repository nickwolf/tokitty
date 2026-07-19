"""Layout-constant tests that must not require a display. ui.py imports
tkinter at module level, so only run what's importable headlessly."""
import inspect

import pytest

tk = pytest.importorskip("tkinter")


def test_pane_height_and_card_width_constants():
    from tokitty import ui
    assert ui.PANE_HEIGHT == 128
    assert ui.CARD_WIDTH == 300


def test_window_height_scales_with_pane_count():
    from tokitty import ui
    assert ui.card_height(1) == 128
    assert ui.card_height(2) == 256


def test_pane_init_signature_has_appearance_kwargs_with_none_defaults():
    from tokitty import ui
    sig = inspect.signature(ui.Pane.__init__)
    params = sig.parameters
    assert params["palette"].default is None
    assert params["card_bg"].default is None
    assert params["bar_fill"].default is None
    assert params["label"].default == ""


def test_pane_set_appearance_signature_defaults_all_none():
    from tokitty import ui
    sig = inspect.signature(ui.Pane.set_appearance)
    params = sig.parameters
    assert params["palette"].default is None
    assert params["card_bg"].default is None
    assert params["bar_fill"].default is None
    assert params["label"].default is None


def test_resolve_bar_fill_returns_override_when_set():
    from tokitty import ui
    assert ui.resolve_bar_fill(10, "#abcdef") == "#abcdef"
    assert ui.resolve_bar_fill(90, "#abcdef") == "#abcdef"


def test_resolve_bar_fill_falls_back_to_bar_color_when_no_override():
    from tokitty import ui
    from tokitty.display import bar_color
    assert ui.resolve_bar_fill(10, None) == bar_color(10)
    assert ui.resolve_bar_fill(90, None) == bar_color(90)
