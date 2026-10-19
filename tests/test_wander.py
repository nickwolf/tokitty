import random

import pytest

from tokitty import sprites, theme
from tokitty.wander import (
    HOME_WAIT_MS, STRIDE_PX, Pose, Roam, Wanderer, home_pose, stops_from,
)


def _drawn(roam):
    """The sprite's top-left in card px, as the widget places it."""
    pose, (dx, dy) = roam.pose, roam.player.offset()
    x = pose.x + (-dx if pose.mirrored else dx) * sprites.SCALE
    return x, theme.placement(pose.spot).origin[1] + dy * sprites.SCALE


def _run(roam, until_ms, now=0, on_step=None):
    """Tick at every due time up to until_ms. Returns every shown step as
    (animation, pose, drawn xy, grid), consecutive repeats merged."""
    shown = [(roam.player.state, roam.pose, _drawn(roam), roam.player.grid())]
    while True:
        due = roam.next_due()
        if due is None or due > until_ms:
            return shown
        now = max(now, due)
        roam.tick(now)
        if on_step is not None:
            on_step(roam, now)
        step = (roam.player.state, roam.pose, _drawn(roam), roam.player.grid())
        if step != shown[-1]:
            shown.append(step)


def _roam(home="inside", state="content", seed=0, roams=True):
    roam = Roam(home, roams, random.Random(seed), 0)
    roam.set_state(state, 0, transition=False)
    return roam


def _moves(shown):
    names = []
    previous = None
    for name, *_ in shown:
        if name.startswith(sprites.MOVE_PREFIX) and name != previous:
            names.append(name[len(sprites.MOVE_PREFIX):])
        previous = name
    return names


def _trip(home="inside", state="content", seed=0):
    roam = _roam(home, state, seed)
    shown = []
    left = False
    now = 0
    shown.append((roam.player.state, roam.pose, _drawn(roam), roam.player.grid()))
    while True:
        now = roam.next_due()
        assert now is not None
        roam.tick(now)
        step = (roam.player.state, roam.pose, _drawn(roam), roam.player.grid())
        if step != shown[-1]:
            shown.append(step)
        if roam.wanderer.away:
            left = True
        elif left:
            return roam, shown, now


def test_stops_are_whole_strides_from_home_along_the_edge():
    track = theme.top_edge_track()
    for home in ("inside", "inside_right", "on_top"):
        x0 = home_pose(home).x
        xs = stops_from(x0)
        assert x0 in xs
        assert all((x - x0) % STRIDE_PX == 0 for x in xs)
        assert xs[0] - STRIDE_PX < track.x_min and xs[-1] + STRIDE_PX > track.x_max
        assert track.x_min <= xs[0] and xs[-1] <= track.x_max


def test_a_cat_that_cannot_roam_stays_home():
    roam = _roam(roams=False)
    assert roam.next_due() is None or roam.wanderer.due is None
    _run(roam, 3_600_000)
    assert roam.pose == home_pose("inside") and not roam.wanderer.away


@pytest.mark.parametrize("home", ["inside", "inside_right"])
def test_an_inside_cat_jumps_up_walks_and_comes_back_down(home):
    roam, shown, _ = _trip(home)
    moves = _moves(shown)
    assert moves[0] == "jump_up" and moves[-1] == "jump_down"
    assert "walk" in moves and "lie_down" in moves  # content loafs at its stops
    assert roam.pose == home_pose(home)
    assert shown[-1][0] == "content"


def test_the_trip_waits_at_home_first():
    roam = _roam()
    due = roam.wanderer.due
    assert HOME_WAIT_MS[0] <= due <= HOME_WAIT_MS[1]
    roam.tick(due - 1)
    assert roam.player.state == "content" and not roam.wanderer.away


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("home", ["inside", "inside_right", "on_top"])
def test_the_cat_never_jumps_between_frames(home, seed):
    # Each shown frame is where the last one left the cat: the moves hand over
    # on the same spot and pose, and a chained walk gains one cell a frame,
    # each stride rebased by _WALK_STRIDE onto the one before (#22 note 3).
    roam, shown, _ = _trip(home, seed=seed)
    track = theme.top_edge_track()
    for (name_a, _, (xa, _), _), (name_b, pose_b, (xb, _), _) in zip(shown, shown[1:]):
        walking = name_a == "move:walk" or name_b == "move:walk"
        step = sprites.SCALE if walking else 0
        assert abs(xb - xa) <= step, (name_a, name_b, xa, xb)
        if pose_b.spot == "on_top":
            assert track.x_min <= pose_b.x <= track.x_max
    # a walk of several strides really covers them
    xs = [x for _, pose, (x, _), _ in shown if pose.spot == "on_top"]
    assert max(xs) - min(xs) >= 2 * STRIDE_PX


