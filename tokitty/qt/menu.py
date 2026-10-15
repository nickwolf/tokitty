"""Render the shared menu model (menu.MenuItem) into a QMenu. One renderer serves the right-click menu
(rebuilt on every open) and the tray menu (built once, then updated in place
through the sync() that render_menu returns)."""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtGui import QActionGroup

from tokitty.menu import MenuItem

AfterAction = Callable[[Optional[Callable[[], None]]], Optional[Callable[[], None]]]


def render_menu(qmenu, items: List[MenuItem], after_action: AfterAction) -> Callable[[], None]:
    """Fill qmenu from items. after_action wraps every item's action (None
    stays None), so the caller can refresh
    whatever else shows the same state.

    Returns a sync() that re-reads every rendered getter in place (dynamic
    label text and visibility, checkbox and radio state), submenus included,
    without adding or removing an action. A dynamic_label item is always
    created, hidden while its getter has no text. The tray renders once and
    syncs; the right-click menu is rebuilt on each open and ignores it."""
    syncs: List[Callable[[], None]] = []
    group: Optional[QActionGroup] = None  # one radio group per menu level
    for item in items:
        if item.dynamic_label is not None:
            getter = item.dynamic_label
            action = qmenu.addAction(getter() or "")
            action.setVisible(bool(getter()))
            _connect(action, after_action(item.action))
            syncs.append(lambda a=action, g=getter: (a.setText(g() or ""), a.setVisible(bool(g()))))
        elif item.separator:
            qmenu.addSeparator()
        elif not item.enabled:
            qmenu.addAction(item.label).setEnabled(False)
        elif item.submenu is not None:
            syncs.append(render_menu(qmenu.addMenu(item.label), item.submenu, after_action))
        elif item.radio_selected is not None:
            if group is None:
                group = QActionGroup(qmenu)
                group.setExclusive(True)
            action = qmenu.addAction(item.label)
            action.setCheckable(True)
            group.addAction(action)
            action.setChecked(bool(item.radio_selected()))
            _connect(action, after_action(item.action))
            syncs.append(lambda a=action, g=item.radio_selected: a.setChecked(bool(g())))
        elif item.checkbox is not None:
            action = qmenu.addAction(item.label)
            action.setCheckable(True)
            action.setChecked(bool(item.checkbox()))
            _connect(action, after_action(item.action))
            syncs.append(lambda a=action, g=item.checkbox: a.setChecked(bool(g())))
        else:
            _connect(qmenu.addAction(item.label), after_action(item.action))

    def sync() -> None:
        for fn in syncs:
            fn()

    return sync


def _connect(action, run: Optional[Callable[[], None]]) -> None:
    if run is not None:
        action.triggered.connect(lambda _checked=False: run())
