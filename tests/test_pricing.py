import json
from datetime import date

import pytest

from tokitty import pricing
from tokitty.pricing import (
    LONG_CONTEXT_SUFFIX,
    SYNTHETIC_MODELS,
    PriceFileError,
    build_table,
    cost_usd,
    display_name,
    long_context_threshold,
    price_as_of,
    price_for,
)


def PRICES():
    return pricing.table().prices


def packaged():
    return json.loads(pricing.PACKAGED_PRICES.read_text(encoding="utf-8"))


def price_file(models, as_of="2026-09-22", **section):
    return {"schema": 1, "providers": {"test": {"as_of": as_of, "models": models, **section}}}


ROW = {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0}


def test_exact_match_resolves():
    assert price_for("claude-opus-5") is PRICES()["claude-opus-5"]


def test_dated_snapshot_resolves_to_its_base():
    PRICES()["claude-opus-4-6"]
    assert price_for("claude-opus-4-6-20251101") is PRICES()["claude-opus-4-6"]


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
    fable = PRICES()["claude-fable-5-1"]
    assert fable.cache_read_per_mtok == 0.25
    assert fable.cache_read_per_mtok != pytest.approx(fable.input_per_mtok * 0.1)

    opus = PRICES()["claude-opus-5"]
    assert opus.cache_read_per_mtok == pytest.approx(opus.input_per_mtok * 0.1)


def test_cache_write_tiers_differ():
    opus = PRICES()["claude-opus-5"]
    assert opus.cache_write_1h_per_mtok > opus.cache_write_5m_per_mtok


def test_cost_usd_sums_every_token_class():
    price = PRICES()["claude-opus-5"]
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


def test_every_known_id_is_a_plain_model_id():
    """Guards against a display name or a typo'd key silently becoming an
    unreachable row. The only spaced ids are the synthesized long-context
    rows."""
    for model in PRICES():
        base = model[: -len(LONG_CONTEXT_SUFFIX)] if model.endswith(LONG_CONTEXT_SUFFIX) else model
        assert base == base.strip().lower() and " " not in base, model


def test_codex_internal_models_are_never_priced():
    """Neither is on OpenAI's pricing page. codex-auto-review is half of
    all measured Codex tokens, so a guessed rate there would be most of
    the readout."""
    assert price_for("codex-auto-review") is None
    assert price_for("gpt-5.3-codex-spark") is None


def test_every_packaged_provider_names_its_source_and_date():
    for name, section in packaged()["providers"].items():
        assert section["source"].startswith("https://"), name
        date.fromisoformat(section["as_of"])


def test_every_priced_model_carries_its_date():
    assert price_as_of("claude-opus-5") is not None
    assert price_as_of("claude-opus-5-20251101") == price_as_of("claude-opus-5")
    assert price_as_of("codex-auto-review") is None


def test_long_context_row_exists_only_where_published():
    assert price_for("gpt-5.6-sol" + LONG_CONTEXT_SUFFIX) is not None
    # gpt-5.5 and gpt-5.4 are published for short context only.
    assert price_for("gpt-5.5" + LONG_CONTEXT_SUFFIX) is None
    assert price_for("gpt-5.4" + LONG_CONTEXT_SUFFIX) is None


def test_tokens_in_a_class_with_no_published_rate_make_the_cost_unknown():
    """gpt-5.5 publishes no cache-write rate. Pricing the other classes
    and dropping the writes would look complete and not be."""
    gpt55 = PRICES()["gpt-5.5"]
    assert cost_usd(gpt55, 1_000_000, 0, 0, 0, 0) == pytest.approx(5.0)
    assert cost_usd(gpt55, 1_000_000, 0, 0, 1, 0) is None


def test_display_name_drops_a_dated_snapshot_suffix():
    """It is the same model at the same price, and the date ate the whole
    row: "claude-haiku-4-5-20251001" rendered as "haiku-4-5-202…"."""
    assert display_name("claude-haiku-4-5-20251001") == "haiku-4-5"


def test_context_tiered_models_carry_a_threshold():
    assert long_context_threshold("gpt-5.6-sol") == 272_000
    assert long_context_threshold("gpt-5.5") == 272_000
    assert long_context_threshold("gpt-6-astra") is None
    assert long_context_threshold("claude-opus-5") is None


