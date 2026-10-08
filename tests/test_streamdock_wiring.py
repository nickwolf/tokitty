import errno
import http.client
import threading
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace

import pytest

from tokitty.accounts import Account
from tokitty.streamdock import wiring
from tokitty.streamdock.runtime import StreamdockRuntime
from tokitty.streamdock.server import _ExclusiveServer


class FakeWatcher:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self):
        return self._sessions


def test_parent_dir_handles_both_separators():
    assert wiring.parent_dir("/home/u/.claude/tokitty/sessions") == "/home/u/.claude/tokitty"
    unc = "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude\\tokitty\\sessions"
    assert wiring.parent_dir(unc) == "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude\\tokitty"
    assert wiring.parent_dir("/a/b/") == "/a"
    assert wiring.parent_dir(None) is None
    assert wiring.parent_dir("") is None


def test_account_inputs_derive_dirs_from_sessions_dir():
    unc = "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude\\tokitty\\sessions"
    units = [
        {"account": Account("work", "x"), "sessions_dir": unc, "distro_name": "Ubuntu"},
        {"account": None, "sessions_dir": lambda: "/c/tokitty/sessions"},
        {"account": None, "sessions_dir": None},
    ]
    a, b, c = wiring.account_inputs(units)
    assert (a.index, a.name) == (0, "work")
    assert a.tokitty_dir() == "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude\\tokitty"
    assert a.config_dir() == "\\\\wsl.localhost\\Ubuntu\\home\\u\\.claude"
    assert (b.name, b.tokitty_dir(), b.config_dir()) == (wiring.DEFAULT_NAME, "/c/tokitty", "/c")
    assert c.tokitty_dir() is None and c.config_dir() is None
    assert (a.distro_name, b.distro_name) == ("Ubuntu", None)


def test_gather_inputs_skips_units_without_usage():
    units = [
        {"watcher": FakeWatcher(["a"]), "deck_usage": (10.0, 20.0, False)},
        {"watcher": FakeWatcher(["b"])},
    ]
    sessions, usage = wiring.gather_inputs(units)
    assert sessions == {0: ["a"], 1: ["b"]}
    assert usage == {0: (10.0, 20.0, False)}


def test_usage_from_display_warns_on_dimmed():
    assert wiring.usage_from_display({"session_pct": 1.0, "weekly_pct": 2.0, "dimmed": True}) == (1.0, 2.0, True)
    assert wiring.usage_from_display({"session_pct": 1.0, "weekly_pct": 2.0}) == (1.0, 2.0, False)


class FakeRuntime:
    started = 0
    fail = None
    kwargs = None

    def __init__(self, port, token, presets, accounts, **kw):
        FakeRuntime.kwargs = (port, token, presets, accounts, kw)

    configured = staticmethod(StreamdockRuntime.configured)

    def start(self):
        if FakeRuntime.fail:
            raise FakeRuntime.fail
        FakeRuntime.started += 1


@pytest.fixture(autouse=True)
def reset():
    FakeRuntime.started = 0
    FakeRuntime.fail = None


def settings(port=4000, token="t"):
    return SimpleNamespace(streamdock_port=port, streamdock_token=token, streamdock_presets=[{"name": "p"}],
                           streamdock_hidden_accounts=["w"])


class Clock:
    """A fake monotonic clock and a fake root.after: run() fires the next scheduled call."""

    def __init__(self):
        self.t = 0.0
        self.queue = []
        self.cancelled = []

    def after(self, ms, fn):
        handle = (ms, fn)
        self.queue.append(handle)
        return handle

    def cancel(self, handle):
        self.cancelled.append(handle)
        self.queue.remove(handle)

    def run(self):
        ms, fn = self.queue.pop(0)
        self.t += ms / 1000
        fn()


def starter(cfg, clock=None, units=(), **kw):
    clock = clock or Clock()
    kw.setdefault("runtime_cls", FakeRuntime)
    kw.setdefault("deadline_s", 150.0)
    return wiring.DeckStarter(cfg, list(units), list, palette_fn=dict, after=clock.after, cancel=clock.cancel,
                              now=lambda: clock.t, **kw), clock


def test_starter_not_configured():
    d, clock = starter(settings(0))
    d.begin()
    assert d.runtime is None and d.state == "off" and FakeRuntime.started == 0 and not clock.queue


def test_starter_starts_runtime():
    d, clock = starter(settings(), units=[{"account": None, "sessions_dir": "/c/tokitty/sessions"}])
    d.begin()
    assert isinstance(d.runtime, FakeRuntime) and FakeRuntime.started == 1 and d.state == "running"
    port, token, presets, accounts, kw = FakeRuntime.kwargs
    assert (port, token, presets, len(accounts)) == (4000, "t", [{"name": "p"}], 1)
    assert "watcher_factory" in kw
    assert kw["list_running_distros_fn"] is list
    assert kw["hidden_accounts"] == ["w"]
    assert not clock.queue


