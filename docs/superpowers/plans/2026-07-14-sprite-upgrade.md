# Phase 1 — Sprite Resolution Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the cat from a 15×13 grid @ SCALE 7 to a 28×26 grid @ SCALE 4 (same on-screen footprint, ~4× pixel density), and give the palette coat/pattern support so Phase 4's coat presets don't touch the sprites again.

**Architecture:** `tokitty/sprites.py` keeps its exact public API (`PALETTE`, `SCALE`, `get_frames`, `ALL_STATES` — consumed only by `ui.py` and `tests/test_sprites.py`), gaining `get_palette(coat)` and a `COATS` table. Templates stay the v1 mechanism: pose grids with single-occurrence placeholder cells composed with per-state substitution dicts. Art review happens through a new headless PNG contact-sheet script (stdlib-only), since the dev environment has no GUI.

**Tech Stack:** Python stdlib only (zero runtime deps is a hard project rule). pytest for tests.

**Closes:** GitHub issues #1 (grid upgrade) and #2 (pattern support). Branch: `feat/sprite-upgrade`, PR to `main`.

## Global Constraints

- Zero pip/runtime dependencies; stdlib only (v1 spec rule).
- Rendering stays Canvas-rect vector — never bitmaps (DPI crispness; v2 spec Phase 1).
- Public sprite API is frozen: `PALETTE: Dict[str,str]`, `SCALE: int`, `get_frames(state) -> List[List[str]]`, `ALL_STATES: tuple`. State names are frozen too (`sleeping, content, interested, alert, panicked, confused, waking, activate, flopped, stirring`) — `mood.py` and `ui.py` depend on them.
- New grid: **28 wide × 26 tall, SCALE = 4** → 112×104 device px, fitting the existing 112×112 `CAT_CANVAS_SIZE` in `ui.py` (do not change `ui.py` constants).
- Every frame must be structurally valid: equal row lengths, only palette-defined characters (existing tests enforce this — they must keep passing throughout).
- Commit messages: conventional style, no AI-attribution lines.

---

### Task 1: Coat/pattern palette support (`get_palette`, `COATS`)

**Files:**
- Modify: `tokitty/sprites.py` (palette section, lines 24–38)
- Test: `tests/test_sprites.py`

**Interfaces:**
- Produces: `BASE_PALETTE: Dict[str, str]` (non-coat keys), `COATS: Dict[str, Dict[str, str]]` (coat name → colors for exactly the keys `{"o","O","s","c","p"}`), `get_palette(coat: str = "orange_tabby") -> Dict[str, str]` (full char→color mapping; raises `KeyError` on unknown coat), module-level `PALETTE = get_palette()` (backward compat — `ui.py` keeps importing it unchanged).
- New palette character: `"c"` = pattern patch region (chest/back patches used by future calico/white coats). For `orange_tabby` it maps to the coat color `"#e8823c"` so it's invisible until other coats differentiate it.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_sprites.py`:

```python
from tokitty.sprites import COATS, get_palette


def test_get_palette_default_matches_module_palette():
    assert get_palette() == PALETTE


def test_get_palette_unknown_coat_raises_key_error():
    with pytest.raises(KeyError):
        get_palette("nonexistent-coat")


def test_every_coat_defines_exactly_the_coat_keys():
    for name, coat in COATS.items():
        assert set(coat) == {"o", "O", "s", "c", "p"}, name


def test_palette_covers_pattern_char():
    assert PALETTE["c"] == PALETTE["o"]  # invisible on the default coat
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sprites.py -v`
Expected: FAIL — `ImportError: cannot import name 'COATS'`

- [ ] **Step 3: Implement** — in `tokitty/sprites.py`, replace the single `PALETTE` dict with:

