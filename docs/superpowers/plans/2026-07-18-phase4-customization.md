# Phase 4 — Your Cat (Customization) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coat presets, a color-picker dialog, and named cat labels — chosen per cat via right-click and persisted across restarts.

**Architecture:** `sprites.COATS` grows four presets (art task, Fable + owner review on rendered PNGs). A new `tokitty/customize.py` persists per-account customization (`coat`, hex `overrides`, `label`) to `<state-dir>/customization.json`, keyed by account name (`"default"` in single-account mode); `accounts.json`'s `coat` field seeds the initial preset. `ui.Pane` gains a per-pane palette + card/bar color overrides + optional name label; the context menu becomes pane-aware with a Coat submenu and a stdlib `tkinter.colorchooser` Customize dialog. `__main__` wires load-at-startup and save-on-change.

**Tech Stack:** Python stdlib only (tkinter + tkinter.colorchooser, json). pytest.

## Global Constraints

- stdlib only; `ui.py` (and the new dialog code inside it) remain the only tkinter importers
- Explicitly out of scope: hats/accessories (spec cut them); per-model bars; any Phase 5 media work
- Tests never touch real `~/.claude*` files or the real state dir — `tmp_path` only
- Conventional commits, **no AI attribution ever**
- All 251 existing tests stay green after every task
- Sprite/palette hex choices are ART: produced by a Fable-model subagent, approved by Nick on rendered contact sheets (`scripts/render_sheet.py`) BEFORE dependent tasks build on them
- Known latent issue to fix here (from Phase 2 ledger): done_hop's ground line uses coat char `s`, so changing coat would recolor the ground — the ground must get its own non-coat char
- Persistence design decision (locked at plan approval): customization lives in `customization.json` in the state dir, NOT inside `accounts.json` — creating `accounts.json` flips credential-resolution mode, so single-account customization must not create it. `accounts.json`'s optional `coat` remains a seed default only.

---

### Task 1: Coat preset palettes (ART — Fable subagent + owner gate)

**Files:**
- Modify: `tokitty/sprites.py` (`COATS` dict only)
- Test: `tests/test_sprites.py` (append)

**Interfaces:**
- Produces: `COATS` gains `gray_tabby`, `black`, `white`, `calico` — each defining exactly the same five keys as `orange_tabby` (`o` coat, `O` shading, `s` stripe, `c` patch, `p` inner ear).

**Art constraints (give these to the Fable art subagent verbatim):**
- Outline char `k` (`#2b1a12`) must stay readable against every coat — the `black` coat body must be visibly lighter than the outline (dark charcoal, not true black)
- `white` needs shading (`O`) with enough contrast to keep the pose silhouette readable on the `#1c1c22` card
- `calico` is the one preset where patch region `c` differs from the coat (orange patches on white base is the classic read); on all other presets `c` matches `o`
- Tabby presets keep visible stripes (`s` darker than `o`); `black`/`white` may set `s` to a subtle tone-on-tone shade
- All five presets share `BASE_PALETTE` accents unchanged

**Steps:**
- [ ] **Step 1: Failing test** — append to `tests/test_sprites.py`:

```python
def test_all_coats_define_identical_region_keys():
    expected = set(COATS["orange_tabby"].keys())
    assert set(COATS.keys()) == {"orange_tabby", "gray_tabby", "black", "white", "calico"}
    for name, coat in COATS.items():
        assert set(coat.keys()) == expected, name
        for char, color in coat.items():
            assert color.startswith("#") and len(color) == 7, (name, char)


def test_black_coat_body_lighter_than_outline():
    def lum(hex_color):
        r, g, b = (int(hex_color[i:i+2], 16) for i in (1, 3, 5))
        return 0.299 * r + 0.587 * g + 0.114 * b
    assert lum(COATS["black"]["o"]) > lum(BASE_PALETTE["k"]) + 15


def test_calico_patch_differs_from_coat():
    assert COATS["calico"]["c"] != COATS["calico"]["o"]
```

Run: `python3 -m pytest tests/test_sprites.py -v` — expected FAIL (missing coats).

