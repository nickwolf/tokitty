"""The seam between one coding-agent harness and one pane.

A pane needs three things, and a harness may supply any subset:

1. Rate-limit state -- the two bars, the reset countdowns, and everything
   the cat's mood is computed from.
2. A per-model token ledger -- the cost readout, scanned off disk.
3. Live activity -- the thinking / working / permission poses.

Claude Code supplies all three. Codex supplies the first two (its
rate-limit block rides along inside the transcript, so there is nothing
to poll). A harness that only writes token counts supplies the second
alone, and its pane shows a cost readout with no bars rather than not
existing.

Capabilities are declared rather than discovered so the UI can tell
"this harness never had bars" apart from "the fetch failed", which are
the same blank pane otherwise and want opposite reactions from the user.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sys
from pathlib import Path
from typing import Callable, Optional, Protocol, Tuple

from tokitty.poller import PollResult

# The status a provider's fetch returns when it has no rate-limit source
# at all. Distinct from every failure status: nothing is broken and no
# retry will help, so the UI must not offer a reconnect hint.
STATUS_UNSUPPORTED = "rate_limits_unsupported"


@dataclass(frozen=True)
class ProviderCapabilities:
    rate_limits: bool = False
    token_ledger: bool = False
    activity: bool = False


class Provider(Protocol):
    """What Tokitty asks of a harness. Every method takes the account's
    config_dir because a provider is a stateless strategy, not a per-
    account object: one instance serves every account of its kind."""

    kind: str
    display_name: str
    capabilities: ProviderCapabilities

    def build_fetch_fn(
        self, config_dir: Optional[str] = None, loader=None
    ) -> Callable[[], PollResult]:
        """A callable the Poller can run on its thread. Always returns a
        callable, even when `capabilities.rate_limits` is False -- see
        unsupported_fetch_fn."""

    def resolve_activity_sessions(
        self, config_dir: Optional[str] = None, credentials=None
    ) -> Tuple[Optional[str], Optional[str]]:
        """(sessions_dir, distro_name) for the ActivityWatcher. (None, None)
        means run without activity; it is never an error."""

    def resolve_projects_dir(
        self, config_dir: Optional[str] = None, credentials=None
    ) -> Tuple[Optional[str], Optional[str]]:
        """(projects_dir, distro_name) for the transcript scanner. (None,
        None) means run without a cost readout."""


def unsupported_fetch_fn(display_name: str) -> Callable[[], PollResult]:
    """The fetch a provider with no rate-limit source hands the Poller.

    It would be tidier to hand back None and skip the Poller entirely, but
    then every caller needs a None branch for a case that is not an error.
    A cheap constant result keeps one code path and lets the pane say why
    it has no bars.
    """

    def fetch() -> PollResult:
        return PollResult(
            status=STATUS_UNSUPPORTED,
            snapshot=None,
            message=f"{display_name} does not publish rate limits",
            fetched_at=datetime.now(timezone.utc),
        )

    return fetch


def no_directory(
    config_dir: Optional[str] = None, credentials=None
) -> Tuple[Optional[str], Optional[str]]:
    """The resolver a provider uses for a capability it does not have."""
    return None, None


# Path helpers shared by every provider that reads a config dir which may
# have been written in either separator style (a WSL UNC path read from
# Windows, a posix path read from inside the distro). They predate the
# seam and moved here from __main__ unchanged.


def _config_root_from(config_dir: str):
    """(root, distro_name) for an explicit config_dir, in the separator
    style of whichever side will do the reading.

    Shared by the sessions path and the projects path so the two can never
    drift: a WSL UNC dir stays UNC on win32 (with the distro parsed out for
    the running-distro check) and becomes its posix path on Linux.
    """
    from tokitty.accounts import parse_wsl_unc

    unc = parse_wsl_unc(config_dir)
    if sys.platform == "win32":
        if unc is not None:
            return config_dir.rstrip("\\/"), unc[0]
        return str(Path(config_dir)), None
    base = unc[1] if unc is not None else config_dir
    return base.rstrip("/"), None


def _join(root: str, *parts: str) -> str:
    """Join in the separator style `root` is already written in.

    pathlib would emit host-native separators, which turns a UNC root into
    backslash-plus-forward-slash soup when tokitty runs on Linux against a
    Windows-style path, and vice versa.
    """
    separator = "\\" if "\\" in root else "/"
    return root + separator + separator.join(parts)


class NullProvider:
    """The provider for an account this build cannot serve -- currently
    only an accounts.json naming a kind from a newer Tokitty. It reports
    nothing rather than guessing, which keeps one unserviceable account
    from taking the window down."""

    kind = "unknown"
    display_name = "unsupported harness"
    capabilities = ProviderCapabilities()

    def build_fetch_fn(self, config_dir: Optional[str] = None, loader=None):
        return unsupported_fetch_fn(self.display_name)

    def resolve_activity_sessions(self, config_dir: Optional[str] = None, credentials=None):
        return no_directory(config_dir, credentials)

    def resolve_projects_dir(self, config_dir: Optional[str] = None, credentials=None):
        return no_directory(config_dir, credentials)