def test_starter_non_retryable_error_fails_at_once(capsys):
    FakeRuntime.fail = OSError(errno.EACCES, "permission denied")
    d, clock = starter(settings())
    d.begin()
    assert d.runtime is None and d.state == "failed" and "denied" in d.reason
    assert not clock.queue
    assert "denied" in capsys.readouterr().err


def test_starter_retries_busy_port_then_gives_up_at_the_deadline():
    FakeRuntime.fail = OSError(errno.EADDRINUSE, "address in use")
    d, clock = starter(settings(), deadline_s=5.0)
    d.begin()
    assert d.state == "starting" and len(clock.queue) == 1 and clock.queue[0][0] == 1000
    while clock.queue:
        clock.run()
        assert clock.t <= 5.0
    assert d.state == "failed" and "in use" in d.reason and d.runtime is None


def test_starter_retry_succeeds_and_sets_the_runtime():
    FakeRuntime.fail = OSError(errno.EADDRINUSE, "address in use")
    d, clock = starter(settings())
    d.begin()
    clock.run()
    assert d.state == "starting" and d.runtime is None
    FakeRuntime.fail = None
    clock.run()
    assert d.state == "running" and isinstance(d.runtime, FakeRuntime) and not clock.queue


def test_starter_stop_cancels_the_retry():
    FakeRuntime.fail = OSError(errno.EADDRINUSE, "address in use")
    d, clock = starter(settings())
    d.begin()
    d.stop()
    assert not clock.queue and len(clock.cancelled) == 1


def test_starter_forwards_settings_changes_made_while_retrying():
    class Rt(FakeRuntime):
        def set_presets(self, presets):
            self.presets = presets

        def set_hidden_accounts(self, names):
            self.hidden = names

    FakeRuntime.fail = OSError(errno.EADDRINUSE, "address in use")
    d, clock = starter(settings(), runtime_cls=Rt)
    d.begin()
    d.set_presets([{"name": "new"}])
    d.set_hidden_accounts(["x"])
    FakeRuntime.fail = None
    clock.run()
    assert d.runtime.presets == [{"name": "new"}] and d.runtime.hidden == ["x"]


@pytest.mark.parametrize("exc,busy", [
    (OSError(errno.EADDRINUSE, "x"), True),
    (OSError(errno.EACCES, "x"), False),
    (OSError(errno.ENOENT, "x"), False),
    (ValueError("x"), False),
])
def test_is_port_busy(exc, busy):
    assert wiring.is_port_busy(exc) is busy


def test_is_port_busy_windows_codes():
    for code in (10048, 10013):
        exc = OSError(code, "x")
        assert wiring.is_port_busy(exc)
        other = OSError(1, "x")
        other.winerror = code
        assert wiring.is_port_busy(other)


def _stream_threads():
    return {t for t in threading.enumerate() if t.name.startswith("streamdock")}


def test_real_port_conflict_retries_until_the_port_is_released():
    """The old copy's exclusive server holds the port; the new copy's deck starts once it lets go."""
    before = _stream_threads()
    squatter = _ExclusiveServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = squatter.server_address[1]
    try:
        d, clock = starter(settings(port, "tok"), runtime_cls=None)
        d.begin()
        assert d.state == "starting" and d.runtime is None
        assert wiring.is_port_busy(OSError(errno.EADDRINUSE, "x"))
        clock.run()
        assert d.state == "starting"
        assert _stream_threads() == before  # a failed bind starts nothing
        squatter.server_close()
        clock.run()
        assert d.state == "running"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/v1/meta?t=tok", headers={"Host": f"127.0.0.1:{port}"})
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        squatter.server_close()
        d.stop()
    assert _stream_threads() == before


def test_real_shutdown_while_retrying_leaves_no_threads():
    before = _stream_threads()
    squatter = _ExclusiveServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        d, clock = starter(settings(squatter.server_address[1], "tok"), runtime_cls=None)
        d.begin()
        for _ in range(3):
            clock.run()
        d.stop()
        assert not clock.queue and d.runtime is None
        assert _stream_threads() == before
    finally:
        squatter.server_close()


def test_watcher_factory_passes_distro_and_probe():
    probe = lambda: ["Ubuntu"]  # noqa: E731
    units = [{"distro_name": "Ubuntu"}]
    (acct,) = wiring.account_inputs([{"account": None, "sessions_dir": "/c/tokitty/sessions"}])
    watcher = wiring.make_watcher_factory(units, probe)(acct)
    assert watcher._distro_name == "Ubuntu"
    assert watcher._list_running_distros_fn is probe
    assert watcher._account_index == 0


def test_apply_idle_cap_follows_the_deck_connection():
    class W:
        cap = "unset"

        def set_idle_cap(self, seconds):
            self.cap = seconds

    units = [{"watcher": W()}, {"watcher": W()}]
    wiring.apply_idle_cap(units, True)
    assert [u["watcher"].cap for u in units] == [wiring.DECK_IDLE_POLL_S] * 2
    wiring.apply_idle_cap(units, False)
    assert [u["watcher"].cap for u in units] == [None, None]
