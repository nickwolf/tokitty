from tokitty.streamdock.pending import PendingRequest
from tokitty.streamdock.window import (
    InWindowViews, always_label, explain_text, format_tool_input, nonces_to_close, title_text,
)


def req(**kw):
    base = dict(nonce="n", session_id="s", tool_use_id="t", tool_name="Bash", tool_input={"command": "ls"},
                digest="d", preview="ls", started=1.0)
    base.update(kw)
    return PendingRequest(**base)


def test_format_tool_input_is_complete_and_pretty():
    long = "x" * 5000
    text = format_tool_input({"command": long, "n": 1})
    assert long in text and text.startswith("{\n  ")


def test_format_tool_input_keeps_non_ascii_and_survives_odd_values():
    assert "é" in format_tool_input({"a": "é"})
    assert "object" in format_tool_input({"a": object()})


def test_labels():
    assert title_text("work", req()) == "Tokitty: work permission request"
    assert always_label(req()) is None
    assert always_label(req(always_rule="Bash(ls)")) == "Always: Bash(ls)"


def test_nonces_to_close():
    assert nonces_to_close({"a", "b"}, {"b", "c"}) == {"a"}
    assert nonces_to_close([], {"x"}) == set()


def test_explain_text_names_the_reason_and_the_ask():
    text = explain_text("ambiguous", "deck twin", req())
    assert '("deck twin")' in text and "same title" in text
    assert "Claude Code wants to use Bash" in text and "answer in the terminal" in text
    assert "tab in Windows Terminal" in explain_text("not_found", None, req())
    assert "too few free keys" in explain_text("few_keys", None, req())
    assert "couldn't show this request" in explain_text("something new", None, req())


class FakeRuntime:
    def __init__(self, pending=()):
        self.calls = []
        self.pending = set(pending)

    def session_title(self, request):
        return "my session"

    def pending_nonces(self):
        return set(self.pending)

    def in_window_opened(self, nonce):
        self.calls.append(("opened", nonce))

    def in_window_closed(self, nonce):
        self.calls.append(("closed", nonce))

    def decide_from_window(self, nonce, behavior):
        self.calls.append(("decide", nonce, behavior))


class FakeView:
    def __init__(self, request, title, explain, anchor, on_answer, on_close, fail=False):
        self.request, self.title, self.explain, self.anchor = request, title, explain, anchor
        self.on_answer, self.on_close = on_answer, on_close
        self.closed = 0
        self.fail = fail

    def close(self):
        self.closed += 1
        if self.fail:
            raise RuntimeError("boom")


def make_views(runtime, fail=False):
    views = []

    def factory(*args):
        view = FakeView(*args, fail=fail)
        views.append(view)
        return view

    return InWindowViews(lambda: runtime, lambda i: f"acct{i}", lambda i: (10, 20), factory), views


def test_open_builds_one_view_with_title_explain_anchor():
    rt = FakeRuntime()
    iw, views = make_views(rt)
    r = req(nonce="a")
    iw.open(r, "few_keys")
    iw.open(r, "few_keys")
    assert len(views) == 1
    v = views[0]
    assert v.request is r
    assert v.title == title_text("acct0", r)
    assert v.explain == explain_text("few_keys", "my session", r)
    assert v.anchor == (10, 20)
    assert rt.calls == [("opened", "a")]


def test_open_without_runtime_does_nothing():
    iw, views = make_views(None)
    iw.open(req())
    assert views == []


def test_answer_decides_then_closes():
    rt = FakeRuntime()
    iw, views = make_views(rt)
    iw.open(req(nonce="a"))
    views[0].on_answer("allow")
    assert rt.calls == [("opened", "a"), ("decide", "a", "allow"), ("closed", "a")]
    assert views[0].closed == 1
    views[0].on_answer("deny")
    assert views[0].closed == 1


def test_window_close_button_closes():
    rt = FakeRuntime()
    iw, views = make_views(rt)
    iw.open(req(nonce="a"))
    views[0].on_close()
    assert rt.calls == [("opened", "a"), ("closed", "a")]
    assert views[0].closed == 1


def test_sync_closes_only_nonces_no_longer_pending():
    rt = FakeRuntime(pending={"b"})
    iw, views = make_views(rt)
    iw.open(req(nonce="a"))
    iw.open(req(nonce="b"))
    iw.sync()
    assert [v.closed for v in views] == [1, 0]
    assert ("closed", "a") in rt.calls and ("closed", "b") not in rt.calls


def test_close_all_closes_every_view():
    rt = FakeRuntime()
    iw, views = make_views(rt)
    iw.open(req(nonce="a"))
    iw.open(req(nonce="b"))
    iw.close_all()
    assert [v.closed for v in views] == [1, 1]
    assert [c for c in rt.calls if c[0] == "closed"] == [("closed", "a"), ("closed", "b")]


def test_close_all_survives_a_view_that_raises():
    rt = FakeRuntime()
    iw, views = make_views(rt, fail=True)
    iw.open(req(nonce="a"))
    iw.open(req(nonce="b"))
    iw.close_all()
    assert [v.closed for v in views] == [1, 1]
