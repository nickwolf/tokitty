# Claude Design brief — tokitty coat patterns (Tasks 8–9)

**Paste this whole file into Claude Design. Attach the two reference PNGs
(`current-cat-orange-tabby.png`, `current-grid-4x5.png`).** It is fully
self-contained; you need no other context.

---

## 1. What tokitty is (the medium you're designing for)

**tokitty** is a tiny desktop-pet cat that sits in the corner of a screen and
changes mood. Its art is **not** raster images — it is **pixel-art on a fixed
28-wide × 26-tall character grid**, stored as Python string lists. **Each cell is
one character; each character maps to exactly one color** via a palette dict. The
renderer blows the grid up 4× (each grid cell → a 4×4 block of device pixels), so
the on-screen cat is ~112×104 px. There is no anti-aliasing and no sub-pixel
detail — a "region" is just *a set of grid cells that share a palette character*.

**This is the crucial constraint: you are designing by choosing which
*character* goes in which *cell*, and which *color* each character resolves to.**
Recoloring "the paws" means: the paw cells all hold the same character, and we
point that character at a color.

The cat renders in ~14 poses (see `current-cat-orange-tabby.png`): sitting
(sleeping/content/interested), alert (alert/panicked/confused/waking/activate),
lying down (flopped/stirring), plus four "prop" poses (working-at-laptop,
thinking-bubble, permission-flag, done-hop) that are **derived** from the base
poses by stamping props on top. **You only edit three base templates**
(`SITTING`, `ALERT`, `FLOPPED`); the four prop poses inherit your edits
automatically.

---

## 2. What we're building (the feature)

Today a "coat" bundles color + markings into 5 fixed presets. We are splitting
that into **two orthogonal axes** that multiply:

- **colorway** = a base *tone palette* (5 tones: `coat`, `shade`, `mark`,
  `light`, `ear`). Think "orange cat" vs "gray cat" vs "cream cat".
- **pattern** = a *map* that says, for each recolorable body region, which tone
  (or literal color) it takes. Think "tabby" vs "tuxedo" vs "colorpoint".

`colorways × patterns` = many distinct looks from a little data. The engine for
this is **already built and tested** (Tasks 1–7, all merged-ready). **Your job is
the two art/design tasks that engine was built to serve:**

- **Task 8 — carve 4 new recolorable regions** into the cat so patterns have
  more than two things to recolor: **paws**, **points** (outer ears + face
  mask), **tail**, **belly**. Plus a go/no-go readability check on a *ringed*
  tail.
- **Task 9 — design new colorways and patterns** that use those regions: 2 new
  colorways (**cream**, **brown**), and silhouette patterns (**tuxedo**,
  **socks**, **colorpoint**, and — only if the ringed tail reads — **van**,
  **ringed**). Then prune any combo that looks muddy.

---

## 3. The current data (verbatim — this is your raw material)

### Base palette (non-recolorable colors, shared by every look)

```python
BASE_PALETTE = {
    ".": "",          # transparent — not drawn
    "k": "#2b1a12",   # outline / closed-eye line
    "w": "#fff6ec",   # muzzle / belly / paws   (near-white)
    "e": "#3fae5c",   # eye, open (green)
    "n": "#d6748c",   # nose
    # ...accent + prop colors (z/!/?/h/g/b/B/f/d/G) — not your concern...
}
```

### Colorways (4 today; you add cream + brown)

Each colorway defines 5 tone slots. `coat`/`shade`/`mark`/`ear` are lifted from
the old presets (so the legacy cats migrate byte-identically). `light` is a
**new** pale tone that `colorpoint`/`van` bodies use — the values below are
**illustrative starting points you finalize**.

