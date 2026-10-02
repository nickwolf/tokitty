"""The deck model: which Stream Dock key shows what, and what a press means.

Pure state, no I/O. The Tk thread owns a DeckModel and is the only caller; after
each tick it publishes an immutable snapshot into a PlanBox, which is the only
thing the HTTP thread touches. Keys are described by KeySpec values, never
images, so the renderer and runtime stay separate from the logic here.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Set, Tuple, Union

from tokitty.activity import SessionView
from tokitty.streamdock.pending import PendingRequest

FOCUSED = "focused"
UNVERIFIED = "unverified"
DECISION_ROLES = ("allow", "deny", "always")


@dataclass(frozen=True)
class SessionRef:
    account_index: int
    session_id: str


@dataclass(frozen=True)
class KeySpec:
    """One key, fully described. Fields not used by a kind keep their defaults."""
    # slot, overflow, usage, interrupt, new, decision, preview, status
    kind: str
    ref: Optional[SessionRef] = None
    state: str = ""
    tool_label: str = ""
    title: str = ""
    pending: bool = False
    focus: str = ""
    count: int = 0
    alert: bool = False
    decision: str = ""
    armed: bool = False
    text: str = ""
    index: int = 0
    total: int = 0
    account: int = 0
    session_pct: float = 0.0
    weekly_pct: float = 0.0
    warn: bool = False


@dataclass(frozen=True)
class Focus:
    session: SessionRef
    seq: int = 0


@dataclass(frozen=True)
class FocusAndOpen:
    session: SessionRef
    request: PendingRequest
    overlay: bool
    seq: int = 0


@dataclass(frozen=True)
class Decide:
    request: PendingRequest
    behavior: str


@dataclass(frozen=True)
class VerifyThenDecide:
    request: PendingRequest
    behavior: str


@dataclass(frozen=True)
class Interrupt:
    session: SessionRef


@dataclass(frozen=True)
class NewSession:
    preset: str


@dataclass(frozen=True)
class CancelOverlay:
    pass


@dataclass(frozen=True)
class Noop:
    reason: str = ""


Action = Union[Focus, FocusAndOpen, Decide, VerifyThenDecide, Interrupt, NewSession, CancelOverlay, Noop]


class Snapshot(NamedTuple):
    revision: int
    plan: Mapping[str, KeySpec]


class PlanBox:
    """The latest published plan, shared with the HTTP thread."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._snap = Snapshot(0, MappingProxyType({}))

    def publish(self, revision: int, plan: Mapping[str, KeySpec]) -> None:
        snap = Snapshot(revision, MappingProxyType(dict(plan)))
        with self._cond:
            self._snap = snap
            self._cond.notify_all()

    def get(self) -> Snapshot:
        with self._cond:
            return self._snap

    def wait_for_change(self, revision: int, timeout: float) -> Snapshot:
        """Block until the published revision differs from `revision` or timeout."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._snap.revision == revision:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            return self._snap


@dataclass
class _Key:
    coords: Tuple[int, int]
    device: str
    role: Optional[Tuple[str, str]]


@dataclass
class _Overlay:
    pressed: str
    nonce: str


def _parse_coords(coords: Any) -> Tuple[int, int]:
    """(row, column) from VSD's {"column": c, "row": r}."""
    try:
        return int(coords["row"]), int(coords["column"])
    except (TypeError, KeyError, ValueError):
        return 0, 0


def _parse_role(settings: Any) -> Optional[Tuple[str, str]]:
    role = settings.get("role") if isinstance(settings, Mapping) else None
    if not isinstance(role, str):
        return None
    if role in ("slot", "interrupt") or role in DECISION_ROLES:
        return role, ""
    kind, _, arg = role.partition(":")
    if kind == "usage":
        try:
            return kind, str(int(arg))
        except ValueError:
            return None
    if kind == "new" and arg:
        return kind, arg
    return None


