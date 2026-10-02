import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from tokitty import update_check
from tokitty.update_check import UpdateChecker, announcer
from tokitty.updater import Release, RunningVersion, UpdateCheckError, UpdateState, load_update_state, save_update_state

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
RUNNING = RunningVersion("v0.2.0", True)
MAIN = threading.main_thread()


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


def listing(tag):
    name = f"tokitty-{tag}-linux-x86_64.tar.gz"
    return [{
        "tag_name": tag, "draft": False, "prerelease": False, "html_url": f"https://example.test/{tag}",
        "assets": [
            {"name": name, "browser_download_url": f"https://example.test/{name}", "size": 10},
            {"name": "SHA256SUMS", "browser_download_url": "https://example.test/SHA256SUMS"},
        ],
    }]


class Fetch:
    """A fake releases list that records its callers and can be held open."""

    def __init__(self, tag="v0.3.0", error=None):
        self.tag, self.error, self.calls, self.threads = tag, error, 0, []
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self):
        self.calls += 1
        self.threads.append(threading.current_thread())
        assert self.gate.wait(5)
        if self.error:
            raise self.error
        return listing(self.tag)


def make(tmp_path, clock=None, fetch=None, **kwargs):
    clock = clock or Clock()
    fetch = fetch or Fetch()
    kwargs.setdefault("target", "linux-x86_64")
    return UpdateChecker(tmp_path, RUNNING, fetch=fetch, now=clock, **kwargs), clock, fetch


