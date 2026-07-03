import threading
import time
from datetime import datetime, timezone

from tokitty.poller import PollResult, Poller


def _ok_result():
    return PollResult(status="ok", snapshot=None, message=None, fetched_at=datetime.now(timezone.utc))


def test_poller_calls_fetch_fn_and_stores_latest_result():
    call_count = {"n": 0}
    ready = threading.Event()

    def fake_fetch():
        call_count["n"] += 1
        ready.set()
        return _ok_result()

    poller = Poller(fetch_fn=fake_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        assert ready.wait(timeout=2)
        time.sleep(0.05)
        assert poller.get_latest().status == "ok"
        assert call_count["n"] >= 1
    finally:
        poller.stop()


def test_poller_recovers_after_an_error():
    results = iter(
        [
            PollResult(status="api_error", snapshot=None, message="boom", fetched_at=datetime.now(timezone.utc)),
            _ok_result(),
        ]
    )
    done = threading.Event()

    def fake_fetch():
        try:
            result = next(results)
        except StopIteration:
            done.set()
            return _ok_result()
        if result.status == "ok":
            done.set()
        return result

    poller = Poller(fetch_fn=fake_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        assert done.wait(timeout=2)
        time.sleep(0.05)
        assert poller.get_latest().status == "ok"
    finally:
        poller.stop()


def test_request_refresh_wakes_the_poller_immediately():
    call_count = {"n": 0}
    lock = threading.Lock()

    def fake_fetch():
        with lock:
            call_count["n"] += 1
        return _ok_result()

    poller = Poller(fetch_fn=fake_fetch, poll_interval=3600)
    poller.start()
    try:
        time.sleep(0.05)
        with lock:
            first_count = call_count["n"]
        poller.request_refresh()
        time.sleep(0.1)
        with lock:
            assert call_count["n"] > first_count
    finally:
        poller.stop()


def test_poller_never_raises_out_of_the_loop_when_fetch_fn_raises():
    def raising_fetch():
        raise RuntimeError("boom")

    poller = Poller(fetch_fn=raising_fetch, poll_interval=60, sleep_fn=lambda seconds: True)
    poller.start()
    try:
        time.sleep(0.1)
        latest = poller.get_latest()
        assert latest is not None
        assert latest.status == "api_error"
    finally:
        poller.stop()
