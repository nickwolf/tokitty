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
