"""The Qt first-run walkthrough window (#88, #112): Welcome, Accounts, Live
activity, Done. The counterpart of first_run_ui, kept structurally parallel to
it; see docs/superpowers/specs/2026-10-08-first-run-walkthrough-design.md.

It is a parentless window shown by run_qt before the widget exists, which
waits on it in a local QEventLoop quit by `closed`. Each step applies only on
its own button (Accounts' "Add N accounts", the hook Install buttons), so Skip
never undoes what the user already confirmed. Finish, Skip and the close
button all mark the walkthrough done and emit `closed` exactly once.

Threading is the Settings rule: discovery, the pending-op recovery, the hook
status query and the installs run on workers that only publish into a
Mailbox; a QtPoller applies results on the GUI thread. Workers never touch a
QObject.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QWidget

from tokitty import first_run, hook_guard, sprites
from tokitty.accounts import ACCOUNTS_FILENAME, Account
from tokitty.accounts_rows import (
    BUSY_MESSAGE, INSTALL_STATES, NO_BUTTON_STATES, RowData, Snapshot, collect_rows, elide_middle,
    run_finish_pending,
)
from tokitty.accounts_rows import run_hook_op as _hook_op
from tokitty.app_version import version_text
from tokitty.first_run import Candidate
from tokitty.hooks_install import _same_config_dir
from tokitty.qt import macos
from tokitty.qt.kit import Kit, clear_layout, hbox, text, vbox
from tokitty.qt.poller import QtPoller
from tokitty.qt.settings_accounts import render_banner, render_pill
from tokitty.settings_style import (
    ACCENT_FG, BAD, BASE_HEIGHT, BASE_WIDTH, BORDER, DIM_COLOR, FONT_BODY, FONT_MEDIUM,
    FONT_MONO, FONT_SMALL, MUTED, RAIL, SURFACE, WARN,
)
from tokitty.worker_mailbox import Mailbox, run_worker

STEPS = (
    ("welcome", "Welcome"),
    ("accounts", "Accounts"),
    ("hooks", "Live activity"),
    ("done", "Done"),
)

PROVIDER_LABELS = {"claude": "Claude Code", "codex": "Codex"}
TRANSCRIPTS_ONLY_NOTE = "Usage history only, no sign-in found"
LOCKED_NOTE = "Accounts are already set up; manage them in Settings."
REMINDER = ("Restart running Claude Code sessions for hooks to apply. Codex asks you to "
            "approve tokitty's hooks the next time it starts.")

_DISCOVER_TAG, _FINISH_TAG, _STATUS_TAG, _OP_TAG = "discover", "finish", "status", "op"


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider.capitalize())


class _CheckBox(QWidget):
    """A square check box in the kit's colours; `enabled` False makes it a display."""

    def __init__(self, checked: bool, enabled: bool, on_change):
        super().__init__()
        self.checked = checked
        self.enabled = enabled
        self._on_change = on_change
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)

    def toggle(self) -> None:
        if not self.enabled:
            return
        self.checked = not self.checked
        self.update()
        self._on_change(self.checked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()

    def paintEvent(self, _event) -> None:
        fill, edge = (SURFACE, DIM_COLOR)
        if self.checked:
            fill = edge = ACCENT_FG if self.enabled else "#6b4f4a"
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(edge), 1))
        p.setBrush(QColor(fill))
        p.drawRect(QRectF(0.5, 0.5, 19, 19))
        if self.checked:
            # Drawn, not a glyph, so it shows wherever the font has no tick.
            p.setPen(QPen(QColor(RAIL), 2))
            p.drawPolyline([QPointF(5, 10), QPointF(8.5, 13.5), QPointF(15, 6.5)])
        p.end()


