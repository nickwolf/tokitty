from datetime import datetime, timezone

import pytest

from tokitty.usage_display import ROW_SLOTS, build_view, fold, format_tokens, hover_text, window_label
from tokitty.usage_scan import (
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    ModelUsage,
    UsageBreakdown,
)
from tokitty.usage_scan import window_start as usage_window_start

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def model(name, cost, tokens=None):
    tokens = tokens if tokens is not None else int((cost or 0) * 1000)
    return ModelUsage(
        model=name,
        input_tokens=tokens,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        cost_usd=cost,
    )


def breakdown(models, status=STATUS_OK, window="7d"):
    return UsageBreakdown(
        status=status,
        window=window,
        window_start=NOW,
        models=tuple(models),
        total_cost_usd=sum(m.cost_usd for m in models if m.cost_usd is not None),
        total_tokens=sum(m.total_tokens for m in models),
        unpriced_models=tuple(m.model for m in models if m.cost_usd is None),
        scanned_at=NOW,
    )


def test_no_snapshot_yet_reads_as_scanning():
    view = build_view(None)
    assert view.rows == ()
    assert "reading" in view.status_text


def test_three_or_fewer_models_are_all_shown():
    view = build_view(breakdown([model("claude-opus-5", 3.0), model("claude-sonnet-5", 1.0)]))
    assert [r.label for r in view.rows] == ["opus-5", "sonnet-5"]


def test_fourth_model_folds_into_other():
    """Three models PLUS an other row is four rows, and there are three
    slots. Two named plus a labelled remainder tells the truth."""
    models = [model(f"m{i}", float(5 - i)) for i in range(5)]
    view = build_view(breakdown(models))
    assert len(view.rows) == ROW_SLOTS
    assert view.rows[-1].label == "other (3)"


def test_folded_other_totals_the_tail():
    models = [model("a", 5.0), model("b", 4.0), model("c", 1.0), model("d", 2.0)]
    rows = fold(models, "cost")
    assert rows[-1] == ("other (2)", 3.0, "$3.00")


def test_shares_are_of_the_total_and_sum_to_100():
    view = build_view(breakdown([model("a", 3.0), model("b", 1.0)]))
    assert [round(r.bar_pct) for r in view.rows] == [75, 25]
    assert sum(r.bar_pct for r in view.rows) == pytest.approx(100.0)


def test_a_folded_other_larger_than_the_biggest_model_never_exceeds_full_width():
    """Share-of-largest would clamp here at over 100%."""
    models = [model("a", 5.0), model("b", 4.0), model("c", 4.0), model("d", 4.0)]
    view = build_view(breakdown(models))
    assert view.rows[-1].label == "other (2)"
    assert all(r.bar_pct <= 100.0 for r in view.rows)
    assert sum(r.bar_pct for r in view.rows) == pytest.approx(100.0)


def test_no_budget_leaves_the_ramp_unused():
    """The red-at-80% ramp means "close to a cap"; share-of-total has none."""
    assert build_view(breakdown([model("a", 9.0)])).ramp_pct is None


def test_budget_shares_one_denominator_and_drives_the_ramp_from_the_total():
    view = build_view(breakdown([model("a", 30.0), model("b", 10.0)]), budget=100.0)
    assert [round(r.bar_pct) for r in view.rows] == [30, 10]
    assert view.ramp_pct == pytest.approx(40.0)
    assert "of $100.00" in view.status_text


def test_a_total_over_budget_keeps_the_real_percentage():
    view = build_view(breakdown([model("a", 50.0)]), budget=40.0)
    assert view.ramp_pct == pytest.approx(125.0)
    assert "125%" in view.status_text


def test_tokens_readout_ignores_a_dollar_budget():
    view = build_view(breakdown([model("a", 30.0)]), readout="tokens", budget=100.0)
    assert view.ramp_pct is None
    assert "$" not in view.status_text
    assert "tokens" in view.status_text


def test_readout_changes_both_the_figures_and_the_bar_widths():
    """Cost ranking and token ranking are not the same ranking."""
    cheap_but_huge = model("cheap", 1.0, tokens=1_000_000)
    dear_but_small = model("dear", 9.0, tokens=1_000)
    by_cost = build_view(breakdown([cheap_but_huge, dear_but_small]))
    by_tokens = build_view(breakdown([cheap_but_huge, dear_but_small]), readout="tokens")

    assert by_cost.rows[0].label == "dear"
    assert by_tokens.rows[0].label == "cheap"
    assert by_cost.rows[0].bar_pct != by_tokens.rows[0].bar_pct


def test_unpriced_model_marks_the_total_as_a_lower_bound():
    view = build_view(breakdown([model("a", 5.0), model("unknown", None, tokens=10)]))
    assert ">=" in view.status_text


def test_unpriced_model_suppresses_the_budget_percentage():
    """A budget bar computed from a total with a hole in it is worse than
    no budget bar."""
    view = build_view(
        breakdown([model("a", 5.0), model("unknown", None, tokens=10)]), budget=40.0
    )
    assert view.ramp_pct is None
    assert "of $40.00" not in view.status_text


def test_unavailable_never_claims_no_usage():
    view = build_view(breakdown([], status=STATUS_UNAVAILABLE))
    assert view.status_text == "can't read usage"


