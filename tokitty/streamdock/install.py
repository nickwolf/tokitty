"""Install the Stream Dock plugin into VSD Craft, and remove it again.

VSD Craft loads plugins from %APPDATA%\\HotSpot\\StreamDock\\plugins. Install
copies the plugin there and writes config.js beside it with the loopback port
and token tokitty will serve on. Both are chosen once and kept in settings, so a
second install changes nothing. The token is never printed.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sys
from pathlib import Path
from typing import Callable, Iterable

from tokitty.paths import get_state_dir
from tokitty.settings import load_settings, update_settings
from tokitty.streamdock.pending import clear_enabled

PLUGIN_UUID = "com.tokitty.deck"
PLUGIN_FOLDER = PLUGIN_UUID + ".sdPlugin"
CONFIG_NAME = "config.js"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def default_plugin_src() -> Path:
    """The bundled plugin folder. A frozen build ships it at the same place
    relative to the tokitty package, which is where prices.json is found too."""
    return Path(__file__).parent / "plugin" / PLUGIN_FOLDER


def _plugins_dir(appdata) -> Path:
    return Path(appdata) / "HotSpot" / "StreamDock" / "plugins"


def install_streamdock(
    *,
    state_dir,
    appdata,
    plugin_src=None,
    print_fn: Callable[..., None] = print,
    port_fn: Callable[[], int] = _free_port,
    token_fn: Callable[[], str] = _new_token,
) -> int:
    plugins = _plugins_dir(appdata)
    if not plugins.is_dir():
        print_fn(f"VSD Craft not found: {plugins} does not exist. Install VSD Craft and run it once first.")
        return 1
    src = Path(plugin_src) if plugin_src is not None else default_plugin_src()
    if not src.is_dir():
        print_fn(f"The bundled plugin is missing: {src}")
        return 1
    settings = load_settings(state_dir)
    port, token = settings.streamdock_port, settings.streamdock_token
    if not (port and token):
        port, token = port_fn(), token_fn()
    # Settings first: a copy that fails halfway must not leave a plugin whose
    # port and token tokitty has not recorded.
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    settings = update_settings(state_dir, streamdock_port=port, streamdock_token=token)
    # A saved value that load_settings rejects (a hand-edited token) was not kept.
    if settings.streamdock_port != port or settings.streamdock_token != token:
        print_fn("Could not save the Stream Dock settings.")
        return 1
    target = plugins / PLUGIN_FOLDER
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    config = "window.TOKITTY = " + json.dumps({"port": port, "token": token}) + ";\n"
    (target / CONFIG_NAME).write_text(config, encoding="utf-8")
    print_fn(f"Installed the Stream Dock plugin to {target} (port {port}).")
    print_fn("Fully exit VSD Craft from its tray icon and start it again to load the plugin.")
    return 0


def uninstall_streamdock(
    *,
    state_dir,
    appdata,
    tokitty_dirs: Iterable,
    print_fn: Callable[..., None] = print,
) -> int:
    target = _plugins_dir(appdata) / PLUGIN_FOLDER
    if target.exists():
        shutil.rmtree(target)
        print_fn(f"Removed {target}.")
    else:
        print_fn("The Stream Dock plugin folder is not installed.")
    settings = load_settings(state_dir)
    if settings.streamdock_port or settings.streamdock_token:
        update_settings(state_dir, streamdock_port=0, streamdock_token="")
        print_fn("Cleared the saved Stream Dock port and token.")
    for d in tokitty_dirs:
        clear_enabled(d)
    print_fn("Cleared the Stream Dock marker for every account.")
    print_fn("Fully exit VSD Craft from its tray icon and start it again to unload the plugin.")
    return 0


def _real_appdata() -> Path:
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def run_install() -> int:
    return install_streamdock(state_dir=get_state_dir(), appdata=_real_appdata())


def run_uninstall() -> int:
    from tokitty.hooks_install import get_config_dirs

    state_dir = get_state_dir()
    dirs = [Path(config_dir) / "tokitty" for config_dir, _ in get_config_dirs(state_dir)]
    return uninstall_streamdock(state_dir=state_dir, appdata=_real_appdata(), tokitty_dirs=dirs)


def install_supported() -> bool:
    """The plugin folder lives under %APPDATA%, so install and uninstall are Windows only."""
    return sys.platform == "win32"


def gui_install(state_dir, appdata=None, *, install_fn=install_streamdock):
    """Install for the Settings window: (ok, lines). Same arguments as run_install.

    install_streamdock saves the port and token before it copies, so a copy
    that failed halfway would otherwise read as installed; ok also needs the
    plugin folder to exist."""
    appdata = _real_appdata() if appdata is None else appdata
    lines: list = []
    try:
        code = install_fn(state_dir=state_dir, appdata=appdata, print_fn=lines.append)
    except Exception as exc:
        lines.append(f"Install failed: {exc}")
        return False, lines
    if code == 0 and not (_plugins_dir(appdata) / PLUGIN_FOLDER).is_dir():
        lines.append("The plugin folder was not created.")
        return False, lines
    return code == 0, lines


def gui_uninstall(state_dir, appdata=None, *, uninstall_fn=uninstall_streamdock, config_dirs_fn=None):
    """Uninstall for the Settings window: (ok, lines). Same arguments as run_uninstall."""
    appdata = _real_appdata() if appdata is None else appdata
    lines: list = []
    try:
        if config_dirs_fn is None:
            from tokitty.hooks_install import get_config_dirs as config_dirs_fn
        dirs = [Path(config_dir) / "tokitty" for config_dir, _ in config_dirs_fn(state_dir)]
        code = uninstall_fn(state_dir=state_dir, appdata=appdata, tokitty_dirs=dirs, print_fn=lines.append)
    except Exception as exc:
        lines.append(f"Uninstall failed: {exc}")
        return False, lines
    return code == 0, lines
