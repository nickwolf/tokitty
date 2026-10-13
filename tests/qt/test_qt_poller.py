"""QtPoller: worker results arrive on the GUI thread, in order."""
import threading

import pytest
from PySide6.QtCore import QObject, QTimer

from tokitty.qt.poller import QtPoller
from tokitty.worker_mailbox import Mailbox, run_worker

pytestmark = pytest.mark.qt


def spin(qapp, until, limit_ms=3000):
    """Run the event loop until `until()` is true or the limit passes."""
    from PySide6.QtCore import QEventLoop

    loop = QEventLoop()
    waited = []

    def check():
        if until() or sum(waited) >= limit_ms:
            loop.quit()
        waited.append(10)

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(10)
    loop.exec()
    timer.stop()


def test_results_from_workers_arrive_on_the_gui_thread_in_order(qapp):
    owner = QObject()
    box = Mailbox()
    got = []
    worker_threads = []

    def handler(tag, ok, value):
        got.append((tag, ok, value, threading.current_thread()))

    poller = QtPoller(owner, box, handler, lambda: len(got) < 4)

    def work(n):
        def fn():
            worker_threads.append(threading.current_thread())
            return n
        return fn

    for n in range(3):
        box.put(f"t{n}", True, n)  # order is the mailbox order
    run_worker(box, "worker", work(9))
    poller.start()
    spin(qapp, lambda: len(got) >= 4)

    assert [g[0] for g in got[:3]] == ["t0", "t1", "t2"]
    assert got[3][:3] == ("worker", True, 9)
    assert all(g[3] is threading.main_thread() for g in got)
    assert worker_threads and worker_threads[0] is not threading.main_thread()


def test_on_tick_runs_on_an_idle_tick(qapp):
    owner = QObject()
    ticks = []
    poller = QtPoller(owner, Mailbox(), lambda *a: None, lambda: len(ticks) < 3,
                      on_tick=lambda: ticks.append(threading.current_thread()), interval_ms=10)
    poller.start()
    spin(qapp, lambda: len(ticks) >= 3)

    assert len(ticks) == 3
    assert all(t is threading.main_thread() for t in ticks)


def test_it_stops_rearming_when_keep_alive_turns_false(qapp):
    owner = QObject()
    ticks = []
    poller = QtPoller(owner, Mailbox(), lambda *a: None, lambda: False,
                      on_tick=lambda: ticks.append(1), interval_ms=10)
    poller.start()
    spin(qapp, lambda: False, limit_ms=150)

    assert ticks == [1]


def test_cancel_stops_delivery_and_empties_the_mailbox(qapp):
    owner = QObject()
    box = Mailbox()
    got = []
    poller = QtPoller(owner, box, lambda *a: got.append(a), lambda: True, interval_ms=10)
    box.put("a", True, 1)
    poller.start()
    poller.cancel()
    box.put("late", True, 2)
    poller.cancel()
    spin(qapp, lambda: False, limit_ms=120)

    assert got == []
    assert box.drain() == []
    poller.start()  # a cancelled poller stays cancelled
    box.put("again", True, 3)
    spin(qapp, lambda: False, limit_ms=120)
    assert got == []


def test_cancel_from_a_handler_drops_the_rest_of_the_batch(qapp):
    owner = QObject()
    box = Mailbox()
    got = []
    ticks = []

    def handler(tag, ok, value):
        got.append(tag)
        poller.cancel()

    poller = QtPoller(owner, box, handler, lambda: True, on_tick=lambda: ticks.append(1),
                      interval_ms=10)
    box.put("a", True, 1)
    box.put("b", True, 2)
    poller.start()
    spin(qapp, lambda: False, limit_ms=150)

    assert got == ["a"]
    assert ticks == []
