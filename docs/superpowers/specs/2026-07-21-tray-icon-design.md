# Tray icon — design

**Issue:** #21 — "Backlog: tray icon". Body: "pystray (first pip dependency);
second event loop thread; native path only."

**Date:** 2026-07-21
**Branch:** `tray-icon`

## Goal

Give the always-on-top, title-bar-less, taskbar-button-less widget a system-tray
icon so the menu (Exit, Refresh, coat/customize, always-in-front) is reachable
without discovering the right-click gesture. Attempt it on all three platforms;
disable cleanly wherever the tray backend is unavailable. This adds Tokitty's
**first runtime dependencies** (`pystray`, `Pillow`) — the whole design is built
around containing that blast radius so the CI matrix stays green.

## Decided (owner, not to be re-opened)

1. Tray icon **on by default**, with a user toggle to disable; the choice is
   **persisted** in the state dir so it survives restarts.
2. Attempt the tray on **Windows, macOS, and Linux**. Where the backend is
   unavailable/headless/unsupported, **disable it cleanly** (no crash) — the app
   runs normally without it.
3. Clicking the tray icon surfaces the **same menu content** as the window's
   right-click context menu, from **one source of truth** rendered to both the Tk
   context menu and the pystray menu.

## Owner choices (brainstorm, 2026-07-21)

| Question | Choice |
|---|---|
| Icon art | **content (sitting)** pose — static single pose, most recognizable as a cat at small size. Rendered from the same sprite art as the widget. |
| Dual-account tray menu | **Pane 0 only.** The tray renders the full menu for the top/personal pane; per-pane customize stays reachable via right-click on the (always-visible) window. |
| Click gesture | **Native gesture** — right-click on Windows/Linux, left-click on the macOS status bar. Decided #3 is about menu *content*, not the click; no per-backend menu-popping hacks. |

## Context / grounding (verified 2026-07-21)

- **Zero runtime deps today.** `pyproject` `[project]` has no `dependencies`
  table; only a `dev` extra (`pytest>=7`, `ruff==0.15.22`). This issue adds the
  first two.
- **tkinter import surface is tightly contained** and must stay that way:
  `tokitty/ui.py` is the only module importing tkinter at module scope;
  `__main__.py` imports `ui`/`tkinter` lazily inside `run_gui()`. The CI `test`
  matrix runs headless (`addopts = -m "not gui"`); the `smoke` job boots the real
  `TokittyWindow` under xvfb; `lint` is pinned `ruff==0.15.22 check`.
- **The menu lives in `ui.py` today** as `_rebuild_context_menu()` — a flat
  `tk.Menu`: Coat ▸ (radiobuttons over `COATS`, bound to `self._coat_var`),
  Customize… (colorchooser dialog), Rename… (simpledialog), separator, Refresh
  now, "Always in front" (checkbutton bound to `self._always_on_top`
  `BooleanVar`), separator, Exit (`self.root.destroy`). It is **pane-specific**:
  `_show_context_menu` sets `self._menu_pane_index` from the click's y before
  rebuilding.
- **Window↔__main__ seam already exists.** `TokittyWindow` exposes
  `on_refresh_requested` and `on_customization_changed` callback attributes that
  `run_gui()` wires. This design adds more seams in the same style.
- **State dir** (`paths.get_state_dir()`) already holds `position.json`,
  `accounts.json`, `customization.json`. `customization.json` is **per-account
  keyed** — the wrong home for an app-global toggle. A new app-level file is
  correct.
- **Sprite rasterization exists as a script, not a package module.**
  `scripts/render_media.py` has `_raster(frame, palette, scale, bg)` producing a
  pixel grid, reused from `scripts/render_sheet.py`. The shipped package cannot
  import from `scripts/`.

## The landmine and how it's contained

pystray's Linux backend pulls in AppIndicator/Gtk system libs the CI runners may
lack, and `import pystray` at a module scope the suite imports would turn the
matrix red. Containment:

- **No module-scope `import pystray` / `from PIL import Image` anywhere the test
  suite imports.** All pystray/PIL access is behind injected factories inside
  `tokitty/tray.py`, imported lazily.
- **Guard instantiation, not just import.** `import pystray` can succeed while
  `pystray.Icon(...)` / backend selection raises on a headless or Gtk-less box
  (and not always as `ImportError`). Both the import and the icon construction
  are wrapped, caught broadly → `available=False`.
- **Tray runs only on the real GUI path** — never `--debug-print` or the
  `TOKITTY_DEBUG_STATE`/`TOKITTY_DEBUG_ACCOUNTS` early-return branch.

## The cross-thread read hazard (design-level, must be built out)

