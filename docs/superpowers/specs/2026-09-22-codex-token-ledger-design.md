# Codex per-model token ledger

Status: measured, not built, 2026-09-22. The provider seam (`eb0d16f`) and the pane display rules (`a7c3cde`) are in on `provider-seam`; `CodexProvider.resolve_projects_dir` currently returns `(None, None)` so the Per-model view is empty for a Codex pane. This document is the measurement that should precede writing the scanner.

## Why the Claude scanner cannot be pointed at it

`usage_scan.TranscriptScanner` reads `<config_dir>/projects/**/*.jsonl` and expects Anthropic's `message.usage` shape: `cache_creation` split by TTL, `cache_read_input_tokens` as a separate class from `input_tokens`, and a model id on the assistant entry. Codex shares none of that. Its ledger lives in the same rollout files as its rate limits, its token classes nest differently, and its model attribution is by join rather than by field.

The output shape is worth keeping identical, though: `UsageBreakdown` of `ModelUsage` rows feeds `usage_display.build_view` unchanged, so a Codex ledger that produces one renders in the existing Per-model view with no UI work.

## What the rollouts actually contain

Verified against 3,193 `token_usage_record` events across 114 rollout files in `/mnt/c/Users/nickw/.codex/sessions` on 2026-09-22, Codex 0.155.0-alpha.9. Five findings drive the design.

### 1. Two event streams report the same API call

Every rollout carries both `token_count` (an `event_msg` payload) and `token_usage_record`. `token_count.info.last_token_usage` and `token_usage_record.usage` were byte-identical in all six files spot-checked, at 81,829 / 27,044 / 121,986 / 129,534 / 53,593 / 167,662 total tokens respectively. **Summing both double-counts the entire bill.**

They are not even reliably paired: one file held 26 `token_count` events against 25 `token_usage_record` events, so "read one and skip the other by position" is not safe either.

Use `token_usage_record`. It is the only one of the two that carries `turn_id`, which is the join key model attribution needs, and it carries `response_id` as a natural dedup key.

### 2. `input_tokens` and `output_tokens` are totals, not disjoint classes

This is the opposite of the Anthropic convention the existing scanner encodes, and getting it backwards inflates the bill.

    input_tokens  >= cached_input_tokens     3193 / 3193 records
    output_tokens >= reasoning_output_tokens 3193 / 3193 records
    input_tokens + output_tokens == total_tokens  3193 / 3193 records

The third line is the proof: if `cached_input_tokens` were a separate class the way `cache_read_input_tokens` is for Anthropic, it would have to appear in `total_tokens` too. It does not. So `cached_input_tokens` is the cached *portion of* `input_tokens`, and `reasoning_output_tokens` the reasoning *portion of* `output_tokens`.

Billing math follows directly:

    uncached_input = input_tokens - cached_input_tokens   priced at the input rate
    cached_input   = cached_input_tokens                  priced at the cached rate
    output         = output_tokens                        priced at the output rate, reasoning included

`cache_write_input_tokens` was present on every record and zero on every record (0 of 3,193 non-zero). Read it and carry it, but no rate can be derived from data that never moves; treat a non-zero value as a signal to revisit rather than something to price from a guess.

### 3. The cached rate is the whole number

Of 331,937,977 input tokens, 317,955,200 were cached: **95.8%**. Total output across the same 3,193 records was 671,954 tokens, or 0.2% of input.

So a Codex cost figure is almost entirely the cached-input rate multiplied by one large number. An error in the output rate is invisible; an error in the cached rate is the entire readout. Whatever price table gets written, that is the row to verify against the live source rather than from memory.

### 4. Model attribution is a join, and it does not always succeed

`token_usage_record` carries no model. `turn_context` carries `turn_id`, `model`, and `effort`, and appears once per turn. Attribution is `turn_context.model` joined on `turn_id`.

Observed over the 114 files:

| model | records | tokens |
|---|---|---|
| `codex-auto-review` | 1,925 | 162,327,649 |
| `gpt-5.6-sol` | 1,206 | 165,102,797 |
| `gpt-6-astra` | 32 | 3,071,635 |
| `gpt-5.6-luna` | 27 | 1,364,631 |
| (no matching `turn_context`) | 3 | 743,219 |

Two consequences. `codex-auto-review` is an internal name for the automatic review pass, not a purchasable model, and it accounts for 49% of all tokens; it has no public price and must not be given one. And 3 records join to nothing, so the scanner needs the same honest-unknown row the Claude scanner already has (`UNATTRIBUTED`): real tokens, no price, rather than tokens attributed to a model that may be wrong.

### 5. `thread_token_usage` is a running total, `turn_token_usage` is not consistently one

`thread_token_usage.total_tokens` advances by exactly the current record's `usage.total_tokens` on each record, so it is the cumulative sum over the file and can be used as a cross-check on a completed scan. `turn_token_usage` accumulated within one file and did not in another, so nothing should be derived from it.

Neither is a substitute for summing `usage`: both are cumulative, and summing a cumulative field over records squares the bill.

## Open decisions

**Prices are not in this document on purpose.** `gpt-5.6-sol`, `gpt-5.6-luna`, and `gpt-6-astra` postdate this session's model knowledge, and `pricing.py` already carries a dated table that goes stale silently; adding a second table sourced from memory would make a wrong number look measured. Fetch the rates from the live pricing page when the scanner is written, stamp them with the date, and leave any model whose rate cannot be sourced without a price. A row with real tokens and `--` for cost is correct; an invented rate is not.

**Whether `codex-auto-review` is billed to the user at all** is unknown. It runs automatically and is 49% of tokens, so if it is not separately billed the honest readout may be to show it with no price rather than to price it as a model.

**Scope of a scan.** The rate-limit reader deliberately looks at only the 12 most recently modified files inside an 8-day window. A ledger covering the existing 24h / 7d / month windows has to walk far more, which makes the incremental-offset machinery in `usage_scan.py` (stored byte offsets plus a tail digest to detect rewrites) worth reusing rather than reinventing.

## Notes for whoever builds it

The unexpected one: 3,071,635 tokens of `gpt-6-astra` on 2026-09-20 between 14:55 and 22:34, all in a single rollout file, alongside 1,364,631 tokens of `gpt-5.6-luna` the same day. Flagged because the standing instruction is to always pass `--model` explicitly and avoid `gpt-6-astra` on cost grounds, and something that day did not.
