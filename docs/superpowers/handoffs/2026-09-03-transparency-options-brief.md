# Handoff: transparency options for cat and bars (#37)

Paste this into a fresh session rooted at `C:\Tools\tokitty`. It is a design and spike brief, not an implementation plan: #37 is deliberately exploratory and the hard question is what is even possible, not how to code it.

## The ask

Issue [#37](https://github.com/nickwolf/tokitty/issues/37), verbatim: "Let's add a transparency slider for the background behind the cat and play with different UI options for various levels of opacity."

So the target is not "fade the whole window". It is: the card background becomes see-through while the cat, the bars, and the text stay readable. Treat that distinction as the crux of the whole issue.

## What tokitty is, in one paragraph

A small always-on-top desktop widget: a pixel-art cat plus session and weekly usage bars, reflecting Claude Code usage and live activity. Python 3.10+, Tk/tkinter for the window, `pystray` for an optional tray icon. Runtime dependencies are exactly `pystray` and `Pillow` (`pyproject.toml:8`), and the project is deliberately stdlib-heavy otherwise. No installer: users clone the repo and run `pythonw.exe -m tokitty` on Windows or `python3 -m tokitty` elsewhere. Primary tested setup is Windows 11 with Claude Code inside WSL2 while tokitty runs as a native Windows process.

## How the UI is actually built, which constrains everything

There is no single composited canvas. Each pane is a `tk.Frame` holding ordinary Tk widgets with solid `bg` colors: a `tk.Canvas` for the cat (`ui.py:144`), `tk.Label` widgets for "SESSION", "WEEK", the reset texts, the hint and the pane label, and two more `tk.Canvas` widgets for the bars (`ui.py:151`, `ui.py:158`). `Pane.set_appearance` recolors them by calling `.configure(bg=...)` on each one (`ui.py:110-136`, `ui.py:208-225`).

Read `tokitty/ui.py` and `tokitty/customize.py` before designing anything. Per-pane look already persists through `customization.json` via `customize.py` (`colorway`, `pattern`, per-key overrides, `label`), and app-wide toggles live in `settings.json` via `settings.py` (`tray_enabled`, `surprise_me`). Deciding which of those two files a transparency setting belongs in is part of the design, and it is not obvious: transparency may be per-pane in principle but is enforced per-window by the OS.

## Empirical findings, measured on this machine, do not re-derive

Tk exposes exactly one transparency knob per platform, and the results differ. Measured directly:

Windows Python 3.13 (`/mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe`):
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

Three things follow, and they are the core of the design problem:

1. **`-alpha` is whole-window only.** It fades the cat, the bars and the text along with the background. It cannot deliver what #37 asks for on its own.
2. **`-alpha` silently no-ops on Linux without a compositor.** Setting 0.6 and reading back 1.0 is not a bug in the probe: X11 alpha needs a compositing window manager. Any Linux support has to treat "the setting applied but nothing happened" as an expected outcome, not an error.
3. **Per-region transparency is Windows-only, via `-transparentcolor`.** That is a color key: pick a magic color, and Windows renders every pixel of that color as fully transparent. It does not exist in Linux Tk at all, and macOS is untested here (no Mac available; Tk on macOS is documented to support `-alpha` and a `-transparent` attribute, but treat that as unverified).

macOS already has two standing Tk limitations recorded in the README worth re-reading: no tray icon (pystray's darwin backend needs `NSApplication.run()` on the main thread, which Tk's `mainloop()` owns, see #45), and the system menu bar cannot be removed without a real `.app` bundle (#44).

## The trap to investigate first

`-transparentcolor` on Windows does not just make those pixels invisible, it makes them **click-through**: mouse events pass to whatever is underneath. The card is dragged by its background, so a fully transparent background plausibly makes the widget undraggable, and may make the right-click menu unreachable over transparent areas.

Verify that before designing around it. It decides whether the Windows color-key route is viable at all, or whether it needs a deliberately opaque drag strip or a modifier-key drag. Check the drag and right-click bindings in `ui.py` while you are there.

## Relationship to #28, which you must address explicitly

[#28](https://github.com/nickwolf/tokitty/issues/28) is "PySide6 transparent-cat rewrite", fenced as a separate project-level decision. Real per-pixel alpha, meaning a genuinely translucent background behind a crisp opaque cat on every platform, is what that rewrite exists to deliver.

So say plainly in your output whether #37 is:
- a small win achievable in Tk now (for example a whole-window opacity slider, honestly labelled as fading everything), or
- only partly achievable, Windows-only, via the color-key route, or
- effectively a duplicate of #28 that should be closed or folded into it.

Do not quietly design a half-feature that implies it delivers what #28 is for. Nick is open to big platform moves but they are never the first choice, so if the honest answer is "this needs #28", say so rather than shipping something misleading.

## What to produce

1. Read the code and run the spikes. Confirm or refute the click-through problem yourself.
2. Then a short design doc at `docs/superpowers/specs/2026-09-XX-transparency-options-design.md`, matching the structure of the existing specs in that directory (Goal, Decided, Scope and non-goals, then the substance, then Open questions). Present the realistic options with their per-platform truth, recommend one, and state what each option cannot do.
3. Stop there and check in with Nick before writing a task plan. This issue's wording ("play with different UI options") means the design is genuinely undecided, and the option choice is his.

## House rules

- Read `/mnt/c/Tools/docs/conventions/public_writing_playbook.md` before writing any prose that lands in the repo. Do not work from memory of it. No em-dashes anywhere, no hard-wrapping, keep every real number exact.
- stdlib plus the existing `pystray` and `Pillow` only. Adding a dependency is a decision for Nick, not a default. PySide6 in particular is #28's territory.
- Verify against real Windows Python at `/mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe`, using `--basetemp="C:\tmp\<name>"` because the default pytest temp root on this machine is permission-locked. The Linux suite being green proves nothing about Windows here.
- Baseline to preserve: 584 headless and 36 gui tests, `ruff check .` clean. Confirm before you start, since the number moves.

## One hard-won warning about this repo's plans

On the autostart branch (#20, merged as PR #53), the design and every implementation were sound, but **six separate assertions in the task plan were factually wrong**, including two that passed on Linux and failed only on Windows. If you write a plan, treat its code blocks as a draft and verify the tool-behavior claims they encode. Do not bend an implementation to satisfy a plan-supplied expectation without first checking that the expectation is true.