def test_empty_window_after_a_clean_scan_says_no_usage():
    assert "no usage in the last 7 days" == build_view(breakdown([])).status_text
    assert "no usage this month" == build_view(breakdown([], window="month")).status_text
    assert "no usage in the last 24 hours" == build_view(breakdown([], window="24h")).status_text


def test_partial_scan_is_marked():
    view = build_view(breakdown([model("a", 5.0)], status=STATUS_PARTIAL))
    assert view.status_text.endswith("partial")


def test_window_label_uses_the_months_own_name():
    """Fed a real window_start, which is the LOCAL 1st expressed in UTC.
    A naive UTC midnight would render as the previous month west of
    Greenwich, which is the same off-by-one the boundary itself avoids."""
    start = usage_window_start("month", NOW)
    assert window_label("month", start) == start.astimezone().strftime("%b")
    assert window_label("7d", NOW) == "7d"


def test_format_tokens():
    assert format_tokens(12_400_000) == "12.4M"
    assert format_tokens(3_100) == "3.1K"
    assert format_tokens(42) == "42"


def test_status_line_says_when_the_rates_behind_it_are_old():
    from datetime import timedelta

    from tokitty.pricing import price_as_of

    as_of = price_as_of("claude-opus-5")
    row = ModelUsage("claude-opus-5", 1_000_000, 0, 0, 0, 0, 5.0)
    fresh = UsageBreakdown(STATUS_OK, "7d", NOW, models=(row,), total_cost_usd=5.0, total_tokens=1_000_000,
                           scanned_at=datetime.combine(as_of, datetime.min.time(), timezone.utc) + timedelta(days=30))
    stale = UsageBreakdown(STATUS_OK, "7d", NOW, models=(row,), total_cost_usd=5.0, total_tokens=1_000_000,
                           scanned_at=datetime.combine(as_of, datetime.min.time(), timezone.utc) + timedelta(days=61))
    assert build_view(fresh).status_text.endswith(" at API rates")
    assert build_view(stale).status_text.endswith(" at old API rates")


def test_an_unpriced_row_never_makes_the_rates_look_old():
    row = ModelUsage("codex-auto-review", 1_000, 0, 0, 0, 0, None)
    breakdown = UsageBreakdown(STATUS_OK, "7d", NOW, models=(row,), total_tokens=1_000,
                               unpriced_models=("codex-auto-review",), scanned_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert "old" not in build_view(breakdown).status_text


# ---- hover detail ------------------------------------------------------------

def full_model(name, cost):
    return ModelUsage(model=name, input_tokens=120_000, output_tokens=45_000,
                      cache_read_tokens=1_800_000, cache_write_5m_tokens=60_000,
                      cache_write_1h_tokens=30_000, cost_usd=cost)


def test_a_row_hovers_with_both_readouts_whichever_the_card_shows():
    models = [full_model("claude-opus-4-6", 12.4), model("claude-sonnet-5", 7.6, tokens=1000)]
    for readout in ("cost", "tokens"):
        view = build_view(breakdown(models), readout)
        top = next(r for r in view.rows if r.label == "opus-4-6")
        text = hover_text(top.detail, view.window_text)
        lines = text.split("\n")
        assert lines[0] == "Opus 4.6 (7d)" or lines[0].endswith("(7d)")
        assert "$12.40" in lines[1] and "2.1M tokens" in lines[1]
        assert lines[2] == "in 120.0K \u00b7 out 45.0K"
        assert lines[3] == "cache read 1.8M \u00b7 cache write 90.0K"


def test_the_share_is_of_the_rows_total_in_the_cards_readout():
    models = [model("claude-opus-5", 6.0), model("claude-sonnet-5", 4.0)]
    view = build_view(breakdown(models), "cost")
    assert "60% of total" in hover_text(view.rows[0].detail, view.window_text)
    assert "40% of total" in hover_text(view.rows[1].detail, view.window_text)


def test_an_unknown_cost_reads_as_dashes():
    view = build_view(breakdown([model("mystery-1", None, tokens=500)]))
    assert hover_text(view.rows[0].detail, "7d").split("\n")[1].startswith("-- \u00b7 500 tokens")


def test_the_other_row_hovers_with_the_summed_numbers():
    models = [full_model(f"claude-m{i}", float(5 - i)) for i in range(5)]
    view = build_view(breakdown(models))
    other = view.rows[-1].detail
    assert other.label == "Other" and other.model_count == 3
    assert other.input_tokens == 3 * 120_000 and other.cache_write_tokens == 3 * 90_000
    assert other.cost_usd == pytest.approx(3.0 + 2.0 + 1.0)
    assert hover_text(other, "7d").split("\n")[0] == "Other (3 models, 7d)"


def test_a_partly_unpriced_other_row_is_marked_as_a_lower_bound():
    models = [model("a", 5.0), model("b", 4.0), model("c", 3.0), model("d", None, tokens=10)]
    other = build_view(breakdown(models)).rows[-1].detail
    assert other.cost_is_partial
    assert ">= $3.00" in hover_text(other, "7d")


def test_detail_does_not_take_part_in_row_equality():
    a = build_view(breakdown([model("claude-opus-5", 3.0)])).rows[0]
    b = build_view(breakdown([model("claude-opus-5", 3.0, tokens=1)])).rows[0]
    assert a == b