- [ ] **Step 2: Fable art subagent produces the four palettes** (hex values for o/O/s/c/p per coat, honoring the art constraints above), added to `COATS`.
- [ ] **Step 3: Tests pass**: `python3 -m pytest tests/test_sprites.py -v`
- [ ] **Step 4: Render contact sheets for owner review** — one per coat: `python3 scripts/render_sheet.py` (check its CLI; if it lacks a coat argument, add `--coat NAME` passing through to `get_palette(coat)`). Send the PNGs to Nick. **HARD GATE: do not proceed to Task 2 until Nick approves the palettes.** Iterate hex values on his feedback.
- [ ] **Step 5: Commit** — `git commit -m "feat(sprites): gray_tabby, black, white, calico coat presets"`

---

### Task 2: Ground-line fix + per-coat frame rendering support

**Files:**
- Modify: `tokitty/sprites.py`
- Test: `tests/test_sprites.py` (append)

**Interfaces:**
- Produces: new non-coat char `G` in `BASE_PALETTE` (`"G": "#5a4632"` — fixed earth tone, never varies with coat); every ground-line cell in the done_hop/ground templates uses `G` instead of literal `s`; `get_palette(coat)` covers every char used by every frame of every state (asserted).

**Steps:**
- [ ] **Step 1: Failing tests**:

```python
def test_ground_line_is_not_coat_colored():
    for frames in (get_frames("done_hop"),):
        for frame in frames:
            bottom_rows = frame[-3:]
            joined = "".join("".join(r) for r in bottom_rows)
            assert "G" in joined  # ground exists
    # the ground char is defined in BASE_PALETTE, not any coat
    assert "G" in BASE_PALETTE
    for coat in COATS.values():
        assert "G" not in coat


def test_every_state_frame_char_is_in_every_coat_palette():
    from tokitty.mood import ALL_SPRITE_STATES  # or enumerate get_frames-known states; see note
    for coat in COATS:
        palette = get_palette(coat)
        for state in ALL_SPRITE_STATES:
            for frame in get_frames(state):
                for row in frame:
                    for ch in row:
                        assert ch in palette, (coat, state, ch)
```