```python
# Non-coat colors: shared by every coat preset.
BASE_PALETTE: Dict[str, str] = {
    ".": "",  # transparent -- not drawn
    "k": "#2b1a12",  # outline / closed-eye line
    "w": "#fff6ec",  # muzzle / belly / paws
    "e": "#3fae5c",  # eye, open
    "n": "#d6748c",  # nose (default accent)
    "z": "#8fd0e8",  # sleep accent
    "!": "#e6483c",  # alert/panic accent
    "?": "#e8c23c",  # confused accent
    "h": "#f2d675",  # activate/happy sparkle accent
}

# Coat presets: each defines the same region keys. "c" is the patch
# region -- distinct cells in the grids that only some coats color
# differently (calico patches); on the default orange tabby it matches
# the coat so the pattern plumbing is invisible until Phase 4 uses it.
COATS: Dict[str, Dict[str, str]] = {
    "orange_tabby": {
        "o": "#e8823c",  # coat
        "O": "#c26a2c",  # coat shading
        "s": "#a8541f",  # tabby stripe
        "c": "#e8823c",  # patch region (matches coat on this preset)
        "p": "#f6b8c8",  # inner ear
    },
}


def get_palette(coat: str = "orange_tabby") -> Dict[str, str]:
    """Full character->color mapping for one coat preset."""
    merged = dict(BASE_PALETTE)
    merged.update(COATS[coat])
    return merged


PALETTE: Dict[str, str] = get_palette()
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS (existing palette-coverage test still passes because `PALETTE` has the same keys plus `"c"`).

- [ ] **Step 5: Commit**

```bash
git add tokitty/sprites.py tests/test_sprites.py
git commit -m "feat(sprites): coat presets and pattern-region palette (get_palette/COATS)"
```

---

### Task 2: Headless PNG contact-sheet renderer

**Files:**
- Create: `scripts/render_sheet.py`
- Test: `tests/test_render_sheet.py`

**Interfaces:**
- Consumes: `ALL_STATES`, `get_frames`, `get_palette` from Task 1.
- Produces: `render_sheet(out_path: Path, scale: int = 8, coat: str = "orange_tabby") -> List[str]` (returns the state names in row order, top to bottom) and a CLI: `python3 scripts/render_sheet.py --out sheet.png [--scale 8] [--coat orange_tabby]` which also prints one state name per line (the row legend). Used by Task 3's art loop and later by Phase 5 media capture. Transparent cells render as the card background `#1c1c22`.

- [ ] **Step 1: Write the failing test** — create `tests/test_render_sheet.py`:

```python
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from render_sheet import render_sheet
from tokitty.sprites import ALL_STATES, get_frames


def _png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_sheet_is_valid_png_with_expected_dimensions(tmp_path):
    out = tmp_path / "sheet.png"
    order = render_sheet(out, scale=2)
    assert order == list(ALL_STATES)
    w, h = _png_size(out)
    gap = 4
    max_frames = max(len(get_frames(s)) for s in ALL_STATES)
    cell_w = max(len(f[0][0]) for f in (get_frames(s) for s in ALL_STATES)) * 2
    cell_h = max(len(f[0]) for f in (get_frames(s) for s in ALL_STATES)) * 2
    expected_w = gap + max_frames * (cell_w + gap)
    expected_h = gap + len(ALL_STATES) * (cell_h + gap)
    assert (w, h) == (expected_w, expected_h)


def test_idat_decompresses_to_rgb_scanlines(tmp_path):
    out = tmp_path / "sheet.png"
    render_sheet(out, scale=1)
    data = out.read_bytes()
    idat_start = data.find(b"IDAT") + 4
    idat_len = struct.unpack(">I", data[idat_start - 8:idat_start - 4])[0]
    raw = zlib.decompress(data[idat_start:idat_start + idat_len])
    w, h = _png_size(out)
    assert len(raw) == h * (1 + w * 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_render_sheet.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'render_sheet'`

- [ ] **Step 3: Implement** — create `scripts/render_sheet.py`:

