"""QtPane is plain state, so it runs headless with no PySide6."""
import subprocess
import sys

from tokitty import sprites
from tokitty.customize import Customization, effective_palette
from tokitty.qt.pane import QtPane, fit_tag


def _render(pane, **kw):
    pane.render("working", 42, 67, "2h", "Mon", "tag", None, None, False, **kw)


def test_import_has_no_toolkit():
    code = (
        "import sys\n"
        "sys.modules['tkinter'] = None\n"
        "sys.modules['PySide6'] = None\n"
        "import tokitty.qt.pane\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_state_change_calls_on_change():
    calls = []
    pane = QtPane(on_change=lambda: calls.append(1))
    _render(pane)
    pane.render_usage("working", object())
    pane.set_appearance(label="x")
    pane.draw_next_frame()
    assert len(calls) == 4


def test_render_records_state_and_status_priority():
    pane = QtPane()
    pane.render("working", 42, 67, "2h", "Mon", "drv", "credits", "hint", True,
                stale_text="stale", tool_label="Bash", accent=True, projection_text="proj")
    assert (pane._current_state, pane._driving_tag, pane._tool_label) == ("working", "drv", "Bash")
    assert pane._accent and pane._dimmed and pane._view_mode == "limits"
    assert (pane.session_pct, pane.weekly_pct) == (42, 67)
    assert pane.status_text == "hint"


def test_accent_callback_fires_on_render():
    seen = []
    pane = QtPane()
    pane.on_accent_changed = lambda: seen.append(pane._accent)
    _render(pane, accent=True)
    assert seen == [True]


def test_set_appearance_none_leaves_slots_unchanged():
    pane = QtPane(Customization(overrides={"card_bg": "#112233", "bar_fill": "#445566"}),
                  label="cat")
    palette = pane.palette
    pane.set_appearance()
    assert pane.palette == palette and pane._label == "cat"
    assert pane.customization.overrides == {"card_bg": "#112233", "bar_fill": "#445566"}


def test_set_appearance_updates_the_customization():
    pane = QtPane()
    pane.set_appearance(card_bg="#112233", bar_fill="#445566", label="Ada",
                        colorway="gray", pattern="tuxedo")
    c = pane.customization
    assert c.overrides == {"card_bg": "#112233", "bar_fill": "#445566"}
    assert (c.label, c.colorway, c.pattern) == ("Ada", "gray", "tuxedo")
    assert (pane._label, pane._colorway, pane._pattern) == ("Ada", "gray", "tuxedo")
    assert pane.palette == effective_palette(c)
    pane.set_appearance(palette={"o": "#000000"}, bar_fill="")
    assert pane.palette == {"o": "#000000"}
    assert "bar_fill" not in pane.customization.overrides


def test_tk_default_card_bg_clears_the_override():
    pane = QtPane(Customization(overrides={"card_bg": "#112233"}))
    pane.set_appearance(card_bg="#1c1c22")
    assert "card_bg" not in pane.customization.overrides


def test_draw_next_frame_steps_through_every_frame_and_wraps():
    pane = QtPane()
    state = next(s for s in sprites.ALL_STATES if len(sprites.get_frames(s)) > 1)
    pane.render(state, 42, 67, "2h", "Mon", "tag", None, None, False)
    frames = sprites.get_frames(state)
    shown = []
    for _ in range(len(frames) + 1):
        pane.draw_next_frame()
        shown.append(pane.current_frame()[0])
    # render shows frame 0, so each draw steps to the next one
    assert shown == [frames[(i + 1) % len(frames)] for i in range(len(frames) + 1)]


def test_fit_tag():
    assert fit_tag("Bash") == "Bash"
    assert fit_tag("a" * 20) == "a" * 11 + "…"


def test_set_customization_replaces_the_whole_customization_and_keeps_the_label():
    calls = []
    pane = QtPane(Customization(cat_home="inside_right", overrides={"coat_base": "#112233"}),
                  on_change=lambda: calls.append(1))
    pane.set_appearance(label="Shown")
    custom = Customization(colorway="gray", pattern="solid", cat_home="on_top")
    pane.set_customization(custom)

    assert pane.customization is custom
    assert pane.customization.cat_home == "on_top"
    assert (pane._colorway, pane._pattern) == ("gray", "solid")
    assert pane.palette == effective_palette(custom)  # the old coat override is gone
    assert pane._label == "Shown"                      # custom.label is "", the shown label stays
    assert calls

    pane.set_customization(custom, label="Other")
    assert pane._label == "Other"


# ---- animation schedule (#125) ----------------------------------------------

import random  # noqa: E402

import pytest  # noqa: E402


class Clock:
    def __init__(self, now=1000):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def synth(monkeypatch):
    """Synthetic animations: current art is all 800 ms two-frame loops."""
    def install(name, ms, loop_from=0, template=None):
        template = template or sprites.SITTING_TEMPLATE
        anim = sprites.single_track(
            template,
            [sprites.Frame({"L": "e", "R": "e", "A": c}, ms=m) for c, m in zip("nz!?hB", ms)],
            loop_from,
        )
        monkeypatch.setitem(sprites.ANIMATIONS, name, anim)
        return anim
    return install


def _pane(clock, **kw):
    return QtPane(clock=clock, rng=kw.pop("rng", random.Random(1)), **kw)


def _shown(pane):
    return pane.current_frame()[1]


def test_state_change_restarts_at_frame_zero(synth):
    synth("a", [100, 100, 100])
    synth("b", [800, 800])
    clock = Clock()
    pane = _pane(clock)
    pane.render("a", 0, 0, "", "", "", None, None, False)
    assert pane.tick(clock.now + 100) and _shown(pane) == 1
    clock.now += 150
    pane.render("b", 0, 0, "", "", "", None, None, False)
    assert _shown(pane) == 0
    assert pane.next_due_ms() == clock.now + 800


def test_same_state_rerender_does_not_restart(synth):
    synth("a", [100, 100, 100])
    clock = Clock()
    pane = _pane(clock)
    pane.render("a", 0, 0, "", "", "", None, None, False)
    pane.tick(clock.now + 100)
    due = pane.next_due_ms()
    clock.now += 120
    pane.render("a", 1, 1, "", "", "", None, None, False)
    pane.render_usage("a", object())
    assert _shown(pane) == 1 and pane.next_due_ms() == due


def test_tick_advances_by_durations_exactly(synth):
    synth("a", [100, 250, 40])
    clock = Clock(0)
    pane = _pane(clock)
    pane.render("a", 0, 0, "", "", "", None, None, False)
    assert pane.next_due_ms() == 100
    assert pane.tick(99) is False and _shown(pane) == 0
    assert pane.tick(100) is True and _shown(pane) == 1 and pane.next_due_ms() == 350
    assert pane.tick(349) is False
    assert pane.tick(350) is True and _shown(pane) == 2 and pane.next_due_ms() == 390
    assert pane.tick(390) is True and _shown(pane) == 0 and pane.next_due_ms() == 490


def test_tick_catches_up_over_several_frames(synth):
    synth("a", [100, 100, 100, 100])
    pane = _pane(Clock(0))
    pane.render("a", 0, 0, "", "", "", None, None, False)
    assert pane.tick(250) is True
    assert _shown(pane) == 2 and pane.next_due_ms() == 300


def test_tick_returns_false_when_the_frame_did_not_change(synth):
    synth("a", [100, 100])
    pane = _pane(Clock(0))
    pane.render("a", 0, 0, "", "", "", None, None, False)
    assert pane.tick(50) is False
    # two frames passed: back on frame 0, so no visible change
    assert pane.tick(200) is False and _shown(pane) == 0


def test_ranged_hold_draws_from_the_rng(synth):
    class Fixed(random.Random):
        # Fixed draws, so it doesn't matter how many the pane's starting
        # state takes before the render.
        def randint(self, a, b):
            return a + 137

    synth("a", [(100, 400), 100])
    pane = _pane(Clock(0), rng=Fixed())
    pane.render("a", 0, 0, "", "", "", None, None, False)
    assert pane.next_due_ms() == 240  # 237 rounded to the 10 ms step


def test_loop_from_wraps_to_the_right_index(synth):
    synth("a", [100, 100, 100], loop_from=1)
    pane = _pane(Clock(0))
    pane.render("a", 0, 0, "", "", "", None, None, False)
    seen = []
    for t in (100, 200, 300, 400, 500):
        pane.tick(t)
        seen.append(_shown(pane))
    assert seen == [1, 2, 1, 2, 1]


def test_loop_from_none_holds_the_last_frame(synth):
    synth("a", [100, 100, 100], loop_from=None)
    pane = _pane(Clock(0))
    pane.render("a", 0, 0, "", "", "", None, None, False)
    assert pane.tick(1000) is True
    assert _shown(pane) == 2 and pane.next_due_ms() is None
    assert pane.tick(5000) is False and _shown(pane) == 2


def test_single_frame_animation_has_nothing_scheduled(synth):
    synth("a", [100])
    pane = _pane(Clock(0))
    pane.render("a", 0, 0, "", "", "", None, None, False)
    assert pane.next_due_ms() is None and pane.tick(10_000) is False


def test_long_stall_skips_ahead_instead_of_replaying(synth):
    synth("a", [100, 100, 100])
    pane = _pane(Clock(0))
    pane.render("a", 0, 0, "", "", "", None, None, False)
    stalled = 3_600_000
    pane.tick(stalled)
    assert stalled < pane.next_due_ms() <= stalled + 100
    assert _shown(pane) in (0, 1, 2)


def test_tick_does_not_call_on_change(synth):
    synth("a", [100, 100])
    calls = []
    pane = _pane(Clock(0), on_change=lambda: calls.append(1))
    pane.render("a", 0, 0, "", "", "", None, None, False)
    calls.clear()
    assert pane.tick(100)
    assert calls == []


def test_draw_next_frame_steps_one_frame_and_reschedules(synth):
    synth("a", [100, 100, 100])
    clock = Clock(0)
    pane = _pane(clock)
    pane.render("a", 0, 0, "", "", "", None, None, False)
    clock.now = 30
    pane.draw_next_frame()
    assert _shown(pane) == 1 and pane.next_due_ms() == 130


def _multi(monkeypatch):
    """Track a holds 100 ms, track b 350 ms, on independent clocks."""
    anim = sprites.Animation(sprites.SITTING_TEMPLATE, (
        sprites.Track("a", tuple(sprites.Frame({"A": c}, ms=100) for c in "nz!")),
        sprites.Track("b", tuple(sprites.Frame({"L": c}, ms=350) for c in "ek")),
    ))
    monkeypatch.setitem(sprites.ANIMATIONS, "multi", anim)
    return anim


def test_tracks_run_on_independent_clocks(monkeypatch):
    _multi(monkeypatch)
    pane = _pane(Clock(0))
    pane.render("multi", 0, 0, "", "", "", None, None, False)
    positions = lambda: tuple(pane._player.positions)  # noqa: E731
    assert pane.next_due_ms() == 100 and positions() == (0, 0)
    assert pane.tick(100) and positions() == (1, 0) and pane.next_due_ms() == 200
    assert pane.tick(200) and positions() == (2, 0) and pane.next_due_ms() == 300
    assert pane.tick(300) and positions() == (0, 0) and pane.next_due_ms() == 350
    # track b is due at 350, a again at 400: only the b change is visible at 350
    seen = []
    for t in (350, 400, 450, 500, 600, 700):
        before = pane.current_frame()[0]
        changed = pane.tick(t)
        assert changed == (pane.current_frame()[0] is not before)
        seen.append((t, positions()))
    assert seen == [(350, (0, 1)), (400, (1, 1)), (450, (1, 1)), (500, (2, 1)),
                    (600, (0, 1)), (700, (1, 0))]
    assert pane.next_due_ms() == 800


def test_multi_track_state_change_resets_every_track(monkeypatch):
    _multi(monkeypatch)
    clock = Clock(0)
    pane = _pane(clock)
    pane.render("multi", 0, 0, "", "", "", None, None, False)
    pane.tick(450)
    clock.now = 450
    pane.render("working", 0, 0, "", "", "", None, None, False)
    pane.render("multi", 0, 0, "", "", "", None, None, False)
    assert pane._player.positions == [0, 0] and pane.next_due_ms() == 550


def test_draw_next_frame_walks_the_enumeration(monkeypatch):
    _multi(monkeypatch)
    pane = _pane(Clock(0))
    pane.render("multi", 0, 0, "", "", "", None, None, False)
    frames = sprites.get_frames("multi")
    assert len(frames) == 4
    shown = []
    for _ in range(5):
        pane.draw_next_frame()
        shown.append(pane.current_frame())
    assert [i for _, i in shown] == [1, 2, 3, 0, 1]
    assert all(g is frames[i] for g, i in shown)


def test_current_frame_index_is_none_off_the_enumeration(monkeypatch):
    _multi(monkeypatch)
    pane = _pane(Clock(0))
    pane.render("multi", 0, 0, "", "", "", None, None, False)
    pane.tick(100)
    pane.tick(350)  # a at 1 (100, 200, 300 -> a back at 0), then move on
    pane._player.positions = [1, 1]
    grid, index = pane.current_frame()
    assert index is None and grid is sprites.compose("multi", (1, 1))


def _transition_pane(monkeypatch):
    for name in ("a", "b"):
        monkeypatch.setitem(sprites.ANIMATIONS, name, sprites.single_track(
            sprites.SITTING_TEMPLATE, [sprites.Frame({"A": c}, ms=100) for c in "nz"], 0))
    monkeypatch.setitem(sprites.TRANSITIONS, ("a", "b"), sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"L": c}, ms=100) for c in "ek"], None))
    clock = [0]
    return QtPane(clock=lambda: clock[0]), clock


