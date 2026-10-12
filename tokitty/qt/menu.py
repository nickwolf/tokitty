"""Render the shared menu model (menu.MenuItem) into a QMenu, the Qt
counterpart of ui._render_tk_menu."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, List, Optional

from PySide6.QtGui import QActionGroup

from tokitty.menu import MenuItem

AfterAction = Callable[[Optional[Callable[[], None]]], Optional[Callable[[], None]]]


def render_menu(qmenu, items: List[MenuItem], after_action: AfterAction) -> None:
    """Fill qmenu from items. after_action wraps every item's action (None
    stays None), as ui._after_menu_action does, so the caller can refresh
    whatever else shows the same state."""
    group: Optional[QActionGroup] = None  # one radio group per menu level, as in Tk
    for item in items:
        if item.dynamic_label is not None:
            text = item.dynamic_label()
            if not text:
                continue  # hidden until the getter has a label
            item = replace(item, label=text)
        if item.separator:
            qmenu.addSeparator()
        elif not item.enabled:
            qmenu.addAction(item.label).setEnabled(False)
        elif item.submenu is not None:
            render_menu(qmenu.addMenu(item.label), item.submenu, after_action)
        elif item.radio_selected is not None:
            if group is None:
                group = QActionGroup(qmenu)
                group.setExclusive(True)
            action = qmenu.addAction(item.label)
            action.setCheckable(True)
            group.addAction(action)
            action.setChecked(bool(item.radio_selected()))
            _connect(action, after_action(item.action))
        elif item.checkbox is not None:
            action = qmenu.addAction(item.label)
            action.setCheckable(True)
            action.setChecked(bool(item.checkbox()))
            _connect(action, after_action(item.action))
        else:
            _connect(qmenu.addAction(item.label), after_action(item.action))


def _connect(action, run: Optional[Callable[[], None]]) -> None:
    if run is not None:
        action.triggered.connect(lambda _checked=False: run())
