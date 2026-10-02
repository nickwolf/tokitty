"""Tests for tokitty.streamdock.render: key images are decodable 64x64 PNGs,
deterministic, memoised, and the accent only touches the border."""

import base64
import io

import pytest
from PIL import Image

from tokitty import sprites
from tokitty.streamdock import render

PALETTE = sprites.resolve_palette()
PREFIX = "data:image/png;base64,"


def decode(data_url):
    assert data_url.startswith(PREFIX)
    img = Image.open(io.BytesIO(base64.b64decode(data_url[len(PREFIX):])))
    img.load()
    return img


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


def test_every_key_is_a_64x64_rgb_png():
    for url in all_keys():
        img = decode(url)
        assert img.format == "PNG"
        assert img.size == (64, 64)
        assert img.mode == "RGB"


@pytest.mark.parametrize("state", sprites.ALL_STATES)
def test_session_key_renders_every_sprite_state(state):
    assert decode(render.session_key(state, 0, PALETTE, "x", False)).size == (64, 64)


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
        if plain.getpixel((x, y)) != accent.getpixel((x, y))
    ]
    assert differing
    b = render.BORDER
    for x, y in differing:
        assert x < b or y < b or x >= 64 - b or y >= 64 - b
    assert accent.getpixel((0, 0)) == (0xe0, 0xa8, 0x38)


def test_title_changes_pixels_below_the_cat():
    a = decode(render.session_key("content", 0, PALETTE, "one", False))
    b = decode(render.session_key("content", 0, PALETTE, "two", False))
    assert a.tobytes() != b.tobytes()
    assert a.crop((0, 0, 64, 52)).tobytes() == b.crop((0, 0, 64, 52)).tobytes()


def test_usage_bar_colours_follow_display_bar_color():
    img = decode(render.usage_key(10, 90, False))
    # Bar rows sit at y=25 (session) and y=54 (weekly); sample just inside the fill.
    assert img.getpixel((9, 22)) == (0x4c, 0xaf, 0x6b)
    assert img.getpixel((9, 51)) == (0xe0, 0x52, 0x52)


def test_usage_key_clamps_out_of_range():
    assert render.usage_key(-5, 140, False) == render.usage_key(0, 100, False)


def test_decision_keys_are_distinct_and_coloured():
    imgs = {k: decode(render.decision_key(k)) for k in ("allow", "deny", "always", "sent", "cancel")}
    assert imgs["allow"].getpixel((1, 1)) == (0x4c, 0xaf, 0x6b)
    assert imgs["deny"].getpixel((1, 1)) == (0xe0, 0x52, 0x52)
    assert imgs["always"].getpixel((1, 1)) == (0xe0, 0xa8, 0x38)
    assert imgs["sent"].getpixel((1, 1)) == imgs["cancel"].getpixel((1, 1))
    assert len({i.tobytes() for i in imgs.values()}) == 5


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
    assert decode(render.status_key("a-very-long-status-word-indeed")).size == (64, 64)
