import json
import sys
import threading

from tokitty.streamdock import wt_focus
from tokitty.streamdock.model import SessionRef
from tokitty.streamdock.wt_focus import (
    FocusDone,
    FocusWorker,
    InterruptDone,
    Tab,
    TitleDone,
    VerifyDone,
    WtFocus,
    normalise_tab_name,
    read_tail,
    tab_matches,
    title_from_tail,
)

S = SessionRef(0, "sess-1")
S2 = SessionRef(0, "sess-2")


class FakeAdapter:
    def __init__(self, tabs, foreground=100, front_ok=True):
        self.tabs = list(tabs)
        self.fg = foreground
        self.front_ok = front_ok
        self.selected_calls = []
        self.front_calls = []
        self.escapes = 0

    def list_tabs(self):
        return list(self.tabs)

    def select(self, tab):
        self.selected_calls.append(tab)
        self.tabs = [Tab(t.window_handle, t.name, t == tab, t.element) for t in self.tabs]

    def bring_to_front(self, handle):
        self.front_calls.append(handle)
        if self.front_ok:
            self.fg = handle
        return self.front_ok

    def foreground_window(self):
        return self.fg

    def send_escape_key(self):
        self.escapes += 1


def write_transcript(tmp_path, session_id, lines):
    d = tmp_path / "projects" / "proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (session_id + ".jsonl")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def title_line(title, sid="sess-1"):
    return json.dumps({"type": "ai-title", "aiTitle": title, "sessionId": sid})


def make(tmp_path, tabs, title="Fix the build", **kw):
    write_transcript(tmp_path, "sess-1", [title_line(title)])
    adapter = FakeAdapter(tabs, **kw)
    return WtFocus(adapter), adapter, str(tmp_path)


def tab(handle, name, selected=False):
    return Tab(handle, name, selected, element=(handle, name))


# title

def test_last_ai_title_wins(tmp_path):
    write_transcript(tmp_path, "sess-1", [title_line("Old"), '{"type":"user"}', title_line("New")])
    assert WtFocus(None).title(S, str(tmp_path)) == "New"


def test_missing_title_gives_no_title(tmp_path):
    write_transcript(tmp_path, "sess-1", ['{"type":"user"}'])
    f = WtFocus(FakeAdapter([tab(1, "x")]))
    assert f.focus(S, str(tmp_path)) == "no_title"


def test_missing_transcript_gives_no_title(tmp_path):
    f = WtFocus(FakeAdapter([tab(1, "x")]))
    assert f.focus(S, str(tmp_path)) == "no_title"


def test_title_for_other_session_ignored():
    assert title_from_tail(title_line("Other", sid="zzz"), "sess-1") is None
    assert title_from_tail('{"type":"ai-title","aiTitle":"No id"}', "sess-1") == "No id"


def test_read_tail_skips_truncated_first_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("aaaa\n" + title_line("Kept") + "\n", encoding="utf-8")
    text = read_tail(str(p), nbytes=len(title_line("Kept")) + 4)
    assert text is not None
    assert "aaaa" not in text
    assert title_from_tail(text, "sess-1") == "Kept"


def test_read_tail_whole_file_keeps_first_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(title_line("First") + "\n", encoding="utf-8")
    assert title_from_tail(read_tail(str(p)), "sess-1") == "First"


def test_read_tail_missing_file(tmp_path):
    assert read_tail(str(tmp_path / "nope.jsonl")) is None


def test_path_cache_and_reglob_when_file_disappears(tmp_path):
    p = write_transcript(tmp_path, "sess-1", [title_line("One")])
    calls = []

    def counting_glob(pattern):
        import glob as g
        calls.append(pattern)
        return g.glob(pattern)

    f = WtFocus(None, glob_fn=counting_glob)
    assert f.title(S, str(tmp_path)) == "One"
    assert f.title(S, str(tmp_path)) == "One"
    assert len(calls) == 1
    p.unlink()
    q = tmp_path / "projects" / "other"
    q.mkdir()
    (q / "sess-1.jsonl").write_text(title_line("Moved") + "\n", encoding="utf-8")
    assert f.title(S, str(tmp_path)) == "Moved"
    assert len(calls) == 2


# matching

def test_name_normalisation():
    assert normalise_tab_name("⠋ Fix the build") == "Fix the build"
    assert normalise_tab_name("✳  Fix the build ") == "Fix the build"
    assert normalise_tab_name("Fix the build") == "Fix the build"