```python
"""Render every sprite state's frames into one PNG contact sheet.

Stdlib-only (struct + zlib hand-rolled PNG, 8-bit RGB, single IDAT).
Exists because this repo's dev environment has no GUI: art review
happens by eyeballing the emitted PNG, not a live tkinter window.

Usage: python3 scripts/render_sheet.py --out sheet.png [--scale 8] [--coat orange_tabby]
Prints one state name per line: the row legend, top to bottom.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokitty.sprites import ALL_STATES, get_frames, get_palette

BG = "#1c1c22"  # ui.py card background
GAP = 4  # background pixels between sprites, in device px


def _hex_to_rgb(color: str) -> bytes:
    return bytes(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _write_png(path: Path, rows: List[bytes], width: int) -> None:
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, len(rows), 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def render_sheet(out_path: Path, scale: int = 8, coat: str = "orange_tabby") -> List[str]:
    palette = get_palette(coat)
    bg = _hex_to_rgb(BG)
    states = list(ALL_STATES)
    all_frames = {s: get_frames(s) for s in states}

    cell_w = max(len(f[0][0]) for f in all_frames.values()) * scale
    cell_h = max(len(f[0]) for f in all_frames.values()) * scale
    max_frames = max(len(f) for f in all_frames.values())
    width = GAP + max_frames * (cell_w + GAP)
    height = GAP + len(states) * (cell_h + GAP)

    grid = [[bg] * width for _ in range(height)]
    for row_i, state in enumerate(states):
        for frame_i, frame in enumerate(all_frames[state]):
            x0 = GAP + frame_i * (cell_w + GAP)
            y0 = GAP + row_i * (cell_h + GAP)
            for r, row in enumerate(frame):
                for c, ch in enumerate(row):
                    color = palette.get(ch, "")
                    if not color:
                        continue
                    px = _hex_to_rgb(color)
                    for dy in range(scale):
                        for dx in range(scale):
                            grid[y0 + r * scale + dy][x0 + c * scale + dx] = px

    _write_png(out_path, [b"".join(row) for row in grid], width)
    return states


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scale", type=int, default=8)
    parser.add_argument("--coat", default="orange_tabby")
    args = parser.parse_args()
    for state in render_sheet(args.out, scale=args.scale, coat=args.coat):
        print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests, then render a real sheet and confirm it opens**

Run: `python3 -m pytest tests/test_render_sheet.py -v` — Expected: PASS
Run: `python3 scripts/render_sheet.py --out /tmp/tokitty-sheet-v1.png --scale 8` — Expected: prints the 10 state names; PNG exists. (Reviewer will view this baseline sheet of the CURRENT art for comparison in Task 3.)

- [ ] **Step 5: Commit**

```bash
git add scripts/render_sheet.py tests/test_render_sheet.py
git commit -m "feat(scripts): headless PNG contact-sheet renderer for sprite review"
```

---

### Task 3: 28×26 pose templates and frame specs (the art)

**OWNER-SESSION TASK — do not delegate the hand-tuning.** v1 precedent: sprite art is where craft matters; the owner session iterates on it directly, using the Task 2 contact sheet for visual review. A subagent may build the first procedural draft, but acceptance is the owner's eyeball.

**Files:**
- Modify: `tokitty/sprites.py` (SCALE, three templates, frame-spec dicts — public API unchanged)
- Test: `tests/test_sprites.py` (structural additions)

**Interfaces:**
- Consumes: `get_palette`/pattern char `"c"` from Task 1; contact sheet from Task 2.
- Produces: `SCALE = 4`; `SITTING_TEMPLATE`, `ALERT_TEMPLATE`, `FLOPPED_TEMPLATE` each exactly 26 rows × 28 cols; the ten state names and their frame counts unchanged; placeholder mechanics unchanged (`L`,`R`,`A` in sitting/alert; `L`,`T` in flopped; each exactly once per template).

- [ ] **Step 1: Write the failing structural tests** — append to `tests/test_sprites.py`:

```python
from tokitty.sprites import (
    ALERT_TEMPLATE, FLOPPED_TEMPLATE, SCALE, SITTING_TEMPLATE,
)

CAT_CANVAS_SIZE = 112  # mirrors ui.py; sprites must fit it


def test_new_grid_dimensions():
    for template in (SITTING_TEMPLATE, ALERT_TEMPLATE, FLOPPED_TEMPLATE):
        assert len(template) == 26
        assert all(len(row) == 28 for row in template)


def test_sprite_fits_cat_canvas():
    for state in ALL_STATES:
        frame = get_frames(state)[0]
        assert len(frame[0]) * SCALE <= CAT_CANVAS_SIZE
        assert len(frame) * SCALE <= CAT_CANVAS_SIZE


def test_placeholders_appear_exactly_once_per_template():
    for template, placeholders in (
        (SITTING_TEMPLATE, "LRA"),
        (ALERT_TEMPLATE, "LRA"),
        (FLOPPED_TEMPLATE, "LT"),
    ):
        joined = "".join(template)
        for ch in placeholders:
            assert joined.count(ch) == 1, f"{ch} appears {joined.count(ch)} times"


