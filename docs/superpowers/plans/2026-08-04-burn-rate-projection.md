# Burn-Rate Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a single line on each pane predicting when the current usage pace will hit a cap — `session caps ~6:20 PM` — and show nothing when no cap is coming.

**Architecture:** A new pure module `tokitty/burn.py` holds a per-account rolling sample buffer and the rate math. `display.py` gains the two string helpers. `ui.py` gains one optional `render` kwarg and multiplexes the existing status line. `__main__.py` owns one `BurnTracker` per account unit and merges the projection into the display dict in `tick()`. `_display_from_snapshot` stays pure and single-snapshot — burn rate is inherently historical and does not belong inside it.

**Tech Stack:** Python 3.10+ stdlib only (`dataclasses`, `datetime`). pytest. No new dependencies.

## Global Constraints

- **No new runtime dependencies.** stdlib only. `pystray`/`Pillow` remain the only pip deps and are tray-only.
- **`burn.py` must not import tkinter** — same rule as `display.py`, `geometry.py`, `mood.py`. It must be unit-testable on a machine with no GUI toolkit.
- **No new test may construct `tk.Tk()`.** Every test in this plan is headless and unmarked. If you find yourself needing a real display, you have put logic in the wrong module — move it to a pure helper. (A `tk.Tk()` test without `@pytest.mark.gui` reddens the whole CI matrix; this was caught in preflight on #21.)
- **The status string must fit 160px on ONE line at Segoe UI 8.** `status_label` has `wraplength=CARD_WIDTH - STATS_X - 8` = 160px and sits at y=108 in a 128px pane. A second wrapped line renders below the pane edge and is clipped. Do not lengthen the strings specified in Task 3.
- **No new files in the state dir.** Samples are memory-only and per-account. A restart re-enters warm-up; that is intended.
- **Timezone:** all internal math in UTC-aware `datetime`; only the formatters call `.astimezone()`.
- Run the full suite with plain `pytest` (the `gui` marker is deselected by default via `addopts`).

## Context — why this design (read before Task 1)

Four findings from the design session that are **not** in issue #25 and are not obvious from reading the code:

1. **`tick()` runs every 500ms (`UI_REFRESH_MS`), but the poller only fetches every 120s** (20s when waking). Worse, on a *failed* poll `_display_state_for` re-renders from `previous.snapshot` — the **same object**. Sampling naively in the render path would ingest one snapshot hundreds of times and report a fake-flat burn rate. Hence the `fetched_at` dedupe in Task 1; it rejects the cached re-render for free.

2. **Non-uniform sample spacing.** Because the poll interval switches between 120s and 20s, "the last N samples" spans anywhere from 100s to 600s. So the buffer is **time-windowed** and the rate is computed first-to-last across that window.

3. **The window is the decay time, and Nick chose 10 minutes.** This is the subtle one. With a first-to-last rate, the projection keeps reporting a burn until every burning sample has aged out — so the window length is exactly how long a stale projection lingers after you stop working, and also how long it takes to notice a new burst. Anchoring `caps_at` to the newest sample's timestamp means it slides *forward* while you are idle, so the `caps_at <= now` guard does **not** clear it either; only ageing out does. `WINDOW_SECONDS = 600` was chosen for responsiveness over smoothness, accepting that the estimate wobbles poll to poll. Do not lengthen it to reduce wobble, and do not add recency-weighting or least-squares to try to get both.

4. **The issue's own phrasing does not fit.** "At this pace, capped by 11:40 PM" measures 196px — 36px over the ceiling. The approved strings are `session caps ~6:20 PM` (131px) and `week caps ~Wed 11:40 PM` (154px worst case). Those widths were measured under a WSL fallback font, which is **wider** than real Segoe UI, so they are conservative — but the Windows manual gate must still confirm no wrap.

Approved product decisions (do not re-litigate):

- **Placement:** reuse the existing `status_label`, priority `hint > credits > projection`. Zero pixels, no `PANE_HEIGHT` change, no `position.json` risk.
- **Which limit:** whichever of session/weekly is projected to cap **first**, and name it. (Live evidence: the work account sits at 95% weekly with a near-idle session — session-only would render silently useless there.)
- **Quiet state:** show **nothing** when no cap is projected. The line's appearance is itself the signal.

---

### Task 1: Sample buffer — `BurnTracker.add`

**Files:**
- Create: `tokitty/burn.py`
- Test: `tests/test_burn.py`

**Interfaces:**
- Consumes: `tokitty.api.UsageSnapshot` (fields `session_pct`, `weekly_pct`, `session_resets_at`, `weekly_resets_at`, `limits`, `fetched_at`), `tokitty.mood.select_binding_capped_limit`.
- Produces: `Sample` (frozen dataclass), `BurnTracker(window_seconds: float = WINDOW_SECONDS)`, `BurnTracker.add(snapshot: UsageSnapshot) -> None`, `BurnTracker.samples` (read-only property returning `List[Sample]`), constants `WINDOW_SECONDS`, `MIN_SAMPLES`, `MIN_SPAN_SECONDS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_burn.py`:

```python
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tokitty.api import LimitInfo, UsageSnapshot
from tokitty.burn import BurnTracker, Sample

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
SESSION_RESET = NOW + timedelta(hours=3)
WEEKLY_RESET = NOW + timedelta(days=4)


def _snapshot(offset_seconds=0, session_pct=10.0, weekly_pct=5.0,
              session_resets_at=SESSION_RESET, weekly_resets_at=WEEKLY_RESET,
              limits=()):
    return UsageSnapshot(
        session_pct=session_pct,
        session_resets_at=session_resets_at,
        weekly_pct=weekly_pct,
        weekly_resets_at=weekly_resets_at,
        limits=list(limits),
        fetched_at=NOW + timedelta(seconds=offset_seconds),
    )


def test_add_records_a_sample():
    tracker = BurnTracker()
    tracker.add(_snapshot(session_pct=12.0))
    assert len(tracker.samples) == 1
    assert tracker.samples[0].session_pct == 12.0


def test_add_ignores_a_repeated_snapshot_with_the_same_fetched_at():
    """The render path re-uses the cached snapshot object every 500ms on a
    failed poll -- ingesting it repeatedly would fake a flat burn rate."""
    tracker = BurnTracker()
    snapshot = _snapshot()
    for _ in range(50):
        tracker.add(snapshot)
    assert len(tracker.samples) == 1


def test_add_ignores_an_out_of_order_snapshot():
    tracker = BurnTracker()
    tracker.add(_snapshot(offset_seconds=300))
    tracker.add(_snapshot(offset_seconds=120))
    assert len(tracker.samples) == 1


def test_add_drops_samples_older_than_the_window():
    tracker = BurnTracker(window_seconds=600)
    tracker.add(_snapshot(offset_seconds=0))
    tracker.add(_snapshot(offset_seconds=300))
    tracker.add(_snapshot(offset_seconds=900))
    assert [s.fetched_at for s in tracker.samples] == [
        NOW + timedelta(seconds=300),
        NOW + timedelta(seconds=900),
    ]


def test_add_records_whether_the_account_is_currently_capped():
    tracker = BurnTracker()
    capped = LimitInfo(kind="session", percent=100.0, severity="exceeded",
                       resets_at=SESSION_RESET, is_active=True)
    tracker.add(_snapshot(limits=[capped]))
    assert tracker.samples[0].capped is True


def test_samples_is_a_copy_not_the_internal_list():
    tracker = BurnTracker()
    tracker.add(_snapshot())
    tracker.samples.clear()
    assert len(tracker.samples) == 1


def test_sample_is_frozen():
    tracker = BurnTracker()
    tracker.add(_snapshot())
    sample = tracker.samples[0]
    assert isinstance(sample, Sample)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.session_pct = 99.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_burn.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tokitty.burn'`

- [ ] **Step 3: Write the minimal implementation**

Create `tokitty/burn.py`:

```python
"""Rolling burn-rate samples and cap projection.

Pure logic -- no tkinter, no I/O -- so it is unit-testable without a GUI
toolkit installed, the same rule display.py and geometry.py follow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from tokitty.api import UsageSnapshot
from tokitty.mood import select_binding_capped_limit

# Trailing time window the rate is measured over. Time-based rather than
# last-N because the poll interval switches between 120s and 20s (waking),
# so a fixed sample count would span wildly different durations.
#
# 10 minutes is a deliberate responsiveness choice, not a smoothing one:
# the window IS the decay time. A first-to-last rate keeps reporting a
# burn until every burning sample ages out, so a longer window means a
# projection that lingers that long after you stop -- and that takes just
# as long to notice a new burst. At 600s the estimate visibly wobbles
# poll to poll; that is the accepted trade.
WINDOW_SECONDS = 10 * 60
MIN_SAMPLES = 2
MIN_SPAN_SECONDS = 5 * 60


@dataclass(frozen=True)
class Sample:
    fetched_at: datetime
    session_pct: float
    weekly_pct: float
    session_resets_at: Optional[datetime]
    weekly_resets_at: Optional[datetime]
    capped: bool


class BurnTracker:
    """A per-account rolling buffer of usage samples. Memory-only by
    design: a restart re-enters warm-up rather than adding a file to the
    state dir."""

    def __init__(self, window_seconds: float = WINDOW_SECONDS):
        self._window_seconds = window_seconds
        self._samples: List[Sample] = []

    @property
    def samples(self) -> List[Sample]:
        return list(self._samples)

    def add(self, snapshot: UsageSnapshot) -> None:
        # tick() runs every 500ms and re-renders the *cached* snapshot
        # object whenever a poll fails, so the same fetched_at arrives
        # hundreds of times. Rejecting anything not strictly newer both
        # dedupes that and guards against out-of-order arrival.
        if self._samples and snapshot.fetched_at <= self._samples[-1].fetched_at:
            return

        self._samples.append(
            Sample(
                fetched_at=snapshot.fetched_at,
                session_pct=snapshot.session_pct,
                weekly_pct=snapshot.weekly_pct,
                session_resets_at=snapshot.session_resets_at,
                weekly_resets_at=snapshot.weekly_resets_at,
                capped=select_binding_capped_limit(snapshot.limits) is not None,
            )
        )

        cutoff = snapshot.fetched_at - timedelta(seconds=self._window_seconds)
        self._samples = [s for s in self._samples if s.fetched_at >= cutoff]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_burn.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tokitty/burn.py tests/test_burn.py
git commit -m "feat(burn): rolling usage sample buffer with fetched_at dedupe"
```

---

### Task 2: Rate math and suppression — `BurnTracker.project`

**Files:**
- Modify: `tokitty/burn.py`
- Test: `tests/test_burn.py`

**Interfaces:**
- Consumes: `Sample`, `BurnTracker._samples` from Task 1.
- Produces: `Projection` (frozen dataclass with `kind: str` — `"session"` or `"weekly"` — and `caps_at: datetime`), and `BurnTracker.project(now: datetime) -> Optional[Projection]`.

**The five suppression rules.** `project` returns `None` when any of these holds. Every one has a test below; do not collapse them.

| Rule | Why |
|---|---|
| fewer than `MIN_SAMPLES`, or span < `MIN_SPAN_SECONDS` | warm-up; early estimates are wild. Also the divide-by-zero guard — a zero elapsed is always < 300s. |
| rate ≤ 0 | flat or just reset; no cap is coming |
| projected `caps_at` ≥ that limit's `resets_at` | you will coast to reset |
| the newest sample was already capped | a projection is meaningless once you are capped |
| `caps_at` ≤ `now` | the estimate has been overtaken by events |

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_burn.py`:

```python
from tokitty.burn import MIN_SPAN_SECONDS, WINDOW_SECONDS, Projection


def _tracker_with(points, **snapshot_kwargs):
    """points = [(offset_seconds, session_pct, weekly_pct), ...]"""
    tracker = BurnTracker()
    for offset, session_pct, weekly_pct in points:
        tracker.add(_snapshot(offset_seconds=offset, session_pct=session_pct,
                              weekly_pct=weekly_pct, **snapshot_kwargs))
    return tracker


def test_project_returns_none_with_a_single_sample():
    tracker = _tracker_with([(0, 10.0, 5.0)])
    assert tracker.project(NOW) is None


def test_project_returns_none_before_the_minimum_span():
    tracker = _tracker_with([(0, 10.0, 5.0), (60, 20.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=60)) is None


def test_project_extrapolates_the_session_cap_from_the_measured_rate():
    # 10% -> 40% over 600s = 0.05 %/s. 60% remains -> 1200s past the last
    # sample, i.e. 30 minutes after NOW+600.
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 40.0, 5.0)])
    projection = tracker.project(NOW + timedelta(seconds=600))
    assert projection == Projection(
        kind="session", caps_at=NOW + timedelta(seconds=1800)
    )


