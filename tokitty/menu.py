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


def build_menu(
    *,
    coats: List[str],
    current_coat: Callable[[], str],
    on_coat: Callable[[str], None],
    on_customize: Callable[[], None],
    on_rename: Callable[[], None],
    on_refresh: Callable[[], None],
    always_on_top: Callable[[], bool],
    on_toggle_always_on_top: Callable[[], None],
    on_quit: Callable[[], None],
    tray_enabled: Optional[Callable[[], bool]] = None,
    on_toggle_tray: Optional[Callable[[], None]] = None,
) -> List[MenuItem]:
    coat_items = [
        MenuItem(
            label=name,
            action=(lambda n=name: on_coat(n)),
            radio_selected=(lambda n=name: current_coat() == n),
        )
        for name in coats
    ]
    items: List[MenuItem] = [
        MenuItem(label="Coat", submenu=coat_items),
        MenuItem(label="Customize…", action=on_customize),
        MenuItem(label="Rename…", action=on_rename),
        MenuItem(separator=True),
        MenuItem(label="Refresh now", action=on_refresh),
        MenuItem(label="Always in front", action=on_toggle_always_on_top, checkbox=always_on_top),
    ]
    if on_toggle_tray is not None and tray_enabled is not None:
        items.append(MenuItem(label="Show tray icon", action=on_toggle_tray, checkbox=tray_enabled))
    items.append(MenuItem(separator=True))
    items.append(MenuItem(label="Exit", action=on_quit))
    return items
