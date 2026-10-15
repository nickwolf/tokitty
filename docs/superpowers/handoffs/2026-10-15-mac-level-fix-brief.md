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
