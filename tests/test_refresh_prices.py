import html
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import refresh_prices  # noqa: E402
from refresh_prices import (  # noqa: E402
    LayoutError,
    anthropic_model_id,
    dumps,
    parse_anthropic,
    parse_openai,
    refresh,
)
from tokitty.pricing import LONG_CONTEXT_SUFFIX, build_table  # noqa: E402

HEADER = ["Model", "Base input tokens", "5m cache writes", "1h cache writes", "Cache hits and refreshes", "Output tokens"]


def anthropic_page(rows, header=HEADER):
    def tr(cells, tag):
        return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"

    body = tr(header, "th") + "".join(tr(r, "td") for r in rows)
    return f"<html><table><tr><th>Concept</th></tr></table><table>{body}</table></html>"


def mtok(value):
    return f"${value} / MTok"


OPUS = ["Claude Opus 5", mtok(5), mtok(6.25), mtok(10), mtok(0.5), mtok(25)]


def island(export, props):
    """Encode props the way Astro does: [0, value] for scalars and
    objects, [1, [...]] for arrays."""

    def enc(value):
        if isinstance(value, list):
            return [1, [enc(v) for v in value]]
        if isinstance(value, dict):
            return [0, {k: enc(v) for k, v in value.items()}]
        return [0, value]

    encoded = {k: enc(v) for k, v in props.items()}
    return f'<astro-island uid="x" component-export="{export}" props="{html.escape(json.dumps(encoded))}"></astro-island>'


GROUPED_HEADINGS = [
    "Model",
    "Short context input", "Short context cached input", "Short context cache writes", "Short context output",
    "Long context input", "Long context cached input", "Long context cache writes", "Long context output",
]


def openai_page(rows, grouped=None, tier="standard"):
    parts = [island("TextTokenPricingTables", {"tier": tier, "rows": rows})]
    if grouped is not None:
        parts.append(island("GroupedPricingTable", {
            "headings": GROUPED_HEADINGS,
            "headingGroups": [{"label": ""}, {"label": {"tooltip": "≤272K input tokens"}}, {"label": {"tooltip": ">272K input tokens"}}],
            "groups": grouped,
        }))
    return "<html>" + "".join(parts) + "</html>"


SOL = ["gpt-5.6-sol", 4, 0.4, 5, 20]
SOL_LONG = [{"model": "gpt-5.6-sol", "rows": [[4, 0.4, 5, 20, 8, 0.8, 10, 30]]}]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Claude Opus 5.5", "claude-opus-5-5"),
        ("Claude Opus 5", "claude-opus-5"),
        ("Claude Mythos 5.1 (limited availability)", "claude-mythos-5-1"),
        ("Claude Opus 4.1 (retired, except on Bedrock and Google Cloud)", "claude-opus-4-1"),
        # The real id is claude-3-5-haiku: version first, not derivable.
        ("Claude Haiku 3.5", None),
        ("Claude Platform on AWS", None),
    ],
)
def test_anthropic_display_names_map_to_api_ids(name, expected):
    assert anthropic_model_id(name) == expected


def test_anthropic_rows_parse_with_footnote_markers():
    fable = ["Claude Fable 5.1", mtok(10), mtok(12.50), mtok(20), mtok(0.25) + "1", mtok(50)]
    models, skipped = parse_anthropic(anthropic_page([OPUS, fable]))
    assert models["claude-fable-5-1"]["cache_read"] == 0.25
    assert models["claude-opus-5"] == {
        "input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10.0,
    }
    assert skipped == []


def test_anthropic_refuses_a_table_whose_columns_moved():
    swapped = ["Claude Opus 5", mtok(5), mtok(10), mtok(6.25), mtok(0.5), mtok(25)]
    with pytest.raises(LayoutError, match="expected columns"):
        parse_anthropic(anthropic_page([swapped]))


def test_anthropic_refuses_a_page_without_the_table():
    with pytest.raises(LayoutError, match="no table"):
        parse_anthropic(anthropic_page([OPUS], header=["Model", "Input", "Output"]))


def test_openai_standard_rows_and_long_context():
    page = openai_page([SOL, ["gpt-6-luna", 0.1, 0.01, 0.125, 0.5]], grouped=SOL_LONG)
    models, threshold, skipped = parse_openai(page)
    assert threshold == 272_000
    assert models["gpt-6-luna"]["cache_write_1h"] is None
    assert models["gpt-5.6-sol"]["long_context"]["input"] == 8.0
    assert "long_context" not in models["gpt-6-luna"]


def test_openai_short_context_only_rows_and_dashes():
    page = openai_page([["gpt-5.5 (<272K context length)", 5, 0.5, "-", 30]])
    models, threshold, _ = parse_openai(page)
    assert models["gpt-5.5"]["short_context_only"] is True
    assert models["gpt-5.5"]["cache_write_5m"] is None
    assert threshold == 272_000


