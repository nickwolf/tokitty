"""Layout-constant tests that must not require a display. ui.py imports
tkinter at module level, so only run what's importable headlessly."""
import inspect
import sys

import pytest

tk = pytest.importorskip("tkinter")


def test_pane_height_and_card_width_constants():
    from tokitty import ui
    assert ui.PANE_HEIGHT == 128
    assert ui.CARD_WIDTH == 300


def test_grid_size_n1():
    from tokitty.ui import grid_size
    assert grid_size(1) == (300, 128, 1)


def test_grid_size_n4():
    from tokitty.ui import grid_size
    assert grid_size(4) == (300, 512, 1)


def test_grid_size_n5():
    from tokitty.ui import grid_size
    assert grid_size(5) == (600, 384, 2)


def test_grid_size_n8():
    from tokitty.ui import grid_size
    assert grid_size(8) == (600, 512, 2)


def test_grid_size_n9():
    from tokitty.ui import grid_size
    assert grid_size(9) == (900, 384, 3)


def test_grid_size_n12():
    from tokitty.ui import grid_size
    assert grid_size(12) == (900, 512, 3)


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
    assert ui.pane_index_at(0, 0, 3, 1) == 0
    assert ui.pane_index_at(0, 127, 3, 1) == 0


def test_pane_index_at_second_pane():
    from tokitty import ui
    assert ui.pane_index_at(0, 128, 3, 1) == 1
    assert ui.pane_index_at(0, 255, 3, 1) == 1


def test_pane_index_at_beyond_pane_count_returns_none():
    # Single column (cols=1): rows 3+ don't exist for pane_count=3, so
    # there is no clamping to the last real pane anymore -- out-of-range
    # rows are blank grid cells, not the bottom pane.
    from tokitty import ui
    assert ui.pane_index_at(0, 10000, 3, 1) is None
    assert ui.pane_index_at(0, 384, 3, 1) is None


def test_pane_index_at_negative_coordinates_return_none_not_clamped():
    from tokitty import ui
    assert ui.pane_index_at(0, -50, 3, 1) is None
    assert ui.pane_index_at(-50, 0, 3, 1) is None


def test_pane_index_at_n5_x350_y50_selects_pane_1_not_0():
    from tokitty.ui import pane_index_at
    assert pane_index_at(350, 50, pane_count=5, cols=2) == 1


def test_pane_index_at_n5_x50_y50_selects_pane_0():
    from tokitty.ui import pane_index_at
    assert pane_index_at(50, 50, pane_count=5, cols=2) == 0


def test_pane_index_at_n5_ragged_last_row_blank_cell_is_none():
    # N=5, cols=2 -> 3 rows, row 2 has only column 0 filled (index 4);
    # row 2 column 1 would be index 5, which does not exist.
    from tokitty.ui import pane_index_at
    assert pane_index_at(350, 300, pane_count=5, cols=2) is None


def test_pane_index_at_negative_coordinates_return_none():
    from tokitty.ui import pane_index_at
    assert pane_index_at(-1, 50, pane_count=5, cols=2) is None
    assert pane_index_at(50, -1, pane_count=5, cols=2) is None


def test_on_customization_changed_default_none_in_init_source():
    from tokitty import ui
    src = inspect.getsource(ui.TokittyWindow.__init__)
    lines = [line.strip() for line in src.splitlines() if "self.on_customization_changed" in line]
    assert lines and lines[0].endswith("= None")


def test_autostart_seam_defaults_none_in_init_source():
    from tokitty import ui

    src = inspect.getsource(ui.TokittyWindow.__init__)
    for attr in ("self.autostart_enabled", "self.on_toggle_autostart"):
        lines = [line.strip() for line in src.splitlines() if attr in line]
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
            # No open_settings seam wired by default -> no "Settings…".
            assert labels == ["Refresh now", "Always in front", "Exit"]
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
def test_open_settings_seam_adds_the_item_and_passes_the_pane():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=3)
            opened = []
            window.open_settings = opened.append
            model = window.build_menu_model(2)
            item = {i.label: i for i in model if not i.separator}["Settings…"]
            item.action()
            assert opened == [2]
            # Pane-specific items are gone from the menu entirely.
            labels = [i.label for i in model if not i.separator]
            assert not {"Colorway", "Pattern", "Randomize", "Customize…", "Rename…", "Set budget…"} & set(labels)
    finally:
        root.destroy()


