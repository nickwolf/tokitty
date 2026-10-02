import http.client
import json
import queue
import threading
import time

import pytest

from tokitty.activity import SessionView
from tokitty.streamdock.model import DeckModel, KeySpec, Noop, PlanBox, SessionRef
from tokitty.streamdock.pending import PendingRequest
from tokitty.streamdock.server import DeckEvent, DeckServer, apply_event, parse_event

TOKEN = "t" * 40


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def rig():
    box = PlanBox()
    inbound = queue.Queue()
    clock = Clock()
    srv = DeckServer(
        0,
        TOKEN,
        box,
        inbound,
        image_fn=lambda spec: "img:" + spec.kind,
        meta_fn=lambda: {"accounts": [{"index": 0, "name": "Main"}], "presets": ["work"]},
        monotonic_fn=clock,
        poll_timeout=0.3,
    )
    srv.start()
    yield srv, box, inbound, clock
    srv.stop()


def req(srv, method, path, body=None, host="default", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    hdrs = dict(headers or {})
    if host != "default":
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        for k, v in hdrs.items():
            conn.putheader(k, v)
        conn.endheaders(body)
    else:
        conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    out = (resp.status, data, resp)
    conn.close()
    return out


def event_body(**kw):
    msg = {"event": "keyUp", "context": "c1", "device": "d1",
           "payload": {"coordinates": {"row": 1, "column": 2}, "settings": {"role": "slot"}}}
    msg.update(kw)
    return json.dumps(msg).encode()


def test_403_without_token_wrong_token_and_wrong_host(rig):
    srv, _, inbound, _ = rig
    for path in ("/v1/plan?rev=0", "/v1/plan?rev=0&t=wrong", "/v1/meta", "/v1/meta?t="):
        assert req(srv, "GET", path)[0] == 403
    assert req(srv, "GET", f"/v1/meta?t={TOKEN}", host="evil.example:%d" % srv.port)[0] == 403
    assert req(srv, "GET", f"/v1/meta?t={TOKEN}", host="localhost:%d" % srv.port)[0] == 403
    assert req(srv, "GET", f"/v1/meta?t={TOKEN}", host="127.0.0.1")[0] == 403
    assert req(srv, "POST", "/v1/event", body=event_body())[0] == 403
    assert req(srv, "POST", "/v1/event?t=nope", body=event_body())[0] == 403
    assert req(srv, "POST", f"/v1/event?t={TOKEN}", body=event_body(), host="evil:1")[0] == 403
    assert inbound.empty()


def test_forbidden_does_not_count_as_a_plan_request(rig):
    srv, *_ = rig
    req(srv, "GET", "/v1/plan?rev=0&t=wrong")
    assert not srv.connected()


def test_options_is_405_and_unknown_path_404(rig):
    srv, *_ = rig
    assert req(srv, "OPTIONS", f"/v1/plan?t={TOKEN}")[0] == 405
    assert req(srv, "OPTIONS", "/v1/plan")[0] == 403
    assert req(srv, "GET", f"/v1/nothing?t={TOKEN}")[0] == 404


def test_plan_returns_immediately_on_a_stale_rev(rig):
    srv, box, *_ = rig
    box.publish(3, {"c1": KeySpec("interrupt"), "c2": KeySpec("status", text="x")})
    t0 = time.monotonic()
    status, data, resp = req(srv, "GET", f"/v1/plan?rev=2&t={TOKEN}")
    assert time.monotonic() - t0 < 0.25
    assert status == 200
    assert resp.getheader("Access-Control-Allow-Origin") == "*"
    body = json.loads(data)
    assert body == {"rev": 3, "keys": {"c1": {"image": "img:interrupt", "title": ""},
                                       "c2": {"image": "img:status", "title": ""}}}


def test_bad_or_missing_rev_returns_at_once(rig):
    srv, box, *_ = rig
    box.publish(5, {})
    for q in ("", "&rev=abc"):
        t0 = time.monotonic()
        status, data, _ = req(srv, "GET", f"/v1/plan?t={TOKEN}{q}")
        assert status == 200 and json.loads(data)["rev"] == 5
        assert time.monotonic() - t0 < 0.25


def test_plan_times_out_with_the_same_rev(rig):
    srv, box, *_ = rig
    box.publish(4, {})
    t0 = time.monotonic()
    status, data, _ = req(srv, "GET", f"/v1/plan?rev=4&t={TOKEN}")
    assert status == 200 and json.loads(data)["rev"] == 4
    assert time.monotonic() - t0 >= 0.25


def test_plan_blocks_until_publish(rig):
    srv, box, *_ = rig
    srv._poll_timeout = 5
    box.publish(1, {})
    result = {}

    def poll():
        result["r"] = req(srv, "GET", f"/v1/plan?rev=1&t={TOKEN}")

    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.2)
    assert th.is_alive()
    box.publish(2, {"c": KeySpec("interrupt")})
    th.join(3)
    assert not th.is_alive()
    assert json.loads(result["r"][1])["rev"] == 2


