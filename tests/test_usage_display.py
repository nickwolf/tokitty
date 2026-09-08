from datetime import datetime, timezone

import pytest

from tokitty.usage_display import ROW_SLOTS, build_view, fold, format_tokens, window_label
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
