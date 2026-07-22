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


def test_pane_index_at_first_pane():
    from tokitty import ui
    assert ui.pane_index_at(0, 3) == 0
    assert ui.pane_index_at(127, 3) == 0


def test_pane_index_at_second_pane():
    from tokitty import ui
    assert ui.pane_index_at(128, 3) == 1
    assert ui.pane_index_at(255, 3) == 1


def test_pane_index_at_clamps_beyond_bottom():
    from tokitty import ui
    assert ui.pane_index_at(10000, 3) == 2
    assert ui.pane_index_at(384, 3) == 2


def test_pane_index_at_clamps_negative_y():
    from tokitty import ui
    assert ui.pane_index_at(-50, 3) == 0


def test_on_customization_changed_default_none_in_init_source():
    from tokitty import ui
    src = inspect.getsource(ui.TokittyWindow.__init__)
    lines = [line.strip() for line in src.splitlines() if "self.on_customization_changed" in line]
    assert lines and lines[0].endswith("= None")


@pytest.mark.gui
def test_build_menu_model_reads_shadow_state():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            model = window.build_menu_model(0)
            labels = [i.label for i in model if not i.separator]
            # No tray seam wired by default -> no "Show tray icon".
            assert labels == ["Coat", "Customize…", "Rename…",
                              "Refresh now", "Always in front", "Exit"]
            # always_on_top getter reads the plain-Python shadow, not a tk Var.
            aot = {i.label: i for i in model if not i.separator}["Always in front"]
            assert aot.checkbox() == window._always_on_top_bool
            # Exit action is the on_quit seam (default root.destroy).
            assert {i.label: i for i in model if not i.separator}["Exit"].action == window.on_quit
    finally:
        root.destroy()


@pytest.mark.gui
def test_toggle_always_on_top_flips_shadow():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            before = window._always_on_top_bool
            window._toggle_always_on_top()
            assert window._always_on_top_bool is (not before)
    finally:
        root.destroy()


@pytest.mark.gui
def test_tray_seam_adds_show_tray_item():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            state = {"enabled": True}
            window.tray_enabled = lambda: state["enabled"]
            window.on_toggle_tray = lambda: None
            labels = [i.label for i in window.build_menu_model(0) if not i.separator]
            assert "Show tray icon" in labels
    finally:
        root.destroy()
