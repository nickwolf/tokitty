"""The Updates tab's rules and the menu actions, without a toolkit (#113, #155).

`UpdateFlow` is what the tab does with a release: which choices it gets, what
each outcome shows, when the window may not close. `UpdateStatus` is what the
tab says about the check, and `history_entries` which release notes it lists
(#171). `UpdateActions` is what the "Update to vX.Y.Z…" and
"Check for updates" items do. The Qt renderer draws them and adds nothing, so
the user-facing rules live once. The install itself is `UpdateController`'s.
"""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from tokitty.update_check import CheckResult
from tokitty.updater import REPO, Release, is_newer, parse_version

RELEASES_URL = f"https://github.com/{REPO}/releases"
NOTES_CAP = 20_000
NO_NOTES = "No release notes for this release."
SHORTENED = "The release notes were shortened."
# How many releases the tab lists when few are newer. Ten covers the 11 days
# from v0.3.0 to v0.8.0 and keeps the list a short scroll.
HISTORY_MIN = 10
NEW = "New"
INSTALLED = "Installed"
MORE_ON_GITHUB = "More on GitHub"

Buttons = List[Tuple[str, Callable[[], None]]]


def release_page_url(release: Release) -> str:
    return release.html_url or RELEASES_URL


def notes_markdown(release: Release) -> str:
    """What the notes area shows: the body capped at NOTES_CAP characters, or a
    line saying there is none, then the GitHub link."""
    link = f"[View on GitHub]({release_page_url(release)})"
    body = (release.body or "").strip()
    if not body:
        return f"{NO_NOTES}\n\n{link}"
    if len(body) > NOTES_CAP:
        return f"{body[:NOTES_CAP].rstrip()}\n\n*{SHORTENED} The full notes are on GitHub.*\n\n{link}"
    return f"{body}\n\n{link}"


def release_badge(release: Release, running: str) -> Optional[str]:
    """NEW for a release ahead of the running version, INSTALLED for the
    running one, and None for older ones or a running version that isn't a
    release tag."""
    if is_newer(release.tag, running):
        return NEW
    version = parse_version(release.tag)
    if version is not None and version == parse_version(running):
        return INSTALLED
    return None


@dataclass(frozen=True)
class HistoryEntry:
    release: Release
    badge: Optional[str]


def history_entries(history, running: str) -> List[HistoryEntry]:
    """The releases the tab lists, highest version first: HISTORY_MIN of them,
    or more when more are ahead of the running version, so the notes of every
    release an install skips are there along with the running one's."""
    count = HISTORY_MIN
    for index, release in enumerate(history):
        if not is_newer(release.tag, running):
            break
        count = max(count, index + 2)
    return [HistoryEntry(release, release_badge(release, running)) for release in history[:count]]


@dataclass(frozen=True)
class UpdateStatus:
    """What the tab says about the check. `release` is set only while a newer
    one is known; `pending` is a newer tag remembered from an earlier run with
    no release behind it yet. `history` is every stable release the last
    successful check found, or None before one has."""

    running: str
    release: Optional[Release] = None
    checking: bool = False
    error: Optional[str] = None
    pending: bool = False
    history: Optional[Tuple[Release, ...]] = None

    @property
    def entries(self) -> List[HistoryEntry]:
        return history_entries(self.history or (), self.running)

    @property
    def headline(self) -> str:
        installed = f"Tokitty {self.running} is installed."
        if self.release is not None:
            return f"{installed}\n{self.release.tag} is available."
        if self.checking or self.pending:
            return "Checking…"
        if self.error:
            return f"Couldn't check for updates: {self.error}"
        return f"Tokitty {self.running} is the latest version."


