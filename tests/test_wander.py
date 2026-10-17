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