def test_project_returns_none_when_the_rate_is_flat():
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 10.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_usage_went_down():
    tracker = _tracker_with([(0, 40.0, 5.0), (600, 10.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_the_cap_lands_after_the_reset():
    # 10% -> 11% over 600s is slow enough to coast past SESSION_RESET (+3h).
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 11.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_already_capped():
    capped = LimitInfo(kind="session", percent=100.0, severity="exceeded",
                       resets_at=SESSION_RESET, is_active=True)
    tracker = BurnTracker()
    tracker.add(_snapshot(offset_seconds=0, session_pct=10.0))
    tracker.add(_snapshot(offset_seconds=600, session_pct=40.0, limits=[capped]))
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_project_returns_none_when_the_projected_cap_is_already_past():
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 40.0, 5.0)])
    assert tracker.project(NOW + timedelta(seconds=99999)) is None


def test_project_picks_the_nearer_of_two_live_projections():
    # BOTH limits project a real cap before their own reset, so this
    # exercises the min() and not just one candidate being suppressed:
    #   session 10 -> 30 over 600s = 0.0333 %/s, 70 left -> caps at +2700s
    #   weekly  90 -> 95 over 600s = 0.00833 %/s, 5 left -> caps at +1200s
    tracker = _tracker_with([(0, 10.0, 90.0), (600, 30.0, 95.0)])
    projection = tracker.project(NOW + timedelta(seconds=600))
    assert projection == Projection(
        kind="weekly", caps_at=NOW + timedelta(seconds=1200)
    )


