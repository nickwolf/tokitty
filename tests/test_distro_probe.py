import subprocess
import threading
import time

from tokitty.distro_probe import ProbeStatus, RunningDistroProbe


class FakeCompletedProcess:
    def __init__(self, stdout: bytes = b"", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_get_running_returns_confirmed_distros():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_running() == ["Ubuntu"]
    assert probe.get_result().status == ProbeStatus.CONFIRMED


def test_get_result_empty_status_on_zero_distros():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout=b"")

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_result().status == ProbeStatus.EMPTY
    assert probe.get_result().distros == frozenset()


def test_get_result_unknown_status_on_subprocess_error():
    def fake_run(cmd, **kwargs):
        raise OSError("wsl.exe not found")

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_result().status == ProbeStatus.UNKNOWN


def test_get_result_unknown_status_on_timeout():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="wsl.exe", timeout=2)

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_result().status == ProbeStatus.UNKNOWN


def test_success_ttl_avoids_a_second_call_within_window():
    calls = []
    fake_time = {"now": 0.0}

    def fake_run(cmd, **kwargs):
        calls.append(1)
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run, time_fn=lambda: fake_time["now"], success_ttl=1.0)
    probe.get_result()
    fake_time["now"] += 0.5
    probe.get_result()
    assert len(calls) == 1


def test_ttl_expiry_triggers_a_fresh_call():
    calls = []
    fake_time = {"now": 0.0}

    def fake_run(cmd, **kwargs):
        calls.append(1)
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run, time_fn=lambda: fake_time["now"], success_ttl=1.0)
    probe.get_result()
    fake_time["now"] += 1.5
    probe.get_result()
    assert len(calls) == 2


def test_failed_refresh_invalidates_a_previously_confirmed_result():
    fake_time = {"now": 0.0}
    responses = [
        FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le")),
    ]

    def fake_run(cmd, **kwargs):
        if responses:
            return responses.pop()
        raise OSError("wsl.exe not found")

    probe = RunningDistroProbe(run=fake_run, time_fn=lambda: fake_time["now"], success_ttl=1.0)
    first = probe.get_result()
    assert first.status == ProbeStatus.CONFIRMED
    fake_time["now"] += 1.5
    second = probe.get_result()
    assert second.status == ProbeStatus.UNKNOWN
    assert second.distros == frozenset()


def test_single_flight_coalesces_concurrent_callers():
    call_count = {"n": 0}
    release = threading.Event()
    entered = threading.Barrier(1, timeout=5)

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        entered.wait()
        release.wait(timeout=5)
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run)
    threads = [threading.Thread(target=probe.get_result) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    release.set()
    for t in threads:
        t.join(timeout=5)
    assert call_count["n"] == 1
