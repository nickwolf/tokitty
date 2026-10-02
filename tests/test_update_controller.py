import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokitty import update_controller
from tokitty.lock import LockAcquisitionError
from tokitty.update_controller import TkHost, UpdateController, UpdateResult, skips_ack
from tokitty.update_install import Staged, UpdateCancelled, UpdateInstallError
from tokitty.update_swap import ack_path, write_pending
from tokitty.updater import Release, RunningVersion, UpdateCheckError, load_update_state

MAIN = threading.main_thread()
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
RELEASE = Release("v0.2.0", "https://example.test/notes", "https://example.test/a.tar.gz", 10, "https://example.test/s")


class FakeChild:
    def __init__(self, alive=True):
        self.alive = alive
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.killed, self.alive = True, False

    def wait(self, timeout=None):
        return 0


class FakeHost:
    def __init__(self, events, restore_error=None):
        self.events = events
        self.restore_error = restore_error

    def hide(self):
        self.events.append(("hide", threading.current_thread()))

    def restore(self):
        self.events.append(("restore", threading.current_thread()))
        if self.restore_error:
            raise self.restore_error

    def finish(self):
        self.events.append(("finish", threading.current_thread()))

    def notice(self, text):
        self.events.append(("notice", text))


class Rig:
    """A controller wired to recording fakes, on a linux layout under tmp_path."""

    def __init__(self, tmp_path, sys_platform="linux", **overrides):
        tmp_path.mkdir(exist_ok=True)
        self.tmp = tmp_path
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.sys_platform = sys_platform
        self.events = []
        self.child = FakeChild()
        self.acked = True
        self.stage_error = None
        self.launch_error = None
        self.swap_in_error = None
        self.promote_error = None
        self.argv = None
        self.env = None
        if sys_platform == "darwin":
            self.versions = tmp_path / "apps"
            self.exe = self.versions / "Tokitty.app" / "Contents" / "MacOS" / "Tokitty"
        else:
            self.versions = tmp_path / "releases"
            self.exe = self.versions / "v0.1.0" / "tokitty" / "tokitty"
        self.versions.mkdir()
        self.host = FakeHost(self.events, overrides.pop("restore_error", None))
        seams = dict(
            running=RunningVersion("v0.1.0", True),
            executable=str(self.exe),
            sys_platform=sys_platform,
            environ={"PATH": "/bin"},
            clock=lambda: NOW,
            stage=self.stage,
            self_check=lambda gui, tag: None,
            promote=self.promote,
            mac_swap_in=self.mac_swap_in,
            mac_swap_back=self.mac_swap_back,
            launch=self.launch,
            wait_for_ack=self.wait_for_ack,
            cleanup=lambda state_dir, **kw: self.events.append(("cleanup", kw)),
            new_token=lambda: "tok123",
        )
        seams.update(overrides)
        self.controller = UpdateController(self.state, self.host, **seams)
        self.done = []
        self.progress = []

    def log(self, name, *info):
        self.events.append((name, threading.current_thread(), *info))

    def names(self):
        return [e[0] for e in self.events]

    def pending_now(self):
        return load_update_state(self.state).pending

    def stage(self, release, target, state_dir, *, sys_platform, progress, cancelled, verify):
        self.log("stage")
        progress(5, 10)
        if self.stage_error:
            raise self.stage_error
        staging = target.parent / f".tokitty-update-{release.tag}-1"
        top = staging / "unpacked" / target.top
        return Staged(staging, top, top / "gui")

    def promote(self, staged, target, tag, state_dir, *, sys_platform, self_check):
        self.log("promote", self.pending_now())
        if self.promote_error:
            raise self.promote_error
        return target.final_path(tag)

    def mac_swap_in(self, staged, app, old_tag, state_dir):
        self.log("swap_in", self.pending_now())
        if self.swap_in_error:
            raise self.swap_in_error
        return Path(app).parent / f".Tokitty-{old_tag}.app"

    def mac_swap_back(self, app, backup, state_dir):
        self.log("swap_back", str(backup))

    def launch(self, argv, env, sys_platform):
        self.log("launch")
        self.argv, self.env = argv, env
        if self.launch_error:
            raise self.launch_error
        return self.child

    def wait_for_ack(self, path, timeout):
        self.log("wait", str(path), timeout)
        return self.acked

    def start(self, **kw):
        return self.controller.start_install(
            RELEASE, lambda d, t: self.progress.append((d, t, threading.current_thread())), self.done.append, **kw
        )

    def pump(self, until, timeout=5.0):
        end = time.monotonic() + timeout
        while not until():
            assert time.monotonic() < end, f"timed out; events so far: {self.names()}"
            self.controller.tick()
            time.sleep(0.005)

    def run(self):
        assert self.start()
        self.pump(lambda: bool(self.done))
        return self.done[0]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


