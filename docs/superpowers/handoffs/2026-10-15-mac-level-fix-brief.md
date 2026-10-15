# Brief: Always in front off on macOS (#117 checks 2 and 8)

For the Mac session. Work on this branch, `qt-mac-level-117` (cut from main at aa53ae6).

## The failure

From the Mac run of `docs/superpowers/plans/2026-10-15-release-gate-macos-check.md` on run 38764477381 (results in ff56312 on `release-gate-117`): turning Always in front off in the frozen agent app (`LSUIElement`) moves the widget from window level 8 to 3 (`NSFloatingWindowLevel`), never to 0. It stays above TextEdit after app switches, and above Tokitty's own Settings and Accounts windows, which are at level 0. Turning it back on gives 8 straight away. A source run in #117 comment 3 dropped to 0 after an app switch, because a regular app's deactivation lowers a floating `Qt.Tool` window and an agent app never goes through that.

Probable cause: Qt gives a `Qt.Tool` window `NSFloatingWindowLevel`, and only `WindowStaysOnTopHint` raises it; clearing the hint leaves 3.

## The fix

- `tokitty/qt/macos.py`: add a step that sets the NSWindow level, `setLevel:` 0 (`NSNormalWindowLevel`), when Always in front is off. Leave Qt's level alone when it is on. Give it its own step name, like `STEP_PREVENT_ACTIVATION` and `STEP_COLLECTION_BEHAVIOR`, and the same warn-and-continue error handling. `apply()` needs to know the state: pass it in (for example `apply(win_id, on_top=...)`), keeping existing callers working.
- `tokitty/qt/widget.py`: `_after_native_window` calls `macos.apply(int(self.winId()))`; pass `self._always_on_top`. `set_always_on_top` already reruns `_after_native_window` after the toggle recreates the native window, so the level is reapplied there.
- Check whether the Settings, Accounts, About, update and presets windows (`macos` is imported in `qt/settings.py`, `accounts.py`, `about.py`, `update_dialog.py`, `presets.py`, `walkthrough.py`) need the same, now that they ride Always in front: the Mac run saw Settings and Accounts at 0 when off and 8 when on, which is right, so probably not. Don't change them unless a check shows a problem.
- Tests: `tests/test_qt_macos.py` has a `FakeObjC`; add the level step there (off sets 0, on leaves the level untouched, a failure warns and the other steps still run). If cheap, add a cocoa test to `tests/qt/test_macos_native.py` that toggles the widget and reads `[NSWindow level]`.
- Don't touch `tokitty/qt/walkthrough.py` or `tests/qt/test_walkthrough.py`: the walkthrough copy fix is already on `release-gate-117` (PR #161).

## Verify

1. `python -m pytest tests/test_qt_macos.py` and `QT_QPA_PLATFORM=cocoa python -m pytest -q -m qt tests/qt/test_macos_native.py`.
2. Push the branch, run the release dry run from it: `gh workflow run release.yml --ref qt-mac-level-117`. This branch doesn't have the rehearsal upload, which you don't need here.
3. With that build, rerun checks 2 and 8 of the checklist (setup as in the checklist, with the new run id): off gives level 0, TextEdit can cover the widget, Settings and Accounts sit above it, and "click wallpaper to reveal desktop" no longer sweeps it away; on gives 8. After the toggle, Spaces (check 1), full screen and check 3 still pass.
4. Note the level values you read and the run id in a short Results section at the end of this file.

Then push and tell Nick. Don't open a PR: the Windows session reviews the diff and ships it. No em-dashes in commit messages or code comments.

## Results

Fix in 5062f31. One addition to the plan above: a one-off `setLevel:` is not enough. Qt's `QNSWindow applicationActivationChanged:` (registered for `WA_MacAlwaysShowToolWindow`, qtbase 6.12 `qnswindow.mm`) sets every level 0 or 3 window back to `windowLevel(flags)` on `NSApplicationWillBecomeActiveNotification`, which is 3 for a `Qt.Tool` window. A source probe showed it: forced to 0, the widget read 3 again after a deactivate and reactivate. Opening Settings activates the app, so check 8 would still have failed. The widget now also runs the helper on every `applicationStateChanged` to active while Always in front is off. Qt reports that state from DidBecomeActive, after its own reset.

Tests: `tests/test_qt_macos.py` 13 passed; the cocoa tests in `tests/qt/test_macos_native.py` 4 passed, including a new one that reads `[NSWindow level]` through a real deactivate (`open -a Finder`) and reactivate: `8 0 0 8` with the fix, `8 0 3 8` with the activation handler stubbed out. Full suite 2863 passed, offscreen `-m qt` 555 passed, ruff clean.

Release dry run 38787761305 at 5062f31: linux-x86_64, windows-x64, macos-x86_64 and the Keychain check passed. macos-arm64 failed `hook_timing_gate` on all four attempts, with frozen medians of 103.0, 158.3, 113.7 and 102.2 ms against the 100 ms gate (baselines 60.2, 75.2, 46.3 and 47.3 ms). That is inside the #158 spread, and this branch does not touch the hook, but four in a row is more than #158 has seen. The gate stops the run before `hook_events`, `autostart` and the update tests. The arm64 build was uploaded anyway (sha256 OK), and the checks below used it.

Mac checks on that build, 2026-10-15, same Mac and method as the release-gate run:

- Check 2. On: level 8. Right-click ▸ Always in front off: level 0, and an Untitled TextEdit window moved over the widget covered it. After Finder then TextEdit (an app switch): still 0 and still covered. On again: 8, above TextEdit. After the toggle, check 1 (other Space: no other app's windows, widget there at 8), full screen (widget over full-screen TextEdit) and check 3 (`% echo tokitty ok`, nothing ran, Terminal stayed frontmost) all passed.
- Wallpaper click with Always in front off still sweeps the widget off to (−288, 1081), and the next click brings it back. It does the same to Chrome and every other level 0 window, so that is the normal-level behaviour and not a bug; at level 8 it stays put. The expectation in Verify step 3 was wrong.
- Check 8, off, with the widget moved under where Settings opens (700, 495). Settings… from the menu-bar cat with TextEdit frontmost: Tokitty active, a typed `z` did not reach TextEdit, Settings at level 0 in front of the widget at level 0 (the window on top at the widget's centre was Settings). The widget stayed at 0 through that activation; before the fix it read 3 here. Colour picker: level 8 above both. Manage accounts…: level 0 in front of Settings and the widget. Menu-bar cat toggle with Settings open: on put Settings and the widget at 8 with Settings in front, off put both back to 0 with Settings still in front. Switching to TextEdit and back to Tokitty (a click on Settings' title bar) left the widget at 0 under Settings.
- Check 8, on: Settings, the colour picker and the accounts manager each came up at level 8 in front of the widget, and a typed `z` did not reach TextEdit.

Afterwards: `position.json` restored, `customization.json` and `settings.json` unchanged, installed Tokitty relaunched with `current` pointing back at it.
