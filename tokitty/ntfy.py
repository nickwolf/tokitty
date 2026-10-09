"""Push notifications through ntfy when a usage limit passes a threshold.

`send` is one blocking POST that never raises. `Notifier` decides which
alerts (from usage_notes.alerts_for) are new for an account and hands the
sends to one background worker, so the Tk tick never waits on HTTP. A window
counts as sent only after a 2xx, and the sent set persists across restarts.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

SENT_FILENAME = "ntfy_sent.json"
KEEP_PER_ACCOUNT = 20
RETRY_SECONDS = 300
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _header_text(value: str) -> str:
    """ASCII passes through; anything else becomes an RFC 2047 encoded word,
    which ntfy decodes. HTTP header values are Latin-1 at best otherwise."""
    value = value.replace("\r", " ").replace("\n", " ")
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def send(url: str, topic: str, token: str, title: str, message: str, tags: Iterable[str],
         urlopen=urllib.request.urlopen, timeout: float = 10) -> Optional[str]:
    """POST one notification. None on 2xx, else a short error string. Never raises."""
    try:
        request = urllib.request.Request(
            url.rstrip("/") + "/" + topic, data=message.encode("utf-8"), method="POST")
        request.add_header("Title", _header_text(title))
        request.add_header("Tags", ",".join(tags))
        if token:
            request.add_header("Authorization", "Bearer " + token)
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
        if 200 <= status < 300:
            return None
        return f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:
        reason = getattr(exc, "reason", None) or exc
        return (str(reason) or type(exc).__name__)[:120]


def send_test(url: str, topic: str, token: str, urlopen=urllib.request.urlopen) -> Optional[str]:
    return send(url, topic, token, "Tokitty test", "ntfy is set up.", ["white_check_mark"], urlopen=urlopen)


def alert_text(label: str, alert: dict) -> Tuple[str, str]:
    """(title, message) for one alert; the reset time is in local time."""
    pct = alert["pct"]
    shown = f"{pct:g}" if pct != int(pct) else str(int(pct))
    title = f"{label} {alert['kind']} usage {shown}%"
    message = f"Threshold {alert['threshold']}%."
    resets = alert.get("resets_at")
    if resets:
        try:
            local = datetime.fromisoformat(resets).astimezone()
            message += f" Resets {local.strftime('%a %b')} {local.day} {local.strftime('%H:%M')}."
        except ValueError:
            pass
    return title, message


class Notifier:
    """Owned by the Tk thread: check() and stop() are called only from there.
    One daemon worker does the HTTP."""

    def __init__(self, state_dir, sender=send, clock: Callable[[], float] = time.monotonic) -> None:
        self._path = Path(state_dir) / SENT_FILENAME
        self._sender = sender
        self._clock = clock
        self._sent: Dict[str, List[str]] = self._load()
        self._inflight: set = set()
        self._next_try: Dict[Tuple[str, str], float] = {}
        self._queue: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    def _load(self) -> Dict[str, List[str]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: [w for w in v if isinstance(w, str)] for k, v in data.items()
                if isinstance(k, str) and isinstance(v, list)}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self._sent), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass

    def check(self, account_key: str, account_label: str, alerts: List[dict], settings_tuple) -> None:
        """settings_tuple is (url, topic, token). Queues a send per new window."""
        self._drain()
        url, topic, token = settings_tuple
        if not topic:
            return
        now = self._clock()
        for alert in alerts:
            window = alert["window"]
            ident = (account_key, window)
            if window in self._sent.get(account_key, ()) or ident in self._inflight:
                continue
            if self._next_try.get(ident, 0) > now:
                continue
            self._inflight.add(ident)
            title, message = alert_text(account_label, alert)
            self._ensure_worker()
            self._queue.put((ident, url, topic, token, title, message))

    def _drain(self) -> None:
        changed = False
        while True:
            try:
                ident, error = self._results.get_nowait()
            except queue.Empty:
                break
            self._inflight.discard(ident)
            account_key, window = ident
            if error is None:
                windows = self._sent.setdefault(account_key, [])
                windows.append(window)
                del windows[:-KEEP_PER_ACCOUNT]
                self._next_try.pop(ident, None)
                changed = True
            else:
                self._next_try[ident] = self._clock() + RETRY_SECONDS
        if changed:
            self._save()

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, daemon=True, name="ntfy-worker")
            self._worker.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            ident, url, topic, token, title, message = item
            error = self._sender(url, topic, token, title, message, ["warning"])
            self._results.put((ident, error))

    def settle(self, timeout: float = 5.0) -> None:
        """Wait for queued sends and apply their results. For tests."""
        deadline = time.monotonic() + timeout
        while self._inflight and time.monotonic() < deadline:
            time.sleep(0.005)
            self._drain()

    def stop(self) -> None:
        self._queue.put(None)