def test_project_ignores_samples_from_before_a_window_reset():
    """A changed resets_at means the limit reset; spanning that boundary
    would read as a large negative burn and suppress a real projection.

    All three samples are inside WINDOW_SECONDS (600) of the newest, so
    the pre-reset sample is excluded by the resets_at walk-back and NOT
    merely aged out by the window -- otherwise this passes for the wrong
    reason.
    """
    tracker = BurnTracker()
    old_reset = SESSION_RESET - timedelta(hours=5)
    tracker.add(_snapshot(offset_seconds=0, session_pct=80.0,
                          session_resets_at=old_reset))
    tracker.add(_snapshot(offset_seconds=100, session_pct=5.0))
    tracker.add(_snapshot(offset_seconds=600, session_pct=55.0))
    assert len(tracker.samples) == 3
    projection = tracker.project(NOW + timedelta(seconds=600))
    # Measured across the two post-reset samples only: 5 -> 55 over 500s
    # = 0.1 %/s, 45 left -> caps 450s after the newest sample.
    assert projection == Projection(
        kind="session", caps_at=NOW + timedelta(seconds=1050)
    )


def test_project_returns_none_when_resets_at_is_missing():
    """A work account with no session window reports session_resets_at
    None -- without it there is no way to know a cap precedes the reset."""
    tracker = _tracker_with([(0, 10.0, 5.0), (600, 40.0, 5.0)],
                            session_resets_at=None, weekly_resets_at=None)
    assert tracker.project(NOW + timedelta(seconds=600)) is None


