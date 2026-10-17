"""The cat's idle wandering (#22): now and then a content or interested cat
leaves its spot for the card's top edge, walks along it, rests at a stop or
two and comes back.

Pure scheduling with no toolkit, like animation.Player. A Wanderer decides
where the cat is and which move comes next; a Roam pairs it with the Player
that shows it, starting each move the moment the last one ends.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import List, Optional

from tokitty import sprites, theme
from tokitty.animation import Player

# States that go on trips. Every move starts and ends on their frame 0.
WANDER_STATES = frozenset({"content", "interested"})
# A cat that falls asleep on the top edge lies down and sleeps where it is.
# Any other state sends it straight home.
STAY_STATES = frozenset({"sleeping"})

STRIDE_PX = sprites._WALK_STRIDE * sprites.SCALE
HOME_WAIT_MS = (45_000, 120_000)  # at home between trips
STOP_REST_MS = (20_000, 60_000)   # at each stop along the edge
STOPS = (1, 3)                    # stops per trip before heading home
MIN_LEG = 2                       # strides between one stop and the next
STALL_MS = 5_000                  # later than this, a step starts now instead


@dataclass(frozen=True)
class Pose:
    """Where the art is placed: the spot gives the row (and the state's
    variant there), x the sprite's left edge in card px, mirrored the way it
    faces. A move's offsets apply on top, mirrored with it."""
    spot: str
    x: int
    mirrored: bool


def home_pose(home: str) -> Pose:
    place = theme.placement(home)
    return Pose(home, place.origin[0], place.mirrored)


def stops_from(x: int) -> List[int]:
    """Every x along the top edge a cat starting at x can stop at: whole
    strides away, so the walk back lands exactly where it started."""
    track = theme.top_edge_track()
    first = x - (x - track.x_min) // STRIDE_PX * STRIDE_PX
    return list(range(first, track.x_max + 1, STRIDE_PX))


def _lies(state: str, spot: str) -> bool:
    """Whether the state is drawn lying down in that spot (the loaf)."""
    return sprites.spot_state(state, spot) != state


class Wanderer:
    def __init__(self, home: str, roams: bool, rng: random.Random,
                 state: str = "sleeping"):
        self.rng = rng
        self.home = home_pose(home)
        # Only a cat that can reach the top edge goes anywhere.
        self.roams = roams
        self.state = state
        self.pose = self.home
        self.move: Optional[str] = None   # the move playing; None at rest
        self.lying = _lies(state, home)    # the art on screen is the loaf
        self.walking = False               # between walk_start and walk_stop
        self.stops: List[int] = []         # x of every stop ahead, home's last
        self.at_stop = False               # resting at stops[0]
        self.rest_until: Optional[int] = None
        self.due: Optional[int] = None     # when the rest ends; None waits for a state

    @property
    def away(self) -> bool:
        return self.move is not None or bool(self.stops) or self.pose != self.home

    def animation(self) -> str:
        """What the Player should show: the move, or the state as drawn where
        the cat is."""
        if self.move is not None:
            return sprites.move_name(self.move)
        return sprites.spot_state(self.state, self.pose.spot)

    def set_state(self, state: str, now: int, settle: bool = True) -> None:
        """A new logical state. Outside WANDER_STATES and STAY_STATES the cat
        is home at once. A resting cat whose new state lies down (or sits up)
        where it is plays lie_down (or sit_up) when `settle`; a moving one
        finishes the move first."""
        self.state = state
        if state not in WANDER_STATES | STAY_STATES:
            if self.away:
                self._go_home()
            self.lying = _lies(state, self.pose.spot)
            self.rest_until = self.due = None
            return
        if self.move is not None:
            return
        lies = _lies(state, self.pose.spot)
        if lies != self.lying:
            if settle:
                self._start("lie_down" if lies else "sit_up")
                return
            self.lying = lies
        self._rest(now)

    def advance(self, at: int) -> None:
        """The move playing ended, or the rest ran out, at `at`: take the next step."""
        if self.move is not None:
            self._land()
        else:
            self.rest_until = self.due = None
            if not self.stops:
                self._plan()
            elif self.at_stop:
                self.stops.pop(0)
                self.at_stop = False
        move = self._next_move()
        if move is None:
            self._rest(at)
        else:
            self._start(move)

    # ---- steps -----------------------------------------------------------

    def _plan(self) -> None:
        here, stops = self.home.x, []
        lattice = stops_from(here)
        for _ in range(self.rng.randint(*STOPS)):
            choices = [x for x in lattice if abs(x - here) >= MIN_LEG * STRIDE_PX]
            here = self.rng.choice(choices)
            stops.append(here)
        self.stops = stops + [self.home.x]

    def _next_move(self) -> Optional[str]:
        """The next move from where the cat is, or None to rest there."""
        lies = _lies(self.state, self.pose.spot)
        if self.state in STAY_STATES:
            if self.walking:
                return "walk_stop"
            return None if lies == self.lying else ("lie_down" if lies else "sit_up")
        if not self.stops:
            return None if lies == self.lying else ("lie_down" if lies else "sit_up")
        target = self.stops[0]
        if self.pose.spot != "on_top":
            return "jump_up"
        if self.pose.x != target:
            if self.lying:
                return "sit_up"
            if not self.walking:
                self.pose = replace(self.pose, mirrored=target < self.pose.x)
                return "walk_start"
            return "walk"
        if self.walking:
            return "walk_stop"
        last = len(self.stops) == 1
        if last and not self.lying:
            # back where it set out: face the way it does at home
            self.pose = replace(self.pose, mirrored=self.home.mirrored)
        if last and self.home.spot != "on_top":
            return "sit_up" if self.lying else "jump_down"
        if lies != self.lying:
            return "lie_down" if lies else "sit_up"
        if last:
            self.stops = []
            self.pose = self.home
        else:
            self.at_stop = True
        return None

    def _start(self, move: str) -> None:
        self.move = move
        self.due = None

    def _land(self) -> None:
        move, pose = self.move, self.pose
        if move == "jump_up":
            pose = replace(pose, spot="on_top")
        elif move == "jump_down":
            pose = replace(pose, spot=self.home.spot)
            self.stops = []
        elif move == "walk":
            pose = replace(pose, x=pose.x + (-STRIDE_PX if pose.mirrored else STRIDE_PX))
        self.walking = move in ("walk_start", "walk")
        self.lying = move == "lie_down"
        self.pose = pose
        self.move = None

    def _rest(self, now: int) -> None:
        """Rest where the cat is: asleep until the state changes, at home
        until the next trip, at a stop until it moves on."""
        if self.state not in WANDER_STATES or (not self.away and not self.roams):
            self.rest_until = self.due = None
            return
        if self.rest_until is None:
            span = STOP_REST_MS if self.away else HOME_WAIT_MS
            self.rest_until = now + self.rng.randint(*span)
        self.due = self.rest_until

    def _go_home(self) -> None:
        self.move = None
        self.stops = []
        self.walking = self.at_stop = False
        self.pose = self.home


