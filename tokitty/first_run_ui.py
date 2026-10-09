"""The first-run walkthrough window (#88): Welcome, Accounts, Live activity,
Done. Drawn with the Settings kit; see
docs/superpowers/specs/2026-10-08-first-run-walkthrough-design.md.

It is a Toplevel of a withdrawn root, so run_gui waits on it with
root.wait_window(). Each step applies only on its own button (Accounts'
"Add N accounts", the hook Install buttons), so Skip never undoes what the
user already confirmed. Finish, Skip and the close button all mark the
walkthrough done exactly once.

Threading is the Settings rule (settings_async): discovery, the pending-op
recovery, the hook status query and the installs run on workers that only
publish into a Mailbox; a Poller on the toplevel applies results on the Tk
thread. Never call ttk.Style().theme_use() here.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from tokitty import first_run, hook_guard, settings_accounts, sprites
from tokitty.accounts import ACCOUNTS_FILENAME, Account
from tokitty.first_run import Candidate
from tokitty.hooks_install import _same_config_dir
from tokitty.settings_accounts import (
    BUSY_MESSAGE, INSTALL_STATES, NO_BUTTON_STATES, PILLS, RowData, Snapshot,
    elide_middle, render_banner,
)
from tokitty.settings_async import Mailbox, Poller, run_worker
from tokitty.settings_ui import version_text
from tokitty.settings_widgets import (
    ACCENT_FG, BASE_HEIGHT, BASE_WIDTH, BAD, BG_COLOR, BORDER, DIM_COLOR, FONT_MEDIUM,
    FONT_MONO, FONT_SMALL, MUTED, RAIL, SURFACE, WARN, Kit, build_rail,
    draw_sprite, paint_rail, sprite_size, text,
)

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


class _CheckBox:
    """A square check box in the kit's colours; `enabled` False makes it a display."""

    def __init__(self, kit: Kit, parent, checked: bool, enabled: bool, on_change):
        px = kit.px
        self.checked = checked
        self.enabled = enabled
        self._on_change = on_change
        self.widget = tk.Label(parent, width=2, font=FONT_MEDIUM, bd=0, highlightthickness=px(1),
                               cursor="hand2" if enabled else "arrow")
        self._paint()
        if enabled:
            self.widget.bind("<Button-1>", self.toggle)

    def toggle(self, _event=None) -> None:
        if not self.enabled:
            return
        self.checked = not self.checked
        self._paint()
        self._on_change(self.checked)

    def _paint(self) -> None:
        if self.checked:
            self.widget.configure(text="✓", fg=RAIL, bg=ACCENT_FG, highlightbackground=ACCENT_FG)
        else:
            self.widget.configure(text="", fg=RAIL, bg=SURFACE, highlightbackground=DIM_COLOR)
        if self.checked and not self.enabled:
            self.widget.configure(bg="#6b4f4a", highlightbackground="#6b4f4a")


