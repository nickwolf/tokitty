"""Discovery.run_discovery: the startup hook work and the WSL sweep.

These drive the class directly, with no GUI toolkit, so they cover what the
launch does on its background thread for either front end."""
import os
import sys
import threading

import pytest

from tokitty import app_core
from tokitty import hooks_install
from tokitty.app_core import Discovery
from tokitty.credentials import CredentialsError


# Captured at import, before the autouse fixture stubs the module attribute.
_REAL_ENSURE_CURRENT = hooks_install.ensure_current


@pytest.fixture(autouse=True)
def _no_real_hook_refresh(monkeypatch):
    """ensure_current reads <state_dir>/accounts.json itself, so a stray real
    one must never reach a developer's real ~/.claude/settings.json. A test
    that cares patches it back."""
    monkeypatch.setattr(hooks_install, "ensure_current", lambda state_dir, refresh_fn=None: [])
    monkeypatch.setattr(app_core, "retry_pending_hook_op", lambda state_dir: None)


class _Cache:
    def __init__(self, matches=(), error=None):
        self.calls = 0
        self._matches = list(matches)
        self._error = error

    def all_matches(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._matches)


def _discovery(tmp_path, cache=None, *, accounts_state="absent", env=False, home=False):
    return Discovery(
        tmp_path, accounts_state=accounts_state, env_override_set=env,
        home_relative_exists=home, wsl_credentials=cache or _Cache(),
    )


def _result(config_dir, warning=None, ok=True, message="installed"):
    return hooks_install.ConfigDirResult(str(config_dir), ok, message, warning=warning)


def test_retry_and_reconcile_warnings_arrive_together_once(tmp_path, monkeypatch):
    monkeypatch.setattr(app_core, "retry_pending_hook_op", lambda state_dir: _result(tmp_path / "a", "retry warning"))
    monkeypatch.setattr(
        hooks_install, "ensure_current",
        lambda state_dir, refresh_fn=None: [_result(tmp_path / "b", "ensure warning")],
    )
    discovery = _discovery(tmp_path)

    discovery.run_discovery()

    assert discovery.take_hook_warnings() == ["retry warning", "ensure warning"]
    assert discovery.take_hook_warnings() == []


def test_the_same_warning_from_two_steps_is_listed_once(tmp_path, monkeypatch):
    monkeypatch.setattr(app_core, "retry_pending_hook_op", lambda state_dir: _result(tmp_path, "same"))
    monkeypatch.setattr(hooks_install, "ensure_current", lambda state_dir, refresh_fn=None: [_result(tmp_path, "same")])
    discovery = _discovery(tmp_path)

    discovery.run_discovery()

    assert discovery.take_hook_warnings() == ["same"]


def test_a_failed_result_reports_its_message_too(tmp_path, monkeypatch):
    monkeypatch.setattr(
        hooks_install, "ensure_current",
        lambda state_dir, refresh_fn=None: [_result(tmp_path, None, ok=False, message="could not write")],
    )
    discovery = _discovery(tmp_path)

    discovery.run_discovery()

    assert discovery.take_hook_warnings() == ["could not write"]


def test_warnings_come_through_the_real_ensure_current(tmp_path, monkeypatch):
    """Everything else stubs ensure_current. This one lets its account
    resolution (accounts.json, provider filter, per-account try) run and fakes
    only the per-account refresh."""
    from tokitty.accounts import Account, save_accounts

    monkeypatch.setattr(hooks_install, "ensure_current", _REAL_ENSURE_CURRENT)
    save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir=os.path.join(str(tmp_path), "acct", ".claude"))])
    monkeypatch.setattr(
        hooks_install, "refresh_hooks_for_dir",
        lambda config_dir, provider: _result(config_dir, "real ensure_current warning", message="refreshed"),
    )
    discovery = _discovery(tmp_path, accounts_state="present")

    discovery.run_discovery()

    assert discovery.take_hook_warnings() == ["real ensure_current warning"]


def test_a_retry_that_raises_does_not_stop_the_reconcile(tmp_path, monkeypatch):
    def boom(state_dir):
        raise OSError("disk on fire")

    monkeypatch.setattr(app_core, "retry_pending_hook_op", boom)
    monkeypatch.setattr(
        hooks_install, "ensure_current",
        lambda state_dir, refresh_fn=None: [_result(tmp_path, "still ran")],
    )
    discovery = _discovery(tmp_path)

    discovery.run_discovery()

    assert discovery.take_hook_warnings() == ["still ran"]


