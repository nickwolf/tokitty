"""Glue between the Stream Dock pieces: server, model, watchers, focus worker.

Only the Tk thread touches the DeckModel. The HTTP server thread and the focus
worker thread both put what they have on one inbound queue, and `tick()` (called
from Tk every UI_REFRESH_MS) drains it in arrival order, applies each item to
the model, feeds the model fresh inputs, executes the actions that come back,
and publishes the new plan for the server to serve. Everything Windows-specific
is injected so this runs under pytest.
"""
from __future__ import annotations

import queue
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from tokitty.accounts import Account
from tokitty.activity import SessionView
from tokitty.streamdock import launch as launch_mod
from tokitty.streamdock import pending as pending_mod
from tokitty.streamdock.keyimages import spec_image
from tokitty.streamdock.model import (
    CancelOverlay,
    Decide,
    DeckModel,
    Focus,
    FocusAndOpen,
    Interrupt,
    KeySpec,
    NewSession,
    Noop,
    PlanBox,
    SessionRef,
    VerifyThenDecide,
)
from tokitty.streamdock.pending import PendingRequest, PendingWatcher
from tokitty.streamdock.server import DeckEvent, DeckServer, apply_event
from tokitty.streamdock.wt_focus import (
    FOCUSED,
    FocusDone,
    FocusWorker,
    TitleDone,
    VerifyDone,
    default_focus,
)

HEARTBEAT_S = 30.0
TITLE_REFRESH_S = 10.0
NEW_SESSION_REPEAT_S = 1.0

TokittyDir = Union[str, Path, Callable[[], Optional[Union[str, Path]]], None]


@dataclass(frozen=True)
class AccountInput:
    """What the runtime needs to know about one account.

    `tokitty_dir` is where that account's hook writes pending/ and reads
    decisions/ (a callable is resolved on every use, like PendingWatcher's).
    """
    index: int
    name: str
    account: Optional[Account]
    config_dir: str
    tokitty_dir: TokittyDir


def _log(message: str) -> None:
    print(f"tokitty: streamdock: {message}", file=sys.stderr)


def _default_server(port, token, box, inbound, *, image_fn, meta_fn):
    return DeckServer(port, token, box, inbound, image_fn=image_fn, meta_fn=meta_fn)


def _default_watcher(acct: AccountInput):
    return PendingWatcher(acct.tokitty_dir, account_index=acct.index)


def _default_worker(on_result):
    return FocusWorker(default_focus, on_result)


