"""The single-source menu model, rendered to both the Tk right-click menu
(ui.py) and the pystray tray menu (tray.py). Pure Python: no tkinter, no
pystray, no I/O.

The getter fields (checkbox, radio_selected) are evaluated by pystray on
its OWN thread when it draws the tray menu. They MUST therefore read only
plain-Python shadow state -- never a tkinter Var or widget. Callers wire
them accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class MenuItem:
    label: str = ""
    action: Optional[Callable[[], None]] = None
    submenu: Optional[List["MenuItem"]] = None
    separator: bool = False
    checkbox: Optional[Callable[[], bool]] = None
    radio_selected: Optional[Callable[[], bool]] = None
    # Only the Tk menu honours this; a status line, not a command.
    enabled: bool = True
    # Evaluated each time the menu is drawn: the label to show, or None to
    # hide the item. Lets the tray (built once) pick up an update that is
    # found later, which a static label could not.
    dynamic_label: Optional[Callable[[], Optional[str]]] = None


def build_menu(
    *,
    on_refresh: Callable[[], None],
    always_on_top: Callable[[], bool],
    on_toggle_always_on_top: Callable[[], None],
    on_quit: Callable[[], None],
    on_open_settings: Optional[Callable[[], None]] = None,
    view_modes: Optional[List[tuple]] = None,
    current_view_mode: Optional[Callable[[], str]] = None,
    on_view_mode: Optional[Callable[[str], None]] = None,
    update_available_label: Optional[Callable[[], Optional[str]]] = None,
    on_install_update: Optional[Callable[[], None]] = None,
) -> List[MenuItem]:
    items: List[MenuItem] = []
    if update_available_label is not None and on_install_update is not None:
        # The getter returns None while no newer release is known, which
        # hides the item in both menus.
        items.append(MenuItem(
            label=update_available_label() or "", action=on_install_update,
            dynamic_label=update_available_label))
    items.append(MenuItem(label="Refresh now", action=on_refresh))
    if view_modes and current_view_mode is not None and on_view_mode is not None:
        items.append(MenuItem(label="View", submenu=[
            MenuItem(
                label=label,
                action=(lambda value=value: on_view_mode(value)),
                radio_selected=(lambda value=value: current_view_mode() == value),
            )
            for value, label in view_modes
        ]))
    items.append(MenuItem(label="Always in front", action=on_toggle_always_on_top, checkbox=always_on_top))
    if on_open_settings is not None:
        items.append(MenuItem(separator=True))
        items.append(MenuItem(label="Settings…", action=on_open_settings))
    items.append(MenuItem(separator=True))
    items.append(MenuItem(label="Exit", action=on_quit))
    return items
