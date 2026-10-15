"""(stored value, label) pairs for the three usage choices, toolkit-free so
the menu and the Settings window share one copy."""
from __future__ import annotations

VIEW_MODE_ITEMS = [("limits", "Limits"), ("models", "Per-model")]
USAGE_WINDOW_ITEMS = [("24h", "Last 24 hours"), ("7d", "Last 7 days"), ("month", "This month")]
USAGE_READOUT_ITEMS = [("cost", "Cost"), ("tokens", "Tokens")]