@pytest.fixture
def mac_rig(tmp_path):
    return Rig(tmp_path, sys_platform="darwin")


# --- refusal ---------------------------------------------------------------


def test_refusal_none_for_an_installable_newer_release(rig):
    assert rig.controller.refusal(RELEASE) is None


def test_refusal_reasons(rig, monkeypatch, tmp_path):
    older = Release("v0.1.0", None, "u", 1, "s")
    assert "up to date" in rig.controller.refusal(older)
    assert "SHA256SUMS" in rig.controller.refusal(Release("v0.2.0", None, None, None, None))
    source = Rig(tmp_path / "src", running=RunningVersion("v0.1.0", False))
    assert "can't update itself" in source.controller.refusal(RELEASE)
    monkeypatch.setattr(update_controller, "probe_writable", lambda d: False)
    assert "can't write" in rig.controller.refusal(RELEASE)


def test_refusal_reports_a_misnamed_mac_bundle(tmp_path):
    rig = Rig(tmp_path, sys_platform="darwin")
    odd = tmp_path / "apps" / "Renamed.app" / "Contents" / "MacOS" / "Tokitty"
    controller = UpdateController(
        rig.state, rig.host, running=RunningVersion("v0.1.0", True), executable=str(odd), sys_platform="darwin"
    )
    assert "Renamed.app" in controller.refusal(RELEASE)


# --- the install flow ------------------------------------------------------


def test_install_on_linux_follows_the_handover_order(rig):
    result = rig.run()

    assert result == UpdateResult("installed", "Updated to v0.2.0.", "v0.2.0")
    assert rig.names() == ["stage", "promote", "hide", "launch", "wait", "finish"]
    assert rig.argv == [str(rig.versions / "v0.2.0" / "tokitty" / "tokitty"), "--after-update", "tok123"]
    assert rig.env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert rig.env["PATH"] == "/bin"
    assert "TOKITTY_SELF_CHECK_TK" not in rig.env
    assert next(e for e in rig.events if e[0] == "wait")[2:] == (str(ack_path(rig.state, "tok123")), 120.0)
    assert rig.pending_now() is None


def test_pending_is_written_before_the_swap_and_cleared_after_the_ack(rig):
    rig.run()

    promote = next(e for e in rig.events if e[0] == "promote")
    pending = promote[2]
    assert pending["token"] == "tok123"
    assert (pending["old_version"], pending["new_version"]) == ("v0.1.0", "v0.2.0")
    assert pending["old_path"] == str(rig.exe.parent)
    assert pending["new_path"] == str(rig.versions / "v0.2.0" / "tokitty")
    assert pending["staging"].endswith(".tokitty-update-v0.2.0-1")


def test_only_the_stage_and_ack_workers_run_off_the_tk_thread(rig):
    rig.run()

    threads = {e[0]: e[1] for e in rig.events if len(e) > 1 and isinstance(e[1], threading.Thread)}
    assert threads["stage"] is not MAIN
    assert threads["wait"] is not MAIN
    for name in ("promote", "hide", "launch", "finish"):
        assert threads[name] is MAIN, name
    assert all(t is MAIN for _, _, t in rig.progress)


def test_progress_reaches_on_progress_from_tick(rig):
    rig.run()

    assert [(d, t) for d, t, _ in rig.progress] == [(5, 10)]


def test_status_and_busy_follow_the_phases(rig):
    assert rig.controller.status.phase == "idle" and not rig.controller.busy
    gate = threading.Event()
    rig.controller._stage = lambda *a, **k: gate.wait(5) and (_ for _ in ()).throw(UpdateInstallError("stop"))
    rig.start()
    assert rig.controller.status.phase == "staging" and rig.controller.busy
    gate.set()
    rig.pump(lambda: bool(rig.done))
    assert rig.controller.status.phase == "idle"


def test_a_second_install_is_refused_while_one_runs(rig):
    gate = threading.Event()
    rig.controller._stage = lambda *a, **k: gate.wait(5) and (_ for _ in ()).throw(UpdateInstallError("stop"))
    assert rig.start()
    assert not rig.start()
    gate.set()
    rig.pump(lambda: bool(rig.done))
    assert len(rig.done) == 1