def _draw(pane, state):
    pane.render(state, 0, 0, "", "", "", None, None, False)


def test_transition_plays_on_a_state_change_but_never_on_the_first_render(monkeypatch):
    pane, clock = _transition_pane(monkeypatch)
    # sleeping is the constructor's placeholder: the first render into any
    # state, even one a transition from "sleeping" exists for, plays none
    monkeypatch.setitem(sprites.TRANSITIONS, ("sleeping", "a"), sprites.TRANSITIONS[("a", "b")])
    _draw(pane, "a")
    assert pane.playing == "a" and pane._current_state == "a"
    clock[0] = 50
    _draw(pane, "b")
    assert pane.playing == "a>b" and pane._current_state == "b"
    assert pane.next_due_ms() == 150
    # polling the same state mid-transition leaves it alone
    clock[0] = 120
    _draw(pane, "b")
    assert pane.playing == "a>b" and pane.next_due_ms() == 150
    pane.tick(150)
    pane.tick(250)  # last frame's hold over: the target takes over from 250
    assert pane.playing == "b" and pane.next_due_ms() == 350


def test_state_change_mid_transition_passes_the_logical_state(monkeypatch):
    pane, clock = _transition_pane(monkeypatch)
    _draw(pane, "a")
    _draw(pane, "b")
    assert pane.playing == "a>b"
    _draw(pane, "a")  # no b>a transition: plain a
    assert pane.playing == "a"
    _draw(pane, "b")
    assert pane.playing == "a>b"