@pytest.mark.gui
def test_update_seams_add_the_update_items_and_the_item_hides_without_a_release():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    def tk_labels(window):
        window._rebuild_context_menu()
        menu = window.menu
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) != "separator"]

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            found = {"label": None}
            window.update_available_label = lambda: found["label"]
            window.on_install_update = lambda: None
            window.on_check_updates = lambda: None
            window.update_check_enabled = lambda: True
            window.on_toggle_update_check = lambda: None
            # Hidden in the Tk menu until a newer release is known.
            labels = tk_labels(window)
            assert labels[0] == "Refresh now"
            assert labels[-1] == "Exit"
            found["label"] = "Update to v0.3.0…"
            assert tk_labels(window)[0] == "Update to v0.3.0…"
            # The tray gets the same model, with the getter intact.
            assert window.build_menu_model(0)[0].dynamic_label() == "Update to v0.3.0…"
    finally:
        root.destroy()


@pytest.mark.gui
def test_blank_cell_rebuilds_menu_with_settings_on_pane_zero():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=5)
            opened = []
            window.open_settings = opened.append

            window._menu_pane_index = None   # right-click on a blank cell
            window._rebuild_context_menu()

            labels = []
            for i in range(window.menu.index("end") + 1):
                try:
                    labels.append(window.menu.entrycget(i, "label"))
                except tk.TclError:
                    pass  # separator: no label to read
            assert labels == ["Refresh now", "Always in front", "Settings…", "Exit"]
            window.menu.invoke(labels.index("Settings…") + 1)   # +1: the separator before it
            assert opened == [0]
    finally:
        root.destroy()


def test_pane_render_accepts_projection_text_defaulting_to_none():
    from tokitty import ui
    sig = inspect.signature(ui.Pane.render)
    assert "projection_text" in sig.parameters
    assert sig.parameters["projection_text"].default is None


def test_ui_uses_the_shared_status_priority_helper():
    """The status line must go through display.resolve_status_text rather
    than re-implementing the hint > credits > projection order.

    Asserted by source inspection because the behaviour it guards lives
    inside a tk.Label configure call -- checking it any other way needs a
    live display, which would force this test into the `gui` marker and
    out of the default headless run. Same trade the signature-inspection
    tests above already make.
    """
    from tokitty import ui
    source = inspect.getsource(ui.Pane.render)
    assert "resolve_status_text" in source


def _bare_window():
    """A TokittyWindow shell with only the attributes _after_menu_action
    reads, so this stays a headless test with no real Tk root."""
    from tokitty.ui import TokittyWindow

    window = TokittyWindow.__new__(TokittyWindow)
    window.on_menu_action_done = None
    window.settings_refresh = None
    return window


def test_after_menu_action_runs_the_action_then_the_done_hook():
    order = []
    window = _bare_window()
    window.on_menu_action_done = lambda: order.append("done")
    wrapped = window._after_menu_action(lambda: order.append("action"))
    wrapped()
    assert order == ["action", "done"]


def test_after_menu_action_without_a_done_hook_still_runs_the_action():
    ran = []
    window = _bare_window()
    wrapped = window._after_menu_action(lambda: ran.append("action"))
    wrapped()
    assert ran == ["action"]


def test_notify_state_changed_runs_the_tray_hook_then_the_settings_refresh():
    window = _bare_window()
    order = []
    window.on_menu_action_done = lambda: order.append("tray")
    window.settings_refresh = lambda: order.append("settings")
    window.notify_state_changed()
    assert order == ["tray", "settings"]


def test_notify_state_changed_with_no_hooks_is_a_noop():
    window = _bare_window()
    window.settings_refresh = None
    window.notify_state_changed()


def test_after_menu_action_also_refreshes_settings():
    window = _bare_window()
    order = []
    window.settings_refresh = lambda: order.append("settings")
    window._after_menu_action(lambda: order.append("action"))()
    assert order == ["action", "settings"]


def test_after_menu_action_passes_none_through():
    assert _bare_window()._after_menu_action(None) is None


@pytest.mark.gui
def test_select_opacity_updates_the_window_level_and_saves():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    from tokitty.transparency import LEVELS
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            saved = []
            window.on_opacity_changed = saved.append

            assert window.opacity() == 100
            window._select_opacity(60)
            assert window.opacity() == 60
            assert saved == [60]
            assert window.opacity() in LEVELS
    finally:
        root.destroy()