def test_connected_follows_plan_requests_and_the_clock(rig):
    srv, box, _, clock = rig
    assert not srv.connected()
    box.publish(1, {})
    req(srv, "GET", f"/v1/plan?rev=0&t={TOKEN}")
    assert srv.connected()
    clock.now += 39
    assert srv.connected()
    clock.now += 2
    assert not srv.connected()


def test_connected_is_set_when_a_plan_request_arrives(rig):
    srv, box, _, clock = rig
    srv._poll_timeout = 5
    th = threading.Thread(target=lambda: req(srv, "GET", f"/v1/plan?rev=0&t={TOKEN}"))
    th.start()
    time.sleep(0.2)
    assert srv.connected()
    box.publish(1, {})
    th.join(3)


def test_event_enqueues_a_deck_event(rig):
    srv, _, inbound, _ = rig
    status, _, resp = req(srv, "POST", f"/v1/event?t={TOKEN}", body=event_body(),
                          headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert status == 204
    assert resp.getheader("Access-Control-Allow-Origin") == "*"
    assert inbound.get_nowait() == DeckEvent("keyUp", "c1", "d1", 1, 2, {"role": "slot"})


def test_forwarded_kinds_and_dropped_kinds(rig):
    srv, _, inbound, _ = rig
    for kind in ("willAppear", "willDisappear", "didReceiveSettings"):
        assert req(srv, "POST", f"/v1/event?t={TOKEN}", body=event_body(event=kind))[0] == 204
        assert inbound.get_nowait().kind == kind
    for kind in ("keyDown", "titleParametersDidChange"):
        assert req(srv, "POST", f"/v1/event?t={TOKEN}", body=event_body(event=kind))[0] == 204
    assert inbound.empty()


def test_event_without_coordinates_or_settings_still_parses(rig):
    srv, _, inbound, _ = rig
    body = json.dumps({"event": "willDisappear", "context": "c9"}).encode()
    assert req(srv, "POST", f"/v1/event?t={TOKEN}", body=body)[0] == 204
    assert inbound.get_nowait() == DeckEvent("willDisappear", "c9", "", 0, 0, {})


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"event": 1, "context": "c"}',
                                  b'{"event": "keyUp"}', b"\xff\xfe"])
def test_bad_event_bodies_are_400(rig, body):
    srv, _, inbound, _ = rig
    assert req(srv, "POST", f"/v1/event?t={TOKEN}", body=body)[0] == 400
    assert inbound.empty()


def test_oversize_body_is_413(rig):
    srv, _, inbound, _ = rig
    body = b'{"event":"keyUp","context":"c","x":"' + b"a" * (64 * 1024) + b'"}'
    assert req(srv, "POST", f"/v1/event?t={TOKEN}", body=body)[0] == 413
    assert inbound.empty()


def test_missing_and_bad_content_length(rig):
    srv, *_ = rig
    status, *_ = req(srv, "POST", f"/v1/event?t={TOKEN}", host=f"127.0.0.1:{srv.port}")
    assert status == 411
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    conn.putrequest("POST", f"/v1/event?t={TOKEN}")
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    assert conn.getresponse().status == 400
    conn.close()