def test_a_reconcile_that_raises_keeps_the_retry_warning(tmp_path, monkeypatch):
    def boom(state_dir, refresh_fn=None):
        raise RuntimeError("anything")

    monkeypatch.setattr(app_core, "retry_pending_hook_op", lambda state_dir: _result(tmp_path, "retry warning"))
    monkeypatch.setattr(hooks_install, "ensure_current", boom)
    discovery = _discovery(tmp_path)

    discovery.run_discovery()

    assert discovery.take_hook_warnings() == ["retry warning"]


def test_translocation_is_noted_when_no_account_would_report_it(tmp_path, monkeypatch):
    """With no eligible account ensure_current never reports it, so the
    runner-link step is the only place it surfaces. The path is only
    inspected as a string, so nothing has to exist."""
    from tokitty.frozen import MOVE_TO_APPLICATIONS

    exe = os.path.join(str(tmp_path), "AppTranslocation", "abc123", "Tokitty.app", "Contents", "MacOS", "tokitty")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe)
    discovery = _discovery(tmp_path)

    discovery.run_discovery()

    warnings = discovery.take_hook_warnings()
    assert len(warnings) == 1 and warnings[0].count(MOVE_TO_APPLICATIONS) == 1


def test_a_frozen_launch_repoints_the_runner_link(tmp_path, monkeypatch):
    from tokitty import runner_link

    windows = sys.platform == "win32"
    release = tmp_path / "release-a"
    release.mkdir()
    exe = release / ("tokitty.exe" if windows else "tokitty")
    exe.write_text("gui", encoding="utf-8")
    (release / ("tokitty-hook.exe" if windows else "tokitty-hook")).write_text("hook", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    discovery = _discovery(tmp_path)

    try:
        discovery.run_discovery()
        assert os.path.realpath(tmp_path / "current") == os.path.realpath(release)
    finally:
        link = tmp_path / "current"
        if runner_link._is_link(str(link)):
            (os.rmdir if windows else os.unlink)(link)


def test_a_held_hook_guard_skips_the_retry_and_the_reconcile(tmp_path, monkeypatch):
    from tokitty import hook_guard

    calls = []
    monkeypatch.setattr(app_core, "retry_pending_hook_op", lambda state_dir: calls.append("retry"))
    monkeypatch.setattr(hooks_install, "ensure_current", lambda state_dir, refresh_fn=None: calls.append("ensure") or [])
    token = hook_guard.try_acquire(tmp_path, "settings")
    try:
        _discovery(tmp_path).run_discovery()
    finally:
        hook_guard.release(token)

    assert calls == []


def test_the_wsl_sweep_result_is_published_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    matches = [("Ubuntu", "/home/a/.claude/.credentials.json"), ("Debian", "/home/b/.claude-work/.credentials.json")]
    cache = _Cache(matches)
    discovery = _discovery(tmp_path, cache)

    discovery.run_discovery()

    assert discovery.wsl_matches() == matches
    assert cache.calls == 1


def test_a_wsl_sweep_that_raises_leaves_no_matches_and_keeps_the_warnings(tmp_path, monkeypatch):
    """wsl.exe missing from PATH raises CredentialsError. The hook steps run
    first, so their warning must still arrive."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        hooks_install, "ensure_current",
        lambda state_dir, refresh_fn=None: [_result(tmp_path, "still ran")],
    )
    discovery = _discovery(tmp_path, _Cache(error=CredentialsError("wsl.exe not found on PATH")))

    discovery.run_discovery()

    assert discovery.take_hook_warnings() == ["still ran"]
    assert discovery.wsl_matches() == []


@pytest.mark.parametrize("kwargs", [
    {"accounts_state": "present"},
    {"env": True},
    {"home": True},
])
def test_no_wsl_sweep_when_accounts_exist_or_credentials_were_found(tmp_path, monkeypatch, kwargs):
    monkeypatch.setattr(sys, "platform", "win32")
    retries = []
    monkeypatch.setattr(app_core, "retry_pending_hook_op", lambda state_dir: retries.append(state_dir))
    cache = _Cache([("Ubuntu", "/x")])
    discovery = _discovery(tmp_path, cache, **kwargs)

    discovery.run_discovery()

    assert cache.calls == 0
    assert discovery.wsl_matches() == []
    assert retries == [tmp_path]


def test_no_wsl_sweep_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    cache = _Cache([("Ubuntu", "/x")])

    _discovery(tmp_path, cache).run_discovery()

    assert cache.calls == 0


def test_start_runs_run_discovery_on_a_worker_thread(tmp_path, monkeypatch):
    seen = []
    done = threading.Event()

    def retry(state_dir):
        seen.append(threading.current_thread())
        done.set()

    monkeypatch.setattr(app_core, "retry_pending_hook_op", retry)

    _discovery(tmp_path).start()

    assert done.wait(5.0)
    assert seen[0] is not threading.main_thread()
