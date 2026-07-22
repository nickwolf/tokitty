# Coat patterns — design

**Issues:** #32 — "Coat pattern options (5–10 presets) in the Customize dialog";
#38 — "Random pattern and colors on start (XS; builds on #32)".
**Out of scope:** #37 (transparency) — a separate M/L conversation.

**Date:** 2026-07-22
**Branch:** `coat-patterns`

## Goal

Give the cat **patterns** as a first-class, user-pickable axis, orthogonal to
coat **color**. Today `sprites.COATS` bundles color and markings into five
presets; this splits them into a **colorway** (a base-color tone palette) times a
**pattern** (which tone each body region gets), so a handful of colorways and a
handful of patterns multiply into many distinct looks — including
silhouette-changing ones (tuxedo, colorpoint, van) that the two existing marking
regions cannot express. #38 adds randomization on top.

Owner (Nick) explicitly chose **maximum visual interest, time no concern**, and
accepted the migration + ripple cost that orthogonality carries. The plan pays
that cost deliberately with a byte-identical refactor first (see Task 1), then
gated owner-art passes.

## Decided (owner, brainstorm 2026-07-22 — not to be re-opened)

| Question | Decision |
|---|---|
| **What is a pattern** | Not pure-data recolors alone. Invest owner-art in **new recolorable region chars** (Tier 3) so patterns can change the silhouette, not just tint two fixed regions. (Full per-pattern marking *geometry* — mackerel vs. swirl — is **rejected**: barely reads at 28×26, huge repeated art, no clean data model.) |
| **Color vs. pattern** | **Two orthogonal axes: colorway × pattern.** Not one bundled preset list. |
| **Region scope** | **Tier 3:** existing stripes/patches **plus** new regions **paws, points (ears+mask), tail, belly**. The white belly is negotiable — belly becomes a recolorable region (a solid "void" cat with no white belly is now possible). |
| **Randomization (#38)** | **All three** curated modes: seed-a-roll on first run (only-if-unset), a **Randomize** menu action (rolls + saves), a **Surprise-me** every-launch toggle (off by default). Rolls pick colorway+pattern only — **not** free-form hex (curated presets always look intentional). Per account. |
| **Surprise-me persistence** | A surprise-me roll **writes** `customization.json` (uniform save path with seed/Randomize). "Never override an explicit pick" is honestly scoped to **first-run seeding only**; surprise-me is an explicit opt-in mode the user enabled knowing it re-rolls. |
| **Tail region** | Kept, but as a **gated fallback**: an early spike renders the ~10px tail curl; if a *ringed* tail reads as mud, drop the ringed/van patterns and keep tail as a plain solid-color region. Must not unravel the plan. |

## The orthogonality is partial — say so honestly

`solid`, `tabby`, `bicolor`, `tabby+white`, `tuxedo`, `socks` are true
colorway-friendly patterns: they reference colorway tones, so **~6 colorways ×
these ≈ a clean grid** of intentional looks. But `calico` (white + orange +
*black*), `colorpoint` (pale body + dark extremities), and `van` (white body +
colored head/tail) are intrinsically about a **base↔accent relationship**, not a
single base color:

- Their multi-hued bits are authored as **literal hex** in the pattern (calico's
  near-black patches), which the token-or-literal palette mechanism supports.
- `colorpoint`/`van` need a **pale body**, so those patterns remap the base fill
  `o`/`O` to the colorway's **`light`** tone (flame point on orange, blue point on
  gray). Dark colorways × these specials will look muddy.

Do **not** pitch "N×M all valid." The pitch is: a clean colorway×pattern grid,
**plus** a few specials that only sing on light colorways. **The art gate renders
the full pattern × colorway grid as a contact sheet so the owner eyeballs and
prunes the muddy corners** — that grid render is the pruning tool, built into the
gate (Task 3).

## Context / grounding (verified 2026-07-22)

