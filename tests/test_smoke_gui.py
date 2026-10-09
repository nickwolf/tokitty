"""GUI smoke test: boot the real Tk widgets and confirm they construct.

Unlike the rest of the suite (which is deliberately headless), this needs a
live display. It's marked `gui` and deselected by default; CI runs it on Linux
under a virtual framebuffer via `xvfb-run -a pytest -m gui`.

It is a construction check, not a behavior check: build the window, render one
frame of fake data (mirroring the app's own debug path), pump a single event
cycle, and tear down -- asserting nothing raises. No network, no polling, no
mainloop.
"""

import sys

import pytest

tk = pytest.importorskip("tkinter")

# Imported after the guard on purpose: tokitty.ui imports tkinter at module
# scope, so importing it up top would turn a tkinter-less runner into a
# collection error instead of a clean skip.
from tokitty.ui import TokittyWindow  # noqa: E402

# Mirrors the fake frame used by run_gui's TOKITTY_DEBUG_STATE path.
FAKE_FRAME = dict(
    state="content",
    session_pct=37.0,
    weekly_pct=62.0,
    session_reset_text="resets 9pm",
    weekly_reset_text="resets Fri",
    driving_tag="debug",
    credits_text=None,
    hint_text=None,
    dimmed=False,
)


@pytest.mark.gui
def test_window_constructs_and_renders(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no usable display even under xvfb
        pytest.skip(f"no Tk display available: {exc}")

    try:
        window = TokittyWindow(root, tmp_path, pane_count=1)
        assert len(window.panes) == 1
        window.panes[0].render(**FAKE_FRAME)
        window.set_opacity(70)
        root.update()  # process the pending event cycle; never enter mainloop
        assert_stays_out_of_the_taskbar(root)
        assert_walkthrough_boots(root, tmp_path)
    finally:
        root.destroy()


def assert_walkthrough_boots(root, state_dir) -> None:
    """The first-run walkthrough constructs and steps through Welcome,
    Accounts (fake discovery) and Done, on the same root for the same reason."""
    import time

    from tokitty.first_run import Candidate
    from tokitty.first_run_ui import Walkthrough

    found = [Candidate("claude", str(state_dir / ".claude"), "This PC", True)]
    walkthrough = Walkthrough(root, state_dir, 1.0, discover=lambda: found)
    try:
        walkthrough.primary_button.invoke()
        deadline = time.monotonic() + 5.0
        while walkthrough.candidates is None and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert walkthrough.candidates == found
        assert walkthrough.primary_button.cget("text") == "Add 1 account"
        walkthrough.go("done")
        root.update()
    finally:
        walkthrough.skip()


def assert_stays_out_of_the_taskbar(root) -> None:
    """Windows only, and folded into the construction test rather than given
    its own: a second tk.Tk() in one interpreter fails on Windows with
    `invalid command name "tcl_findLibrary"`, so the suite gets one root.

    Writing -alpha strips WS_EX_TOOLWINDOW from the wrapper Tk recreates,
    which put a stray "tk" button in the taskbar until _apply_opacity started
    restoring it.
    """
    if sys.platform != "win32":
        return

    import ctypes
    from ctypes import wintypes

    from tokitty.transparency import root_hwnd

    style = ctypes.windll.user32.GetWindowLongW(wintypes.HWND(root_hwnd(root)), -20)
    assert style & 0x00000080  # WS_EX_TOOLWINDOW

