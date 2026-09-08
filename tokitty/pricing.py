"""Per-model API prices, for costing the token counts usage_scan.py reads
out of Claude Code's transcripts.

Every rate is written out literally rather than derived from a multiplier.
The usual relationships (cache read 0.1x input, 5m cache write 1.25x, 1h
cache write 2x) do not hold universally -- Claude Fable 5.1 reads cache at
a flat $0.25/MTok against $10 input, which is 0.025x, not 0.1x -- so a
table of multipliers would silently misprice it while looking correct.

These are Anthropic first-party API rates as published on 2026-09-08.
They will go stale. The test suite deliberately does NOT assert that any
price is current, because no offline test can know that; it asserts the
lookup rules and the set of ids the app knows about.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

MTOK = 1_000_000

# A dated snapshot suffix, e.g. the "-20251101" in
# "claude-opus-4-5-20251101". This is the ONLY suffix price_for() will
# strip -- see its docstring for why prefix matching is not used.
_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class ModelPrice:
    """US dollars per million tokens, per token class."""

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_5m_per_mtok: float
    cache_write_1h_per_mtok: float


def _standard(input_per_mtok: float, output_per_mtok: float) -> ModelPrice:
    """A row whose cache rates follow the usual multipliers. Spelled out
    at construction so the stored row is literal even when the arithmetic
    that produced it was not."""
    return ModelPrice(
        input_per_mtok=input_per_mtok,
        output_per_mtok=output_per_mtok,
        cache_read_per_mtok=input_per_mtok * 0.1,
        cache_write_5m_per_mtok=input_per_mtok * 1.25,
        cache_write_1h_per_mtok=input_per_mtok * 2.0,
    )


PRICES: Dict[str, ModelPrice] = {
    # Fable's cache read is a flat rate, not a multiple of its input rate.
    # This is the row that rules out a multiplier-based table.
    "claude-fable-5-1": ModelPrice(10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-fable-5": ModelPrice(10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-mythos-5-1": ModelPrice(10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-opus-5": _standard(5.0, 25.0),
    "claude-opus-4-8": _standard(5.0, 25.0),
    "claude-opus-4-7": _standard(5.0, 25.0),
    "claude-opus-4-6": _standard(5.0, 25.0),
    "claude-sonnet-5": _standard(2.0, 10.0),
    "claude-sonnet-4-6": _standard(3.0, 15.0),
    "claude-haiku-4-5": _standard(1.0, 5.0),
}

# Models the scanner synthesizes rather than reads from the wire. They are
# never billed and must never reach the pricing table or the display.
SYNTHETIC_MODELS = frozenset({"<synthetic>", "<advisor-unknown>", "<unattributed>"})


def price_for(model_id: Optional[str]) -> Optional[ModelPrice]:
    """The price row for a model id, or None when it is unknown.

    Exact match first, then the id with a trailing -YYYYMMDD stripped.
    Nothing else. Longest-prefix matching is deliberately NOT used: a
    future "claude-opus-5-1" would resolve to "claude-opus-5" and report a
    confidently wrong number, where None reports an honest unknown that
    the UI already knows how to render. A dated snapshot is a documented
    naming pattern; a version bump is not.

    None is not an error. Callers still count the tokens, show the model,
    render its cost as unknown, and exclude it from the dollar total.
    """
    if not isinstance(model_id, str) or not model_id:
        return None
    if model_id in SYNTHETIC_MODELS:
        return None

    exact = PRICES.get(model_id)
    if exact is not None:
        return exact

    base = _SNAPSHOT_SUFFIX.sub("", model_id)
    if base != model_id:
        return PRICES.get(base)
    return None


def cost_usd(
    price: Optional[ModelPrice],
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_5m_tokens: int,
    cache_write_1h_tokens: int,
) -> Optional[float]:
    """Dollar cost for one model's token counts, or None when unpriced."""
    if price is None:
        return None
    return (
        input_tokens * price.input_per_mtok
        + output_tokens * price.output_per_mtok
        + cache_read_tokens * price.cache_read_per_mtok
        + cache_write_5m_tokens * price.cache_write_5m_per_mtok
        + cache_write_1h_tokens * price.cache_write_1h_per_mtok
    ) / MTOK


def display_name(model_id: str) -> str:
    """The short form shown in a pane row: "claude-opus-5" -> "opus-5".

    A dated snapshot suffix is dropped too, because it is the same model
    at the same price and the date is pure noise in a 158px column:
    "claude-haiku-4-5-20251001" rendered as "haiku-4-5-202…", spending the
    whole row on a truncated date. Synthetic ids pass through, since their
    angle brackets are the point.
    """
    if model_id in SYNTHETIC_MODELS:
        return model_id
    name = _SNAPSHOT_SUFFIX.sub("", model_id)
    if name.startswith("claude-"):
        return name[len("claude-") :]
    return name
