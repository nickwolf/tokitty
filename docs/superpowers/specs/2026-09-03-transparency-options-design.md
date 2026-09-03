# Transparency options for the cat and bars (design)

Issue: [#37](https://github.com/nickwolf/tokitty/issues/37) ("Let's add a transparency slider for the background behind the cat and play with different UI options for various levels of opacity").

Status: option C chosen by the owner on 2026-09-03, along with the four sub-decisions recorded below. An adversarial review the same day found a blocker that the original spikes could not see, plus nine supporting defects. The blocker now has a verified fix. See the review section at the end before planning.

Related: [#28](https://github.com/nickwolf/tokitty/issues/28) ("PySide6 transparent-cat rewrite"). This design says explicitly which parts of #37 need that rewrite and which do not.

## Goal

The card background becomes see-through at a level the user picks, while the cat, the bars and the text stay readable on top of it.

That is not the same thing as fading the whole widget, and the difference is the whole issue. Fading the whole widget is one line of code and is option A below. Everything else in this document exists because option A does not deliver what the issue asks for.

## Decided (owner, not to be re-opened)

1. Dependencies stay at `pystray` and `Pillow` (`pyproject.toml:8`). PySide6, PyQt and any compositing toolkit are #28's territory, not this issue's.
2. Windows 11 is the platform that has to look right. Linux and macOS need to not break, and are allowed to deliver less.
3. Whatever ships is labelled honestly in the menu. A control called "Transparency" that visibly fades the cat is a bug report waiting to happen.

## Scope and non-goals

In scope: the opacity control itself, where the value is persisted, how it reaches both menus, and the per-platform truth about what the user will actually see.

Not in scope: per-pane opacity (see "Why this cannot be per-pane"), blur or acrylic backdrops, rounded corners, drop shadows, and any change to the sprite palettes.

## What Tk can do, measured on this machine

Windows Python 3.13.2 at `/mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe`:

```
-alpha              OK   -> 0.6
-transparentcolor   OK   -> <color object: '#ff00ff'>
-transparent        FAIL -> unknown color name "1"
```

Linux, Tk 8.6.14 under Xvfb:

```
-alpha              OK   -> 1.0      (set to 0.6, read back 1.0)
-transparentcolor   FAIL -> bad attribute: must be -alpha, -topmost, -zoomed, -fullscreen, or -type
-transparent        FAIL -> same
```

macOS is untested, no Mac is available. Tk documents `-alpha` and a macOS-only `-transparent`; `-transparentcolor` is a Windows-only attribute. Treat the macOS row as documentation, not measurement.

Three consequences drive everything below. `-alpha` is whole-window only, so it fades the cat and the text along with the background. `-alpha` silently does nothing on Linux without a compositing window manager, which is why the probe set 0.6 and read back 1.0; that is an expected outcome to tolerate, not an error to report. Per-region transparency exists only on Windows, via the `-transparentcolor` colour key, and a colour key is binary: a keyed pixel is fully gone, there is no 60% version of it.

## The click-through trap, confirmed

`-transparentcolor` makes keyed pixels click-through, and this was measured rather than assumed. The probe builds the window, sets the key, then calls `WindowFromPoint` at a pixel painted in the key colour and at a pixel painted opaque, resolving each result with `GetAncestor(GA_ROOT)` and comparing against the window's own root HWND.

Control run first, same window with no key set, to prove the instrument reports "ours" when it should:

```
control(no colorkey):        opaque_pixel=OURS  keyed_pixel=OURS
colorkey:                    opaque_pixel=OURS  keyed_pixel=OTHER(16649260)
colorkey(label text area):   opaque_pixel=OURS  keyed_pixel=OTHER(16649260)
alpha0.6+colorkey:           opaque_pixel=OURS  keyed_pixel=OTHER(16649260)
```

So the trap is real. Drag is bound on root `<Button-1>` and the context menu on `bind_all("<Button-3>")` (`ui.py:337-339`, `ui.py:354`), and both rely on the card background receiving the click. Key the background out in a single window and the widget can only be dragged by grabbing a cat pixel or a glyph.

The third line matters too: a `tk.Label` whose `bg` is the key colour has its background keyed out along with everything else, so the gaps between glyphs are click-through, not just the empty card. Setting `-alpha` at the same time as the key does not restore hit testing; alpha and the colour key are independent flags on the same layered window.

## The text rendering problem, measured

Colour-keying is free for the cat and the bars and expensive for the text, and the split is caused by antialiasing.

The cat is drawn as hard-edged `create_rectangle` calls (`ui.py:257`) and the bar fills and track are rectangles too (`ui.py:151`, `ui.py:158`, `ui.py:224-233`). Hard edges have no blend pixels, so keying the canvas background out leaves the sprite pixel-identical.

Text does not survive it. Tk renders `tk.Label` text with ClearType subpixel antialiasing computed against the widget's `bg`, and when that `bg` is then punched out by the colour key, every glyph edge keeps a blend of a background that is no longer there. A 300x60 grab of a window keyed on `#ff00ff` contained 0 pixels of exact `#ff00ff` (so the key itself was genuinely transparent) and 119 pixels within tolerance of magenta, all of them glyph edges. Sample from one text row: `(252, 135, 242)`, `(240, 240, 240)`, `(242, 135, 252)`. That is a white glyph with a bright magenta halo.

A near-black key (`#010203`) is far less lurid but still visibly wrong. Rendering the identical two labels side by side, one on a keyed window over a translucent card and one on an opaque window painted the colour that card composites to, the keyed copy has heavy coloured fringing and the 8pt dim line is close to illegible, while the control is clean.

Changing the key to `#7a777d`, the colour the card actually composites to over that backdrop, makes the two copies indistinguishable. So the fringing is entirely a wrong-background-at-antialias-time artifact, not something inherent to colour keying. That is not a usable fix, because the composited colour depends on the desktop wallpaper behind the widget and changes the moment the user drags it.

The usable conclusion: put rectangles on the keyed surface and keep antialiased text off it.

## Option A: whole-window opacity

Set `-alpha` on the existing root window from a menu control.

Delivers a working opacity control everywhere Tk supports it, in a handful of lines, with no structural change. What it cannot do is the thing #37 asks for: at 60% the cat, the bars and the text are all at 60% too, over whatever is behind them. It also does nothing at all on a Linux desktop with no compositor, and the setting will persist and read back while the screen never changes.

## Option B: single window, colour key

Key the card background colour out of the existing window on Windows.

The cat and bars float crisply on nothing, which looks good, and it is a small diff. It cannot offer levels, because a colour key has exactly two states. It breaks drag and right-click over the whole background, per the probe above, so it needs either a deliberately opaque drag strip across the card (a visible design change) or a modifier-key drag (undiscoverable). It is Windows-only with no fallback, and it puts every label on a keyed background, which is the fringing case measured above.

## Option C: two windows, translucent card under opaque keyed content

A card `Toplevel` carrying the background at `-alpha`, and a content `Toplevel` at the same geometry keyed on an unused colour, holding the parts that must stay crisp. Both `overrideredirect`, both topmost, moved together.

This was built and it works. Screenshot check with the card at alpha 0.55 over a light backdrop: the background is genuinely translucent, the sprite rectangles and the bar are fully opaque and crisp, and the slider value has no effect on their sharpness.

Hit testing lands where it needs to. Over a sprite pixel the click goes to the content window, over keyed empty space it falls through to the card underneath, which is the window that carries drag and the context menu. So the click-through behaviour that breaks option B is what makes option C work.

Stacking held across every check: after creation, after an intruding topmost window appeared, and after both windows were moved, `GetWindow(GW_HWNDNEXT)` walking from the content window still reached the card.

The text has to go on the card window, not the keyed one. That keeps antialiasing correct at every opacity, at the cost of the text fading along with the background. The alternative, text on the keyed window, is the fringed version measured above, and the fringe changes with the wallpaper behind the widget.

Costs, all real:

- `Pane.__init__` takes a single `parent` today (`ui.py:76`). Splitting the cat canvas and the two bar canvases onto one window while the six labels stay on another means `Pane` owns widgets under two parents, and `set_appearance` and `render` currently recolour all of them in one loop (`ui.py:109-121`, `ui.py:207-218`).
- Drag and the context menu must be bound on both windows, and `_save_position` and `_restore_position` must drive both geometries.
- The multi-pane grid stays one card window and one content window covering all panes, not two per pane, so `grid_size` and `pane_index_at` are unchanged. Coordinate translation for `_show_context_menu` has to be relative to whichever window received the event.
- `driving_tag` and `tool_label` are canvas text items drawn on the cat canvas (`ui.py:259-271`), so under this option they are antialiased text on the keyed surface. Resolved by the chip, see below.
- The key colour must be one nothing else can produce. None of the 44 distinct sprite palette colours is near `#010203`, and neither are `BG_COLOR` `#1c1c22`, `BAR_BG` `#333340`, `ACCENT_BG` `#3a1620`, `FG_COLOR` `#f0f0f0` or `DIM_COLOR` `#8a8a92`. But `customize.py` lets a user pick any `#rrggbb` for `coat_base`, `coat_shade`, `card_bg` and `bar_fill` through the colour chooser, so a user could in principle pick the key and punch a hole in their own cat. Nudging a chosen colour by one unit when it equals the key is enough.
- Windows only. Everywhere else this falls back to option A.

## Option D: close #37 into #28

Real per-pixel alpha, a genuinely translucent background behind a crisp cat, on every platform, with correctly antialiased text at any opacity, is what the PySide6 rewrite exists to deliver.

Option C reaches the same visible result on Windows. It does not reach it on Linux or macOS, and it pays for the Windows version with a second window and a split `Pane`.

## Recommendation

Option C on Windows with option A as the automatic fallback elsewhere, and #37 stays open and separate from #28.

The reasoning is that C is not a half-measure on the platform that matters here. On Windows it does exactly what the issue asks: the background takes the slider value, the cat and the bars stay fully opaque and crisp. The concession is the text, which fades with the background rather than staying opaque, and that is a deliberate trade for text that renders correctly at every level rather than text that stays bright and fringed.

Elsewhere the same control drives whole-window `-alpha` and the menu says so, because a control that quietly means two different things is worse than one that admits it.

If the second window and the split `Pane` read as too much structure for a cosmetic feature, option A alone is a defensible ship, relabelled "Window opacity" so it never claims to be what #28 is for. That is the fallback, not the recommendation.

Option B should not ship in any form. It is Windows-only like C, delivers no levels at all, and breaks dragging.

## Why this cannot be per-pane

Both mechanisms are properties of a window. `-alpha` applies to the whole toplevel, and `-transparentcolor` keys a colour across the whole layered window. All panes live in one window (`ui.py:308-312`), so there is no way to give pane 2 a different opacity from pane 1 without one window per pane, which multiplies every cost in option C by the pane count.

That settles where the value belongs. It is app-wide, so it goes in `settings.json` next to `tray_enabled` and `surprise_me` (`settings.py:19-20`), not in `customization.json`, which is keyed per account.

Load must degrade the way the rest of that file does: a missing, non-numeric or out-of-range value falls back to the default rather than raising, matching the existing `isinstance` checks in `load_settings`.

## The control surface, and the floor

`tk.Menu` has no slider, and pystray cannot render one at all, so a literal slider is not available in the surface both menus share. Discrete levels as radio items fit the existing `MenuItem` model in `menu.py` and render identically in both.

Whatever the levels are, the lowest must be well above zero. At full transparency the widget is invisible and, on the colour-key path, click-through everywhere, so there is no way to get it back except the tray icon or hand-editing `settings.json`, and the tray is itself a toggle the user may have turned off. A floor around 30% keeps it recoverable.

Any menu item added here must go through `_after_menu_action` (`ui.py:386`). PR #54 exists because pystray builds its menu once and the win32 backend caches the native HMENU, so a toggle made in the right-click menu stayed invisible in the tray until `update_menu` ran. A green suite, 8/8 CI and a passing manual gate all missed that once already.

## Testing strategy

Headless tests can cover the settings round-trip, the clamp and degradation rules, the menu model containing the levels with the right one marked selected, and the platform branch choosing C or A from a faked `sys.platform`.

They cannot cover any of the findings in this document. `-transparentcolor` does not exist under Xvfb, `-alpha` no-ops there, and neither hit testing nor antialiasing shows up in a headless assertion. So the real gate is manual on Windows, and it has to check at least: the cat stays crisp at the lowest level, the widget still drags from an empty area of the card, the right-click menu still opens over an empty area, the level survives a restart, and the right-click and tray menus agree on the current level after toggling from each side.

Baseline to preserve, confirmed before this document was written: 584 headless, 36 gui, `ruff check .` clean.

## Decisions (owner, 2026-09-03)

1. **Option C.** Two windows on Windows, whole-window `-alpha` as the automatic fallback everywhere else.
2. **Text lives on the card window** and fades with the background, rather than staying opaque on the keyed window and carrying the fringe.
3. **`driving_tag` and `tool_label` stay on the cat canvas, on an opaque chip.** See below; this replaces the earlier suggestion of moving them to the card window.
4. **Six levels: 100, 90, 80, 70, 60, 50, with a floor of 50.** The cat and bars are fully opaque at every one of them, so the level only controls how faint the card and the text get.
5. **The permission accent ignores the opacity setting.** Its job is to catch peripheral vision, and a 50% version of it catches less.

## The chip, which resolves the two overlay tags

`driving_tag` and `tool_label` are canvas text drawn on the cat canvas (`ui.py:259-271`), so under option C they sit on the keyed surface. Neither obvious answer works.

Leaving them keyed is the worst case measured anywhere in this spike, not the mildest. Canvas text keys exactly as badly as `tk.Label` text does: rendered at Segoe UI 8 against a `#010203` key over a card at alpha 0.5, `tool_label` in `FG_COLOR` stays legible but fringed, and `driving_tag` in `DIM_COLOR` is illegible. The dim 8pt case is precisely the combination that suffers most.

Moving them to the card window puts them behind the cat instead of on top of it, because the content window is above the card window. That is a real regression, not a theoretical one: the sprite grid is 28x26 at `SCALE` 4, so it spans the full 112px canvas width and y 4 to 108, and both tag positions fall inside its footprint. Counting opaque cells under the tag regions, the top-left region (`tool_label`) has 4 to 7 opaque cells of 33 sampled, and the bottom-left region (`driving_tag`) has 14 to 26 of 44. That sample covered 4 states and 2 frames each. `sprites.ALL_STATES` actually holds 14 states, and `flopped` has 4 frames rather than 2, so the real worst case is unmeasured and can only be larger. The cat would eat a third to half of the tag.

The fix is to give each tag an opaque backing rectangle on the keyed canvas: create the text, take its `bbox`, draw a filled rectangle in `BG_COLOR` `#1c1c22` padded 3px horizontally and 1px vertically, then `tag_raise` the text above it. Text antialiased against a colour that is actually still there renders correctly, and the chip is opaque so it survives the key.

Verified by screenshot against the same opaque control used for the other comparisons: both tags are crisp and fully legible with no fringing, and they stay above the cat. The visible change is that each tag now reads as a dark badge rather than bare text over the sprite, which is a normal idiom for a transient status tag.

The same trick does not generalise to the six stat labels. A chip behind every line would amount to painting the card background back in, which is the thing the feature exists to remove.

## The accent interaction, which is window-wide

Decision 5 has a consequence worth stating before it is planned. The accent is per-pane: `render` takes `accent` per pane and swaps that pane's background to `ACCENT_BG` (`ui.py:207-218`). Opacity is not per-pane, because `-alpha` is a property of the whole toplevel.

So "the accent ignores the opacity setting" has to mean the card window goes to alpha 1.0 whenever any pane is accented, and returns to the stored level when no pane is. With one pane that is exactly the intent. With several, one pane's pending permission prompt briefly makes every pane opaque.

That is the right trade, since the alternative is an accent that is quieter than the state it exists to announce, but it is a behaviour to name in the plan rather than discover.

It is also not brief. `GONE_S` in `activity.py:16` is 1800.0, so an abandoned permission flag persists for 30 minutes by design. One stale pane can hold every pane opaque for that long.

## Adversarial review, 2026-09-03

Reviewed cold by `gpt-5.6-sol` at high effort, read-only, briefed with all seven measurements and the exact probe methods. Verdict was request-changes, and it was right. Every code-level claim below was checked against the repo before being recorded here, and the blocker was reproduced and then fixed empirically.

### The blocker: a single real click inverts the two windows and the cat disappears

`WindowFromPoint` reports the pre-click hit target. It says nothing about what happens after the click lands. A real click on an inactive top-level sends `WM_MOUSEACTIVATE`, and default handling activates that window and raises it within its topmost band. So clicking the card raises the card above the content window, and the content window is exactly where the cat lives.

Reproduced with real mouse input via `SetCursorPos` plus `mouse_event`, counting visible cat pixels in a screenshot after each click:

```
before any click          fg=content  content_above_card=True   cat_px_visible=3600
after click on card       fg=card     content_above_card=False  cat_px_visible=0
after click on cat        fg=card     content_above_card=False  cat_px_visible=0
after click on card again fg=card     content_above_card=False  cat_px_visible=0
```

The cat goes from 3600 visible pixels to 0 and never comes back, because it is now behind an opaque region of the card. One drag of the widget, which is the most ordinary interaction it has, destroys the feature. None of the earlier spikes could see this: they inspected hit testing and z-order without ever sending a click.

The earlier stacking check was also weaker than it read. Walking `GW_HWNDNEXT` down from the content window until it reaches the card proves the card is somewhere below, not that it is directly below. Another topmost window between them would expose and receive input through the keyed holes.

### The fix: make the content window owned by the card window

Three mitigations were tested against the same click sequence plus a simulated drag.

| Mitigation | Result |
|---|---|
| `SetWindowLongPtr(content, GWLP_HWNDPARENT, card)` | Works. `content_above_card=True` and `cat_px=3600` through every click and the drag. |
| `WS_EX_NOACTIVATE` on the card | Fails. The card still took the foreground and still inverted, `cat_px=0`. |
| Tk `<Button-1>` on the card calling `content.lift()` | Works in this test, but repairs after the fact rather than preventing, and only for events Tk sees. |

An owned window is always above its owner and cannot be covered by it, which is enforced by Windows rather than re-asserted by us. That is the mechanism to build on. The Tk re-lift is worth keeping as a cheap secondary repair, not as the primary. `WS_EX_NOACTIVATE` is refuted and should not be retried.

This is Windows-only ctypes, which costs nothing here because the whole two-window path is already Windows-only.

### Supporting defects, all verified in the code

1. **`Pane` destroys the colour-key invariant on every render.** `set_appearance` (`ui.py:91`) and `render` (`ui.py:183`) both set the cat canvas background to the pane's card or accent colour. Re-parenting the widgets does not fix that. The plan needs an explicit invariant: the content toplevel, its frames and the cat canvas empty pixels stay the key colour permanently, bar tracks stay deliberately opaque, and no per-pane render path may recolour a keyed surface. The hand-built spikes bypassed this code entirely.

2. **Settings writes already lose fields, before opacity is added.** `TrayManager.set_enabled` (`tray.py:154`) writes `Settings(tray_enabled=enabled)` and drops `surprise_me` on the floor today. `toggle_surprise` (`__main__.py:640`) rebuilds from `settings.tray_enabled`, a startup snapshot, so it can resurrect a stale value. Adding a third field gives both writers a third thing to lose. This needs one merge-style update path, and it is a live bug independent of #37.

3. **Settings load after the window is built.** `tk.Tk()` and `TokittyWindow` are constructed at `__main__.py:393-394`, and `load_settings` does not run until `__main__.py:627`. Cold start would flash fully opaque and then snap to the stored level. Load settings first and pass the initial opacity into construction.

4. **Per-pane accent cannot drive window alpha from inside `Pane.render`.** Panes render sequentially at `__main__.py:744`, so the last pane wins: an accented pane sets 1.0 and an ordinary pane immediately restores 0.5. Accent has to be aggregated at `TokittyWindow` level once all pane states are known.

5. **Drag needs `x_root`/`y_root`.** `_on_drag_start` and `_on_drag_move` (`ui.py:341-350`) use widget-relative `event.x`/`event.y`. With two toplevels and descendants on different surfaces, that is ambiguous. Use root coordinates plus the card's starting geometry. Two `geometry()` calls are also not atomic, so transient separation during a drag is possible and needs a synchronisation policy with reentrancy protection.

6. **DPI awareness is already set too late.** `_configure_window` (`ui.py:318`) calls `SetProcessDpiAwareness(2)` from `TokittyWindow.__init__`, which runs after `tk.Tk()` at `__main__.py:393` has already created an HWND. Windows does not support changing process DPI awareness once a window exists, and the bare `except Exception: pass` hides the failure because the HRESULT is never inspected. Pre-existing, but it matters more with two HWNDs, which can process `WM_DPICHANGED` independently and diverge.

7. **The chip needs bounds.** `_tool_label` (`activity.py:52`) returns unknown tool names verbatim, so an arbitrary MCP or custom tool name becomes an arbitrarily wide opaque slab across the cat. Specify a maximum width or truncation and the clipping behaviour, and give the chip rectangle the same `"cat"` delete tag as the text or `_draw_frame` will leak a rectangle per frame.

8. **Key collision is exact, not near, and the wrong set was checked.** Collision is exact RGB equality, so "none is near `#010203`" is not the relevant test. Of the four user-settable overrides, `coat_base`, `coat_shade` and `bar_fill` live on the keyed surface and can collide; `card_bg` cannot, because it belongs to the card window. Compare normalised RGB rather than case-sensitive strings, and do not silently rewrite the colour the user picked in `customization.json`. Substitute at render time, or pick the key dynamically from a colour absent from the resolved keyed surface.

9. **Do not double-bind the context menu.** `bind_all` is interpreter-wide and already sees events from every Tk toplevel, so binding `<Button-3>` on both windows risks duplicate or replacement behaviour. Drag bindings do need to cover both toplevels. Separately, the dialogs in `ui.py` and `accounts_ui.py` are `transient` to `root`, so the design has to say which window `root` is.

10. **Readability at the floor is unmeasured.** The fringing comparison shows the mechanism cleanly but was run over one light backdrop at alpha 0.55. All six stat labels, including 8pt `DIM_COLOR`, fade to 50% over arbitrary wallpaper. Needs light, dark, high-frequency and similarly-coloured backdrops at each supported DPI scale before 50 is confirmed as the floor.

### What the review did not change

The capability probe, the controlled text-fringing comparison, the chip as a rendering remedy, app-wide rather than per-account persistence, and leaving `grid_size` and `pane_index_at` untouched all stand.

The reviewer's position on #28 is worth recording accurately: it would not start the PySide6 rewrite for #37 alone, but it would defer #37 into #28 rather than ship option C if the project is unwilling to maintain an owned two-HWND unit and repeat a Windows interaction and DPI gate for future UI work. The evidence rules out options A and B as exact implementations of the issue. It does not rule out deferral.

## Open questions

1. Given that option C now requires an owned two-HWND unit maintained with ctypes, plus a manual Windows interaction and DPI gate that headless CI cannot cover, is that still the right trade against deferring #37 into #28. The blocker has a verified fix, so this is a maintenance-appetite question rather than a feasibility one.
2. Whether the settings-writer bug (defect 2) is fixed as a prerequisite commit on its own, since it loses `surprise_me` today regardless of #37.
3. Whether the DPI ordering fix (defect 6) belongs in this work or a separate issue. It is pre-existing and touches startup for every platform.
