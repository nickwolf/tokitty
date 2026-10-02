"""Loopback HTTP server between tokitty and the Stream Dock plugin page.

The plugin runs as a web page inside VSD Craft's embedded Chromium. It long-polls
the plan, draws the images tokitty sends, and forwards key events. All logic stays
in the DeckModel on the Tk thread; this server never touches the model. It reads
the published PlanBox snapshot and puts inbound events on a queue that the Tk
tick drains through `apply_event`.

Only CORS "simple" requests are used, so Chromium never sends a preflight: no
custom headers, the token travels as the `t` query parameter, and POST bodies are
`text/plain`. Every request needs a valid token and a Host header of exactly
`127.0.0.1:<port>`, which stops DNS-rebinding pages. Anything else gets a bare 403
and nothing about the request is logged. Responses carry
`Access-Control-Allow-Origin: *` so the plugin page can read them, which grants
nothing without the token.

Trust boundary: the token keeps out web pages and anything that does not have it.
It does not keep out other programs running as the same Windows user, which can
read the plugin's config.js. That is accepted: such a program can already type
into the terminal, so the deck adds no new power.
"""
from __future__ import annotations

import hmac
import json
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlsplit

from tokitty.streamdock.model import Action, DeckModel, KeySpec, PlanBox

HOST = "127.0.0.1"
POLL_TIMEOUT = 25.0
CONNECTED_WINDOW = 40.0
MAX_BODY = 64 * 1024
# Events that reach the model. keyDown is dropped so a held key fires only on release.
FORWARDED_EVENTS = frozenset({"willAppear", "willDisappear", "keyUp", "didReceiveSettings"})


@dataclass(frozen=True)
class DeckEvent:
    kind: str
    context: str
    device: str = ""
    row: int = 0
    column: int = 0
    settings: Dict[str, Any] = field(default_factory=dict)


def apply_event(model: DeckModel, ev: DeckEvent) -> Optional[Action]:
    """Apply one inbound event to the model. Only a key release returns an action."""
    if ev.kind in ("willAppear", "didReceiveSettings"):
        model.appear(ev.context, {"row": ev.row, "column": ev.column}, ev.device, ev.settings)
    elif ev.kind == "willDisappear":
        model.disappear(ev.context)
    elif ev.kind == "keyUp":
        return model.press(ev.context)
    return None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_event(raw: Any) -> Optional[DeckEvent]:
    """A DeckEvent from a decoded VSD message, or None when it is malformed.

    Returns a keyDown or unknown event as-is; the caller decides what to forward.
    """
    if not isinstance(raw, dict):
        return None
    kind, context = raw.get("event"), raw.get("context")
    if not isinstance(kind, str) or not isinstance(context, str):
        return None
    payload = raw.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    coords = payload.get("coordinates")
    coords = coords if isinstance(coords, dict) else {}
    settings = payload.get("settings")
    device = raw.get("device")
    return DeckEvent(
        kind,
        context,
        device if isinstance(device, str) else "",
        _int(coords.get("row")),
        _int(coords.get("column")),
        settings if isinstance(settings, dict) else {},
    )


class _ExclusiveServer(ThreadingHTTPServer):
    """Owns its port outright. HTTPServer sets SO_REUSEADDR, which on Windows lets a
    second socket bind the same port alongside this one instead of failing."""

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class DeckServer:
    """Serves the plugin page's requests on 127.0.0.1.

    `image_fn` turns a KeySpec into a data URL. `meta_fn` returns what the property
    inspector needs, in the shape
    `{"accounts": [{"index": i, "name": n}, ...], "presets": [name, ...]}`.
    """

    def __init__(
        self,
        port: int,
        token: str,
        box: PlanBox,
        inbound: "queue.Queue[DeckEvent]",
        *,
        image_fn: Callable[[KeySpec], str],
        meta_fn: Callable[[], dict],
        monotonic_fn: Callable[[], float] = time.monotonic,
        poll_timeout: float = POLL_TIMEOUT,
    ) -> None:
        self.port = port
        self._token = token.encode("utf-8")
        self._box = box
        self._inbound = inbound
        self._image_fn = image_fn
        self._meta_fn = meta_fn
        self._monotonic = monotonic_fn
        self._poll_timeout = poll_timeout
        self._lock = threading.Lock()
        self._last_plan: Optional[float] = None
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Bind and serve on a daemon thread. A bind OSError propagates."""
        deck = self

        class Handler(_Handler):
            server_deck = deck

        httpd = _ExclusiveServer((HOST, self.port), Handler)
        httpd.daemon_threads = True
        self.port = httpd.server_address[1]
        self._httpd = httpd
        self._thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.05), name="streamdock-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def connected(self) -> bool:
        """True while plan requests keep arriving: one within the last 40 seconds."""
        with self._lock:
            last = self._last_plan
        return last is not None and self._monotonic() - last <= CONNECTED_WINDOW

    def _touch(self) -> None:
        with self._lock:
            self._last_plan = self._monotonic()

    def _authorised(self, token: str, host: Optional[str]) -> bool:
        if host != f"{HOST}:{self.port}":
            return False
        return hmac.compare_digest(token.encode("utf-8"), self._token)

    def _plan_body(self, rev: int) -> bytes:
        self._touch()
        snap = self._box.wait_for_change(rev, self._poll_timeout)
        self._touch()
        keys = {ctx: {"image": self._image_fn(spec), "title": ""} for ctx, spec in snap.plan.items()}
        return json.dumps({"rev": snap.revision, "keys": keys}).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_deck: DeckServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send(self, status: int, body: bytes = b"", content_type: Optional[str] = None, close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _gate(self) -> Optional[Dict[str, Any]]:
        """The parsed query when the request passes the Host and token checks, else a 403."""
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        token = (query.get("t") or [""])[0]
        if not self.server_deck._authorised(token, self.headers.get("Host")):
            # Close the connection: an unread POST body would corrupt the next request.
            self._send(403, close=True)
            return None
        return {"path": url.path, "query": query}

    def _handle(self, method: str) -> None:
        try:
            gate = self._gate()
            if gate is None:
                return
            if method not in ("GET", "POST"):
                self._send(405, close=True)
                return
            path, query = gate["path"], gate["query"]
            if method == "GET" and path == "/v1/plan":
                self._plan(query)
            elif method == "GET" and path == "/v1/meta":
                body = json.dumps(self.server_deck._meta_fn()).encode("utf-8")
                self._send(200, body, "application/json")
            elif method == "POST" and path == "/v1/event":
                self._event()
            else:
                self._send(404, close=method == "POST")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _plan(self, query: Dict[str, Any]) -> None:
        try:
            rev = int((query.get("rev") or ["-1"])[0])
        except ValueError:
            rev = -1
        self._send(200, self.server_deck._plan_body(rev), "application/json")

    def _event(self) -> None:
        length = self.headers.get("Content-Length")
        if length is None:
            self._send(411, close=True)
            return
        try:
            size = int(length)
        except ValueError:
            self._send(400, close=True)
            return
        if size < 0:
            self._send(400, close=True)
            return
        if size > MAX_BODY:
            self._send(413, close=True)
            return
        raw = self.rfile.read(size)
        try:
            ev = parse_event(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            ev = None
        if ev is None:
            self._send(400)
            return
        if ev.kind in FORWARDED_EVENTS:
            self.server_deck._inbound.put(ev)
        self._send(204)

    def _dispatch(self) -> None:
        self._handle(self.command)

    do_GET = do_POST = do_OPTIONS = do_PUT = do_DELETE = do_PATCH = do_HEAD = _dispatch
