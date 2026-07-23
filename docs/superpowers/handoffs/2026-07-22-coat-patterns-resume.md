# Coat patterns (#32 + #38) — resume handoff

**Written 2026-07-22.** Pick this up in a fresh chat. Read this + the ledger + the plan's Task 8 section, then continue.

- **Plan:** `docs/superpowers/plans/2026-07-22-coat-patterns.md`
- **Spec:** `docs/superpowers/specs/2026-07-22-coat-patterns-design.md`
- **Ledger (SDD progress, recovery map):** `.superpowers/sdd/progress.md` → "Phase 7" section.
  ⚠️ The ledger is **gitignored scratch** — it survives on disk but is NOT in git. **Never `git clean -fdx`** (it would destroy the ledger + task briefs/reports). If lost, recover from `git log`.

## State

- **Branch:** `coat-patterns`, tip `85487c3`, base `1b4a22c` (main after #21/PR #41 merge).
- **Tasks 1–7 COMPLETE and committed.** Branch is fully CI-green-able:
  **329 headless + 5 gui (xvfb) + `ruff check .` clean (ruff==0.15.22).**
- All commits authored by **nickwolf**, zero attribution trailers. Working tree clean.

Commit map (base `1b4a22c`..`85487c3`):

| Commit | Task |
|---|---|
| `8d36d8d` | docs: plan + spec |
| `d0491b6` | T1 — `COLORWAYS`×`PATTERNS`×`resolve_palette` alongside `COATS` (byte-identical) |
| `7558eae` | T2 — the flip: `coat` → `colorway`+`pattern` across sprites/customize/ui/menu/tray/main + 7 test files |
| `7d24c2c` | T3 — `randomize.random_look` (curated, injectable) |
| `cf6b8c6` | T4 — `settings.surprise_me` |
| `f95e007` | T5 — menu **Randomize** + **Surprise me** seams (conditional) |
| `fb1bf7a` | T6 — `run_gui` wiring: first-run seed **persists**, Randomize, Surprise-me-on-start |
| `85487c3` | T7 — `render_sheet --grid`, `render_media` migrated (docs/media no drift) |

## Cadence in use (#21-style, per Nick)

Fresh **Sonnet** implementer subagent per task; **NO per-task reviewer**; controller re-verifies each task empirically (suite + ruff + task-specific gate); **ONE final Opus whole-branch review** before the PR. Commits by Nick alone (no AI attribution). No new runtime deps. TDD each step.

## 🔴 OPEN DECISION — settle FIRST before Task 8

**Who executes the Task 8–9 pixel art?**
- Traditionally (Phases 2 & 4) art candidates came from a **Fable subagent**, with Nick reviewing/approving PNGs + the live widget.
- **Fable is out by Nick's choice, not availability:** Fable 5 is the top-tier model and *is* available, but it was paygated behind usage credits on 2026-07-19 and Nick is declining to use it (protest) until it's in his plan. **Reversible** — if Nick says Fable's now in-plan, it's back on the table.
- This task's brief tightened art to **"Nick's owner-only work, never delegated"** (i.e. Nick does the pixels himself).

**Resolve before touching Task 8 (do not assume):** with Fable off the table, the realistic paths are **Nick-by-hand** or an **Opus subagent generating candidates for Nick's approval** — and whether to keep or relax this task's "never delegated" framing.

## Task 8 — new template regions (paws `m` / points `x` / tail `y` / belly `u`) + tail go/no-go

**HARD GATE. Owner-art. Not committed — the test goes red until templates carry the chars, so art + scaffolding land as ONE green commit.**

**Art:** re-char in `SITTING_TEMPLATE`, `ALERT_TEMPLATE`, `FLOPPED_TEMPLATE` (`tokitty/sprites.py`):
- **paws `m`** ← lower-leg/foot cells currently `w`
- **belly `u`** ← belly/underside cells currently `w` (muzzle stays `w`)
- **points `x`** ← outer-ear + face-mask cells currently `o`
- **tail `y`** ← tail-curl cells currently `O`

Derived poses inherit via `_overlay` (props stamp over region cells and win — confirm by rendering working/permission/done_hop).

**Scaffolding (ready to paste alongside the art):**

`tokitty/sprites.py` — `REGION_CHARS` (currently line 58) → 8-tuple, and every `PATTERNS` entry gains 4 identity-preserving keys:
```python
REGION_CHARS: Tuple[str, ...] = ("o", "O", "s", "c", "m", "x", "y", "u")

PATTERNS: Dict[str, Dict[str, str]] = {
    "solid":       {"o": "coat", "O": "shade", "s": "coat", "c": "coat",
                    "m": "coat", "x": "coat", "y": "shade", "u": "coat"},
    "tabby":       {"o": "coat", "O": "shade", "s": "mark", "c": "coat",
                    "m": "white", "x": "coat", "y": "shade", "u": "white"},
    "bicolor":     {"o": "coat", "O": "shade", "s": "coat", "c": "white",
                    "m": "white", "x": "coat", "y": "shade", "u": "white"},
    "tabby_white": {"o": "coat", "O": "shade", "s": "mark", "c": "white",
                    "m": "white", "x": "coat", "y": "shade", "u": "white"},
    "calico":      {"o": "coat", "O": "shade", "s": "#453a33", "c": "#e8823c",
                    "m": "white", "x": "coat", "y": "shade", "u": "white"},
}
```

`tests/test_sprites.py` — append:
```python
def test_new_regions_present_and_covered():
    from tokitty.sprites import REGION_CHARS, PATTERNS, SITTING_TEMPLATE
    for ch in ("m", "x", "y", "u"):
        assert ch in REGION_CHARS
    for name, pat in PATTERNS.items():
        assert set(pat.keys()) == set(REGION_CHARS), name
    joined = "".join(SITTING_TEMPLATE)
    for ch in ("m", "x", "y", "u"):
        assert ch in joined, ch
```

**Free self-check on cell placement:** those default tokens keep the 5 existing looks byte-identical (`u`/`m`→`white`=`#fff6ec`=old `w`; `x`→`coat`=old `o`; `y`→`shade`=old `O`). So after re-charring, **`python3 scripts/render_media.py --out docs/media && git status --short docs/media` must show NO drift.** Drift ⇒ a cell was mapped to the wrong region char.

**Review commands:**
- Solid reads as a cat: `python3 scripts/render_sheet.py --out /tmp/solid.png --pattern solid`
- Per-region debug: add a throwaway bright-color region map to confirm cell placement (build on request).
- Grid: `python3 scripts/render_sheet.py --grid --out /tmp/grid.png` (matrix pruning tool from T7).

**🔴 Tail go/no-go (gates Task 9):** render a `ringed` tail spike (alternate `y` cells). Record explicitly:
- **GO** → Task 9 ships **van + ringed** patterns.
- **NO-GO** → Task 9 drops van + ringed; tail stays a plain solid region.

## Task 9 — new colorways (cream/brown) + silhouette patterns (tuxedo/socks/colorpoint [+van/ringed if GO]) + grid prune

Owner-art. Blocked on Task 8. Scaffolding (test + key structure) per plan Task 9; hexes/tokens are owner-picked; the `--grid` render is the prune tool; HARD GATE on Nick's approval of the final set.

## Task 10 — README + push/PR + CI + manual Windows gate + merge + memory

Ordinary mechanics, but **depends on 8–9 being done** (README describes the final set; PR closes #32/#38; the manual gate exercises the full colorway/pattern grid). Manual Windows gate is Nick's. Then rebase-merge `--delete-branch` and update the `project_tokitty_v2` memory (final pattern/colorway set + tail go/no-go outcome).

## Carried to final Opus review (Minor, not blocking)

- **T6 `toggle_surprise`** writes `Settings(tray_enabled=<startup settings.tray_enabled>, …)`. Harmless today because `toggle_tray` is session-only (byte-identical to #21 merge-base, never persists), so the on-disk `tray_enabled` is always the startup value it writes back. Would clobber only if `toggle_tray` ever gains persistence. Plan-mandated code — flagged, not unilaterally changed.

## Verified invariants worth trusting on resume

- **Byte-identity anchored (T1, advisor-prompted):** while `COATS` still existed, `get_palette(name) == resolve_palette(*LEGACY_COAT_MAP[name])` full-dict for **all 5** legacy coats incl. calico. The color tables + `resolve_palette` are byte-unchanged since T1, so the default cat is provably identical through T7. (This proof is impossible to re-run now — `COATS` is deleted.)
- Tray discipline intact: menu getters read plain-Python shadows (`pane._colorway`/`_pattern`, surprise/tray shadows), no tk Vars; no module-scope pystray/PIL in ui/main/menu.