def _render_kwargs(**overrides):
    kwargs = dict(state="working", session_pct=10.0, weekly_pct=20.0,
                  session_reset_text="1h", weekly_reset_text="2d", driving_tag="",
                  credits_text=None, hint_text=None, dimmed=False)
    kwargs.update(overrides)
    return kwargs


@pytest.mark.gui
def test_keyed_canvas_keeps_the_key_colour_through_an_accent_render():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import ACCENT_BG, Pane
    from tokitty.transparency import KEY_COLOR

    root = tk.Tk()
    try:
        card = tk.Frame(root)
        content = tk.Frame(root)
        pane = Pane(card, content)
        pane.render(**_render_kwargs(accent=True))
        # The accent recolours the card surface and its labels, but the cat
        # canvas is on the keyed window: painting it would put an opaque card
        # back behind the cat.
        assert str(pane.canvas.cget("bg")) == KEY_COLOR
        assert str(pane.session_label.cget("bg")) == ACCENT_BG
        pane.set_appearance(card_bg="#123456")
        assert str(pane.canvas.cget("bg")) == KEY_COLOR
    finally:
        root.destroy()


@pytest.mark.gui
def test_unkeyed_canvas_still_follows_the_card_colour():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import ACCENT_BG, Pane

    root = tk.Tk()
    try:
        frame = tk.Frame(root)
        pane = Pane(frame)
        pane.render(**_render_kwargs(accent=True))
        assert str(pane.canvas.cget("bg")) == ACCENT_BG
    finally:
        root.destroy()


@pytest.mark.gui
def test_one_accented_pane_holds_the_window_opaque():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=2, opacity=50)
            applied = []
            window.root.attributes = lambda *args: applied.append(args)

            # Panes render one after another. The accented pane renders first
            # and the ordinary one second, which is exactly the order that
            # would let the last pane win if alpha were applied per pane.
            window.panes[0].render(**_render_kwargs(accent=True))
            window.panes[1].render(**_render_kwargs(accent=False))
            window._flush_opacity()
            assert applied[-1] == ("-alpha", 1.0)

            window.panes[0].render(**_render_kwargs(accent=False))
            window._flush_opacity()
            assert applied[-1] == ("-alpha", 0.5)
    finally:
        root.destroy()


def test_known_tool_labels_are_never_truncated():
    from tokitty.activity import _TOOL_LABELS
    from tokitty.ui import fit_tag

    for label in _TOOL_LABELS.values():
        assert fit_tag(label) == label


def test_an_unknown_tool_name_is_cut_to_fit_the_canvas():
    from tokitty.ui import TOOL_LABEL_MAX, fit_tag

    fitted = fit_tag("SomeVeryLongMcpToolName")
    assert len(fitted) == TOOL_LABEL_MAX
    assert fitted.endswith("…")


@pytest.mark.gui
def test_choosing_an_item_from_the_tk_menu_resyncs_the_tray():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1, opacity=100)
            resyncs = []
            window.on_menu_action_done = lambda: resyncs.append(window.opacity())

            # pystray builds its menu once and the win32 backend caches the
            # native HMENU, so anything changed from this menu is invisible in
            # the tray until update_menu runs (PR #54).
            window._rebuild_context_menu()
            before = window._always_on_top_bool
            window.menu.invoke(window.menu.index("Always in front"))

            assert window._always_on_top_bool is (not before)
            assert resyncs == [100]
    finally:
        root.destroy()


# --- per-model view ----------------------------------------------------


def test_model_rows_clear_the_account_label_and_the_status_line():
    """The account label is anchored top-right at y=4 and an 8pt label is
    roughly 13px tall, so a right-aligned figure at y=8 would sit under
    it. The bottom bar must also clear the status line at y=108."""
    from tokitty.ui import MODEL_BAR_OFFSET, MODEL_ROW_Y

    assert MODEL_ROW_Y[0] >= 18
    assert MODEL_ROW_Y[-1] + MODEL_BAR_OFFSET + 8 <= 108


def test_model_rows_do_not_overlap_each_other():
    from tokitty.ui import MODEL_BAR_OFFSET, MODEL_ROW_Y

    for upper, lower in zip(MODEL_ROW_Y, MODEL_ROW_Y[1:]):
        assert upper + MODEL_BAR_OFFSET + 8 <= lower