def test_title_starting_with_punctuation_matches():
    assert tab_matches("⠋ [WIP] thing", "[WIP] thing")
    assert tab_matches("[WIP] thing", "[WIP] thing")
    assert not tab_matches("⠋ Fix the build now", "Fix the build")


def test_one_match_focuses_across_windows(tmp_path):
    f, a, cfg = make(tmp_path, [tab(1, "⠋ Other"), tab(2, "✳ Fix the build"), tab(2, "pwsh")])
    assert f.focus(S, cfg) == "focused"
    assert [t.element for t in a.selected_calls] == [(2, "✳ Fix the build")]
    assert a.front_calls == [2]


def test_zero_matches_not_found(tmp_path):
    f, a, cfg = make(tmp_path, [tab(1, "Other")])
    assert f.focus(S, cfg) == "not_found"
    assert a.selected_calls == [] and a.front_calls == []


def test_two_matches_ambiguous(tmp_path):
    f, a, cfg = make(tmp_path, [tab(1, "⠋ Fix the build"), tab(2, "✳ Fix the build")])
    assert f.focus(S, cfg) == "ambiguous"
    assert a.selected_calls == [] and a.front_calls == []


def test_two_tabs_same_name_same_window_ambiguous(tmp_path):
    f, a, cfg = make(tmp_path, [tab(1, "Fix the build"), Tab(1, "Fix the build", False, "e2")])
    assert f.focus(S, cfg) == "ambiguous"


def test_bring_to_front_false_gives_not_front(tmp_path):
    f, a, cfg = make(tmp_path, [tab(1, "Fix the build")], front_ok=False)
    assert f.focus(S, cfg) == "not_front"
    assert len(a.selected_calls) == 1
    assert f.is_selected(S) is False


# is_selected and escape

def focused(tmp_path, tabs=None):
    f, a, cfg = make(tmp_path, tabs or [tab(1, "⠋ Fix the build"), tab(1, "Other")])
    assert f.focus(S, cfg) == "focused"
    return f, a


def test_is_selected_true_after_focus(tmp_path):
    f, a = focused(tmp_path)
    assert f.is_selected(S) is True


def test_is_selected_false_after_manual_switch(tmp_path):
    f, a = focused(tmp_path)
    a.tabs = [Tab(1, "⠋ Fix the build", False), Tab(1, "Other", True)]
    assert f.is_selected(S) is False


def test_is_selected_false_when_other_window_foreground(tmp_path):
    f, a = focused(tmp_path)
    a.fg = 999
    assert f.is_selected(S) is False


def test_is_selected_false_when_tab_appears_twice(tmp_path):
    f, a = focused(tmp_path)
    a.tabs.append(Tab(2, "✳ Fix the build", False))
    assert f.is_selected(S) is False


def test_is_selected_false_when_tab_moved_window(tmp_path):
    f, a = focused(tmp_path)
    a.tabs = [Tab(2, "Fix the build", True)]
    a.fg = 2
    assert f.is_selected(S) is False


def test_is_selected_false_with_no_remembered_match(tmp_path):
    f, a, cfg = make(tmp_path, [tab(1, "Fix the build", True)])
    assert f.is_selected(S) is False


def test_forget_drops_remembered_match(tmp_path):
    f, a = focused(tmp_path)
    f.forget(S)
    assert f.is_selected(S) is False


def test_send_escape_only_when_selected(tmp_path):
    f, a = focused(tmp_path)
    assert f.send_escape(S) is True
    assert a.escapes == 1
    a.fg = 999
    assert f.send_escape(S) is False
    assert a.escapes == 1


# unavailable

def test_unavailable_without_adapter(tmp_path):
    f = WtFocus(None)
    assert f.available is False
    assert f.focus(S, str(tmp_path)) == "unavailable"
    assert f.is_selected(S) is False
    assert f.send_escape(S) is False


def test_make_adapter_none_without_comtypes(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "comtypes", None)
    monkeypatch.setitem(sys.modules, "comtypes.client", None)
    assert wt_focus.make_uia_adapter() is None
    assert WtFocus(wt_focus.make_uia_adapter()).available is False


def test_make_adapter_none_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert wt_focus.make_uia_adapter() is None


# worker

