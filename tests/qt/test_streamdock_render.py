"""Tests for tokitty.streamdock.render: key images are decodable 64x64 PNGs,
deterministic, memoised, drawn in the packaged font (also on a worker thread
and through a real /plan request), and the accent only touches the border."""

import base64
import http.client
import json
import queue
import threading

import pytest
from PySide6.QtGui import QFont, QFontInfo, QFontMetricsF, QImage

from tokitty import sprites
from tokitty.streamdock import render
from tokitty.streamdock.model import KeySpec, PlanBox
from tokitty.streamdock.server import DeckServer

pytestmark = pytest.mark.qt

PALETTE = sprites.resolve_palette()
PREFIX = "data:image/png;base64,"


@pytest.fixture(autouse=True)
def _font(qapp):
    render.register_font()


def decode(data_url):
    assert data_url.startswith(PREFIX)
    data = base64.b64decode(data_url[len(PREFIX):])
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    img = QImage.fromData(data, "PNG")
    assert not img.isNull()
    return img


def px(img, x, y):
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def all_keys():
    return [
        render.session_key("working", 0, PALETTE, "tokitty", False),
        render.usage_key(42, 91, True),
        render.decision_key("allow"),
        render.decision_key("deny"),
        render.decision_key("always"),
        render.decision_key("sent"),
        render.decision_key("cancel"),
        render.status_key("+3"),
        render.status_key("no comtypes"),
        render.status_key(""),
        *render.preview_keys("git push origin main --force-with-lease", 2),
    ]


def test_every_key_is_a_64x64_opaque_png():
    for url in all_keys():
        img = decode(url)
        assert (img.width(), img.height()) == (64, 64)
        assert not img.hasAlphaChannel() or all(img.pixelColor(x, y).alpha() == 255 for x in (0, 63) for y in (0, 63))


@pytest.mark.parametrize("state", sprites.ALL_STATES)
def test_session_key_renders_every_sprite_state(state):
    assert decode(render.session_key(state, 0, PALETTE, "x", False)).width() == 64


def test_output_is_deterministic_across_equal_inputs():
    # A fresh dict with equal contents must give the same image.
    a = render.session_key("working", 1, PALETTE, "tokitty", True)
    b = render.session_key("working", 1, dict(PALETTE), "tokitty", True)
    assert a == b
    render._session_key.cache_clear()
    assert render.session_key("working", 1, PALETTE, "tokitty", True) == a


def test_memoisation_returns_the_identical_string():
    a = render.usage_key(10, 20, False)
    assert render.usage_key(10, 20, False) is a
    assert render.decision_key("allow") is render.decision_key("allow")
    s = render.session_key("content", 0, PALETTE, "t", False)
    assert render.session_key("content", 0, PALETTE, "t", False) is s


def test_caches_are_bounded():
    assert render._session_key.cache_info().maxsize is not None
    assert render._usage_key.cache_info().maxsize is not None


def test_frame_index_wraps():
    n = len(sprites.get_frames("working"))
    assert render.session_key("working", 0, PALETTE, "t") == render.session_key("working", n, PALETTE, "t")
    assert render.session_key("working", 0, PALETTE, "t") != render.session_key("working", 1, PALETTE, "t")


def test_accent_changes_border_pixels_only():
    plain = decode(render.session_key("working", 0, PALETTE, "tokitty", False))
    accent = decode(render.session_key("working", 0, PALETTE, "tokitty", True))
    differing = [
        (x, y)
        for y in range(64)
        for x in range(64)
        if px(plain, x, y) != px(accent, x, y)
    ]
    assert differing
    b = render.BORDER
    for x, y in differing:
        assert x < b or y < b or x >= 64 - b or y >= 64 - b
    assert px(accent, 0, 0) == (0xe0, 0xa8, 0x38)


def test_title_changes_pixels_below_the_cat():
    a = decode(render.session_key("content", 0, PALETTE, "one", False))
    b = decode(render.session_key("content", 0, PALETTE, "two", False))
    assert a != b
    assert a.copy(0, 0, 64, 52) == b.copy(0, 0, 64, 52)


def test_usage_bar_colours_follow_display_bar_color():
    img = decode(render.usage_key(10, 90, False))
    # Bar rows sit at y=25 (session) and y=54 (weekly); sample just inside the fill.
    assert px(img, 9, 22) == (0x4c, 0xaf, 0x6b)
    assert px(img, 9, 51) == (0xe0, 0x52, 0x52)


def test_usage_key_clamps_out_of_range():
    assert render.usage_key(-5, 140, False) == render.usage_key(0, 100, False)


def test_decision_keys_are_distinct_and_coloured():
    imgs = {k: decode(render.decision_key(k)) for k in ("allow", "deny", "always", "sent", "cancel")}
    assert px(imgs["allow"], 1, 1) == (0x4c, 0xaf, 0x6b)
    assert px(imgs["deny"], 1, 1) == (0xe0, 0x52, 0x52)
    assert px(imgs["always"], 1, 1) == (0xe0, 0xa8, 0x38)
    assert px(imgs["sent"], 1, 1) == px(imgs["cancel"], 1, 1)
    assert len({bytes(i.constBits()) for i in imgs.values()}) == 5


def test_unknown_decision_kind_is_rejected():
    with pytest.raises(ValueError):
        render.decision_key("maybe")