def test_pattern_region_is_used():
    joined = "".join(SITTING_TEMPLATE + ALERT_TEMPLATE + FLOPPED_TEMPLATE)
    assert "c" in joined, "templates must use the patch region so coats can differ"
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python3 -m pytest tests/test_sprites.py -v`
Expected: the four new tests FAIL (templates are 13×15, no `c` cells); all pre-existing tests still PASS.

- [ ] **Step 3: Generate first-draft 28×26 templates procedurally** (scratch script, not committed — same as v1). Stamp simple shapes onto a 28×26 grid of `"."`: head = ellipse (center ~(13, 7), radii ~(7, 6)) in `o` with `k` 1-px outline; ears = triangles; body = wider ellipse below in `o`/`w` chest; tabby stripes = 3–4 short `s` runs across head-top and back; patch region = a 2×3 `c` block on the chest and one on the back haunch; shading `O` along the right/lower silhouette edge; tail curled around the base. For FLOPPED, one wide low ellipse lying on the baseline. Place placeholders: `L`/`R` eye cells on the head ellipse, `A` nose cell, `T` tail tip (flopped only). Then bake the three grids into `sprites.py`, set `SCALE = 4`, keep every `*_FRAME_SPECS` dict's keys and substitution semantics identical.

- [ ] **Step 4: Structural tests pass**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS.

- [ ] **Step 5: Visual iteration loop (owner)** — render and view, tune grid cells by hand, repeat:

Run: `python3 scripts/render_sheet.py --out /tmp/tokitty-sheet-v2.png --scale 8`
Owner views the PNG (the harness can read images) against the v1 baseline sheet. Acceptance: each of the 10 states reads clearly at a glance as its intended pose/mood; frame pairs animate sensibly (compare A/B columns); silhouette, ears, eyes, and tail readable; stripes and shading not noisy. Iterate: edit grids → re-render → re-view. Budget several rounds; this is the craft step.

- [ ] **Step 6: Commit**

```bash
git add tokitty/sprites.py tests/test_sprites.py
git commit -m "feat(sprites): 28x26 hi-res pose templates at SCALE 4, pattern regions (closes #1, #2)"
```

---

### Task 4: On-desktop verification + docs + PR

**Files:**
- Modify: `README.md` (Known limitations bullet about 15x13 art)
- No `ui.py` changes expected (it imports `SCALE`/`PALETTE`; 28×4=112 ≤ CAT_CANVAS_SIZE).

- [ ] **Step 1: Live check on the real desktop (owner, Windows side).** Launch `pythonw.exe -m tokitty` from the repo root via PowerShell; cycle key states with the harness, e.g.:

```powershell
$env:TOKITTY_DEBUG_STATE="panicked"; pythonw.exe -m tokitty
```

Check at least `sleeping`, `panicked`, `flopped`, `confused`: sprite centered in the canvas, no clipping, crisp at the monitor's DPI scaling, animation alternating. Screenshot-driven, same as v1's visual fixes.

- [ ] **Step 2: Update README** — replace the Known-limitations bullet:

> - Sprite art is a first pass: a 15x13 grid composed from three reusable pose templates (sitting calm, sitting alert, lying down), not a fully independent illustration per state. Recognizable and animated, not polished.

with:

> - Sprite art is composed from three reusable 28x26 pose templates (sitting calm, sitting alert, lying down) with per-state substitutions, not a fully independent illustration per state.

- [ ] **Step 3: Full suite + commit**

Run: `python3 -m pytest tests/ -v` — Expected: ALL PASS

```bash
git add README.md
git commit -m "docs: note the 28x26 sprite grid in known limitations"
```

- [ ] **Step 4: Push branch and open PR**

```bash
git push -u origin feat/sprite-upgrade
```

Open a PR `feat/sprite-upgrade` → `main` titled "Phase 1: sprite resolution upgrade (28x26 @ SCALE 4, coat/pattern palette)" with body "Closes #1. Closes #2." (Use the GitHub REST API with the stored token — `gh` is not installed; POST /repos/nickwolf/tokitty/pulls.)