def test_install_on_macos_swaps_after_hiding_and_launches_the_inner_exe(mac_rig):
    rig = mac_rig
    result = rig.run()

    assert result.outcome == "installed"
    assert rig.names() == ["stage", "hide", "swap_in", "launch", "wait", "finish"]
    app = rig.versions / "Tokitty.app"
    assert rig.argv == [str(app / "Contents" / "MacOS" / "Tokitty"), "--after-update", "tok123"]
    assert next(e for e in rig.events if e[0] == "swap_in")[2]["token"] == "tok123"
    assert rig.pending_now() is None


def test_no_ack_kills_the_child_and_rolls_back(rig):
    rig.acked = False
    result = rig.run()

    assert result.outcome == "rolled_back"
    assert rig.child.killed
    assert rig.names() == ["stage", "promote", "hide", "launch", "wait", "restore", "notice"]
    assert rig.events[-1] == ("notice", "Tokitty v0.2.0 didn't start. Still running v0.1.0.")
    assert result.message == "Tokitty v0.2.0 didn't start. Still running v0.1.0."
    assert rig.pending_now() is None


def test_no_ack_on_macos_swaps_back_before_restoring(mac_rig):
    rig = mac_rig
    rig.acked = False
    result = rig.run()

    assert result.outcome == "rolled_back"
    assert rig.names() == ["stage", "hide", "swap_in", "launch", "wait", "swap_back", "restore", "notice"]
    assert next(e for e in rig.events if e[0] == "swap_back")[2] == str(rig.versions / ".Tokitty-v0.1.0.app")


def test_no_ack_leaves_an_already_exited_child_alone(rig):
    rig.acked = False
    rig.child = FakeChild(alive=False)
    rig.run()

    assert not rig.child.killed


def test_a_failed_launch_rolls_back_without_waiting(mac_rig):
    rig = mac_rig
    rig.launch_error = OSError("blocked")
    result = rig.run()

    assert result.outcome == "failed"
    assert "couldn't be started (blocked)" in result.message
    assert rig.names() == ["stage", "hide", "swap_in", "launch", "swap_back", "restore", "notice"]


def test_a_failed_mac_swap_does_not_swap_back(mac_rig):
    rig = mac_rig
    rig.swap_in_error = UpdateInstallError("no swap")
    result = rig.run()

    assert result.outcome == "failed"
    assert rig.names() == ["stage", "hide", "swap_in", "restore", "notice"]


def test_a_lost_lock_during_rollback_closes_the_app(rig):
    rig.acked = False
    rig.host.restore_error = LockAcquisitionError("held")
    result = rig.run()

    assert result.outcome == "failed"
    assert "Couldn't resume" in result.message
    assert rig.names()[-3:] == ["restore", "notice", "finish"]
    assert "Couldn't resume" in next(e for e in rig.events if e[0] == "notice")[1]
    assert rig.pending_now() is None


def test_a_failed_promote_never_hides_the_windows(rig):
    rig.promote_error = UpdateInstallError("exists")
    result = rig.run()

    assert result == UpdateResult("failed", "exists", "v0.2.0")
    assert "hide" not in rig.names()
    assert rig.pending_now() is None


def test_a_stage_failure_is_reported_and_nothing_else_runs(rig):
    rig.stage_error = UpdateInstallError("hash mismatch")
    result = rig.run()

    assert result == UpdateResult("failed", "hash mismatch", "v0.2.0")
    assert rig.names() == ["stage"]


def test_an_unexpected_stage_error_is_reported_too(rig):
    rig.stage_error = ValueError("boom")
    result = rig.run()

    assert result.outcome == "failed" and "boom" in result.message


def test_a_release_the_app_cannot_install_fails_before_staging(rig):
    result = rig.controller.start_install(Release("v0.2.0", None, None, None, None), None, rig.done.append)
    rig.pump(lambda: bool(rig.done))

    assert result and rig.done[0].outcome == "failed"
    assert "stage" not in rig.names()


def test_cancel_during_the_download_discards_and_reports_cancelled(rig):
    started = threading.Event()

    def slow_stage(release, target, state_dir, *, sys_platform, progress, cancelled, verify):
        started.set()
        while not cancelled():
            time.sleep(0.005)
        raise UpdateCancelled()

    rig.controller._stage = slow_stage
    rig.start()
    assert started.wait(5)
    rig.controller.cancel()
    rig.pump(lambda: bool(rig.done))

    assert rig.done[0].outcome == "cancelled"
    assert "hide" not in rig.names()


