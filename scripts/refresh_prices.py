"""Rewrite tokitty/prices.json from the live Anthropic and OpenAI pricing
pages, for review as a diff before committing.

Stdlib only. Run by hand, never by the app: Tokitty reads its ledgers with
no network, and a scrape that breaks quietly would be worse than a table
that is honestly dated.

Both parsers check their column assumptions against the rates themselves
(cache writes are 1.25x input, cached input is at most input, and so on)
and abort on a violation rather than write a table with shifted columns.
A model that has left a page is kept, not dropped, because its past usage
still needs a price; the report lists it so a person can decide.

Usage: python3 scripts/refresh_prices.py            # write and report
       python3 scripts/refresh_prices.py --check    # report only; exit 1 if anything would change
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PRICES_FILE = Path(__file__).resolve().parent.parent / "tokitty" / "prices.json"

ANTHROPIC_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
OPENAI_URL = "https://developers.openai.com/api/docs/pricing"

ANTHROPIC_HEADER = ["Model", "Base input tokens", "5m cache writes", "1h cache writes", "Cache hits and refreshes", "Output tokens"]

NOTES = {
    "anthropic": "First-party Claude API rates from the main model pricing table.",
    "openai": (
        "Standard tier only: Fast mode bills 2x and Batch/Flex 0.5x, and no rollout field says which "
        "tier a turn ran on. OpenAI has one cache-write rate, carried in cache_write_5m. "
        "Cache writes and cached input are portions of input, not additions to it."
    ),
}


class LayoutError(RuntimeError):
    """A page no longer looks the way the parser expects."""


def fetch(url: str) -> str:
    # openai.com/api/pricing answers 403 to urllib's default agent.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


# --- Anthropic -------------------------------------------------------------

_MTOK_PRICE = re.compile(r"\$([\d.]+)\s*/\s*MTok")


def _cells(row_html: str) -> List[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[hd].*?</t[hd]>", row_html, re.S)]


def anthropic_model_id(name: str) -> Optional[str]:
    """"Claude Opus 5.5" -> "claude-opus-5-5". None for a pre-4 name,
    whose API id puts the version first ("claude-3-5-haiku") and cannot
    be derived from the display name without guessing."""
    name = re.sub(r"\(.*?\)", "", name).strip()
    match = re.fullmatch(r"Claude ([A-Za-z]+) (\d+(?:\.\d+)?)", name)
    if not match:
        return None
    family, version = match.group(1).lower(), match.group(2)
    if int(version.split(".")[0]) < 4:
        return None
    return f"claude-{family}-{version.replace('.', '-')}"


def parse_anthropic(page: str) -> Tuple[Dict[str, dict], List[str]]:
    tables = re.findall(r"<table.*?</table>", page, re.S)
    target = None
    for table in tables:
        rows = re.findall(r"<tr.*?</tr>", table, re.S)
        if rows and _cells(rows[0]) == ANTHROPIC_HEADER:
            target = rows
            break
    if target is None:
        raise LayoutError(f"Anthropic: no table with header {ANTHROPIC_HEADER}")

    models: Dict[str, dict] = {}
    skipped: List[str] = []
    for row in target[1:]:
        cells = _cells(row)
        model_id = anthropic_model_id(cells[0])
        if model_id is None:
            skipped.append(f"{cells[0]}: API id not derivable from the display name")
            continue
        rates = []
        for cell in cells[1:]:
            match = _MTOK_PRICE.search(cell)
            if not match:
                raise LayoutError(f"Anthropic: {cells[0]}: unreadable price {cell!r}")
            rates.append(float(match.group(1)))
        base, write_5m, write_1h, hit, output = rates
        if not (_close(write_5m, base * 1.25) and _close(write_1h, base * 2) and hit <= base <= output):
            raise LayoutError(f"Anthropic: {cells[0]}: rates {rates} do not fit the expected columns")
        models[model_id] = {
            "input": base,
            "output": output,
            "cache_read": hit,
            "cache_write_5m": write_5m,
            "cache_write_1h": write_1h,
        }
    if not models:
        raise LayoutError("Anthropic: pricing table had no readable rows")
    return models, skipped


# --- OpenAI ----------------------------------------------------------------

_ISLAND = re.compile(r'<astro-island[^>]*component-export="([^"]+)"[^>]*props="([^"]*)"')
_SHORT_ONLY = re.compile(r"\s*\(<\s*(\d+)K context length\)\s*$")
_LONG_TOOLTIP = re.compile(r">\s*(\d+)K input tokens")


def _devalue(value):
    """Astro serializes island props as [type, value] pairs: 0 for a plain
    value or object, 1 for an array."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        kind, inner = value
        if kind == 0:
            return {k: _devalue(v) for k, v in inner.items()} if isinstance(inner, dict) else inner
        if kind == 1:
            return [_devalue(v) for v in inner]
        return inner
    if isinstance(value, dict):
        return {k: _devalue(v) for k, v in value.items()}
    return value