def test_draw_next_frame_steps_through_the_playing_animation(monkeypatch):
    pane, _ = _transition_pane(monkeypatch)
    _draw(pane, "a")
    _draw(pane, "b")
    _, index = pane.current_frame()
    assert index == 0
    pane.draw_next_frame()
    assert pane.current_frame()[1] == 1 and pane.playing == "a>b"


def _spot_pane(monkeypatch, spot="inside"):
    for name in ("a", "b"):
        monkeypatch.setitem(sprites.ANIMATIONS, name, sprites.single_track(
            sprites.SITTING_TEMPLATE, [sprites.Frame({"A": c}, ms=100) for c in "nz"], 0))
    anim = sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({"L": "e", "R": "e"}, ms=100), sprites.Frame({"A": "z"}, ms=100)], 0)
    monkeypatch.setitem(sprites.SPOT_VARIANTS, ("a", "on_top"), anim)
    monkeypatch.setitem(sprites.SPOT_VARIANTS, ("b", "on_top"), anim)
    monkeypatch.setitem(sprites.TRANSITIONS, ("a@on_top", "b@on_top"), sprites.single_track(
        sprites.SITTING_TEMPLATE, [sprites.Frame({"L": c}, ms=100) for c in "ek"], None))
    clock = [0]
    return QtPane(clock=lambda: clock[0], spot=spot), clock


