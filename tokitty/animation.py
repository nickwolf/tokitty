"""Plays an animation's tracks against a caller-supplied clock.

Pure scheduling with no toolkit: the Qt pane drives one Player from the
widget's timer, and the media scripts drive another through simulated time.
Each track keeps its own position and due time, so a 100 ms blink and a 3 s
breath run side by side; the grid on screen is sprites.compose of every
track's current position.
"""
from __future__ import annotations

import random
from typing import List, Optional, Tuple

from tokitty import sprites
from tokitty.sprites import frame_ms, frame_ms_max


class Player:
    def __init__(self, state: str = "sleeping", rng: Optional[random.Random] = None,
                 now_ms: int = 0):
        self.rng = rng if rng is not None else random.Random()
        self.state = state
        self.positions: List[int] = []
        self.dues: List[Optional[int]] = []
        self.restart(state, now_ms)

    def restart(self, state: str, now_ms: int) -> None:
        """Every track back to frame 0, shown now."""
        self.state = state
        self.positions = [0] * len(sprites.get_tracks(state))
        self._schedule(now_ms)

    def _schedule(self, start_ms: int) -> None:
        """Each track's due time from the moment its current frame went up. A
        one-frame track has nothing to advance to."""
        self.dues = [
            start_ms + frame_ms(track.frames[pos], self.rng) if len(track.frames) > 1 else None
            for track, pos in zip(sprites.get_tracks(self.state), self.positions)
        ]

    def grid(self) -> List[str]:
        return sprites.compose(self.state, self.positions)

    def next_due(self) -> Optional[int]:
        dues = [d for d in self.dues if d is not None]
        return min(dues) if dues else None

    def tick(self, now_ms: int) -> bool:
        """Advance every track whose time has passed. True iff the composed
        grid changed."""
        before = self.grid()
        for i, track in enumerate(sprites.get_tracks(self.state)):
            due = self.dues[i]
            if due is None or now_ms < due:
                continue
            n, pos = len(track.frames), self.positions[i]
            # A long stall (suspend, a blocked event loop) must not replay
            # every missed frame: past one full cycle, restart the hold from
            # now. (A play-once track ends within one pass, so it needs no
            # skipping.)
            cycle = sum(frame_ms_max(f) for f in track.frames)
            if track.loop_from is not None and now_ms - due > cycle:
                due = now_ms
            while due is not None and now_ms >= due:
                nxt = pos + 1
                if nxt >= n:
                    if track.loop_from is None:
                        due = None  # played once; the last frame stays up
                        break
                    nxt = track.loop_from
                pos = nxt
                due += frame_ms(track.frames[pos], self.rng)
            self.positions[i], self.dues[i] = pos, due
        return self.grid() is not before

    def enum_index(self) -> Optional[int]:
        """Index of the current positions in sprites.get_frames(state), or
        None when the tracks are in a combination that list does not hold."""
        try:
            return sprites.enumeration_positions(self.state).index(tuple(self.positions))
        except ValueError:
            return None

    def step(self, now_ms: int) -> None:
        """Move to the next get_frames() entry (wrapping), ignoring the
        schedule. Deterministic stepping for the pixel checks."""
        positions = sprites.enumeration_positions(self.state)
        index = self.enum_index()
        self.positions = list(positions[0 if index is None else (index + 1) % len(positions)])
        self._schedule(now_ms)


def simulate(state: str, seconds: float, rng: random.Random) -> List[Tuple[List[str], int]]:
    """Play a state for `seconds` of simulated time: (grid, hold ms) for every
    change of the composed grid, consecutive equal grids merged. The holds add
    up to the whole duration (a play-once state ends on its last grid)."""
    end = int(seconds * 1000)
    player = Player(state, rng)
    out: List[Tuple[List[str], int]] = []
    start, shown = 0, player.grid()
    while True:
        due = player.next_due()
        if due is None or due >= end:
            out.append((shown, end - start))
            return out
        if player.tick(due):
            out.append((shown, due - start))
            start, shown = due, player.grid()
