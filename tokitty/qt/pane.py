"""State of one pane in the Qt widget: what the last render said, nothing more.

The public surface (render, render_usage, set_appearance, draw_next_frame)
takes a PaneUpdate's kwargs unchanged. There is no painting here and no PySide6
import: every state change calls on_change and the widget repaints.

Each pane also owns an animation Player: which frame each track shows and
when it is next due, on a monotonic millisecond clock the caller can inject.
The widget asks next_due_ms() when to wake and calls tick(); a frame advance
is not a state change, so tick() never calls on_change (the widget repaints
just the cat).
"""
from __future__ import annotations

import random
import time
from dataclasses import replace
from typing import Callable, Dict, Optional, Tuple

from tokitty.customize import Customization, effective_palette
from tokitty.display import resolve_status_text
from tokitty.animation import Player
from tokitty.theme import CardStyle

# ui.fit_tag's limit: the longest label the table produces is 10 characters.
TOOL_LABEL_MAX = 12


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def palette_signature(palette: Dict[str, str]) -> Tuple[Tuple[str, str], ...]:
    """Hashable stand-in for a palette dict in the image cache key."""
    return tuple(sorted(palette.items()))


def fit_tag(text: str, limit: int = TOOL_LABEL_MAX) -> str:
    """Keep an overlay tag inside the cat canvas, ellipsis included."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class QtPane:
    def __init__(self, customization: Optional[Customization] = None, label: str = "",
                 on_change: Optional[Callable[[], None]] = None,
                 clock: Callable[[], int] = monotonic_ms,
                 rng: Optional[random.Random] = None):
        self.customization = customization if customization is not None else Customization()
        # The widget sets this; it repaints on any state change.
        self.on_change = on_change
        # Set by the owner (window alpha is recomputed there).
        self.on_accent_changed = None
        self._palette: Dict[str, str] = effective_palette(self.customization)
        self._key_of: Optional[Dict[str, str]] = None
        self._key: Tuple[Tuple[str, str], ...] = ()
        self._label = label or self.customization.label
        self._colorway = self.customization.colorway
        self._pattern = self.customization.pattern
        self._tail = self.customization.tail
        self._accent = False
        self._dimmed = False
        self._current_state = "sleeping"
        # The constructor's "sleeping" is a placeholder nobody saw, so the
        # first render must not play a transition out of it.
        self._rendered = False
        self._driving_tag = ""
        self._tool_label = ""
        self._view_mode = "limits"
        self._clock = clock
        self._player = Player("sleeping", rng if rng is not None else random.Random(), clock())
        self.session_pct = 0.0
        self.weekly_pct = 0.0
        self.session_reset_text = ""
        self.weekly_reset_text = ""
        self.status_text = ""
        self.usage_view = None

    @property
    def palette(self) -> Dict[str, str]:
        return self._palette

    @property
    def palette_key(self) -> Tuple[Tuple[str, str], ...]:
        """palette_signature of the palette, sorted once per palette object
        instead of on every paint."""
        if self._key_of is not self._palette:
            self._key = palette_signature(self._palette)
            self._key_of = self._palette
        return self._key

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def set_appearance(self, palette=None, card_bg=None, bar_fill=None, label=None,
                       colorway=None, pattern=None, tail=None) -> None:
        """Live re-style. None leaves a slot unchanged, """
        overrides = dict(self.customization.overrides)
        if card_bg is not None:
            # The card's default colour means "no override"; keep
            # that from pinning the card against a themed background.
            if card_bg and card_bg != CardStyle.background:
                overrides["card_bg"] = card_bg
            else:
                overrides.pop("card_bg", None)
        if bar_fill is not None:
            if bar_fill:
                overrides["bar_fill"] = bar_fill
            else:
                overrides.pop("bar_fill", None)
        if label is not None:
            self._label = label
        if colorway is not None:
            self._colorway = colorway
        if pattern is not None:
            self._pattern = pattern
        if tail is not None:
            self._tail = tail
        self.customization = replace(
            self.customization, overrides=overrides, label=self._label,
            colorway=self._colorway, pattern=self._pattern, tail=self._tail,
        )
        if palette is not None:
            self._palette = palette
        elif colorway is not None or pattern is not None or tail is not None:
            self._palette = effective_palette(self.customization)
        self._changed()

    def set_customization(self, custom: Customization, label: Optional[str] = None) -> None:
        """Take a whole new customization (so cat_home survives and a reset
        really clears old overrides) and recompute the palette, colorway and
        pattern from it. The shown label stays what it was unless one is
        passed: launch shows the account's resolved label, which can differ
        from custom.label for an unnamed account."""
        self.customization = custom
        if label is not None:
            self._label = label
        self._colorway = custom.colorway
        self._pattern = custom.pattern
        self._tail = custom.tail
        self._palette = effective_palette(custom)
        self._changed()

    def render_usage(
        self,
        state: str,
        usage_view,
        driving_tag: str = "",
        tool_label: str = "",
        accent: bool = False,
        dimmed: bool = False,
    ) -> None:
        self._set_state(state)
        self._driving_tag = driving_tag
        self._tool_label = tool_label
        self._accent = accent
        self._dimmed = dimmed
        self._view_mode = "models"
        self.usage_view = usage_view
        self._finish()

    def render(
        self,
        state: str,
        session_pct: float,
        weekly_pct: float,
        session_reset_text: str,
        weekly_reset_text: str,
        driving_tag: str,
        credits_text: Optional[str],
        hint_text: Optional[str],
        dimmed: bool,
        stale_text: Optional[str] = None,
        tool_label: str = "",
        accent: bool = False,
        projection_text: Optional[str] = None,
    ) -> None:
        self._set_state(state)
        self._driving_tag = driving_tag
        self._tool_label = tool_label
        self._accent = accent
        self._dimmed = dimmed
        self._view_mode = "limits"
        self.session_pct = session_pct
        self.weekly_pct = weekly_pct
        self.session_reset_text = session_reset_text
        self.weekly_reset_text = weekly_reset_text
        self.status_text = resolve_status_text(hint_text, credits_text, projection_text, stale_text)
        self._finish()

    def _finish(self) -> None:
        self._changed()
        if self.on_accent_changed is not None:
            self.on_accent_changed()

    # ---- animation schedule ----------------------------------------------

    def _set_state(self, state: str) -> None:
        """Polls re-render the same state every second or so; only a real
        change of sprite state may restart the loop."""
        from_state = self._current_state if self._rendered else None
        self._rendered = True
        self._current_state = state
        if state != self._player.target:
            self._player.restart(state, self._clock(), from_state)

    @property
    def playing(self) -> str:
        """The animation on screen: the state, or a transition's name while
        one plays into it (_current_state stays the logical state)."""
        return self._player.state

    def next_due_ms(self) -> Optional[int]:
        """Clock ms when tick() will next change a track's frame, or None."""
        return self._player.next_due()

    def tick(self, now_ms: int) -> bool:
        """Advance every track whose time has passed. True iff the composed
        grid changed. Never calls on_change: the widget repaints only the cat."""
        return self._player.tick(now_ms)

    def draw_next_frame(self) -> None:
        """Step to the next get_frames() entry (wrapping), ignoring the
        schedule. Deterministic stepping for the pixel checks; the due times
        restart from now so the live timer stays coherent."""
        self._player.step(self._clock())
        self._changed()

    def grid(self):
        """The grid on screen now, without the enumeration index
        current_frame() also works out."""
        return self._player.grid()

    def current_frame(self):
        """(grid, index) on screen now. The index is the grid's place in
        sprites.get_frames(state), or None when the tracks are in a
        combination that list does not hold."""
        return self._player.grid(), self._player.enum_index()
