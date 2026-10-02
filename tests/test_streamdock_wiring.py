from types import SimpleNamespace

import pytest

from tokitty.accounts import Account
from tokitty.streamdock import wiring
from tokitty.streamdock.runtime import StreamdockRuntime


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


def test_start_streamdock_not_configured():
    assert wiring.start_streamdock(settings(0), [], list, palette_fn=dict, runtime_cls=FakeRuntime) is None
    assert FakeRuntime.started == 0


def test_start_streamdock_starts_runtime():
    rt = wiring.start_streamdock(settings(), [{"account": None, "sessions_dir": "/c/tokitty/sessions"}], list,
                                 palette_fn=dict, runtime_cls=FakeRuntime)
    assert isinstance(rt, FakeRuntime) and FakeRuntime.started == 1
    port, token, presets, accounts, kw = FakeRuntime.kwargs
    assert (port, token, presets, len(accounts)) == (4000, "t", [{"name": "p"}], 1)
    assert "watcher_factory" in kw
    assert kw["list_running_distros_fn"] is list
    assert kw["hidden_accounts"] == ["w"]


def test_start_streamdock_bind_error_is_logged_not_raised(capsys):
    FakeRuntime.fail = OSError("address in use")
    assert wiring.start_streamdock(settings(), [], list, palette_fn=dict, runtime_cls=FakeRuntime) is None
    assert "address in use" in capsys.readouterr().err


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