class DeckModel:
    def __init__(self) -> None:
        self._keys: Dict[str, _Key] = {}
        self._views: Dict[SessionRef, SessionView] = {}
        self._assign: Dict[SessionRef, int] = {}
        self._pending: Dict[SessionRef, PendingRequest] = {}
        self._pending_nonces: Set[str] = set()
        self._usage: Dict[int, Tuple[float, float, bool]] = {}
        self._focus: Dict[SessionRef, str] = {}
        self._focus_seq: Dict[SessionRef, int] = {}
        self._live_count: Dict[SessionRef, int] = {}
        self._titles: Dict[SessionRef, str] = {}
        self._in_window: Set[str] = set()
        self._sent: Set[str] = set()
        self._overlay: Optional[_Overlay] = None
        self._last_focused: Optional[SessionRef] = None
        self._overflow_ref: Optional[SessionRef] = None
        self._plan: Dict[str, KeySpec] = {}
        self._revision = 0

    # inputs

    def appear(self, context: str, coords: Any, device: str, settings: Any) -> None:
        self._keys[context] = _Key(_parse_coords(coords), device, _parse_role(settings))
        self._refresh()

    def disappear(self, context: str) -> None:
        self._keys.pop(context, None)
        self._refresh()

    def update(
        self,
        sessions_by_account: Dict[int, List[SessionView]],
        pending: List[PendingRequest],
        usage: Dict[int, Tuple[float, float, bool]],
        *,
        titles: Optional[Dict[SessionRef, str]] = None,
    ) -> None:
        self._views = {
            SessionRef(acct, v.session_id): v for acct, views in sessions_by_account.items() for v in views
        }
        self._assign_slots()
        self._pending = {}
        for req in sorted(pending, key=lambda r: r.started):
            ref = SessionRef(req.account_index, req.session_id)
            if ref in self._views:
                self._pending.setdefault(ref, req)
        self._pending_nonces = {r.nonce for r in pending}
        self._live_count = {}
        for req in pending:
            ref = SessionRef(req.account_index, req.session_id)
            self._live_count[ref] = self._live_count.get(ref, 0) + 1
        self._sent &= self._pending_nonces
        self._in_window &= self._pending_nonces
        self._usage = dict(usage)
        self._focus = {r: st for r, st in self._focus.items() if r in self._views}
        self._focus_seq = {r: n for r, n in self._focus_seq.items() if r in self._views}
        self._titles = dict(titles or {})
        self._refresh()

    def set_in_window(self, nonce: str, open: bool) -> None:
        """Task 11 reports that tokitty's own window shows (or stopped showing) this request."""
        if open:
            self._in_window.add(nonce)
        else:
            self._in_window.discard(nonce)
        self._refresh()

    def focus_result(self, session: SessionRef, status: str, seq: int) -> None:
        """The focus worker's result for a Focus or FocusAndOpen action carrying `seq`.

        Ignored when a newer slot press for the session has started another job.
        """
        if session not in self._views or seq != self._focus_seq.get(session, 0):
            return
        self._focus[session] = status
        self._refresh()

    def verify_result(self, nonce: str, ok: bool) -> None:
        """The runtime's answer to VerifyThenDecide."""
        if ok:
            self._sent.add(nonce)
        else:
            for ref, req in self._pending.items():
                if req.nonce == nonce:
                    self._focus[ref] = UNVERIFIED
        self._refresh()

    # outputs

    @property
    def revision(self) -> int:
        return self._revision

    def render_plan(self) -> Dict[str, KeySpec]:
        return dict(self._plan)

    def press(self, context: str) -> Action:
        key = self._keys.get(context)
        if key is None:
            return Noop("unknown key")
        if self._overlay is not None:
            if context == self._overlay.pressed:
                self._overlay = None
                self._refresh()
                return CancelOverlay()
            role = self._overlay_layout().get(context)
            if role is not None:
                return self._press_overlay(role)
        if key.role is None:
            return Noop("no role")
        kind, arg = key.role
        if kind == "slot":
            return self._press_slot(context)
        if kind == "interrupt":
            ref = self._last_focused
            if ref is not None and ref in self._views and self._focus.get(ref) == FOCUSED:
                return Interrupt(ref)
            return Noop("session tab not confirmed")
        if kind == "new":
            return NewSession(arg)
        if kind in DECISION_ROLES:
            return Noop("no permission prompt open")
        return Noop("usage key")

    # slots

    def _assign_slots(self) -> None:
        self._assign = {r: i for r, i in self._assign.items() if r in self._views}
        used = set(self._assign.values())
        fresh = sorted((r for r in self._views if r not in self._assign), key=self._order)
        for ref in fresh:
            i = 0
            while i in used:
                i += 1
            self._assign[ref] = i
            used.add(i)
        if self._last_focused not in self._views:
            self._last_focused = None

    def _order(self, ref: SessionRef) -> Tuple[float, int, str]:
        return self._views[ref].first_seen, ref.account_index, ref.session_id

    def _slot_contexts(self) -> List[str]:
        return self._sorted_contexts(lambda k: k.role is not None and k.role[0] == "slot")

    def _sorted_contexts(self, want) -> List[str]:
        items = [(k.coords, c) for c, k in self._keys.items() if want(k)]
        return [c for _, c in sorted(items)]

    def _layout_slots(self) -> Tuple[Dict[str, SessionRef], Optional[str], List[SessionRef]]:
        """Regular slot contexts to sessions, the overflow context, and the overflow pool."""
        ctxs = self._slot_contexts()
        by_index = {i: r for r, i in self._assign.items()}
        if not ctxs:
            return {}, None, []
        needs_overflow = bool(by_index) and max(by_index) >= len(ctxs)
        regular = len(ctxs) - 1 if needs_overflow else len(ctxs)
        shown = {ctxs[i]: by_index[i] for i in range(regular) if i in by_index}
        if not needs_overflow:
            return shown, None, []
        pool = [by_index[i] for i in sorted(by_index) if i >= regular]
        pool.sort(key=lambda r: r not in self._pending)
        return shown, ctxs[-1], pool

    def _overflow_shown(self, pool: List[SessionRef]) -> SessionRef:
        cur = self._overflow_ref
        if cur not in pool or (cur not in self._pending and pool[0] in self._pending):
            cur = pool[0]
        self._overflow_ref = cur
        return cur

    def _press_slot(self, context: str) -> Action:
        shown, overflow_ctx, pool = self._layout_slots()
        if context == overflow_ctx:
            cur = self._overflow_shown(pool)
            ref = pool[(pool.index(cur) + 1) % len(pool)]
            self._overflow_ref = ref
        elif context in shown:
            ref = shown[context]
        else:
            return Noop("empty slot")
        self._last_focused = ref
        seq = self._focus_seq.get(ref, 0) + 1
        self._focus_seq[ref] = seq
        # The runtime starts a new focus job, so any earlier result is stale.
        self._focus.pop(ref, None)
        req = self._pending.get(ref)
        if req is None:
            self._refresh()
            return Focus(ref, seq)
        # With several live requests in one session the terminal may be showing a
        # different prompt, so only tokitty's own window can show this one in full.
        opened = self._place_decisions(context, req) is not None and not self._multi(ref)
        if opened:
            self._overlay = _Overlay(context, req.nonce)
        self._refresh()
        return FocusAndOpen(ref, req, opened, seq)

    # overlay

    def _participants(self, pressed: str) -> List[str]:
        return [
            c for c in self._sorted_contexts(lambda k: k.role is not None and k.role[0] in ("slot", "interrupt"))
            if c != pressed
        ]

    def _overlay_request(self) -> Optional[PendingRequest]:
        if self._overlay is None:
            return None
        for req in self._pending.values():
            if req.nonce == self._overlay.nonce:
                return req
        return None

    def _place_decisions(self, pressed: str, req: PendingRequest) -> Optional[Dict[str, Tuple[str, int]]]:
        """Context to ("allow"|"deny"|"always"|"preview", preview index), or None when
        Allow and Deny cannot both be placed.

        Keys with a decision role always show that decision. A decision with no such
        key is taken from the participants in reading order, and the rest preview.
        """
        kinds = ["allow", "deny"]
        if req.always_rule:
            kinds.append("always")
        layout: Dict[str, Tuple[str, int]] = {}
        missing = []
        for kind in kinds:
            ctxs = self._sorted_contexts(lambda k, kind=kind: k.role is not None and k.role[0] == kind)
            if ctxs:
                layout.update({c: (kind, 0) for c in ctxs})
            else:
                missing.append(kind)
        ctxs = self._participants(pressed)
        if len([k for k in missing if k != "always"]) > len(ctxs):
            return None
        for i, ctx in enumerate(ctxs):
            layout[ctx] = (missing[i], 0) if i < len(missing) else ("preview", i - len(missing))
        return layout

    def _overlay_layout(self) -> Dict[str, Tuple[str, int]]:
        req = self._overlay_request()
        if self._overlay is None or req is None:
            return {}
        return self._place_decisions(self._overlay.pressed, req) or {}

    def _multi(self, ref: SessionRef) -> bool:
        return self._live_count.get(ref, 0) > 1

    def _armed(self, ref: SessionRef, req: PendingRequest) -> bool:
        if req.nonce in self._in_window:
            return True
        return self._focus.get(ref) == FOCUSED and not self._multi(ref)

    def _overlay_ref(self, req: PendingRequest) -> SessionRef:
        return SessionRef(req.account_index, req.session_id)

    def _press_overlay(self, role: Tuple[str, int]) -> Action:
        req = self._overlay_request()
        kind = role[0]
        if req is None or kind == "preview":
            return Noop("preview key")
        if req.nonce in self._sent:
            return Noop("already sent")
        if kind == "deny":
            self._sent.add(req.nonce)
            self._refresh()
            return Decide(req, "deny")
        ref = self._overlay_ref(req)
        if not self._armed(ref, req):
            return Noop("request not visible")
        if req.nonce in self._in_window:
            self._sent.add(req.nonce)
            self._refresh()
            return Decide(req, kind)
        return VerifyThenDecide(req, kind)

    # plan

    def _validate_overlay(self) -> None:
        o = self._overlay
        if o is None:
            return
        if (
            o.pressed not in self._keys
            or self._overlay_request() is None
            or not self._overlay_layout()
        ):
            self._overlay = None

    def _slot_spec(self, ref: SessionRef, kind: str, count: int = 0, alert: bool = False) -> KeySpec:
        v = self._views[ref]
        return KeySpec(
            kind,
            ref=ref,
            state=v.state,
            tool_label=v.tool_label,
            title=self._titles.get(ref, ""),
            pending=ref in self._pending,
            focus=self._focus.get(ref, ""),
            count=count,
            alert=alert,
        )

    def _build_plan(self) -> Dict[str, KeySpec]:
        plan: Dict[str, KeySpec] = {}
        shown, overflow_ctx, pool = self._layout_slots()
        layout = self._overlay_layout()
        req = self._overlay_request()
        total_previews = sum(1 for r in layout.values() if r[0] == "preview")
        sent = req is not None and req.nonce in self._sent
        armed = req is not None and self._armed(self._overlay_ref(req), req)
        for ctx, key in self._keys.items():
            if self._overlay is not None and ctx == self._overlay.pressed:
                plan[ctx] = KeySpec("decision", decision="cancel", armed=True)
            elif ctx in layout and req is not None:
                kind, idx = layout[ctx]
                if kind == "preview":
                    plan[ctx] = KeySpec("preview", text=req.preview, index=idx, total=total_previews)
                else:
                    plan[ctx] = KeySpec(
                        "decision",
                        decision="sent" if sent else kind,
                        armed=True if kind == "deny" else armed,
                    )
            elif key.role is None:
                plan[ctx] = KeySpec("status", text="set role")
            else:
                plan[ctx] = self._plain_spec(ctx, key.role, shown, overflow_ctx, pool)
        return plan

    def _plain_spec(
        self,
        ctx: str,
        role: Tuple[str, str],
        shown: Dict[str, SessionRef],
        overflow_ctx: Optional[str],
        pool: List[SessionRef],
    ) -> KeySpec:
        kind, arg = role
        if kind == "slot":
            if ctx == overflow_ctx:
                ref = self._overflow_shown(pool)
                alert = any(r in self._pending for r in pool)
                return self._slot_spec(ref, "overflow", count=len(pool), alert=alert)
            if ctx in shown:
                return self._slot_spec(shown[ctx], "slot")
            return KeySpec("status")
        if kind in DECISION_ROLES:
            return KeySpec("status", text=kind.capitalize())
        if kind == "interrupt":
            ref = self._last_focused
            ok = ref is not None and self._focus.get(ref) == FOCUSED
            return KeySpec("interrupt", ref=ref, armed=ok)
        if kind == "usage":
            acct = int(arg)
            if acct not in self._usage:
                return KeySpec("status", text="no usage", account=acct)
            s, w, warn = self._usage[acct]
            return KeySpec("usage", account=acct, session_pct=s, weekly_pct=w, warn=warn)
        return KeySpec("new", text=arg)

    def _refresh(self) -> None:
        self._validate_overlay()
        plan = self._build_plan()
        if plan != self._plan:
            self._plan = plan
            self._revision += 1