class _Spinner(QWidget):
    """The discovery spinner: a ring with a quarter arc that advances per tick."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(22, 22)
        self.angle = 0

    def advance(self, spin: int) -> None:
        self.angle = (-spin * 40) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(3, 3, 16, 16)
        p.setPen(QPen(QColor(BORDER), 2))
        p.drawEllipse(rect)
        p.setPen(QPen(QColor(ACCENT_FG), 2))
        p.drawArc(rect, self.angle * 16, 90 * 16)
        p.end()


class Walkthrough(QWidget):
    """Show it, then wait for `closed`.

    The callables are test seams. Defaults: discover runs
    first_run.discover_candidates through `credentials_cache` (the process's
    WslCredentialsCache, shared with the unit loop so the WSL sweep runs
    once per launch); save, collect, run_hook_op and finish_pending are the
    backend and Accounts-tab functions."""

    closed = Signal()
    # The Quit shortcut: the launch ends and onboarding stays pending.
    quit_requested = Signal()

    def __init__(
        self,
        state_dir,
        credentials_cache=None,
        running_distros: Optional[Callable[[], List[str]]] = None,
        *,
        platform: str = sys.platform,
        discover: Optional[Callable[[], List[Candidate]]] = None,
        save: Optional[Callable[[List[Candidate]], List[Account]]] = None,
        collect: Optional[Callable[[Optional[Callable[[str], bool]]], Snapshot]] = None,
        run_hook_op: Optional[Callable[[RowData, str], tuple]] = None,
        finish_pending: Optional[Callable[[], tuple]] = None,
    ):
        super().__init__()
        self.state_dir = Path(state_dir)
        self.kit = Kit()
        self.platform = platform
        self._running_distros = running_distros
        self._discover = discover or (lambda: first_run.discover_candidates(
            credentials_cache, platform=platform))
        self._save = save or (lambda picked: first_run.save_picked_accounts(self.state_dir, picked))
        self._collect = collect or (lambda running: collect_rows(self.state_dir, running))
        self._run_hook_op = run_hook_op or (
            lambda row, op: _hook_op(self.state_dir, row, op))
        self._finish_pending = finish_pending or (
            lambda: run_finish_pending(self.state_dir, self._running_distros))

        self._platform = platform
        self.step = "welcome"
        self._discovery_started = False
        self._finished = False
        # Accounts step.
        self.candidates: Optional[List[Candidate]] = None
        self.discover_error: Optional[str] = None
        self.checks: List[Optional[_CheckBox]] = []
        self.accounts_locked = False  # saved here, or already on disk: no re-save
        self.accounts_error: Optional[str] = None
        self.saved: List[Account] = []
        self._picked_ids: set = set()
        # Hooks step: shown after a save, or for a read-only / pre-existing
        # setup (macOS, a forced run), where every row applies.
        self.show_hooks = False
        self.hook_names: Optional[set] = None
        self.snapshot: Optional[Snapshot] = None
        self.banner: Optional[Tuple[str, str, Optional[str]]] = None
        self.hook_rows: List[dict] = []
        self._checking = False
        self._requery = False
        self._finishing = False
        self._op_row: Optional[RowData] = None
        self._queue: List[RowData] = []
        self._pending = 0
        self._guard_busy = False
        self._spin = 0

        # Parentless and not stacked: it runs before the widget exists.
        self.kit.style_window(self)
        self.setWindowTitle("Tokitty setup")
        self.setMinimumSize(BASE_WIDTH, BASE_HEIGHT)
        self.resize(BASE_WIDTH, BASE_HEIGHT)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.x() + max((area.width() - BASE_WIDTH) // 2, 0),
                      area.y() + max((area.height() - BASE_HEIGHT) // 3, 0))

        self.mailbox = Mailbox()
        self.poller = QtPoller(self, self.mailbox, self._on_result, self._keep_alive,
                               on_tick=self._on_tick)
        self.quit_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Quit), self)
        self.quit_shortcut.activated.connect(self.quit_app)

        shell = hbox(self)
        self.nav = self.kit.rail(STEPS, version_text())
        shell.addWidget(self.nav)
        body = QWidget()
        body_col = vbox(body)
        body_col.setContentsMargins(20, 18, 20, 12)
        shell.addWidget(body, 1)
        self.page = QWidget()
        vbox(self.page)
        body_col.addWidget(self.page, 1)
        self.footer = QWidget()
        vbox(self.footer)
        body_col.addWidget(self.footer)

        self.skip_button: Optional[QPushButton] = None
        self.back_button: Optional[QPushButton] = None
        self.primary_button: Optional[QPushButton] = None
        self.install_all_button: Optional[QPushButton] = None
        self.finish_it_button: Optional[QPushButton] = None
        self.spinner: Optional[_Spinner] = None
        self.banner_label: Optional[QLabel] = None
        self._render()

    # -- lifecycle -------------------------------------------------------

    def present(self) -> None:
        """Show the window in front. It takes the foreground when it can."""
        self.show()
        macos.activate_app()
        self.raise_()
        self.activateWindow()

    def alive(self) -> bool:
        return not self._finished

    def _writing(self) -> bool:
        """A hook change is in flight: nothing may leave the step."""
        return self._op_row is not None or self._finishing

    def skip(self) -> None:
        if self._writing() or self._finished:
            return
        self.close()

    def finish(self) -> None:
        if self._writing() or self._finished:
            return
        self.close()

    def quit_app(self) -> None:
        """The Quit shortcut: end the launch without marking onboarding done,
        so the walkthrough shows again next time. Refused while an install
        runs, as Skip is. The walkthrough runs before the widget and the tray
        exist, so nothing else offers a way out."""
        if self._writing() or self._finished:
            return
        self._finished = True
        self.poller.cancel()
        self.quit_requested.emit()
        self.close()

    def closeEvent(self, event) -> None:
        # The window's close button is Skip: refused while an install runs.
        if self._finished:
            event.accept()
            return
        if self._writing():
            event.ignore()
            return
        self._close()
        event.accept()

    def _close(self) -> None:
        self._finished = True
        self.poller.cancel()
        try:
            first_run.mark_done(self.state_dir)
        except OSError as exc:
            print(f"tokitty: first run: could not record the walkthrough as done: {exc}",
                  file=sys.stderr)
        self.closed.emit()

    def _keep_alive(self) -> bool:
        return self._pending > 0 or self._guard_busy

    def _launch(self, tag: str, fn: Callable[[], object]) -> None:
        self._pending += 1
        run_worker(self.mailbox, tag, fn)
        self.poller.start()

    def _on_tick(self) -> None:
        if self.step == "accounts" and self.candidates is None and self.discover_error is None:
            self._spin += 1
            if self.spinner is not None:
                self.spinner.advance(self._spin)
        elif self._guard_busy and not hook_guard.held(self.state_dir):
            self._guard_busy = False
            if self.step == "hooks":
                self._query()

    # -- navigation ------------------------------------------------------

    def go(self, step: str) -> None:
        self.step = step
        if step == "accounts" and not self._discovery_started:
            self._discovery_started = True
            self._launch(_DISCOVER_TAG, self._discover)
        if step == "hooks":
            self.banner = None
            self.snapshot = None
            self._finishing = True
            self._launch(_FINISH_TAG, self._finish_pending)
        self._render()

    def back(self) -> None:
        if self._writing():
            return
        if self.step == "done":
            self.go("hooks" if self.show_hooks else "accounts")
        elif self.step == "hooks":
            self.go("accounts")
        elif self.step == "accounts":
            self.go("welcome")

    def next(self) -> None:
        """The primary button."""
        if self.step == "welcome":
            self.go("accounts")
        elif self.step == "accounts":
            self._accounts_next()
        elif self.step == "hooks":
            if not self._writing():
                self.go("done")
        else:
            self.finish()

    # -- accounts step ---------------------------------------------------

    def _accounts_next(self) -> None:
        if self.candidates is None:
            return
        if self.discover_error is not None or not self.candidates:
            self.show_hooks = False
            self.go("done")
            return
        if self.accounts_locked:
            self.go("hooks" if self.show_hooks else "done")
            return
        picked = [c for c, box in zip(self.candidates, self.checks) if box is not None and box.checked]
        if not picked:
            # Same as Skip for this step: no accounts.json, no hooks step.
            self.show_hooks = False
            self.go("done")
            return
        try:
            saved = self._save(picked)
        except (OSError, ValueError) as exc:
            self.accounts_error = f"Could not save the accounts: {exc}"
            self._render()
            return
        self.saved = list(saved)
        self.hook_names = {account.name for account in saved}
        self._picked_ids = {id(c) for c in picked}
        self.accounts_locked = True
        self.accounts_error = None
        self.show_hooks = bool(saved)
        self.go("hooks" if self.show_hooks else "done")

    def _accept_candidates(self, candidates: List[Candidate]) -> None:
        self.candidates = list(candidates)
        read_only = any(c.read_only for c in self.candidates)
        existing = (self.state_dir / ACCOUNTS_FILENAME).exists()
        if read_only or existing:
            # macOS has no account picking, and a forced run must never
            # overwrite accounts.json: show the rows, write nothing, and let
            # the hooks step cover every account that is there.
            self.accounts_locked = True
            self.show_hooks = True
            self.hook_names = None
        self.checks = []
        self._render()

    # -- results -----------------------------------------------------------

    def _on_result(self, tag: str, ok: bool, value) -> None:
        self._pending -= 1
        if tag == _DISCOVER_TAG:
            if ok:
                self._accept_candidates(value)
            else:
                self.discover_error = str(value) or value.__class__.__name__
                self._render()
        elif tag == _FINISH_TAG:
            self._finishing = False
            self._after_finish(ok, value)
        elif tag == _STATUS_TAG:
            self._checking = False
            self.snapshot = value if ok else Snapshot((), f"Could not read accounts: {value}")
            self._render_hooks_if_shown()
            if self._requery:
                self._requery = False
                self._query()
        elif tag == _OP_TAG:
            self._after_op(ok, value)

    def _after_finish(self, ok: bool, value) -> None:
        if not ok:
            self.banner = ("bad", f"Could not finish the earlier change: {value}", None)
        else:
            kind, result = value
            if kind == "busy":
                self.banner = ("warn", BUSY_MESSAGE, None)
            elif result is not None and not result.ok:
                self.banner = ("bad", result.message, None)
        self._query()

    def _after_op(self, ok: bool, value) -> None:
        failed = True
        if not ok:
            self.banner = ("bad", f"Could not change hooks: {value}", None)
        else:
            kind, result = value
            if kind == "busy":
                self.banner = ("warn", BUSY_MESSAGE, None)
            elif kind == "gone":
                self.banner = ("warn", "That account is no longer in the list.", None)
            elif result.blocked_by:
                self.banner = ("blocked", result.message, result.blocked_by)
            elif not result.ok:
                self.banner = ("bad", result.message, None)
            else:
                failed = False
        if not failed and self._queue:
            # Install for all: the next row goes straight in, so the step
            # stays write-locked until the whole queue has drained.
            self._start_next()
            return
        self._queue.clear()
        self._op_row = None
        self._query()

    # -- hooks step --------------------------------------------------------

    def _distro_running(self):
        probe = self._running_distros
        if probe is None:
            return None

        def running(name: str) -> bool:
            return name.casefold() in {d.casefold() for d in probe()}

        return running

    def _query(self) -> None:
        if self._checking:
            self._requery = True
            return
        self._checking = True
        self._render_hooks_if_shown()
        running = self._distro_running()
        self._launch(_STATUS_TAG, lambda: self._collect(running))

    def _locked(self) -> bool:
        return (self._checking or self._writing() or hook_guard.held(self.state_dir))

    def _rows(self) -> List[RowData]:
        if self.snapshot is None:
            return []
        if self.hook_names is None:
            return list(self.snapshot.rows)
        return [row for row in self.snapshot.rows if row.name in self.hook_names]

    def _installable(self) -> List[RowData]:
        return [row for row in self._rows()
                if row.config_dir and row.status is not None and row.status.state in INSTALL_STATES]

    def _start_op(self, row: RowData) -> None:
        if self._locked():
            return
        self.banner = None
        self._queue = []
        self._op_row = row
        self._render_hooks_if_shown()
        self._launch(_OP_TAG, lambda: self._run_hook_op(row, "install"))

    def install_all(self) -> None:
        if self._locked():
            return
        queue = self._installable()
        if not queue:
            return
        self.banner = None
        self._queue = queue
        self._start_next()

    def _start_next(self) -> None:
        row = self._queue.pop(0)
        self._op_row = row
        self._render_hooks_if_shown()
        self._launch(_OP_TAG, lambda: self._run_hook_op(row, "install"))

    def _finish_it(self) -> None:
        if self._locked():
            return
        self.banner = None
        self._finishing = True
        self._render_hooks_if_shown()
        self._launch(_FINISH_TAG, self._finish_pending)

    def _render_hooks_if_shown(self) -> None:
        if self.step == "hooks":
            self._render()

    def _title_for(self, row: RowData) -> str:
        for candidate in self.candidates or []:
            if row.config_dir and _same_config_dir(candidate.config_dir, row.config_dir):
                return f"{provider_label(candidate.provider)} · {candidate.where}"
        return row.name

    def _account_name_for(self, config_dir: str) -> str:
        if self.snapshot is not None:
            for row in self.snapshot.rows:
                if row.config_dir and _same_config_dir(row.config_dir, config_dir):
                    return self._title_for(row)
        return elide_middle(config_dir)

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        if not self.alive():
            return
        self.nav.paint(self.step)
        clear_layout(self.page.layout())
        self.hook_rows = []
        builder = {
            "welcome": self._build_welcome,
            "accounts": self._build_accounts,
            "hooks": self._build_hooks,
            "done": self._build_done,
        }[self.step]
        builder()
        self.page.layout().addStretch(1)
        self._render_footer()

    def _hero(self, title: str, body: str, top: int) -> None:
        box = QWidget()
        column = vbox(box)
        column.setContentsMargins(0, top, 0, 0)
        column.addWidget(self.kit.sprite_preview(sprites.resolve_palette("orange", "tabby"),
                                                 "content", False, cell=4),
                         0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(20)
        heading = text(title, font=("Segoe UI", 16, "bold"))
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(heading)
        column.addSpacing(8)
        message = text(body, color=MUTED)
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(message)
        self.page.layout().addWidget(box)

    def _note(self, value: str, *, color: str = MUTED, font: tuple = FONT_BODY,
              top: int = 0) -> QLabel:
        """A wrapped paragraph in the page."""
        label = text(value, color=color, font=font)
        label.setWordWrap(True)
        label.setContentsMargins(0, top, 0, 0)
        self.page.layout().addWidget(label)
        return label

    def _build_welcome(self) -> None:
        self.kit.page_header(self.page, "Welcome to tokitty", "A little company for your desktop.")
        self._hero(
            "Your usage, at a glance",
            "tokitty shows your Claude Code and Codex usage as cats on your desktop. Next, "
            "choose which installs to watch and turn on live activity.", 36)

    def _build_done(self) -> None:
        self.kit.page_header(self.page, "Done", "Your desktop cats are ready.")
        where = ("Click the cat in the menu bar and choose Settings…, or right-click any cat,"
                 if self._platform == "darwin" else "Right-click any cat and choose Settings…")
        self._hero("You're set", f"{where} to manage accounts, hooks, looks, and the Stream Dock.", 40)

    def _build_accounts(self) -> None:
        kit = self.kit
        kit.page_header(self.page, "Accounts", "Choose the installs you want to watch.")
        self.spinner = None
        self.checks = []
        if self.discover_error is not None:
            self._note(f"Could not look for installs: {self.discover_error}", color=BAD,
                       font=FONT_BODY)
            return
        if self.candidates is None:
            holder = QWidget()
            row = hbox(holder)
            row.setContentsMargins(0, 8, 0, 0)
            self.spinner = _Spinner()
            self.spinner.advance(self._spin)
            row.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignTop)
            row.addSpacing(12)
            column = vbox()
            column.addWidget(text("Looking for Claude Code and Codex…", font=FONT_MEDIUM))
            note = text(first_run.searching_note(self.platform), color=DIM_COLOR, font=FONT_SMALL)
            note.setContentsMargins(0, 3, 0, 0)
            column.addWidget(note)
            row.addLayout(column)
            row.addStretch(1)
            self.page.layout().addWidget(holder)
            return
        if not self.candidates:
            self.page.layout().addWidget(text("No Claude Code or Codex installs found.",
                                              font=FONT_MEDIUM))
            self._note("Install Claude Code or Codex and sign in once, then add them from "
                       "Settings. You can skip this for now.", top=4, font=FONT_BODY)
            return
        count = len(self.candidates)
        kit.section_header(self.page, "FOUND ON THIS DEVICE",
                           f"{count} install" + ("" if count == 1 else "s"), top=0)
        for candidate in self.candidates:
            self._build_candidate(candidate)
        hint = LOCKED_NOTE if self.accounts_locked and not any(
            c.read_only for c in self.candidates) else "You can change these later in Settings."
        self._note(hint, color=DIM_COLOR, font=FONT_SMALL, top=8)
        if self.accounts_error:
            self._note(self.accounts_error, color=BAD, font=FONT_SMALL, top=6)

    def _build_candidate(self, candidate: Candidate) -> None:
        row = self.kit.surface(rows=False)
        row_layout = hbox(row)
        row_layout.setContentsMargins(10, 7, 10, 7)
        # Pre-existing accounts.json or a read-only row: no box to tick.
        saved_here = bool(self.saved)
        if self.accounts_locked and not saved_here:
            box = None
        else:
            checked = (id(candidate) in self._picked_ids) if saved_here else candidate.default_checked
            box = _CheckBox(checked, enabled=not self.accounts_locked,
                            on_change=lambda _v: self._render_footer())
            row_layout.addWidget(box, 0, Qt.AlignmentFlag.AlignVCenter)
            row_layout.addSpacing(12)
        self.checks.append(box)
        info = QWidget()
        info_col = vbox(info)
        row_layout.addWidget(info, 1)
        head = QWidget()
        head_row = hbox(head)
        head_row.addWidget(text(provider_label(candidate.provider), font=FONT_MEDIUM))
        head_row.addWidget(text(f"  {candidate.where}", color=DIM_COLOR, font=FONT_SMALL))
        if candidate.detail:
            head_row.addWidget(text(f"  {candidate.detail}", color=MUTED, font=FONT_SMALL))
        head_row.addStretch(1)
        info_col.addWidget(head)
        path = text(elide_middle(candidate.config_dir), color=DIM_COLOR, font=FONT_MONO)
        path.setContentsMargins(0, 3, 0, 0)
        info_col.addWidget(path)
        if not candidate.signed_in and not candidate.read_only:
            warn = text(TRANSCRIPTS_ONLY_NOTE, color=WARN, font=FONT_SMALL)
            warn.setContentsMargins(0, 3, 0, 0)
            info_col.addWidget(warn)
        self.page.layout().addWidget(row)
        self.page.layout().addSpacing(3)

    def _build_hooks(self) -> None:
        kit = self.kit
        kit.page_header(self.page, "Live activity", "Install hooks to show what your cats are doing.")
        self.banner_holder = QWidget()
        vbox(self.banner_holder)
        self.page.layout().addWidget(self.banner_holder)
        own = self._writing()
        self._guard_busy = hook_guard.held(self.state_dir) and not own and self._pending == 0
        banner = self.banner
        if banner is None and self._guard_busy:
            banner = ("warn", BUSY_MESSAGE, None)
        label, finish = render_banner(
            kit, self.banner_holder, banner, name_for=self._account_name_for,
            on_finish=self._finish_it, finish_disabled=self._locked())
        self.banner_label = label
        self.finish_it_button = finish
        self.install_all_button = None
        if self._guard_busy:
            self.poller.start()
        if self.snapshot is None:
            self.page.layout().addWidget(text("Checking…", color=MUTED))
            return
        rows = self._rows()
        if self.snapshot.note and not rows:
            self.page.layout().addWidget(text(self.snapshot.note, color=MUTED))
        kit.section_header(self.page, "ACCOUNTS", f"{len(rows)} selected", top=0)
        locked = self._locked()
        for data in rows:
            self._build_hook_row(data, locked)
        actions = QWidget()
        actions_row = hbox(actions)
        actions_row.setContentsMargins(0, 9, 0, 0)
        actions_row.addStretch(1)
        self.install_all_button = kit.button(
            "Install for all", "primary", self.install_all,
            disabled=locked or not self._installable())
        actions_row.addWidget(self.install_all_button)
        self.page.layout().addWidget(actions)
        self._note(REMINDER, color=MUTED, font=FONT_SMALL, top=12)

    def _build_hook_row(self, data: RowData, locked: bool) -> None:
        kit = self.kit
        status = data.status
        row = kit.surface(rows=False)
        row_layout = hbox(row)
        row_layout.setContentsMargins(10, 7, 9, 7)
        info = QWidget()
        info_col = vbox(info)
        row_layout.addWidget(info, 1)
        head = QWidget()
        head_row = hbox(head)
        head_row.addWidget(text(self._title_for(data), font=FONT_MEDIUM))
        head_row.addStretch(1)
        working = self._checking or self._op_row is data
        label = ("Working…" if self._op_row is data else "Checking…") if working else None
        view = {"data": data, "pill": render_pill(kit, data, label), "install": None}
        head_row.addWidget(view["pill"])
        info_col.addWidget(head)
        path = text(elide_middle(data.config_dir) if data.config_dir
                    else "macOS Keychain (no config directory)", color=DIM_COLOR, font=FONT_MONO)
        path.setContentsMargins(0, 4, 0, 0)
        info_col.addWidget(path)
        if status is not None and status.detail and status.state != "installed" and not working:
            detail = text(status.detail, color=BAD if status.state == "error" else DIM_COLOR,
                          font=FONT_SMALL)
            detail.setWordWrap(True)
            detail.setContentsMargins(0, 3, 0, 0)
            info_col.addWidget(detail)
        if status is not None and data.config_dir and status.state not in NO_BUTTON_STATES:
            can_install = status.state in INSTALL_STATES and not locked
            view["install"] = kit.button(
                "Install", "secondary", lambda d=data: self._start_op(d), compact=True,
                disabled=not can_install)
            row_layout.addWidget(view["install"], 0, Qt.AlignmentFlag.AlignTop)
        self.page.layout().addWidget(row)
        self.page.layout().addSpacing(3)
        self.hook_rows.append(view)

    def _primary_title(self) -> Tuple[str, bool]:
        """(label, enabled) for the primary button."""
        if self.step == "welcome":
            return "Next", True
        if self.step == "hooks":
            return "Next", not self._writing()
        if self.step == "done":
            return "Finish", True
        if self.candidates is None and self.discover_error is None:
            return "Next", False
        if self.discover_error is not None or not self.candidates:
            return "Continue", True
        if self.accounts_locked:
            return "Next", True
        count = sum(1 for box in self.checks if box is not None and box.checked)
        if count == 0:
            return "Continue without accounts", True
        return f"Add {count} account" + ("" if count == 1 else "s"), True

    def _render_footer(self) -> None:
        if not self.alive():
            return
        kit = self.kit
        clear_layout(self.footer.layout())
        rule = QFrame()
        rule.setProperty("kit", "rule")
        rule.setFixedHeight(1)
        self.footer.layout().addWidget(rule)
        self.footer.layout().addSpacing(9)
        holder = QWidget()
        row = hbox(holder)
        writing = self._writing()
        self.skip_button = None
        if self.step != "done":
            self.skip_button = kit.button("Skip", "quiet", self.skip, compact=True, disabled=writing)
            row.addWidget(self.skip_button)
        row.addStretch(1)
        self.back_button = None
        if self.step != "welcome":
            self.back_button = kit.button("Back", "secondary", self.back, disabled=writing)
            row.addWidget(self.back_button)
            row.addSpacing(7)
        title, enabled = self._primary_title()
        self.primary_button = kit.button(title, "primary", self.next, disabled=not enabled)
        row.addWidget(self.primary_button)
        self.footer.layout().addWidget(holder)

