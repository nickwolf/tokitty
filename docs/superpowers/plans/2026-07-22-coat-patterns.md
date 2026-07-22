# Coat Patterns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the cat a **pattern** axis orthogonal to coat **color** — a `colorway` (tone palette) × `pattern` (region→tone map) model — with new recolorable regions (paws/points/tail/belly), ~10 patterns, and randomization (#32 + #38).

**Architecture:** `sprites.COATS` (bundled color+markings) is replaced by `COLORWAYS` (tone slots `coat/shade/mark/light/ear`) × `PATTERNS` (each coat-driven char → a tone token or literal hex), resolved by `resolve_palette(colorway, pattern)`. Persistence (`customization.json`) stores `colorway`+`pattern` with a load-time migration of the five legacy `coat` names. The single-source `menu.py` model grows Colorway▸/Pattern▸ submenus + Randomize + Surprise-me; `randomize.py` rolls curated looks; `settings.py` gains a `surprise_me` toggle. New sprite regions are owner-only, gated art passes rendered via `render_sheet.py`.

**Tech Stack:** Python 3.10+ stdlib (tkinter, json, dataclasses, random), pytest. Existing runtime deps only (pystray, Pillow — no new deps expected).

## Global Constraints

- **Commits authored by Nick alone.** NO `Co-Authored-By`, AI-attribution, or session-URL lines in any commit.
- **Orthogonal model, not bundled presets:** `Customization` stores `colorway` + `pattern` (replacing `coat`). Legacy `coat` names migrate at load via `LEGACY_COAT_MAP`; old `customization.json`/`accounts.json` keep working.
- **Persistence homes unchanged:** per-account look → `customization.json` (state dir); app-global toggle → `settings.json`. Never `accounts.json` (flips credential mode).
- **Tray discipline (from #21) is preserved:** every menu getter pystray can evaluate off-thread (`current_colorway`, `current_pattern`, `surprise_me`) reads **plain-Python shadow state**, never a tk Var/widget. The tray now has **two** off-thread radio getters, not one.
- **No new runtime deps** expected. If one is proposed, it gets the tray's first-dep blast-radius review before landing.
- **CI stays green on all 8 checks** (test matrix ubuntu/macos/windows × py3.10/3.14, xvfb `smoke -m gui`, `lint` pinned `ruff==0.15.22`). Display-requiring tests are marked `@pytest.mark.gui` (deselected by `addopts = -m "not gui"`).
- **New sprite art is owner-only (Nick), never delegated** (Tasks 8–9). Art is gated on Nick's approval of PNG contact sheets (`scripts/render_sheet.py`) + the live widget.
- **TDD throughout**, frequent commits, exact paths.
- **Randomization never rolls free-form hex** — only curated `colorway`+`pattern`. "Never override an explicit pick" is scoped to first-run seeding; Randomize and Surprise-me are explicit and **do** write.

---

### Task 1: `sprites.py` — colorway × pattern model, additive + byte-identical proof

Introduce `COLORWAYS`, `PATTERNS`, `REGION_CHARS`, `LEGACY_COAT_MAP`, and `resolve_palette` **alongside** the existing `COATS`/`get_palette` (both untouched). Prove `resolve_palette` reproduces every legacy coat byte-for-byte. No caller changes; suite stays green.

**Files:**
- Modify: `tokitty/sprites.py` (add tables + resolver; `COATS`/`get_palette` unchanged)
- Test: `tests/test_sprites.py` (append)

**Interfaces:**
- Produces: `COLORWAYS: Dict[str, Dict[str,str]]` (keys `coat,shade,mark,light,ear`); `PATTERNS: Dict[str, Dict[str,str]]` (values = tone token in `{coat,shade,mark,light,white}` **or** literal `#rrggbb`); `REGION_CHARS: Tuple[str,...] = ("o","O","s","c")`; `LEGACY_COAT_MAP: Dict[str, Tuple[str,str]]`; `resolve_palette(colorway="orange", pattern="tabby") -> Dict[str,str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sprites.py`:

```python
import pytest
from tokitty.sprites import (
    BASE_PALETTE, COLORWAYS, PATTERNS, REGION_CHARS, LEGACY_COAT_MAP,
    resolve_palette, get_frames, ALL_STATES,
)

# Frozen snapshot of the five legacy coats' region colors (o,O,s,c,p),
# captured so this proof survives COATS being deleted in Task 2.
_LEGACY_REGIONS = {
    "orange_tabby": {"o": "#e8823c", "O": "#c26a2c", "s": "#a8541f", "c": "#e8823c", "p": "#f6b8c8"},
    "gray_tabby":   {"o": "#a4aec2", "O": "#818ba0", "s": "#5f6879", "c": "#a4aec2", "p": "#e3a9ba"},
    "black":        {"o": "#4a4653", "O": "#38343f", "s": "#575263", "c": "#4a4653", "p": "#a8798c"},
    "white":        {"o": "#f1ebdf", "O": "#c4bcae", "s": "#ded6c6", "c": "#f1ebdf", "p": "#f6b8c8"},
    "calico":       {"o": "#f1ebdf", "O": "#c4bcae", "s": "#453a33", "c": "#e8823c", "p": "#f6b8c8"},
}


@pytest.mark.parametrize("legacy_name, regions", list(_LEGACY_REGIONS.items()))
def test_resolve_palette_reproduces_legacy_byte_identical(legacy_name, regions):
    colorway, pattern = LEGACY_COAT_MAP[legacy_name]
    palette = resolve_palette(colorway, pattern)
    for char, color in regions.items():          # region chars match legacy exactly
        assert palette[char] == color, (legacy_name, char)
    for char, color in BASE_PALETTE.items():      # every other char is untouched BASE_PALETTE
        if char in ("o", "O", "s", "c", "p"):
            continue
        assert palette[char] == color, (legacy_name, char)


def test_every_pattern_covers_region_chars():
    for name, pat in PATTERNS.items():
        assert set(pat.keys()) == set(REGION_CHARS), name


def test_colorways_define_all_tone_slots():
    for name, cw in COLORWAYS.items():
        assert set(cw.keys()) == {"coat", "shade", "mark", "light", "ear"}, name
        for k, v in cw.items():
            assert v.startswith("#") and len(v) == 7, (name, k)


def test_black_colorway_body_lighter_than_outline():
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.299 * r + 0.587 * g + 0.114 * b
    assert lum(COLORWAYS["black"]["coat"]) > lum(BASE_PALETTE["k"]) + 15


def test_every_state_char_defined_for_every_look():
    for colorway in COLORWAYS:
        for pattern in PATTERNS:
            palette = resolve_palette(colorway, pattern)
            for state in ALL_STATES:
                for frame in get_frames(state):
                    for row in frame:
                        for ch in row:
                            assert ch in palette, (colorway, pattern, state, ch)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sprites.py -k "resolve_palette or region_chars or tone_slots or every_state_char or black_colorway" -v`
Expected: FAIL — `ImportError: cannot import name 'COLORWAYS' from 'tokitty.sprites'`.

- [ ] **Step 3: Add the model to `tokitty/sprites.py`**

First extend the typing import at the top:

```python
from typing import Dict, List, Tuple
```

Then, immediately **after** the existing `COATS = {...}` block (leave `COATS`, `get_palette`, and `PALETTE` exactly as they are), insert:

```python
# --- colorway x pattern model (replaces the bundled COATS in Task 2) ---
# A colorway is a tone palette; a pattern maps each coat-driven char to a
# tone token ("coat"/"shade"/"mark"/"light"/"white") or a literal #rrggbb.
COLORWAYS: Dict[str, Dict[str, str]] = {
    "orange": {"coat": "#e8823c", "shade": "#c26a2c", "mark": "#a8541f", "light": "#f7e0c0", "ear": "#f6b8c8"},
    "gray":   {"coat": "#a4aec2", "shade": "#818ba0", "mark": "#5f6879", "light": "#e4e8ef", "ear": "#e3a9ba"},
    "black":  {"coat": "#4a4653", "shade": "#38343f", "mark": "#575263", "light": "#c9c6cf", "ear": "#a8798c"},
    "white":  {"coat": "#f1ebdf", "shade": "#c4bcae", "mark": "#ded6c6", "light": "#f6f2ea", "ear": "#f6b8c8"},
}

# Which template chars a pattern controls. Grows in Task 8 with the new
# regions (m paws, x points, y tail, u belly).
REGION_CHARS: Tuple[str, ...] = ("o", "O", "s", "c")

PATTERNS: Dict[str, Dict[str, str]] = {
    "solid":       {"o": "coat", "O": "shade", "s": "coat", "c": "coat"},
    "tabby":       {"o": "coat", "O": "shade", "s": "mark", "c": "coat"},
    "bicolor":     {"o": "coat", "O": "shade", "s": "coat", "c": "white"},
    "tabby_white": {"o": "coat", "O": "shade", "s": "mark", "c": "white"},
    "calico":      {"o": "coat", "O": "shade", "s": "#453a33", "c": "#e8823c"},
}

# Old bundled coat names -> (colorway, pattern). Legacy black/white carried a
# subtle tone-on-tone sheen (a mark-tone stripe), so they map to +tabby, not
# +solid, to reproduce that exactly; users can switch to solid for a flat look.
LEGACY_COAT_MAP: Dict[str, Tuple[str, str]] = {
    "orange_tabby": ("orange", "tabby"),
    "gray_tabby":   ("gray", "tabby"),
    "black":        ("black", "tabby"),
    "white":        ("white", "tabby"),
    "calico":       ("white", "calico"),
}


def resolve_palette(colorway: str = "orange", pattern: str = "tabby") -> Dict[str, str]:
    """Full char->color map for one colorway x pattern. The colorway supplies
    the tone slots; the pattern maps each coat-driven char to a tone token or a
    literal hex. Every other char is BASE_PALETTE unchanged."""
    cw = COLORWAYS[colorway]
    tones = {
        "coat": cw["coat"], "shade": cw["shade"], "mark": cw["mark"],
        "light": cw["light"], "white": BASE_PALETTE["w"],
    }
    merged = dict(BASE_PALETTE)
    merged["p"] = cw["ear"]
    for char, src in PATTERNS[pattern].items():
        merged[char] = tones.get(src, src)  # tone token -> tone, else literal hex
    return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sprites.py -v`
Expected: PASS (new tests + all existing COATS tests still green — nothing was removed).

- [ ] **Step 5: Lint + commit**

```bash
ruff check tokitty/sprites.py tests/test_sprites.py
git add tokitty/sprites.py tests/test_sprites.py
git commit -m "feat(sprites): colorway x pattern model alongside COATS (byte-identical)"
```

---

### Task 2: The coordinated flip — persistence + UI + tray to colorway × pattern

This is the one near-atomic change: `Customization.coat` disappearing invalidates `customize`, `__main__`, `ui`, `menu`, and `tray` simultaneously, so they flip together and today's looks stay byte-identical. **Six existing test files reference `coat`/`COATS` and go red the moment `COATS` is removed — migrating them is part of this task, not a surprise.** No new patterns/colorways/art here: the app simply offers the Task-1 sets via two menus.

**Files:**
- Modify: `tokitty/sprites.py` (remove `COATS`; make `get_palette` a legacy shim; repoint `PALETTE`)
- Modify: `tokitty/customize.py` (`Customization` fields + migration + `effective_palette`)
- Modify: `tokitty/__main__.py` (`initial_customization`, `apply_customization`, `handle_customization_changed`, tray construction)
- Modify: `tokitty/ui.py` (`Pane` colorway/pattern, `set_appearance`, `build_menu_model`, coat→colorway/pattern selectors)
- Modify: `tokitty/menu.py` (`build_menu` Colorway▸/Pattern▸)
- Modify: `tokitty/tray.py` (`TrayManager(colorway, pattern)`, image factory)
- Test: `tests/test_sprites.py`, `tests/test_customize.py`, `tests/test_main.py`, `tests/test_ui_layout.py`, `tests/test_menu.py`, `tests/test_tray.py`

**Interfaces:**
- Produces: `sprites.get_palette(coat)` = legacy shim over `resolve_palette(*LEGACY_COAT_MAP[coat])`; `Customization(colorway="orange", pattern="tabby", overrides={}, label="")`; `customize.effective_palette(custom)` via `resolve_palette`; `Pane(...colorway, pattern)`, `Pane.set_appearance(...colorway, pattern)`; `TokittyWindow.on_customization_changed` `field ∈ {"colorway","pattern","coat_base","coat_shade","card_bg","bar_fill","label","reset"}`; `menu.build_menu(*, colorways, patterns, current_colorway, current_pattern, on_colorway, on_pattern, ...)`; `TrayManager(root, menu_provider, state_dir, colorway="orange", pattern="tabby", icon_factory=None, image_factory=None)` with `image_factory(colorway, pattern)`.

- [ ] **Step 1: Capture the pre-flip render (correctness oracle)**

Run: `python3 scripts/render_sheet.py --out /tmp/before.png && sha256sum /tmp/before.png`
Note the hash. The default cat must render identically after the flip (Step 13).

- [ ] **Step 2: `sprites.py` — remove `COATS`, shim `get_palette`, repoint `PALETTE`**

Delete the entire `COATS: Dict[str, Dict[str, str]] = { ... }` block. Replace the existing `get_palette` + `PALETTE` lines:

```python
def get_palette(coat: str = "orange_tabby") -> Dict[str, str]:
    """Full character->color mapping for one coat preset."""
    merged = dict(BASE_PALETTE)
    merged.update(COATS[coat])
    return merged


PALETTE: Dict[str, str] = get_palette()
```

with:

```python
def get_palette(coat: str = "orange_tabby") -> Dict[str, str]:
    """Legacy shim: a bundled coat name -> resolve_palette. New code calls
    resolve_palette(colorway, pattern) directly. Kept so scripts and any other
    legacy callers keep working until they migrate."""
    return resolve_palette(*LEGACY_COAT_MAP[coat])


PALETTE: Dict[str, str] = resolve_palette("orange", "tabby")
```

- [ ] **Step 3: `sprites.py` — drop the now-dead COATS tests**

In `tests/test_sprites.py`, delete the four phase-4 tests that read `COATS` (superseded by Task 1's tests): `test_all_coats_define_identical_region_keys`, `test_black_coat_body_lighter_than_outline`, `test_calico_patch_differs_from_coat`, `test_every_state_frame_char_is_in_every_coat_palette`. Also remove `COATS` from that file's `from tokitty.sprites import ...` line if present.

- [ ] **Step 4: `customize.py` — new fields + migration + resolver**

Replace the `Customization` dataclass:

```python
@dataclass(frozen=True)
class Customization:
    coat: str = "orange_tabby"
    overrides: Dict[str, str] = field(default_factory=dict)
    label: str = ""
```

with:

```python
@dataclass(frozen=True)
class Customization:
    colorway: str = "orange"
    pattern: str = "tabby"
    overrides: Dict[str, str] = field(default_factory=dict)
    label: str = ""
```

In `load_customization`, replace the coat-resolution block:

```python
        coat = entry.get("coat")
        if not isinstance(coat, str) or coat not in sprites.COATS:
            coat = "orange_tabby"
        label = entry.get("label")
        if not isinstance(label, str):
            label = ""
        result[key] = Customization(
            coat=coat,
            overrides=_clean_overrides(entry.get("overrides")),
            label=label,
        )
```

with:

```python
        colorway, pattern = _resolve_colorway_pattern(entry)
        label = entry.get("label")
        if not isinstance(label, str):
            label = ""
        result[key] = Customization(
            colorway=colorway,
            pattern=pattern,
            overrides=_clean_overrides(entry.get("overrides")),
            label=label,
        )
```

Add the migration helper above `load_customization`:

```python
def _resolve_colorway_pattern(entry: dict) -> "tuple[str, str]":
    """New files carry colorway+pattern; legacy files carry a single `coat`
    name. Invalid/missing values fall back through legacy translation to the
    orange+tabby default."""
    colorway = entry.get("colorway")
    pattern = entry.get("pattern")
    if not (isinstance(colorway, str) and colorway in sprites.COLORWAYS):
        colorway = None
    if not (isinstance(pattern, str) and pattern in sprites.PATTERNS):
        pattern = None
    if colorway is None or pattern is None:
        coat = entry.get("coat")
        if isinstance(coat, str) and coat in sprites.LEGACY_COAT_MAP:
            legacy_cw, legacy_pat = sprites.LEGACY_COAT_MAP[coat]
            colorway = colorway or legacy_cw
            pattern = pattern or legacy_pat
    return colorway or "orange", pattern or "tabby"
```

Replace `effective_palette`:

```python
def effective_palette(custom: Customization) -> Dict[str, str]:
    palette = dict(sprites.resolve_palette(custom.colorway, custom.pattern))
    if "coat_base" in custom.overrides:
        palette["o"] = custom.overrides["coat_base"]
    if "coat_shade" in custom.overrides:
        palette["O"] = custom.overrides["coat_shade"]
    return palette
```

Update the module docstring's override list if it names `coat` (leave override keys unchanged: `coat_base/coat_shade/card_bg/bar_fill`).

- [ ] **Step 5: `menu.py` — Colorway▸ / Pattern▸**

Replace `build_menu`'s signature head and coat items. Change the parameters `coats`, `current_coat`, `on_coat` to:

```python
def build_menu(
    *,
    colorways: List[str],
    patterns: List[str],
    current_colorway: Callable[[], str],
    current_pattern: Callable[[], str],
    on_colorway: Callable[[str], None],
    on_pattern: Callable[[str], None],
    on_customize: Callable[[], None],
    on_rename: Callable[[], None],
    on_refresh: Callable[[], None],
    always_on_top: Callable[[], bool],
    on_toggle_always_on_top: Callable[[], None],
    on_quit: Callable[[], None],
    tray_enabled: Optional[Callable[[], bool]] = None,
    on_toggle_tray: Optional[Callable[[], None]] = None,
) -> List[MenuItem]:
    colorway_items = [
        MenuItem(label=n, action=(lambda n=n: on_colorway(n)),
                 radio_selected=(lambda n=n: current_colorway() == n))
        for n in colorways
    ]
    pattern_items = [
        MenuItem(label=n, action=(lambda n=n: on_pattern(n)),
                 radio_selected=(lambda n=n: current_pattern() == n))
        for n in patterns
    ]
    items: List[MenuItem] = [
        MenuItem(label="Colorway", submenu=colorway_items),
        MenuItem(label="Pattern", submenu=pattern_items),
        MenuItem(label="Customize…", action=on_customize),
        MenuItem(label="Rename…", action=on_rename),
        MenuItem(separator=True),
        MenuItem(label="Refresh now", action=on_refresh),
        MenuItem(label="Always in front", action=on_toggle_always_on_top, checkbox=always_on_top),
    ]
    if on_toggle_tray is not None and tray_enabled is not None:
        items.append(MenuItem(label="Show tray icon", action=on_toggle_tray, checkbox=tray_enabled))
    items.append(MenuItem(separator=True))
    items.append(MenuItem(label="Exit", action=on_quit))
    return items
```

- [ ] **Step 6: `ui.py` — Pane colorway/pattern, set_appearance, menu model, selectors**

Change the import line `from tokitty.sprites import COATS, PALETTE, SCALE, get_frames` to:

```python
from tokitty.sprites import COLORWAYS, PATTERNS, PALETTE, SCALE, get_frames
```

In `Pane.__init__`, replace the `coat` parameter and field. Change the signature `..., label="", coat=None):` to `..., label="", colorway=None, pattern=None):` and replace:

```python
        self._coat = coat if coat is not None else "orange_tabby"
```

with:

```python
        self._colorway = colorway if colorway is not None else "orange"
        self._pattern = pattern if pattern is not None else "tabby"
```

In `Pane.set_appearance`, change the signature `..., label=None, coat=None) -> None:` to `..., label=None, colorway=None, pattern=None) -> None:` and replace:

```python
        if coat is not None:
            self._coat = coat
```

with:

```python
        if colorway is not None:
            self._colorway = colorway
        if pattern is not None:
            self._pattern = pattern
```

In `build_menu_model`, replace the `build_menu(...)` call body:

```python
        pane = self.panes[pane_index]
        return build_menu(
            coats=list(COATS.keys()),
            current_coat=(lambda p=pane: p._coat),
            on_coat=(lambda name, i=pane_index: self._select_coat(i, name)),
            on_customize=(lambda i=pane_index: self._open_customize_dialog(i)),
            ...
        )
```

with:

```python
        pane = self.panes[pane_index]
        return build_menu(
            colorways=list(COLORWAYS.keys()),
            patterns=list(PATTERNS.keys()),
            current_colorway=(lambda p=pane: p._colorway),
            current_pattern=(lambda p=pane: p._pattern),
            on_colorway=(lambda name, i=pane_index: self._select_colorway(i, name)),
            on_pattern=(lambda name, i=pane_index: self._select_pattern(i, name)),
            on_customize=(lambda i=pane_index: self._open_customize_dialog(i)),
            on_rename=(lambda i=pane_index: self._open_rename_dialog(i)),
            on_refresh=self._on_refresh_now,
            always_on_top=(lambda: self._always_on_top_bool),
            on_toggle_always_on_top=self._toggle_always_on_top,
            on_quit=self.on_quit,
            tray_enabled=self.tray_enabled,
            on_toggle_tray=self.on_toggle_tray,
        )
```

Replace the `_select_coat` method:

```python
    def _select_coat(self, pane_index: int, coat_name: str) -> None:
        self._fire_customization_changed(pane_index, "coat", coat_name)
```

with:

```python
    def _select_colorway(self, pane_index: int, name: str) -> None:
        self._fire_customization_changed(pane_index, "colorway", name)

    def _select_pattern(self, pane_index: int, name: str) -> None:
        self._fire_customization_changed(pane_index, "pattern", name)
```

Update the `on_customization_changed` doc comment in `__init__` to list `"colorway", "pattern"` instead of `"coat"`.

- [ ] **Step 7: `tray.py` — TrayManager takes colorway + pattern**

Replace `_default_image_factory`:

```python
def _default_image_factory(coat: str):
    from PIL import Image

    from tokitty.sprite_raster import raster_rgba
    from tokitty.sprites import get_frames, get_palette

    frame = get_frames("content")[0]
    width, height, raw = raster_rgba(frame, get_palette(coat), TRAY_ICON_SCALE)
    ...
```

with:

```python
def _default_image_factory(colorway: str, pattern: str):
    from PIL import Image

    from tokitty.sprite_raster import raster_rgba
    from tokitty.sprites import get_frames, resolve_palette

    frame = get_frames("content")[0]
    width, height, raw = raster_rgba(frame, resolve_palette(colorway, pattern), TRAY_ICON_SCALE)
    sprite = Image.frombytes("RGBA", (width, height), raw)
    side = max(width, height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(sprite, ((side - width) // 2, (side - height) // 2))
    return canvas
```

In `TrayManager.__init__`, change `coat: str = "orange_tabby"` to `colorway: str = "orange", pattern: str = "tabby"`, store `self._colorway = colorway` and `self._pattern = pattern` (remove `self._coat`), and in `_probe` change `self._image = self._image_factory(self._coat)` to `self._image = self._image_factory(self._colorway, self._pattern)`.

- [ ] **Step 8: `__main__.py` — flip the three helpers + tray construction**

Replace `initial_customization`:

```python
def initial_customization(account: Optional[Account], stored: Optional[Customization]) -> Customization:
    ...
    if stored is not None:
        return stored
    coat = account.coat if account is not None else None
    if isinstance(coat, str) and coat in sprites.COATS:
        return Customization(coat=coat)
    return Customization()
```

with:

```python
def _seed_from_account(account: Optional[Account]) -> Tuple[Optional[str], Optional[str]]:
    """Translate a legacy accounts.json `coat` seed to (colorway, pattern)."""
    coat = account.coat if account is not None else None
    if isinstance(coat, str) and coat in sprites.LEGACY_COAT_MAP:
        return sprites.LEGACY_COAT_MAP[coat]
    return None, None


def initial_customization(account: Optional[Account], stored: Optional[Customization]) -> Customization:
    """Stored (customization.json) always wins; else seed from the account's
    legacy `coat` when it names a known preset, else the orange+tabby default."""
    if stored is not None:
        return stored
    colorway, pattern = _seed_from_account(account)
    if colorway is not None:
        return Customization(colorway=colorway, pattern=pattern)
    return Customization()
```

In `apply_customization`, replace `coat=custom.coat,` with `colorway=custom.colorway, pattern=custom.pattern,`.

In `handle_customization_changed`, replace the `field == "coat"` branch:

```python
        if field == "coat":
            if value in sprites.COATS:
                custom = replace(custom, coat=value)
```

with:

```python
        if field == "colorway":
            if value in sprites.COLORWAYS:
                custom = replace(custom, colorway=value)
        elif field == "pattern":
            if value in sprites.PATTERNS:
                custom = replace(custom, pattern=value)
```

In `run_gui`, replace the tray construction:

```python
    pane0_coat = window.panes[0]._coat
    tray = TrayManager(root, lambda: window.build_menu_model(0), state_dir, coat=pane0_coat)
```

with:

```python
    pane0 = window.panes[0]
    tray = TrayManager(root, lambda: window.build_menu_model(0), state_dir,
                       colorway=pane0._colorway, pattern=pane0._pattern)
```

- [ ] **Step 9: Migrate `tests/test_customize.py`**

Update every `Customization(coat="...")` to `Customization(colorway="...", pattern="...")`, every saved-dict `{"coat": ...}` fixture, and the coat-fallback test. Add migration coverage:

```python
def test_load_migrates_legacy_coat(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"coat": "calico", "label": "Mimi"}}', encoding="utf-8")
    got = load_customization(tmp_path)["default"]
    assert (got.colorway, got.pattern, got.label) == ("white", "calico", "Mimi")


def test_load_prefers_new_fields_over_legacy_coat(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"coat": "calico", "colorway": "gray", "pattern": "solid"}}', encoding="utf-8")
    got = load_customization(tmp_path)["default"]
    assert (got.colorway, got.pattern) == ("gray", "solid")


def test_load_unknown_colorway_pattern_defaults(tmp_path):
    (tmp_path / "customization.json").write_text(
        '{"default": {"colorway": "nope", "pattern": "nope"}}', encoding="utf-8")
    got = load_customization(tmp_path)["default"]
    assert (got.colorway, got.pattern) == ("orange", "tabby")


def test_save_writes_colorway_pattern_not_coat(tmp_path):
    save_customization(tmp_path, {"default": Customization(colorway="black", pattern="solid")})
    import json
    data = json.loads((tmp_path / "customization.json").read_text(encoding="utf-8"))
    assert data["default"]["colorway"] == "black"
    assert data["default"]["pattern"] == "solid"
    assert "coat" not in data["default"]


def test_effective_palette_uses_colorway_pattern(tmp_path):
    from tokitty.sprites import resolve_palette
    custom = Customization(colorway="gray", pattern="tabby")
    assert effective_palette(custom)["s"] == resolve_palette("gray", "tabby")["s"]
```

Keep the existing override-clean / roundtrip-label / corrupt-file tests, adjusting any `coat=` usage to `colorway=`/`pattern=`.

- [ ] **Step 10: Migrate `tests/test_main.py`**

`test_main.py` builds `Account(name, config_dir, coat=None)` and `Customization(coat=...)`. At THIS task `initial_customization` does **not** yet random-seed (Task 6 adds that), so every "no stored" case still resolves to the orange+tabby default via `_seed_from_account`. Rewrite the five affected `initial_customization` tests (existing names, ~lines 206–233) exactly:

```python
def test_initial_customization_no_stored_no_seed_defaults_orange_tabby():
    account = Account(name="Work", config_dir="/x")
    result = initial_customization(account, None)
    assert (result.colorway, result.pattern) == ("orange", "tabby")


def test_initial_customization_seeds_from_account_coat():
    account = Account(name="Work", config_dir="/x", coat="black")
    result = initial_customization(account, None)
    assert (result.colorway, result.pattern) == ("black", "tabby")


def test_initial_customization_stored_beats_seed():
    account = Account(name="Work", config_dir="/x", coat="black")
    stored = Customization(colorway="white", pattern="calico", label="Work Cat")
    assert initial_customization(account, stored) == stored


def test_initial_customization_invalid_seed_coat_falls_back_to_default():
    account = Account(name="Work", config_dir="/x", coat="not_a_real_coat")
    result = initial_customization(account, None)
    assert (result.colorway, result.pattern) == ("orange", "tabby")


def test_initial_customization_no_account_no_stored_defaults():
    result = initial_customization(None, None)
    assert (result.colorway, result.pattern) == ("orange", "tabby")
```

Also update the label-roundtrip test's construction (~line 268) from `Customization(coat="calico", overrides={...})` to `Customization(colorway="white", pattern="calico", overrides={...})`. The `initial_label` tests use `Customization()` / `Customization(label=...)` with no coat — leave them unchanged. **Note:** two of these (`..._invalid_seed_coat_falls_back_to_default`, `..._no_account_no_stored_defaults`) and `..._no_stored_no_seed_defaults_orange_tabby` change behavior in Task 6 (random seeding) and are rewritten there — that is expected, not churn to avoid.

- [ ] **Step 11: Migrate `tests/test_menu.py`**

Replace the `_kwargs` builder's `coats`/`current_coat`/`on_coat` with `colorways`, `patterns`, `current_colorway`, `current_pattern`, `on_colorway`, `on_pattern`, and update assertions:

```python
def _kwargs(**overrides):
    calls = {"colorway": [], "pattern": [], "customize": 0, "rename": 0,
             "refresh": 0, "toggle_aot": 0, "quit": 0, "toggle_tray": 0}
    state = {"colorway": "gray", "pattern": "tabby", "aot": True, "tray": True}
    base = dict(
        colorways=["orange", "gray", "black"],
        patterns=["solid", "tabby", "calico"],
        current_colorway=lambda: state["colorway"],
        current_pattern=lambda: state["pattern"],
        on_colorway=lambda c: calls["colorway"].append(c),
        on_pattern=lambda p: calls["pattern"].append(p),
        on_customize=lambda: calls.__setitem__("customize", calls["customize"] + 1),
        on_rename=lambda: calls.__setitem__("rename", calls["rename"] + 1),
        on_refresh=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
        always_on_top=lambda: state["aot"],
        on_toggle_always_on_top=lambda: calls.__setitem__("toggle_aot", calls["toggle_aot"] + 1),
        on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1),
    )
    base.update(overrides)
    return base, calls, state


def test_structure_and_labels():
    kwargs, _, _ = _kwargs()
    items = build_menu(**kwargs)
    labels = [i.label for i in items if not i.separator]
    assert labels == ["Colorway", "Pattern", "Customize…", "Rename…",
                      "Refresh now", "Always in front", "Exit"]
    assert [c.label for c in items[0].submenu] == ["orange", "gray", "black"]
    assert [p.label for p in items[1].submenu] == ["solid", "tabby", "calico"]


def test_radio_reflects_current_selection():
    kwargs, _, state = _kwargs()
    items = build_menu(**kwargs)
    assert [c.label for c in items[0].submenu if c.radio_selected()] == ["gray"]
    assert [p.label for p in items[1].submenu if p.radio_selected()] == ["tabby"]
    state["pattern"] = "calico"
    assert [p.label for p in build_menu(**kwargs)[1].submenu if p.radio_selected()] == ["calico"]


def test_action_wiring():
    kwargs, calls, _ = _kwargs()
    items = build_menu(**kwargs)
    items[0].submenu[1].action()   # colorway "gray"
    items[1].submenu[2].action()   # pattern "calico"
    assert calls["colorway"] == ["gray"]
    assert calls["pattern"] == ["calico"]
```

Keep `test_tray_item_absent_without_seam` / `test_tray_item_present_with_seam` (they still pass).

- [ ] **Step 12: Migrate `tests/test_ui_layout.py` and `tests/test_tray.py`**

In `tests/test_ui_layout.py`, update the menu-model labels assertion (Coat → Colorway + Pattern):

```python
            assert labels == ["Colorway", "Pattern", "Customize…", "Rename…",
                              "Refresh now", "Always in front", "Exit"]
```

and any `pane._coat` / `_select_coat` references to `pane._colorway`/`pane._pattern`.

In `tests/test_tray.py`, change the fake image factory and constructor kwargs:

```python
    kwargs = dict(
        root=root,
        menu_provider=lambda: ["model"],
        state_dir=tmp_path,
        colorway="orange",
        pattern="tabby",
        icon_factory=icon_factory,
        image_factory=lambda colorway, pattern: f"image:{colorway}:{pattern}",
    )
```

and in `test_guard_image_factory_raises`, update `def boom(coat):` to `def boom(colorway, pattern):`.

- [ ] **Step 13: Run the full suite, lint, and the render-diff gate**

Run: `python3 -m pytest -q`
Expected: all pass.
Run: `xvfb-run -a python3 -m pytest -m gui -q`
Expected: PASS.
Run: `ruff check .`
Expected: clean (`ruff==0.15.22`).
Run: `python3 scripts/render_sheet.py --out /tmp/after.png && sha256sum /tmp/after.png`
Expected: **identical hash to Step 1** — the default cat is byte-for-byte unchanged. If it differs, a tone/token/mapping drifted; fix before committing.

- [ ] **Step 14: Confirm no stray `COATS`/`_coat` references remain**

Run: `grep -rn "COATS\|\._coat\b\|get_palette(coat" tokitty/ tests/`
Expected: only the intentional `get_palette` legacy shim in `sprites.py` (and its docstring). Any other hit is a missed migration.

- [ ] **Step 15: Commit**

```bash
git add tokitty/sprites.py tokitty/customize.py tokitty/__main__.py tokitty/ui.py tokitty/menu.py tokitty/tray.py tests/
git commit -m "refactor: flip coat -> colorway x pattern across persistence, UI, tray"
```

---

### Task 3: `randomize.py` — curated rolls

A pure, injectable roller. Never touches free-form hex.

**Files:**
- Create: `tokitty/randomize.py`
- Test: `tests/test_randomize.py`

**Interfaces:**
- Produces: `random_look(colorways: Sequence[str], patterns: Sequence[str], rng: Optional[random.Random] = None) -> Tuple[str, str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_randomize.py`:

```python
import random

from tokitty.randomize import random_look


def test_returns_one_of_each_set():
    cws, pats = ["orange", "gray"], ["solid", "tabby"]
    cw, pat = random_look(cws, pats)
    assert cw in cws and pat in pats


def test_deterministic_with_injected_rng():
    cws, pats = ["orange", "gray", "black"], ["solid", "tabby", "calico"]
    a = random_look(cws, pats, rng=random.Random(1))
    b = random_look(cws, pats, rng=random.Random(1))
    assert a == b


def test_never_returns_outside_sets():
    cws, pats = ["orange"], ["solid"]
    for _ in range(20):
        assert random_look(cws, pats) == ("orange", "solid")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_randomize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tokitty.randomize'`.

- [ ] **Step 3: Write minimal implementation**

Create `tokitty/randomize.py`:

```python
"""Curated appearance randomization: pick a colorway and a pattern from the
existing preset keys. Never rolls free-form hex overrides -- curated presets
always look intentional. rng is injectable for deterministic tests."""
from __future__ import annotations

import random
from typing import Optional, Sequence, Tuple


def random_look(colorways: Sequence[str], patterns: Sequence[str],
                rng: Optional[random.Random] = None) -> Tuple[str, str]:
    r = rng or random
    return r.choice(list(colorways)), r.choice(list(patterns))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_randomize.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + commit**

```bash
ruff check tokitty/randomize.py tests/test_randomize.py
git add tokitty/randomize.py tests/test_randomize.py
git commit -m "feat(randomize): curated colorway+pattern roller"
```

---

### Task 4: `settings.py` — add the `surprise_me` toggle

**Files:**
- Modify: `tokitty/settings.py`
- Test: `tests/test_settings.py` (append)

**Interfaces:**
- Produces: `Settings(tray_enabled: bool = True, surprise_me: bool = False)`; `load_settings`/`save_settings` round-trip both, robust-load each independently.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settings.py`:

```python
def test_surprise_me_default_false(tmp_path):
    assert load_settings(tmp_path).surprise_me is False


def test_surprise_me_roundtrip(tmp_path):
    save_settings(tmp_path, Settings(tray_enabled=True, surprise_me=True))
    assert load_settings(tmp_path).surprise_me is True


def test_surprise_me_non_bool_defaults(tmp_path):
    (tmp_path / "settings.json").write_text('{"surprise_me": "yes"}', encoding="utf-8")
    assert load_settings(tmp_path).surprise_me is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_settings.py -k surprise -v`
Expected: FAIL — `TypeError` (unexpected kwarg) / `AttributeError: 'Settings' object has no attribute 'surprise_me'`.

- [ ] **Step 3: Implement**

In `tokitty/settings.py`, add the field to the dataclass:

```python
@dataclass(frozen=True)
class Settings:
    tray_enabled: bool = True
    surprise_me: bool = False
```

In `load_settings`, after the `tray_enabled` coercion and before the `return`, add:

```python
    surprise_me = data.get("surprise_me", False)
    if not isinstance(surprise_me, bool):
        surprise_me = False
    return Settings(tray_enabled=tray_enabled, surprise_me=surprise_me)
```

(Replace the existing `return Settings(tray_enabled=tray_enabled)`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_settings.py -v`
Expected: PASS (all, including existing tray tests).

- [ ] **Step 5: Lint + commit**

```bash
ruff check tokitty/settings.py tests/test_settings.py
git add tokitty/settings.py tests/test_settings.py
git commit -m "feat(settings): add surprise_me toggle"
```

---

### Task 5: Menu + UI seams for Randomize and Surprise-me

Add a `Randomize` command and a `Surprise me` checkbox to the single-source menu model (present only when their seams are wired, mirroring "Show tray icon"), and expose the seams on `TokittyWindow`. `surprise_me` is read from a plain-Python shadow (pystray reads it off-thread).

**Files:**
- Modify: `tokitty/menu.py`
- Modify: `tokitty/ui.py`
- Test: `tests/test_menu.py` (append), `tests/test_ui_layout.py` (append)

**Interfaces:**
- Produces: `build_menu(..., on_randomize=None, surprise_me=None, on_toggle_surprise=None)`; `TokittyWindow.on_randomize: Optional[Callable[[int], None]] = None`, `TokittyWindow.surprise_me: Optional[Callable[[], bool]] = None`, `TokittyWindow.on_toggle_surprise: Optional[Callable[[], None]] = None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_menu.py`:

```python
def test_randomize_and_surprise_absent_without_seams():
    kwargs, _, _ = _kwargs()
    labels = [i.label for i in build_menu(**kwargs) if not i.separator]
    assert "Randomize" not in labels
    assert "Surprise me" not in labels


def test_randomize_and_surprise_present_with_seams():
    kwargs, calls, state = _kwargs(
        on_randomize=lambda: calls.__setitem__("rand", calls.get("rand", 0) + 1),
        surprise_me=lambda: state.get("surprise", True),
        on_toggle_surprise=lambda: calls.__setitem__("tsurp", calls.get("tsurp", 0) + 1),
    )
    items = {i.label: i for i in build_menu(**kwargs) if not i.separator}
    assert "Randomize" in items and "Surprise me" in items
    items["Randomize"].action()
    assert calls["rand"] == 1
    assert items["Surprise me"].checkbox() is True
    items["Surprise me"].action()
    assert calls["tsurp"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_menu.py -k "randomize or surprise" -v`
Expected: FAIL — `TypeError: build_menu() got an unexpected keyword argument 'on_randomize'`.

- [ ] **Step 3: Implement in `menu.py`**

Add three keyword params to `build_menu` (after `on_toggle_tray`):

```python
    on_randomize: Optional[Callable[[], None]] = None,
    surprise_me: Optional[Callable[[], bool]] = None,
    on_toggle_surprise: Optional[Callable[[], None]] = None,
```

Build the head without Randomize, then append it conditionally. Replace the `items` construction + tray block with:

```python
    items: List[MenuItem] = [
        MenuItem(label="Colorway", submenu=colorway_items),
        MenuItem(label="Pattern", submenu=pattern_items),
    ]
    if on_randomize is not None:
        items.append(MenuItem(label="Randomize", action=on_randomize))
    items += [
        MenuItem(label="Customize…", action=on_customize),
        MenuItem(label="Rename…", action=on_rename),
        MenuItem(separator=True),
        MenuItem(label="Refresh now", action=on_refresh),
        MenuItem(label="Always in front", action=on_toggle_always_on_top, checkbox=always_on_top),
    ]
    if on_toggle_tray is not None and tray_enabled is not None:
        items.append(MenuItem(label="Show tray icon", action=on_toggle_tray, checkbox=tray_enabled))
    if on_toggle_surprise is not None and surprise_me is not None:
        items.append(MenuItem(label="Surprise me", action=on_toggle_surprise, checkbox=surprise_me))
    items.append(MenuItem(separator=True))
    items.append(MenuItem(label="Exit", action=on_quit))
    return items
```

- [ ] **Step 4: Wire the seams in `ui.py`**

In `TokittyWindow.__init__`, alongside the existing `on_toggle_tray`/`tray_enabled` attributes, add:

```python
        self.on_randomize: Optional[Callable[[int], None]] = None
        self.surprise_me: Optional[Callable[[], bool]] = None
        self.on_toggle_surprise: Optional[Callable[[], None]] = None
```

In `build_menu_model`, add to the `build_menu(...)` call (after `on_toggle_tray=self.on_toggle_tray,`):

```python
            on_randomize=((lambda i=pane_index: self.on_randomize(i)) if self.on_randomize is not None else None),
            surprise_me=self.surprise_me,
            on_toggle_surprise=self.on_toggle_surprise,
        )
```

- [ ] **Step 5: Add a headless UI test**

Append to `tests/test_ui_layout.py`:

```python
def test_randomize_and_surprise_seams_add_items():
    tk = pytest.importorskip("tkinter")
    from tokitty.ui import TokittyWindow
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        with tempfile.TemporaryDirectory() as d:
            window = TokittyWindow(root, Path(d), pane_count=1)
            window.on_randomize = lambda i: None
            window.surprise_me = lambda: True
            window.on_toggle_surprise = lambda: None
            labels = [i.label for i in window.build_menu_model(0) if not i.separator]
            assert "Randomize" in labels and "Surprise me" in labels
    finally:
        root.destroy()
```

Mark it `@pytest.mark.gui` if the file's convention is to mark real-`tk.Tk()` tests (check the top of the file; match the surrounding tests).

- [ ] **Step 6: Run tests + lint + commit**

Run: `python3 -m pytest tests/test_menu.py -q && xvfb-run -a python3 -m pytest tests/test_ui_layout.py -m gui -q`
Expected: PASS.

```bash
ruff check tokitty/menu.py tokitty/ui.py tests/test_menu.py tests/test_ui_layout.py
git add tokitty/menu.py tokitty/ui.py tests/test_menu.py tests/test_ui_layout.py
git commit -m "feat(menu): Randomize action and Surprise-me toggle seams"
```

---

### Task 6: `__main__.py` — randomization wiring (seed, surprise, action)

Seed a random look on first run (only-if-unset), roll on every start when Surprise-me is on (writes, per owner decision), wire the Randomize action, and wire the Surprise-me toggle.

**Files:**
- Modify: `tokitty/__main__.py`
- Test: `tests/test_main.py` (append)

**Interfaces:**
- Consumes: `randomize.random_look`, `sprites.COLORWAYS`/`PATTERNS`, `settings.load_settings`/`save_settings`/`Settings`.
- Produces: `initial_customization(account, stored, rng=None)` (seeds a roll when unset + no seed); `run_gui` sets `window.on_randomize`, `window.surprise_me`, `window.on_toggle_surprise`.

- [ ] **Step 1: Update the tests whose behavior random-seeding changes**

Task 6 makes `initial_customization` roll a random look whenever there is no stored customization AND no valid account seed. Three tests migrated in Task 2 still assert the orange+tabby default for that case — they must now assert a random (but valid) roll under an injected rng. In `tests/test_main.py`, **delete** the three default-asserting tests (`test_initial_customization_no_stored_no_seed_defaults_orange_tabby`, `test_initial_customization_invalid_seed_coat_falls_back_to_default`, `test_initial_customization_no_account_no_stored_defaults`) and add:

```python
import random
from tokitty.sprites import COLORWAYS, PATTERNS


def test_initial_customization_no_stored_no_seed_rolls_random():
    account = Account(name="Work", config_dir="/x")
    result = initial_customization(account, None, rng=random.Random(0))
    assert result.colorway in COLORWAYS and result.pattern in PATTERNS


def test_initial_customization_invalid_seed_coat_rolls_random():
    account = Account(name="Work", config_dir="/x", coat="not_a_real_coat")
    result = initial_customization(account, None, rng=random.Random(0))
    assert result.colorway in COLORWAYS and result.pattern in PATTERNS


def test_initial_customization_no_account_no_stored_rolls_random():
    result = initial_customization(None, None, rng=random.Random(0))
    assert result.colorway in COLORWAYS and result.pattern in PATTERNS
```

Keep `test_initial_customization_seeds_from_account_coat` and `test_initial_customization_stored_beats_seed` unchanged — the account-seed and stored-wins paths short-circuit before any roll. (`initial_customization` is already imported in `test_main.py`'s existing top-of-file import block.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py -k rolls_random -v`
Expected: FAIL — `TypeError: initial_customization() got an unexpected keyword argument 'rng'`.

- [ ] **Step 3: Add rng-seeding to `initial_customization`**

Add the import near the top of `__main__.py`:

```python
from tokitty.randomize import random_look
```

Replace `initial_customization` (from Task 2) with:

```python
def initial_customization(account: Optional[Account], stored: Optional[Customization],
                          rng=None) -> Customization:
    """Stored (customization.json) always wins; else seed from the account's
    legacy `coat`; else roll a random curated look so a fresh install/account
    gets a unique cat (only-if-unset -- never overrides an explicit pick)."""
    if stored is not None:
        return stored
    colorway, pattern = _seed_from_account(account)
    if colorway is not None:
        return Customization(colorway=colorway, pattern=pattern)
    colorway, pattern = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS), rng=rng)
    return Customization(colorway=colorway, pattern=pattern)
```

- [ ] **Step 4: Run the seeding tests**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: PASS.

- [ ] **Step 5: Persist the first-run seed, then wire Randomize + Surprise-me in `run_gui`**

**(a) Persist the seed (required — else the random seed re-rolls every launch).** The per-account setup loop applies `initial_customization` but never saves it; only `handle_customization_changed` writes today. Immediately AFTER that loop (the `for index, account in enumerate(accounts or [None]):` block ending in `units.append(...)`), add:

```python
    # Persist first-run seeds (and re-write loaded entries idempotently) so a
    # random seed becomes a STABLE identity instead of re-rolling each launch.
    # Creates customization.json on first run -- intended; it is the per-account
    # look file, never accounts.json, so there is no credential-mode impact.
    save_customization(state_dir, customization_store)
```

**(b) Randomize branch.** In `handle_customization_changed`, add a branch (after the `pattern` branch):

```python
        elif field == "randomize":
            cw, pat = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))
            custom = replace(custom, colorway=cw, pattern=pat)
```

**(c) Seams.** After `window.on_customization_changed = handle_customization_changed`, and after `settings = load_settings(state_dir)` (from the tray block), add:

```python
    from tokitty.settings import Settings, save_settings

    surprise_state = {"on": settings.surprise_me}
    window.surprise_me = lambda: surprise_state["on"]

    def randomize(pane_index: int) -> None:
        handle_customization_changed(pane_index, "randomize", None)

    window.on_randomize = randomize

    def toggle_surprise() -> None:
        surprise_state["on"] = not surprise_state["on"]
        save_settings(state_dir, Settings(tray_enabled=settings.tray_enabled,
                                          surprise_me=surprise_state["on"]))
        if surprise_state["on"]:
            for i in range(len(units)):
                handle_customization_changed(i, "randomize", None)

    window.on_toggle_surprise = toggle_surprise
```

- [ ] **Step 6: Roll on start when Surprise-me is on**

In `run_gui`, immediately after the per-account unit setup loop (after `units.append(...)` completes, before `window.on_refresh_requested = refresh_all`), add:

```python
    if settings.surprise_me:
        for index in range(len(units)):
            handle_customization_changed(index, "randomize", None)
```

Note: `handle_customization_changed` already applies + saves, so a surprise roll writes `customization.json` (per owner decision). It is defined below this point in the file — move this loop to just after `window.on_customization_changed = handle_customization_changed` so the function exists, OR (cleaner) place the loop right after the toggle wiring in Step 5. Put it there.

- [ ] **Step 7: Run the full suite + lint**

Run: `python3 -m pytest -q && ruff check tokitty/__main__.py tests/test_main.py`
Expected: all pass; clean.

- [ ] **Step 8: Commit**

```bash
git add tokitty/__main__.py tests/test_main.py
git commit -m "feat(main): first-run seed, Randomize action, Surprise-me on-start"
```

---

### Task 7: `render_sheet.py` grid mode + `render_media.py` signature

Give the art-review tool a `--colorway`/`--pattern` and a `--grid` mode (the full `COLORWAYS × PATTERNS` matrix = the pruning tool for Tasks 8–9). Migrate `render_media.py` off the legacy `get_palette(coat)`.

**Files:**
- Modify: `scripts/render_sheet.py`
- Modify: `scripts/render_media.py`
- Test: `tests/test_render_sheet.py` (adjust)

**Interfaces:**
- Produces: `render_sheet(out_path, scale=8, colorway="orange", pattern="tabby")`; `render_grid(out_path, scale=6) -> List[str]` (one label per `colorway/pattern` cell, row-major).

- [ ] **Step 1: Adjust the existing render_sheet test**

In `tests/test_render_sheet.py`, replace any `render_sheet(..., coat=...)` call with `colorway=`/`pattern=`, and add:

```python
def test_render_grid_writes_png(tmp_path):
    from scripts.render_sheet import render_grid
    out = tmp_path / "grid.png"
    labels = render_grid(out, scale=2)
    assert out.is_file() and out.stat().st_size > 0
    assert labels  # non-empty legend
```

(Read the file first to match its import style — it may `sys.path.insert` like `render_sheet.py` does.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_render_sheet.py -v`
Expected: FAIL — `TypeError` on `coat=` / `ImportError: cannot import name 'render_grid'`.

- [ ] **Step 3: Update `render_sheet.py`**

Change the import `from tokitty.sprites import ALL_STATES, get_frames, get_palette` to:

```python
from tokitty.sprites import ALL_STATES, COLORWAYS, PATTERNS, get_frames, resolve_palette
```

Change `render_sheet`'s signature and its `palette = get_palette(coat)` line:

```python
def render_sheet(out_path: Path, scale: int = 8, colorway: str = "orange",
                 pattern: str = "tabby") -> List[str]:
    palette = resolve_palette(colorway, pattern)
    ...
```

Add `render_grid` below `render_sheet` (renders the "content" pose for every colorway×pattern into a matrix; reuses the module's `_hex_to_rgb`/`_write_png`):

```python
def render_grid(out_path: Path, scale: int = 6) -> List[str]:
    """One 'content' cat per colorway x pattern, laid out as a matrix.
    Returns the row-major cell legend (\"colorway/pattern\")."""
    from tokitty.sprites import _apply, SITTING_TEMPLATE
    pose = _apply(SITTING_TEMPLATE, {"L": "e", "R": "e", "A": "n"})
    rows_n, cols_n = len(pose), len(pose[0])
    cell_w, cell_h = cols_n * scale, rows_n * scale
    colorways, patterns = list(COLORWAYS), list(PATTERNS)
    width = GAP + len(patterns) * (cell_w + GAP)
    height = GAP + len(colorways) * (cell_h + GAP)
    bg = _hex_to_rgb(BG)
    grid = [[bg] * width for _ in range(height)]
    legend: List[str] = []
    for r, cw in enumerate(colorways):
        for c, pat in enumerate(patterns):
            palette = resolve_palette(cw, pat)
            x0 = GAP + c * (cell_w + GAP)
            y0 = GAP + r * (cell_h + GAP)
            for ry, row in enumerate(pose):
                for cx, ch in enumerate(row):
                    color = palette.get(ch, "")
                    if not color:
                        continue
                    px = _hex_to_rgb(color)
                    for dy in range(scale):
                        for dx in range(scale):
                            grid[y0 + ry * scale + dy][x0 + cx * scale + dx] = px
            legend.append(f"{cw}/{pat}")
    _write_png(out_path, [b"".join(row) for row in grid], width)
    return legend
```

Update `main()`'s argparse: replace `--coat` with `--colorway` (default `"orange"`) and `--pattern` (default `"tabby"`), add `--grid` (`action="store_true"`); when `--grid`, call `render_grid(args.out, scale=args.scale)` and print its legend; else `render_sheet(args.out, scale=args.scale, colorway=args.colorway, pattern=args.pattern)`.

- [ ] **Step 4: Update `render_media.py`**

In `scripts/render_media.py`, replace `get_palette(...)` usage. Change its import of `get_palette` to `resolve_palette`, and each `get_palette(coat_name)` call to `resolve_palette(colorway, pattern)` (the README default cat = `resolve_palette("orange", "tabby")`; if it currently loops coats for a multi-coat strip, map each legacy coat via `LEGACY_COAT_MAP` — import it — to `resolve_palette(*LEGACY_COAT_MAP[name])`). Read the file first to see which form it uses.

- [ ] **Step 5: Run tests + regenerate media + verify no drift**

Run: `python3 -m pytest tests/test_render_sheet.py -q`
Expected: PASS.
Run: `python3 scripts/render_media.py --out docs/media && git status --short docs/media`
Expected: no modified files under `docs/media/` (the default cat is unchanged from Task 2). If anything shows modified, a colorway/pattern default drifted — fix.

- [ ] **Step 6: Lint + commit**

```bash
ruff check scripts/render_sheet.py scripts/render_media.py tests/test_render_sheet.py
git add scripts/render_sheet.py scripts/render_media.py tests/test_render_sheet.py
git commit -m "feat(scripts): render_sheet colorway/pattern + grid mode; render_media resolve_palette"
```

---

### Task 8: ART — new template regions (paws/points/tail/belly) + tail go/no-go

**Owner-only (Nick).** Add the four new region chars to the three base templates, expand `REGION_CHARS`, and give **every** existing `PATTERNS` entry the new keys (the coverage invariant goes red the instant a template uses a char no pattern defines). Includes the tail-readability spike whose result gates Task 9's van/ringed patterns.

**Files:**
- Modify: `tokitty/sprites.py` (templates + `REGION_CHARS` + `PATTERNS` keys)
- Test: `tests/test_sprites.py` (append)

**Interfaces:**
- Produces: `REGION_CHARS = ("o","O","s","c","m","x","y","u")` (paws `m`, points `x`, tail `y`, belly `u`); every `PATTERNS` value defines all eight; templates render those chars.

- [ ] **Step 1: Write the failing coverage test**

Append to `tests/test_sprites.py`:

```python
def test_new_regions_present_and_covered():
    from tokitty.sprites import REGION_CHARS, PATTERNS, SITTING_TEMPLATE
    for ch in ("m", "x", "y", "u"):
        assert ch in REGION_CHARS
    # every pattern defines every region (closed set)
    for name, pat in PATTERNS.items():
        assert set(pat.keys()) == set(REGION_CHARS), name
    # the new chars actually appear in the base template art
    joined = "".join(SITTING_TEMPLATE)
    for ch in ("m", "x", "y", "u"):
        assert ch in joined, ch
```

Run: `python3 -m pytest tests/test_sprites.py -k new_regions -v` → FAIL (chars not in REGION_CHARS/templates).

- [ ] **Step 2: OWNER ART — re-char the templates**

Nick edits `SITTING_TEMPLATE`, `ALERT_TEMPLATE`, `FLOPPED_TEMPLATE` in `sprites.py`, converting cells to the new region chars: **paws `m`** (the lower-leg/foot cells currently `w`), **belly `u`** (the belly/underside cells currently `w`, leaving the muzzle as `w`), **points `x`** (outer-ear + face-mask cells currently `o`), **tail `y`** (the tail-curl cells currently `O`). Derived poses inherit via `_overlay`; verify no `_overlay` prop cell needs a region char it now finds re-charred (props stamp *over*, so they win — confirm by rendering the working/permission/done_hop states). This is gated on the PNG review in Step 5.

- [ ] **Step 3: Expand `REGION_CHARS` and every `PATTERNS` entry**

Set:

```python
REGION_CHARS: Tuple[str, ...] = ("o", "O", "s", "c", "m", "x", "y", "u")
```

Give every existing pattern the four new keys (sensible defaults; final look tuned on the sheet):

```python
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

- [ ] **Step 4: Run sprite tests**

Run: `python3 -m pytest tests/test_sprites.py -q`
Expected: PASS — `test_new_regions_present_and_covered` + `test_every_state_char_defined_for_every_look` (now covers m/x/y/u across every look) green.

- [ ] **Step 5: OWNER GATE — PNG review + tail spike**

Render for Nick:
- `python3 scripts/render_sheet.py --out /tmp/solid.png --pattern solid` (the cat must still read as a cat with all regions coat-colored).
- A per-region debug render (temporarily map m/x/y/u to distinct bright colors) to confirm cell placement.
- **Tail spike:** a `ringed` proof — mark alternating tail cells and render — to judge whether the ~10px curl bands cleanly.

**HARD GATE + go/no-go:** Nick approves the re-charred templates. Record the tail decision explicitly: **tail-ringed = GO** (Task 9 ships van + ringed) or **NO-GO** (Task 9 drops van + ringed; tail stays a plain solid-color region). Do not start Task 9 until this is recorded.

- [ ] **Step 6: Commit**

```bash
git add tokitty/sprites.py tests/test_sprites.py
git commit -m "feat(sprites): paws/points/tail/belly regions in base templates"
```

---

### Task 9: ART — new colorways + silhouette patterns + grid prune

**Owner-only (Nick).** Add colorways (cream, brown) and the silhouette patterns using the new regions; render the full colorway×pattern grid and prune muddy combos. Van + ringed ship **only if Task 8's tail spike was GO**.

**Files:**
- Modify: `tokitty/sprites.py` (`COLORWAYS`, `PATTERNS`)
- Test: `tests/test_sprites.py` (append)

**Interfaces:**
- Produces: added `COLORWAYS` (`cream`, `brown`, …) each with all five tone slots; added `PATTERNS` (`tuxedo`, `socks`, `colorpoint`, and — if GO — `van`, `ringed`) each covering all `REGION_CHARS`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sprites.py`:

```python
def test_silhouette_patterns_present_and_covered():
    from tokitty.sprites import PATTERNS, COLORWAYS, REGION_CHARS
    for name in ("tuxedo", "socks", "colorpoint"):
        assert name in PATTERNS
        assert set(PATTERNS[name].keys()) == set(REGION_CHARS), name
    assert "cream" in COLORWAYS
    # colorpoint pales the body and darkens the extremities
    cp = PATTERNS["colorpoint"]
    assert cp["o"] == "light" and cp["x"] == "mark" and cp["m"] == "mark"
```

Run: `python3 -m pytest tests/test_sprites.py -k silhouette -v` → FAIL.

- [ ] **Step 2: OWNER ART — add colorways + patterns**

Nick adds to `COLORWAYS` (owner-picked hexes; all five slots each):

```python
    "cream": {"coat": "#e9d9bd", "shade": "#cdb894", "mark": "#a98d63", "light": "#f6ecda", "ear": "#f0c4cf"},
    "brown": {"coat": "#8a5a3c", "shade": "#6d452d", "mark": "#4f3020", "light": "#cbb39a", "ear": "#d69aa6"},
```

and to `PATTERNS` (owner tunes the source tokens on the sheet):

```python
    "tuxedo":     {"o": "coat", "O": "shade", "s": "coat", "c": "white",
                   "m": "white", "x": "coat", "y": "shade", "u": "white"},
    "socks":      {"o": "coat", "O": "shade", "s": "coat", "c": "coat",
                   "m": "white", "x": "coat", "y": "shade", "u": "coat"},
    "colorpoint": {"o": "light", "O": "light", "s": "light", "c": "light",
                   "m": "mark", "x": "mark", "y": "mark", "u": "light"},
    # van + ringed ONLY if Task 8 tail spike == GO:
    # "van":    {"o": "white", "O": "shade", "s": "white", "c": "white",
    #            "m": "white", "x": "coat", "y": "coat", "u": "white"},
    # "ringed": <needs a tail-band char decided in the Task 8 spike>,
```

If the tail spike was NO-GO, omit `van`/`ringed` and adjust the Step-1 test to not require them.

- [ ] **Step 3: Run sprite tests**

Run: `python3 -m pytest tests/test_sprites.py -q`
Expected: PASS (coverage invariant holds for every new look).

- [ ] **Step 4: OWNER GATE — grid render + prune**

Run: `python3 scripts/render_sheet.py --grid --out /tmp/grid.png` and send to Nick. He eyeballs the full colorway×pattern matrix and prunes: drop any whole pattern/colorway that never reads (dark×dark colorpoint corners are expected to be muddy — that's acceptable as a user choice, but a pattern that reads badly *everywhere* should be cut). **HARD GATE:** Nick approves the final set before wiring is considered done.

- [ ] **Step 5: Full suite + lint + commit**

Run: `python3 -m pytest -q && ruff check tokitty/sprites.py tests/test_sprites.py`
Expected: all pass; clean.

```bash
git add tokitty/sprites.py tests/test_sprites.py
git commit -m "feat(sprites): cream/brown colorways + tuxedo/socks/colorpoint(+van/ringed) patterns"
```

---

### Task 10: README + push, PR, and the manual Windows gate

**Files:**
- Modify: `README.md`
- Integration/CI + manual gate.

- [ ] **Step 1: README**

Update the customization section: right-click → **Colorway ▸ / Pattern ▸ / Randomize / Customize… / Rename…**; the colorway × pattern model (looks = colorways × patterns); the pattern list; **Surprise me** every-launch toggle; `customization.json` now stores `colorway`+`pattern` (legacy `coat` files migrate automatically). Keep the security section truthful (no new persisted files beyond the existing `customization.json`/`settings.json`).

- [ ] **Step 2: Full suite green + lint**

Run: `python3 -m pytest -q && xvfb-run -a python3 -m pytest -m gui -q && ruff check .`
Expected: all green; ruff clean (`ruff==0.15.22`).

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin coat-patterns
gh pr create --repo nickwolf/tokitty --title "Coat patterns (#32, #38)" --body "Closes #32
Closes #38"
```

- [ ] **Step 4: Watch CI**

Run: `gh pr checks --repo nickwolf/tokitty --watch`
Expected: all 8 green (test ×6, smoke, lint). No new deps → the `pip install -e ".[dev]"` resolve step is unchanged. Triage any red before proceeding; do not merge red.

- [ ] **Step 5: Manual Windows gate with Nick**

Hand off to Nick on the real desktop (this feature has visual + native surfaces, so CI-green alone does not merge):

1. `pythonw.exe -m tokitty` from an elevated PowerShell; confirm `SessionId` matches `explorer.exe` (`Get-Process python*,explorer | Select ProcessName,SessionId`).
2. Colorway ▸ and Pattern ▸ both switch and **persist across restart**; the tray icon reflects pane-0's colorway+pattern.
3. **Randomize** re-rolls and persists; **Surprise me** on → a different look each launch, off → the last roll is kept.
4. Delete `customization.json` → first run seeds a fresh random cat → **restart → the same cat** (proves the seed persisted, not a per-launch re-roll). Then restore a **legacy** `{"default":{"coat":"calico"}}` file → loads as white+calico unchanged.
5. Dual-account card: each pane rolls/customizes independently.

Wait for Nick's confirmation (and any iterative art feedback on the new regions/patterns) before merging.

- [ ] **Step 6: Rebase-merge + record**

After CI-green AND Nick's manual pass:

```bash
gh pr merge --repo nickwolf/tokitty --rebase --delete-branch
```

Confirm #32 + #38 auto-closed. Update the `tokitty-v2` project memory: coat patterns done, note the final pattern/colorway set and the tail go/no-go outcome.

---

## Self-Review

**Spec coverage** (each spec section → task):
- Colorway × pattern token-resolved model → Task 1 (`COLORWAYS`/`PATTERNS`/`resolve_palette`). ✓
- New regions (paws/points/tail/belly), muzzle stays white → Task 8. ✓
- Honest partial-orthogonality + grid-prune tool → Task 7 (`render_grid`), Task 9 Step 4. ✓
- ~10 patterns (colorway-friendly + specials) → Tasks 1 (5 pure-data) + 9 (tuxedo/socks/colorpoint/van/ringed). ✓
- Randomization: seed-on-first-run (only-if-unset), Randomize action, Surprise-me every-launch (writes) → Tasks 3, 5, 6. ✓
- Schema + migration (legacy `coat`, accounts seed) → Task 2 (`_resolve_colorway_pattern`, `_seed_from_account`). ✓
- Two-menu UX, live-apply → Task 2 (menu.py Colorway/Pattern). ✓
- `settings.surprise_me` → Task 4. ✓
- Byte-identical refactor first (render-diff + literal-snapshot migration tests) → Task 1 (literal snapshot) + Task 2 (render-diff gate, Steps 1/13). ✓
- Tray ripple, two off-thread getters read shadows → Task 2 (Step 6/7), Global Constraints. ✓
- Tail gated fallback with explicit go/no-go → Task 8 Step 5. ✓
- render_sheet/render_media migration → Task 7. ✓
- Manual Windows gate + memory update → Task 10. ✓

**Placeholder scan:** No "TBD/TODO/implement later". Owner-art steps (Tasks 8–9) show the exact mechanism (chars, `REGION_CHARS`, every `PATTERNS` key) and the illustrative hexes; the actual pixel placement and final hexes are explicitly gated owner work, not vague requirements. ✓

**Type/name consistency across the flip:**
- `resolve_palette(colorway, pattern)` — same signature in Task 1 (def), Task 2 (customize `effective_palette`, tray image factory, `PALETTE`), Task 7 (render scripts). ✓
- `Customization(colorway, pattern, overrides, label)` — Task 2 def matches Task 2/6 `initial_customization` / `handle_customization_changed` `replace(...)` and Task 9 tests. ✓
- `build_menu(*, colorways, patterns, current_colorway, current_pattern, on_colorway, on_pattern, …, on_randomize, surprise_me, on_toggle_surprise)` — Task 2 (base) + Task 5 (three seams) match `ui.build_menu_model`'s call and `tests/test_menu.py`. ✓
- `TrayManager(..., colorway="orange", pattern="tabby")` + `image_factory(colorway, pattern)` — Task 2 def matches `__main__` construction and `tests/test_tray.py` fakes. ✓
- `field` set `{"colorway","pattern","coat_base","coat_shade","card_bg","bar_fill","label","reset","randomize"}` — produced by ui selectors (Task 2/5) + `handle_customization_changed` (Task 2/6). ✓
- `REGION_CHARS` grows `("o","O","s","c")` (T1) → `+("m","x","y","u")` (T8); every `PATTERNS` entry updated in the same task it grows (T8 Step 3, T9 Step 2) — coverage invariant never left red. ✓