def test_row_slots_matches_the_number_of_laid_out_rows():
    from tokitty.usage_display import ROW_SLOTS
    from tokitty.ui import MODEL_ROW_Y

    assert len(MODEL_ROW_Y) == ROW_SLOTS


def test_render_usage_takes_no_poll_derived_arguments():
    """The per-model view has to be fully legible with no credentials and
    no subscription, so nothing PollResult-shaped may reach it."""
    import inspect

    from tokitty.ui import Pane

    params = set(inspect.signature(Pane.render_usage).parameters)
    assert not params & {
        "session_pct",
        "weekly_pct",
        "session_reset_text",
        "weekly_reset_text",
        "credits_text",
        "hint_text",
        "projection_text",
    }


@pytest.mark.gui
def test_switching_views_hides_the_other_views_widgets():
    import tkinter as tk

    from tokitty.ui import Pane
    from tokitty.usage_display import build_view

    root = tk.Tk()
    try:
        frame = tk.Frame(root)
        frame.place(x=0, y=0)
        pane = Pane(frame)

        pane.render(
            state="content", session_pct=10.0, weekly_pct=20.0,
            session_reset_text="9pm", weekly_reset_text="Fri", driving_tag="",
            credits_text=None, hint_text=None, dimmed=False,
        )
        assert pane.session_bar_bg.winfo_manager() == "place"
        assert pane.model_bars[0].winfo_manager() == ""

        pane.render_usage(state="content", usage_view=build_view(None))
        assert pane.session_bar_bg.winfo_manager() == ""
        assert pane.model_bars[0].winfo_manager() == "place"

        pane.render(
            state="content", session_pct=10.0, weekly_pct=20.0,
            session_reset_text="9pm", weekly_reset_text="Fri", driving_tag="",
            credits_text=None, hint_text=None, dimmed=False,
        )
        assert pane.session_bar_bg.winfo_manager() == "place"
        assert pane.model_bars[0].winfo_manager() == ""
    finally:
        root.destroy()


@pytest.mark.gui
def test_unused_model_rows_render_blank_rather_than_stale():
    import tkinter as tk
    from datetime import datetime, timezone

    from tokitty.ui import Pane
    from tokitty.usage_display import build_view
    from tokitty.usage_scan import STATUS_OK, ModelUsage, UsageBreakdown

    def usage(models):
        return UsageBreakdown(
            status=STATUS_OK,
            window="7d",
            window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
            models=tuple(models),
            total_cost_usd=sum(m.cost_usd or 0 for m in models),
            total_tokens=sum(m.total_tokens for m in models),
            scanned_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        )

    def model(name, cost):
        return ModelUsage(name, 100, 0, 0, 0, 0, cost)

    root = tk.Tk()
    try:
        frame = tk.Frame(root)
        frame.place(x=0, y=0)
        pane = Pane(frame)

        pane.render_usage(
            state="content",
            usage_view=build_view(usage([model("claude-opus-5", 3.0), model("claude-sonnet-5", 1.0)])),
        )
        assert pane.model_name_labels[2].cget("text") == ""

        pane.render_usage(state="content", usage_view=build_view(usage([model("claude-opus-5", 3.0)])))
        assert pane.model_name_labels[1].cget("text") == ""
    finally:
        root.destroy()


def test_cell_size_scales_both_dimensions():
    from tokitty.ui import cell_size
    assert cell_size(1.0) == (300, 128)
    assert cell_size(1.5) == (450, 192)
    assert cell_size(1.25) == (375, 160)


def test_grid_size_uses_one_rounded_cell_for_every_column():
    # The grid must be an exact multiple of the cell the frames are placed
    # with. Rounding CARD_WIDTH * cols separately from CARD_WIDTH would let
    # the window be a pixel wider or narrower than its own panes at a
    # custom scale.
    from tokitty.ui import cell_size, grid_size
    scale = 106 / 96.0
    card_w, pane_h = cell_size(scale)
    width, height, cols = grid_size(5, scale)
    assert cols == 2
    assert width == card_w * 2
    assert height == pane_h * 3


