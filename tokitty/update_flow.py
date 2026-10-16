"""The Updates tab's rules and the menu actions, without a toolkit (#113, #155).

`UpdateFlow` is what the tab does with a release: which choices it gets, what
each outcome shows, when the window may not close. `UpdateStatus` is what the
tab says about the check. `UpdateActions` is what the "Update to vX.Y.Z…" and
"Check for updates" items do. The Qt renderer draws them and adds nothing, so
the user-facing rules live once. The install itself is `UpdateController`'s.
"""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from tokitty.update_check import CheckResult
from tokitty.updater import REPO, Release

RELEASES_URL = f"https://github.com/{REPO}/releases"
NOTES_CAP = 20_000
NO_NOTES = "No release notes for this release."
SHORTENED = "The release notes were shortened."

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


@dataclass(frozen=True)
class UpdateStatus:
    """What the tab says about the check. `release` is set only while a newer
    one is known; `pending` is a newer tag remembered from an earlier run with
    no release behind it yet."""

    running: str
    release: Optional[Release] = None
    checking: bool = False
    error: Optional[str] = None
    pending: bool = False

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
        )

    def new_flow(self, release: Release, render) -> UpdateFlow:
        return UpdateFlow(release, self._controller, render)

    def ensure_release(self) -> None:
        """A tag remembered from an earlier run has no release to show yet:
        check once, and not again after a failure (Check now retries)."""
        if self.status().pending and not self._checking and self._last_error is None:
            self.check_now()

    def install(self) -> None:
        """The "Update to vX.Y.Z…" item. With no release in hand (the tag came
        from an earlier run) it checks first."""
        release = self._checker.latest_release
        if release is not None and self._checker.update_available:
            self._offer(release)
        else:
            self.check_now()

    def check_now(self) -> None:
        if self._checking:
            return
        self._checking = True
        self._last_error = None
        self._checker.check_now(self._on_result)
        self._changed()

    def _on_result(self, result: CheckResult) -> None:
        self._checking = False
        self._last_error = result.error
        self._defer(lambda: self.present(result))

    def present(self, result: CheckResult) -> None:
        if result.error:
            self._error("Check for updates", f"Couldn't check for updates: {result.error}")
        elif result.newer and result.release is not None:
            self._offer(result.release)
        else:
            self._info("Check for updates", f"Tokitty {self._controller.running.version} is the latest version.")
        self._changed()