- **`s`/`c` cells are at fixed template positions.** SITTING/ALERT carry 17 `s` +
  13 `c` cells; FLOPPED carries 13 `s` + 8 `c`; every state renders both. A
  pure-data pattern can only *recolor* these two regions — it cannot move a stripe
  or add a sock. New silhouettes require **new region chars in the templates**.
  (Demonstrated to the owner via a "pure-data ceiling" contact sheet during the
  brainstorm.)
- **Muzzle, belly, and paws are already white** (`BASE_PALETTE["w"] = #fff6ec`,
  shared by every coat). So a plain white chest is free today. The highest-impact
  new regions are the parts that are *not* white-by-default: ears/mask, tail, and
  paws-as-a-real-region (so they can be colored, not just white).
- **`get_palette(coat)` is called widely** and its signature changes: `ui.py`
  (`PALETTE`, per-pane palette), `customize.effective_palette`,
  `scripts/render_sheet.py`, `scripts/render_media.py`, `tokitty/tray.py`
  (`_default_image_factory(coat)`), and `sprite_raster` consumers. All must move to
  `(colorway, pattern)`.
- **The just-merged tray (#21) is the sharpest ripple.** `Pane._coat` →
  `_colorway`/`_pattern` flows through the menu radio getters (`current_coat` →
  colorway/pattern getters), `TrayManager(coat=…)` → `(colorway=…, pattern=…)`,
  and the plain-Python **shadow state**. Keep #21's discipline: getters read
  plain-Python shadows, never tk Vars/widgets (pystray evaluates them off the main
  thread). The tray icon image must rebuild from the pane-0 colorway+pattern.
- **A closed-set invariant already guards palette coverage:** the sprites test that
  every char of every frame of every state is defined in every palette. A new
  region char is therefore **cross-cutting** — every pattern (and the colorway
  layer) must define it. Keep the region set small and closed; enforce with a test
  that every `PATTERNS` entry covers exactly `REGION_CHARS`.
- **Persistence homes are settled:** per-account look → `customization.json` (state
  dir, keyed by account name / `"default"`); app-global toggle → `settings.json`
  (as `tray_enabled` already lives there). Never `accounts.json` (would flip
  credential-resolution mode).

## Architecture

### `tokitty/sprites.py` — colorway × pattern, token-resolved

Replace the bundled `COATS` with two tables plus a resolver.

**Colorway** = a base-color tone palette (5 hexes). `mark` is the marking/accent
tone (darker stripes on most colorways; a subtle sheen on near-black); `light` is
the pale tone that `colorpoint`/`van` bodies use.

The `coat`/`shade`/`mark`/`ear` hexes below are lifted verbatim from today's
`COATS` (so the four legacy colorways migrate byte-identically). `light` is a
**new** tone with no legacy value; the hexes shown are **illustrative starting
points the owner finalizes on the contact sheet** (set `light ≈ coat`/white if the
pale tint doesn't read at 28×26 — the model degrades gracefully).

```python
COLORWAYS: Dict[str, Dict[str, str]] = {
    # keys: coat, shade, mark, light, ear
    "orange": {"coat": "#e8823c", "shade": "#c26a2c", "mark": "#a8541f",
               "light": "#f7e0c0", "ear": "#f6b8c8"},   # light = illustrative (flame-point cream)
    "gray":   {"coat": "#a4aec2", "shade": "#818ba0", "mark": "#5f6879",
               "light": "#e4e8ef", "ear": "#e3a9ba"},   # light = illustrative (blue-point pale)
    "black":  {"coat": "#4a4653", "shade": "#38343f", "mark": "#575263",
               "light": "#c9c6cf", "ear": "#a8798c"},   # light = illustrative (smoke pale)
    "white":  {"coat": "#f1ebdf", "shade": "#c4bcae", "mark": "#ded6c6",
               "light": "#f6f2ea", "ear": "#f6b8c8"},   # light = illustrative
    # NEW colorways (art task): "cream", "brown", ... (owner-picked hexes)
}
```

**Pattern** = a map from every coat-driven char → a **source token**
(`"coat"|"shade"|"mark"|"light"|"white"`) **or a literal `#rrggbb`**. The keys are
the base fill/shade `o`/`O` plus the region chars. `REGION_CHARS` is the single
source of truth for which chars are pattern-driven; it grows in Task 2 when the
templates gain the new regions.

```python
# After Task 2 (new regions charred in):
#   o  = coat fill        O = coat shade
#   s  = stripes          c = patches
#   m  = paws/lower legs   x = points (outer ears + face mask)
#   y  = tail (curl)       u = belly / underside
REGION_CHARS = ("o", "O", "s", "c", "m", "x", "y", "u")   # p (inner ear) = colorway.ear, not pattern-driven

PATTERNS: Dict[str, Dict[str, str]] = {
    "solid":  {"o": "coat", "O": "shade", "s": "coat", "c": "coat",
               "m": "coat", "x": "coat", "y": "shade", "u": "coat"},
    "tabby":  {"o": "coat", "O": "shade", "s": "mark", "c": "coat",
               "m": "white", "x": "coat", "y": "shade", "u": "white"},
    "tuxedo": {"o": "coat", "O": "shade", "s": "coat", "c": "white",
               "m": "white", "x": "coat", "y": "shade", "u": "white"},
    "colorpoint": {"o": "light", "O": "light", "s": "light", "c": "light",
                   "m": "mark", "x": "mark", "y": "mark", "u": "light"},
    "calico": {"o": "coat", "O": "shade", "s": "#453a33", "c": "#e8823c",
               "m": "white", "x": "coat", "y": "shade", "u": "white"},
    # ... socks, bicolor, tabby+white, van, ringed (final set = art task)
}
```

Resolver:

```python
def effective_palette(colorway: str, pattern: str) -> Dict[str, str]:
    cw = COLORWAYS[colorway]
    tones = {"coat": cw["coat"], "shade": cw["shade"], "mark": cw["mark"],
             "light": cw["light"], "white": BASE_PALETTE["w"]}
    merged = dict(BASE_PALETTE)
    merged["p"] = cw["ear"]
    for char, src in PATTERNS[pattern].items():
        merged[char] = tones[src] if src in tones else src   # token or literal hex
    return merged
```

`get_palette(coat)` is **replaced** by `effective_palette(colorway, pattern)` (or
a thin `get_palette(colorway, pattern)` kept as the name every caller uses).
`customize.effective_palette(custom)` becomes a thin wrapper that also applies the
user's `coat_base`/`coat_shade` overrides on top (unchanged override semantics).
Module `PALETTE` = default colorway `"orange"` + pattern `"tabby"`.

### New region chars in the templates (owner art, Task 2)

The region chars `m` (paws), `x` (points), `y` (tail), `u` (belly) are
**provisional** — all four are currently unused in the palette/templates (verified
no collision with `. k w e n z ! ? h g b B f d G o O s c p L R A 1-8 S P t T`);
Task 2 confirms and freezes them. Add `m`/`x`/`y`/`u` cells to **SITTING, ALERT,
FLOPPED**; the derived poses
(WORKING/THINKING/PERMISSION/DONE_HOP) inherit via `_overlay` (props stamp *over*
region cells, which is fine — the prop cell wins). Muzzle stays `w`. Verify no
overlay leaves a region char it expects to be `o`, and that `_shift_up`/ground
handling still holds. This is the foundational art pass, gated on the owner's PNG
review (a "solid" render confirms the cat still reads; a per-region debug render
confirms cell placement).

### `tokitty/customize.py` — schema + migration

`Customization` replaces `coat` with `colorway` + `pattern`:

```python
@dataclass(frozen=True)
class Customization:
    colorway: str = "orange"
    pattern: str = "tabby"
    overrides: Dict[str, str] = field(default_factory=dict)   # coat_base/coat_shade/card_bg/bar_fill (unchanged)
    label: str = ""
```

**Load-time migration** (a legacy entry has `coat`, no `colorway`/`pattern`):

```python
LEGACY_COAT_MAP = {
    "orange_tabby": ("orange", "tabby"),
    "gray_tabby":   ("gray",   "tabby"),
    "black":        ("black",  "tabby"),   # subtle tone-on-tone sheen = mark tone
    "white":        ("white",  "tabby"),   # subtle sheen = mark tone
    "calico":       ("white",  "calico"),  # literals reproduce the tricolor exactly
}
```

Unknown colorway/pattern names fall back to defaults (like the old
`coat not in COATS` guard). `save_customization` writes the new fields only (no
`coat`). Note: legacy `black`/`white` map to `<colorway>+tabby` to reproduce their
sheen **byte-identically**; users can switch to `solid` for a flat look.

`accounts.json` seed: `initial_customization` translates a legacy `coat` seed via
`LEGACY_COAT_MAP`, and also reads optional `colorway`/`pattern` seed fields if
present (minimal `accounts.py` addition). Stored customization still beats seed.

### `tokitty/randomize.py` (new) — curated rolls

```python
def random_look(colorways: Sequence[str], patterns: Sequence[str],
                rng: random.Random | None = None) -> Tuple[str, str]:
    r = rng or random
    return r.choice(list(colorways)), r.choice(list(patterns))
```

Pure and injectable (`rng` for deterministic tests). Callers pass
`COLORWAYS.keys()` / `PATTERNS.keys()`. Never rolls free-form hex overrides.

### `tokitty/settings.py` — add the surprise-me toggle

Add `surprise_me: bool = False` to the frozen `Settings` dataclass, robust-loaded
exactly like `tray_enabled` (non-bool / missing / wrong-shape → default `False`).
Round-trips through the existing `save_settings`.

### `tokitty/menu.py` + `tokitty/ui.py` — two pickers + randomize

- `build_menu` gains **Colorway ▸** and **Pattern ▸** submenus (radio lists over
  `COLORWAYS`/`PATTERNS`, current selected), replacing the single **Coat ▸**; plus
  a **Randomize** command and a **Surprise me** checkbox (present iff its seam is
  wired, mirroring the "Show tray icon" pattern). Getters read plain-Python shadow
  state (`pane._colorway`, `pane._pattern`, a surprise-me shadow) — **never tk
  Vars** (pystray reads them off-thread).
- `Pane` swaps `_coat` for `_colorway`/`_pattern`; `set_appearance` gains
  `colorway`/`pattern`. `on_customization_changed` `field` set grows to include
  `"colorway"`, `"pattern"`, and `"randomize"` (value ignored) alongside the
  existing `coat_base`/`coat_shade`/`card_bg`/`bar_fill`/`label`/`reset`.
- The **Customize…** dialog is unchanged in shape (the four color-override rows +
  Reset/Close); colorway/pattern live in the menu, live-applying like coat does
  today.

### `tokitty/__main__.py` `run_gui()` — wiring

- `initial_customization`: when there is **no** stored entry **and** no valid
  account seed, **seed a `random_look()`** (only-if-unset), then persist it — so a
  fresh install/account gets a unique, stable cat instead of default orange tabby.
- **Surprise me:** read `settings.surprise_me`; if on, roll each pane's look on
  start, apply it, **and save** (uniform save path). Toggling the menu item flips
  `settings.surprise_me`, persists, and re-rolls/applies.
- **Randomize** action: roll colorway+pattern for the clicked pane, apply, save
  (write-through, like a coat change).
- `handle_customization_changed` grows `"colorway"`/`"pattern"`/`"randomize"`
  branches. The tray is constructed with the pane-0 `(colorway, pattern)`.

### `scripts/render_sheet.py` + `scripts/render_media.py`

`render_sheet` `--coat` → `--colorway` + `--pattern`; add a **grid mode** that
renders the full `COLORWAYS × PATTERNS` matrix as one contact sheet (the art-gate
pruning tool). `render_media` updates its `get_palette` calls to the new
signature (README media regenerated; byte-diff checked where the default cat is
unchanged after Task 1).

## Randomization semantics (precise)

| Trigger | Rolls | Writes `customization.json`? | Respects existing pick? |
|---|---|---|---|
| First run, no stored + no seed | colorway+pattern | yes | n/a (was unset) |
| **Randomize** menu action | colorway+pattern | yes | overwrites (explicit user action) |
| **Surprise me** ON, each start | colorway+pattern | yes (per owner decision) | overwrites (explicit opt-in mode) |
| Surprise me OFF | — | — | renders the stored look |

All rolls are per account (each pane independent). Free-form hex overrides are
never rolled.

## Testing (headless; display-requiring tests marked `@pytest.mark.gui`)

- **`sprites`**: `COLORWAYS` each define the 5 tone keys; every `PATTERNS` entry
  covers exactly `REGION_CHARS`; token values resolve to valid hex; literal values
  are `#rrggbb`; `effective_palette(cw, pat)` covers **every char of every frame of
  every state** for **every** colorway×pattern (the closed-set invariant); the
  black-body-lighter-than-outline check survives.
- **Byte-identical refactor (Task 1)**: for each legacy coat, `effective_palette`
  of its migrated `(colorway, pattern)` equals the old `get_palette(coat)` **key by
  key**; and a render-diff gate (contact sheet before/after) shows **no** pixel
  change.
- **`customize`**: migration of each legacy `coat` name; unknown colorway/pattern →
  defaults; round-trip save/load of `colorway`+`pattern`; save omits `coat`; old
  files load; override semantics unchanged.
- **`randomize`**: deterministic with an injected `Random`; only ever returns keys
  from the given sets.
- **`settings`**: `surprise_me` default `False`; round-trip; robust-load.
- **`menu`**: Colorway/Pattern submenus present with radios; current selection
  reflects the getters; Randomize command wired; Surprise-me checkbox present iff
  seam; getters are plain callables (no tk access).
- **`ui`/`main`** (gui-marked where a real `tk.Tk()` is built): `build_menu_model`
  reads colorway/pattern shadows; `initial_customization` seeds a roll only when
  unset (injected rng); surprise-me on-start rolls+saves.
- **Art** is reviewed via PNG contact sheets, not asserted pixel-by-pixel (beyond
  the invariant/coverage tests).

## Art plan & gates (owner-only; never delegated)

1. **Task 1 refactor renders byte-identically** — no art, pure model split;
   render-diff + migration tests are the gate.
2. **Template re-charring** (m/x/y/u across 3 templates) — gated on owner PNG
   review (solid render reads as a cat; debug render confirms cell placement).
   **Tail spike here:** render a ringed tail; if muddy, drop ringed/van, keep tail
   as a solid region.
3. **Colorways + patterns data** — owner picks hexes and region→source maps;
   **grid contact sheet** (COLORWAYS × PATTERNS) is the review + pruning tool.
   HARD GATE before dependent UI wiring ships its final look.

## Out of scope (noted, not built)

- #37 transparency (separate M/L).
- Per-pattern marking **geometry** (mackerel vs. classic swirl vs. spotted) —
  rejected: doesn't read at 28×26, unbounded repeated art.
- Randomizing free-form hex overrides (garish; curated presets only).
- Hats / accessories (cut in Phase 4 already).
- Per-region color-picker rows in the Customize dialog (future; overrides stay
  coat_base/coat_shade/card_bg/bar_fill).

## Verification gates (before claiming done)

- Full suite green headless (`pytest`, gui deselected); `xvfb-run -a pytest -m gui`
  green; `ruff check .` clean with pinned `ruff==0.15.22`.
- CI: all 8 checks green across all three OSes (no new runtime deps expected — if
  one is proposed, it gets the tray's first-dep blast-radius review).
- `grep` confirms no module-scope tkinter/pystray leak reintroduced; tray getters
  still read shadows only.
- **Owner art gates** (Task 1 byte-identical; region-charring PNG; grid render
  prune) all passed.
- **Manual Windows gate with Nick**: real dual-account card; colorway and pattern
  menus both switch + persist across restart; Randomize re-rolls + persists;
  Surprise-me on → different look each launch, off → last roll kept; first-run seed
  gives a fresh cat; legacy `customization.json` loads unchanged.