def test_preview_keys_returns_exactly_n_images():
    assert render.preview_keys("anything", 0) == []
    assert len(render.preview_keys("", 3)) == 3
    assert len(render.preview_keys("x" * 500, 2)) == 2


def test_preview_keys_distinguishes_truncation():
    per_key = render.PREVIEW_COLS * render.PREVIEW_LINES
    fits = render.preview_keys("a" * per_key, 1)[0]
    cut = render.preview_keys("a" * (per_key + 1), 1)[0]
    assert fits != cut


def test_preview_keys_wrap_by_character_across_keys():
    keys = render.preview_keys("abcdefgh" * 8, 2)
    assert keys[0] == render.preview_keys("abcdefgh" * 4, 1)[0]
    assert keys[0] == keys[1]


def test_status_key_handles_long_text():
    assert decode(render.status_key("a-very-long-status-word-indeed")).width() == 64


def test_qt_can_render_text_off_the_gui_thread():
    # Qt 6 dropped QFontDatabase.supportsThreadedFontRendering(); threaded text
    # is expected everywhere, so check it by drawing. The CI qt job runs this on
    # every OS. A worker-drawn title must leave ink in the title strip.
    out = []
    t = threading.Thread(target=lambda: out.append(render.session_key("content", 0, PALETTE, "worker", False)))
    t.start()
    t.join(10)
    assert out
    blank = decode(render.session_key("content", 0, PALETTE, "", False))
    assert decode(out[0]).copy(0, 52, 64, 12) != blank.copy(0, 52, 64, 12)


def test_font_is_registered_and_used():
    family = render.register_font()
    assert render.register_font() == family
    assert family == "Aileron"
    font = render._font(10)
    assert font.family() == family
    assert QFontInfo(font).family() == family
    assert QFontInfo(font).pixelSize() == 10


def test_font_is_fully_hinted_without_subpixel_antialiasing():
    render.register_font()
    font = render._font(10)
    assert font.hintingPreference() == QFont.HintingPreference.PreferFullHinting
    assert font.styleStrategy() & QFont.StyleStrategy.NoSubpixelAntialias


def test_the_ellipsis_the_preview_uses_has_a_glyph():
    # Without a glyph the font engine would fall back to another font for it.
    from PySide6.QtGui import QRawFont
    raw = QRawFont.fromFont(render._font(10))
    assert raw.supportsCharacter(ord("\u2026"))


def test_rendering_without_the_font_raises(monkeypatch):
    monkeypatch.setattr(render, "_family", None)
    with pytest.raises(RuntimeError, match="register_font"):
        render.status_key("never drawn with a fallback")
    with pytest.raises(RuntimeError, match="register_font"):
        render.usage_key(1, 2)


def test_register_font_reports_a_missing_file(monkeypatch):
    monkeypatch.setattr(render, "_family", None)
    monkeypatch.setattr(render, "FONT_FILE", "missing.ttf")
    with pytest.raises(RuntimeError, match="could not read"):
        render.register_font()


def test_many_threads_draw_different_keys_at_once():
    # The deck server answers requests on several threads, each drawing
    # uncached keys at the same time.
    titles = [f"thread {i}" for i in range(16)]
    expected = {t: render.session_key("content", 0, PALETTE, t, False) for t in titles}
    for fn in (render._session_key, render._text_lines):
        fn.cache_clear()
    start = threading.Barrier(len(titles))
    got = {}

    def draw(title):
        start.wait(10)
        got[title] = render.session_key("content", 0, PALETTE, title, False)

    threads = [threading.Thread(target=draw, args=(t,)) for t in titles]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert got == expected


def test_fit_truncates_to_the_width_with_two_dots():
    metrics = QFontMetricsF(render._font(8))
    text = "a long session title that cannot fit"
    fitted = render._fit(text, 8, 40)
    assert fitted.endswith("..") and fitted != text
    assert metrics.horizontalAdvance(fitted) <= 40
    assert render._fit("ok", 8, 40) == "ok"
    assert render._fit("anything", 8, 0) == ".."


def test_worker_thread_render_equals_gui_thread_render():
    def draw():
        return (
            render.session_key("working", 2, PALETTE, "threaded title", True),
            render.usage_key(33, 77, False),
            render.decision_key("always", False),
            render.status_key("no tab"),
            *render.preview_keys("pytest -q tests/qt -k font", 2),
        )

    # Fresh caches so the worker really draws instead of returning main-thread strings.
    expected = draw()
    for fn in (render._session_key, render._usage_key, render.decision_key, render._text_lines, render._preview_keys):
        fn.cache_clear()
    got = []
    t = threading.Thread(target=lambda: got.append(draw()))
    t.start()
    t.join(30)
    assert got and got[0] == expected


def test_a_real_plan_request_renders_on_the_server_thread():
    box = PlanBox()
    seen = []

    def image_fn(spec):
        seen.append(threading.current_thread())
        return render.status_key(spec.text)

    token = "t" * 40
    srv = DeckServer(0, token, box, queue.Queue(), image_fn=image_fn, meta_fn=dict, poll_timeout=0.3)
    srv.start()
    try:
        box.publish(1, {"c1": KeySpec("status", text="hello")})
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        conn.request("GET", f"/v1/plan?rev=0&t={token}")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
    finally:
        srv.stop()
    assert resp.status == 200
    image = body["keys"]["c1"]["image"]
    assert decode(image) == decode(render.status_key("hello"))
    assert seen and seen[0] is not threading.main_thread()
