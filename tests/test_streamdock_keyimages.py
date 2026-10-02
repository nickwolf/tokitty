from tokitty import sprites
from tokitty.streamdock import keyimages, render
from tokitty.streamdock.model import KeySpec, SessionRef

REF = SessionRef(0, "s1")


def palette(index):
    return sprites.get_palette()


def img(spec):
    return keyimages.spec_image(spec, palette)


def test_every_kind_is_a_png_data_url():
    specs = [
        KeySpec("slot", ref=REF, state="working", title="api"),
        KeySpec("overflow", ref=REF, state="idle", count=3, alert=True),
        KeySpec("usage", account=0, session_pct=40, weekly_pct=70, warn=True),
        KeySpec("interrupt", armed=True),
        KeySpec("new", text="work"),
        KeySpec("decision", decision="allow", armed=True),
        KeySpec("preview", text="git status --short", index=0, total=2),
        KeySpec("status", text="set role"),
        KeySpec("status"),
    ]
    for spec in specs:
        assert img(spec).startswith("data:image/png;base64,"), spec


def test_slot_uses_the_render_calls():
    spec = KeySpec("slot", ref=REF, state="thinking", title="api", pending=True)
    assert img(spec) == render.session_key("thinking", 0, palette(0), "api", accent=True)


def test_unknown_or_idle_state_draws_the_content_sprite():
    for state in ("idle", "", "mystery"):
        spec = KeySpec("slot", ref=REF, state=state, title="x")
        assert img(spec) == render.session_key("content", 0, palette(0), "x")


def test_no_tab_title_replaces_the_session_title():
    for focus in ("not_found", "ambiguous", "no_title", "unavailable", "not_front"):
        spec = KeySpec("slot", ref=REF, state="idle", title="api", focus=focus)
        assert img(spec) == render.session_key("content", 0, palette(0), "no tab")
    ok = KeySpec("slot", ref=REF, state="idle", title="api", focus="focused")
    assert img(ok) == render.session_key("content", 0, palette(0), "api")


def test_overflow_shows_count_and_alert_accent():
    spec = KeySpec("overflow", ref=REF, state="working", count=4, alert=True)
    assert img(spec) == render.session_key("working", 0, palette(0), "+4", accent=True)
    calm = KeySpec("overflow", ref=REF, state="working", count=4)
    assert img(calm) != img(spec)


def test_interrupt_and_new_and_status_use_status_key():
    assert img(KeySpec("interrupt")) == render.status_key("Esc")
    assert img(KeySpec("new", text="work")) == render.status_key("work")
    assert img(KeySpec("status", text="no usage")) == render.status_key("no usage")


def test_unarmed_decision_is_greyed():
    for kind in ("allow", "always"):
        armed = img(KeySpec("decision", decision=kind, armed=True))
        greyed = img(KeySpec("decision", decision=kind, armed=False))
        assert armed != greyed
        assert greyed == render.decision_key(kind, False)
    # Deny and cancel stay usable whatever the armed flag says.
    assert img(KeySpec("decision", decision="deny", armed=False)) == render.decision_key("deny")


def test_preview_picks_its_own_key():
    spec = KeySpec("preview", text="a" * 40, index=1, total=2)
    assert img(spec) == render.preview_keys("a" * 40, 2)[1]
