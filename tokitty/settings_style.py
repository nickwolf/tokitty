"""Colors, fonts and base size of the Settings window, toolkit-free so the Qt port can share them."""
from __future__ import annotations

BG_COLOR = "#1c1c22"
FG_COLOR = "#f0f0f0"
DIM_COLOR = "#8a8a92"
BAR_BG = "#333340"
ACCENT_BG = "#3a1620"
ACCENT_FG = "#ffb4a8"
RAIL = "#17171c"
SURFACE = "#25252d"
SURFACE_HOVER = "#303039"
BORDER = "#393943"
MUTED = "#aaaab2"
GOOD = "#a9d7bb"
WARN = "#e5c78d"
BAD = "#f3a1a9"
BLUE = "#9fbedd"

FONT_FAMILY = "Segoe UI"
FONT_BODY = (FONT_FAMILY, 9)
FONT_MEDIUM = (FONT_FAMILY, 9, "bold")
FONT_SMALL = (FONT_FAMILY, 8)
FONT_TITLE = (FONT_FAMILY, 17, "bold")
FONT_SECTION = (FONT_FAMILY, 9, "bold")
FONT_MONO = ("Consolas", 8)

BASE_WIDTH, BASE_HEIGHT = 640, 540

_PILL_COLORS = {
    "good": (GOOD, "#24372f"),
    "warn": (WARN, "#393128"),
    "bad": (BAD, "#3c272e"),
    "neutral": (MUTED, "#34343d"),
    "blue": (BLUE, "#293442"),
}