def test_openai_three_column_rows_are_skipped_not_guessed():
    models, _, skipped = parse_openai(openai_page([SOL, ["gpt-4o", 2.5, 1.25, 10]]))
    assert "gpt-4o" not in models
    assert any("gpt-4o" in s for s in skipped)


def test_openai_only_the_standard_tier_is_read():
    with pytest.raises(LayoutError, match="standard"):
        parse_openai(openai_page([SOL], tier="batch"))


def test_openai_refuses_rows_whose_columns_moved():
    # Output and cache-write swapped: output below input is impossible.
    with pytest.raises(LayoutError, match="do not fit"):
        parse_openai(openai_page([["gpt-5.6-sol", 4, 0.4, 20, 5]]))


def test_refresh_keeps_models_that_left_the_page_and_reports_changes():
    existing = {"schema": 1, "providers": {"anthropic": {"as_of": "2026-09-08", "models": {
        "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10.0},
        "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write_5m": 12.5, "cache_write_1h": 20.0},
        "claude-old-4": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0},
    }}}}
    fable = ["Claude Fable 5", mtok(10), mtok(12.50), mtok(20), mtok(1), mtok(50)]
    data, report = refresh(existing, anthropic_page([OPUS, fable]), openai_page([SOL], grouped=SOL_LONG), "2026-09-22")

    anthropic = data["providers"]["anthropic"]
    assert anthropic["as_of"] == "2026-09-22"
    assert anthropic["models"]["claude-fable-5"]["cache_read"] == 1.0
    assert "claude-old-4" in anthropic["models"]
    text = "\n".join(report)
    assert "~ claude-fable-5" in text
    assert "? claude-old-4" in text

    # What the script writes is what the app loads.
    table = build_table(json.loads(dumps(data)))
    assert table.prices["gpt-5.6-sol" + LONG_CONTEXT_SUFFIX].input_per_mtok == 8.0
    assert table.long_context_thresholds["gpt-5.6-sol"] == 272_000


def test_openai_refuses_a_short_only_label_with_long_context_rates():
    page = openai_page([["gpt-5.6-sol (<272K context length)", 4, 0.4, 5, 20]], grouped=SOL_LONG)
    with pytest.raises(LayoutError, match="short-context only"):
        parse_openai(page)


def test_openai_refuses_a_renamed_long_context_table():
    page = openai_page([SOL]) + island("GroupedPricingTable", {"headings": ["Model", "Long context input (new)"], "groups": []})
    with pytest.raises(LayoutError, match="unexpected headings"):
        parse_openai(page)


def test_anthropic_refuses_a_short_row():
    with pytest.raises(LayoutError, match="cells"):
        parse_anthropic(anthropic_page([OPUS[:4]]))


def test_a_model_that_left_the_page_keeps_its_old_date():
    existing = {"schema": 1, "providers": {"anthropic": {"as_of": "2026-09-08", "models": {
        "claude-old-4": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0},
    }}}}
    data, _ = refresh(existing, anthropic_page([OPUS]), openai_page([SOL], grouped=SOL_LONG), "2026-09-22")
    assert data["providers"]["anthropic"]["models"]["claude-old-4"]["as_of"] == "2026-09-08"
    assert "as_of" not in data["providers"]["anthropic"]["models"]["claude-opus-5"]
    table = build_table(json.loads(dumps(data)))
    assert str(table.as_of["claude-old-4"]) == "2026-09-08"


def test_losing_a_long_context_row_is_called_out():
    existing = {"schema": 1, "providers": {"openai": {"as_of": "2026-09-08", "long_context_threshold": 272000, "models": {
        "gpt-5.6-sol": {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write_5m": 5.0, "cache_write_1h": None,
                        "long_context": {"input": 8.0, "output": 30.0, "cache_read": 0.8, "cache_write_5m": 10.0, "cache_write_1h": None}},
    }}}}
    _, report = refresh(existing, anthropic_page([OPUS]), openai_page([SOL]), "2026-09-22")
    assert any("! gpt-5.6-sol lost long_context" in line for line in report)


def test_main_refuses_to_write_a_file_the_app_would_not_load(tmp_path, monkeypatch, capsys):
    target = tmp_path / "prices.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(refresh_prices, "PRICES_FILE", target)
    broken = {"schema": 1, "providers": {"openai": {"as_of": "2026-09-22", "models": {
        "x": {"input": 1.0, "output": 2.0, "short_context_only": True}}}}}
    monkeypatch.setattr(refresh_prices, "refresh", lambda *a: (broken, []))
    monkeypatch.setattr(refresh_prices, "fetch", lambda url: "")
    assert refresh_prices.main([]) == 2
    assert target.read_text(encoding="utf-8") == "{}"
    assert "does not load" in capsys.readouterr().err
