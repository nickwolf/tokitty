"""QtHost: what the Qt Settings tabs read their state and actions from.

The tabs read about 40 seams as attributes of the host. The toolkit-free ones
come from the Controls that app_controls.make_controls builds (attach() copies
them on under the same names, so tab code reads host.on_toggle_ntfy), and this
class adds the
few that touch the Qt widget: always in front, the theme, the colours a pane
shows, the layout follow-up after a customization save, and the stacking of
the windows it opened. The tray and update seams stay None until run_qt sets
them (#114, #113), which hides their rows; the tray row is also hidden
while tray_available says no.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from tokitty import theme as T
from tokitty.customize import Customization


class QtHost:
    def __init__(self, widget, state_dir):
        self.widget = widget
        self.state_dir = state_dir
        self.controls = None
        # Called by notify_state_changed; the open Settings window sets it.
        self.settings_refresh: Optional[Callable[[], None]] = None
        # Opened by slices 3 and 5; None hides the rows that need them.
        self.on_open_accounts: Optional[Callable[[], None]] = None
        self.on_edit_streamdock_presets: Optional[Callable[[], None]] = None
        # The StreamdockHolder's methods, set by run_qt once the deck exists;
        # None reads as "not installed" in the Stream Dock tab.
        self.streamdock_state: Optional[Callable[[], str]] = None
        self.streamdock_info: Optional[Callable[[], dict]] = None
        self.streamdock_install: Optional[Callable] = None
        self.streamdock_uninstall: Optional[Callable] = None
        self.streamdock_install_supported: Optional[Callable[[], bool]] = None
        # The tray seams are set by run_qt (#114). The update seams are set
        # by run_qt too (#113).
        self.tray_refresh: Optional[Callable[[], None]] = None
        self.on_toggle_tray = None
        self.tray_enabled = None
        # Read live (a tray that failed to start drops out), not at build time.
        self.tray_available: Optional[Callable[[], bool]] = None
        self.update_check_enabled = None
        self.on_toggle_update_check = None
        self.on_check_updates = None
        self.update_available_label = None
        self.on_install_update = None
        # The UpdateActions behind the Updates tab's status, notes and install.
        self.update_actions = None
        self._windows: List[QWidget] = []

    def attach(self, controls) -> None:
        """Second phase of setup: Controls needs host methods and the host
        needs Controls. Its seams become attributes under their own names."""
        self.controls = controls
        for name, value in controls.seams().items():
            setattr(self, name, value)

    @property
    def panes(self):
        return self.widget.panes

    # ---- always in front and the windows that follow it ----------------

    def always_on_top(self) -> bool:
        return self.widget.always_on_top

    def toggle_always_on_top(self) -> None:
        """Flip the widget, then every window opened here: a parentless
        window without the hint sits under a topmost widget, and raise()
        cannot change that."""
        self.widget.set_always_on_top(not self.widget.always_on_top)
        for window in list(self._windows):
            try:
                self._apply_stacking(window)
            except RuntimeError:  # the C++ window is already gone
                self._windows.remove(window)

    def register_window(self, window: QWidget) -> None:
        """Track a top-level window and give it the hint if the widget is in
        front. Call before its first show()."""
        if window not in self._windows:
            self._windows.append(window)
        self._apply_stacking(window)

    def forget_window(self, window: QWidget) -> None:
        if window in self._windows:
            self._windows.remove(window)

    def is_registered(self, window: QWidget) -> bool:
        return window in self._windows

    def restack(self, window: QWidget) -> None:
        """Re-apply the Always in front hint to a window, as register_window does."""
        self._apply_stacking(window)

    def _apply_stacking(self, window: QWidget) -> None:
        on = self.widget.always_on_top
        if bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == on:
            return
        was_visible = window.isVisible()
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            window.show()  # a flag change hides the window

    # ---- theme ---------------------------------------------------------

    def theme(self) -> T.Theme:
        return self.widget.theme

    def set_theme(self, theme: T.Theme) -> None:
        try:
            T.save_theme(self.state_dir, theme)
        except OSError as exc:
            self.warn("Tokitty", f"Could not save the theme: {exc}")
        self.widget.set_theme(theme)

    # ---- per pane ------------------------------------------------------

    def pane_color(self, index: int, field: str) -> Optional[str]:
        """The colour the Look tab shows for one row. None means automatic
        (only the bar fill has that)."""
        pane = self.widget.panes[index]
        if field == "coat_base":
            return pane.palette.get("o")
        if field == "coat_shade":
            return pane.palette.get("O")
        overrides = pane.customization.overrides
        if field == "card_bg":
            # What the widget paints: the override, else the theme's card.
            return overrides.get("card_bg") or self.widget.theme.card.background
        if field == "session_bar":
            return overrides.get("session_bar") or overrides.get("bar_fill") or None
        return overrides.get("weekly_bar") or overrides.get("bar_fill") or None

    def on_layout_changed(self, pane_index: int, custom: Customization) -> None:
        """Called by Controls after every customization save. The pane takes
        the whole customization (so cat_home survives), then the window
        follows if any pane's home moved. The label is Controls' business."""
        self.widget.panes[pane_index].set_customization(custom)
        count = len(self.widget.panes)
        homes = [T.resolve_home(pane.customization.cat_home, i, count)
                 for i, pane in enumerate(self.widget.panes)]
        if homes != self.widget.homes:
            self.widget.rebuild_layout(homes)

    # ---- refresh and warnings ------------------------------------------

    def notify_state_changed(self) -> None:
        """Re-sync the tray menu, then an open Settings window."""
        if self.tray_refresh is not None:
            self.tray_refresh()
        if self.settings_refresh is not None:
            self.settings_refresh()

    def dialog_parent(self) -> QWidget:
        """What a message box is parented to: the last visible window opened
        here, else the widget."""
        return next((w for w in reversed(self._windows) if w.isVisible()), self.widget)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self.dialog_parent(), title, text)

