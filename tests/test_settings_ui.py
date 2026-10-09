"""GUI tests for the Settings window (xvfb). Each builds a real
TokittyWindow with fake seams over plain-Python shadow state, as run_gui
does, and drives SettingsWindow through its controls."""
import pytest

tk = pytest.importorskip("tkinter")

from tokitty import settings_ui  # noqa: E402
from tokitty.settings_ui import SettingsWindow  # noqa: E402
from tokitty.ui import TokittyWindow  # noqa: E402


class Harness:
    """A TokittyWindow whose seams record calls and keep shadow state."""

    def __init__(self, root, state_dir, panes=3):
        self.calls = []
        self.state = {"tray": True, "autostart": False, "surprise": False,
                      "update_check": True, "view": "limits", "window": "7d",
                      "readout": "cost", "budgets": {},
                      "notes": True, "note_session": 90, "note_weekly": 95}
        w = self.window = TokittyWindow(root, state_dir, pane_count=panes)
        w.on_menu_action_done = lambda: self.calls.append("done")
        w.on_toggle_tray = lambda: self._flip("tray")
        w.tray_enabled = lambda: self.state["tray"]
        w.on_toggle_autostart = lambda: self._flip("autostart")
        w.autostart_enabled = lambda: self.state["autostart"]
        w.on_toggle_surprise = lambda: self._flip("surprise")
        w.surprise_me = lambda: self.state["surprise"]
        w.on_toggle_update_check = lambda: self._flip("update_check")
        w.update_check_enabled = lambda: self.state["update_check"]
        w.update_available_label = lambda: None
        w.on_check_updates = lambda: self.calls.append("check")
        w.on_randomize = lambda i: self.calls.append(("randomize", i))
        w.on_customization_changed = lambda i, f, v: self.calls.append(("custom", i, f, v))
        w.on_opacity_changed = lambda level: self.calls.append(("opacity", level))
        w.view_mode = lambda: self.state["view"]
        w.on_view_mode = lambda v: self._set("view", v)
        w.usage_window = lambda: self.state["window"]
        w.on_usage_window = lambda v: self._set("window", v)
        w.usage_readout = lambda: self.state["readout"]
        w.on_usage_readout = lambda v: self._set("readout", v)
        w.budget_for_pane = lambda i: self.state["budgets"].get((i, self.state["window"]))
        w.set_budget_for_pane = self._set_budget
        w.usage_notes_enabled = lambda: self.state["notes"]
        w.on_toggle_usage_notes = lambda: self._flip("notes")
        w.usage_note_threshold = lambda kind: self.state["note_" + kind]
        w.set_usage_note_threshold = self._set_note

    def _flip(self, key):
        self.state[key] = not self.state[key]
        self.calls.append(("seam", key))

    def _set(self, key, value):
        self.state[key] = value
        self.calls.append(("seam", key))

    def _set_note(self, kind, text):
        from tokitty.settings import parse_percent

        value, error = parse_percent(text)
        if error is None:
            self.state["note_" + kind] = value
            self.calls.append(("note", kind, value))
        return error

    def _set_budget(self, i, text):
        text = text.strip().lstrip("$")
        if text == "":
            self.state["budgets"].pop((i, self.state["window"]), None)
            return None
        try:
            amount = float(text)
        except ValueError:
            return "not a number"
        self.state["budgets"][(i, self.state["window"])] = amount
        return None


@pytest.fixture
def harness(tmp_path):
    root = tk.Tk()
    root.withdraw()
    yield Harness(root, tmp_path)
    for instance in list(settings_ui._instances.values()):
        instance.close()
    root.destroy()


def _open(harness, index=0):
    return SettingsWindow.open(harness.window, index)


@pytest.mark.gui
def test_opens_on_requested_pane(harness):
    settings = _open(harness, 2)
    assert settings.pane_index == 2
    assert settings.tabs["look"].pane_picker.get() == settings.pane_names()[2]
    assert settings.tabs["usage"].pane_picker.get() == settings.pane_names()[2]
    assert harness.window.settings_refresh is not None


@pytest.mark.gui
def test_reopen_switches_pane_without_second_window(harness):
    first = _open(harness, 0)
    second = _open(harness, 1)
    assert second is first
    assert first.pane_index == 1
    assert first.tabs["look"].pane_picker.get() == first.pane_names()[1]
    toplevels = [c for c in harness.window.root.winfo_children() if isinstance(c, tk.Toplevel)]
    assert len([t for t in toplevels if t.title() == "Tokitty — Settings"]) == 1