def test_cancel_after_staging_but_before_the_handover_still_cancels(rig):
    staging = rig.versions / ".tokitty-update-v0.2.0-1"
    staging.mkdir()
    (staging / "f").write_text("x")
    done_staging = threading.Event()
    real_stage = rig.stage

    def stage_then_signal(*a, **k):
        staged = real_stage(*a, **k)
        done_staging.set()
        return staged

    rig.controller._stage = stage_then_signal
    rig.start()
    assert done_staging.wait(5)
    time.sleep(0.05)  # the result is published; tick() has not run yet
    rig.controller.cancel()
    rig.pump(lambda: bool(rig.done))

    assert rig.done[0].outcome == "cancelled"
    assert "promote" not in rig.names() and "hide" not in rig.names()
    assert not staging.exists()


def test_a_raising_callback_does_not_break_tick(rig, capsys):
    rig.controller.start_install(RELEASE, None, lambda r: 1 / 0)
    rig.pump(lambda: rig.controller.status.phase == "waiting" or "finish" in rig.names())
    rig.pump(lambda: "finish" in rig.names())

    assert "callback" in capsys.readouterr().err


# --- startup cleanup and the install race ------------------------------------


def test_startup_clears_only_a_stale_pending(rig):
    write_pending(rig.state, old_version="v0.1.0", new_version="v0.2.0", old_path="a", new_path="b",
                  token="t", staging="s", now=NOW - timedelta(minutes=10))
    rig.controller.startup(frozen=False)
    assert rig.pending_now() is None

    write_pending(rig.state, old_version="v0.1.0", new_version="v0.2.0", old_path="a", new_path="b",
                  token="t", staging="s", now=NOW - timedelta(minutes=1))
    rig.controller.startup(frozen=False)
    assert rig.pending_now()["token"] == "t"


def test_startup_writes_nothing_when_there_is_nothing_to_clear(rig):
    rig.controller.startup(frozen=False)

    assert not (rig.state / "update.json").exists()


def test_startup_skips_cleanup_in_a_source_run(rig):
    rig.controller.startup(frozen=False)
    time.sleep(0.05)

    assert "cleanup" not in rig.names()


def test_startup_cleanup_gets_the_running_release_and_currents_target(rig):
    real = rig.versions / "v0.1.0" / "tokitty"
    real.mkdir(parents=True)
    (rig.state / "current").symlink_to(real, target_is_directory=True)
    rig.controller.startup(frozen=True)
    rig.pump(lambda: "cleanup" in rig.names())

    kwargs = next(e for e in rig.events if e[0] == "cleanup")[1]
    assert kwargs["running_release"] == os.path.realpath(rig.exe)
    assert kwargs["current_target"] == os.path.realpath(real)
    assert kwargs["running_version"] == "v0.1.0"
    assert kwargs["now"] == NOW
    assert kwargs["target"].parent == rig.versions


def test_startup_cleanup_with_no_current_link(rig):
    rig.controller.startup(frozen=True)
    rig.pump(lambda: "cleanup" in rig.names())

    assert next(e for e in rig.events if e[0] == "cleanup")[1]["current_target"] is None


def test_a_failing_cleanup_does_not_block_installs(rig, capsys):
    def broken(state_dir, **kw):
        raise OSError("disk")

    rig.controller._cleanup = broken
    rig.controller.startup(frozen=True)
    result = rig.run()

    assert result.outcome == "installed"
    assert "cleanup: disk" in capsys.readouterr().err


