"""Pure helpers that connect run_gui's per-account units to the Stream Dock runtime."""
from __future__ import annotations

import errno
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from tokitty.activity import SessionView
from tokitty.streamdock.runtime import AccountInput

DEFAULT_NAME = "Claude"
# How often an idle account is checked for new sessions while the deck is connected.
DECK_IDLE_POLL_S = 3.0
# How often a busy deck port is retried. The caller sets the deadline to cover an
# in-app update's whole handover (its ack timeout plus this margin).
BIND_RETRY_S = 1.0
BIND_MARGIN_S = 30.0
WSAEADDRINUSE = 10048
WSAEACCES = 10013


def parent_dir(path: Optional[str]) -> Optional[str]:
    """The parent of a path string, splitting on either separator.

    The sessions dir is built by string concatenation with the separators of the
    side it lives on (a \\\\wsl.localhost UNC path on Windows), so os.path would be
    wrong for it on any other host.
    """
    if not path:
        return None
    head, _, _ = path.rstrip("\\/").rpartition("\\" if "\\" in path else "/")
    return head or None


def _resolve(value: Any) -> Optional[str]:
    return value() if callable(value) else value


def account_inputs(units: List[dict]) -> List[AccountInput]:
    """One AccountInput per unit. The hook writes <config>/tokitty/{sessions,pending,decisions},
    so the tokitty dir is the parent of the activity sessions dir and the config dir the
    parent of that, in the same form (UNC for WSL) the ActivityWatcher already reads."""
    out = []
    for index, unit in enumerate(units):
        sessions_dir = unit.get("sessions_dir")

        def tokitty_dir(sessions_dir=sessions_dir) -> Optional[str]:
            return parent_dir(_resolve(sessions_dir))

        def config_dir(tokitty_dir=tokitty_dir) -> Optional[str]:
            return parent_dir(tokitty_dir())

        account = unit.get("account")
        name = account.name if account else DEFAULT_NAME
        out.append(AccountInput(index, name, account, config_dir, tokitty_dir, unit.get("distro_name")))
    return out


def gather_inputs(
    units: List[dict],
) -> Tuple[Dict[int, List[SessionView]], Dict[int, Tuple[float, float, bool]]]:
    """(sessions, usage) keyed by unit index. A unit with no poll result yet has no usage."""
    sessions: Dict[int, List[SessionView]] = {}
    usage: Dict[int, Tuple[float, float, bool]] = {}
    for index, unit in enumerate(units):
        sessions[index] = unit["watcher"].get_sessions()
        if unit.get("deck_usage") is not None:
            usage[index] = unit["deck_usage"]
    return sessions, usage


def usage_from_display(display: dict) -> Tuple[float, float, bool]:
    """Percentages the pane shows, and its dimmed look (stale or unconfirmed numbers) as the warning."""
    return (display["session_pct"], display["weekly_pct"], bool(display.get("dimmed")))


def make_watcher_factory(units: List[dict], list_running_distros_fn: Callable[[], List[str]]) -> Callable:
    """PendingWatcher per account with the unit's distro, so a stopped WSL distro is never read."""
    from tokitty.streamdock.pending import PendingWatcher

    def factory(acct: AccountInput):
        unit = units[acct.index]
        return PendingWatcher(
            acct.tokitty_dir,
            account_index=acct.index,
            distro_name=unit.get("distro_name"),
            list_running_distros_fn=list_running_distros_fn,
        )

    return factory


def is_port_busy(exc: BaseException) -> bool:
    """True when a bind failed because another socket holds the port.

    That is EADDRINUSE everywhere. On Windows the old copy's exclusive socket can
    also surface as WSAEADDRINUSE (10048) or WSAEACCES (10013). A bare EACCES
    (a privileged port on POSIX) is not retryable.
    """
    if not isinstance(exc, OSError):
        return False
    codes = {errno.EADDRINUSE, WSAEADDRINUSE, WSAEACCES}
    return exc.errno in codes or getattr(exc, "winerror", None) in (WSAEADDRINUSE, WSAEACCES)