def test_min_span_seconds_is_five_minutes():
    assert MIN_SPAN_SECONDS == 300


def test_window_seconds_is_ten_minutes():
    """The window is the decay time -- how long a stale projection lingers
    after you stop. Changing this is a product decision, not a tuning knob."""
    assert WINDOW_SECONDS == 600
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_burn.py -v`
Expected: FAIL — `ImportError: cannot import name 'Projection' from 'tokitty.burn'`

- [ ] **Step 3: Write the minimal implementation**

Append to `tokitty/burn.py`:

```python
@dataclass(frozen=True)
class Projection:
    kind: str  # "session" | "weekly"
    caps_at: datetime


def _pct(sample: Sample, kind: str) -> float:
    return sample.session_pct if kind == "session" else sample.weekly_pct


def _reset(sample: Sample, kind: str) -> Optional[datetime]:
    return sample.session_resets_at if kind == "session" else sample.weekly_resets_at
```

Then add these methods to `BurnTracker`:

```python
    def project(self, now: datetime) -> Optional[Projection]:
        """Return the nearer of the session/weekly cap projections, or
        None when no cap is credibly coming before the window resets."""
        if len(self._samples) < MIN_SAMPLES:
            return None

        newest = self._samples[-1]
        if newest.capped:
            return None

        candidates = [
            projection
            for projection in (self._project_kind(kind, newest, now) for kind in ("session", "weekly"))
            if projection is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda projection: projection.caps_at)

    def _project_kind(self, kind: str, newest: Sample, now: datetime) -> Optional[Projection]:
        resets_at = _reset(newest, kind)
        if resets_at is None:
            return None

        # Walk back only through samples from the SAME window. A changed
        # resets_at means this limit reset, and measuring across that
        # boundary would read as a large negative burn.
        oldest = newest
        for sample in reversed(self._samples[:-1]):
            if _reset(sample, kind) != resets_at:
                break
            oldest = sample

        elapsed = (newest.fetched_at - oldest.fetched_at).total_seconds()
        if elapsed < MIN_SPAN_SECONDS:
            return None

        rate = (_pct(newest, kind) - _pct(oldest, kind)) / elapsed
        if rate <= 0:
            return None

        remaining = 100.0 - _pct(newest, kind)
        if remaining <= 0:
            return None

        caps_at = newest.fetched_at + timedelta(seconds=remaining / rate)
        if caps_at >= resets_at or caps_at <= now:
            return None
        return Projection(kind=kind, caps_at=caps_at)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_burn.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add tokitty/burn.py tests/test_burn.py
