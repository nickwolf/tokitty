# Per-model usage tracking

Status: built on `feat/per-model-usage`, 2026-09-08. See the implementation notes at the end for the three places the built version deviates from this plan.

## Why

Tokitty's two bars come from `api/oauth/usage`, which only answers for a Claude subscription. Someone on pay-as-you-go API billing gets `credentials_unreachable` or a `stale_token` pane forever: the widget is decorative to them. Every Claude Code install, subscription or not, writes a full token ledger to `<config_dir>/projects/**/*.jsonl`. Reading that ledger gives those users real numbers, and gives subscription users a per-model cost breakdown the endpoint does not expose.

The feature is a second *view* of the same pane, toggled from the menu. Nothing about the existing limits view changes.

## What the transcripts actually contain

Verified against 5,817 assistant entries in `~/.claude/projects` on 2026-09-08 (Claude Code 2.1.263). These four findings drive the whole design.

### 1. `iterations[]` is authoritative, top-level `usage` is not

An assistant entry's `message.usage` carries an `iterations` array when the turn made more than one API call. Top-level `usage` is **not** the sum: it omits iterations whose `type` is `advisor_message`. A real entry:

| field | top-level | sum(iterations) | per-iteration |
|---|---|---|---|
| `input_tokens` | 3 | 54,069 | `[1, 54066, 2]` |
| `output_tokens` | 961 | 8,084 | `[545, 7123, 416]` |
| `cache_read_input_tokens` | 102,413 | 102,413 | `[50069, 0, 52344]` |
| `cache_creation_input_tokens` | 3,859 | 3,859 | `[2275, 0, 1584]` |

`iter types: ['message', 'advisor_message', 'message']`. 46 of 1,495 sampled entries were multi-iteration, and all 46 disagreed with top-level. Costing from top-level would under-report those turns by roughly 94% of their input.

The advisor iteration is billed against a different model: the entry carries a sibling `advisorModel` field (`claude-opus-5` here). So the scanner attributes `type == "advisor_message"` iterations to `advisorModel` and everything else to `message.model`. When `iterations` is absent, top-level `usage` is the record.

### 2. Duplicates are the norm, and they are within one file

4,037 of 5,817 `message.id` values appear more than once. **Zero** span more than one file, so this is not the resumed-session duplication other tools guard against; Claude Code rewrites the row inside the same transcript. 4,024 duplicate groups carry byte-identical usage. 14 do not, and the later row is the complete one:

```
msg_011CeAPuEgxbKiq3VagsQcBS
  line 29  output_tokens=1    iterations=0
  line 30  output_tokens=167  iterations=1
```

Dedup key is `message.id`, **last occurrence wins**. First-wins would silently drop 99% of that turn.

### 3. Cache creation is split by TTL and the two prices differ