@pytest.mark.gui
def test_general_toggles_call_seam_then_menu_action_done(harness):
    settings = _open(harness)
    general = settings.tabs["general"]
    for key, state_key in (("tray", "tray"), ("autostart", "autostart"),
                           ("surprise", "surprise"), ("update_toggle", "update_check")):
        harness.calls.clear()
        toggle = general.update_toggle if key == "update_toggle" else general.toggles[key]
        before = harness.state[state_key]
        toggle._clicked()
        assert harness.calls[0] == ("seam", state_key), key
        assert "done" in harness.calls[1:], key
        assert harness.state[state_key] is (not before)
        assert toggle.get() is (not before)


@pytest.mark.gui
def test_always_in_front_toggle_flips_window_then_notifies(harness):
    settings = _open(harness)
    before = harness.window._always_on_top_bool
    settings.tabs["general"].toggles["aot"]._clicked()
    assert harness.window._always_on_top_bool is (not before)
    assert harness.calls == ["done"]


@pytest.mark.gui
def test_transparency_segmented_writes_level(harness):
    settings = _open(harness)
    seg = settings.tabs["general"].transparency
    assert seg.get() == "100"
    seg._clicked("70")
    assert ("opacity", 70) in harness.calls
    assert "done" in harness.calls
    assert harness.window.opacity() == 70
    assert seg.get() == "70"


@pytest.mark.gui
def test_tray_style_action_refreshes_open_window(harness):
    settings = _open(harness)
    toggle = settings.tabs["general"].toggles["tray"]
    assert toggle.get() is True
    harness.state["tray"] = False     # the tray flips shadow state itself
    harness.window.notify_state_changed()
    assert toggle.get() is False
    assert ("seam", "tray") not in harness.calls   # refresh wrote nothing


@pytest.mark.gui
def test_refresh_does_not_write(harness):
    settings = _open(harness)
    harness.calls.clear()
    settings.refresh()
    settings.set_pane(1)
    for key in settings.tabs:
        settings._show(key)
    assert harness.calls == []


@pytest.mark.gui
def test_hidden_controls_when_seams_are_none(harness):
    harness.window.on_toggle_tray = None
    harness.window.on_toggle_autostart = None
    settings = _open(harness)
    assert "tray" not in settings.tabs["general"].toggles
    assert "autostart" not in settings.tabs["general"].toggles


@pytest.mark.gui
def test_update_status_line(harness):
    settings = _open(harness)
    pill = settings.tabs["general"].update_pill
    assert pill.cget("text").startswith("Up to date · ")
    harness.window.update_available_label = lambda: "Update to v9.9.9…"
    settings.refresh()
    assert pill.cget("text") == "Update to v9.9.9…"
    harness.calls.clear()


@pytest.mark.gui
def test_look_writes_target_selected_pane_not_pane_zero(harness):
    settings = _open(harness, 2)
    look = settings.tabs["look"]
    look.colorway._picked("gray")
    look.pattern._picked("solid")
    look.name_entry.delete(0, "end")
    look.name_entry.insert(0, "Biscuit")
    look._save_name()
    settings.tabs["look"].write(
        lambda: harness.window._fire_customization_changed(settings.pane_index, "reset", None))
    harness.calls  # noqa: B018
    customs = [c for c in harness.calls if isinstance(c, tuple) and c[0] == "custom"]
    assert customs == [("custom", 2, "colorway", "gray"), ("custom", 2, "pattern", "solid"),
                       ("custom", 2, "label", "Biscuit"), ("custom", 2, "reset", None)]


@pytest.mark.gui
def test_look_color_change_uses_selected_pane(harness, monkeypatch):
    settings = _open(harness, 1)
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return (None, "#123456")

    monkeypatch.setattr(settings_ui.colorchooser, "askcolor", fake)
    settings.tabs["look"]._choose_color("coat_shade", "Shade")
    assert seen["parent"] is settings.toplevel
    assert ("custom", 1, "coat_shade", "#123456") in harness.calls


@pytest.mark.gui
def test_look_randomize_targets_selected_pane(harness):
    settings = _open(harness)
    settings.set_pane(2)
    harness.window.on_randomize(2)
    harness.calls.clear()
    settings.tabs["look"].frame.winfo_children()  # built
    # Randomize button lives in the picker row; drive it through the seam it calls.
    picker_row = settings.tabs["look"].pane_picker.frame.master
    button = [w for w in picker_row.winfo_children() if isinstance(w, tk.Button)][0]
    button.invoke()
    assert harness.calls[0] == ("randomize", 2)


@pytest.mark.gui
def test_look_shows_pane_effective_colors_and_preview(harness):
    harness.window.panes[1].set_appearance(
        palette={**harness.window.panes[1].palette, "o": "#aabbcc"}, card_bg="#102030")
    settings = _open(harness, 1)
    swatch, value = settings.tabs["look"].color_widgets["coat_base"]
    assert value.cget("text") == "#AABBCC"
    assert swatch.cget("bg") == "#aabbcc"
    assert settings.tabs["look"].color_widgets["card_bg"][1].cget("text") == "#102030"
    assert settings.tabs["look"].preview.find_all()