def test_moves_join_on_the_frame_they_hand_over():
    # every move ends on the frame the next animation starts on, so nothing
    # cuts mid-trip (mirroring aside: a cat turns round before it walks back).
    # A stride's frames are steps of their own: the stride after, or the stop,
    # carries on from where it put the cat.
    for seed in range(4):
        _check_joins(_trip(seed=seed)[1])


def _check_joins(shown):
    for (name_a, pose_a, xy_a, grid_a), (name_b, pose_b, xy_b, grid_b) in zip(shown, shown[1:]):
        if name_a == name_b or pose_a.mirrored != pose_b.mirrored or name_b == "move:walk":
            continue
        assert xy_a == xy_b, (name_a, name_b)
        # (a rest may be mid-blink when the trip starts; a move's end is fixed)
        if name_a.startswith(sprites.MOVE_PREFIX) and name_a != "move:walk":
            assert grid_a == grid_b, (name_a, name_b)


@pytest.mark.parametrize("seed", range(4))
def test_a_loafing_cat_sits_up_before_it_walks_or_jumps_down(seed):
    # #22 note 2: from the loaf the cat sits up first; it never walks or jumps
    # straight out of it
    roam, shown, _ = _trip(seed=seed)
    lying = False
    for name, pose, _, _ in shown:
        if name == "move:lie_down":
            lying = True
        elif name == "move:sit_up":
            lying = False
        elif name in ("move:walk_start", "move:jump_down"):
            assert not lying, name


def test_an_on_top_cat_walks_the_edge_without_jumping():
    roam, shown, _ = _trip("on_top")
    moves = _moves(shown)
    assert "jump_up" not in moves and "jump_down" not in moves
    assert moves[0] == "sit_up" and moves[-1] == "lie_down"  # out of and back into the loaf
    assert roam.pose == home_pose("on_top") and shown[-1][0] == "content@on_top"


def test_an_alert_cat_on_the_edge_sits_and_never_lies_down():
    # alert@on_top is drawn sitting, so having a variant there is no reason to lie down
    assert sprites.spot_state("alert", "on_top") == "alert@on_top"
    roam = _roam("on_top")
    roam.set_state("alert", 0, transition=False)
    shown = _run(roam, 10 * 60_000)
    assert _moves(shown) == [] and not roam.wanderer.lying
    assert shown[-1][0] == "alert@on_top"
    # calm again, it lies back down into the loaf instead of cutting to it
    roam.set_state("content", 10 * 60_000)
    assert _moves(_run(roam, 11 * 60_000, now=10 * 60_000))[:1] == ["lie_down"]


def test_an_interested_cat_sits_at_its_stops():
    roam, shown, _ = _trip(state="interested", seed=1)
    moves = _moves(shown)
    assert "lie_down" not in moves and "sit_up" not in moves
    assert any(name == "interested" and pose.spot == "on_top" for name, pose, _, _ in shown)


def _away_on_the_edge(home="inside", seed=0, walking=False):
    roam = _roam(home, seed=seed)
    now = 0
    while True:
        now = roam.next_due()
        roam.tick(now)
        w = roam.wanderer
        if w.pose.spot == "on_top" and w.stops and (w.move == "walk" if walking else w.at_stop):
            return roam, now


@pytest.mark.parametrize("state", ["working", "permission", "alert", "panicked"])
def test_any_other_state_sends_the_cat_straight_home(state):
    roam, now = _away_on_the_edge(walking=True)
    roam.set_state(state, now + 10)
    assert roam.pose == home_pose("inside") and not roam.wanderer.away
    assert roam.player.target == state
    assert roam.wanderer.due is None
    # and it stays there while the state holds
    _run(roam, now + 3_600_000, now=now + 10)
    assert roam.pose == home_pose("inside")


def test_a_snap_plays_the_states_own_transition():
    roam, now = _away_on_the_edge(walking=True)
    roam.set_state("panicked", now + 10)
    assert roam.player.state == sprites.transition_name(sprites.ANY_STATE, "panicked")


def test_a_cat_that_falls_asleep_on_the_edge_lies_down_and_sleeps_there():
    roam, now = _away_on_the_edge(walking=True)
    x = roam.pose.x
    roam.set_state("sleeping", now + 10)
    shown = _run(roam, now + 600_000, now=now + 10)
    assert _moves(shown)[-2:] == ["walk_stop", "lie_down"]
    assert roam.player.state == "sleeping@on_top" and roam.wanderer.due is None
    assert roam.pose.spot == "on_top" and abs(roam.pose.x - x) <= STRIDE_PX
    assert roam.next_due() is not None  # only the loaf's own frames
    # it wakes up content and finishes the trip
    t = now + 600_000
    roam.set_state("content", t)
    while roam.wanderer.away:
        t = roam.next_due()
        roam.tick(t)
    assert roam.pose == home_pose("inside")


