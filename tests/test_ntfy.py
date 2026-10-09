import base64
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tokitty import ntfy
from tokitty.api import UsageSnapshot
from tokitty.usage_notes import alerts_for

RESET = datetime(2026, 10, 12, 18, 0, 0, tzinfo=timezone.utc)


class Server:
    def __init__(self, status=200):
        self.status = status
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append((self.path, body, dict(self.headers)))
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


def test_send_posts_path_body_and_headers(server):
    assert ntfy.send(server.url + "/", "my-topic", "", "T", "hello ünï", ["warning", "x"]) is None
    path, body, headers = server.requests[0]
    assert path == "/my-topic"
    assert body.decode("utf-8") == "hello ünï"
    assert headers["Title"] == "T"
    assert headers["Tags"] == "warning,x"
    assert "Authorization" not in headers


def test_send_adds_bearer_only_with_token(server):
    assert ntfy.send(server.url, "t", "tk_abc", "T", "m", ["warning"]) is None
    assert server.requests[0][2]["Authorization"] == "Bearer tk_abc"


def test_send_non_ascii_title_is_rfc2047(server):
    assert ntfy.send(server.url, "t", "", "Café weekly usage 95%", "m", ["warning"]) is None
    title = server.requests[0][2]["Title"]
    assert title.startswith("=?UTF-8?B?") and title.endswith("?=")
    assert base64.b64decode(title[len("=?UTF-8?B?"):-2]).decode("utf-8") == "Café weekly usage 95%"


def test_send_non_2xx_returns_error(server):
    server.status = 403
    assert ntfy.send(server.url, "t", "", "T", "m", []) == "HTTP 403"


def test_send_connection_refused_returns_error():
    s = Server()
    url = s.url
    s.close()
    error = ntfy.send(url, "t", "", "T", "m", [], timeout=2)
    assert isinstance(error, str) and error


def test_send_never_raises_on_bad_url():
    assert isinstance(ntfy.send("not a url", "t", "", "T", "m", []), str)


def test_alert_text_weekly():
    alert = {"kind": "weekly", "pct": 95.4, "threshold": 95, "resets_at": RESET.isoformat(), "window": "w"}
    title, message = ntfy.alert_text("Work", alert)
    assert title == "Work weekly usage 95.4%"
    local = RESET.astimezone()
    assert message == f"Threshold 95%. Resets {local.strftime('%a %b')} {local.day} {local.strftime('%H:%M')}."


def test_alert_text_whole_percent_and_no_reset():
    alert = {"kind": "session", "pct": 90.0, "threshold": 90, "resets_at": None, "window": "w"}
    assert ntfy.alert_text("Codex", alert) == ("Codex session usage 90%", "Threshold 90%.")


# -- Notifier -------------------------------------------------------------

CONF = ("http://x", "topic", "")


def alert(window="weekly:A", kind="weekly"):
    return {"kind": kind, "pct": 96.0, "threshold": 95, "resets_at": None, "window": window}


class Recorder:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])
        self.gate = None

    def __call__(self, url, topic, token, title, message, tags):
        if self.gate is not None:
            self.gate.wait(5)
        self.calls.append((url, topic, token, title, message, tags))
        return self.results.pop(0) if self.results else None


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def make(tmp_path):
    made = []

    def factory(sender, clock=None):
        n = ntfy.Notifier(tmp_path, sender=sender, clock=clock or Clock())
        made.append(n)
        return n

    yield factory
    for n in made:
        n.stop()


def test_new_window_sends_once(make):
    rec = Recorder()
    n = make(rec)
    n.check("a", "Work", [alert()], CONF)
    n.settle()
    n.check("a", "Work", [alert()], CONF)
    n.settle()
    assert len(rec.calls) == 1
    assert rec.calls[0][3:] == ("Work weekly usage 96%", "Threshold 95%.", ["warning"])


def test_persists_across_instances(make, tmp_path):
    rec = Recorder()
    first = make(rec)
    first.check("a", "Work", [alert()], CONF)
    first.settle()
    assert json.loads((tmp_path / "ntfy_sent.json").read_text()) == {"a": ["weekly:A"]}
    rec2 = Recorder()
    second = make(rec2)
    second.check("a", "Work", [alert()], CONF)
    second.settle()
    assert rec2.calls == []
    second.check("a", "Work", [alert("weekly:B")], CONF)
    second.settle()
    assert len(rec2.calls) == 1


def test_failed_send_retried_only_after_five_minutes(make):
    rec = Recorder(["HTTP 500"])
    clock = Clock()
    n = make(rec, clock)
    n.check("a", "Work", [alert()], CONF)
    n.settle()
    assert len(rec.calls) == 1
    clock.now += 299
    n.check("a", "Work", [alert()], CONF)
    n.settle()
    assert len(rec.calls) == 1
    clock.now += 2
    n.check("a", "Work", [alert()], CONF)
    n.settle()
    assert len(rec.calls) == 2
    clock.now += 1000
    n.check("a", "Work", [alert()], CONF)
    n.settle()
    assert len(rec.calls) == 2


def test_accounts_are_independent(make):
    rec = Recorder()
    n = make(rec)
    n.check("a", "A", [alert()], CONF)
    n.check("b", "B", [alert()], CONF)
    n.settle()
    assert sorted(c[3] for c in rec.calls) == ["A weekly usage 96%", "B weekly usage 96%"]


def test_sent_list_pruned_to_newest_twenty(make, tmp_path):
    n = make(Recorder())
    for i in range(25):
        n.check("a", "A", [alert(f"weekly:{i}")], CONF)
        n.settle()
    saved = json.loads((tmp_path / "ntfy_sent.json").read_text())["a"]
    assert saved == [f"weekly:{i}" for i in range(5, 25)]


def test_worker_does_not_block_check(make):
    rec = Recorder()
    rec.gate = threading.Event()
    n = make(rec)
    n.check("a", "A", [alert()], CONF)
    n.check("a", "A", [alert()], CONF)
    assert rec.calls == []
    rec.gate.set()
    n.settle()
    assert len(rec.calls) == 1


def test_empty_topic_sends_nothing(make):
    rec = Recorder()
    n = make(rec)
    n.check("a", "A", [alert()], ("http://x", "", ""))
    assert rec.calls == []


def test_works_with_real_alerts_for(make):
    rec = Recorder()
    n = make(rec)
    snap = UsageSnapshot(session_pct=None, session_resets_at=None, weekly_pct=97.0, weekly_resets_at=RESET)
    n.check("a", "A", alerts_for(snap, 90, 95), CONF)
    n.settle()
    assert len(rec.calls) == 1


def test_publish_ntfy_wiring_covers_codex_and_skips_when_off(make):
    from types import SimpleNamespace

    from tokitty.__main__ import publish_ntfy

    rec = Recorder()
    n = make(rec)
    snap = UsageSnapshot(session_pct=95.0, session_resets_at=None, weekly_pct=None, weekly_resets_at=None)
    unit = {"key": "acct", "provider": SimpleNamespace(kind="codex")}
    latest = SimpleNamespace(snapshot=snap)
    state = {"ntfy": (False, "http://x", "t", ""), "notes": (True, 90, 95)}
    publish_ntfy(unit, latest, state, n, {})
    n.settle()
    assert rec.calls == []
    state["ntfy"] = (True, "http://x", "t", "")
    publish_ntfy(unit, latest, state, n, {})
    n.settle()
    assert [c[3] for c in rec.calls] == ["Codex session usage 95%"]
