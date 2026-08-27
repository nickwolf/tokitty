import gc
import threading

import pytest


@pytest.fixture(autouse=True)
def _clean_up_gui_root(request, monkeypatch):
    """Keep one GUI test's Tcl event queue out of the next GUI test.

    Several run_gui tests replace Tk.mainloop with a bounded or no-op test
    double.  Unlike the real quit path, that double does not invoke the
    application's root.destroy callback, so recurring ``after`` work can
    otherwise survive until a later test pumps Tcl's process-wide queue.
    Join every test-spawned worker first, then destroy and collect Tk objects
    on their owning thread so a later worker cannot run Tk finalizers.
    """
    if request.node.get_closest_marker("gui") is None:
        yield
        return

    spawned = []
    real_thread_class = threading.Thread

    class _TrackedThread(real_thread_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self)

    monkeypatch.setattr(threading, "Thread", _TrackedThread)

    yield

    for thread in spawned:
        if thread is threading.current_thread():
            continue
        if thread.ident is None and not thread.is_alive():
            continue
        thread.join(timeout=10.0)
    still_alive = [thread.name for thread in spawned if thread.is_alive()]
    assert not still_alive, f"GUI test leaked background threads: {still_alive}"

    try:
        import tkinter as tk
    except ImportError:
        return

    root = getattr(tk, "_default_root", None)
    if root is not None:
        try:
            pending = root.tk.splitlist(root.tk.call("after", "info"))
            for after_id in pending:
                try:
                    root.after_cancel(after_id)
                except tk.TclError:
                    pass
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass

    # Tkinter objects can participate in cycles.  Collect them here on the
    # thread that owned Tcl, rather than letting a later filesystem worker
    # trigger their __del__ methods from a background thread.
    gc.collect()