def test_a_change_of_posture_at_a_stop_plays_the_move_between():
    roam, now = _away_on_the_edge()
    assert roam.player.state == "content@on_top"
    roam.set_state("interested", now + 10)
    assert roam.player.state == "move:sit_up"
    _run(roam, now + 2_000, now=now + 10)
    assert roam.player.state == "interested" and roam.wanderer.at_stop
    roam.set_state("content", now + 2_000)
    assert roam.player.state == "move:lie_down"


def test_polls_of_the_same_state_never_cut_a_move():
    roam, now = _away_on_the_edge(walking=True)
    playing = roam.player.state
    roam.set_state("content", now + 1)
    assert roam.player.state == playing and roam.wanderer.move == "walk"


def test_a_long_stall_does_not_replay_trips():
    roam = _roam()
    roam.tick(10 * 3_600_000)  # suspended for ten hours
    assert roam.next_due() > 10 * 3_600_000 - 1


def test_set_home_puts_the_cat_home():
    roam, now = _away_on_the_edge(walking=True)
    roam.set_home("on_top", True, now + 5)
    assert roam.pose == home_pose("on_top") and roam.player.state == "content@on_top"
    roam.set_home("inside", False, now + 6)
    assert roam.pose == home_pose("inside") and roam.wanderer.due is None


def test_wanderer_starts_at_home_drawn_as_its_state_there():
    w = Wanderer("on_top", True, random.Random(0), "content")
    assert w.pose == Pose("on_top", 8, False) and w.lying
    assert w.animation() == "content@on_top"


# ---- golden: default Drives is today's behaviour (#138) -----------------------

def _golden_sequence(home, roams):
    roam = _roam(home, roams=roams)
    seq = [(0, roam.player.state, roam.pose)]
    while True:
        due = roam.next_due()
        if due is None or due > 4 * 3600_000:
            return roam, seq
        roam.tick(due)
        seq.append((due, roam.player.state, roam.pose))


@pytest.mark.parametrize("home, count, digest, prefix", [
    ("inside", 37170, "8f72afd610cd9182",
     ["jump_up", "walk_start", "walk", "walk_stop", "lie_down", "sit_up"]),
    ("on_top", 36887, "797c5f70598225c5",
     ["sit_up", "walk_start", "walk", "walk_stop", "lie_down", "sit_up"]),
])
def test_default_drives_replay_the_seeded_sequence_exactly(home, count, digest, prefix):
    """Recorded before Drives existed: seed 0, content, four simulated hours."""
    import hashlib
    _, seq = _golden_sequence(home, True)
    assert len(seq) == count
    assert hashlib.sha256(repr(seq).encode()).hexdigest()[:16] == digest
    assert _moves(_run(_roam(home), 4 * 3600_000))[:6] == prefix


def test_a_non_roaming_cat_with_default_drives_has_no_rest():
    roam = _roam(roams=False)
    assert roam.wanderer.due is None
    assert roam.wanderer.rest_until is None


# ---- time of day: nap, stay, trip (#138) ---------------------------------------

from tokitty.behaviour import Drives, drives  # noqa: E402

HOUR = 3600_000


def _driven(home="inside", state="content", seed=0, roams=True, d=Drives()):
    """Drives are in place before the first rest is drawn."""
    roam = Roam(home, roams, random.Random(seed), 0)
    roam.set_drives(d, 0)
    roam.set_state(state, 0, transition=False)
    return roam


def _nap(roam, limit=HOUR):
    """Tick until the cat dozes off; returns the time."""
    while True:
        now = roam.next_due()
        assert now is not None and now < limit
        roam.tick(now)
        if roam.wanderer.nap_until is not None:
            return now


def _count(roam, hours):
    """(trips, naps) over the time: a trip is setting out along the top edge
    (a scratch at home is no trip), a nap a switch to the sleeping art while
    logically content."""
    trips = naps = 0
    was_away = was_napping = False
    until = hours * HOUR
    while True:
        due = roam.next_due()
        if due is None or due > until:
            return trips, naps
        roam.tick(due)
        w = roam.wanderer
        if w.stops and not was_away:
            trips += 1
        was_away = bool(w.stops)
        if w.nap_until is not None and not was_napping:
            naps += 1
        was_napping = w.nap_until is not None


