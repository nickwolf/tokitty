# Handoff: PySide6 go/no-go spike (#28)

Paste this into a fresh session rooted at `C:\Tools\tokitty`. It is a spike brief, not an implementation plan. The output is evidence and a recommendation. Nothing from the spike gets merged into the app.

## Why this exists

Nick runs tokitty on Windows and on a Mac, and wants the widget to stop being a fixed box. The cat should be able to leave the card or use more of it: sleep on top of the card, walk to the other side, wander around it (#22). The card itself should be able to go see-through or invisible while the cat and the bars stay fully opaque, and the whole widget should be more customizable than it is now, not just the cat's coat.

The current sprites keep their look. Same 28x26 grids, same palettes, same `SCALE = 4` pixel art. This is about what the art is drawn on, not the art.

That needs one window larger than the card, with real per-pixel alpha, so the cat can be drawn outside the card's edges over a transparent margin. Tk can't do that. On Windows, #37 gets an opaque cat over a faded card with two stacked windows and a colour key, and that only works inside the card's rectangle. On macOS and Linux, Tk fades the whole window together. [#28](https://github.com/nickwolf/tokitty/issues/28) is the PySide6 rewrite that would deliver it properly. The spike decides whether to start it.

## What tokitty is, briefly

A small always-on-top desktop widget: a pixel-art cat plus session and weekly usage bars for Claude Code accounts, with live activity states from Claude Code hooks. Python 3.10+, tkinter for every window, `pystray` for the tray, `Pillow`. Runtime dependencies are exactly `pystray` and `Pillow>=10.1` (`pyproject.toml:8`). Shipped as frozen PyInstaller builds for Windows, macOS (arm64 and x86_64) and Linux through `.github/workflows/release.yml`, with an in-app auto-updater.

## Facts measured on 2026-10-09, do not re-derive

- At `f8ae9b9` (v0.6.0), 15 files under `tokitty/` import tkinter, 7,471 lines between them. The biggest are `__main__.py`, `accounts_ui.py`, `ui.py`, `first_run_ui.py` and `settings_ui.py`. The #28 issue comment still says the swap is "only `ui.py` (~489 LOC)". That was true in July and is not true now.
- Everything below the view is toolkit-free: the state machine, pollers and watchers, sprites and palettes, `customize.py`, `transparency.py`'s level arithmetic, accounts, hooks.
- `tokitty/sprite_raster.py` already has `raster_rgba(frame, palette, scale)`, which returns `(width, height, rgba_bytes)` with empty cells fully transparent. The tray icon uses it. That byte string can go straight into a `QImage` (`Format_RGBA8888`), so the spike does not need a new rasterizer.
- v0.6.0 release asset sizes, the baseline for the packaging gate: `tokitty-v0.6.0-windows-x64.zip` 22,417,629 bytes, `tokitty-v0.6.0-macos-arm64.zip` 18,982,047, `tokitty-v0.6.0-macos-x86_64.zip` 20,165,770, `tokitty-v0.6.0-linux-x86_64.tar.gz` 38,795,809.
- macOS has no tray icon today. pystray's darwin backend needs `NSApplication.run()` on the main thread and Tk's `mainloop()` already owns it (#45, README line 279). Under Qt, one event loop owns the main thread and `QSystemTrayIcon` is native, so this may go away. Check it, don't assume it.
- On Windows, a colour-keyed pixel is click-through and a real click can reorder two sibling topmost windows. Both cost a lot to find during #37. `docs/superpowers/specs/2026-09-03-transparency-options-design.md` has the details and the `WindowFromPoint` method used to verify hit testing.

## What to build

A throwaway branch `spike/pyside6` in a linked worktree, holding one standalone script, `spike/qt_card.py`. It imports from the `tokitty` package for sprites and palettes and nothing else. It does not touch the app's code or `pyproject.toml`. Install `PySide6-Essentials` (QtCore, QtGui, QtWidgets only) into a venv, not the full `PySide6` meta-package.

The script opens one frameless, always-on-top window that does not show up in the taskbar or Dock, with `WA_TranslucentBackground`. It must be larger than the card on every side the cat can go to. Inside it:

- A card drawn with `QPainter`: rounded rectangle, adjustable opacity from 0% to 100% (keys or a small slider), two usage bars and their labels drawn in the card.
- The current cat from `get_frames(state)` and `get_palette(...)`, animated at the app's 800ms frame interval, drawn from `raster_rgba` at `SCALE = 4` with nearest-neighbour scaling only.
- A key that moves the cat between four spots: inside the card where it is today, sitting on the card's top edge with its body above the card, beside the card outside its rectangle, and walking across the card's top edge. The walk uses `QImage.mirrored()` for the other direction.
- Drag to move the widget, and a right-click `QMenu` with Quit.

No wandering logic, no theme model, no dialogs, no real usage data. Hardcode the bar values.

## Gates

Each gate records a pass or fail with the measurement behind it. Windows is checked on this machine with real Windows Python, not from WSL's Python. The Mac checks are Nick's, so the script has to run there from a clone with `python3 -m venv .venv && .venv/bin/pip install PySide6-Essentials && .venv/bin/python spike/qt_card.py`, and the findings doc needs a short numbered checklist he can work through.

1. **Pixel identity.** The cat Qt draws is identical to `raster_rgba` output, byte for byte, at 100% and 150% Windows display scaling and on a Retina display. Grab the widget with `QWidget.grab()` and compare pixels. No smoothing, no half-pixel offsets.
2. **Click-through on the empty margin.** A click on a fully transparent pixel goes to the window underneath, on both OSes. A click on the cat, a bar, or the card at any opacity above 0% reaches tokitty. On Windows, verify with `WindowFromPoint` as in #37, not by eye. Record what happens to the card at exactly 0%. If it becomes click-through, an invisible card can only be grabbed by the cat or the bars, and the findings should say what that implies for recovery.
3. **Focus and stacking.** Clicking or dragging the widget does not take keyboard focus from the terminal (`WA_ShowWithoutActivating`, `WindowDoesNotAcceptFocus`). The widget stays on top of normal windows. On the Mac, record what happens when switching Spaces and with a full-screen app. If staying visible across Spaces needs `NSWindow` collection behaviour, which Qt doesn't expose, note that it would need `pyobjc` or a small native call. That is a dependency decision for Nick, not something to add in the spike.
4. **Drag and placement.** Dragging works on both OSes and across a mixed-DPI pair of monitors on Windows, and the cat stays crisp when the window crosses between them.
5. **Text.** Labels and bar text drawn over the card at 100%, 50%, 20% and 0% opacity. Take screenshots of each on both OSes. This is a judgment call for Nick, not a pass or fail.
6. **macOS menu bar.** `QSystemTrayIcon` shows a working menu-bar item on the Mac with no crash. Also check whether setting `LSUIElement` in a frozen build's `Info.plist` gives a widget with no Dock icon and no app menu bar, since that is #44.
7. **CPU.** Idle CPU of the spike at the 800ms frame interval, and while the cat walks at 30 frames per second, against the current tkinter app showing one pane. Measure on Windows over 60 seconds. On Mac, Activity Monitor's figure from Nick is fine.
8. **Frozen size.** A PyInstaller one-folder build of the spike, using `freeze/tokitty.spec` as the model and excluding unused Qt modules and plugins, zipped the same way as a release. Compare to the v0.6.0 numbers above. If Nick's Mac isn't handy, build the macOS one with a temporary `workflow_dispatch` workflow on the spike branch running on `macos-latest`. The existing `release.yml` builds the app, not the spike, so don't reuse it. The Linux build only has to succeed. Linux translucency is out of the gate.
9. **Licence.** PySide6 is LGPLv3. Confirm a one-folder PyInstaller build keeps Qt as separate, replaceable shared libraries, and list the notices a release would need to ship.

## What to produce

1. The spike script and its findings, committed to the spike branch only.
2. A findings doc at `docs/superpowers/specs/2026-10-XX-pyside6-spike-findings.md`: a gate table with the real measurements, the screenshots, and a go or no-go recommendation for #28. If it's a go, list anything the spike found that changes the sub-issues under the #28 milestone.
3. Stop and check in with Nick before anything else. The go/no-go is his decision.

## House rules

- Read `/mnt/c/Tools/docs/conventions/public_writing_playbook.md` before writing any prose that lands in the repo. Don't work from memory of it. No em-dashes anywhere, no hard-wrapping, keep every real number exact.
- PySide6 goes in the spike's venv only. Adding it to `pyproject.toml` is the port's job, after a go.
- Windows Python is at `/mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe`. If you run pytest with it, pass `--basetemp="C:\tmp\<name>"`, because the default pytest temp root on this machine is permission-locked.
- GUI windows launched from WSL sometimes land in invisible Session 0. Launching without an error does not mean the window appeared. Compare the process's `SessionId` with `explorer.exe`'s before trusting anything you see or don't see.
- On the autostart branch (#20), six assertions in the task plan were factually wrong, two of them passing on Linux and failing only on Windows. The same applies to this brief: treat every claim here about Qt's behaviour as a hypothesis for the spike to test.