```python
COLORWAYS = {
    "orange": {"coat": "#e8823c", "shade": "#c26a2c", "mark": "#a8541f", "light": "#f7e0c0", "ear": "#f6b8c8"},
    "gray":   {"coat": "#a4aec2", "shade": "#818ba0", "mark": "#5f6879", "light": "#e4e8ef", "ear": "#e3a9ba"},
    "black":  {"coat": "#4a4653", "shade": "#38343f", "mark": "#575263", "light": "#c9c6cf", "ear": "#a8798c"},
    "white":  {"coat": "#f1ebdf", "shade": "#c4bcae", "mark": "#ded6c6", "light": "#f6f2ea", "ear": "#f6b8c8"},
}
```

### Patterns + region chars (today — the 4 chars `o O s c`)

```python
REGION_CHARS = ("o", "O", "s", "c")   # the cells a pattern may recolor

PATTERNS = {
    #                o=fill    O=shade    s=stripes  c=patches
    "solid":       {"o": "coat", "O": "shade", "s": "coat", "c": "coat"},
    "tabby":       {"o": "coat", "O": "shade", "s": "mark", "c": "coat"},
    "bicolor":     {"o": "coat", "O": "shade", "s": "coat", "c": "white"},
    "tabby_white": {"o": "coat", "O": "shade", "s": "mark", "c": "white"},
    "calico":      {"o": "coat", "O": "shade", "s": "#453a33", "c": "#e8823c"},
}
```