def test_pane_index_at_uses_the_scaled_cell():
    from tokitty.ui import cell_size, pane_index_at
    card_w, pane_h = cell_size(1.5)
    assert card_w == 450 and pane_h == 192
    # A point that is pane 0 at 100% is still pane 0 at 150% only if the
    # hit test is told the scaled cell; with the logical constants it would
    # land in pane 1.
    assert pane_index_at(0, 150, 3, 1, card_w, pane_h) == 0
    assert pane_index_at(0, 192, 3, 1, card_w, pane_h) == 1
    assert pane_index_at(0, 150, 3, 1) == 1


def test_sprite_bounds_fill_the_canvas_at_every_scale():
    # The rounded-cell-size approach this replaced froze the sprite at 112px
    # for a 123px canvas at 110%, because round(4 * 1.104) is 4. Flooring
    # had the same defect. Boundary mapping tracks the canvas exactly.
    from tokitty.ui import CAT_CANVAS_SIZE, sprite_bounds
    for scale in (1.0, 1.1, 106 / 96.0, 1.25, 1.5, 1.75, 2.0, 2.4):
        xs, ys = sprite_bounds(28, 28, scale)
        canvas = round(CAT_CANVAS_SIZE * scale)
        assert xs[-1] - xs[0] == canvas, scale
        assert xs[-1] <= canvas, scale
        assert ys[-1] <= canvas, scale


def test_sprite_bounds_leave_no_gaps_between_cells():
    from tokitty.ui import sprite_bounds
    xs, ys = sprite_bounds(28, 26, 106 / 96.0)
    assert all(b >= a for a, b in zip(xs, xs[1:]))
    assert len(xs) == 29 and len(ys) == 27
    # Every cell boundary is the next cell's start, so no row of background
    # shows through between two filled pixels.
    assert xs == sorted(xs)


def test_a_shorter_sprite_stays_centred_vertically():
    from tokitty.ui import CAT_CANVAS_SIZE, sprite_bounds
    scale = 1.5
    xs, ys = sprite_bounds(28, 26, scale)
    canvas = round(CAT_CANVAS_SIZE * scale)
    assert ys[0] == (canvas - (ys[-1] - ys[0])) // 2


