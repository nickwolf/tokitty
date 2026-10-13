# #114 macOS manual check

Run on a Mac from a checkout of `qt-tray-114`. Spec: `docs/superpowers/specs/2026-10-13-qt-tray-design.md`; this is the macOS half of its manual checks.

Setup, from the repo root:

```
python3 -m venv /tmp/tk114 && /tmp/tk114/bin/pip install -e ".[dev,qt]"
mkdir -p /tmp/tk114-home
```

Scratch state: `HOME=/tmp/tk114-home` keeps the real `~/Library/Application Support/Tokitty` untouched. The cat will show no usage data, which is fine for these checks. The first launch shows the walkthrough; finish or close it.

```
HOME=/tmp/tk114-home TOKITTY_QT=1 /tmp/tk114/bin/python -m tokitty
```

1. The cat appears in the menu bar, and it's legible with both a light and a dark menu bar (System Settings > Appearance).
2. Clicking it opens a menu with the same items as the widget's right-click menu, and each one works. A View or Always in front change made there shows in the widget's right-click menu, and the other way round.
3. Settings… from the menu-bar item comes to the front, with Always in front on and off.
4. Settings > General > Show tray icon removes the item and puts it back, and the choice survives a relaunch.
5. The app menu has an About item (in a source run it may read "About Python"). It shows a box saying "Tokitty <version>" and "Running from source."
6. Quit from the menu-bar item removes the item and ends the process.
7. The real native panel call, which tests the ctypes signatures on this Mac's CPU: `QT_QPA_PLATFORM=cocoa /tmp/tk114/bin/python -m pytest -q -m qt tests/qt/test_macos_native.py` passes, including `test_the_real_about_panel_opens_without_aborting` (it opens Python's About panel for half a second).

Report: pass or fail for each step, a screenshot of the menu-bar item, and the text the About item shows in step 5.