def test_an_install_waits_for_startup_cleanup_so_it_cannot_delete_the_download(rig):
    """Cleanup deletes owned staging folders no live pending names. With the
    install queued behind it, the staging folder only exists once cleanup is
    done; with the order reversed this test fails because cleanup removes it."""
    from tokitty.update_swap import cleanup as real_cleanup
    from tokitty.updater import add_owned

    gate = threading.Event()
    cleanup_entered = threading.Event()

    def slow_cleanup(state_dir, **kw):
        cleanup_entered.set()
        assert gate.wait(5)
        real_cleanup(state_dir, **kw)

    staging_made = []

    def staging_stage(release, target, state_dir, *, sys_platform, progress, cancelled, verify):
        staging = target.parent / f".tokitty-update-{release.tag}-{os.getpid()}"
        add_owned(state_dir, staging, release.tag, "staging")
        staging.mkdir()
        (staging / "archive").write_text("partial")
        staged_top = staging / "unpacked" / target.top
        staged_top.mkdir(parents=True)
        staging_made.append(staging)
        # Give a (wrongly) concurrent cleanup the chance to strike mid-download.
        time.sleep(0.2)
        assert staging.exists(), "startup cleanup deleted the staging folder mid-download"
        return Staged(staging, staged_top, staged_top / "gui")

    rig.controller._cleanup = slow_cleanup
    rig.controller._stage = staging_stage
    rig.controller._promote = lambda staged, target, tag, state_dir, **kw: target.final_path(tag)
    rig.controller.startup(frozen=True)
    assert cleanup_entered.wait(5)

    rig.start()
    time.sleep(0.3)
    assert staging_made == [], "install began staging while startup cleanup was still running"
    assert rig.controller.status.phase == "staging"

    gate.set()
    rig.pump(lambda: bool(rig.done))
    assert rig.done[0].outcome == "installed"


def test_cancel_works_while_waiting_for_startup_cleanup(rig):
    gate = threading.Event()
    rig.controller._cleanup = lambda state_dir, **kw: gate.wait(5)
    rig.controller.startup(frozen=True)
    rig.start()
    rig.controller.cancel()
    rig.pump(lambda: bool(rig.done))
    gate.set()

    assert rig.done[0].outcome == "cancelled"
    assert "stage" not in rig.names()


# --- --apply-update ----------------------------------------------------------


def _release_json(tag, *, assets=True):
    name = f"tokitty-{tag}-linux-x86_64.tar.gz"
    listing = [{"name": name, "browser_download_url": f"https://example.test/{name}", "size": 10}] if assets else []
    listing.append({"name": "SHA256SUMS", "browser_download_url": "https://example.test/SHA256SUMS"})
    return {"tag_name": tag, "draft": False, "prerelease": False, "html_url": "https://example.test/r", "assets": listing}


def test_apply_newest_finds_and_installs_the_newest_release(tmp_path, monkeypatch):
    monkeypatch.setattr(update_controller, "platform_target", lambda sys_platform=None, machine=None: "linux-x86_64")
    rig = Rig(tmp_path, fetch=lambda: [_release_json("v0.1.0"), _release_json("v0.2.5"), _release_json("v0.2.0")])
    rig.controller.start_apply_newest(rig.done.append)
    rig.pump(lambda: bool(rig.done))

    assert rig.done[0] == UpdateResult("installed", "Updated to v0.2.5.", "v0.2.5")
    assert rig.argv[0] == str(rig.versions / "v0.2.5" / "tokitty" / "tokitty")


@pytest.mark.parametrize(
    "listing, text",
    [
        ([_release_json("v0.1.0")], "no release newer"),
        ([_release_json("v0.3.0", assets=False)], "no download"),
        ([], "no release newer"),
    ],
)
def test_apply_newest_fails_when_nothing_newer_is_installable(tmp_path, monkeypatch, listing, text):
    monkeypatch.setattr(update_controller, "platform_target", lambda sys_platform=None, machine=None: "linux-x86_64")
    rig = Rig(tmp_path, fetch=lambda: listing)
    rig.controller.start_apply_newest(rig.done.append)
    rig.pump(lambda: bool(rig.done))

    assert rig.done[0].outcome == "failed" and text in rig.done[0].message
    assert "stage" not in rig.names()


def test_apply_newest_reports_a_failed_fetch(tmp_path):
    def broken():
        raise UpdateCheckError("offline")

    rig = Rig(tmp_path, fetch=broken)
    rig.controller.start_apply_newest(rig.done.append)
    rig.pump(lambda: bool(rig.done))

    assert rig.done[0].outcome == "failed" and "offline" in rig.done[0].message


# --- the test-only no-ack switch ---------------------------------------------


def test_skips_ack_needs_both_variables():
    assert skips_ack({"TOKITTY_UPDATE_TEST_NO_ACK": "1", "TOKITTY_UPDATE_API_URL": "https://x"})
    assert not skips_ack({"TOKITTY_UPDATE_TEST_NO_ACK": "1"})
    assert not skips_ack({"TOKITTY_UPDATE_API_URL": "https://x"})
    assert not skips_ack({"TOKITTY_UPDATE_TEST_NO_ACK": "0", "TOKITTY_UPDATE_API_URL": "https://x"})
    assert not skips_ack({"TOKITTY_UPDATE_TEST_NO_ACK": "1", "TOKITTY_UPDATE_API_URL": ""})