def test_night_makes_fewer_trips_and_more_naps_than_day():
    night = _count(_driven(d=drives(2)), 8)
    day = _count(_driven(d=drives(12)), 8)
    assert night[0] < day[0]
    assert night[1] > day[1]


def test_a_nap_shows_the_sleeping_art_and_ends_in_the_content_art():
    roam = _driven(d=Drives(trip=0.0, nap=1.0))
    w = roam.wanderer
    now = _nap(roam)
    assert w.nap_until is not None and w.state == "content"
    assert roam.player.target == "sleeping"
    assert w.due == w.nap_until
    assert 180_000 <= w.nap_until - now <= 600_000
    roam.tick(w.nap_until)
    assert w.nap_until is None
    assert roam.player.target == "content"


def test_an_alert_ends_a_nap_at_once():
    roam = _driven(d=Drives(trip=0.0, nap=1.0))
    now = _nap(roam)
    assert roam.wanderer.nap_until is not None
    roam.set_state("alert", now + 1_000)
    assert roam.wanderer.nap_until is None
    assert roam.wanderer.animation() == "alert"
    assert roam.player.target == "alert"


def test_the_same_state_during_a_nap_leaves_it_alone():
    roam = _driven(d=Drives(trip=0.0, nap=1.0))
    now = _nap(roam)
    w = roam.wanderer
    until, due = w.nap_until, w.due
    for t in (1_000, 2_000, 3_000):
        roam.set_state("content", now + t)
    assert (w.nap_until, w.due) == (until, due)
    assert roam.player.target == "sleeping"


def test_an_interested_cat_never_naps():
    roam = _driven(state="interested", d=Drives(trip=0.0, nap=1.0))
    assert _count(roam, 4) == (0, 0)   # no trip weight, and no nap for it
    roam = _driven(state="interested", d=Drives(trip=1.0, nap=50.0))
    assert _count(roam, 4)[1] == 0


def test_a_cat_that_cannot_roam_naps_in_place_and_never_leaves():
    roam = _driven(roams=False, d=drives(2))
    assert roam.wanderer.due is not None
    trips, naps = _count(roam, 8)
    assert trips == 0 and naps > 0


def test_a_non_roaming_cat_gets_a_rest_when_a_nap_becomes_possible():
    roam = _driven(roams=False)
    assert roam.wanderer.due is None
    roam.set_drives(drives(2), 1_000)
    assert roam.wanderer.due is not None
    roam.set_drives(Drives(), 2_000)  # a nap already planned is not cut short
    assert roam.wanderer.due is not None


def test_a_nap_on_top_swaps_loaf_for_loaf_and_wakes_as_content():
    roam = _driven(home="on_top", d=Drives(trip=0.0, nap=1.0))
    w = roam.wanderer
    _nap(roam)
    assert roam.player.target == "sleeping@on_top" and w.nap_until is not None
    roam.tick(w.nap_until)
    assert roam.player.target == "content@on_top" and w.nap_until is None


def test_set_drives_mid_rest_keeps_the_current_deadline():
    roam = _driven()
    due = roam.wanderer.due
    roam.set_drives(drives(2), 5_000)
    roam.set_drives(drives(12), 6_000)
    assert roam.wanderer.due == due


def test_set_drives_survives_set_home():
    roam = _driven(d=drives(2))
    roam.set_home("on_top", True, 1_000)
    assert roam.wanderer.drives == drives(2)


def test_pace_scales_the_rest():
    slow = _driven(d=Drives(pace=2.0))
    plain = _driven()
    assert slow.wanderer.due == 2 * plain.wanderer.due


# ---- play: scratching (#124) ---------------------------------------------------

from tokitty.wander import PLAY_MOVES, bat_move  # noqa: E402

PLAY = Drives(trip=0.0, play=1.0)


@pytest.mark.parametrize("home", ["inside", "inside_right", "on_top"])
def test_play_scratches_and_the_cat_ends_where_it_started(home):
    roam = _driven(home, d=PLAY)
    start = _drawn(roam)
    shown = _run(roam, 2 * HOUR)
    played = [m for m in _moves(shown) if m in PLAY_MOVES[home]]
    assert played and set(played) <= set(PLAY_MOVES[home])
    assert roam.pose == home_pose(home)
    assert _drawn(roam) == start


@pytest.mark.parametrize("home", ["inside", "inside_right", "on_top"])
def test_a_cat_plays_every_move_of_its_spot_and_no_other(home):
    played = set(_moves(_run(_driven(home, d=PLAY), 12 * HOUR)))
    assert set(PLAY_MOVES[home]) <= played
    assert played <= set(PLAY_MOVES[home]) | {"sit_up", "lie_down"}