def test_meta_returns_meta_fn(rig):
    srv, *_ = rig
    status, data, resp = req(srv, "GET", f"/v1/meta?t={TOKEN}")
    assert status == 200
    assert json.loads(data) == {"accounts": [{"index": 0, "name": "Main"}], "presets": ["work"]}
    assert resp.getheader("Access-Control-Allow-Origin") == "*"


def test_every_response_has_content_length(rig):
    srv, *_ = rig
    for method, path in (("GET", "/v1/meta?t=" + TOKEN), ("GET", "/v1/meta"), ("OPTIONS", f"/v1/meta?t={TOKEN}")):
        _, _, resp = req(srv, method, path)
        assert resp.getheader("Content-Length") is not None


def test_stop_releases_the_port_and_start_reports_a_clash(rig):
    srv, box, inbound, _ = rig
    clash = DeckServer(srv.port, TOKEN, box, inbound, image_fn=str, meta_fn=dict)
    with pytest.raises(OSError):
        clash.start()
    srv.stop()
    srv.stop()


def test_parse_event_shapes():
    assert parse_event("x") is None
    ev = parse_event({"event": "willAppear", "context": "c", "device": "d",
                      "payload": {"coordinates": {"row": True, "column": "x"}, "settings": []}})
    assert ev == DeckEvent("willAppear", "c", "d", 0, 0, {})


def _view(sid="s1"):
    return SessionView(session_id=sid, state="idle", tool_label="", first_seen=1.0, last_ts=1.0)


def test_apply_event_drives_the_real_model():
    model = DeckModel()
    appear = DeckEvent("willAppear", "k1", "dev", 0, 1, {"role": "slot"})
    assert apply_event(model, appear) is None
    assert model.render_plan()["k1"].kind == "status"
    # A role change arrives as didReceiveSettings and must update the role.
    assert apply_event(model, DeckEvent("didReceiveSettings", "k1", "dev", 0, 1, {"role": "interrupt"})) is None
    assert model.render_plan()["k1"].kind == "interrupt"
    assert apply_event(model, DeckEvent("didReceiveSettings", "k1", "dev", 0, 1, {"role": "new:work"})) is None
    assert model.render_plan()["k1"] == KeySpec("new", text="work")
    action = apply_event(model, DeckEvent("keyUp", "k1", "dev", 0, 1, {}))
    assert action.__class__.__name__ == "NewSession" and action.preset == "work"
    assert apply_event(model, DeckEvent("willDisappear", "k1")) is None
    assert model.render_plan() == {}
    assert isinstance(apply_event(model, DeckEvent("keyUp", "k1")), Noop)


def test_apply_event_press_on_a_session_slot():
    model = DeckModel()
    model.update({0: [_view()]}, [], {})
    apply_event(model, DeckEvent("willAppear", "k1", "dev", 0, 0, {"role": "slot"}))
    action = apply_event(model, DeckEvent("keyUp", "k1"))
    assert action.session == SessionRef(0, "s1")


def test_settings_event_delivers_the_prompt_setting_to_the_model():
    model = DeckModel()
    model.update({0: [_view()]}, [], {})
    apply_event(model, DeckEvent("willAppear", "k0", "dev", 0, 0, {"role": "slot"}))
    apply_event(model, DeckEvent("willAppear", "k1", "dev", 0, 1, {"role": "blank"}))
    apply_event(model, DeckEvent("willAppear", "k2", "dev", 0, 2, {"role": "blank"}))
    pending = PendingRequest("n1", "s1", "tu", "Bash", {}, "d", "ls", 1.0, account_index=0)
    model.update({0: [_view()]}, [pending], {})
    # The prompt choice arrives with the role in one didReceiveSettings payload.
    apply_event(model, DeckEvent("didReceiveSettings", "k1", "dev", 0, 1, {"role": "blank", "prompt": "allow"}))
    apply_event(model, DeckEvent("didReceiveSettings", "k2", "dev", 0, 2, {"role": "blank", "prompt": "deny"}))
    action = apply_event(model, DeckEvent("keyUp", "k0"))
    assert action.overlay is True
    plan = model.render_plan()
    assert plan["k1"].decision == "allow" and plan["k2"].decision == "deny"