class UpdateFlow:
    """One per release shown. `render` has `show(note, buttons)` (buttons is a
    list of (label, callback), or None to keep the current ones) and `close()`.
    The constructor calls `render.show`, so the renderer must be ready."""

    def __init__(self, release: Release, controller, render):
        self._release = release
        self._controller = controller
        self._render = render
        # refusal() writes and removes a probe file, so ask once, here.
        self._reason: Optional[str] = controller.refusal(release)
        self.alive = True
        self.note = ""
        self._show(self._reason or "", self._choices())

    def _show(self, note: str, buttons: Optional[Buttons]) -> None:
        self.note = note
        self._render.show(note, buttons)

    def _choices(self) -> Buttons:
        # A refused release has the reason as its note and the GitHub link in
        # its notes, so there is nothing to offer.
        return [] if self._reason else [("Install", self.install)]

    def _show_choices(self, note: str) -> None:
        self._show(note, self._choices())

    def install(self) -> None:
        if not self._controller.start_install(self._release, self.on_progress, self.on_done):
            self._show_choices("An update is already in progress.")
            return
        self._show("Preparing…", [("Cancel", self.cancel)])

    def cancel(self) -> None:
        self._controller.cancel()
        self._show("Cancelling…", [])

    def on_progress(self, done: int, total: int) -> None:
        if not self.alive or self.note == "Cancelling…":
            return
        if total <= 0:
            text = "Downloading…"
        elif done >= total:
            text = "Checking the download…"
        else:
            text = f"Downloading… {done * 100 // total}%"
        self._show(text, None)

    def on_done(self, result) -> None:
        if not self.alive:
            return
        if result.outcome == "installed":
            self._show(f"Updated to {result.tag}. Restarting…", [])
        elif result.outcome == "rolled_back":
            # The controller's own notice says what happened.
            self.close()
        elif result.outcome == "cancelled":
            self._show_choices("Cancelled.")
        else:
            self._show_choices(result.message or "The update failed.")

    @property
    def busy(self) -> bool:
        """True while an install runs: the window may not close."""
        return self.alive and bool(self._controller.busy)

    def detach(self) -> None:
        """The renderer is going away; late callbacks must not reach it."""
        self.alive = False

    def close(self) -> None:
        # The renderer may call back in from its own close handling.
        if not self.alive:
            return
        self.alive = False
        self._render.close()


class UpdateActions:
    """The menu actions and the Updates tab's data. Result callbacks arrive
    from tick(), so the boxes go through `defer` to keep tick() from stalling.
    `changed` is called when the check's state does, so the tab re-reads it."""

    def __init__(
        self,
        checker,
        controller,
        *,
        defer: Callable[[Callable[[], None]], None],
        error: Callable[[str, str], None],
        info: Callable[[str, str], None],
        offer: Callable[[Release], None],
        changed: Callable[[], None] = lambda: None,
        open_url: Callable[[str], object] = webbrowser.open,
    ):
        self._checker = checker
        self._controller = controller
        self._defer = defer
        self._error = error
        self._info = info
        self._offer = offer
        self._changed = changed
        self.open_url = open_url
        self._checking = False
        # Whether the check in flight reports its result in a box.
        self._loud = False
        self._last_error: Optional[str] = None

    def status(self) -> UpdateStatus:
        available = self._checker.update_available
        release = self._checker.latest_release if available else None
        return UpdateStatus(
            running=self._controller.running.version,
            release=release,
            checking=self._checking,
            error=self._last_error,
            pending=available and release is None,
            history=self._checker.release_history,
        )

    def new_flow(self, release: Release, render) -> UpdateFlow:
        return UpdateFlow(release, self._controller, render)

    def ensure_release(self) -> None:
        """What the tab asks for when it opens. A tag remembered from an
        earlier run has no release to show yet, and the release notes need a
        check this run: check once, and not again after a failure (Check now
        retries). A check only for the notes is quiet, and only runs while
        automatic checks are on."""
        if self._checking or self._last_error is not None:
            return
        status = self.status()
        if status.pending:
            self.check_now()
        elif status.history is None and self._checker.enabled:
            self.check_now(quiet=True)

    def install(self) -> None:
        """The "Update to vX.Y.Z…" item. With no release in hand (the tag came
        from an earlier run) it checks first."""
        release = self._checker.latest_release
        if release is not None and self._checker.update_available:
            self._offer(release)
        else:
            self.check_now()

    def check_now(self, *, quiet: bool = False) -> None:
        """A quiet check only updates the tab. A loud one asked for while a
        quiet one runs joins it and still gets its box."""
        if not quiet:
            self._loud = True
        if self._checking:
            return
        self._checking = True
        self._last_error = None
        self._checker.check_now(self._on_result)
        self._changed()

    def _on_result(self, result: CheckResult) -> None:
        loud, self._loud = self._loud, False
        self._checking = False
        self._last_error = result.error
        self._defer(lambda: self.present(result) if loud else self._changed())

    def present(self, result: CheckResult) -> None:
        if result.error:
            self._error("Check for updates", f"Couldn't check for updates: {result.error}")
        elif result.newer and result.release is not None:
            self._offer(result.release)
        else:
            self._info("Check for updates", f"Tokitty {self._controller.running.version} is the latest version.")
        self._changed()
