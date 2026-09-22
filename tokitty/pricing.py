"""Per-model API prices, for costing the token counts usage_scan.py reads
out of Claude Code's transcripts.

Every rate is written out literally rather than derived from a multiplier.
The usual relationships (cache read 0.1x input, 5m cache write 1.25x, 1h
cache write 2x) do not hold universally -- Claude Fable 5.1 reads cache at
a flat $0.25/MTok against $10 input, which is 0.025x, not 0.1x -- so a
table of multipliers would silently misprice it while looking correct.

The Claude rows are Anthropic first-party API rates as published on
2026-09-08; the OpenAI rows are dated separately below. Both will go stale. The test suite deliberately does NOT assert that any
price is current, because no offline test can know that; it asserts the
lookup rules and the set of ids the app knows about.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

MTOK = 1_000_000

# A dated snapshot suffix, e.g. the "-20251101" in
# "claude-opus-4-5-20251101". This is the ONLY suffix price_for() will
# strip -- see its docstring for why prefix matching is not used.
_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class ModelPrice:
    """US dollars per million tokens, per token class.

    A None rate means the source publishes no price for that class. The
    row still prices every other class; see cost_usd for what happens when
    tokens actually land in the unpublished one.
    """

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_5m_per_mtok: Optional[float]
    cache_write_1h_per_mtok: Optional[float]


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

# OpenAI standard-tier rates, read off the pricing tables at
# https://developers.openai.com/api/docs/pricing on this date. Rows are
# ModelPrice(input, output, cached input, cache write, None): OpenAI has one
# cache-write rate, which the Codex ledger carries in the 5m slot, and no
# 1h tier at all. A "-" on the page is None here, never a derived number.
#
# Only models seen in real Codex rollouts are listed. Deliberately absent:
# codex-auto-review (the automatic review pass, not on the page) and
# gpt-5.3-codex-spark (not on the page either). Both keep their tokens and
# show "--" for cost.
#
# Not modelled: Fast mode (formerly Priority) bills 2x, Batch and Flex 0.5x,
# and nothing in a rollout says which tier a turn ran on, so standard is
# the only rate that can be applied without guessing. The page also notes
# gpt-5.6-sol and gpt-6-astra are on promotional pricing "at least through
# November 21, 2026".
OPENAI_PRICES_AS_OF = "2026-09-22"

# Requests above this many input tokens are billed at a separate long-
# context rate by the models in CONTEXT_TIERED_MODELS.
LONG_CONTEXT_THRESHOLD = 272_000

# Suffix the Codex ledger appends to a context-tiered model's id for a
# request over the threshold, so it lands on its own price row. A model
# with no long-context row published stays unpriced over the threshold
# rather than being charged the short-context rate.
LONG_CONTEXT_SUFFIX = " >272K"

CONTEXT_TIERED_MODELS: FrozenSet[str] = frozenset({"gpt-5.6-sol", "gpt-5.5", "gpt-5.4"})

PRICES.update(
    {
        "gpt-6-astra": ModelPrice(10.0, 50.0, 1.0, 12.5, None),
        "gpt-5.6-sol": ModelPrice(4.0, 20.0, 0.4, 5.0, None),
        "gpt-5.6-sol" + LONG_CONTEXT_SUFFIX: ModelPrice(8.0, 30.0, 0.8, 10.0, None),
        "gpt-5.6-terra": ModelPrice(2.0, 12.0, 0.2, 2.5, None),
        "gpt-5.6-luna": ModelPrice(0.2, 1.2, 0.02, 0.25, None),
        # Published for short context only, with no cache-write rate.
        "gpt-5.5": ModelPrice(5.0, 30.0, 0.5, None, None),
        "gpt-5.4": ModelPrice(2.5, 15.0, 0.25, None, None),
    }
)

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
    """Dollar cost for one model's token counts, or None when unpriced.

    Also None when tokens landed in a class the row has no rate for:
    pricing the rest and silently dropping those would print a complete-
    looking number with a hole in it.
    """
    if price is None:
        return None
    total = 0.0
    for tokens, rate in (
        (input_tokens, price.input_per_mtok),
        (output_tokens, price.output_per_mtok),
        (cache_read_tokens, price.cache_read_per_mtok),
        (cache_write_5m_tokens, price.cache_write_5m_per_mtok),
        (cache_write_1h_tokens, price.cache_write_1h_per_mtok),
    ):
        if not tokens:
            continue
        if rate is None:
            return None
        total += tokens * rate
    return total / MTOK


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