(Note: if no `ALL_SPRITE_STATES` constant exists, enumerate the states `get_frames` knows — read `sprites.py`'s frame registry and list them explicitly in the test.)

- [ ] **Step 2: Implement** — add `G` to `BASE_PALETTE`; replace the ground-line literal `s` cells (the rows the `# ground line` comments in sprites.py mark, around lines 283–300) with `G` in the template/frame data. Keep the explanatory comments, updating `"s"` mentions to `"G"`.
- [ ] **Step 3: All sprite tests pass**, then full suite: `python3 -m pytest -q`
- [ ] **Step 4: Commit** — `git commit -m "fix(sprites): ground line gets its own non-coat color char"`

---

### Task 3: `customize.py` — persistence + palette override model

**Files:**
- Create: `tokitty/customize.py`
- Test: `tests/test_customize.py`

**Interfaces:**
- Produces:
  - `Customization` frozen dataclass: `coat: str = "orange_tabby"`, `overrides: Dict[str, str] = field(default_factory=dict)`, `label: str = ""`
  - Override keys (closed set): `"coat_base"` (char `o`), `"coat_shade"` (char `O`), `"card_bg"`, `"bar_fill"` — values `#rrggbb`
  - `load_customization(state_dir: Path) -> Dict[str, Customization]` — keyed by account name; missing/invalid file ⇒ `{}`; unknown override keys dropped on load
  - `save_customization(state_dir: Path, data: Dict[str, Customization]) -> None` — atomic write (temp + `os.replace`)
  - `effective_palette(custom: Customization) -> Dict[str, str]` — `get_palette(custom.coat)` with `coat_base`→`o`, `coat_shade`→`O` overrides applied
  - `SINGLE_KEY = "default"` — the key single-account mode uses

**Steps:**
- [ ] **Step 1: Failing tests** covering: roundtrip save/load; absent file ⇒ `{}`; corrupt JSON ⇒ `{}`; unknown coat name in file falls back to `"orange_tabby"` on load; unknown override key dropped; invalid hex value dropped; `effective_palette` applies `coat_base`/`coat_shade` and ignores `card_bg`/`bar_fill` (those are consumed by the UI, not the sprite palette); empty overrides ⇒ palette identical to `get_palette(coat)`.
- [ ] **Step 2: Implement** (stdlib json/dataclasses; validate hex with a `re.fullmatch(r"#[0-9a-fA-F]{6}", v)`).
- [ ] **Step 3: Tests + full suite pass.**
- [ ] **Step 4: Commit** — `git commit -m "feat(customize): persistent per-account coat, color overrides, and label"`

---

### Task 4: Per-pane palette, card/bar colors, and name label in `ui.py`

**Files:**
- Modify: `tokitty/ui.py`
- Test: `tests/test_ui_layout.py` (append; headless-safe — no Tk root)

**Interfaces:**
- Consumes: `effective_palette`, `Customization` (Task 3)
- Produces:
  - `Pane.__init__(parent, palette: Optional[Dict[str, str]] = None, card_bg: Optional[str] = None, bar_fill: Optional[str] = None, label: str = "")` — all default to current behavior (module `PALETTE`, `BG_COLOR`, threshold `bar_color()`, no label)
  - `Pane.set_appearance(palette=None, card_bg=None, bar_fill=None, label=None)` — live re-style without rebuild (used by the menu/dialog callbacks); pass `None` to leave a slot unchanged, `""`/default sentinel semantics documented in the docstring
  - `_draw_frame` reads `self._palette` (instance) instead of module `PALETTE`
  - `bar_fill` override, when set, replaces `bar_color(pct)`'s return for both bars; `card_bg` replaces `BG_COLOR` for that pane's frame + widgets (accent flash still wins while active, reverting to the pane's `card_bg` not global `BG_COLOR`)
  - label rendered as small dim text at the pane's top-right (`anchor="ne"`, x=CARD_WIDTH-6, y=4, `DIM_COLOR`, Segoe UI 8) — empty string renders nothing
- Constraint: default-constructed `Pane` must render pixel-identically to Phase 3 (all Phase 3 callers unchanged).

**Steps:**
- [ ] **Step 1: Failing tests** — headless assertions: `Pane` accepts the new kwargs with the stated defaults (inspect `inspect.signature`); `card_height` unchanged; a pure helper `resolve_bar_fill(pct, override)` (extract it: returns `override or bar_color(pct)`) unit-tested for both branches.
- [ ] **Step 2: Implement.** The render path changes: everywhere `BG_COLOR` appears inside `Pane`, use `self._card_bg`; everywhere `bar_color(...)` is called, use `resolve_bar_fill(...)`; `ACCENT_BG` handling reverts to `self._card_bg`.
- [ ] **Step 3: Full suite green.**
- [ ] **Step 4: Commit** — `git commit -m "feat(ui): per-pane palette, card/bar color overrides, and cat name label"`

---

### Task 5: Pane-aware context menu + Customize dialog

**Files:**
- Modify: `tokitty/ui.py`
- Test: `tests/test_ui_layout.py` (append what's headless-testable)

**Interfaces:**
- Consumes: `Pane.set_appearance` (Task 4), `COATS` (Task 1)
- Produces:
  - Right-click resolves which pane was clicked: `pane_index_at(y_root_relative: int) -> int` = `min(y // PANE_HEIGHT, pane_count - 1)` — pure function, unit-tested
  - Menu gains, per click, a `Coat` submenu (one radio entry per `COATS` key, current one checked) and a `Customize…` entry, both acting on the clicked pane
  - `TokittyWindow.on_customization_changed: Optional[Callable[[int, str, Optional[str]], None]]` — callback `(pane_index, field, value)` where field ∈ {`"coat"`, `"coat_base"`, `"coat_shade"`, `"card_bg"`, `"bar_fill"`, `"reset"`}; `__main__` (Task 6) subscribes to persist
  - Customize dialog: `tk.Toplevel` with four rows (coat base / coat shading / card background / bar color), each a button opening `tkinter.colorchooser.askcolor`, plus `Reset to preset` and `Close`. Choosing a color applies immediately (live preview via `set_appearance`) and fires the callback; Reset clears all four overrides.
- Constraint: dialog code lives in `ui.py` (sole tkinter importer); dialog must not break always-on-top (set `transient` on root).

**Steps:**
- [ ] **Step 1: Failing test** for `pane_index_at` (0 for y<128, 1 for 128≤y<256, clamps to last pane for y beyond bottom edge).
- [ ] **Step 2: Implement** menu rework + dialog.
- [ ] **Step 3: Full suite green.** GUI behavior itself is covered by the Task 7 manual gate.
- [ ] **Step 4: Commit** — `git commit -m "feat(ui): coat submenu and Customize color-picker dialog per pane"`

---

### Task 6: Wiring + persistence in `__main__.py`

**Files:**
- Modify: `tokitty/__main__.py`
- Test: `tests/test_main.py` (append)

**Interfaces:**
- Consumes: `load_customization`/`save_customization`/`effective_palette`/`SINGLE_KEY` (Task 3), `Pane.set_appearance` (Task 4), `on_customization_changed` (Task 5), `Account.coat`/`Account.name` (Phase 3)
- Produces:
  - At startup: per account, resolve `Customization` = stored entry (key = account name, or `SINGLE_KEY` single-mode) else new one seeded with `Account.coat` (when set and valid) — pure helper `initial_customization(account, stored) -> Customization`, unit-tested (stored beats seed; invalid seed coat falls back to default)
  - Default labels: dual mode ⇒ account name; single mode ⇒ `""` — unless the stored customization has an explicit label (pure helper `initial_label(account, custom, dual: bool)`, unit-tested)
  - Pane construction/appearance applies `effective_palette`, `card_bg`, `bar_fill`, label
  - `on_customization_changed` handler mutates the right entry and `save_customization`s immediately (write-through)
- Constraint: `--debug-print` untouched; no tkinter import outside `run_gui`.

**Steps:**
- [ ] **Step 1: Failing tests** for `initial_customization` and `initial_label` covering: no stored + no seed ⇒ orange_tabby; accounts.json seed respected; stored beats seed; invalid seed coat ⇒ default; label rules single vs dual vs explicit.
- [ ] **Step 2: Implement** helpers + run_gui wiring.
- [ ] **Step 3: Full suite green.**
- [ ] **Step 4: Commit** — `git commit -m "feat(dual): load, apply, and persist per-account customization"`

---

### Task 7: README + manual verification gate

**Files:**
- Modify: `README.md`

**Steps:**
- [ ] **Step 1: README** — Customization section: right-click → Coat / Customize…; the five presets; overrides stored in `customization.json` (state dir); label defaults (account name in dual mode); `accounts.json` `coat` = initial default only. Keep security-section claims truthful (customization.json is a new persisted file — disclose it alongside position.json).
- [ ] **Step 2: Full suite green**; commit `git commit -m "docs: customization (coats, color picker, named cats)"`
- [ ] **Step 3: Manual gate (Nick, Windows)** — includes the still-pending Phase 3 dual-pane check: real `accounts.json`, both cats render with distinct coats; change a coat via menu (persists across restart); Customize dialog live-preview + reset; labels render; single-account mode with no `accounts.json` still v1-clean.

---

## Not in this phase

- Hats/accessories (spec: cut), per-model bars, Phase 5 media
- Live accounts.json reload; multi-window

## Self-review notes

- Spec coverage: coat presets via palette dicts + right-click per cat + persisted (T1/T5/T6), colorchooser dialog with hex overrides + reset (T3/T4/T5), named cats with dual-mode defaults (T4/T6), persistence file decision documented in Global Constraints
- Type consistency: `Customization` produced T3 consumed T4/T6; `set_appearance` produced T4 consumed T5/T6; callback signature `(pane_index, field, value)` produced T5 consumed T6
- Art gate: Task 1 blocks on Nick's PNG approval before anything builds on the palettes