def settle(checker, until=lambda: True, seconds=5.0):
    """Tick, as the Tk thread does, until the worker's result has been consumed."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        checker.tick()
        # The worker clears _checking and sets _result together, so reading
        # them in this order can't miss a result that has not been consumed.
        if not checker._checking and checker._result is None and until():
            return
        time.sleep(0.005)
    raise AssertionError("the check never finished")


def test_no_check_before_sixty_seconds(tmp_path):
    checker, clock, fetch = make(tmp_path)
    clock.advance(seconds=59)
    checker.tick()
    assert fetch.calls == 0
    assert checker.menu_label() is None


def test_first_check_runs_after_sixty_seconds_on_a_worker_thread(tmp_path):
    checker, clock, fetch = make(tmp_path)
    clock.advance(seconds=60)
    settle(checker)
    assert fetch.calls == 1
    assert fetch.threads[0] is not MAIN
    assert checker.menu_label() == "Update to v0.3.0…"
    assert checker.latest_release.tag == "v0.3.0"
    assert checker.update_available is True


def test_a_successful_check_writes_last_checked_and_latest_tag(tmp_path):
    checker, clock, _ = make(tmp_path)
    clock.advance(seconds=60)
    settle(checker)
    state = load_update_state(tmp_path)
    assert state.latest_tag == "v0.3.0"
    # Parses on Python 3.10, where fromisoformat rejects a trailing "Z".
    assert datetime.fromisoformat(state.last_checked) == clock.now
    assert state.last_checked.endswith("+00:00")


def test_no_check_while_disabled_then_one_when_switched_on(tmp_path):
    on = {"v": False}
    checker, clock, fetch = make(tmp_path, enabled=lambda: on["v"])
    clock.advance(hours=30)
    checker.tick()
    assert fetch.calls == 0
    on["v"] = True
    settle(checker)
    assert fetch.calls == 1


def test_one_check_per_day(tmp_path):
    checker, clock, fetch = make(tmp_path)
    clock.advance(seconds=60)
    settle(checker)
    for _ in range(3):
        clock.advance(hours=1)
        checker.tick()
    assert fetch.calls == 1
    clock.advance(hours=22, minutes=1)
    settle(checker)
    assert fetch.calls == 2


def test_a_fresh_last_checked_defers_the_first_check(tmp_path):
    save_update_state(tmp_path, UpdateState(last_checked=(T0 - timedelta(hours=10)).isoformat()))
    checker, clock, fetch = make(tmp_path)
    clock.advance(minutes=5)
    checker.tick()
    assert fetch.calls == 0
    clock.advance(hours=14)
    settle(checker)
    assert fetch.calls == 1


def test_an_old_last_checked_checks_after_sixty_seconds(tmp_path):
    save_update_state(tmp_path, UpdateState(last_checked=(T0 - timedelta(hours=21)).isoformat()))
    checker, clock, fetch = make(tmp_path)
    clock.advance(seconds=60)
    settle(checker)
    assert fetch.calls == 1


@pytest.mark.parametrize("stamp", ["not a date", "2999-01-01T00:00:00+00:00"])
def test_an_unreadable_or_future_last_checked_counts_as_never(tmp_path, stamp):
    state = UpdateState(last_checked="2026-01-01T00:00:00+00:00")
    save_update_state(tmp_path, state)
    path = tmp_path / "update.json"
    path.write_text(path.read_text().replace("2026-01-01T00:00:00+00:00", stamp))
    checker, clock, fetch = make(tmp_path)
    clock.advance(seconds=60)
    settle(checker)
    assert fetch.calls == 1


def test_a_failed_scheduled_check_is_silent_writes_nothing_and_retries_in_an_hour(tmp_path):
    fetch = Fetch(error=UpdateCheckError("no network"))
    seen = []
    checker, clock, fetch = make(tmp_path, fetch=fetch, on_available=seen.append)
    clock.advance(seconds=60)
    settle(checker)
    assert fetch.calls == 1 and seen == []
    assert not (tmp_path / "update.json").exists()
    assert checker.menu_label() is None
    clock.advance(minutes=59)
    checker.tick()
    assert fetch.calls == 1
    clock.advance(minutes=1)
    settle(checker)
    assert fetch.calls == 2


def test_an_unexpected_fetch_error_is_reported_not_raised(tmp_path):
    results = []
    checker, _, _ = make(tmp_path, fetch=Fetch(error=ValueError("boom")))
    checker.check_now(results.append)
    settle(checker)
    assert results[0].error == "The update check failed: boom"


def test_a_release_that_is_not_newer_shows_no_label_but_is_recorded(tmp_path):
    checker, clock, _ = make(tmp_path, fetch=Fetch("v0.2.0"))
    clock.advance(seconds=60)
    settle(checker)
    assert checker.menu_label() is None
    assert checker.latest_release.tag == "v0.2.0" and checker.update_available is False
    assert load_update_state(tmp_path).latest_tag == "v0.2.0"


def test_a_newer_tag_from_an_earlier_run_shows_straight_away(tmp_path):
    save_update_state(tmp_path, UpdateState(last_checked=T0.isoformat(), latest_tag="v0.3.0"))
    checker, _, fetch = make(tmp_path)
    assert checker.menu_label() == "Update to v0.3.0…"
    assert checker.latest_release is None and fetch.calls == 0


def test_a_stale_tag_that_is_no_longer_newer_shows_nothing(tmp_path):
    save_update_state(tmp_path, UpdateState(latest_tag="v0.1.9"))
    checker, _, _ = make(tmp_path)
    assert checker.menu_label() is None


def test_manual_check_runs_when_disabled_and_calls_back_from_tick(tmp_path):
    checker, clock, fetch = make(tmp_path, enabled=lambda: False)
    results = []
    checker.check_now(lambda result: results.append((result, threading.current_thread())))
    settle(checker, until=lambda: results)
    (result, thread), = results
    assert thread is MAIN
    assert result.newer and result.release.tag == "v0.3.0" and result.error is None
    assert fetch.calls == 1


def test_manual_check_reports_an_error_and_a_current_version(tmp_path):
    results = []
    checker, _, _ = make(tmp_path, fetch=Fetch(error=UpdateCheckError("HTTP 500 from the releases list")))
    checker.check_now(results.append)
    settle(checker, until=lambda: results)
    assert results[0].error == "HTTP 500 from the releases list" and results[0].release is None

    checker, _, _ = make(tmp_path, fetch=Fetch("v0.2.0"))
    checker.check_now(results.append)
    settle(checker, until=lambda: len(results) == 2)
    assert results[1].error is None and results[1].newer is False


def test_manual_checks_join_one_in_flight(tmp_path):
    fetch = Fetch()
    fetch.gate.clear()
    checker, _, _ = make(tmp_path, fetch=fetch)
    first, second = [], []
    checker.check_now(first.append)
    checker.check_now(second.append)
    fetch.gate.set()
    settle(checker, until=lambda: first and second)
    assert fetch.calls == 1


def test_on_available_fires_once_per_tag(tmp_path):
    seen = []
    fetch = Fetch()
    checker, clock, _ = make(tmp_path, fetch=fetch, on_available=lambda release: seen.append(release.tag))
    clock.advance(seconds=60)
    settle(checker)
    clock.advance(hours=25)
    settle(checker)
    assert fetch.calls == 2 and seen == ["v0.3.0"]
    fetch.tag = "v0.4.0"
    clock.advance(hours=25)
    settle(checker)
    assert seen == ["v0.3.0", "v0.4.0"]


def test_a_failing_callback_does_not_break_the_tick(tmp_path, capsys):
    def boom(release):
        raise RuntimeError("nope")

    checker, clock, _ = make(tmp_path, on_available=boom)
    clock.advance(seconds=60)
    settle(checker)
    assert checker.menu_label() == "Update to v0.3.0…"
    assert "callback: nope" in capsys.readouterr().err


def test_the_default_fetch_is_looked_up_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setattr(update_check, "fetch_releases", lambda: listing("v0.9.0"))
    checker = UpdateChecker(tmp_path, RUNNING, now=Clock(), target="linux-x86_64")
    results = []
    checker.check_now(results.append)
    settle(checker, until=lambda: results)
    assert results[0].release.tag == "v0.9.0"


# --- the announcement ---------------------------------------------------------

RELEASE = Release("v0.3.0", None, None, None, None)


def test_announcer_refreshes_notifies_and_records_the_tag(tmp_path):
    tray = Mock()
    tray.notify.return_value = True
    announcer(tmp_path, tray)(RELEASE)
    tray.refresh.assert_called_once_with()
    tray.notify.assert_called_once_with("Tokitty v0.3.0 is available", "Right-click Tokitty to update")
    assert load_update_state(tmp_path).notified_tag == "v0.3.0"


def test_announcer_does_not_notify_a_tag_twice(tmp_path):
    tray = Mock()
    tray.notify.return_value = True
    announce = announcer(tmp_path, tray)
    announce(RELEASE)
    announce(RELEASE)
    assert tray.notify.call_count == 1
    tray.refresh.assert_called()
    announcer(tmp_path, tray)(RELEASE)  # a later run reads the record
    assert tray.notify.call_count == 1


def test_announcer_notifies_the_next_tag(tmp_path):
    tray = Mock()
    tray.notify.return_value = True
    announce = announcer(tmp_path, tray)
    announce(RELEASE)
    announce(Release("v0.4.0", None, None, None, None))
    assert tray.notify.call_count == 2
    assert load_update_state(tmp_path).notified_tag == "v0.4.0"


def test_announcer_leaves_notified_tag_unset_when_the_tray_could_not_notify(tmp_path):
    tray = Mock()
    tray.notify.return_value = False
    announcer(tmp_path, tray)(RELEASE)
    assert load_update_state(tmp_path).notified_tag is None
    tray.refresh.assert_called_once_with()
