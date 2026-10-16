"""The track player and the simulated playback the media scripts use."""
import random

import pytest

from tokitty import sprites
from tokitty.animation import Player, simulate


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