git commit -m "feat(burn): project the nearer cap with warm-up and coast suppression"
```

---

### Task 3: String formatting — `display.py`

**Files:**
- Modify: `tokitty/display.py`
- Test: `tests/test_display.py`

**Interfaces:**
- Consumes: `format_reset_time(dt)` (already in `display.py`), `Projection.kind` / `Projection.caps_at` from Task 2.
- Produces: `format_projection(kind: str, caps_at: datetime) -> str` and `resolve_status_text(hint_text: Optional[str], credits_text: Optional[str], projection_text: Optional[str]) -> str`.

**Width budget — do not exceed.** `status_label` wraps at 160px and a wrapped second line renders below the 128px pane edge and is clipped. Measured (fallback font, wider than real Segoe UI): `session caps ~6:20 PM` = 131px, `week caps ~Wed 11:40 PM` = 154px. The weekly form uses the **weekday abbreviation only** — no month or day-of-month. `format_reset_day` produces `"Wed Aug 6"`, which pushes the line to ~174px; do not use it here. A weekday alone is unambiguous inside a 7-day window.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_display.py`:

```python
from datetime import timedelta

from tokitty.display import format_projection, resolve_status_text


def test_format_projection_session_shows_time_only():
    caps_at = datetime(2026, 8, 4, 18, 20, tzinfo=timezone.utc)
    text = format_projection("session", caps_at)
    local = caps_at.astimezone()
    hour_12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    assert text == f"session caps ~{hour_12}:{local.minute:02d} {period}"


def test_format_projection_weekly_shows_weekday_and_time():
    caps_at = datetime(2026, 8, 5, 23, 40, tzinfo=timezone.utc)
    text = format_projection("weekly", caps_at)
    local = caps_at.astimezone()
    hour_12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    assert text == f"week caps ~{local.strftime('%a')} {hour_12}:{local.minute:02d} {period}"


def test_format_projection_weekly_omits_the_month_and_day():
    """Month/day pushes the line past the 160px wrap ceiling."""
    caps_at = datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)
    text = format_projection("weekly", caps_at)
    assert "Aug" not in text


def test_format_projection_stays_within_the_character_budget():
    """A crude proxy for the 160px wrap ceiling that runs headlessly.
    The real check is the Windows manual gate."""
    worst = format_projection("weekly", datetime(2026, 8, 5, 23, 40, tzinfo=timezone.utc))
    assert len(worst) <= 24


def test_resolve_status_text_prefers_the_hint():
    assert resolve_status_text("API hiccup", "$1.00 / $5.00", "session caps ~6:20 PM") == "API hiccup"


def test_resolve_status_text_falls_back_to_credits():
    assert resolve_status_text(None, "$1.00 / $5.00", "session caps ~6:20 PM") == "$1.00 / $5.00"


def test_resolve_status_text_falls_back_to_the_projection():
    assert resolve_status_text(None, None, "session caps ~6:20 PM") == "session caps ~6:20 PM"


def test_resolve_status_text_is_empty_when_nothing_applies():
    assert resolve_status_text(None, None, None) == ""


def test_resolve_status_text_treats_empty_strings_as_absent():
    assert resolve_status_text("", "", "session caps ~6:20 PM") == "session caps ~6:20 PM"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_display.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_projection' from 'tokitty.display'`