def test_the_pane_plays_the_variant_of_its_spot(monkeypatch):
    pane, _ = _spot_pane(monkeypatch, "on_top")
    assert pane.spot == "on_top"
    _draw(pane, "a")
    assert pane.playing == pane.target == "a@on_top" and pane._current_state == "a"
    _draw(pane, "working")  # no variant: the plain state
    assert pane.playing == pane.target == "working"
    inside, _ = _spot_pane(monkeypatch, "inside")
    _draw(inside, "a")
    assert inside.playing == "a"


def test_transitions_are_looked_up_by_resolved_names(monkeypatch):
    pane, clock = _spot_pane(monkeypatch, "on_top")
    _draw(pane, "a")
    clock[0] = 50
    _draw(pane, "b")
    assert pane.playing == "a@on_top>b@on_top" and pane.target == "b@on_top"
    assert pane._current_state == "b"
    # the same two states inside have no transition between them
    inside, _ = _spot_pane(monkeypatch, "inside")
    _draw(inside, "a")
    _draw(inside, "b")
    assert inside.playing == "b"


def test_a_spot_change_restarts_on_the_new_variant_without_a_transition(monkeypatch):
    calls = []
    pane, clock = _spot_pane(monkeypatch, "inside")
    pane.on_change = lambda: calls.append(1)
    _draw(pane, "a")
    calls.clear()
    clock[0] = 70
    pane.set_spot("on_top")
    assert pane.playing == "a@on_top" and pane.target == "a@on_top"
    assert pane.next_due_ms() == 170 and calls == [1]
    pane.set_spot("on_top")  # same spot: nothing happens
    assert calls == [1]
    pane.set_spot("inside")
    assert pane.playing == "a" and pane._current_state == "a"


