"""Pure formatting for the per-model pane view: which rows to show, how
wide each bar is, and what the status line says.

Kept free of tkinter like display.py, so the layout rules are unit-tested
without a GUI toolkit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from tokitty.pricing import display_name
from tokitty.usage_scan import (
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    ModelUsage,
    UsageBreakdown,
)

# The pane is 128px and already carries a status line and an account
# label. Three bar rows fit; a fourth does not. So the third row becomes
# "other" when there are more models than slots, rather than silently
# dropping the tail.
ROW_SLOTS = 3

WINDOW_LABELS = {"24h": "24h", "7d": "7d"}


@dataclass(frozen=True)
class UsageRow:
    label: str
    value_text: str
    bar_pct: float


@dataclass(frozen=True)
class UsageView:
    rows: Tuple[UsageRow, ...]
    status_text: str
    # Drives bar_color when a budget is in play, and None when it is not:
    # the red-at-80% ramp means "close to a cap", and share-of-total has
    # no cap to be close to.
    ramp_pct: Optional[float]


def window_label(window: str, window_start: datetime) -> str:
    """"24h" / "7d" / the month's own name, so the line says what it
    covers without spending width on a date range."""
    if window == "month":
        return window_start.astimezone().strftime("%b")
    return WINDOW_LABELS.get(window, window)


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def format_money(amount: float) -> str:
    return f"${amount:,.2f}"


def _value(model: ModelUsage, readout: str) -> float:
    if readout == "tokens":
        return float(model.total_tokens)
    return model.cost_usd or 0.0


def _value_text(model: ModelUsage, readout: str) -> str:
    if readout == "tokens":
        return format_tokens(model.total_tokens)
    if model.cost_usd is None:
        return "--"
    return format_money(model.cost_usd)


def fold(models: Sequence[ModelUsage], readout: str) -> List[Tuple[str, float, str]]:
    """(label, value, value_text) per displayed row, tail folded.

    Folding happens BEFORE any share is computed. Computing shares first
    and folding after would let a combined "other" exceed the largest
    single model and clamp at over 100%.
    """
    ordered = sorted(models, key=lambda m: -_value(m, readout))
    if len(ordered) <= ROW_SLOTS:
        return [(display_name(m.model), _value(m, readout), _value_text(m, readout)) for m in ordered]

    head = ordered[: ROW_SLOTS - 1]
    tail = ordered[ROW_SLOTS - 1 :]
    rows = [(display_name(m.model), _value(m, readout), _value_text(m, readout)) for m in head]

    tail_value = sum(_value(m, readout) for m in tail)
    if readout == "tokens":
        tail_text = format_tokens(sum(m.total_tokens for m in tail))
    elif all(m.cost_usd is None for m in tail):
        tail_text = "--"
    else:
        tail_text = format_money(tail_value)
    rows.append((f"other ({len(tail)})", tail_value, tail_text))
    return rows


def _status_text(
    breakdown: UsageBreakdown, readout: str, budget: Optional[float], total_pct: Optional[float]
) -> str:
    label = window_label(breakdown.window, breakdown.window_start)

    if breakdown.status == STATUS_UNAVAILABLE:
        return "can't read usage"
    if not breakdown.models:
        if breakdown.window == "month":
            return "no usage this month"
        if breakdown.window == "24h":
            return "no usage in the last 24 hours"
        return "no usage in the last 7 days"

    if readout == "tokens":
        body = f"{label} · {format_tokens(breakdown.total_tokens)} tokens"
    elif budget is not None and total_pct is not None:
        body = f"{label} · {format_money(breakdown.total_cost_usd)} of {format_money(budget)} · {total_pct:.0f}%"
    else:
        prefix = ">= " if breakdown.is_partial_cost else ""
        # "at API rates" is dropped first when the line has to shrink: a
        # subscriber's tokens do not cost them this, but the figure itself
        # is never the part that gets cut.
        body = f"{label} · {prefix}{format_money(breakdown.total_cost_usd)} at API rates"

    if breakdown.status == STATUS_PARTIAL:
        return f"{body} · partial"
    return body


def build_view(
    breakdown: Optional[UsageBreakdown],
    readout: str = "cost",
    budget: Optional[float] = None,
) -> UsageView:
    """Everything the pane needs to draw the per-model view."""
    if breakdown is None:
        return UsageView(rows=(), status_text="reading usage…", ramp_pct=None)

    rows_data = fold(breakdown.models, readout)

    # A dollar budget is meaningless against a token count, and a budget
    # percentage computed from a total that is missing an unpriced model
    # is a reassuring number with a hole in it.
    budget_applies = (
        budget is not None
        and budget > 0
        and readout == "cost"
        and not breakdown.is_partial_cost
        and breakdown.status != STATUS_UNAVAILABLE
    )

    total_pct = None
    if budget_applies:
        denominator = budget
        total_pct = breakdown.total_cost_usd / budget * 100.0
    else:
        denominator = sum(value for _, value, _ in rows_data)

    rows = []
    for label, value, value_text in rows_data:
        pct = (value / denominator * 100.0) if denominator else 0.0
        rows.append(UsageRow(label=label, value_text=value_text, bar_pct=pct))

    return UsageView(
        rows=tuple(rows),
        status_text=_status_text(
            breakdown, readout, budget if budget_applies else None, total_pct
        ),
        ramp_pct=total_pct if budget_applies else None,
    )


def is_scanned(breakdown: Optional[UsageBreakdown]) -> bool:
    """Whether "no usage" is a claim we are entitled to make."""
    return breakdown is not None and breakdown.status in (STATUS_OK, STATUS_PARTIAL)