def _islands(page: str, export: str) -> List[dict]:
    return [
        _devalue(json.loads(html.unescape(props)))
        for name, props in _ISLAND.findall(page)
        if name == export
    ]


def _num(value) -> Optional[float]:
    if value is None or value == "-":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutError(f"OpenAI: unexpected cell {value!r}")
    return float(value)


def _openai_rates(label: str, cells) -> dict:
    """input, cached input, cache write, output: the four-column layout.
    The props carry no headings for this table, so the order is checked
    against the rates themselves."""
    rate_in, cached, write, output = (_num(c) for c in cells)
    if rate_in is None or output is None:
        raise LayoutError(f"OpenAI: {label}: missing input or output rate")
    if (cached is not None and cached > rate_in) or output < rate_in or (write is not None and not _close(write, rate_in * 1.25)):
        raise LayoutError(f"OpenAI: {label}: rates {list(cells)} do not fit input/cached/write/output")
    return {"input": rate_in, "output": output, "cache_read": cached, "cache_write_5m": write, "cache_write_1h": None}


def parse_openai(page: str) -> Tuple[Dict[str, dict], Optional[int], List[str]]:
    standard = [p for p in _islands(page, "TextTokenPricingTables") if p.get("tier") == "standard"]
    if not standard:
        raise LayoutError("OpenAI: no standard-tier text token table")

    models: Dict[str, dict] = {}
    skipped: List[str] = []
    thresholds = set()
    for table in standard:
        for row in table.get("rows") or []:
            label = row[0]
            if not isinstance(label, str):
                continue
            if len(row) != 5:
                # Three-column rows exist (gpt-5.4-mini and friends) and
                # nothing on the page says which column went missing.
                skipped.append(f"{label}: {len(row) - 1} rate columns, expected 4")
                continue
            short = _SHORT_ONLY.search(label)
            model_id = _SHORT_ONLY.sub("", label).strip()
            if " " in model_id:
                skipped.append(f"{label}: not a model id")
                continue
            entry = _openai_rates(label, row[1:])
            if short:
                entry["short_context_only"] = True
                thresholds.add(int(short.group(1)) * 1000)
            if model_id in models and models[model_id] != entry:
                raise LayoutError(f"OpenAI: {model_id} listed twice with different rates")
            models[model_id] = entry

    for grouped in _islands(page, "GroupedPricingTable"):
        headings = grouped.get("headings") or []
        if headings[1:] != [
            "Short context input", "Short context cached input", "Short context cache writes", "Short context output",
            "Long context input", "Long context cached input", "Long context cache writes", "Long context output",
        ]:
            continue
        match = _LONG_TOOLTIP.search(json.dumps(grouped.get("headingGroups")))
        if not match:
            raise LayoutError("OpenAI: long-context table without a readable threshold")
        thresholds.add(int(match.group(1)) * 1000)
        for group in grouped.get("groups") or []:
            model_id = group.get("model")
            rows = group.get("rows") or []
            if model_id not in models or len(rows) != 1 or len(rows[0]) != 8:
                skipped.append(f"{model_id}: long-context row with no standard row to attach to")
                continue
            short_rates = _openai_rates(model_id, rows[0][:4])
            if any(short_rates[k] != models[model_id][k] for k in short_rates):
                raise LayoutError(f"OpenAI: {model_id}: short-context rates disagree between tables")
            if any(c in (None, "-") for c in rows[0][4:]):
                models[model_id]["short_context_only"] = True
            else:
                models[model_id]["long_context"] = _openai_rates(model_id + " long", rows[0][4:])

    if len(thresholds) > 1:
        raise LayoutError(f"OpenAI: more than one long-context threshold {sorted(thresholds)}")
    return models, (thresholds.pop() if thresholds else None), skipped