A pattern value is either a **tone token** (`"coat"|"shade"|"mark"|"light"|"white"`)
or a **literal `#rrggbb`** (e.g. calico's near-black patches). Resolver:

```python
def resolve_palette(colorway, pattern):
    cw = COLORWAYS[colorway]
    tones = {"coat": cw["coat"], "shade": cw["shade"], "mark": cw["mark"],
             "light": cw["light"], "white": BASE_PALETTE["w"]}
    merged = dict(BASE_PALETTE)
    merged["p"] = cw["ear"]                 # inner-ear pink = colorway.ear (not pattern-driven)
    for char, src in PATTERNS[pattern].items():
        merged[char] = tones.get(src, src)  # tone token → tone, else literal hex
    return merged
```

Note `p` (inner-ear pink) is driven by the colorway's `ear` slot directly, **not**
by patterns. Leave `p` alone.

### The three base templates (28 wide × 26 tall)

A 0-indexed column ruler is shown above the first template; all three share the
same 28 columns and are numbered rows 0 (top) → 25 (bottom). **`.` = transparent.** Legend of the
characters you'll touch: `o` = coat fill, `O` = coat shade, `s` = stripes,
`c` = patches, `w` = white (muzzle/belly/paws), `p` = inner-ear pink,
`L`/`R` = eye cells, `A` = nose/accent cell, `k` = outline.

```
SITTING_TEMPLATE   (rows numbered 0 top → 25 bottom; each grid row is exactly 28 chars)
          111111111122222222
0123456789012345678901234567
............................    row 0
........o.........o.........    row 1
.......ooo...o...ooo........    row 2
......ooooooooooooooo.......    row 3
......oopoosssssoopoo.......    row 4
.....ooocccoooooooooOO......    row 5
.......occsssosssoOO........    row 6
.......oooooooooooOO........    row 7
......ooooLoooooRooOO.......    row 8
.......oooooowooooOO........    row 9
.......oooowwAwwooOO........    row 10
.......ooowwwwwwwoOO........    row 11
........ooowwwwwoOO.........    row 12
.........oooowooOO..........    row 13
........ooooooooooo..OO.....    row 14
.......ooooooooooooo..OO....    row 15
......ooooooooooooooo..OO...    row 16
......osssooowooooooo..OO...    row 17
......ooooowwwwwooooo...OO..    row 18
.....osssowwwwwwwcccoo..OO..    row 19
......oooowwwwwwwccco...OO..    row 20
......ooowwwwwwwwwcco..OO...    row 21
......oooowwwwwwwoooo.OO....    row 22
.......ooowwwwwwwooo.OO.....    row 23
........oowwwwwwwOO.........    row 24
.........oooooooOO..........    row 25
```

```
ALERT_TEMPLATE  (ears perked; same cell roles)
........o.........o.........    row 0
........o....o....o.........    row 1
.......opooooooooopo........    row 2
......oopoosssssoopoo.......    row 3
......oocccoooooooooo.......    row 4
.......occsssosssoOO........    row 5
.......oooooooooooOO........    row 6
......ooooLoooooRooOO.......    row 7
.......oooooowooooOO........    row 8
.......oooowwAwwooOO........    row 9
.......ooowwwwwwwoOO........    row 10
........ooowwwwwoOO.........    row 11
..........ooowoOO...........    row 12
..........oooooOO...........    row 13
.........ooooooooo...OO.....    row 14
........ooooooooooo...OO....    row 15
.......ooooooooooooo...OO...    row 16
.......sssooowoooooo...OO...    row 17
.......oooowwwwwoooo....OO..    row 18
......sssowwwwwwwccco...OO..    row 19
.......ooowwwwwwwccc....OO..    row 20
.......oowwwwwwwwwcc...OO...    row 21
.......ooowwwwwwwooo..OO....    row 22
........oowwwwwwwoo..OO.....    row 23
.........OwwwwwwwO..........    row 24
..........oooooOO...........    row 25
```

```
FLOPPED_TEMPLATE  (lying down; L = near eye; 1–5 = animated tail-sweep cells)
............................    rows 0–2 blank
....oo......oo..............    row 3
...oopo...opoo..............    row 4
...oopooooopoo..............    row 5
...ooooooooooo..............    row 6
...oooosssoooo..............    row 7
..ooooooooooooo.............    row 8
..oossooooossoo.............    row 9
..ooooooooooooo.............    row 10
..oooLooookkooo.............    row 11
..oooooonoooooo........1..23    row 12
..oooowwwwwoooo........11223    row 13
...ooowwwwwooooossoooo..1453    row 14
....ooowwwooooooooossoo.1453    row 15
.....oOOOOOoooooooooooss.4oo    row 16
......OOOOOooooooooOoooo..oo    row 17
...wwoooooooooooooOooooo..oo    row 18
..wwooooooooowwwwwOoccooooo.    row 19
...........owwwwwwOccccooo..    row 20
..wwoooooooowwwwwwwOoccoo...    row 21
..wwoooooooowwwwwwwoooooooww    row 22
........................ooww    row 23
............................    rows 24–25 blank
```

> **These ASCII blocks are a readable guide. The authoritative source is
> `tokitty/sprites.py` — if you produce grid edits, work against that file's
> exact strings, which we will attach or you can reason cell-by-cell here.**

---

## 4. Task 8 — carve the four new regions

Introduce **4 new region characters** and paint them into the cells that are
*currently* a different character:

| New region | Char | Currently painted as | Where |
|---|---|---|---|
| **paws** (lower legs / feet) | `m` | `w` (white) | the foot/lower-leg cells |
| **belly** (underside) | `u` | `w` (white) | the chest/belly cells — **muzzle stays `w`** |
| **points** (outer ears + face mask) | `x` | `o` (coat fill) | the outer-ear cells + the mask band across the face |
| **tail** (the curl) | `y` | `O` (coat shade) | the tail curl only |

After Task 8, `REGION_CHARS = ("o","O","s","c","m","x","y","u")`.

### 🔒 Hard rule: the 5 existing looks must stay **byte-for-byte identical**

We prove correctness by re-rendering the old cats and diffing pixels. So the new
chars must default to the color the old char had:

- `u` (belly), `m` (paws) → default tone **`white`** (= old `w` = `#fff6ec`)
- `x` (points) → default tone **`coat`** (= old `o`)
- `y` (tail) → default tone **`shade`** (= old `O`)

Every existing pattern therefore gains the 4 new keys with these
identity-preserving defaults. **This block is already worked out — use it
verbatim:**

```python
REGION_CHARS = ("o", "O", "s", "c", "m", "x", "y", "u")

PATTERNS = {
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

### ⚠️ Re-char PER CELL, never a blanket character swap

The old characters are overloaded — the *same* character is used for a region we
want **and** for a region we must leave alone. Only re-char the cells that truly
belong to the new region:

- **`w` is muzzle AND belly AND paws.** The **muzzle** (the small `w` patch right
  around the nose `A`, e.g. SITTING rows 9–13) must **stay `w`**. Only the
  belly/chest `w` → `u`, and only the foot/lower-leg `w` → `m`.
- **`o` is body-fill AND the ear/mask.** Only the **outer-ear** cells and the
  **face-mask band** become `x`. The body fill stays `o`.
- **`O` is the tail-curl AND haunch/body-shade edges.** In SITTING the `O` cells
  are essentially all tail-curl. **But in ALERT, row 24's `O` at col 9 and col 17
  are haunch outline, not tail** — those must **stay `O`**; only the far-right
  descending `OO` pair is the tail → `y`.
- **FLOPPED has no `O` tail.** Its tail is the animated `1`–`5` sweep (painted
  `o` per frame), and its `O` cells (rows 16–18) are **body-shade — leave them
  `O`**. Recommendation: **do not** try to make the flopped sweep a `y` region
  (doing so naively would break byte-identity); the independently-recolorable
  tail is a SITTING/ALERT feature. Flag it if you disagree.

### Derived poses inherit automatically

`WORKING`/`THINKING`/`PERMISSION`/`DONE_HOP` are built from these templates by
stamping props *on top* (the prop cell wins). So your re-charring flows through
for free — **but sanity-check** that no prop was sitting on a cell you re-charred
in a way that now looks wrong (it shouldn't; props occlude, they don't depend on
region color).

### 🎯 Task 8 go/no-go: the ringed tail

The tail curl is only ~8–10 cells. Render a **ringed** tail (alternate the tail
cells between two tones, like a raccoon-ish band) and judge honestly:
**does it read as rings, or as mud?**

- **GO** → Task 9 ships the `van` + `ringed` patterns.
- **NO-GO** → drop `van` + `ringed`; the tail stays a plain solid-color region.

State your verdict explicitly.

---

## 5. Task 9 — new colorways + silhouette patterns

### Add 2 colorways (all 5 tone slots each; you pick/tune the hexes)

Illustrative starting points:

```python
"cream": {"coat": "#e9d9bd", "shade": "#cdb894", "mark": "#a98d63", "light": "#f6ecda", "ear": "#f0c4cf"},
"brown": {"coat": "#8a5a3c", "shade": "#6d452d", "mark": "#4f3020", "light": "#cbb39a", "ear": "#d69aa6"},
```

Also feel free to **re-tune every colorway's `light` tone** — it's brand-new and
only matters for colorpoint/van; make it read as a believable pale version of the
coat.

### Add silhouette patterns (each must cover all 8 `REGION_CHARS`)

Illustrative maps (tune the tokens on the contact sheet):

```python
"tuxedo":     {"o": "coat", "O": "shade", "s": "coat", "c": "white",
               "m": "white", "x": "coat", "y": "shade", "u": "white"},
"socks":      {"o": "coat", "O": "shade", "s": "coat", "c": "coat",
               "m": "white", "x": "coat", "y": "shade", "u": "coat"},
"colorpoint": {"o": "light", "O": "light", "s": "light", "c": "light",
               "m": "mark",  "x": "mark",  "y": "mark",  "u": "light"},
# van + ringed ONLY if the tail spike was GO:
# "van":    {"o": "white", "O": "shade", "s": "white", "c": "white",
#            "m": "white", "x": "coat", "y": "coat", "u": "white"},
# "ringed": <needs a tail-band scheme decided in the Task 8 spike>,
```

### Honesty about orthogonality (don't over-promise)

`solid`/`tabby`/`bicolor`/`tabby_white`/`tuxedo`/`socks` are **true
colorway-friendly** patterns (they reference colorway tones, so every colorway ×
these = a clean grid of intentional looks). But `calico`, `colorpoint`, and `van`
are about a **base↔accent relationship**, not one base color — `colorpoint`/`van`
remap the body to the `light` tone, so **dark colorways × these will look
muddy.** That is expected and acceptable as a user choice; we just don't pitch
"every combo is great." **The pruning step below is where we cut the ones that
read badly *everywhere*.**

### Prune

Render the full `colorways × patterns` grid (see `current-grid-4x5.png` for the
current 4×5 — yours will be ~6×8+). Cut any *whole* pattern or colorway that
never reads. Muddy corners (dark × colorpoint) are fine; a pattern that's mud in
*every* colorway should go.

---

## 6. Deliverable — what to hand back

**Best case (you can encode directly):** the three modified templates
(`SITTING`/`ALERT`/`FLOPPED` as 26-row × 28-col character lists), the updated
`REGION_CHARS`, the full updated `PATTERNS` dict, and the two added `COLORWAYS`
entries — plus rendered previews. We drop these straight into `sprites.py`.

**Also totally fine (concepts we encode in-session):** if editing a 28×26 ASCII
grid isn't your strength, hand back the *design*:
1. **Region maps** — annotate on the attached cat which cells/areas are paws,
   points, tail, belly (a marked-up image or a per-region description is fine).
2. **Colorway palettes** — final cream/brown hexes + any re-tuned `light` tones,
   as swatches with hex values.
3. **Pattern intent** — for each new pattern, which tone each region should take.
4. **Ringed-tail verdict** — GO or NO-GO, with the render you judged it on.

We will translate concepts into the exact grid edits + data and run the
verification gates ourselves.

### How your work gets verified (so you know the bar)

1. **Byte-identity:** the 5 legacy looks re-render pixel-identical (catches any
   cross-region mistake — but NOT paw↔belly / points↔fill / tail↔shade mix-ups,
   since those render the same under legacy patterns).
2. **Per-region debug render:** we paint `m`/`x`/`y`/`u` in 4 loud distinct
   colors to confirm every cell landed in the right region. **This, plus a
   preview under a differentiating pattern (socks/colorpoint), is the real
   placement check — so region boundaries are exactly what your design is judged
   on.**
3. **Grid contact sheet** of the full look-space for the muddy-combo prune.
4. **Nick's eyeball approval** at each gate. Iteration is expected and welcome —
   this is his precise, iterative feedback loop.

---

## 7. Hard constraints (non-negotiable)

- **Grid is fixed 28×26.** Don't change dimensions. Every row stays 28 chars.
- **Muzzle stays `w`.** Belly and paws leave white; the muzzle does not.
- **`p` (inner-ear pink) is colorway-driven, not pattern-driven — leave it.**
- **Every `PATTERNS` entry must define exactly the 8 `REGION_CHARS`** (a test
  enforces this closed set — a missing/extra key fails).
- **Every `COLORWAYS` entry must define all 5 slots** (`coat`/`shade`/`mark`/
  `light`/`ear`), each a valid `#rrggbb`.
- **The 5 legacy looks stay byte-identical** (Section 4's default tokens).
- **No new tools/deps.** Output is plain character grids + hex color data.
- **Pattern values** are a tone token or a literal `#rrggbb` — nothing else.

---

## 8. Reference images (attached)

- **`current-cat-orange-tabby.png`** — all ~14 poses of today's default cat
  (orange + tabby). This is what you're re-charring. Regenerate:
  `python3 scripts/render_sheet.py --out cat.png --scale 10 --colorway orange --pattern tabby`
- **`current-grid-4x5.png`** — the current colorway×pattern look-space (4
  colorways × 5 patterns). Regenerate:
  `python3 scripts/render_sheet.py --grid --out grid.png --scale 9`
