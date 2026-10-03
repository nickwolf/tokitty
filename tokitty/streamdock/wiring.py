"""Pure helpers that connect run_gui's per-account units to the Stream Dock runtime."""
from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from tokitty.activity import SessionView
from tokitty.streamdock.runtime import AccountInput

DEFAULT_NAME = "Claude"
# How often an idle account is checked for new sessions while the deck is connected.
DECK_IDLE_POLL_S = 3.0


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


def start_streamdock(
    settings: Any,
    units: List[dict],
    list_running_distros_fn: Callable[[], List[str]],
    *,
    palette_fn: Callable[[int], Dict[str, str]],
    open_in_window: Optional[Callable] = None,
    runtime_cls: Any = None,
):
    """Build and start the runtime, or return None when not installed or when starting fails.

    A bind error or any other failure is logged and the app carries on without the deck.
    """
    from tokitty.streamdock.runtime import StreamdockRuntime

    cls = runtime_cls or StreamdockRuntime
    if not cls.configured(settings):
        return None
    try:
        runtime = cls(
            settings.streamdock_port,
            settings.streamdock_token,
            list(settings.streamdock_presets),
            account_inputs(units),
            palette_fn=palette_fn,
            open_in_window=open_in_window,
            watcher_factory=make_watcher_factory(units, list_running_distros_fn),
            list_running_distros_fn=list_running_distros_fn,
            hidden_accounts=list(settings.streamdock_hidden_accounts),
        )
        runtime.start()
        return runtime
    except Exception as exc:
        print(f"tokitty: streamdock: not started: {exc}", file=sys.stderr)
        return None


def apply_idle_cap(units: List[dict], connected: bool) -> None:
    """Poll idle accounts quickly while the deck is connected, so a new session gets a key."""
    cap = DECK_IDLE_POLL_S if connected else None
    for unit in units:
        unit["watcher"].set_idle_cap(cap)