def test_sprite_bounds_are_pixel_identical_to_the_unscaled_arithmetic():
    # At 100% the new boundary mapping has to reproduce the old
    # x_off + i * SCALE exactly, so a machine at 96 dpi sees no change at
    # all from the scaling pass.
    from tokitty.ui import CAT_CANVAS_SIZE, SCALE, sprite_bounds
    cols, rows = 28, 26
    xs, ys = sprite_bounds(cols, rows, 1.0)
    x_off = max((CAT_CANVAS_SIZE - cols * SCALE) // 2, 0)
    y_off = max((CAT_CANVAS_SIZE - rows * SCALE) // 2, 0)
    assert xs == [x_off + i * SCALE for i in range(cols + 1)]
    assert ys == [y_off + i * SCALE for i in range(rows + 1)]


def test_unscaled_grid_and_hit_test_are_unchanged():
    from tokitty.ui import CARD_WIDTH, PANE_HEIGHT, cell_size, grid_size, pane_index_at
    assert cell_size(1.0) == (CARD_WIDTH, PANE_HEIGHT)
    assert grid_size(5) == grid_size(5, 1.0) == (600, 384, 2)
    assert pane_index_at(350, 50, 5, 2) == pane_index_at(350, 50, 5, 2, 300, 128) == 1


def test_context_menu_opens_on_right_click_only_off_macos():
    from tokitty.ui import context_menu_sequences

    assert context_menu_sequences("win32") == ["<Button-3>"]
    assert context_menu_sequences("linux") == ["<Button-3>"]


def test_context_menu_also_opens_on_control_click_and_button_2_on_macos():
    from tokitty.ui import context_menu_sequences

    # Aqua Tk has reported the right button as Button-2 or Button-3 depending
    # on the version, and Control-click is the one-button way to right-click.
    assert set(context_menu_sequences("darwin")) == {"<Button-2>", "<Button-3>", "<Control-Button-1>"}


@pytest.mark.gui
def test_every_context_menu_sequence_is_bound():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow, context_menu_sequences
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            TokittyWindow(root, Path(d), pane_count=1)
            for sequence in context_menu_sequences():
                assert root.bind_all(sequence), sequence
    finally:
        root.destroy()


@pytest.mark.gui
def test_app_menu_settings_command_opens_settings_on_pane_zero():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=3)
            opened = []
            window.open_settings = opened.append
            window.register_app_menu_settings()
            # What Aqua Tk calls for Tokitty ▸ Settings… and Cmd-comma.
            root.tk.call("::tk::mac::ShowPreferences")
            assert opened == [0]
    finally:
        root.destroy()


_FROZEN_ABOUT_CHECK = """
import sys, tempfile, tkinter as tk
from pathlib import Path
import AppKit
from tokitty.ui import TokittyWindow

root = tk.Tk()
window = TokittyWindow(root, Path(tempfile.mkdtemp()), pane_count=1)
window.register_about_panel(frozen=True)
calls = []
root.tk.call("trace", "add", "execution", "tkAboutDialog", "enter", root.register(lambda *_: calls.append(1)))
root.tk.call("tkAboutDialog")
root.update()
panels = [w for w in AppKit.NSApp().windows() if w.className() == "NSPanel" and w.isVisible()]

def texts(view):
    out = [str(view.stringValue())] if view.isKindOfClass_(AppKit.NSTextField) else []
    for sub in view.subviews():
        out += texts(sub)
    return out

versions = [t for p in panels for t in texts(p.contentView()) if t.startswith("Version")]
for panel in panels:
    panel.orderOut_(None)
print(versions)
print(f"calls={len(calls)} panels={len(panels)}")
"""


@pytest.mark.gui
def test_about_item_in_a_frozen_build_opens_the_native_panel_once():
    # No stand-in for the native call: in Tk 9 ::tk::mac::standardAboutPanel
    # sends orderFrontStandardAboutPanel:, which Tk routes back to
    # tkAboutDialog, so calling it from the handler recursed and showed nothing.
    # Its own process, since AppKit redraws windows left by earlier tests' Tk
    # roots and crashes on them; the app only ever has one root.
    if sys.platform != "darwin":
        pytest.skip("Aqua only")
    pytest.importorskip("AppKit")
    import subprocess

    result = subprocess.run([sys.executable, "-c", _FROZEN_ABOUT_CHECK],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "calls=1 panels=1"
    # Only the version, not "Version 0.6.0 (0.6.0)".
    assert "(" not in lines[-2], lines[-2]


@pytest.mark.gui
def test_about_item_from_source_names_the_version(monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tkinter import messagebox
    from tokitty import settings_ui
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: shown.append((title, message)))
    monkeypatch.setattr(settings_ui, "version_text", lambda: "v9.9.9")
    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            window.register_about_panel(frozen=False)
            root.tk.call("tkAboutDialog")
            assert shown and shown[0][0] == "About Tokitty"
            assert "v9.9.9" in shown[0][1]
    finally:
        root.destroy()


class _Pointer:
    def __init__(self, x_root, y_root):
        self.x_root, self.y_root = x_root, y_root


@pytest.mark.gui
def test_motion_without_a_press_on_the_card_does_not_move_it():
    # Dragging the native About panel by its title bar: Aqua Tk sees the
    # press there, loses the release, and sends the card B1-Motion events.
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            moves = []
            window._move_to = lambda x, y: moves.append((x, y))
            window._on_drag_move(_Pointer(900, 700))
            assert moves == []
    finally:
        root.destroy()


@pytest.mark.gui
def test_a_drag_that_starts_on_the_card_still_moves_it_and_ends_on_release():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            moves = []
            window._move_to = lambda x, y: moves.append((x, y))
            window._save_position = lambda: None
            start = _Pointer(root.winfo_x() + 10, root.winfo_y() + 10)
            window._on_drag_start(start)
            window._on_drag_move(_Pointer(start.x_root + 40, start.y_root + 30))
            assert moves == [(root.winfo_x() + 40, root.winfo_y() + 30)]
            window._on_drag_end(_Pointer(0, 0))
            window._on_drag_move(_Pointer(900, 700))
            assert len(moves) == 1
    finally:
        root.destroy()


@pytest.mark.gui
def test_source_run_dock_icon_is_the_app_icon():
    tk = pytest.importorskip("tkinter")
    pytest.importorskip("PIL")
    from tokitty import app_icon

    root = tk.Tk()
    try:
        photo = app_icon.photo(root, 256)
        assert (photo.width(), photo.height()) == (256, 256)
    finally:
        root.destroy()