# --- merge and write -------------------------------------------------------


def merge(existing: dict, fresh: Dict[str, dict], source: str, as_of: str, notes: str, threshold: Optional[int] = None):
    """(section, report). Fresh rows win; rows missing from the page are
    kept. The report is (added, changed, kept) id lists."""
    old_models = existing.get("models", {}) if existing else {}
    added = sorted(set(fresh) - set(old_models))
    changed = sorted(m for m in set(fresh) & set(old_models) if fresh[m] != old_models[m])
    kept = sorted(set(old_models) - set(fresh))
    models = {**old_models, **fresh}
    section = {"source": source, "as_of": as_of, "notes": notes}
    if threshold is not None or any("long_context" in m or "short_context_only" in m for m in models.values()):
        section["long_context_threshold"] = threshold if threshold is not None else existing.get("long_context_threshold")
    section["models"] = {k: models[k] for k in sorted(models)}
    return section, (added, changed, kept)


def dumps(data: dict) -> str:
    """JSON with one model per line, so a price change is a one-line diff."""
    lines = ["{", f'  "schema": {data["schema"]},', '  "providers": {']
    providers = list(data["providers"].items())
    for p_index, (name, section) in enumerate(providers):
        lines.append(f"    {json.dumps(name)}: {{")
        for key in [k for k in section if k != "models"]:
            lines.append(f"      {json.dumps(key)}: {json.dumps(section[key])},")
        lines.append('      "models": {')
        models = list(section["models"].items())
        for m_index, (model_id, entry) in enumerate(models):
            comma = "," if m_index < len(models) - 1 else ""
            lines.append(f"        {json.dumps(model_id)}: {json.dumps(entry)}{comma}")
        lines.append("      }")
        lines.append("    }" + ("," if p_index < len(providers) - 1 else ""))
    lines.extend(["  }", "}", ""])
    return "\n".join(lines)


def refresh(existing: dict, anthropic_page: str, openai_page: str, today: str):
    """(new data, printable report lines). Pure, so tests feed it pages."""
    report: List[str] = []
    data = {"schema": 1, "providers": {}}
    old = existing.get("providers", {}) if existing else {}

    anthropic, skipped_a = parse_anthropic(anthropic_page)
    data["providers"]["anthropic"], result_a = merge(old.get("anthropic", {}), anthropic, ANTHROPIC_URL, today, NOTES["anthropic"])
    openai, threshold, skipped_o = parse_openai(openai_page)
    data["providers"]["openai"], result_o = merge(old.get("openai", {}), openai, OPENAI_URL, today, NOTES["openai"], threshold)

    for provider, (added, changed, kept), skipped in (("anthropic", result_a, skipped_a), ("openai", result_o, skipped_o)):
        old_models = old.get(provider, {}).get("models", {})
        new_models = data["providers"][provider]["models"]
        report.append(f"{provider}: {len(added)} added, {len(changed)} changed, {len(kept)} not on page (kept)")
        report.extend(f"  + {m} {json.dumps(new_models[m])}" for m in added)
        for m in changed:
            report.append(f"  ~ {m} {json.dumps(old_models[m])}")
            report.append(f"    -> {json.dumps(new_models[m])}")
        report.extend(f"  ? {m} kept from {old.get(provider, {}).get('as_of')}, no longer on the page" for m in kept)
        report.extend(f"  skipped {s}" for s in skipped)
    return data, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report only; exit 1 if the file would change")
    args = parser.parse_args(argv)

    existing = json.loads(PRICES_FILE.read_text(encoding="utf-8")) if PRICES_FILE.exists() else {}
    try:
        data, report = refresh(existing, fetch(ANTHROPIC_URL), fetch(OPENAI_URL), date.today().isoformat())
    except LayoutError as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 2
    print("\n".join(report))

    # The as_of stamp moves on every run, so "changed" means rates.
    def rates(d):
        return {p: s.get("models") for p, s in d.get("providers", {}).items()}

    changed = rates(data) != rates(existing)
    if args.check:
        return 1 if changed else 0
    PRICES_FILE.write_text(dumps(data), encoding="utf-8")
    print(f"wrote {PRICES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