class StreamdockRuntime:
    def __init__(
        self,
        port: int,
        token: str,
        presets: List[dict],
        accounts: List[AccountInput],
        *,
        palette_fn: Callable[[int], Dict[str, str]],
        open_in_window: Optional[Callable[[PendingRequest], None]] = None,
        image_fn: Optional[Callable[[KeySpec], str]] = None,
        server_factory: Callable[..., Any] = _default_server,
        watcher_factory: Callable[[AccountInput], Any] = _default_watcher,
        worker_factory: Callable[[Callable[[Any], None]], Any] = _default_worker,
        launch_fn: Callable[[dict, Account], None] = launch_mod.launch,
        write_decision_fn: Callable[..., None] = pending_mod.write_decision,
        touch_enabled_fn: Callable[[Any], None] = pending_mod.touch_enabled,
        clear_enabled_fn: Callable[[Any], None] = pending_mod.clear_enabled,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._presets = list(presets)
        self._accounts: Dict[int, AccountInput] = {a.index: a for a in accounts}
        self._open_in_window = open_in_window or (lambda request: None)
        self._launch = launch_fn
        self._write_decision = write_decision_fn
        self._touch_enabled = touch_enabled_fn
        self._clear_enabled = clear_enabled_fn
        self._monotonic = monotonic_fn
        self._worker_factory = worker_factory

        self._model = DeckModel()
        self._box = PlanBox()
        self._inbound: "queue.Queue[Any]" = queue.Queue()
        if image_fn is None:
            def image_fn(spec: KeySpec) -> str:
                return spec_image(spec, palette_fn)
        self._server = server_factory(port, token, self._box, self._inbound, image_fn=image_fn, meta_fn=self._meta)
        self._watchers: Dict[int, Any] = {a.index: watcher_factory(a) for a in accounts}
        self._worker: Any = None

        self._published_rev = -1
        self._was_connected = False
        self._last_touch = 0.0
        self._pending: List[PendingRequest] = []
        self._held: Dict[str, Tuple[PendingRequest, str]] = {}
        self._opens: Dict[SessionRef, Tuple[int, PendingRequest]] = {}
        self._in_window: set = set()
        self._launched_at: Dict[str, float] = {}
        self._titles: Dict[SessionRef, str] = {}
        self._title_asked: Dict[SessionRef, float] = {}
        self._title_inflight: set = set()

    @staticmethod
    def configured(settings: Any) -> bool:
        """True when `--install-streamdock` has stored a port and a token."""
        port = getattr(settings, "streamdock_port", 0)
        return isinstance(port, int) and port > 0 and bool(getattr(settings, "streamdock_token", ""))

    @property
    def connected(self) -> bool:
        try:
            return bool(self._server.connected())
        except Exception:
            return False

    # lifecycle

    def start(self) -> None:
        """Start the focus worker, the watchers and the server. A bind error propagates."""
        try:
            self._worker = self._worker_factory(self._inbound.put)
            for watcher in self._watchers.values():
                watcher.start()
            self._server.start()
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        """Stop everything and clear every enabled marker. Never raises."""
        steps: List[Callable[[], None]] = [self._server.stop]
        steps += [w.stop for w in self._watchers.values()]
        if self._worker is not None:
            steps.append(self._worker.stop)
        steps += [lambda a=a: self._clear(a) for a in self._accounts.values()]
        for step in steps:
            try:
                step()
            except Exception as exc:
                _log(f"stop: {exc}")
        self._was_connected = False

    # Tk thread

    def tick(
        self,
        sessions_by_account: Dict[int, List[SessionView]],
        usage_by_account: Dict[int, Tuple[float, float, bool]],
    ) -> None:
        now = self._monotonic()
        actions: List[Any] = []
        self._drain(actions)
        self._heartbeat(now)
        self._pending = [req for w in self._watchers.values() for req in w.get_pending()]
        self._request_titles(sessions_by_account, now)
        self._model.update(
            sessions_by_account, self._pending, usage_by_account, titles=dict(self._titles)
        )
        for action in actions:
            self._run_action(action, now)
        if self._model.revision != self._published_rev:
            self._published_rev = self._model.revision
            self._box.publish(self._published_rev, self._model.render_plan())

    def in_window_opened(self, nonce: str) -> None:
        self._in_window.add(nonce)
        self._model.set_in_window(nonce, True)

    def in_window_closed(self, nonce: str) -> None:
        self._in_window.discard(nonce)
        self._model.set_in_window(nonce, False)

    def decide_from_window(self, nonce: str, behavior: str) -> bool:
        """The in-window Allow/Deny buttons. Only a currently pending request is answered."""
        for req in self._pending:
            if req.nonce == nonce:
                return self._decide(req, behavior)
        return False

    # inbound

    def _drain(self, actions: List[Any]) -> None:
        while True:
            try:
                item = self._inbound.get_nowait()
            except queue.Empty:
                return
            try:
                if isinstance(item, DeckEvent):
                    action = apply_event(self._model, item)
                    if action is not None:
                        actions.append(action)
                else:
                    self._apply_result(item)
            except Exception as exc:
                _log(f"inbound {type(item).__name__}: {exc}")

    def _apply_result(self, item: Any) -> None:
        if isinstance(item, FocusDone):
            self._model.focus_result(item.session, item.status, item.seq)
            wanted = self._opens.get(item.session)
            if wanted is not None and wanted[0] == item.seq:
                del self._opens[item.session]
                if item.status != FOCUSED:
                    self._open(wanted[1])
        elif isinstance(item, VerifyDone):
            held = self._held.pop(item.nonce, None)
            if held is None:
                return
            if item.ok:
                if self._decide(held[0], held[1]):
                    self._model.verify_result(item.nonce, True)
            else:
                self._model.verify_result(item.nonce, False)
        elif isinstance(item, TitleDone):
            self._title_inflight.discard(item.session)
            if item.title:
                self._titles[item.session] = item.title
        # InterruptDone carries nothing the model needs.

    # actions

    def _run_action(self, action: Any, now: float) -> None:
        try:
            if isinstance(action, Focus):
                self._opens.pop(action.session, None)
                self._worker.submit_focus(action.session, action.seq, self._config_dir(action.session.account_index))
            elif isinstance(action, FocusAndOpen):
                if action.overlay:
                    self._opens[action.session] = (action.seq, action.request)
                else:
                    self._opens.pop(action.session, None)
                    self._open(action.request)
                self._worker.submit_focus(action.session, action.seq, self._config_dir(action.session.account_index))
            elif isinstance(action, Decide):
                self._decide(action.request, action.behavior)
            elif isinstance(action, VerifyThenDecide):
                self._held[action.request.nonce] = (action.request, action.behavior)
                self._worker.submit_verify(action.request.nonce, SessionRef(action.request.account_index, action.request.session_id))
            elif isinstance(action, Interrupt):
                self._worker.submit_interrupt(action.session)
            elif isinstance(action, NewSession):
                self._new_session(action.preset, now)
            elif isinstance(action, (CancelOverlay, Noop)):
                pass
        except Exception as exc:
            _log(f"{type(action).__name__}: {exc}")

    def _config_dir(self, index: int) -> str:
        acct = self._accounts.get(index)
        return acct.config_dir if acct is not None else ""

    def _open(self, request: PendingRequest) -> None:
        if request.nonce in self._in_window:
            return
        if request.nonce not in {r.nonce for r in self._pending}:
            return
        self._open_in_window(request)

    def _decide(self, req: PendingRequest, behavior: str) -> bool:
        acct = self._accounts.get(req.account_index)
        directory = self._dir(acct) if acct is not None else None
        if not directory:
            _log(f"decision: no tokitty dir for account {req.account_index}")
            return False
        try:
            self._write_decision(directory, req, behavior)
        except Exception as exc:
            _log(f"decision write: {exc}")
            return False
        return True

    def _new_session(self, name: str, now: float) -> None:
        last = self._launched_at.get(name)
        if last is not None and now - last < NEW_SESSION_REPEAT_S:
            return
        self._launched_at[name] = now
        preset = launch_mod.find_preset(self._presets, name)
        if preset is None:
            _log(f"no preset named {name!r}")
            return
        acct = self._accounts.get(preset.get("account_index"))
        if acct is None or acct.account is None:
            _log(f"preset {name!r} names an unknown account")
            return
        self._launch(preset, acct.account)

    # heartbeat and titles

    @staticmethod
    def _dir(acct: AccountInput) -> Optional[Union[str, Path]]:
        directory = acct.tokitty_dir() if callable(acct.tokitty_dir) else acct.tokitty_dir
        return directory or None

    def _clear(self, acct: AccountInput) -> None:
        directory = self._dir(acct)
        if directory:
            self._clear_enabled(directory)

    def _heartbeat(self, now: float) -> None:
        connected = self.connected
        if connected and not self._was_connected:
            for watcher in self._watchers.values():
                watcher.set_active(True)
            self._touch_all()
            self._last_touch = now
        elif connected and now - self._last_touch >= HEARTBEAT_S:
            self._touch_all()
            self._last_touch = now
        elif not connected and self._was_connected:
            for acct in self._accounts.values():
                self._clear(acct)
            for watcher in self._watchers.values():
                watcher.set_active(False)
        self._was_connected = connected

    def _touch_all(self) -> None:
        for acct in self._accounts.values():
            directory = self._dir(acct)
            if directory:
                self._touch_enabled(directory)

    def _request_titles(self, sessions_by_account: Dict[int, List[SessionView]], now: float) -> None:
        live = {SessionRef(i, v.session_id) for i, views in sessions_by_account.items() for v in views}
        for ref in [r for r in self._title_asked if r not in live]:
            del self._title_asked[ref]
            self._titles.pop(ref, None)
            self._title_inflight.discard(ref)
            self._submit(lambda ref=ref: self._worker.forget(ref))
        for ref in sorted(live, key=lambda r: (r.account_index, r.session_id)):
            asked = self._title_asked.get(ref)
            if ref in self._title_inflight or (asked is not None and now - asked < TITLE_REFRESH_S):
                continue
            self._title_asked[ref] = now
            self._title_inflight.add(ref)
            self._submit(lambda ref=ref: self._worker.submit_title(ref, self._config_dir(ref.account_index)))

    def _submit(self, job: Callable[[], None]) -> None:
        if self._worker is None:
            return
        try:
            job()
        except Exception as exc:
            _log(f"worker: {exc}")

    # server

    def _meta(self) -> dict:
        return {
            "accounts": [{"index": a.index, "name": a.name} for a in self._accounts.values()],
            "presets": [p.get("name") for p in self._presets if isinstance(p, dict) and p.get("name")],
        }
