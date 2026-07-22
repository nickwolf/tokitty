"""System-tray icon manager.

Runs pystray's icon.run() on a daemon thread. Every tray->UI action is
marshaled back to the main thread with root.after(0, ...), because tkinter
is not thread-safe. All pystray/PIL access lives inside the injectable
`icon_factory` / `image_factory` (lazy imports), so this module -- and its
tests -- import neither at module scope. Where the backend is unavailable
(headless, missing libs), construction is guarded and `available` is False;
the app then runs with no tray and no crash.
"""
from __future__ import annotations

import threading
from typing import Callable, List, Optional

from tokitty.settings import Settings, save_settings

TRAY_ICON_SCALE = 2  # 28x26 content sprite -> 56x52, padded to a 56x56 square


def _default_image_factory(coat: str):
    from PIL import Image

    from tokitty.sprite_raster import raster_rgba
    from tokitty.sprites import get_frames, get_palette

    frame = get_frames("content")[0]
    width, height, raw = raster_rgba(frame, get_palette(coat), TRAY_ICON_SCALE)
    sprite = Image.frombytes("RGBA", (width, height), raw)
    side = max(width, height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(sprite, ((side - width) // 2, (side - height) // 2))
    return canvas


def _default_icon_factory(image, menu_model, wrap, title):
    import pystray

    def to_entries(items):
        entries = []
        for it in items:
            if it.separator:
                entries.append(pystray.Menu.SEPARATOR)
            elif it.submenu is not None:
                entries.append(pystray.MenuItem(it.label, pystray.Menu(*to_entries(it.submenu))))
            elif it.radio_selected is not None:
                entries.append(pystray.MenuItem(
                    it.label, wrap(it.action),
                    checked=(lambda i, g=it.radio_selected: g()), radio=True))
            elif it.checkbox is not None:
                entries.append(pystray.MenuItem(
                    it.label, wrap(it.action),
                    checked=(lambda i, g=it.checkbox: g())))
            else:
                entries.append(pystray.MenuItem(it.label, wrap(it.action)))
        return entries

    menu = pystray.Menu(*to_entries(menu_model))
    return pystray.Icon("tokitty", image, title, menu)


class TrayManager:
    def __init__(self, root, menu_provider: Callable[[], List], state_dir,
                 coat: str = "orange_tabby", icon_factory=None, image_factory=None):
        self._root = root
        self._menu_provider = menu_provider
        self._state_dir = state_dir
        self._coat = coat
        self._title = "Tokitty"
        self._icon_factory = icon_factory or _default_icon_factory
        self._image_factory = image_factory or _default_image_factory
        self._wrap = lambda action: (lambda *args: self._root.after(0, action))
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._image = None
        self.available = self._probe()

    def _probe(self) -> bool:
        """Build the icon image and a throwaway icon so both the PIL import
        and pystray backend selection are guarded up front -- not just the
        `import`. Any failure => tray unavailable, cleanly."""
        try:
            self._image = self._image_factory(self._coat)
            self._icon_factory(self._image, [], self._wrap, self._title)
            return True
        except Exception:
            self._image = None
            return False

    def start(self) -> None:
        if not self.available or self._icon is not None:
            return
        try:
            self._icon = self._icon_factory(
                self._image, self._menu_provider(), self._wrap, self._title)
        except Exception:
            self.available = False
            self._icon = None
            return
        self._thread = threading.Thread(target=self._run_icon, daemon=True)
        self._thread.start()

    def _run_icon(self) -> None:
        try:
            self._icon.run()
        except Exception:
            # Backend imported but failed to run (e.g. headless): disable
            # cleanly rather than leave a crashed thread.
            self.available = False
            self._icon = None

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def set_enabled(self, enabled: bool) -> None:
        save_settings(self._state_dir, Settings(tray_enabled=enabled))
        if enabled:
            self.start()
        else:
            self.stop()