class DeckStarter:
    """Starts the runtime and keeps retrying while its port is held by someone else.

    After an in-app update the old copy keeps the deck port until the new copy's
    window acks, so the bind fails at launch and succeeds a little later. The retry
    runs on the Tk thread through `after`, never blocks it, and gives up at the
    deadline. One runtime is built and its start() retried: a failed bind has
    started nothing (see StreamdockRuntime.start), so there is nothing to leak.

    `runtime` is the started runtime or None. `state` is "off" (not installed),
    "starting", "running" or "failed"; `reason` is the last error text.
    """

    def __init__(
        self,
        settings: Any,
        units: List[dict],
        list_running_distros_fn: Callable[[], List[str]],
        *,
        palette_fn: Callable[[int], Dict[str, str]],
        after: Callable[[int, Callable[[], None]], Any],
        cancel: Callable[[Any], None],
        deadline_s: float,
        open_in_window: Optional[Callable] = None,
        runtime_cls: Any = None,
        now: Callable[[], float] = time.monotonic,
        retry_s: float = BIND_RETRY_S,
    ) -> None:
        self._settings = settings
        self._units = units
        self._list_running = list_running_distros_fn
        self._palette_fn = palette_fn
        self._open_in_window = open_in_window
        self._cls = runtime_cls
        self._after = after
        self._cancel = cancel
        self._now = now
        self._retry_s = retry_s
        self._deadline_s = deadline_s
        self._candidate: Any = None
        self._handle: Any = None
        self._deadline = 0.0
        self._stopped = False
        self.runtime: Any = None
        self.state = "off"
        self.reason = ""
        self.port = getattr(settings, "streamdock_port", 0)

    def begin(self) -> None:
        """Build the runtime and make the first attempt. Tk thread."""
        from tokitty.streamdock.runtime import StreamdockRuntime

        cls = self._cls or StreamdockRuntime
        settings = self._settings
        if not cls.configured(settings):
            return
        try:
            self._candidate = cls(
                settings.streamdock_port,
                settings.streamdock_token,
                list(settings.streamdock_presets),
                account_inputs(self._units),
                palette_fn=self._palette_fn,
                open_in_window=self._open_in_window,
                watcher_factory=make_watcher_factory(self._units, self._list_running),
                list_running_distros_fn=self._list_running,
                hidden_accounts=list(settings.streamdock_hidden_accounts),
            )
        except Exception as exc:
            self._fail(exc)
            return
        self._deadline = self._now() + self._deadline_s
        self._attempt()

    def _attempt(self) -> None:
        self._handle = None
        if self._stopped:
            return
        try:
            self._candidate.start()
        except Exception as exc:
            if is_port_busy(exc) and self._now() + self._retry_s < self._deadline:
                if self.state != "starting":
                    print(f"tokitty: streamdock: port {self.port} is busy, retrying: {exc}", file=sys.stderr)
                self.state = "starting"
                self.reason = str(exc)
                self._handle = self._after(int(self._retry_s * 1000), self._attempt)
            else:
                self._fail(exc)
            return
        self.runtime, self._candidate = self._candidate, None
        self.state = "running"
        self.reason = ""

    def _fail(self, exc: BaseException) -> None:
        self.state = "failed"
        self.reason = str(exc)
        self._candidate = None
        print(f"tokitty: streamdock: not started: {exc}", file=sys.stderr)

    def set_presets(self, presets: List[dict]) -> None:
        target = self.runtime or self._candidate
        if target is not None:
            target.set_presets(presets)

    def set_hidden_accounts(self, names: List[str]) -> None:
        target = self.runtime or self._candidate
        if target is not None:
            target.set_hidden_accounts(names)

    def stop(self) -> None:
        """Cancel a pending retry and stop the runtime if it started. Tk thread."""
        self._stopped = True
        if self._handle is not None:
            try:
                self._cancel(self._handle)
            except Exception:
                pass
            self._handle = None
        self._candidate = None
        if self.runtime is not None:
            self.runtime.stop()


def apply_idle_cap(units: List[dict], connected: bool) -> None:
    """Poll idle accounts quickly while the deck is connected, so a new session gets a key."""
    cap = DECK_IDLE_POLL_S if connected else None
    for unit in units:
        unit["watcher"].set_idle_cap(cap)
