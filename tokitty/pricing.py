"""Per-model API prices, for costing the token counts the ledger scanners
read out of Claude Code transcripts and Codex rollouts.

The rates live in prices.json beside this module, one section per
provider, each stamped with the page it came from and the date it was
read. scripts/refresh_prices.py rewrites that file from the live pricing
pages, and the change lands as a reviewable diff rather than code. A user
can also drop a prices.json of the same shape into Tokitty's state
directory; its models replace the packaged ones entry by entry, so a model
released yesterday can be priced without a new release.

Every rate is written out literally rather than derived from a multiplier.
The usual relationships (cache read 0.1x input, 5m cache write 1.25x, 1h
cache write 2x) do not hold universally -- Claude Fable 5.1 reads cache at
a flat $0.25/MTok against $10 input, which is 0.025x, not 0.1x -- so a
table of multipliers would silently misprice it while looking correct.

The test suite deliberately does NOT assert that any price is current,
because no offline test can know that; it asserts the lookup rules, the
file format, and that the packaged file loads.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

MTOK = 1_000_000

PACKAGED_PRICES = Path(__file__).with_name("prices.json")
OVERRIDE_FILENAME = "prices.json"

SCHEMA_VERSION = 1

# Rate keys in a model entry. input and output are required; a null in any
# of the others means the source publishes no rate for that class.
RATE_KEYS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
_REQUIRED_RATES = ("input", "output")

# Suffix the Codex ledger appends to a context-tiered model's id for a
# request over its threshold, so it lands on its own price row. A model
# priced for short context only has no row under that id and stays
# unpriced over the threshold rather than being charged the short rate.
LONG_CONTEXT_SUFFIX = " long"

# Prices older than this are called out in the pane rather than presented
# as current.
STALE_AFTER_DAYS = 60

# A dated snapshot suffix, e.g. the "-20251101" in
# "claude-opus-4-5-20251101". This is the ONLY suffix price_for() will
# strip -- see its docstring for why prefix matching is not used.
_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")

# Models the scanner synthesizes rather than reads from the wire. They are
# never billed and must never reach the pricing table or the display.
SYNTHETIC_MODELS = frozenset({"<synthetic>", "<advisor-unknown>", "<unattributed>"})


@dataclass(frozen=True)
class ModelPrice:
    """US dollars per million tokens, per token class.

    A None rate means the source publishes no price for that class. The
    row still prices every other class; see cost_usd for what happens when
    tokens actually land in the unpublished one.
    """

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: Optional[float]
    cache_write_5m_per_mtok: Optional[float]
    cache_write_1h_per_mtok: Optional[float]


@dataclass(frozen=True)
class PriceTable:
    """Everything loaded from the price files, keyed by model id.

    A long-context row is stored under its suffixed id, so price_for needs
    no special case for it. `as_of` maps every id to the date its provider
    section was read, which is what staleness is judged on.
    """

    prices: Dict[str, ModelPrice] = field(default_factory=dict)
    as_of: Dict[str, Optional[date]] = field(default_factory=dict)
    long_context_thresholds: Dict[str, int] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    # "<provider> <as_of> (<origin>)" per section loaded, for --debug-print.
    sources: Tuple[str, ...] = ()


class PriceFileError(ValueError):
    """The packaged price file is malformed. Always a bug in the file."""


def _rate(entry: dict, key: str, where: str) -> Optional[float]:
    value = entry.get(key)
    if value is None:
        if key in _REQUIRED_RATES:
            raise PriceFileError(f"{where}: missing {key}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PriceFileError(f"{where}: {key} must be a non-negative number or null, got {value!r}")
    return float(value)


_ENTRY_KEYS = set(RATE_KEYS) | {"long_context", "short_context_only", "as_of"}


def _model_price(entry: dict, where: str, allowed=frozenset(RATE_KEYS)) -> ModelPrice:
    if not isinstance(entry, dict):
        raise PriceFileError(f"{where}: expected an object")
    unknown = set(entry) - set(allowed)
    if unknown:
        # A typo'd key would otherwise read as "no published rate" and
        # quietly unprice a whole token class.
        raise PriceFileError(f"{where}: unknown keys {sorted(unknown)}")
    return ModelPrice(*(_rate(entry, key, where) for key in RATE_KEYS))


def _as_of(value, where: str) -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise PriceFileError(f"{where}: as_of must be YYYY-MM-DD, got {value!r}") from None


@dataclass
class _Entry:
    """One model as a price file describes it, before merging.

    `tiering_set` records whether the entry said anything about context
    length. An override that only restates a model's rates must not wipe
    the packaged long-context row: that would quietly bill every long
    request at the short rate.
    """

    price: ModelPrice
    as_of: date
    long_price: Optional[ModelPrice] = None
    short_only: bool = False
    threshold: Optional[int] = None
    tiering_set: bool = False


def _parse_provider(name: str, section, origin: str):
    """Yield (model_id, _Entry) for one provider section. Raises
    PriceFileError on anything malformed."""
    where = f"{origin}: providers.{name}"
    if not isinstance(section, dict):
        raise PriceFileError(f"{where}: expected an object")
    section_as_of = _as_of(section.get("as_of"), where)
    threshold = section.get("long_context_threshold")
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0):
        raise PriceFileError(f"{where}: long_context_threshold must be a positive integer")
    models = section.get("models")
    if not isinstance(models, dict):
        raise PriceFileError(f"{where}: models must be an object")

    for model_id, entry in models.items():
        entry_where = f"{where}.models.{model_id}"
        price = _model_price(entry, entry_where, _ENTRY_KEYS)
        # A model kept after leaving its pricing page carries the date it
        # was last actually read, not the date of the section around it.
        as_of = _as_of(entry["as_of"], entry_where) if "as_of" in entry else section_as_of
        long_entry = entry.get("long_context")
        short_only = entry.get("short_context_only", False)
        if not isinstance(short_only, bool):
            raise PriceFileError(f"{entry_where}: short_context_only must be true or false")
        tiered = long_entry is not None or short_only
        if tiered and threshold is None:
            raise PriceFileError(f"{where}: a context-tiered model needs long_context_threshold")
        if long_entry is not None and short_only:
            raise PriceFileError(f"{entry_where}: long_context and short_context_only conflict")
        yield model_id, _Entry(
            price=price,
            as_of=as_of,
            long_price=_model_price(long_entry, entry_where + ".long_context") if long_entry is not None else None,
            short_only=short_only,
            threshold=threshold if tiered else None,
            tiering_set="long_context" in entry or "short_context_only" in entry,
        )


def _sources(data, origin: str) -> Tuple[str, ...]:
    return tuple(f"{name} {section.get('as_of')} ({origin})" for name, section in data["providers"].items())


def _parse_file(data, origin: str) -> Dict[str, _Entry]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        raise PriceFileError(f"{origin}: expected schema {SCHEMA_VERSION}")
    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise PriceFileError(f"{origin}: providers must be an object")
    parsed: Dict[str, _Entry] = {}
    for name, section in providers.items():
        for model_id, entry in _parse_provider(name, section, origin):
            if model_id in parsed:
                raise PriceFileError(f"{origin}: {model_id} is listed twice")
            parsed[model_id] = entry
    return parsed


def build_table(packaged: dict, override: Optional[dict] = None, override_origin: str = "override") -> PriceTable:
    """Merge the packaged prices with an optional user override.

    The packaged file must be valid; a fault there raises. An override is
    the user's own file, so a fault there is reported in `warnings` and
    the packaged prices are used unchanged: a typo in a hand-edited file
    should cost the user their override, not the whole cost readout.

    An override entry replaces the packaged one's rates. It inherits the
    packaged context tiering (long-context row, short-only flag,
    threshold) unless it sets long_context or short_context_only itself;
    "short_context_only": false with no long_context makes a model flat.
    """
    entries = _parse_file(packaged, "packaged prices.json")
    sources = _sources(packaged, "packaged")
    warnings = []
    if override is not None:
        try:
            overrides = _parse_file(override, override_origin)
        except PriceFileError as exc:
            warnings.append(f"ignored {exc}")
        else:
            sources += _sources(override, override_origin)
            for model_id, entry in overrides.items():
                base = entries.get(model_id)
                if base is not None and not entry.tiering_set:
                    entry.long_price = base.long_price
                    entry.short_only = base.short_only
                    entry.threshold = base.threshold
                entries[model_id] = entry

    table = PriceTable(warnings=tuple(warnings), sources=sources)
    for model_id, entry in entries.items():
        table.prices[model_id] = entry.price
        table.as_of[model_id] = entry.as_of
        if entry.threshold is not None:
            table.long_context_thresholds[model_id] = entry.threshold
        if entry.long_price is not None:
            table.prices[model_id + LONG_CONTEXT_SUFFIX] = entry.long_price
            table.as_of[model_id + LONG_CONTEXT_SUFFIX] = entry.as_of
    return table


def override_path() -> Path:
    from tokitty.paths import state_dir_path

    return state_dir_path() / OVERRIDE_FILENAME


def load_table() -> PriceTable:
    packaged = json.loads(PACKAGED_PRICES.read_text(encoding="utf-8"))
    path = override_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return build_table(packaged)
    except OSError as exc:
        table = build_table(packaged)
        return PriceTable(table.prices, table.as_of, table.long_context_thresholds, (f"could not read {path}: {exc}",), table.sources)
    try:
        override = json.loads(raw)
    except ValueError as exc:
        table = build_table(packaged)
        return PriceTable(table.prices, table.as_of, table.long_context_thresholds, (f"ignored {path}: {exc}",), table.sources)
    return build_table(packaged, override, override_origin=str(path))


_TABLE: Optional[PriceTable] = None


def table() -> PriceTable:
    """The loaded prices, read once per process. Warnings go to stderr the
    first time, since a hand-edited override that silently does nothing is
    the failure a user would never diagnose."""
    global _TABLE
    if _TABLE is None:
        _TABLE = load_table()
        for warning in _TABLE.warnings:
            print(f"tokitty: prices: {warning}", file=sys.stderr)
    return _TABLE


def reload() -> PriceTable:
    global _TABLE
    _TABLE = None
    return table()


def _resolve(model_id: Optional[str], keys=None) -> Optional[str]:
    """The id in the table a model id prices as, or None. A dated snapshot
    resolves to its base, including under the long-context suffix."""
    if not isinstance(model_id, str) or not model_id:
        return None
    if model_id in SYNTHETIC_MODELS:
        return None
    keys = table().prices if keys is None else keys
    if model_id in keys:
        return model_id
    suffix = LONG_CONTEXT_SUFFIX if model_id.endswith(LONG_CONTEXT_SUFFIX) else ""
    stem = model_id[: -len(suffix)] if suffix else model_id
    base = _SNAPSHOT_SUFFIX.sub("", stem) + suffix
    if base != model_id and base in keys:
        return base
    return None


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
    resolved = _resolve(model_id)
    return table().prices[resolved] if resolved else None


def price_as_of(model_id: Optional[str]) -> Optional[date]:
    """The date a model's price was read, or None when it has no price."""
    resolved = _resolve(model_id)
    return table().as_of.get(resolved) if resolved else None


def long_context_threshold(model_id: str) -> Optional[int]:
    """Input tokens above which a request is billed at a different rate,
    or None for a model whose price does not depend on context length."""
    resolved = _resolve(model_id, table().long_context_thresholds)
    return table().long_context_thresholds[resolved] if resolved else None


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
    suffix = LONG_CONTEXT_SUFFIX if model_id.endswith(LONG_CONTEXT_SUFFIX) else ""
    name = _SNAPSHOT_SUFFIX.sub("", model_id[: len(model_id) - len(suffix)]) + suffix
    if name.startswith("claude-"):
        return name[len("claude-") :]
    return name
