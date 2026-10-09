"""Settings buttons on macOS (#104): Aqua Tk draws tk.Button natively and
ignores bg, so the dark theme's light text lands on a white bezel. On darwin
Kit.button returns a label-based FlatButton that keeps its colours."""
import tkinter as tk

import pytest

from tokitty.settings_widgets import ACCENT_BG, SURFACE, FlatButton, Kit

pytestmark = pytest.mark.gui


@pytest.fixture
def root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_macos_buttons_keep_their_theme_colours(root):
    kit = Kit(platform="darwin")
    secondary = kit.button(root, "Check now")
    primary = kit.button(root, "Done", "primary")
    assert isinstance(secondary, FlatButton)
    assert secondary.cget("bg") == SURFACE
    assert primary.cget("bg") == ACCENT_BG
    assert secondary.cget("text") == "Check now"


def test_macos_button_invokes_its_command(root):
    pressed = []
    button = Kit(platform="darwin").button(root, "Install", command=lambda: pressed.append(1))
    button.invoke()
    assert pressed == [1]


def test_macos_disabled_button_does_nothing(root):
    pressed = []
    button = Kit(platform="darwin").button(root, "Install", command=lambda: pressed.append(1), disabled=True)
    assert button.cget("state") == "disabled"
    button.invoke()
    assert pressed == []


def test_macos_button_fires_on_click_release(root):
    button = Kit(platform="darwin").button(root, "Install")
    assert button.bind("<ButtonRelease-1>")


def test_other_platforms_keep_native_buttons(root):
    for platform in ("win32", "linux"):
        button = Kit(platform=platform).button(root, "Install")
        assert type(button) is tk.Button


def test_macos_dropdown_keeps_its_theme_colours(root):
    from tokitty.settings_widgets import Dropdown

    dropdown = Dropdown(Kit(platform="darwin"), root, ["Orange", "Gray"])
    assert dropdown.button.winfo_class() == "Label"
    assert dropdown.button.cget("bg") == SURFACE
    assert dropdown.button.bind("<Button-1>")


def test_macos_dropdown_menu_still_picks(root):
    from tokitty.settings_widgets import Dropdown

    picked = []
    dropdown = Dropdown(Kit(platform="darwin"), root, ["Orange", "Gray"], on_change=picked.append)
    dropdown.menu.invoke(1)
    assert dropdown.get() == "Gray"
    assert picked == ["Gray"]


def test_other_platforms_keep_the_menubutton_dropdown(root):
    from tokitty.settings_widgets import Dropdown

    dropdown = Dropdown(Kit(platform="win32"), root, ["Orange"])
    assert dropdown.button.winfo_class() == "Menubutton"


def test_scrollbar_thumb_tracks_the_visible_fraction(root):
    from tokitty.settings_widgets import ThinScrollbar

    bar = ThinScrollbar(Kit(), root)
    bar.configure(height=200)
    bar.set(0.25, 0.75)
    top, bottom = bar.thumb_span(200)
    assert (top, bottom) == (50, 150)


def test_dragging_the_thumb_scrolls_by_the_same_fraction(root):
    from tokitty.settings_widgets import ThinScrollbar

    calls = []
    bar = ThinScrollbar(Kit(), root, command=lambda *args: calls.append(args))
    bar.set(0.0, 0.5)
    bar.drag(from_y=10, to_y=60, track=200)
    assert calls == [("moveto", 0.25)]


def test_clicking_the_track_pages_toward_the_click(root):
    from tokitty.settings_widgets import ThinScrollbar

    calls = []
    bar = ThinScrollbar(Kit(), root, command=lambda *args: calls.append(args))
    bar.set(0.0, 0.5)
    bar.click(y=180, track=200)
    assert calls == [("scroll", 1, "pages")]