- [ ] **Step 3: Write the minimal implementation**

Append to `tokitty/display.py`:

```python
def format_projection(kind: str, caps_at: datetime) -> str:
    """Render a cap projection for the pane's status line.

    The weekly form deliberately carries the weekday only -- adding the
    month and day-of-month pushes the string past status_label's 160px
    wrap width, and a wrapped second line falls below the pane's bottom
    edge. A weekday is unambiguous inside a 7-day window.
    """
    when = format_reset_time(caps_at)
    if kind == "session":
        return f"session caps ~{when}"
    return f"week caps ~{caps_at.astimezone().strftime('%a')} {when}"


def resolve_status_text(hint_text, credits_text, projection_text) -> str:
    """Pick what the single shared status line shows. An error hint always
    wins, then credits, then the burn projection."""
    return hint_text or credits_text or projection_text or ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_display.py -v`
Expected: all pass (9 new)

- [ ] **Step 5: Commit**

```bash
git add tokitty/display.py tests/test_display.py
git commit -m "feat(display): projection formatting and status-line priority"
```

---

### Task 4: Pane renders the projection — `ui.py`

**Files:**
- Modify: `tokitty/ui.py` (import at line 14; `Pane.render` signature at lines 167-180; status line at line 219)
- Test: `tests/test_ui_layout.py`

**Interfaces:**
- Consumes: `resolve_status_text` from Task 3.
- Produces: `Pane.render(..., projection_text: Optional[str] = None)`. The default matters — `run_gui` calls `render` with explicit kwargs in three other places (the debug path and the accounts-warning path) that must keep working untouched.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_layout.py` (these use `inspect`, not a live display — do **not** add `@pytest.mark.gui`):

```python
def test_pane_render_accepts_projection_text_defaulting_to_none():
    from tokitty import ui
    sig = inspect.signature(ui.Pane.render)
    assert "projection_text" in sig.parameters
    assert sig.parameters["projection_text"].default is None


def test_ui_uses_the_shared_status_priority_helper():
    """The status line must go through display.resolve_status_text rather
    than re-implementing the hint > credits > projection order.

    Asserted by source inspection because the behaviour it guards lives
    inside a tk.Label configure call -- checking it any other way needs a
    live display, which would force this test into the `gui` marker and
    out of the default headless run. Same trade the signature-inspection
    tests above already make.
    """
    from tokitty import ui
    source = inspect.getsource(ui.Pane.render)
    assert "resolve_status_text" in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_ui_layout.py -v`
Expected: FAIL — `AssertionError` on `"projection_text" in sig.parameters`

- [ ] **Step 3: Write the minimal implementation**

In `tokitty/ui.py`, change the import on line 14 from:

```python
from tokitty.display import bar_color
```

to:

```python
from tokitty.display import bar_color, resolve_status_text
```

Add the parameter to `Pane.render` — after `accent: bool = False,` in the signature (line 179):

```python
        accent: bool = False,
        projection_text: Optional[str] = None,
```

Replace line 219:

```python
        self.status_label.configure(text=hint_text if hint_text else (credits_text or ""))
```

with:

```python
        self.status_label.configure(text=resolve_status_text(hint_text, credits_text, projection_text))
```

- [ ] **Step 4: Run the full suite to verify nothing regressed**

Run: `pytest -v`
Expected: all pass. Then run the GUI tests too: `xvfb-run -a pytest -m gui -v` — expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tokitty/ui.py tests/test_ui_layout.py
git commit -m "feat(ui): render the burn projection on the shared status line"
```

---

### Task 5: Wire the tracker into the app — `__main__.py`

