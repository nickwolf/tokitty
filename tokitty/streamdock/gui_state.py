"""What the Settings window's Stream Dock tab shows, and what its buttons do.

start_streamdock runs once at launch, so installing or uninstalling from the
Settings window cannot start or stop the live runtime. This holder keeps the
settings the state is read from current and remembers, for this session,
that the plugin was installed (restart_to_connect) or removed with the
runtime still up (restart_to_finish_removal).

state() is read on the Tk thread; install() and uninstall() run on a worker
thread. The only shared writes are plain attribute assignments.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from tokitty.settings import load_settings
from tokitty.streamdock import install as install_mod
from tokitty.streamdock.runtime import StreamdockRuntime

STATES = ("not_installed", "not_connected", "connected", "restart_to_connect",
          "restart_to_finish_removal", "starting", "failed")


class StreamdockHolder:
    def __init__(self, settings: Any, state_dir, runtime: Callable[[], Optional[Any]],
                 *, deck: Optional[Any] = None, load_fn=load_settings, install_fn=install_mod.gui_install,
                 uninstall_fn=install_mod.gui_uninstall):
        self.settings = settings
        self.override: Optional[str] = None
        # The runtime started at launch serves this port and token for the
        # whole session, so a reinstall that saves different ones needs a restart.
        self._started_with = self._credentials(settings)
        self._state_dir = state_dir
        self._runtime = runtime
        # Owns the start retry (wiring.DeckStarter); None means no retry state.
        self._deck = deck
        self._load = load_fn
        self._install = install_fn
        self._uninstall = uninstall_fn

    def state(self) -> str:
        if self.override is not None:
            return self.override
        if not StreamdockRuntime.configured(self.settings):
            return "not_installed"
        runtime = self._runtime()
        phase = self._phase()
        if runtime is None and phase in ("starting", "failed"):
            return phase
        return "connected" if runtime is not None and runtime.connected else "not_connected"

    def _phase(self) -> Optional[str]:
        return getattr(self._deck, "state", None)

    def info(self) -> dict:
        """What the starting and failed details name: the port and the last error."""
        return {"port": getattr(self._deck, "port", 0), "reason": getattr(self._deck, "reason", "")}

    def _serving(self) -> bool:
        """True while the launch-time runtime holds or is still waiting for its port."""
        return self._runtime() is not None or self._phase() == "starting"

    @staticmethod
    def _credentials(settings: Any) -> Tuple[Any, Any]:
        return getattr(settings, "streamdock_port", 0), getattr(settings, "streamdock_token", "")

    def install(self) -> Tuple[bool, List[str]]:
        ok, lines = self._install(self._state_dir)
        if ok:
            self.settings = self._load(self._state_dir)
            # A running runtime serves the port and token it started with; the
            # plugin can reach it only if the install kept those.
            same = self._credentials(self.settings) == self._started_with
            self.override = None if self._serving() and same else "restart_to_connect"
        return ok, lines

    def uninstall(self) -> Tuple[bool, List[str]]:
        ok, lines = self._uninstall(self._state_dir)
        if ok:
            self.settings = self._load(self._state_dir)
            self.override = "restart_to_finish_removal" if self._serving() else None
        return ok, lines