# --- TkHost ------------------------------------------------------------------


class _Recorder:
    def __init__(self, log, name):
        self._log, self._name = log, name

    def __getattr__(self, method):
        return lambda *a: self._log.append(f"{self._name}.{method}")


class _Root(_Recorder):
    def after(self, ms, fn):
        self._log.append(f"root.after({ms})")
        fn()


def _host(tmp_path, monkeypatch, **kw):
    log = []
    root = _Root(log, "root")
    content = _Recorder(log, "content")
    tray = _Recorder(log, "tray")
    lock = _Recorder(log, "lock")
    monkeypatch.setattr(update_controller, "acquire_with_retry", lambda lk, timeout: log.append(f"acquire({timeout})"))
    monkeypatch.setattr(update_controller.runner_link, "ensure_runner_link", lambda sd: log.append("link"))
    monkeypatch.setattr(update_controller.autostart, "ensure_current", lambda sd, b: log.append(f"autostart({b})"))
    host = TkHost(root, [root, content, content], tray, lock, tmp_path, kw.pop("backend", "BACKEND"), **kw)
    return host, log, root


def test_host_hide_stops_the_tray_withdraws_windows_then_releases_the_lock(tmp_path, monkeypatch):
    host, log, _ = _host(tmp_path, monkeypatch, quiet=True)
    host.hide()

    assert log == ["tray.stop", "root.withdraw", "content.withdraw", "root.update_idletasks", "lock.release"]


def test_host_restore_retakes_the_lock_relinks_and_brings_everything_back(tmp_path, monkeypatch):
    host, log, _ = _host(tmp_path, monkeypatch, quiet=True)
    host.restore()

    assert log == [
        "acquire(10.0)", "link", "autostart(BACKEND)", "root.deiconify", "content.deiconify", "tray.start", "tray.refresh",
    ]


def test_host_restore_leaves_the_tray_off_when_it_was_disabled(tmp_path, monkeypatch):
    host, log, _ = _host(tmp_path, monkeypatch, quiet=True, tray_enabled=lambda: False)
    host.restore()

    assert "tray.start" not in log


def test_host_restore_stops_when_the_lock_cannot_be_retaken(tmp_path, monkeypatch):
    host, log, _ = _host(tmp_path, monkeypatch, quiet=True)

    def held(lk, timeout):
        raise LockAcquisitionError("held")

    monkeypatch.setattr(update_controller, "acquire_with_retry", held)
    with pytest.raises(LockAcquisitionError):
        host.restore()
    assert log == []


def test_host_restore_survives_relink_and_autostart_failures(tmp_path, monkeypatch, capsys):
    host, log, _ = _host(tmp_path, monkeypatch, quiet=True)

    def fail(*a):
        raise OSError("nope")

    monkeypatch.setattr(update_controller.runner_link, "ensure_runner_link", fail)
    monkeypatch.setattr(update_controller.autostart, "ensure_current", fail)
    host.restore()

    assert "root.deiconify" in log and "tray.start" in log
    assert "relinking hooks: nope" in capsys.readouterr().err


def test_host_restore_skips_autostart_with_no_backend(tmp_path, monkeypatch):
    host, log, _ = _host(tmp_path, monkeypatch, quiet=True, backend=None)
    host.restore()

    assert not any(line.startswith("autostart") for line in log)


def test_host_notice_is_a_deferred_messagebox_and_silent_when_quiet(tmp_path, monkeypatch, capsys):
    shown = []

    class Box:
        @staticmethod
        def showwarning(title, text, parent=None):
            shown.append((title, text, parent))

    host, log, root = _host(tmp_path, monkeypatch, messagebox=Box)
    host.notice("Tokitty v1 didn't start.")
    assert shown == [("Tokitty", "Tokitty v1 didn't start.", root)] and "root.after(0)" in log

    quiet, quiet_log, _ = _host(tmp_path, monkeypatch, quiet=True)
    quiet.notice("hello")
    assert quiet_log == [] and capsys.readouterr().err == ""
    assert len(shown) == 1


def test_host_finish_is_deferred_and_safe_twice(tmp_path, monkeypatch):
    host, log, _ = _host(tmp_path, monkeypatch, quiet=True)

    class Boom(_Root):
        def destroy(self):
            raise RuntimeError("already destroyed")

    host._root = Boom(log, "root")
    host.finish()
    host.finish()

    assert log.count("root.after(0)") == 2