@pytest.mark.gui
def test_budget_entry_rereads_when_window_changes(harness):
    harness.state["budgets"] = {(0, "7d"): 12.5, (0, "24h"): 3.0}
    settings = _open(harness)
    usage = settings.tabs["usage"]
    assert usage.budget_entry.get() == "12.5"
    assert usage.budget_label.cget("text") == "Budget for the last 7 days"
    usage.groups["window"][0]._clicked("24h")
    assert usage.budget_entry.get() == "3"
    assert usage.budget_label.cget("text") == "Budget for the last 24 hours"
    usage.groups["window"][0]._clicked("month")
    assert usage.budget_entry.get() == ""
    assert usage.budget_label.cget("text") == "Budget for this month"


@pytest.mark.gui
def test_budget_entry_rereads_when_pane_changes_and_saves_to_that_pane(harness):
    harness.state["budgets"] = {(1, "7d"): 40.0}
    settings = _open(harness)
    usage = settings.tabs["usage"]
    assert usage.budget_entry.get() == ""
    settings.pick_pane_by_name(settings.pane_names()[1])
    assert usage.budget_entry.get() == "40"
    usage.budget_entry.delete(0, "end")
    usage.budget_entry.insert(0, "$25")
    harness.calls.clear()
    usage._save_budget()
    assert harness.state["budgets"][(1, "7d")] == 25.0
    assert "done" in harness.calls


@pytest.mark.gui
def test_budget_error_is_inline_and_does_not_notify(harness):
    settings = _open(harness)
    usage = settings.tabs["usage"]
    usage.budget_entry.insert(0, "abc")
    harness.calls.clear()
    usage._save_budget()
    assert usage.budget_error.cget("text") == "not a number"
    assert "done" not in harness.calls


@pytest.mark.gui
def test_usage_radios_call_seams_then_notify(harness):
    settings = _open(harness)
    usage = settings.tabs["usage"]
    for key, value in (("view", "models"), ("window", "24h"), ("readout", "tokens")):
        harness.calls.clear()
        usage.groups[key][0]._clicked(value)
        assert harness.calls[0] == ("seam", key)
        assert "done" in harness.calls


@pytest.mark.gui
def test_settings_refresh_is_none_after_close_and_reopen_works(harness):
    settings = _open(harness)
    assert harness.window.settings_refresh == settings.refresh
    settings.close()
    assert harness.window.settings_refresh is None
    harness.window.notify_state_changed()     # must not touch the dead window
    again = _open(harness)
    assert again is not settings
    assert harness.window.settings_refresh == again.refresh


@pytest.mark.gui
def test_window_is_not_topmost_and_is_transient(harness):
    settings = _open(harness)
    assert not settings.toplevel.attributes("-topmost")
    assert str(settings.toplevel.transient()) == str(harness.window.root)


@pytest.mark.gui
def test_rail_has_five_entries_and_placeholders_switch(harness):
    settings = _open(harness)
    assert list(settings.nav) == ["general", "look", "usage", "accounts", "streamdock"]
    settings._show("accounts")
    assert settings.tabs["accounts"].frame.winfo_manager() == "pack"
    assert settings.tabs["general"].frame.winfo_manager() == ""


@pytest.mark.gui
def test_usage_notes_section_reflects_state(harness):
    harness.state.update(notes=False, note_session=80, note_weekly=99)
    usage = _open(harness).tabs["usage"]
    assert usage.notes_toggle.get() is False
    assert usage.note_entries["session"][1].get() == "80"
    assert usage.note_entries["weekly"][1].get() == "99"


@pytest.mark.gui
def test_usage_notes_toggle_calls_seam_then_notifies(harness):
    usage = _open(harness).tabs["usage"]
    harness.calls.clear()
    usage.notes_toggle._clicked()
    assert ("seam", "notes") in harness.calls
    assert harness.state["notes"] is False
    assert "done" in harness.calls


@pytest.mark.gui
def test_usage_note_threshold_saves_and_notifies(harness):
    usage = _open(harness).tabs["usage"]
    _holder, entry, error = usage.note_entries["weekly"]
    entry.delete(0, "end")
    entry.insert(0, "85")
    harness.calls.clear()
    usage._save_note_threshold("weekly")
    assert harness.state["note_weekly"] == 85
    assert error.cget("text") == ""
    assert "done" in harness.calls


@pytest.mark.gui
@pytest.mark.parametrize("bad", ["0", "101", "abc", ""])
def test_usage_note_threshold_error_is_inline_and_does_not_notify(harness, bad):
    usage = _open(harness).tabs["usage"]
    holder, entry, error = usage.note_entries["session"]
    entry.delete(0, "end")
    entry.insert(0, bad)
    harness.calls.clear()
    usage._save_note_threshold("session")
    assert error.cget("text")
    assert harness.state["note_session"] == 90
    assert "done" not in harness.calls
