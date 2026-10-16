"""The track player and the simulated playback the media scripts use."""
import random

import pytest

from tokitty import sprites
from tokitty.animation import Player, simulate, simulate_frames


@pytest.fixture
def blink(monkeypatch):
    """A 100 ms track and a 350 ms track, plus a ranged one."""
    anim = sprites.Animation(sprites.SITTING_TEMPLATE, (
        sprites.Track("a", tuple(sprites.Frame({"A": c}, ms=100) for c in "nz")),
        sprites.Track("b", tuple(sprites.Frame({"L": c}, ms=m) for c, m in
                                 (("e", 350), ("k", (100, 400))))),
    ))
    monkeypatch.setitem(sprites.ANIMATIONS, "blink", anim)


def test_simulation_is_deterministic_for_a_seed(blink):
    a = simulate("blink", 12, random.Random(3))
    b = simulate("blink", 12, random.Random(3))
    assert a == b
    assert sum(ms for _, ms in a) == 12_000
    assert simulate("blink", 12, random.Random(4)) != a


def test_simulation_merges_consecutive_equal_grids(monkeypatch):
    anim = sprites.Animation(sprites.SITTING_TEMPLATE, (
        sprites.Track("a", (sprites.Frame({"A": "n"}, ms=100), sprites.Frame({"A": "n"}, ms=100))),
    ))
    monkeypatch.setitem(sprites.ANIMATIONS, "same", anim)
    assert simulate("same", 1, random.Random(0)) == [(sprites.get_frames("same")[0], 1000)]


def test_simulation_of_independent_clocks_has_exact_holds(monkeypatch):
    anim = sprites.Animation(sprites.SITTING_TEMPLATE, (
        sprites.Track("a", (sprites.Frame({"A": "n"}, ms=100), sprites.Frame({"A": "z"}, ms=100))),
        sprites.Track("b", (sprites.Frame({"L": "e"}, ms=350), sprites.Frame({"L": "k"}, ms=350))),
    ))
    monkeypatch.setitem(sprites.ANIMATIONS, "ab", anim)
    holds = [ms for _, ms in simulate("ab", 0.7, random.Random(0))]
    # a flips at 100, 200, 300; b at 350; a at 400, 500, 600, b at 700 (end)
    assert holds == [100, 100, 100, 50, 50, 100, 100, 100]


def test_play_once_track_ends_on_its_last_frame(monkeypatch):
    anim = sprites.single_track(sprites.SITTING_TEMPLATE,
                                [sprites.Frame({"A": c}, ms=100) for c in "nzh"], loop_from=None)
    monkeypatch.setitem(sprites.ANIMATIONS, "once", anim)
    out = simulate("once", 5, random.Random(0))
    assert [ms for _, ms in out] == [100, 100, 4800] and out[-1][0] is sprites.get_frames("once")[2]


def test_player_restart_and_step(blink):
    player = Player("blink", random.Random(0), 1000)
    assert player.positions == [0, 0] and player.next_due() == 1100
    player.step(2000)
    assert player.enum_index() == 1 and player.next_due() == 2100


# ---- transitions -----------------------------------------------------------


def _states(monkeypatch, *names):
    """Looping 100 ms states, one frame differing per name."""
    for name in names:
        monkeypatch.setitem(sprites.ANIMATIONS, name, sprites.single_track(
            sprites.SITTING_TEMPLATE,
            [sprites.Frame({"A": c}, ms=100) for c in "nz"], loop_from=0))


@pytest.fixture
def trans(monkeypatch):
    """a>b: one track of 100 + 200 ms (ends at 300) and a one-frame 450 ms
    track (ends at 450); b is a looping 100 ms state."""
    _states(monkeypatch, "a", "b", "c")
    anim = sprites.Animation(sprites.SITTING_TEMPLATE, (
        sprites.Track("x", (sprites.Frame({"L": "e"}, ms=100), sprites.Frame({"L": "k"}, ms=200)),
                      loop_from=None),
        sprites.Track("y", (sprites.Frame({"R": "k"}, ms=450),), loop_from=None),
    ))
    monkeypatch.setitem(sprites.TRANSITIONS, ("a", "b"), anim)
    return "a>b"


