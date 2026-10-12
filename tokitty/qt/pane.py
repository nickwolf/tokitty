"""State of one pane in the Qt widget: what the last render said, nothing more.

The public surface mirrors ui.Pane (render, render_usage, set_appearance,
draw_next_frame and the attributes __main__ reads) so a PaneUpdate's kwargs
apply to either pane unchanged. There is no painting here and no PySide6
import: every state change calls on_change and the widget repaints.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Dict, Optional

from tokitty.customize import Customization, effective_palette
from tokitty.display import resolve_status_text
from tokitty.sprites import get_frames
from tokitty.theme import CardStyle

# ui.fit_tag's limit: the longest label the table produces is 10 characters.
TOOL_LABEL_MAX = 12


def fit_tag(text: str, limit: int = TOOL_LABEL_MAX) -> str:
    """Keep an overlay tag inside the cat canvas, ellipsis included."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class QtPane:
    def __init__(self, customization: Optional[Customization] = None, label: str = "",
                 on_change: Optional[Callable[[], None]] = None):
        self.customization = customization if customization is not None else Customization()
        # The widget sets this; it repaints on any state change.
        self.on_change = on_change
        # Set by the owner, as on ui.Pane (window alpha is recomputed there).
        self.on_accent_changed = None
        self._palette: Dict[str, str] = effective_palette(self.customization)
        self._label = label or self.customization.label
        self._colorway = self.customization.colorway
        self._pattern = self.customization.pattern
        self._accent = False
        self._dimmed = False
        self._current_state = "sleeping"
        self._driving_tag = ""
        self._tool_label = ""
        self._view_mode = "limits"
        self._frame_index = 0   # the next frame draw_next_frame will show
        self._shown_index = 0   # the frame on screen now
        self.session_pct = 0.0
        self.weekly_pct = 0.0
        self.session_reset_text = ""
        self.weekly_reset_text = ""
        self.status_text = ""
        self.usage_view = None

    @property
    def palette(self) -> Dict[str, str]:
        return self._palette

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def set_appearance(self, palette=None, card_bg=None, bar_fill=None, label=None,
                       colorway=None, pattern=None) -> None:
        """Live re-style. None leaves a slot unchanged, as on ui.Pane."""
        overrides = dict(self.customization.overrides)
        if card_bg is not None:
            # Tk callers pass its default colour to mean "no override"; keep
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
        self.customization = replace(
            self.customization, overrides=overrides, label=self._label,
            colorway=self._colorway, pattern=self._pattern,
        )
        if palette is not None:
            self._palette = palette
        elif colorway is not None or pattern is not None:
            self._palette = effective_palette(self.customization)
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
        self._current_state = state
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
        self._current_state = state
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

    def draw_next_frame(self) -> None:
        frames = get_frames(self._current_state)
        self._shown_index = self._frame_index % len(frames)
        self._frame_index += 1
        self._changed()

    def current_frame(self):
        """The frame grid on screen now, for the state of the last render."""
        frames = get_frames(self._current_state)
        return frames[self._shown_index % len(frames)], self._shown_index % len(frames)
