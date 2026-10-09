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