class Roam:
    """A Player and a Wanderer driven together: the cat a pane shows."""

    def __init__(self, home: str, roams: bool, rng: random.Random, now_ms: int,
                 state: str = "sleeping"):
        self.rng = rng
        self.wanderer = Wanderer(home, roams, rng, state)
        self.player = Player(self.wanderer.animation(), rng, now_ms)

    @property
    def pose(self) -> Pose:
        return self.wanderer.pose

    def set_state(self, state: str, now_ms: int, transition: bool = True) -> None:
        """A poll's state. Re-rendering the same state changes nothing; a
        change plays the transition into it when `transition` (not on the
        first render)."""
        before = self.player.target
        self.wanderer.set_state(state, now_ms, settle=transition)
        name = self.wanderer.animation()
        if name != before:
            self.player.restart(name, now_ms, before if transition else None)

    def set_home(self, home: str, roams: bool, now_ms: int) -> None:
        """A new home spot or wander setting: the cat is at home, as it is drawn there."""
        state = self.wanderer.state
        self.wanderer = Wanderer(home, roams, self.rng, state)
        self.wanderer.set_state(state, now_ms, settle=False)
        if self.wanderer.animation() != self.player.target:
            self.player.restart(self.wanderer.animation(), now_ms)

    def next_due(self) -> Optional[int]:
        dues = [self.player.next_due(), None if self.wanderer.move else self.wanderer.due]
        dues = [d for d in dues if d is not None]
        return min(dues) if dues else None

    def tick(self, now_ms: int) -> bool:
        """Advance the frames, and step on whenever a move ends or a rest runs
        out. True iff the grid, the offset or the pose changed."""
        changed = self.player.tick(now_ms)
        while True:
            w = self.wanderer
            at = self.player.finished_at if w.move is not None else w.due
            if at is None or at > now_ms:
                return changed
            # After a long stall (suspend) carry on from now, not through
            # every trip that would have happened meanwhile.
            at = now_ms if now_ms - at > STALL_MS else at
            w.advance(at)
            self.player.restart(w.animation(), at)
            self.player.tick(now_ms)
            changed = True
