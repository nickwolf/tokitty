import random

from tokitty.randomize import random_look


def test_returns_one_of_each_set():
    cws, pats = ["orange", "gray"], ["solid", "tabby"]
    cw, pat = random_look(cws, pats)
    assert cw in cws and pat in pats


def test_deterministic_with_injected_rng():
    cws, pats = ["orange", "gray", "black"], ["solid", "tabby", "calico"]
    a = random_look(cws, pats, rng=random.Random(1))
    b = random_look(cws, pats, rng=random.Random(1))
    assert a == b


def test_never_returns_outside_sets():
    cws, pats = ["orange"], ["solid"]
    for _ in range(20):
        assert random_look(cws, pats) == ("orange", "solid")


def test_random_tail_weighting():
    from collections import Counter
    from tokitty import sprites
    from tokitty.randomize import random_tail
    rng = random.Random(7)
    counts = Counter(random_tail(rng) for _ in range(20000))
    ringed = [n for n in sprites.TAIL_STYLES if n != "plain"]
    assert set(counts) == set(sprites.TAIL_STYLES)
    assert abs(counts["plain"] / 20000 - 0.6) < 0.02
    for name in ringed:
        assert abs(counts[name] / 20000 - 0.4 / len(ringed)) < 0.02


def test_random_tail_deterministic_with_rng():
    from tokitty.randomize import random_tail
    assert [random_tail(random.Random(3)) for _ in range(5)] == [random_tail(random.Random(3)) for _ in range(5)]


def test_initial_customization_rolls_tail_and_can_pick_a_custom_colorway():
    from tokitty import sprites
    from tokitty.app_core import initial_customization
    sprites.set_custom_colorways({"custom-1": dict(sprites.COLORWAYS["orange"])})
    try:
        looks = [initial_customization(None, None, rng=random.Random(i)) for i in range(400)]
    finally:
        sprites.set_custom_colorways({})
    assert any(c.colorway == "custom-1" for c in looks)
    assert any(c.tail != "plain" for c in looks)
    assert any(c.tail == "plain" for c in looks)


def test_random_eyes_blue_on_colorpoint_else_uniform():
    from collections import Counter
    from tokitty import sprites
    from tokitty.randomize import random_eyes
    rng = random.Random(11)
    assert {random_eyes("colorpoint", rng) for _ in range(200)} == {"blue"}
    counts = Counter(random_eyes("tabby", rng) for _ in range(10000))
    assert set(counts) == set(sprites.EYE_COLORS)
    for name in sprites.EYE_COLORS:
        assert abs(counts[name] / 10000 - 1 / len(sprites.EYE_COLORS)) < 0.02


def test_initial_customization_gives_colorpoint_cats_blue_eyes():
    from tokitty.app_core import initial_customization
    looks = [initial_customization(None, None, rng=random.Random(i)) for i in range(300)]
    points = [c for c in looks if c.pattern == "colorpoint"]
    assert points and all(c.eyes == "blue" for c in points)
    assert len({c.eyes for c in looks}) == 5