`usage.cache_creation` splits into `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. A 1h write costs 2x base input; a 5m write costs 1.25x. Real sessions use 1h heavily (34,248 of 34,248 in one sampled entry). Collapsing them into the flat `cache_creation_input_tokens` field overstates or understates by up to 60% of the write cost, so the scanner reads the split and falls back to the flat field as 5m only when the split is missing.

### 4. Scale is not a problem if mtime gates the scan

134 MB across the whole `projects` tree, but only 20 MB in 37 files touched in the last 7 days. A 7-day window never needs to open the other 97 MB.

Unrelated but load-bearing: project directory names are derived from the cwd, so `/mnt/c/Tools` becomes the directory `-mnt-c-Tools`, whose name starts with a dash. Anything shelling out or globbing has to tolerate that; `ls */*.jsonl` fails outright on it. The scanner uses `pathlib` throughout and never builds a shell argument.

## Scope

In:

- Credential-independent discovery of Claude Code config directories, so an account with transcripts but no OAuth login can be found and added at all.
- A new pure-Python scanner that turns one account's transcript tree into per-model token and cost totals over a selectable window.
- A bundled pricing table with per-model overrides.
- A second pane view showing per-model bars, toggled from the right-click and tray menus, persisted in `settings.json`.
- A selectable window (24 hours, 7 days, or the calendar month), a selectable readout (cost or tokens), and an optional per-account dollar budget that turns the bars into real percent-of-budget bars.
- The per-model view works with **no** network, no credentials, and no subscription.
- First-run onboarding: detect the no-subscription case and land that user in the working view instead of a permanently broken one, and explain in the Accounts dialog what tokitty actually found.

Out (worth doing later, not now):

- Per-project or per-session attribution.
- History beyond the selected window, charts, exports.
- Refreshing the pricing table over the network.

## Modules

### Credential-independent discovery (changes to `wsl_probe.py`, `manual_path.py`, `__main__.py`)

This has to come first, because without it the rest of the feature cannot reach a single byte of the data it is built on.

Every existing path to "where is this account's Claude Code" runs through an OAuth credentials file:

- `resolve_activity_sessions`'s no-`config_dir` Windows branch calls `find_wsl_credentials`, and returns `(None, None)` when it raises.
- `find_all_wsl_credentials`, which feeds the Accounts dialog's discovery rows, matches on `.credentials.json`.
- `validate_manual_path` rejects a directory outright unless `.credentials.json` is present **and** parses as OAuth (`_parses_as_oauth`), on both the WSL and local branches.

So the exact user this feature targets, an API-key user with no OAuth login, cannot be auto-discovered, cannot be added by hand, and would sit at `reading usage...` forever. Three changes:

1. `wsl_probe` gains `find_all_wsl_claude_dirs()`, matching on a `projects/` subdirectory rather than on credentials, sharing the same distro enumeration and the same `--exec sh -c` invocation form the credentials scan already uses. `find_all_wsl_credentials` is untouched; the limits view keeps its exact current behavior.
2. `validate_manual_path` stops returning a bare ok/error and starts reporting **what the directory can do**: `PathValidationResult` gains `capabilities: frozenset` drawn from `{"limits", "models"}`. Valid OAuth credentials grant `limits`; a non-empty `projects/` grants `models`. Empty capabilities is the only rejection. Existing callers that want the old semantics read `"limits" in result.capabilities`, so no current check is silently loosened.
3. `resolve_activity_sessions` is split into `resolve_config_root(config_dir)` plus the two leaf paths (`tokitty/sessions` and `projects`), so the sessions path and the projects path cannot drift, and its credential-free fallback is the discovery above rather than `(None, None)`.

An account whose capabilities are `{"models"}` only is a first-class account. Its pane defaults to the per-model view and its limits view shows the honest reason rather than an error look.

### `tokitty/pricing.py` (new, pure)

```python
@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float     # explicit, not a multiplier
    cache_write_5m_per_mtok: float
    cache_write_1h_per_mtok: float
```

Derived rows use the standard multipliers (read 0.1x input, 5m write 1.25x input, 1h write 2x input) but are written out literally, because the multipliers are not universal: Claude Fable 5.1 reads at a flat $0.25/MTok, which is 0.025x its $10 input, not 0.1x. A table of multipliers would silently misprice it.

Seed rows (first-party API rates, `$/MTok` input/output): `claude-fable-5-1` 10/50, `claude-fable-5` 10/50, `claude-opus-5` 5/25, `claude-opus-4-8` 5/25, `claude-opus-4-7` 5/25, `claude-opus-4-6` 5/25, `claude-sonnet-5` 2/10, `claude-sonnet-4-6` 3/15, `claude-haiku-4-5` 1/5.

`price_for(model_id)` does exact match, then a **narrow** snapshot match: the id with a trailing `-YYYYMMDD` stripped, and nothing else. Arbitrary longest-prefix matching is a trap, because a future `claude-opus-5-1` would silently inherit `claude-opus-5`'s prices and report a confidently wrong number instead of an honest unknown. A dated snapshot is a documented naming pattern; a version bump is not. A `None` price is not an error: the model's tokens are still counted and shown, its cost renders as `--`, and it is excluded from the dollar total. `<synthetic>` and any model id that is not a string are skipped entirely.

This table will go stale. It is a dated constant with the source noted in a comment, checked by a test that asserts the ids are the ones the app knows about rather than asserting prices are current, which no offline test can do.

### `tokitty/usage_scan.py` (new, pure except for reading files)

```python
@dataclass(frozen=True)
class ModelUsage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    cost_usd: Optional[float]      # None when the model is unpriced

@dataclass(frozen=True)
class UsageBreakdown:
    status: str                    # "ok" | "partial" | "unavailable"
    window: str                    # "24h" | "7d" | "month"
    window_start: datetime
    models: Tuple[ModelUsage, ...] # descending by the active readout, unpriced last
    total_cost_usd: float          # priced models only; a lower bound when unpriced_models is non-empty
    total_tokens: int
    unpriced_models: Tuple[str, ...]
    failed_files: int
    failed_rows: int
    scanned_at: datetime
```

**`status` is not decoration.** Without it, "the projects directory does not exist", "WSL is down", "every file raised an I/O error", and "you genuinely have not used Claude Code this week" are the same empty breakdown, and the pane would confidently report no usage when it actually failed to look. `unavailable` means the tree could not be read at all, `partial` means some files or rows failed, `ok` means a clean pass. **The "no usage in this window" message is only ever shown on `ok`.**

Every collection field is a tuple, and a snapshot is built fully detached and then published by swapping one reference. See the threading note under the watcher.

`iter_billing_records(path)` yields one record per (message id, iteration) with its own model, tokens, and timestamp. `window_start(window, now)` resolves the three window values, deriving the month boundary in local time. `aggregate(records, window, now)` folds the retained records into a breakdown. All three are pure over an iterable, so the tests are fixture files, not mocks, and switching windows is an `aggregate` call with no I/O behind it.

Parsing rules, in order:

1. Skip any line that is not JSON, and any entry whose `type` is not `"assistant"` or that has no `message.usage`. Corrupt trailing lines happen while Claude Code is mid-write; a bad line is skipped, never fatal.
2. Skip `isApiErrorMessage` entries.
3. **Keep** `isSidechain` entries. Subagent tokens are billed.
4. Key on `message.id`; fall back to `requestId`, then `uuid`. The dedup unit is the **whole occurrence**, not a single iteration: last occurrence in file order replaces the earlier one entirely. Storing per `(message_id, iteration_index)` instead would be a bug, because a corrected occurrence with one iteration replacing an earlier one with three leaves indices 1 and 2 stranded and still charged.
5. Iterations are expanded at aggregation time, from the winning occurrence. If `usage.iterations` is a non-empty list, emit one record per iteration: model is `advisorModel` for `type == "advisor_message"` and `message.model` otherwise. When an advisor iteration has no `advisorModel`, the model is **`<advisor-unknown>`** with unknown cost, not `message.model`. Falling back to `message.model` would attribute the advisor's tokens to a model that is known to potentially differ, which is exactly the mistake this whole section exists to avoid; an honest unknown is worth more than a confident wrong number. Otherwise emit one record from top-level `usage`.
6. Reconciling iterations against top level is **per field, not wholesale**. For each recognized counter: if the field appears in any iteration, the iteration sum wins (this is the `advisor_message` correction). If it appears only at top level, it is real spend that no iteration accounted for, and it is attributed to `message.model` when that is the only model in the occurrence, and to `<unattributed>` when the occurrence spans more than one model. Taking iterations wholesale silently drops such a field; adding top level on top of iterations double-counts.
7. Cache writes: prefer `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`; if that object is missing, treat the flat `cache_creation_input_tokens` as 5m.
8. Timestamp from the entry's `timestamp`, parsed like `api._parse_iso`. An unparseable timestamp drops the record rather than counting it into the wrong window.

Incremental scanning. `TranscriptScanner` holds per-file cursors and, crucially, keeps its store nested as `{path: {dedup_key: occurrence}}` rather than one flat map. The nesting is what makes a full re-read able to *remove* records: replacing that path's whole inner map drops occurrences that compaction or deletion took away, which a flat map keyed only by message id cannot express.

The cursor is `(file_id, size, mtime_ns, offset, tail_digest)`, where `file_id` is `(st_dev, st_ino)` when `st_ino` is non-zero and the path otherwise, since a `\\wsl.localhost` UNC stat can report zero, and `tail_digest` is a hash of the 256 bytes immediately preceding `offset`.

`(size, mtime, offset)` alone cannot establish what it needs to. A line rewritten in place at the same size, a file truncated and regrown past its old size between passes, a file replaced by a different file with the same size and mtime, and WSL/Windows mtime granularity or clock skew all defeat it, and the failure is silent: the scanner seeks into unrelated content and publishes a blend of two files. The rules are therefore:

- Files whose mtime predates the retention start are never opened.
- Re-read from zero when `file_id` changed, when size shrank, when mtime changed while size did not, or when `tail_digest` does not match the bytes before the stored offset. Only a clean match on all four continues from `offset`.
- **Commit the offset only through the last complete newline.** Claude Code appends while the scanner reads, so a pass routinely reaches EOF mid-line. Treating that fragment as a corrupt line and committing past it would permanently lose the record, since the remainder arrives after the new offset and is never revisited. The fragment is simply left unconsumed and re-read on the next pass. Buffering it instead was tried and is wrong: the fragment is still sitting in the file at the committed offset, so a buffer that gets prepended to the next read duplicates it into an unparseable line and loses the record anyway.
- `stat()` again after reading. If size or mtime moved during the read, discard the cursor update and re-read next pass rather than trusting a torn read.
- Records older than retention are pruned by record timestamp after replacement, not before, so a corrected occurrence that moves out of the window removes the earlier retained version instead of leaving it charged.

Retention is decoupled from display, and its start is `min(now - 24h, now - 7d, start_of_local_month) - margin`. "The calendar month is always the longest window" is false: on the 6th of a month, the trailing 7-day window reaches into the previous month while the month boundary does not, and assuming otherwise would prune records the displayed window still needs.

A cold scan now covers a month of retention rather than the 20 MB across 37 files a 7-day window would touch, so it is measured on the reference machine before merge. If it exceeds roughly a second the first paint shows "reading usage..." rather than blocking, which the watcher already does by construction: it runs off the Tk thread and `get_latest()` returns `None` until the first pass lands.

### `tokitty/usage_watcher.py` (new)

A daemon thread in the shape of `ActivityWatcher`: `start()`, `stop()`, `get_latest() -> Optional[UsageBreakdown]`, a rescan interval of 60s, and a `request_refresh()` wired into the existing "Refresh now". It never touches Tk. `tick()` reads `get_latest()` exactly like it reads `watcher.get_latest()` today.

**It must copy `ActivityWatcher`'s running-distro gate, not just its shape.** `_tick_once` probes `list_running_distros_fn` before touching a `\\wsl.localhost` path and bails to an idle publish when the distro is stopped, with the comment that touching the UNC path silently boots the distro and defeats WSL's idle auto-shutdown. A 60-second scanner that skipped that check would boot a stopped distro and then hold it awake indefinitely, which is a worse bug than the one it fixes because the user has no way to connect it to tokitty. Same `list_running_distros_fn` injection, same one-probe-per-tick caching, and a stopped distro publishes `status="unavailable"` rather than an empty `ok`.

Threading. The record store stays worker-owned and the lock is never held while scanning. Each pass builds a fully detached `UsageBreakdown` of immutable tuples and publishes it by swapping one reference under the lock, exactly as `ActivityWatcher._publish` does. The Tk thread can then hold an older snapshot across ticks with no tearing and no lifetime question; the size of the record dict behind it is irrelevant because the dict is never handed out.

Resolving each account's transcript directory goes through the shared `resolve_config_root(config_dir)` introduced above.

## Settings and menus

Four new `Settings` fields, each validated with the same degrade-to-default handling `opacity` already uses, so a hand-edited or older `settings.json` can never crash the app:

| field | default | values |
|---|---|---|
| `view_mode` | `"limits"` | `"limits"`, `"models"` |
| `usage_window` | `"7d"` | `"24h"`, `"7d"`, `"month"` |
| `usage_readout` | `"cost"` | `"cost"`, `"tokens"` |
| `usage_budgets` | `{}` | identity slug -> window -> positive float |
| `onboarding_version` | `0` | int, highest onboarding step already run |

`view_mode`, `usage_window`, and `usage_readout` are app-level, not per-account. The panes are one card, and a card whose panes disagreed about what window they were showing would read as a bug rather than a feature.

`usage_budgets` **is** per-account, because a budget is a fact about one billing account. It is keyed by the same identity slug `customization.json` uses (`customization_key`), so a removed and re-added account recovers its budget exactly the way it recovers its colorway. It lives in `settings.json` rather than `customization.json` because that file is explicitly the per-account *look*. An absent key means no budget, which is a different state from a budget of zero: zero is rejected by the dialog.

It is also keyed **per window**, `{slug: {window: amount}}`. A single scalar has no coherent meaning across three windows: a user who sets $40 while viewing the month and then switches to 24h would be shown a comfortable green bar against a denominator that is now a daily budget, which is worse than showing no budget at all. $40 a month and $40 a day are different statements and the app should be able to hold both. The dialog is labelled with the window it is setting, and switching windows switches which budget applies.

A dollar budget is ignored entirely when `usage_readout` is `tokens`; comparing a token count to a dollar figure is meaningless, so that combination falls back to share-of-total bars.

`build_menu` gains optional getter/setter pairs for each. Per `menu.py`'s contract every getter reads plain-Python shadow state, never a Tk var, because pystray evaluates them on its own thread. Each group appears only when both halves are supplied, matching how `on_toggle_tray`/`tray_enabled` already gate each other:

- **View** submenu, radios: Limits / Per-model. Placed next to Transparency. Radios rather than a checkbox so a third view later does not force a rewrite.
- **Usage window** submenu, radios: Last 24 hours / Last 7 days / This month.
- **Usage readout** submenu, radios: Cost / Tokens.
- **Set budget...** item, opening a `simpledialog.askstring` on the pane whose menu was used (`_menu_pane_index`, which the customization actions already use to find their account), labelled with the window currently selected. `askstring`, not `askfloat`: `askfloat` cannot distinguish a cancel from a submitted blank (it returns `None` for the first and rejects the second with its own validation error), and clearing a budget has to be expressible. Cancel returns `None` and leaves the budget untouched; a submitted blank clears it; a value that does not parse, or parses to <= 0, is rejected with a message and the dialog stays open. The item is not in the tray menu, since the tray has no pane context.

Only **Set budget...** joins `_PANE_SPECIFIC_LABELS`. View, window, and readout are global and belong in the tray menu as well.

## Onboarding

Today a pay-as-you-go user's first launch is the worst experience the app has. `should_auto_open` only fires when `accounts.json` is absent **and** async WSL discovery found more than one credential source, so a single-install API-key user never sees a dialog at all: they get `can't find credentials`, a confused cat, and nothing on screen suggesting the widget could work for them. That user is exactly who this feature is for, so onboarding is part of the feature rather than a follow-up.

Three changes, smallest first.

### 1. Auto-select the working view when the endpoint is provably unusable

`credentials_unreachable` with local transcripts present is an unambiguous signature: no OAuth credential resolves anywhere in `credentials.py`'s precedence order, and yet Claude Code has been writing billing records. That is an API-key user. A subscription account cannot produce it, because a subscription account has credentials by definition. This arm depends entirely on the credential-independent discovery above; without it there are never transcripts to find and the condition can never be true.

When that holds and `onboarding_version` is 0, tokitty sets `view_mode` to `models` once, bumps `onboarding_version`, and shows `showing per-model usage` in the status line for the first minute. Afterward it is an ordinary persisted setting, changeable from the View menu like any other, and never re-applied.

The narrowness is deliberate. Three states that look similar must **not** trigger it:

- `stale_token` with no capped binding is the documented **resting look**: a work account's token expires about an hour after that account's Claude Code last ran, so this is the pane's normal overnight state for a subscriber. Switching their view because they stopped working at 6pm would be a bug.
- `api_error` is transient by construction and already has backoff behind it.
- `keychain_denied` is a permission the user can still grant. It stays sticky-blocked until "Refresh now", so its recovery hint has to remain on screen.

Also required: a scan that actually succeeded (`status == "ok"` or `"partial"`) with at least one billing record in it. An `unavailable` scan must not trigger the switch, since "we could not read the transcripts" is not evidence of anything, and switching on it would trade one broken pane for a differently broken one.

An account whose discovered capabilities are `{"models"}` with no `limits` skips the heuristic entirely and simply starts in the per-model view, because there is nothing to guess about.

### 2. Open the setup dialog for the case that currently gets nothing

`should_auto_open` becomes `resolve_first_run_action(...) -> Optional[str]`, returning `None`, `"accounts"`, or `"usage_setup"`, keeping the existing pure-and-injectable shape so the gui-marked tests still never touch WSL. The existing multi-install condition keeps returning `"accounts"`, unchanged. The new arm returns `"usage_setup"` when `accounts.json` is absent, no credential source resolves at all, and the credential-independent discovery found transcripts: precisely the user who today sees only an error.

`"usage_setup"` opens the same `AccountsManager` dialog, scrolled to the usage section described below, rather than a separate wizard. A one-shot modal on first launch would cut against the way the rest of the app works: no installer, no admin rights, every feature off by default and opted into from a menu.

### 3. Make the Accounts dialog say what tokitty found

The dialog is already the de facto onboarding surface, and it currently only lists paths. Each account row gains two derived facts, computed from state the app already holds rather than from new probes:

```
Cat 1   \\wsl.localhost\Ubuntu\home\nick\.claude
        subscription usage: not available (no credentials found)
        local usage: 3 models, 11 days of history
```

Below the rows, a usage section carries the same three global controls the menu does (view, window, readout) plus a per-account budget field for the selected window, so a user who never opens a right-click menu still finds them. It writes through the same `Settings` helpers the menu uses: no second code path, no second source of truth.

The dialog's add-by-path flow follows `validate_manual_path`'s new capability result rather than its old boolean. A directory with transcripts and no OAuth credentials is accepted, and the row says what it can do instead of refusing with "not a valid Claude Code credentials file", which is the message that currently turns this exact user away at the door.

That wording is also the honest one for the target user. `not available (no credentials found)` sitting next to a populated `local usage` line tells the whole story without calling anything broken.

### Supporting change: `--debug-print`

`--debug-print` gains the per-model breakdown per account. It is the first thing to ask for when someone reports wrong numbers, and it works on a machine with no GUI toolkit installed:

```
- account 1 (\\wsl.localhost\Ubuntu\home\nick\.claude)
  status: credentials_unreachable
  usage (7d, 37 files scanned, 20.1 MB):
    claude-opus-5     12.4M tok   $38.20
    claude-sonnet-5    3.1M tok   $ 4.85
    claude-haiku-4-5   0.9M tok   $ 0.31
    total                         $43.36 at API rates
```

## Pane layout in per-model view

The pane stays 128px. Growing it would change `grid_size`, the root geometry, and the keyed content window's frame placement on Windows, which is the riskiest surface in the app and buys nothing here.

Three model rows fit where two limit rows sit now, using the same `STATS_X`/`BAR_WIDTH` column:

**Three rows, and the third is `other` when there are more than three models.** Not three models *plus* an `other` row: that is four rows, and there is no fourth slot. The tradeoff is deliberate, since two named models plus a labelled remainder tells the truth, while a silently dropped fourth model does not.

```
no budget set                            budget set ($40, month)
y=20   opus-5             $12.40         opus-5              $12.40
y=34   [###########.........]            [######..............]
y=48   sonnet-5            $4.85         sonnet-5             $4.85
y=62   [####................]            [##..................]
y=76   other (2)           $0.31         other (2)            $0.31
y=90   [.....................]           [....................]
y=108  7d - $17.56 at API rates          Sep - $17.56 of $40 - 44%
```

Row 1 starts at y=20, not y=8. The pane's account label is already anchored top-right at y=4, and an 8pt label occupies roughly 13px, so a figure at y=8 in the same right-aligned column would sit underneath it. The bottom row's bar ends at y=98, clearing the status line at y=108.

The totals line **is** the existing status line, not a new label at y=96. A new label there would collide with the status label at y=108 under Windows DPI scaling, and the status line is free in this view anyway, since poll hints are suppressed here by design. It joins `resolve_status_text`'s precedence and gets an explicit fitting rule: the window label and the figure are never truncated, and the trailing `at API rates` is dropped before anything else if the string exceeds the 158px column.

The name is the display form (`opus-5`, not `claude-opus-5`), truncated with the existing `fit_tag`. With `usage_readout` set to `tokens` the figures become `12.4M` / `3.1M` / `0.9M` and the line reads `7d - 16.4M tokens`.

**Bar denominator.**

- **No budget (default): share of the window total for the active readout**, folding `other` before computing any share. Share-of-*largest* was the first draft and is wrong twice over: once `other` aggregates several models it can exceed the largest single model and clamp at over 100%, and cost ranking and token ranking are not the same ranking, so the top row is not the same row in both readouts. Share of total is a real composition, always sums to 100%, survives the fold, and needs no user setting. Bars follow whichever readout is on screen, so switching readouts **does** change the widths.
- **Budget set (cost readout only): percent of that window's budget**, every row against the same denominator, with `bar_color(pct)` restored to its real meaning and driven by the **total** rather than per row. A per-row ramp would paint a $30-of-$40 top row red while the account is comfortably inside budget. Over 100% the bars clamp the way `render` already clamps `min(pct, 100)` while the text keeps the true percentage.
- **Any displayed model is unpriced:** the total renders as `>= $17.56` and the budget percentage is **suppressed entirely**. A budget bar computed from a partial total is a reassuring number with a hole in it, which is the one thing worse than no number.

Widgets are created once in `_build_widgets` and shown or hidden per view with `place`/`place_forget`, not rebuilt, because `set_appearance` has to be able to restyle either view without knowing which is up.

`Pane.render` keeps its current signature and gains `view_mode` plus `breakdown: Optional[UsageBreakdown]`, both defaulted, so every existing caller and test keeps working.

## Behavior when there is no subscription

The rule is about **failed** poll results, not about the poller: **in per-model view, no failed `PollResult` reaches the pane.** No `dimmed`, no `confused` cat, no "token expired" hint, regardless of what the poller is doing.

The one thing that is *not* suppressed is a **confirmed cap from a fresh successful poll**. Hiding that would be its own bug: a subscriber who toggles into this view while genuinely capped would see a cat happily working against a wall, and the app's whole capped/stirring/waking vocabulary exists to prevent exactly that confusion. So a `PollResult(status="ok")` carrying an active capped binding keeps the capped pose and its driving tag; everything else about the pane still comes from the scanner. Absent that, the cat's mood comes from live activity, so it still thinks and works and hops.

Scanner states, driven by `UsageBreakdown.status`:

- No snapshot yet: `reading usage...`.
- `unavailable`: `can't read usage` plus the reason when there is a short one (`WSL not running`, `no transcripts found`). Never silently rendered as zero.
- `partial`: rows render, and the line carries a `partial` marker so numbers known to be incomplete are never presented as final.
- `ok` with an empty window: rows blank, line reads `no usage in the last 7 days` / `in the last 24 hours` / `this month`, following the selected window. Not an error look. **This message is reachable only from `ok`**, which is the entire reason `status` exists.

The poller keeps running in per-model view. Switching back must be instant, and a subscription user in this view still has a valid session countdown behind the toggle.

## Windows

Three, selectable, defaulting to 7 days:

| value | meaning | label |
|---|---|---|
| `24h` | trailing 24 hours from now | `24h` |
| `7d` | trailing 7 days from now | `7d` |
| `month` | since 00:00 local on the 1st of the current month | `Sep` |

Trailing 7 days is the default because it lines up with the existing WEEK bar, so toggling between the two views compares like with like. Calendar month exists because it is the window a pay-as-you-go user is actually billed on, and a trailing-7-day figure cannot be reconciled against an invoice. 24 hours exists because it is the one that moves fast enough to watch while working.

The month boundary is computed in **local** time, not UTC, since that is how the user reads a calendar; transcript timestamps are UTC and are converted before comparison. A user in UTC-6 would otherwise see the first six hours of the month attributed to the previous one.

The scanner's retention start is `min(now - 24h, now - 7d, start_of_local_month) - margin`, computed fresh each pass. It is deliberately not "the calendar month, which is the longest": early in a month the trailing 7-day window reaches further back than the 1st does, and retaining only to the 1st would prune records the 7d view still needs and quietly undercount it. Aggregation then filters the retained set per window, which makes switching windows instant and purely arithmetic: no I/O, no rescan, no visible gap. The cost is that the mtime gate opens roughly a month of transcripts rather than a week (the worst case measured here is 134 MB total, and a month is a fraction of that), paid once at startup and incrementally after.

A 5h window is not offered. It is a subscription concept anchored to a reset event that only the endpoint knows, and a trailing 5 hours would look like the SESSION bar while meaning something different.

## Tests

All new logic is pure and testable without tkinter, matching how `display.py` and `mood.py` are already tested.

- `test_pricing.py`: exact resolution; a `-YYYYMMDD` snapshot resolves to its base; a version-bumped id such as `claude-opus-5-1` resolves to `None` rather than inheriting `claude-opus-5`; Fable's non-multiplier cache read is priced from its own row.
- Discovery: `find_all_wsl_claude_dirs` matches a dir with `projects/` and no credentials; `validate_manual_path` returns `{"models"}` for that dir, `{"limits", "models"}` for a full one, `{"limits"}` for credentials with no transcripts, and rejects only on an empty set; every existing caller's behavior is pinned via `"limits" in capabilities`.
- `test_usage_scan.py`, on fixture JSONL files carrying the real shapes above: multi-iteration entry attributes the advisor iteration to `advisorModel` and does not use top-level usage; duplicate `message.id` resolves last-wins including the differing-usage case; sidechain counted; API-error entry skipped; corrupt line skipped without raising; 5m/1h split honored and the flat-field fallback treated as 5m; window boundary excludes an entry one second early; unpriced model counted in tokens and excluded from the dollar total.
- Incremental scan: append to a fixture and assert only the new bytes are read; truncate it and assert a full re-read; a corrected duplicate row arriving in a later pass replaces the earlier record, including the case where the corrected occurrence has fewer iterations than the one it replaces.
- Cursor invalidation: same-size in-place rewrite with a bumped mtime forces a re-read; same size and same mtime with a mismatched `tail_digest` forces a re-read; a truncate-and-regrow past the old size forces a re-read; a changed `file_id` forces a re-read.
- Mid-line append: write a fixture ending in half a JSON line, scan, then append the remainder; assert the completed record is counted on the second pass and the offset never advanced past the fragment.
- Torn read: mutate the file between the read and the post-read `stat`; assert the cursor is not committed and the next pass re-reads.
- Scan status: a missing projects dir yields `unavailable`; one unreadable file among several yields `partial` with `failed_files == 1`; a clean empty window yields `ok`; the "no usage" message is unreachable from `unavailable`.
- Per-field reconciliation: a counter present only at top level is attributed to `message.model` for a single-model occurrence and to `<unattributed>` for a multi-model one; an advisor iteration with no `advisorModel` becomes `<advisor-unknown>` and is excluded from the dollar total.
- Retention: on the 6th of a month, retention start is the 7-day boundary and not the 1st.
- Distro gate: a stopped distro publishes `unavailable` and the transcript directory is never touched.
- Windows: `24h`, `7d`, and `month` boundaries computed from a fixed `now`; the month boundary derived in local time, asserted with a UTC-6 fixture where a UTC-derived boundary would give a different answer; switching windows re-aggregates the same retained records without reopening a file.
- `test_settings.py`: all four new fields round-trip; each unknown or wrong-typed value degrades to its default independently; `usage_budgets` survives an unrelated key, drops a non-numeric or non-positive entry, and a missing key is distinguishable from a zero.
- `test_startup.py`: `resolve_first_run_action` returns `"accounts"` for every input that made the old `should_auto_open` return True and `None` for every input that made it return False, pinning the existing behavior; returns `"usage_setup"` only for absent accounts plus no resolvable credential plus transcripts found.
- Auto-select: fires for `credentials_unreachable` with records present and `onboarding_version` at 0; does **not** fire for `stale_token` with no binding, for `api_error`, for `keychain_denied`, for zero records, or when `onboarding_version` is already bumped; fires at most once across consecutive ticks.
- `test_accounts_ui.py`: row specs carry the subscription-available and local-usage facts; the usage section writes through the same `Settings` helpers the menu uses.
- `test_menu.py`: each new submenu appears only when both its getter and setter are supplied; radios reflect state; `Set budget...` is pane-specific and the other three are not.
- `test_ui_layout.py`: per-model widgets hidden in limits view and vice versa; a fourth model folds into `other`; totals row matches the sum; shares are of the window total and sum to 100% after `other` is folded; a folded `other` larger than the biggest named model does not exceed full width; with a budget set every row shares the denominator, the ramp is driven by the total rather than per row, and a total over 100% clamps the bar while the text keeps the real number; the `tokens` readout changes both the figures and the bar widths, and ignores a dollar budget; an unpriced model turns the total into `>=` form and suppresses the budget percentage.
- Layout collisions: row 1's figure does not overlap the account label at y=4; the bottom bar clears the status line; the totals string drops `at API rates` before overflowing the 158px column.

## Docs

README gets a section next to Accounts describing the view and stating plainly that it works without a subscription. The design doc is this file. `docs/ROADMAP.md` is not edited: it deliberately links the board instead of listing items.

## Resolved design questions

These were open in the first draft and are now decided, each toward the more flexible option rather than the cheaper one.

1. **Bar denominator: both.** Share-of-largest with no budget, percent-of-budget when one is set, budget per account. Picking one would have forced a bad default on half the audience: share-of-largest tells a subscriber which model is eating their week, and percent-of-budget tells a pay-as-you-go user the thing they actually opened the widget for.
2. **Window: all three, selectable.** 24h, 7d, and calendar month, with the retention window always sized to the largest so switching is instant. The invoice-shaped window and the comparable-to-WEEK window are both legitimate, and they are cheap to hold simultaneously once retention is decoupled from display.
3. **Dollar wording: labelled, and skippable.** With no budget the totals line ends `at API rates`, which is true for everyone and does not read as a bill to a subscriber whose tokens cost them nothing marginal. With a budget the phrase is dropped, because a user who typed a dollar figure has already told us they are spending dollars. Anyone who wants no dollar figure at all switches the readout to `tokens`, which is also the honest mode for a subscriber and the one to suggest in the README.

## Adversarial review, 2026-09-08

Reviewed cold by gpt-5.6-sol at high effort, read-only, against the repo and the first revision of this document. Thirteen ranked failure modes; the two it called critical were both real and both verified against the code before acting:

- **Discovery was credential-gated end to end.** `resolve_activity_sessions`'s Windows branch returns `(None, None)` when `find_wsl_credentials` raises, and `validate_manual_path` rejects any directory whose `.credentials.json` is missing or does not parse as OAuth. Verified in `manual_path.py:50-57` and `__main__.py:100`. The feature could not have reached its target user's data at all, and the Accounts dialog would have turned them away with "not a valid Claude Code credentials file". This is why credential-independent discovery is now the first module in the plan rather than an afterthought.
- **Mid-line append lost records permanently.** Combining "skip corrupt lines" with "commit the new offset" means a half-written trailing line is skipped and then never revisited, because the remainder lands behind the committed offset.

Also adopted: file identity plus tail fingerprint instead of `(size, mtime, offset)`; the corrected retention formula; whole-occurrence dedup with per-file nesting; an explicit scan status; the running-distro gate (verified at `activity_watcher.py:119`); per-window budgets; `askstring` over `askfloat`; per-field iteration reconciliation and `<advisor-unknown>`; snapshot-suffix-only price matching with `>=` totals; share-of-total instead of share-of-largest; and the layout fixes for the y=4 account label and the missing fourth row.

Not adopted as stated: nothing. The review's threading verdict matched the design's intent and is now written down explicitly rather than left implied.

## Implementation notes

Three things changed during the build, each because the code said so.

**The trailing fragment is not buffered.** The plan said to hold the incomplete last line in a buffer and prepend it to the next pass. That is wrong and the test caught it: the fragment is still sitting in the file at the committed offset, so prepending a buffered copy produces `{"type"...{"type"...`, an unparseable line, and the record is lost anyway plus a spurious failed row. The scanner simply commits the offset through the last complete newline and re-reads the fragment next pass. Same guarantee, less state.

**Normal lines are not failures.** The first cut counted every `parse_entry` returning None as a failed row, which meant every user turn in every transcript. Measured on the reference machine: 6,577 "failures" on a completely healthy tree, which would have pinned every install at `partial` forever and made the status field worthless. `classify_line` now separates "not a billing entry" (normal, silent) from "is a billing entry and could not be used" (counted). After the fix the same tree scans `ok`, 0 failed files, 0 failed rows, $110.85 across 119.3M tokens over 7 days, in 0.27s cold.

**`display_name` drops the snapshot date too.** Real transcripts carry `claude-haiku-4-5-20251001`, which rendered as `haiku-4-5-202…` and spent the entire row on a truncated date. It is the same model at the same price, so the suffix comes off for display as well as for pricing.

`should_auto_open` was deleted rather than kept as a wrapper. Once `resolve_first_run_action` became the gate `run_gui` consults, the wrapper had no callers, and its tests now assert the same pinned behaviour through the live seam.
