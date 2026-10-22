"""Turn a KeySpec into the image the deck shows for it.

Kept apart from model.py (which only describes keys) and render.py (which only
draws). The frame index is always 0: animating the cats would change the plan,
and so bump its revision, many times a second.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from tokitty import theme as T
from tokitty.pose import ACTIVITY_SPRITE_PLACEHOLDERS
from tokitty.streamdock import render
from tokitty.streamdock.model import KeySpec

IDLE_SPRITE = "content"
NO_TAB = "no tab"
TERMINAL_ONLY = "terminal"
# Focus statuses that mean the session's terminal tab could not be confirmed.
NO_TAB_STATUSES = frozenset({"not_found", "ambiguous", "no_title", "unavailable", "not_front"})


def _sprite(state: str) -> str:
    return ACTIVITY_SPRITE_PLACEHOLDERS.get(state, IDLE_SPRITE)


def spec_image(spec: KeySpec, palette_fn: Callable[[int], Dict[str, str]],
               bars: Optional[T.BarStyle] = None) -> str:
    """The data URL for `spec`. `palette_fn` maps an account index to a sprite
    palette; `bars` is the theme's bar style, the default when None."""
    kind = spec.kind
    if kind in ("slot", "overflow") and spec.ref is not None:
        palette = palette_fn(spec.ref.account_index)
        if kind == "overflow":
            return render.session_key(_sprite(spec.state), 0, palette, f"+{spec.count}", accent=spec.alert)
        if spec.focus in NO_TAB_STATUSES:
            title = NO_TAB
        elif spec.terminal_only:
            title = TERMINAL_ONLY
        else:
            title = spec.title
        return render.session_key(_sprite(spec.state), 0, palette, title, accent=spec.pending)
    if kind == "usage":
        return render.usage_key(spec.session_pct, spec.weekly_pct, spec.warn, bars or T.BarStyle())
    if kind == "interrupt":
        return render.status_key("Esc")
    if kind == "new":
        return render.status_key(spec.text)
    if kind == "decision":
        return render.decision_key(spec.decision, spec.armed)
    if kind == "preview":
        keys = render.preview_keys(spec.text, max(spec.total, spec.index + 1))
        return keys[spec.index]
    return render.status_key(spec.text)
