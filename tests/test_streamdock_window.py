from tokitty.streamdock.pending import PendingRequest
from tokitty.streamdock.window import always_label, explain_text, format_tool_input, nonces_to_close, title_text


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