def test_packaged_file_is_what_the_refresh_script_would_write():
    """One model per line keeps a price change to a one-line diff; a hand
    edit that reflowed the file would lose that."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from refresh_prices import dumps

    assert pricing.PACKAGED_PRICES.read_text(encoding="utf-8") == dumps(packaged())


def test_override_replaces_a_model_and_adds_new_ones():
    override = price_file({"claude-opus-5": {**ROW, "input": 99.0}, "gpt-7": ROW}, as_of="2026-10-01")
    table = build_table(packaged(), override)
    assert table.prices["claude-opus-5"].input_per_mtok == 99.0
    assert table.as_of["claude-opus-5"] == date(2026, 10, 1)
    assert table.prices["gpt-7"].output_per_mtok == 2.0
    assert table.warnings == ()


def test_a_broken_override_is_ignored_with_a_warning_not_a_crash():
    table = build_table(packaged(), price_file({"gpt-7": {"input": 1.0}}))
    assert "gpt-7" not in table.prices
    assert table.prices["claude-opus-5"] == build_table(packaged()).prices["claude-opus-5"]
    assert "missing output" in table.warnings[0]


def test_a_malformed_packaged_file_raises():
    with pytest.raises(PriceFileError):
        build_table(price_file({"x": {**ROW, "input": "4"}}))


def test_a_typod_rate_key_is_rejected_rather_than_read_as_unpublished():
    with pytest.raises(PriceFileError, match="unknown keys"):
        build_table(price_file({"x": {**ROW, "cache_raed": 0.1}}))


def test_a_long_context_row_needs_a_threshold():
    with pytest.raises(PriceFileError, match="threshold"):
        build_table(price_file({"x": {**ROW, "short_context_only": True}}))


def test_override_file_on_disk_is_read(tmp_path, monkeypatch):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(price_file({"gpt-7": ROW})), encoding="utf-8")
    monkeypatch.setattr(pricing, "override_path", lambda: path)
    pricing.reload()
    assert price_for("gpt-7") is not None


def test_unparseable_override_json_falls_back_to_packaged(tmp_path, monkeypatch, capsys):
    path = tmp_path / "prices.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(pricing, "override_path", lambda: path)
    pricing.reload()
    assert price_for("claude-opus-5") is not None
    assert "ignored" in capsys.readouterr().err


def test_an_override_of_rates_alone_keeps_the_long_context_row():
    """A user bumping one rate must not quietly bill every long request at
    the short rate by wiping the packaged tiering."""
    table = build_table(packaged(), price_file({"gpt-5.6-sol": {**ROW, "input": 4.5}}))
    assert table.prices["gpt-5.6-sol"].input_per_mtok == 4.5
    assert table.prices["gpt-5.6-sol" + LONG_CONTEXT_SUFFIX].input_per_mtok == 8.0
    assert table.long_context_thresholds["gpt-5.6-sol"] == 272_000

    short_only = build_table(packaged(), price_file({"gpt-5.5": ROW}))
    assert short_only.long_context_thresholds["gpt-5.5"] == 272_000
    assert "gpt-5.5" + LONG_CONTEXT_SUFFIX not in short_only.prices


def test_an_override_can_make_a_model_flat_on_purpose():
    table = build_table(packaged(), price_file({"gpt-5.5": {**ROW, "short_context_only": False}}))
    assert "gpt-5.5" not in table.long_context_thresholds


def test_short_context_only_must_be_a_real_boolean():
    with pytest.raises(PriceFileError, match="true or false"):
        build_table(price_file({"x": {**ROW, "short_context_only": "false"}}, long_context_threshold=1000))


def test_a_model_can_carry_its_own_date():
    table = build_table(price_file({"x": {**ROW, "as_of": "2026-01-01"}, "y": ROW}, as_of="2026-09-22"))
    assert table.as_of["x"] == date(2026, 1, 1)
    assert table.as_of["y"] == date(2026, 9, 22)


def test_dated_snapshots_resolve_under_the_long_context_suffix():
    assert long_context_threshold("gpt-5.6-sol-20260901") == 272_000
    assert price_for("gpt-5.6-sol-20260901" + LONG_CONTEXT_SUFFIX) is price_for("gpt-5.6-sol" + LONG_CONTEXT_SUFFIX)
    assert price_for("gpt-5.5-20260901" + LONG_CONTEXT_SUFFIX) is None
    assert display_name("gpt-5.6-sol-20260901" + LONG_CONTEXT_SUFFIX) == "gpt-5.6-sol" + LONG_CONTEXT_SUFFIX