def test_a_spot_change_leaves_a_state_without_a_variant_playing(monkeypatch):
    pane, clock = _spot_pane(monkeypatch, "inside")
    _draw(pane, "working")
    due = pane.next_due_ms()
    clock[0] = 30
    pane.set_spot("on_top")
    assert pane.playing == "working" and pane.next_due_ms() == due


def test_rest_grid_and_offset_follow_the_target(monkeypatch):
    anim = sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({}, ms=100), sprites.Frame({}, ms=100, offset=(1, -2))], None)
    monkeypatch.setitem(sprites.ANIMATIONS, "hop", anim)
    pane = QtPane(clock=lambda: 0)
    _draw(pane, "hop")
    assert pane.offset() == (0, 0) and pane.rest_grid() is sprites.get_frames("hop")[0]
    pane.tick(100)
    assert pane.offset() == (1, -2) and pane.rest_grid() is sprites.get_frames("hop")[0]


# ---- wandering (#22) --------------------------------------------------------

def _content(pane, state="content"):
    pane.render(state, 0, 0, "", "", "", None, None, False)


def _walk_until(pane, clock, stop, limit=400):
    """Tick at every due time until stop(pane) holds."""
    for _ in range(limit):
        if stop(pane):
            return True
        clock.now = pane.next_due_ms()
        pane.tick(clock.now)
    return stop(pane)


def _moving(pane):
    return pane.playing.startswith(sprites.MOVE_PREFIX)


def test_a_content_pane_leaves_home_after_its_wait():
    clock = Clock(0)
    pane = _pane(clock, roams=True)
    _content(pane)
    home = pane.pose
    assert pane.spot == "inside"
    assert not _moving(pane)
    assert _walk_until(pane, clock, _moving)
    assert _walk_until(pane, clock, lambda p: p.pose.spot == "on_top")
    assert pane.pose != home and pane.spot == "inside"


def test_a_repeated_poll_does_not_restart_a_move():
    clock = Clock(0)
    pane = _pane(clock, roams=True)
    _content(pane)
    assert _walk_until(pane, clock, _moving)
    playing, due = pane.playing, pane.next_due_ms()
    clock.now += 10
    _content(pane)
    assert pane.playing == playing and pane.next_due_ms() == due


def test_working_while_away_puts_the_cat_home_at_once():
    clock = Clock(0)
    pane = _pane(clock, roams=True)
    _content(pane)
    assert _walk_until(pane, clock, lambda p: p.pose.spot == "on_top")
    pane.render("working", 0, 0, "", "", "", None, None, False)
    assert pane.pose == pane.mask_pose == pane._roam.wanderer.home
    assert not pane.playing.startswith(sprites.MOVE_PREFIX)


def test_the_mask_pose_stays_home_while_a_move_plays_and_follows_a_rest():
    clock = Clock(0)
    pane = _pane(clock, roams=True)
    _content(pane)
    home = pane.pose
    assert _walk_until(pane, clock, _moving)
    assert pane.mask_pose == home
    assert _walk_until(pane, clock, lambda p: p.pose.x != home.x and p.mask_pose == p.pose)


def test_a_pane_that_does_not_roam_never_moves():
    clock = Clock(0)
    pane = _pane(clock)
    _content(pane)
    assert pane.next_due_ms() is not None  # the idle blinks
    for _ in range(300):
        clock.now = pane.next_due_ms()
        pane.tick(clock.now)
        assert pane.pose == pane.mask_pose == pane._roam.wanderer.home
        assert not pane.playing.startswith(sprites.MOVE_PREFIX)


def test_set_spot_with_roams_sends_the_new_home_wandering():
    clock = Clock(0)
    pane = _pane(clock)
    _content(pane)
    pane.set_spot("inside", True)
    assert _walk_until(pane, clock, lambda p: p.pose.spot == "on_top")
    pane.set_spot("on_top")  # roams stays on
    assert pane.pose.spot == "on_top" and pane.spot == "on_top"