class FakeFocus:
    def __init__(self):
        self.calls = []
        self.thread = None

    def focus(self, session, config_dir):
        self.calls.append(("focus", session, config_dir))
        self.thread = threading.current_thread()
        if session == S2:
            raise RuntimeError("boom")
        return "focused"

    def is_selected(self, session):
        self.calls.append(("verify", session))
        if session == S2:
            raise RuntimeError("boom")
        return True

    def send_escape(self, session):
        self.calls.append(("esc", session))
        if session == S2:
            raise RuntimeError("boom")
        return True

    def forget(self, session):
        self.calls.append(("forget", session))

    def title(self, session, config_dir):
        self.calls.append(("title", session, config_dir))
        if session == S2:
            raise RuntimeError("boom")
        return "My title"


def run_worker(submit, make=None):
    fake = FakeFocus()
    results = []
    worker = FocusWorker(make or (lambda: fake), results.append)
    submit(worker)
    worker.stop(timeout=0.5)
    return fake, results, worker


def test_worker_results_and_order():
    def submit(w):
        w.submit_focus(S, 3, "cfg")
        w.submit_verify("n1", S)
        w.forget(S)
        w.submit_interrupt(S)

    fake, results, worker = run_worker(submit)
    assert results == [FocusDone(S, 3, "focused"), VerifyDone("n1", True), InterruptDone(S, True)]
    assert [c[0] for c in fake.calls] == ["focus", "verify", "forget", "esc"]
    assert not worker._thread.is_alive()


def test_worker_runs_on_its_own_thread():
    fake, _, _ = run_worker(lambda w: w.submit_focus(S, 1, "cfg"))
    assert fake.thread is not threading.main_thread()
    assert fake.thread.daemon


def test_worker_exceptions_give_safe_results_and_thread_survives():
    def submit(w):
        w.submit_focus(S2, 1, "cfg")
        w.submit_verify("n", S2)
        w.submit_interrupt(S2)
        w.submit_focus(S, 2, "cfg")

    _, results, _ = run_worker(submit)
    assert results == [
        FocusDone(S2, 1, "unavailable"),
        VerifyDone("n", False),
        InterruptDone(S2, False),
        FocusDone(S, 2, "focused"),
    ]


def test_worker_make_focus_failure_gives_safe_results():
    def bad():
        raise RuntimeError("no uia")

    def submit(w):
        w.submit_focus(S, 1, "cfg")
        w.submit_verify("n", S)
        w.submit_interrupt(S)

    _, results, _ = run_worker(submit, make=bad)
    assert results == [FocusDone(S, 1, "unavailable"), VerifyDone("n", False), InterruptDone(S, False)]


def test_worker_on_result_exception_does_not_kill_thread():
    fake = FakeFocus()
    seen = []

    def on_result(r):
        seen.append(r)
        if len(seen) == 1:
            raise ValueError("bad consumer")

    w = FocusWorker(lambda: fake, on_result)
    w.submit_verify("a", S)
    w.submit_verify("b", S)
    w.stop(timeout=0.5)
    assert seen == [VerifyDone("a", True), VerifyDone("b", True)]


def test_worker_stop_joins():
    w = FocusWorker(FakeFocus, lambda r: None)
    w.stop(timeout=0.5)
    assert not w._thread.is_alive()


def test_worker_title_job_reports_title_and_reports_none_on_failure():
    def submit(w):
        w.submit_title(S, "cfg")
        w.submit_title(S2, "cfg")
        w.submit_verify("n", S)

    fake, results, _ = run_worker(submit)
    assert results == [TitleDone(S, "My title"), TitleDone(S2, None), VerifyDone("n", True)]
    assert [c[0] for c in fake.calls] == ["title", "title", "verify"]


def test_worker_title_job_skips_a_stopped_distro():
    def submit(w):
        w.submit_title(S, "cfg", lambda: False)
        w.submit_title(S, "cfg", lambda: True)

    fake, results, _ = run_worker(submit)
    assert results == [TitleDone(S, None), TitleDone(S, "My title")]
    assert [c[0] for c in fake.calls] == ["title"]


def test_custom_title_from_rename_wins_over_ai_title():
    ai = '{"type":"ai-title","aiTitle":"%s","sessionId":"sess-1"}'
    custom = '{"type":"custom-title","customTitle":"%s","sessionId":"sess-1"}'
    lines = [ai % "Old", custom % "deck twin", ai % "Newer ai"]
    assert title_from_tail("\n".join(lines), "sess-1") == "deck twin"
    assert title_from_tail("\n".join([custom % "First", custom % "Second"]), "sess-1") == "Second"
    other = '{"type":"custom-title","customTitle":"Theirs","sessionId":"zzz"}'
    assert title_from_tail("\n".join([ai % "Mine", other]), "sess-1") == "Mine"
