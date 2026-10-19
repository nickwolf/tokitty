# v0.8.0 behaviour track: time of day, personality, bars, handoff hints

Issues: #138 Time of day, #136 A personality for each cat, #135 The cat reacts to its own bars, #129 Account handoff hints. All four change what an idle cat does and where it goes, so they share one model. Status: agreed with Nick 2026-10-17 after Codex review (gpt-6-sol, high). Decisions: all four stay in v0.8.0; cats that cannot roam still nap in place; #129 compares all accounts across providers; the default personality is balanced.

## Where things stand

- The cat's state is not chosen. `mood.compute_mood` maps the higher of session and weekly percent onto a ladder: sleeping below 25, content below 50, interested below 75, alert below 90, panicked from 90 (`mood.py:10`). `pose.resolve_pose` puts prompts first, then capped and wake, then working, thinking and done_hop, then that mood (`pose.py:56`).
- `wander.Wanderer` (#22) only acts on content and interested. Its one decision is when to take a trip along the top edge and how many stops to make. The inputs are `HOME_WAIT_MS` 45 to 120 s, `STOP_REST_MS` 20 to 60 s and `STOPS` 1 to 3 (`wander.py:25-27`), all drawn from an injected `random.Random`. Time arrives as `now_ms` from the pane's monotonic clock. Only first-row panes can reach the top edge (`widget.py:158`).
- `app.tick` runs every 500 ms (`UI_REFRESH_MS`) and calls `app_core.step_unit` once per account. No step sees another account, and no step remembers the previous percentages except `detect_activate`, which compares two polls to catch a capped limit clearing (`mood.py:91`).
- The cat itself is not ticked. The widget sleeps until the next due frame or rest (`widget.py:266`), so anything that adds wake-ups costs idle CPU.

## Rule zero: the ladder stays honest

The cat is a gauge first. None of the four may change which ladder state a percent maps to, or override alert, panicked, capped, wake, prompt or activity states, except where this doc names an exception (#135 watch, #129 tired). The behaviours only choose among things a calm cat does.

"Calm" means the resolved state is content or interested, the states that wander today. A sleeping cat stays asleep. Nothing here makes a cat sleep or wake against its usage.

## The shared model: Drives

One small frozen dataclass, computed by one pure function, read by the Wanderer when it decides.

```python
@dataclass(frozen=True)
class Drives:
    trip: float = 1.0     # weight of "go for a walk" when a rest ends at home
    nap: float = 0.0      # weight of "doze where you are" (content only)
    stay: float = 0.0     # weight of "keep sitting, decide again later"
    play: float = 0.0     # #127 art; stays 0 until that art exists
    pace: float = 1.0     # multiplies every rest span (2.0 = rests twice as long)
    stops: tuple = (1, 3) # stops per trip

def drives(personality: str, hour: Optional[int], context: BarContext) -> Drives
```

`Drives()` with no arguments reproduces today's behaviour exactly (always trip, today's spans), which gives the refactor a golden test. `drives()` takes the time-of-day row as the baseline (#138, the day row when the setting is off), multiplies in the personality row (#136), then applies the bar context (#135, #129). A multiplier cannot lift a weight from 0, which is why time of day supplies absolute values and personality only scales them. Each table is plain data in a new `tokitty/behaviour.py`, with no toolkit and no clock. Factors multiply, so a lazy cat at 2 AM is lazier than either alone. `pace` is clamped to 0.5 to 3.0 so no combination wakes the cat more often than twice today's rate. That clamp is also the idle-CPU budget.

Wanderer changes:
- `Wanderer.drives` replaces the module constants as the source of the rest spans and stop counts. It defaults to `Drives()`, so behaviour is unchanged until something sets it.
- When a rest at home runs out, the Wanderer makes a weighted choice among trip, nap and stay (play joins later). Today it always trips, which is what the default Drives (trip 1, the rest 0) still do. When only one choice has weight, no RNG draw is made, so seeded sequences under default Drives match today's exactly.
- Cats that cannot roam (wander off, or not on the first row) have no home rest today (`_rest` returns early when `not self.roams`). They get one when the Drives give any nap weight, and their only choices are nap and stay. Trips stay impossible for them.
- A nap is its own scheduler substate: `Wanderer.nap_until` (a ms deadline) plus `animation()` returning `spot_state("sleeping", spot)` while it is set. It shows the sleeping art at home, or the sleeping loaf on the top edge with lie_down as today, for `NAP_MS` 3 to 10 minutes times `pace`. The logical state stays content. `set_state` with the same calm state (every 500 ms render) leaves the nap and its deadline alone. Any other state clears `nap_until` at once, so a nap can never hide a poll that says alert. Interested cats never nap: their nap weight is forced to 0. When the nap ends the cat plays the content art again (sit_up first if it lay down on top) and starts a fresh home rest.
- `Roam.set_drives(drives, now_ms)` stores the new value. It never interrupts a move or a rest in progress, and takes effect at the next decision. So recomputing Drives every 500 ms costs a comparison and nothing else.

Drives are computed in `app_core.step_unit` (per account) from that account's personality and the hour, and passed through `PaneUpdate` to `pane.render`, which calls `set_drives`. The hour comes from `now`, which `app.tick` already passes in, converted with an injected tz (`now.astimezone(tz)`, tz None meaning local) so tests pin it.

Determinism: the RNG is the pane's existing injected `random.Random`, the clock is the pane's injected clock, and the hour comes from the `now` the test passes. Nothing new reads wall time or builds an unseeded RNG.

## #138 Time of day

Baseline row by local hour (absolute weights):

| Hours | Name | trip | nap | stay | pace |
|---|---|---|---|---|---|
| 23 to 6 | night | 0.3 | 3.0 | 1.0 | 1.6 |
| 6 to 10 | morning | 1.5 | 0.2 | 0 | 0.7 |
| 10 to 18 | day | 1.0 | 0.5 | 0 | 1.0 |
| 18 to 23 | evening | 0.8 | 1.0 | 0.5 | 1.2 |

Bands, not a smooth curve: easier to test and to explain, and the switch is never seen because the cat only reads Drives at its next decision.

Setting: `CatStyle.time_of_day: bool = True` in theme.json beside `wander`, with a "Follows the clock" toggle next to "Cat wanders" on the Theme tab. Off means the baseline is `Drives()`, today's behaviour, with personality still applied on top. On by default, as #138 asks, so existing cats change on upgrade: that goes in the release notes. Back-compat: a missing key loads as True through `pick.bool`, the same as `wander`. An older build drops the key, which is harmless.

Poses: all existing (sleeping, sleeping loaf, content, content loaf, walk, jumps, lie_down, sit_up). No new art.

## #136 Personality

`Customization.personality: str = "balanced"` in customization.json, validated against `PERSONALITIES = ("balanced", "lazy", "playful", "curious")`. Unknown or missing loads as balanced, so existing users see no change. Randomize (#38) picks one too.

Multipliers on the time-of-day row:

| Personality | trip | nap | stay | play | pace | stops |
|---|---|---|---|---|---|---|
| balanced | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1 to 3 |
| lazy | 0.5 | 2.5 | 1.5 | 0.5 | 1.4 | 1 to 2 |
| playful | 1.2 | 0.6 | 0.5 | 3.0 | 0.8 | 1 to 3 |
| curious | 1.6 | 0.6 | 0.5 | 1.0 | 0.9 | 2 to 4 |

Curious also doubles the #135 watch and bat weights. Playful doubles bat.

Shown as a dropdown on each account's Look tab, below Tail.

Honest gap: without #127's play art, playful and curious differ only in trip rate, stops and (once #135 lands) batting. Playful only becomes distinct when #127 ships, and `play` stays 0 until then.

Poses: existing. Play needs #127 art (art track).

## #135 The cat reacts to its bars

`step_unit` keeps the last shown session and weekly percent per unit (`unit["bars_seen"]`), and derives a `BarContext` each tick:

- `moved`: which bar's displayed whole percent went up since the cat last looked. Bars mostly move while the cat shows working or thinking, which outrank everything here, so the event is latched as pending. It is consumed only when the bat move actually starts, and expires after 10 minutes. A cat that is away, napping or in the bat cooldown keeps it pending.
- `reset`: a session window rolled over without having capped (resets_at moved forward by over an hour and the percent dropped). Latched the same way, expiring after 10 minutes. The capped case already plays `activate` and stays as it is.
- `near`: the driving bar is in the alert band (75 to 90).

Behaviours:
- Bat: a calm cat at home with a latched `moved` plays a one-shot `bat` move toward the bars, at most once per 2 minutes. It is a Wanderer move, like jump_up. New art: `bat` (paw swipe toward the stats side, mirrored for inside_right).
- Pounce: on `reset`, a calm cat plays a one-shot `pounce` move. New art: `pounce`, defined in MOVES like jump_up, because `Roam.tick` only advances on `Player.finished_at`, which is set only for `move:` animations (`animation.py:43`). done_hop is a state, not a move, so it cannot stand in. If the art is late, the pounce waits.
- Watch, the exception to rule zero: an alert cat on a pane that can roam jumps to the top edge and sits on the stop nearest the driving bar's fill end, looking down, until the state changes. The alert state is still what is shown. Only its spot changes. New art: `alert@on_top` (spot variant, alert sitting on the edge looking down). Panicked still goes home. A pane that cannot roam shows the alert at home as today.
  Two Wanderer changes make this possible. Alert joins a new `WATCH_STATES` set that plans a one-stop trip to the watch x and does not send the cat home in `set_state`. And `_lies` stops inferring posture from "a spot variant exists": a `LYING_VARIANTS` set names the variants drawn lying down (the content and sleeping loaves), so a sitting `alert@on_top` does not trigger lie_down.

The bar rectangles come from `theme.layout_card` (`session_bar`, `weekly_bar`). The fill end is `rect.x + rect.w * pct / 100`, snapped to the stride lattice from `stops_from`.

Per-tick work: one comparison per account per 500 ms tick. The cat gets at most one extra move per 2 minutes.

As built (2026-10-18), where it differs from the above:
- `step_unit` only detects. `_note_bars` keeps `unit["bars_seen"]` from fresh, undimmed polls and passes `bar_events=(moved, reset)` counters to the pane. The Wanderer latches a new count for `BAR_EVENT_TTL_MS` (10 min) and keeps the bat cooldown, because only it knows when a bat starts. A move is the session whole percent going up; the weekly bar has no bat. A reset compares with the last poll that had a `resets_at`.
- `near` is gone: the band is the alert state, so watch keys off alert as the pane receives it (after `apply_hint`, so tired wins).
- `Drives` gains `bat`, `pounce` and `watch` chances (0 in `Drives()`; `drives()` gives 0.5, 1.0, 0.5, curious doubles bat and watch, playful doubles bat, lazy halves both). Wander off zeroes bat and pounce.
- Bat and pounce play from home only, never over a transition. The pane takes in the poll's fill end and counters before its state, and follows the fill end after it.
- Watch waits `WATCH_SETTLE_MS` (25 s) of alert at home (a cat already out on the edge draws at once), then one draw per alert episode; the head of `alert@on_top` (`PERCH_HEAD_PX`) goes over the fill end, snapped to the stops. Tired does not watch. With the stats on the right (inside, and on_top beside_cat) the fill end at 75 to 90% is beyond the head's reach from the last stop (x=184), so that cat sits at the far end; only inside_right and a full_width on_top card follow the fill end.
- Only the bat has the 2 minute cooldown. A pounce needs a reset, so it comes at most once per session window.


## #129 Account handoff hints

`app.tick` changes from step-then-render per unit to two passes: step every unit and collect the `PaneUpdate`s, run `handoff_hints` over the limits updates, then render. Today each pane renders inside the per-unit loop (`app.py:526`), so a pass that ran afterwards would be one tick late. `handoff_hints(updates, flags) -> (hints, flags)` is pure. The per-account near-cap flag that hysteresis needs is carried in `unit["handoff_near"]` between ticks. The models view (`render_usage`, model bars instead of session and weekly) gets no hints: that account counts as neither side.

- Every account compares with every other, across providers (Nick, 2026-10-17): a near-cap Claude card can say "codex has room".
- Near cap: session at or above 85, weekly at or above 90, or capped. Leave at 80 and 85 (hysteresis, so it does not flicker on a poll at the threshold).
- Has room: not capped, session below 50 and weekly below 70.
- Only confirmed numbers count. A dimmed display (stale token resting look, keychain denied, overdue cached countdown, `app_core.py:140-160`) is neither near cap nor roomy, and its cat is left alone.
- A hint appears only when there is at least one near-cap account and at least one with room. If every account is fine, or every account is near cap, nothing shows. A single account never sees it.

Shown:
- Near-cap card: the status line reads "work has room", using the roomy account's label (several: the one with the lowest session percent). In `resolve_status_text` it ranks below the error hint and the stale age, and above credits and the projection. Nothing changes in the card painting: it reuses the status line text.
- Near-cap cat looks tired, the second exception to rule zero. It only replaces alert and never replaces panicked, capped or wake. New art: `tired` (alert body, heavy lids, slow tail).
- Roomy cat perks up: a cat whose confirmed ladder state is sleeping or content shows interested (existing art). Never a dimmed cat, never an activity or prompt state, never a state above interested.
- Out of scope: the cats facing each other across stacked panes. It needs per-pane facing, which side spots alone do not give. Filed as a follow-up.

## Poses: existing and new

| Behaviour | Existing | New art (art track) |
|---|---|---|
| #138 night nap / morning trips | sleeping, sleeping loaf, content, content loaf, walk, jumps, lie_down, sit_up | none |
| #136 lazy, curious | same as #138 | none |
| #136 playful | same | play poses (#127) |
| #135 bat | none | `bat` move |
| #135 pounce | done_hop as fallback | `pounce` move |
| #135 watch | jump_up, walk | `alert@on_top` variant |
| #129 perk up | interested | none |
| #129 tired | none | `tired` state |

## Build order

1. #138: lands `behaviour.py`, `Drives`, the weighted errand choice, naps, and the theme toggle. Everything else adds factor tables to it.
2. #136: customization field, table, Look tab dropdown.
3. #129: cross-account pass, status line, perk up. Ships text and perk-up first. Tired waits for its art behind a check (`"tired" in sprites.ANIMATIONS`, else alert).
4. #135: BarContext, bat, pounce, watch. The most art-dependent, so it goes last.

Codex recommended moving #135 to v0.9.0 (three new art assets plus new routing, posture and event-lifetime rules). Nick kept all four in v0.8.0 (2026-10-17), so #135 goes last and blocks on its art.

Each adds per-tick work, so each gets the idle-CPU check (C:\tmp\123-cpu, main vs branch).

## Tests

- `tests/test_behaviour.py`: `drives()` tables, clamps, and the defaults that reproduce today's behaviour.
- `tests/test_wander.py`: seeded `Roam`, driven by ms. With default Drives, trip timing is unchanged (a golden sequence for seed 0). Night makes fewer trips and more naps over a simulated 8 hours. A nap ends on the next non-calm state. Interested never naps.
- `tests/test_app_core*.py`: hour from an injected `now`/tz; BarContext latching; reset detection; handoff thresholds, hysteresis, and cross-provider comparison.
- Qt: existing wander tests keep `T.CatStyle(wander=False)` for fixed pixels and the seeded RNG patch.