class Walkthrough:
    """Use Walkthrough(...) then root.wait_window(walkthrough.toplevel).

    The callables are test seams. Defaults: discover runs
    first_run.discover_candidates through `credentials_cache` (the process's
    WslCredentialsCache, shared with the unit loop so the WSL sweep runs
    once per launch); save, collect, run_hook_op and finish_pending are the
    backend and Accounts-tab functions."""

    def __init__(
        self,
        root: tk.Misc,
        state_dir,
        scale: float = 1.0,
        *,
        credentials_cache=None,
        platform: str = sys.platform,
        running_distros: Optional[Callable[[], List[str]]] = None,
        discover: Optional[Callable[[], List[Candidate]]] = None,
        save: Optional[Callable[[List[Candidate]], List[Account]]] = None,
        collect: Optional[Callable[[Optional[Callable[[str], bool]]], Snapshot]] = None,
        run_hook_op: Optional[Callable[[RowData, str], tuple]] = None,
        finish_pending: Optional[Callable[[], tuple]] = None,
    ):
        self.root = root
        self.state_dir = Path(state_dir)
        self.kit = Kit(scale)
        self._running_distros = running_distros
        self._discover = discover or (lambda: first_run.discover_candidates(
            credentials_cache, platform=platform))
        self._save = save or (lambda picked: first_run.save_picked_accounts(self.state_dir, picked))
        self._collect = collect or (lambda running: settings_accounts.collect_rows(self.state_dir, running))
        self._run_hook_op = run_hook_op or (
            lambda row, op: settings_accounts.run_hook_op(self.state_dir, row, op))
        self._finish_pending = finish_pending or (
            lambda: settings_accounts.run_finish_pending(self.state_dir, self._running_distros))

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

        px = self.kit.px
        top = self.toplevel = tk.Toplevel(root)
        top.title("Tokitty setup")
        top.configure(bg=BG_COLOR)
        top.resizable(False, False)
        width, height = px(BASE_WIDTH), px(BASE_HEIGHT)
        # No transient(): its master is the withdrawn root, and Windows hides
        # a transient window whose owner is hidden.
        x = max((top.winfo_screenwidth() - width) // 2, 0)
        y = max((top.winfo_screenheight() - height) // 3, 0)
        top.geometry(f"{width}x{height}+{x}+{y}")
        top.protocol("WM_DELETE_WINDOW", self.skip)
        top.bind("<Destroy>", self._on_destroy, add="+")

        self.mailbox = Mailbox()
        self.poller = Poller(top, self.mailbox, self._on_result, self._keep_alive,
                             on_tick=self._on_tick)

        shell = tk.Frame(top, bg=BG_COLOR)
        shell.pack(fill="both", expand=True)
        rail = tk.Frame(shell, bg=RAIL, width=px(132))
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        self.nav = build_rail(self.kit, rail, STEPS, version_text())
        content = tk.Frame(shell, bg=BG_COLOR)
        content.pack(side="left", fill="both", expand=True)
        body = tk.Frame(content, bg=BG_COLOR)
        body.pack(fill="both", expand=True, padx=px(20), pady=(px(18), px(12)))
        self.footer = tk.Frame(body, bg=BG_COLOR)
        self.footer.pack(side="bottom", fill="x")
        self.page = tk.Frame(body, bg=BG_COLOR)
        self.page.pack(fill="both", expand=True)

        self.skip_button: Optional[tk.Button] = None
        self.back_button: Optional[tk.Button] = None
        self.primary_button: Optional[tk.Button] = None
        self.install_all_button: Optional[tk.Button] = None
        self.finish_it_button: Optional[tk.Button] = None
        self.spinner: Optional[tk.Canvas] = None
        self.banner_label: Optional[tk.Label] = None
        self._render()
        top.update_idletasks()
        try:
            top.lift()
            top.focus_force()
        except tk.TclError:
            pass

    # -- lifecycle -------------------------------------------------------

    def alive(self) -> bool:
        try:
            return not self._finished and bool(self.toplevel.winfo_exists())
        except tk.TclError:
            return False

    def _writing(self) -> bool:
        """A hook change is in flight: nothing may leave the step."""
        return self._op_row is not None or self._finishing

    def skip(self) -> None:
        if self._writing() or self._finished:
            return
        self._close()

    def finish(self) -> None:
        if self._writing() or self._finished:
            return
        self._close()

    def _close(self) -> None:
        self._finished = True
        self.poller.cancel()
        try:
            first_run.mark_done(self.state_dir)
        except OSError as exc:
            print(f"tokitty: first run: could not record the walkthrough as done: {exc}",
                  file=sys.stderr)
        try:
            self.toplevel.destroy()
        except tk.TclError:
            pass

    def _on_destroy(self, event) -> None:
        if event.widget is self.toplevel:
            self.poller.cancel()

    def _keep_alive(self) -> bool:
        return self._pending > 0 or self._guard_busy

    def _launch(self, tag: str, fn: Callable[[], object]) -> None:
        self._pending += 1
        run_worker(self.mailbox, tag, fn)
        self.poller.start()

    def _on_tick(self) -> None:
        if self.step == "accounts" and self.candidates is None and self.discover_error is None:
            self._spin += 1
            self._paint_spinner()
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
        paint_rail(self.nav, self.step)
        for child in self.page.winfo_children():
            child.destroy()
        self.hook_rows = []
        builder = {
            "welcome": self._build_welcome,
            "accounts": self._build_accounts,
            "hooks": self._build_hooks,
            "done": self._build_done,
        }[self.step]
        builder()
        self._render_footer()

    def _hero(self, title: str, body: str, top: int) -> None:
        px = self.kit.px
        box = tk.Frame(self.page, bg=BG_COLOR)
        box.pack(fill="x", pady=(px(top), 0))
        cell = px(4)
        width, height = sprite_size()
        canvas = tk.Canvas(box, width=width * cell, height=height * cell, bg=BG_COLOR,
                           highlightthickness=0)
        canvas.pack()
        draw_sprite(canvas, sprites.resolve_palette("orange", "tabby"), cell)
        # text() anchors west; a hero is centred.
        heading = text(box, title, font=("Segoe UI", 16, "bold"))
        heading.configure(anchor="center")
        heading.pack(fill="x", pady=(px(20), px(8)))
        message = text(box, body, color=MUTED, wraplength=px(355), justify="center")
        message.configure(anchor="center")
        message.pack()

    def _build_welcome(self) -> None:
        self.kit.page_header(self.page, "Welcome to tokitty", "A little company for your desktop.")
        self._hero(
            "Your usage, at a glance",
            "tokitty shows your Claude Code and Codex usage as cats on your desktop. Next, "
            "choose which installs to watch and turn on live activity.", 36)

    def _build_done(self) -> None:
        self.kit.page_header(self.page, "Done", "Your desktop cats are ready.")
        where = ("Choose Tokitty ▸ Settings… from the menu bar, or right-click any cat,"
                 if self._platform == "darwin" else "Right-click any cat and choose Settings…")
        self._hero("You're set", f"{where} to manage accounts, hooks, looks, and the Stream Dock.", 40)

    def _build_accounts(self) -> None:
        kit = self.kit
        px = kit.px
        kit.page_header(self.page, "Accounts", "Choose the installs you want to watch.")
        self.spinner = None
        self.checks = []
        if self.discover_error is not None:
            text(self.page, f"Could not look for installs: {self.discover_error}",
                 color=BAD, wraplength=px(440), justify="left").pack(anchor="w")
            return
        if self.candidates is None:
            holder = tk.Frame(self.page, bg=BG_COLOR)
            holder.pack(fill="x", pady=(px(8), 0))
            self.spinner = tk.Canvas(holder, width=px(22), height=px(22), bg=BG_COLOR,
                                     highlightthickness=0)
            self.spinner.pack(side="left", padx=(0, px(12)))
            box = tk.Frame(holder, bg=BG_COLOR)
            box.pack(side="left")
            text(box, "Looking for Claude Code and Codex…", font=FONT_MEDIUM).pack(anchor="w")
            text(box, "Checking this PC and WSL installs", color=DIM_COLOR,
                 font=FONT_SMALL).pack(anchor="w", pady=(px(3), 0))
            self._paint_spinner()
            return
        if not self.candidates:
            text(self.page, "No Claude Code or Codex installs found.", font=FONT_MEDIUM).pack(anchor="w")
            text(self.page, "Install Claude Code or Codex and sign in once, then add them from "
                 "Settings. You can skip this for now.", color=MUTED, wraplength=px(440),
                 justify="left").pack(anchor="w", pady=(px(4), 0))
            return
        count = len(self.candidates)
        kit.section_header(self.page, "FOUND ON THIS DEVICE",
                           f"{count} install" + ("" if count == 1 else "s"), top=0)
        for candidate in self.candidates:
            self._build_candidate(candidate)
        hint = LOCKED_NOTE if self.accounts_locked and not any(
            c.read_only for c in self.candidates) else "You can change these later in Settings."
        text(self.page, hint, color=DIM_COLOR, font=FONT_SMALL).pack(anchor="w", pady=(px(8), 0))
        if self.accounts_error:
            text(self.page, self.accounts_error, color=BAD, font=FONT_SMALL, wraplength=px(440),
                 justify="left").pack(anchor="w", pady=(px(6), 0))

    def _build_candidate(self, candidate: Candidate) -> None:
        kit = self.kit
        px = kit.px
        row = tk.Frame(self.page, bg=SURFACE)
        row.pack(fill="x", pady=(0, px(3)))
        # Pre-existing accounts.json or a read-only row: no box to tick.
        saved_here = bool(self.saved)
        if self.accounts_locked and not saved_here:
            box = None
        else:
            checked = (id(candidate) in self._picked_ids) if saved_here else candidate.default_checked
            box = _CheckBox(kit, row, checked, enabled=not self.accounts_locked,
                            on_change=lambda _v: self._render_footer())
            box.widget.pack(side="left", padx=(px(10), px(4)), pady=px(8))
        self.checks.append(box)
        info = tk.Frame(row, bg=SURFACE)
        info.pack(side="left", fill="both", expand=True, padx=(px(8), px(8)), pady=px(7))
        head = tk.Frame(info, bg=SURFACE)
        head.pack(fill="x")
        text(head, provider_label(candidate.provider), bg=SURFACE, font=FONT_MEDIUM).pack(side="left")
        text(head, f"  {candidate.where}", bg=SURFACE, color=DIM_COLOR, font=FONT_SMALL).pack(side="left")
        if candidate.detail:
            text(head, f"  {candidate.detail}", bg=SURFACE, color=MUTED,
                 font=FONT_SMALL).pack(side="left")
        text(info, elide_middle(candidate.config_dir), bg=SURFACE, color=DIM_COLOR,
             font=FONT_MONO).pack(anchor="w", pady=(px(3), 0))
        if not candidate.signed_in and not candidate.read_only:
            text(info, TRANSCRIPTS_ONLY_NOTE, bg=SURFACE, color=WARN,
                 font=FONT_SMALL).pack(anchor="w", pady=(px(3), 0))

    def _paint_spinner(self) -> None:
        canvas = self.spinner
        if canvas is None:
            return
        try:
            canvas.delete("all")
            px = self.kit.px
            canvas.create_oval(px(3), px(3), px(19), px(19), outline=BORDER, width=px(2))
            canvas.create_arc(px(3), px(3), px(19), px(19), start=(-self._spin * 40) % 360,
                              extent=90, style="arc", outline=ACCENT_FG, width=px(2))
        except tk.TclError:
            pass

    def _build_hooks(self) -> None:
        kit = self.kit
        px = kit.px
        kit.page_header(self.page, "Live activity", "Install hooks to show what your cats are doing.")
        self.banner_holder = tk.Frame(self.page, bg=BG_COLOR)
        self.banner_holder.pack(fill="x")
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
        if self._guard_busy:
            self.poller.start()
        if self.snapshot is None:
            text(self.page, "Checking…", color=MUTED).pack(anchor="w")
            return
        rows = self._rows()
        if self.snapshot.note and not rows:
            text(self.page, self.snapshot.note, color=MUTED).pack(anchor="w")
        kit.section_header(self.page, "ACCOUNTS", f"{len(rows)} selected", top=0)
        locked = self._locked()
        for data in rows:
            self._build_hook_row(data, locked)
        actions = tk.Frame(self.page, bg=BG_COLOR)
        actions.pack(fill="x", pady=(px(9), 0))
        self.install_all_button = kit.button(
            actions, "Install for all", "primary", self.install_all,
            disabled=locked or not self._installable())
        self.install_all_button.pack(side="right")
        text(self.page, REMINDER, color=MUTED, font=FONT_SMALL, wraplength=px(440),
             justify="left").pack(anchor="w", pady=(px(12), 0))

    def _build_hook_row(self, data: RowData, locked: bool) -> None:
        kit = self.kit
        px = kit.px
        status = data.status
        row = tk.Frame(self.page, bg=SURFACE)
        row.pack(fill="x", pady=(0, px(3)))
        info = tk.Frame(row, bg=SURFACE)
        info.pack(side="left", fill="both", expand=True, padx=(px(10), 0), pady=px(7))
        head = tk.Frame(info, bg=SURFACE)
        head.pack(fill="x")
        text(head, self._title_for(data), bg=SURFACE, font=FONT_MEDIUM).pack(side="left")
        working = self._checking or self._op_row is data
        if status is None or data.config_dir is None:
            label, kind = "Read-only", "neutral"
        elif working:
            label, kind = ("Working…" if self._op_row is data else "Checking…"), "neutral"
        else:
            label, kind = PILLS.get(status.state, (status.state, "neutral"))
        view = {"data": data, "pill": kit.pill(head, label, kind), "install": None}
        view["pill"].pack(side="right", padx=(px(4), px(3)))
        path = elide_middle(data.config_dir) if data.config_dir else "macOS Keychain (no config directory)"
        text(info, path, bg=SURFACE, color=DIM_COLOR, font=FONT_MONO).pack(anchor="w", pady=(px(4), 0))
        if status is not None and status.detail and status.state != "installed" and not working:
            text(info, status.detail, bg=SURFACE, font=FONT_SMALL, wraplength=px(330),
                 color=BAD if status.state == "error" else DIM_COLOR,
                 justify="left").pack(anchor="w", pady=(px(3), 0))
        if status is not None and data.config_dir and status.state not in NO_BUTTON_STATES:
            actions = tk.Frame(row, bg=SURFACE)
            actions.pack(side="right", padx=px(9))
            can_install = status.state in INSTALL_STATES and not locked
            view["install"] = kit.button(
                actions, "Install", "secondary", compact=True, disabled=not can_install,
                command=lambda d=data: self._start_op(d))
            view["install"].pack()
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
        px = kit.px
        for child in self.footer.winfo_children():
            child.destroy()
        tk.Frame(self.footer, height=px(1), bg=BORDER).pack(fill="x", pady=(0, px(9)))
        row = tk.Frame(self.footer, bg=BG_COLOR)
        row.pack(fill="x")
        writing = self._writing()
        self.skip_button = None
        if self.step != "done":
            self.skip_button = kit.button(row, "Skip", "quiet", self.skip, compact=True,
                                          disabled=writing)
            self.skip_button.pack(side="left")
        title, enabled = self._primary_title()
        self.primary_button = kit.button(row, title, "primary", self.next, disabled=not enabled)
        self.primary_button.pack(side="right")
        self.back_button = None
        if self.step != "welcome":
            self.back_button = kit.button(row, "Back", "secondary", self.back, disabled=writing)
            self.back_button.pack(side="right", padx=(0, px(7)))
