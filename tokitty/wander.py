"""The cat's idle wandering (#22): now and then a content or interested cat
leaves its spot for the card's top edge, walks along it, rests at a stop or
two and comes back.

Pure scheduling with no toolkit, like animation.Player. A Wanderer decides
where the cat is and which move comes next; a Roam pairs it with the Player
that shows it, starting each move the moment the last one ends.

When a rest at home runs out, the cat's Drives (behaviour.py, #138) weigh a
trip against a nap, play or simply resting on. A nap is drawn as the sleeping
art while the logical state stays what it was, so an alert always ends it.
Play is a scratch (#124), as is a stop now and then on a trip.

The cat also answers its own bars (#135). A tick-up of the session bar latches
a bat and a window reset a pounce, played from home once the cat is calm and
idle. An alert cat may walk to the top edge and sit over the fill end of the
bar that drives it, the one exception to the ladder staying honest.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import List, Optional

from tokitty import sprites, theme
from tokitty.behaviour import Drives
from tokitty.animation import Player

# States that go on trips. Every move starts and ends on their frame 0.
WANDER_STATES = frozenset({"content", "interested"})
# A cat that falls asleep on the top edge lies down and sleeps where it is.
# Any other state sends it straight home.
STAY_STATES = frozenset({"sleeping"})
# States that may leave home for a stop over the driving bar's fill end (#135).
WATCH_STATES = frozenset({"alert"})

STRIDE_PX = sprites._WALK_STRIDE * sprites.SCALE
HOME_WAIT_MS = (45_000, 120_000)  # at home between trips
STOP_REST_MS = (20_000, 60_000)   # at each stop along the edge
NAP_MS = (180_000, 600_000)       # a nap at home
MIN_LEG = 2                       # strides between one stop and the next
STALL_MS = 5_000                  # later than this, a step starts now instead
# What play plays, by where the cat is. The rolls and the yarn need the floor
# inside the card, so they are not played on the top edge; knock_off needs
# the top edge to push the glass off; the upright scratches need the stats or
# the card's side beside the cat, so on the top edge only the stretch fits.
_FLOOR_PLAY = ("scratch", "scratch_stats", "scratch_edge", "roll", "tail_catch",
               "tail_catch_roll", "fly", "yarn", "mouse", "wand")
PLAY_MOVES = {"inside": _FLOOR_PLAY,
              "inside_right": _FLOOR_PLAY,
              "on_top": ("scratch", "tail_catch", "fly", "knock_off", "mouse",
                         "wand")}
# Moves only a first-row card can play: the wand's string comes down from
# above the card, which only a first-row pane has clear.
FIRST_ROW_MOVES = frozenset({"wand"})
BAR_EVENT_TTL_MS = 600_000        # a bar event that finds no calm moment is dropped
BAT_COOLDOWN_MS = 120_000         # between the starts of two bats
WATCH_SETTLE_MS = 25_000          # an alert must hold this long before the cat goes to watch
# Where the head sits in alert@on_top, in px from the sprite's left edge when
# not mirrored: the middle of the head's columns, which is the nose's column.
PERCH_HEAD_PX = 54
STOP_SCRATCH = 1 / 3              # chance of a scratch at a stop, while play > 0
# The bat a bar ticking up plays (#135), by where the cat is: on the edge
# it reaches down over it, and one time in five rolls home instead of walking.
BAT_MOVES = {"inside": (("bat", 1),), "inside_right": (("bat", 1),),
             "on_top": (("bat_down", 4), ("bat_down_roll", 1))}


def bat_move(spot: str, rng: random.Random) -> str:
    """The weighted bat for a spot; a lone entry is taken without drawing."""
    options = BAT_MOVES[spot]
    if len(options) == 1:
        return options[0][0]
    pick = rng.random() * sum(w for _, w in options)
    for name, w in options:
        pick -= w
        if pick < 0:
            return name
    return options[-1][0]

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
    return (state, spot) in sprites.LYING_VARIANTS


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
        self.drives = Drives()
        self.nap_until: Optional[int] = None  # dozing at home until then
        self.play: Optional[str] = None    # a move to play once the cat is up for it
        # Bar events (#135): the last seq taken per kind, and each pending
        # event's deadline. A bat is started at most once per BAT_COOLDOWN_MS.
        self.bar_seen = [0, 0]             # moved, reset
        self.moved_until: Optional[int] = None
        self.reset_until: Optional[int] = None
        self.last_bat: Optional[int] = None
        # Watch: the driving bar's fill end in card px, whether the cat is
        # on its way to or at the stop over it (stops = [that stop, home]),
        # and whether this alert episode has had its one draw.
        self.fill_x: Optional[float] = None
        self.watching = False
        self.watch_drawn = False

    def inherit(self, old: "Wanderer") -> None:
        """Take over what outlives a change of home: drives and the bar events."""
        self.drives = old.drives
        self.bar_seen = list(old.bar_seen)
        self.moved_until, self.reset_until = old.moved_until, old.reset_until
        self.last_bat = old.last_bat

    @property
    def away(self) -> bool:
        return self.move is not None or bool(self.stops) or self.pose != self.home

    @property
    def shown(self) -> str:
        """The state the art shows: a napping cat looks asleep."""
        return "sleeping" if self.nap_until is not None else self.state

    def animation(self) -> str:
        """What the Player should show: the move, or the state as drawn where
        the cat is."""
        if self.move is not None:
            return sprites.move_name(self.move)
        return sprites.spot_state(self.shown, self.pose.spot)

    def set_state(self, state: str, now: int, settle: bool = True) -> None:
        """A new logical state. Outside WANDER_STATES and STAY_STATES the cat
        is home at once. A resting cat whose new state lies down (or sits up)
        where it is plays lie_down (or sit_up) when `settle`; a moving one
        finishes the move first. The same calm state again leaves a nap
        alone; any other state ends it."""
        if state != self.state and self.nap_until is not None:
            self.nap_until = self.rest_until = self.due = None
        prev, self.state = self.state, state
        if state not in WANDER_STATES:
            self.play = None
        if prev in WATCH_STATES and state not in WATCH_STATES:
            self.watching = self.watch_drawn = False
            self.rest_until = self.due = None
        if state not in WANDER_STATES | STAY_STATES:
            if state in WATCH_STATES and self._watch_on_trip():
                return
            if self.away:
                self._go_home()
            self.lying = _lies(state, self.pose.spot)
            if state not in WATCH_STATES or prev != state:
                self.rest_until = self.due = None
            self._watch_arm(now)
            return
        if self.move is not None:
            return
        lies = _lies(self.shown, self.pose.spot)
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
            if self.nap_until is not None:
                self.nap_until = None     # awake: sit up, then a fresh rest
            elif self.state in WATCH_STATES:
                self._watch_decide()      # the settle wait at home ran out
            elif not self.stops:
                self._decide(at)
            elif self.at_stop:
                self.stops.pop(0)
                self.at_stop = False
        move = self._next_move()
        if move is None:
            self._rest(at)
        else:
            self._start(move)

    # ---- bar events ------------------------------------------------------

    def set_bar_events(self, events, now: int) -> None:
        """The (moved, reset) counters from the poll. A new count latches that
        event until it is played (Roam tries at every render) or
        BAR_EVENT_TTL_MS passes."""
        moved, reset = events
        if moved != self.bar_seen[0]:
            self.moved_until = now + BAR_EVENT_TTL_MS
        if reset != self.bar_seen[1]:
            self.reset_until = now + BAR_EVENT_TTL_MS
        self.bar_seen = [moved, reset]

    def bar_play(self, now: int) -> None:
        """Play a latched event if the cat is calm, home, idle and not napping.
        A pounce wins over a bat. A taken event is spent whether or not its
        chance passes; a chance of 0 leaves it pending until it expires, and a
        bat still cooling down waits too."""
        if self.moved_until is not None and now >= self.moved_until:
            self.moved_until = None
        if self.reset_until is not None and now >= self.reset_until:
            self.reset_until = None
        if (self.state not in WANDER_STATES or self.away or self.nap_until is not None
                or self.play is not None):
            return
        d = self.drives
        if self.reset_until is not None and d.pounce > 0:
            self.reset_until = None
            if self._chance(d.pounce):
                self._play_now("pounce")
            return
        if (self.moved_until is not None and d.bat > 0
                and (self.last_bat is None or now - self.last_bat >= BAT_COOLDOWN_MS)):
            self.moved_until = None
            if self._chance(d.bat):
                self.last_bat = now
                self._play_now(bat_move(self.home.spot, self.rng))

    def _chance(self, p: float) -> bool:
        """Whether a chance in (0, 1] passes; a certain one draws nothing."""
        return p >= 1.0 or self.rng.random() < p

    def _play_now(self, move: str) -> None:
        """Skip the home rest: the move starts now, after a sit_up if lying."""
        self.play = move
        self.rest_until = self.due = None
        self._start(self._next_move())

    # ---- watching the bar (#135) -----------------------------------------

    def set_watch(self, fill_x: Optional[float], now: int) -> None:
        """Where the driving bar's fill ends, in card px (None: not on show).
        A cat on watch follows it to the nearest stop."""
        self.fill_x = fill_x
        if self.state not in WATCH_STATES:
            return
        if not self.watching:
            if not self.away:
                self._watch_arm(now)
            return
        if fill_x is None:
            return
        target = self._watch_stop()
        if self.stops[0] != target:
            self.stops[0] = target
            if self.move is None:
                self.at_stop = False
                self._kick()

    def _can_watch(self) -> bool:
        return self.roams and self.drives.watch > 0 and self.fill_x is not None

    def _watch_stop(self) -> int:
        """The stop whose sprite has its head over the fill end."""
        head = theme.SPRITE_W - PERCH_HEAD_PX if self.home.mirrored else PERCH_HEAD_PX
        want = self.fill_x - head
        return min(stops_from(self.home.x), key=lambda x: abs(x - want))

    def _watch_arm(self, now: int) -> None:
        """An alert cat at home waits out WATCH_SETTLE_MS before its one draw.
        The wait is kept across renders of the same alert."""
        if self.state not in WATCH_STATES:
            return
        if not self.away and not self.watch_drawn and self._can_watch():
            if self.rest_until is None:
                self.rest_until = now + WATCH_SETTLE_MS
            self.due = self.rest_until
        else:
            self.rest_until = self.due = None

    def _watch_decide(self) -> None:
        """The settle wait ran out: draw once for this alert episode."""
        if self.watch_drawn or not self._can_watch():
            return
        self.watch_drawn = True
        if self._chance(self.drives.watch):
            self._watch_begin()

    def _watch_on_trip(self) -> bool:
        """Alert arrives while the cat is out on the top edge: draw at once and,
        on a pass, carry on to the watch stop once the move playing ends. True
        when the cat is (now) on watch; otherwise it goes home as usual."""
        if self.watching:
            return True
        if self.watch_drawn or not self.stops or self.move == "jump_down" or not self._can_watch():
            return False
        self.watch_drawn = True
        if not self._chance(self.drives.watch):
            return False
        self._watch_begin()
        if self.move is None:
            self._kick()
        return True

    def _watch_begin(self) -> None:
        self.watching = True
        self.play = None
        self.at_stop = False
        self.rest_until = self.due = None
        self.stops = [self._watch_stop(), self.home.x]

    def _kick(self) -> None:
        """Start whatever the cat has to do next, now, instead of resting."""
        move = self._next_move()
        if move is not None:
            self._start(move)

    # ---- steps -----------------------------------------------------------

    def _decide(self, at: int) -> None:
        """A rest at home ran out: set out, doze off, or rest on. A lone
        candidate is taken without drawing, so default Drives replay the
        seeded sequences of before they existed."""
        d = self.drives
        weights = [
            ("trip", d.trip if self.roams else 0.0),
            ("nap", d.nap if self.state == "content" else 0.0),
            ("play", d.play),
            ("stay", d.stay),
        ]
        weights = [(name, w) for name, w in weights if w > 0]
        if not weights:
            return
        if len(weights) == 1:
            choice = weights[0][0]
        else:
            pick = self.rng.random() * sum(w for _, w in weights)
            for choice, w in weights:
                pick -= w
                if pick < 0:
                    break
        if choice == "trip":
            self._plan()
        elif choice == "nap":
            self.nap_until = at + self._span(NAP_MS)
        elif choice == "play":
            moves = [m for m in PLAY_MOVES[self.pose.spot]
                     if self.roams or m not in FIRST_ROW_MOVES]
            self.play = self.rng.choice(moves)

    def _span(self, span) -> int:
        return int(self.rng.randint(*span) * self.drives.pace)

    def _plan(self) -> None:
        here, stops = self.home.x, []
        lattice = stops_from(here)
        for _ in range(self.rng.randint(*self.drives.stops)):
            choices = [x for x in lattice if abs(x - here) >= MIN_LEG * STRIDE_PX]
            here = self.rng.choice(choices)
            stops.append(here)
        self.stops = stops + [self.home.x]

    def _next_move(self) -> Optional[str]:
        """The next move from where the cat is, or None to rest there."""
        lies = _lies(self.shown, self.pose.spot)
        if self.state in STAY_STATES:
            if self.walking:
                return "walk_stop"
            return None if lies == self.lying else ("lie_down" if lies else "sit_up")
        if self.play is not None:
            if self.lying:
                return "sit_up"
            move, self.play = self.play, None
            return move
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
            if (target < self.pose.x) != self.pose.mirrored:
                return "walk_stop"    # the watch stop moved behind it
            return "walk"
        if self.walking:
            return "walk_stop"
        last = len(self.stops) == 1
        if (last or self.watching) and not self.lying:
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
        elif (move == "walk_stop" and len(self.stops) > 1 and not self.watching
              and self.drives.play > 0 and self.rng.random() < STOP_SCRATCH):
            self.play = "scratch"
        self.walking = move in ("walk_start", "walk")
        self.lying = move in sprites.LYING_MOVES
        self.pose = pose
        self.move = None

    def _rest(self, now: int) -> None:
        """Rest where the cat is: asleep until the state changes, at home
        until the next trip, at a stop until it moves on."""
        if self.state not in WANDER_STATES:
            self.rest_until = self.due = None
            return
        if self.nap_until is not None:
            self.rest_until = self.due = self.nap_until
            return
        # A cat that cannot roam rests only if it could doze off or play.
        if not self.away and not self.roams and not self._can_nap() and self.drives.play <= 0:
            self.rest_until = self.due = None
            return
        if self.rest_until is None:
            self.rest_until = now + self._span(STOP_REST_MS if self.away else HOME_WAIT_MS)
        self.due = self.rest_until

    def _can_nap(self) -> bool:
        return self.state == "content" and self.drives.nap > 0

    def _go_home(self) -> None:
        self.move = None
        self.play = None
        self.stops = []
        self.walking = self.at_stop = self.watching = False
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
        self._bar_play(now_ms)

    def _bar_play(self, now_ms: int) -> None:
        """Play a latched bar event, but not over a transition still
        playing into the state: the next render tries again."""
        if not self.player.in_transition:
            self.wanderer.bar_play(now_ms)
            self._resync(now_ms)

    def _resync(self, now_ms: int) -> None:
        """Show what the Wanderer started outside a tick (a move from a bar
        event or a new watch stop), so its end is seen."""
        name = self.wanderer.animation()
        if name != self.player.target:
            self.player.restart(name, now_ms)

    def set_bar_events(self, events, now_ms: int) -> None:
        """The poll's (moved, reset) counters; see Wanderer.set_bar_events."""
        self.wanderer.set_bar_events(events, now_ms)
        self._bar_play(now_ms)

    def note_bars(self, fill_x: Optional[float], events, now_ms: int) -> None:
        """What this poll shows, taken in before its state: the fill end (or
        None) and the (moved, reset) counters, or None to keep them. Nothing
        moves here, so the state change decides with both in hand."""
        self.wanderer.fill_x = fill_x
        if events is not None:
            self.wanderer.set_bar_events(events, now_ms)

    def set_watch(self, fill_x: Optional[float], now_ms: int) -> None:
        """Where the driving bar's fill ends in card px, or None."""
        self.wanderer.set_watch(fill_x, now_ms)
        self._resync(now_ms)

    def set_home(self, home: str, roams: bool, now_ms: int) -> None:
        """A new home spot or wander setting: the cat is at home, as it is drawn there."""
        old = self.wanderer
        self.wanderer = Wanderer(home, roams, self.rng, old.state)
        self.wanderer.inherit(old)
        state = old.state
        self.wanderer.set_state(state, now_ms, settle=False)
        if self.wanderer.animation() != self.player.target:
            self.player.restart(self.wanderer.animation(), now_ms)

    def set_drives(self, drives: Drives, now_ms: int) -> None:
        """New inclinations. Nothing under way is cut short: they matter at
        the next decision. A cat with nothing scheduled that could now rest
        gets a rest, so it is not stuck."""
        w = self.wanderer
        if drives == w.drives:
            return
        w.drives = drives
        if w.due is None and w.move is None:
            w._rest(now_ms)

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