def test_a_loafing_cat_sits_up_to_scratch_and_lies_down_after(monkeypatch):
    monkeypatch.setitem(PLAY_MOVES, "on_top", ("scratch",))
    moves = _moves(_run(_driven("on_top", d=PLAY), HOUR))
    i = moves.index("scratch")
    assert moves[i - 1:i + 2] == ["sit_up", "scratch", "lie_down"]


def test_a_move_that_ends_lying_down_needs_no_lie_down_after(monkeypatch):
    # bat_down_roll rolls home into the loaf: a loafing cat stays down, and an
    # interested one (drawn sitting on the edge) sits up from it
    monkeypatch.setitem(PLAY_MOVES, "on_top", ("bat_down_roll",))
    content = _moves(_run(_driven("on_top", d=PLAY), HOUR))
    assert "bat_down_roll" in content and "lie_down" not in content
    interested = _moves(_run(_driven("on_top", state="interested", d=PLAY), HOUR))
    i = interested.index("bat_down_roll")
    assert interested[i + 1] == "sit_up"


def test_scratch_stats_heads_for_the_stats_on_either_side():
    for home, toward in (("inside", 1), ("inside_right", -1)):
        roam = _driven(home, d=PLAY)
        start = _drawn(roam)[0]
        xs = [xy[0] for name, _, xy, _ in _run(roam, 6 * HOUR) if name == "move:scratch_stats"]
        assert xs and max((x - start) * toward for x in xs) == 3 * sprites.SCALE


def test_a_cat_that_cannot_roam_plays_in_place():
    roam = _driven(roams=False, d=PLAY)
    assert roam.wanderer.due is not None
    shown = _run(roam, 2 * HOUR)
    assert all(pose == home_pose("inside") for _, pose, _, _ in shown)
    assert any(m.startswith("scratch") for m in _moves(shown))


def test_only_a_first_row_cat_plays_the_wand():
    shut = set(_moves(_run(_driven(roams=False, d=PLAY), 12 * HOUR)))
    assert shut and "wand" not in shut
    first = set(_moves(_run(_driven(roams=True, d=PLAY), 12 * HOUR)))
    assert "wand" in first


def test_the_bat_is_weighted_by_spot_and_a_lone_one_draws_nothing():
    for spot in ("inside", "inside_right"):
        rng = random.Random(3)
        state = rng.getstate()
        assert bat_move(spot, rng) == "bat" and rng.getstate() == state
    rng = random.Random(7)
    picks = [bat_move("on_top", rng) for _ in range(2000)]
    assert set(picks) == {"bat_down", "bat_down_roll"}
    assert 0.15 < picks.count("bat_down_roll") / len(picks) < 0.25


def _stop_scratches(play, seeds=range(6)):
    found = 0
    for seed in seeds:
        moves = _moves(_run(_driven(seed=seed, d=Drives(play=play)), 2 * HOUR))
        found += sum(1 for a, b in zip(moves, moves[1:]) if (a, b) == ("walk_stop", "scratch"))
    return found


def test_a_stop_on_a_trip_sometimes_has_a_scratch_but_never_with_no_play():
    assert _stop_scratches(1e-9) > 0
    assert _stop_scratches(0.0) == 0


def test_an_alert_cuts_a_scratch_and_a_nap_drops_one_still_to_come():
    roam = _driven(d=PLAY)
    while not roam.player.state.startswith("move:scratch"):
        roam.tick(roam.next_due())
    roam.set_state("alert", roam.next_due())
    assert roam.wanderer.move is None and roam.pose == home_pose("inside")
    w = _driven(d=PLAY).wanderer
    w.play = "scratch"
    w.set_state("sleeping", 0)
    assert w.play is None


def test_a_lazy_cat_makes_fewer_trips_than_a_balanced_one_at_night():
    lazy = sum(_count(_driven(seed=s, d=drives(2, "lazy")), 8)[0] for s in range(5))
    balanced = sum(_count(_driven(seed=s, d=drives(2, "balanced")), 8)[0] for s in range(5))
    assert lazy < balanced


@pytest.mark.parametrize("home", ["inside", "on_top", "inside_right"])
def test_a_curious_trip_has_two_to_four_stops_on_the_real_track(home):
    seen = set()
    for seed in range(60):
        w = Wanderer(home, True, random.Random(seed), "content")
        w.drives = drives(7, "curious")
        w._plan()
        assert w.stops[-1] == w.home.x
        seen.add(len(w.stops) - 1)
    assert seen == {2, 3, 4}