Every tray→UI **action** is marshaled to the main thread. But pystray also
**evaluates the menu's `checked=` / radio getters on its own thread** when it
draws the menu — a read path. If those getters read tk state
(`self._always_on_top.get()`, a pane's `_coat`), that is a cross-thread Tcl
access: a latent flake that a mocked test and even one manual run can miss.

**Resolution:** anything pystray evaluates on its thread — the `checkbox` and
`radio_selected` getters — must read **plain-Python shadow state**, never tk
Vars or widgets. `ui.py` maintains shadows (`self._always_on_top_bool`, a
per-pane current-coat value) updated on the main thread wherever the
corresponding tk state changes. The menu model's getters read only the shadows.

## Architecture

### `tokitty/menu.py` (new) — the single source of truth

Pure Python, no tkinter, no pystray. A `MenuItem` dataclass:

```
label: str = ""
action: Callable[[], None] | None = None    # command / checkbox / radio click
submenu: list[MenuItem] | None = None        # cascade
separator: bool = False
checkbox: Callable[[], bool] | None = None       # -> checkbutton; getter = state
radio_selected: Callable[[], bool] | None = None # -> radio item; getter = selected?
```

`build_menu(*, coats, current_coat, on_coat, on_customize, on_rename,
on_refresh, always_on_top, on_toggle_always_on_top, tray_enabled,
on_toggle_tray, on_quit)` returns the item tree:

Coat ▸ (radio per coat; each `radio_selected = lambda c=name: current_coat() ==
c`) · Customize… · Rename… · —— · Refresh now · Always in front (checkbox =
`always_on_top`) · Show tray icon (checkbox = `tray_enabled`) · —— · Exit.

The `Show tray icon` item is included **only when `on_toggle_tray`/`tray_enabled`
are provided** — omitted when the tray backend is unavailable. All getters passed
in are shadow-state readers (see hazard section). Fully unit-testable headless.

### `tokitty/sprite_raster.py` (new) — one rasterizer

Lift the frame→pixel-grid logic out of `scripts/render_media.py` into the
package. `render_media.py` imports it (behavior unchanged); `tray.py` builds its
PIL Image from the same grid. Brand consistency = literally the same pixels; no
drift when the art changes. Nothing new imports from `scripts/`.

### `tokitty/settings.py` (new) — app-level persisted toggle

`settings.json` in the state dir, robust-load like `customize.py` (missing /
unparseable / wrong-shape → defaults, never crash). A frozen `Settings`
dataclass with `tray_enabled: bool = True`. `load_settings(state_dir)` /
`save_settings(state_dir, settings)` (atomic tmp+`os.replace`, mirroring
`save_customization`).

### `tokitty/tray.py` (new) — `TrayManager`

Owns the pystray icon lifecycle and daemon thread. Constructor injects
`icon_factory` and `image_factory`, each defaulting to a real lazy-import
implementation, so tests pass mocks/raising fakes with no `sys.modules` patching.

- `available: bool` — set by probing the factories (import + a trial icon build)
  under broad `except`. `False` → all methods no-op.
- `start()` — if available and enabled and not already running: build the PIL
  Image (content pose, pane-0 coat) via `image_factory`, render `menu.py`'s model
  to a `pystray.Menu` **wrapping every action in `root.after(0, action)`** so all
  tray→UI calls marshal to the main thread, then run the icon on a daemon thread.
- `stop()` — `icon.stop()` if running; None-guarded so stopping before any start
  is a no-op. Idempotent.
- `set_enabled(bool)` — persist via `settings.py` and `start()`/`stop()`
  accordingly; re-enabling builds a fresh Icon on a new thread.

Menu model built for **pane 0** (owner choice). The action wrapper marshaling is
what makes the Customize…/Rename… dialogs safe to invoke from the tray.

### `tokitty/ui.py` (changed)

- `_rebuild_context_menu()` becomes a thin renderer of `menu.py`'s model into a
  `tk.Menu` (radio → `add_radiobutton` with a per-cascade `StringVar` set from
  the `radio_selected` getters; checkbox → `add_checkbutton` with a `BooleanVar`
  seeded from the getter; separator/command/cascade as today). Called directly on
  the main thread, so it invokes actions without marshaling.
- Add shadow state: `self._always_on_top_bool` and a per-pane current-coat value,
  updated on the main thread wherever the tk state changes (toggle handler, coat
  selection). The model getters read only these.
- Expose new callback seams, defaulted so today's behavior is unchanged until
  `run_gui` wires them: `on_quit` (default `self.root.destroy`), `on_toggle_tray`
  (default `None`), `tray_enabled` (default `None`). Exit's action becomes
  `on_quit`. The "Show tray icon" item appears only when `on_toggle_tray` is set.

### `tokitty/__main__.py` `run_gui()` (changed)

Real-GUI branch only (after the debug early-returns):

1. `settings = load_settings(state_dir)`.
2. Construct `TrayManager(root, window, state_dir)` — builds the pane-0 menu
   model from the window's seams.
3. `window.on_quit = lambda: (tray.stop(), root.destroy())`.
4. `window.tray_enabled = lambda: settings.tray_enabled` and
   `window.on_toggle_tray = <persist + tray.set_enabled>` — **only if
   `tray.available`**; otherwise leave them None so the item is omitted.
5. `tray.start()` if `tray.available and settings.tray_enabled`.
6. Add `tray.stop()` to the existing `finally` (alongside poller/watcher stop and
   `lock.release()`).

Tray is constructed here, **not** in `TokittyWindow.__init__`, so the xvfb `gui`
smoke test (which builds a bare `TokittyWindow`) never touches pystray.

## Menu content (both surfaces, one source)

Coat ▸ (radio) · Customize… · Rename… · —— · Refresh now · ☑ Always in front ·
☑ Show tray icon · —— · Exit.

Right-click renders it for the clicked pane; the tray renders it for pane 0.
"Show tray icon" is present on both surfaces so the tray can be re-enabled from
the right-click menu after the icon is gone.

## Threading & shutdown

Tk `mainloop()` on the main thread; `icon.run()` on a daemon thread. Exit (either
surface) → `on_quit` → `icon.stop()` then `root.destroy()`; `mainloop` unwinds
into the existing `finally` (pollers/watchers stop, lock released) with no
orphaned thread. `stop()`/`start()` are None-guarded and idempotent.

## Dependencies & CI

- `pyproject.toml` `[project]` gains `dependencies = ["pystray", "Pillow"]`
  (Tokitty's first runtime deps). No version pins beyond what's needed — plain
  names, floor only if a resolve problem surfaces in CI.
- **Distinct gate:** confirm `pip install -e ".[dev]"` **resolves** on
  ubuntu/macos/windows in CI — separate from "tests pass." pystray's platform
  markers should pull the right backend per OS; the resolve step proves it.
- No module-scope `import pystray` on any suite-imported path (grep to confirm).

## Testing (headless, pystray mocked via injected factories)

- **`menu.py` model** — build with fake callables/getters; assert item order,
  labels, cascade/radio/checkbox/separator kinds, that clicking an item calls the
  right callable, that radio `radio_selected` reflects `current_coat`, and that
  "Show tray icon" is present iff the toggle seam is provided.
- **`settings.py`** — default `tray_enabled=True`; round-trip save/load; missing /
  unparseable / wrong-shape file → defaults.
- **`sprite_raster.py`** — grid dimensions and a spot-checked pixel from a known
  frame/palette (guards against drift when lifting from the script).
- **`TrayManager`** (mock icon/image factories) — `start()` builds an icon and
  spawns a thread; `stop()` calls `icon.stop()` and is a no-op before start;
  `set_enabled(False/True)` persists and stops/starts; every rendered pystray
  action is wrapped so invoking it routes through `root.after`.
- **Native-path guard** — inject a factory that raises on import *and* one that
  raises on icon construction → `available=False`, `start()`/`set_enabled()` are
  no-ops, no exception.
- The real `icon.run()` is **not** CI-tested.
- `ui.py` context-menu render stays covered by the existing gui/layout tests;
  add a headless assertion that the model getters read shadow state (no tk access
  off the main thread by construction — the getters are plain callables).

## Out of scope (noted, not built)

- Dynamic mood-reflecting tray icon (future backlog).
- Show/Hide-window tray items (keeps to decided "same menu content").
- Forcing left-click-opens-menu on Windows (non-portable).
- Autostart (#20) and other backlog items.

## Verification gates (before claiming done)

- Full suite green headless: `pytest` (gui deselected).
- `xvfb-run -a pytest -m gui` still green (tray never constructed on this path).
- `ruff check .` clean with pinned `ruff==0.15.22`.
- CI: all 8 checks green across all three OSes, **including** that
  `pip install -e ".[dev]"` resolves on each.
- `grep -rn "import pystray\|from PIL" tokitty/` shows imports only inside
  `tray.py` functions (and `sprite_raster`/`render_media` for PIL on the media
  path), never module scope on a suite-imported path.
- **Manual Windows gate with Nick** (visual + native surface): render the icon,
  `pythonw.exe -m tokitty` (check SessionId vs `explorer.exe`), confirm the tray
  icon appears, the menu shows and its items act, the toggle disables/re-enables,
  and Exit quits cleanly with no orphaned process.