**Files:**
- Modify: `tokitty/__main__.py` (imports; unit construction at lines 375-391; `tick()` at lines 488-501)
- Modify: `README.md`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `BurnTracker` (Task 1), `BurnTracker.project` (Task 2), `format_projection` (Task 3), `Pane.render(projection_text=...)` (Task 4).
- Produces: `_projection_text_for(tracker, display, now) -> Optional[str]` in `__main__.py` — extracted so the wiring decision is testable without a display.

**Why the projection is gated on `display["dimmed"]`:** `dimmed` is already the app's "we cannot confirm this" signal (stale token, overdue countdown). Showing a confident prediction on top of numbers we have flagged as unconfirmed would be worse than showing nothing. Gating on `status == "ok"` instead would make the line blink off during every transient API hiccup, which is why it is not used here.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`:

```python
from tokitty.__main__ import _projection_text_for
from tokitty.burn import BurnTracker


def _usage(offset_seconds=0, session_pct=10.0, weekly_pct=5.0):
    return UsageSnapshot(
        session_pct=session_pct,
        session_resets_at=NOW + timedelta(hours=3),
        weekly_pct=weekly_pct,
        weekly_resets_at=NOW + timedelta(days=4),
        limits=[],
        fetched_at=NOW + timedelta(seconds=offset_seconds),
    )


def _burning_tracker():
    tracker = BurnTracker()
    tracker.add(_usage(offset_seconds=0, session_pct=10.0))
    tracker.add(_usage(offset_seconds=600, session_pct=40.0))
    return tracker


def test_projection_text_for_formats_a_live_projection():
    text = _projection_text_for(_burning_tracker(), {"dimmed": False},
                                NOW + timedelta(seconds=600))
    assert text is not None
    assert text.startswith("session caps ~")


def test_projection_text_for_is_none_when_the_display_is_dimmed():
    """Dimmed means we cannot confirm the numbers -- do not layer a
    confident prediction on top of them."""
    text = _projection_text_for(_burning_tracker(), {"dimmed": True},
                                NOW + timedelta(seconds=600))
    assert text is None


def test_projection_text_for_is_none_during_warm_up():
    tracker = BurnTracker()
    tracker.add(_usage(offset_seconds=0, session_pct=10.0))
    text = _projection_text_for(tracker, {"dimmed": False}, NOW)
    assert text is None


def test_projection_text_for_tolerates_a_display_without_a_dimmed_key():
    text = _projection_text_for(_burning_tracker(), {}, NOW + timedelta(seconds=600))
    assert text is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — `ImportError: cannot import name '_projection_text_for' from 'tokitty.__main__'`

- [ ] **Step 3: Write the minimal implementation**

In `tokitty/__main__.py`, add to the imports:

```python
from tokitty.burn import BurnTracker
from tokitty.display import format_projection
```

(`display` is already imported from in this module — extend the existing import line rather than adding a second one. `datetime` and `timezone` are already imported.)

Add this function next to `_next_last_good`:

```python
def _projection_text_for(tracker: BurnTracker, display: dict, now: datetime) -> Optional[str]:
    """Format the burn projection for a pane, or None to leave the status
    line to credits/hints.

    Gated on `dimmed` -- the app's existing "these numbers are not
    confirmed" signal -- rather than on poll status, so an ordinary
    transient API hiccup does not make the line blink off.
    """
    if display.get("dimmed"):
        return None
    projection = tracker.project(now)
    if projection is None:
        return None
    return format_projection(projection.kind, projection.caps_at)
```

In `run_gui`, add a tracker to each unit. Change the `units.append(...)` call (line 390):

```python
        units.append({"pane": pane, "poller": poller, "watcher": watcher,
                      "last_good": None, "key": key, "account": account,
                      "burn": BurnTracker()})
```

Then in `tick()`, insert the sampling and projection between the `_display_state_for` call and the `render` call:

```python
    def tick():
        for unit in units:
            latest = unit["poller"].get_latest()
            if latest is None:
                continue
            display = _display_state_for(latest, unit["last_good"])
            if latest.status == "ok" and latest.snapshot is not None:
                unit["burn"].add(latest.snapshot)
            display["projection_text"] = _projection_text_for(
                unit["burn"], display, datetime.now(timezone.utc)
            )
            activity = unit["watcher"].get_latest()
            pose = resolve_pose(display["state"], activity)
            display["state"] = pose["sprite_state"]
            display["tool_label"] = pose["tool_label"]
            display["accent"] = pose["accent"]
            unit["pane"].render(**display)
            unit["last_good"] = _next_last_good(latest, unit["last_good"])
        root.after(UI_REFRESH_MS, tick)
