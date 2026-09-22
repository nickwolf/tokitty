import pytest

from tokitty.pricing import (
    PRICES,
    SYNTHETIC_MODELS,
    cost_usd,
    display_name,
    price_for,
)


def test_exact_match_resolves():
    assert price_for("claude-opus-5") is PRICES["claude-opus-5"]


def test_dated_snapshot_resolves_to_its_base():
    PRICES["claude-opus-4-6"]
    assert price_for("claude-opus-4-6-20251101") is PRICES["claude-opus-4-6"]


def test_version_bump_does_not_inherit_a_price():
    """The whole reason price_for does not prefix-match: a future
    "claude-opus-5-1" is a different model at a possibly different price,
    and inheriting claude-opus-5's rates would be confidently wrong."""
    assert price_for("claude-opus-5-1") is None


def test_unknown_model_is_none_not_an_error():
    assert price_for("gpt-4") is None
    assert price_for("") is None
    assert price_for(None) is None
    assert price_for(123) is None


@pytest.mark.parametrize("model", sorted(SYNTHETIC_MODELS))
def test_synthetic_models_are_never_priced(model):
    assert price_for(model) is None


def test_fable_cache_read_is_a_flat_rate_not_a_multiple_of_input():
    """The row that rules out a multiplier-based table: 0.25 against a
    $10 input rate is 0.025x, where every other model reads at 0.1x."""
    fable = PRICES["claude-fable-5-1"]
    assert fable.cache_read_per_mtok == 0.25
    assert fable.cache_read_per_mtok != pytest.approx(fable.input_per_mtok * 0.1)

    opus = PRICES["claude-opus-5"]
    assert opus.cache_read_per_mtok == pytest.approx(opus.input_per_mtok * 0.1)


def test_cache_write_tiers_differ():
    opus = PRICES["claude-opus-5"]
    assert opus.cache_write_1h_per_mtok > opus.cache_write_5m_per_mtok


def test_cost_usd_sums_every_token_class():
    price = PRICES["claude-opus-5"]
    cost = cost_usd(price, 1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    expected = (
        price.input_per_mtok
        + price.output_per_mtok
        + price.cache_read_per_mtok
        + price.cache_write_5m_per_mtok
        + price.cache_write_1h_per_mtok
    )
    assert cost == pytest.approx(expected)


def test_cost_usd_is_none_when_unpriced():
    assert cost_usd(None, 1, 1, 1, 1, 1) is None


def test_display_name_strips_the_vendor_prefix():
    assert display_name("claude-opus-5") == "opus-5"
    assert display_name("gpt-4") == "gpt-4"


def test_display_name_passes_synthetic_ids_through():
    assert display_name("<advisor-unknown>") == "<advisor-unknown>"


def test_every_known_id_is_a_claude_or_openai_model():
    """Guards against a typo'd key silently becoming an unreachable row."""
    assert all(model.startswith(("claude-", "gpt-")) for model in PRICES)


def test_codex_internal_models_are_never_priced():
    """Neither is on OpenAI's pricing page. codex-auto-review is half of
    all measured Codex tokens, so a guessed rate there would be most of
    the readout."""
    assert price_for("codex-auto-review") is None
    assert price_for("gpt-5.3-codex-spark") is None


def test_openai_rows_are_dated():
    from tokitty.pricing import OPENAI_PRICES_AS_OF

    assert OPENAI_PRICES_AS_OF == "2026-09-22"


def test_long_context_row_exists_only_where_published():
    from tokitty.pricing import LONG_CONTEXT_SUFFIX

    assert price_for("gpt-5.6-sol" + LONG_CONTEXT_SUFFIX) is not None
    # gpt-5.5 and gpt-5.4 are published for short context only.
    assert price_for("gpt-5.5" + LONG_CONTEXT_SUFFIX) is None
    assert price_for("gpt-5.4" + LONG_CONTEXT_SUFFIX) is None


def test_tokens_in_a_class_with_no_published_rate_make_the_cost_unknown():
    """gpt-5.5 publishes no cache-write rate. Pricing the other classes
    and dropping the writes would look complete and not be."""
    gpt55 = PRICES["gpt-5.5"]
    assert cost_usd(gpt55, 1_000_000, 0, 0, 0, 0) == pytest.approx(5.0)
    assert cost_usd(gpt55, 1_000_000, 0, 0, 1, 0) is None


def test_display_name_drops_a_dated_snapshot_suffix():
    """It is the same model at the same price, and the date ate the whole
    row: "claude-haiku-4-5-20251001" rendered as "haiku-4-5-202…"."""
    assert display_name("claude-haiku-4-5-20251001") == "haiku-4-5"
