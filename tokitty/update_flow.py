"""The update dialog's rules and the menu actions, without a toolkit (#113).

`UpdateFlow` is what the confirm dialog does: which choices a release gets,
what each outcome shows, when the window may close. `UpdateActions` is what
the "Update to vX.Y.Z…" and "Check for updates" items do. The Qt
renderer draws them and adds nothing, so the user-facing rules live once.
The install itself is `UpdateController`'s.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from tokitty.update_check import CheckResult
from tokitty.updater import REPO, Release

RELEASES_URL = f"https://github.com/{REPO}/releases"
WRAP_PX = 340

Buttons = List[Tuple[str, Callable[[], None]]]


class UpdateFlow:
    """One per dialog. `render` has `show(note, buttons)` (buttons is a list
    of (label, callback), or None to keep the current ones) and `close()`.
    The constructor calls `render.show`, so the renderer must be ready."""

    def __init__(self, release: Release, running: str, controller, render, open_url):
        self._release = release
        self._running = running
        self._controller = controller
        self._render = render
        self._open_url = open_url
        # refusal() writes and removes a probe file, so ask once, here.
        self._reason: Optional[str] = controller.refusal(release)
        self.alive = True
        self.note = ""
        self._show(self._reason or "", self._choices())

    @property
    def info_text(self) -> str:
        return f"Tokitty {self._running} is installed.\n{self._release.tag} is available."

    def _show(self, note: str, buttons: Optional[Buttons]) -> None:
        self.note = note
        self._render.show(note, buttons)

    def _choices(self) -> Buttons:
        if self._reason:
            # Release notes would open the same page, so it isn't offered twice.
            choices = [("Open release page", self.release_page)]
        else:
            choices = [("Install", self.install), ("Release notes", self.release_page)]
        return choices + [("Later", self.close_requested)]

    def _show_choices(self, note: str) -> None:
        self._show(note, self._choices())

    def release_page(self) -> None:
        self._open_url(self._release.html_url or RELEASES_URL)

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

    def close_requested(self) -> None:
        """The window's close button and Later: refused while an install runs."""
        if not self._controller.busy:
            self.close()

    def close(self) -> None:
        # The renderer may call back in from its own close handling.
        if not self.alive:
            return
        self.alive = False
        self._render.close()


class UpdateActions:
    """The menu actions. Result callbacks arrive from tick(), so the boxes
    go through `defer` to keep tick() from stalling."""

    def __init__(
        self,
        checker,
        controller,
        *,
        defer: Callable[[Callable[[], None]], None],
        error: Callable[[str, str], None],
        info: Callable[[str, str], None],
        offer: Callable[[Release], None],
    ):
        self._checker = checker
        self._controller = controller
        self._defer = defer
        self._error = error
        self._info = info
        self._offer = offer
        self._checking = False

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
        self._checker.check_now(self._on_result)

    def _on_result(self, result: CheckResult) -> None:
        self._checking = False
        self._defer(lambda: self.present(result))

    def present(self, result: CheckResult) -> None:
        if result.error:
            self._error("Check for updates", f"Couldn't check for updates: {result.error}")
        elif result.newer and result.release is not None:
            self._offer(result.release)
        else:
            self._info("Check for updates", f"Tokitty {self._controller.running.version} is the latest version.")