```

- [ ] **Step 4: Run the full suite**

Run: `pytest -v`
Expected: all pass.
Run: `xvfb-run -a pytest -m gui -v`
Expected: all pass.
Run: `ruff check .`
Expected: clean.

- [ ] **Step 5: Update the README**

In `README.md`, add a bullet to the feature list describing the projection. Match the surrounding voice — short, concrete, no marketing:

```markdown
- **Burn-rate projection.** When your current pace would hit a cap before the window resets, the status line says when: `session caps ~6:20 PM`. It tracks whichever limit lands first, and stays blank when you are coasting.
```

- [ ] **Step 6: Commit**

```bash
git add tokitty/__main__.py tests/test_main.py README.md
git commit -m "feat: show a burn-rate cap projection per account"
```

---

### Task 6: Windows manual gate

**Files:** none — verification only.

This is the one thing the headless suite cannot check. The 160px width budget was measured under a WSL fallback font, which is **wider** than real Segoe UI, so the strings should fit with room to spare — but "should" is not "does", and a wrapped second line is invisible in tests and clipped in the app.

- [ ] **Step 1: Launch the real widget on Windows**

From PowerShell (not WSL — and confirm the process `SessionId` matches `explorer.exe`):

```powershell
pythonw.exe -m tokitty
```

- [ ] **Step 2: Confirm the line renders on one line**

Drive the **personal** account — actively use Claude Code in `~/.claude` so the pace is non-zero — and leave it running past warm-up. `MIN_SPAN_SECONDS` is 300, and polls land every 120s, so the first sample pair spanning ≥300s arrives on the **4th poll, ~6 minutes in**. (Three polls span only 240s and are correctly suppressed; do not report that as a failure.)

Confirm:

- the projection appears on the status line, on **one** line, not clipped at the pane's bottom edge
- the text is fully visible — no truncation at the right edge of the 300px card

For the `week caps ~` form you must drive the **work** account (`~/.claude-work`, 95% weekly at time of writing) during the gate. Outside work hours that pane sits at `stale_token` with `binding is None`, which renders dimmed — and `_projection_text_for` returns `None` on a dimmed pane by design, so the check cannot pass unless that account is actually in use.

- [ ] **Step 3: Confirm the quiet state**

Stop using Claude Code and leave the widget running for **a full 10 minutes** — one `WINDOW_SECONDS`. The line should still be showing at the 5-minute mark and should be **gone** by ~10 minutes, once every burning sample has aged out of the window.

Two things that look like bugs here but are not:

- The projected time **drifts later** while you sit idle. `caps_at` is anchored to the newest sample, so each flat poll pushes it forward. Expected.
- The line does **not** clear at the first idle poll. The window is the decay time; there is no faster path by design.

If it has not cleared by ~12 minutes, that is a real defect — check that `project()` is filtering `self._samples` against its own `now` argument (not just relying on `add()`'s trim, which freezes once polls stop succeeding and `add()` stops being called).

- [ ] **Step 4: Report the result**

Report to Nick with a screenshot before merging. If either form wraps, shorten it in `format_projection` and update the character-budget test in Task 3 — do not bump `PANE_HEIGHT`.

---

## Self-Review

**Coverage:** Placement (Task 4), which-limit / nearer-cap (Task 2), quiet state (Tasks 2+3), five suppression rules (Task 2, one test each), `fetched_at` dedupe (Task 1), time-based window (Tasks 1+2), width ceiling (Task 3 + gate in Task 6), memory-only samples (Task 1, no state-dir file), README (Task 5).

**Type consistency:** `Projection(kind, caps_at)` is produced in Task 2 and consumed unchanged in Tasks 3 and 5. `format_projection(kind, caps_at)` and `resolve_status_text(hint, credits, projection)` keep the same argument order everywhere. `projection_text` is the name used in the display dict, the `render` kwarg, and `_projection_text_for`'s return.

**Known gap, accepted:** `session caps ~6:20 PM` does not disambiguate today from tomorrow. The session window is 5 hours, so a projected cap is always within a few hours; a date would not fit the width budget.