def test_transition_plays_on_a_matching_change_only(trans):
    player = Player("a", random.Random(0), 0)
    player.restart("c", 10, "a")
    assert (player.state, player.target, player.in_transition) == ("c", "c", False)
    player.restart("b", 20)
    assert (player.state, player.target) == ("b", "b")
    player.restart("b", 30, "a")
    assert (player.state, player.target, player.in_transition) == ("a>b", "b", True)
    assert player.positions == [0, 0] and player.next_due() == 130


def test_transition_ends_at_the_latest_track_end_then_target_starts_there(trans):
    player = Player("a", random.Random(0), 0)
    player.restart("b", 1000, "a")
    assert player.tick(1100) and player.positions == [1, 0]
    # track x is done at 1300 but y holds its single frame until 1450
    player.tick(1300)
    assert player.state == "a>b" and player.dues == [None, 1450]
    assert player.tick(1449) is False and player.state == "a>b"
    player.tick(1450)
    assert (player.state, player.positions, player.next_due()) == ("b", [0], 1550)
    assert player.tick(1550) and player.positions == [1]


def test_late_tick_starts_the_target_from_the_exact_end(trans):
    player = Player("a", random.Random(0), 0)
    player.restart("b", 1000, "a")
    player.tick(1520)  # 70 ms past the 1450 end: b has run 70 ms of its first 100
    assert (player.state, player.positions, player.next_due()) == ("b", [0], 1550)


def test_stall_across_the_end_skips_the_target_frames_too(trans):
    player = Player("a", random.Random(0), 0)
    player.restart("b", 1000, "a")
    assert player.tick(1000 + 450 + 5_000_000)
    assert player.state == "b" and player.positions[0] in (0, 1)
    assert 5_001_450 <= player.next_due() <= 5_001_550


def test_restart_mid_transition_uses_the_given_from_state(trans):
    player = Player("a", random.Random(0), 0)
    player.restart("b", 0, "a")
    player.tick(100)
    player.restart("c", 150, "b")  # no b>c transition
    assert (player.state, player.target, player.positions) == ("c", "c", [0])
    player.restart("b", 200, "a")
    player.tick(250)
    player.restart("b", 300, "a")  # an explicit restart starts the transition over
    assert player.state == "a>b" and player.positions == [0, 0] and player.next_due() == 400


def test_transition_enumeration_and_stepping(trans):
    player = Player("a", random.Random(0), 0)
    player.restart("b", 0, "a")
    assert player.enum_index() == 0
    player.step(500)
    assert player.state == "a>b" and player.enum_index() == 1


def test_simulate_plays_the_transition_then_the_target(trans):
    out = simulate("b", 1, random.Random(0), from_state="a")
    assert sum(ms for _, ms in out) == 1000
    first = sprites.compose("a>b", [0, 0])
    assert out[0] == (first, 100)
    # 100 (x0), then 350 of the same grid (x1 from 100, y until 450), then b every 100 ms
    assert [ms for _, ms in out[:4]] == [100, 350, 100, 100]
    assert out[2][0] == sprites.get_frames("b")[0]
    # no transition for the pair: just the state
    plain = simulate("b", 1, random.Random(0), from_state="c")
    assert plain == simulate("b", 1, random.Random(0))


@pytest.fixture
def hop(monkeypatch):
    """A play-once move whose frames share one grid and differ only by offset."""
    monkeypatch.setitem(sprites.MOVES, "hop", sprites.single_track(
        sprites.SITTING_TEMPLATE,
        [sprites.Frame({}, ms=100), sprites.Frame({}, ms=100, offset=(0, -3)),
         sprites.Frame({}, ms=100, offset=(2, -3))], loop_from=None))
    return sprites.move_name("hop")


def test_tick_reports_an_offset_only_change(hop):
    player = Player(hop, random.Random(0), 0)
    assert player.offset() == (0, 0)
    grid = player.grid()
    assert player.tick(100) is True and player.grid() is grid and player.offset() == (0, -3)
    assert player.tick(150) is False
    assert player.tick(200) is True and player.offset() == (2, -3)


def test_simulate_frames_splits_on_offset_and_simulate_merges(hop):
    out = simulate_frames(hop, 1, random.Random(0))
    assert [(ms, off) for _, ms, off in out] == [(100, (0, 0)), (100, (0, -3)), (800, (2, -3))]
    assert out[0][0] is out[1][0] is out[2][0]
    assert simulate(hop, 1, random.Random(0)) == [(out[0][0], 1000)]
