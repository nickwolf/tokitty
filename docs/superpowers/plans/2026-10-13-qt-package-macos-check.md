# Mac checks for the frozen tk,qt build (#115)

The frozen checks the spec leaves to a real Mac: decisions 9 (notifications) and 10 (About panel), and #117 items 3, 14 and 15. Item 13 (`LSUIElement`) moved to #142.

## Setup

The build is the `workflow_dispatch` run on `qt-package-115` (run 38506230465). Quit the installed Tokitty first: both copies use `~/Library/Application Support/Tokitty` and its lock.

```
gh run download 38506230465 -R nickwolf/tokitty -n tokitty-macos-arm64 -D ~/Downloads/tokitty-115
cd ~/Downloads/tokitty-115
ditto -x -k tokitty/tokitty/tokitty-dryrun-*-macos-arm64.zip .
xattr -dr com.apple.quarantine Tokitty.app
```

A real launch of this copy repoints the Claude Code hook command in `~/.claude/settings.json` at it; the next launch of the installed app puts it back. `TOKITTY_DEBUG_STATE=content` in front of a command gives a fake pane that touches nothing, which is enough for checks 1 to 4.

## Checks

1. Tk mode, unchanged: `open Tokitty.app`. The widget comes up with a Dock icon and an app menu, as v0.6.2 does. Tokitty ▸ About Tokitty shows the version once and two copyright lines, the second "Includes Qt 6.12.0 and PySide6 6.12.0 under the LGPL-3.0, see Contents/Resources/licenses." Quit.
2. Qt mode: `TOKITTY_QT=1 ./Tokitty.app/Contents/MacOS/Tokitty`. The widget and the menu-bar cat come up. Tokitty ▸ About Tokitty opens the native panel with the name "Tokitty", the version once, and the same two copyright lines (decision 10). Quit from the menu-bar menu.
3. Notifications (decision 9): `TOKITTY_QT=1 TOKITTY_DEBUG_NOTIFY=1 ./Tokitty.app/Contents/MacOS/Tokitty`. About 5 s after start, Terminal prints `tokitty: debug notify: submitted` or `not submitted (...)`. Does a notification appear? Does macOS ask for permission first? Quit and run it a second time. Note what you see each time.
4. Licences: `ls Tokitty.app/Contents/Resources/licenses` lists seven files, and `ls Tokitty.app/Contents/Resources/pystray Tokitty.app/Contents/Resources/PySide6` shows plain `.py` files.
5. #117 item 3, full screen, Qt mode (a real launch from here on: `TOKITTY_QT=1 ./Tokitty.app/Contents/MacOS/Tokitty`): put another app in full screen. Is the widget visible over it?
6. #117 item 14, focus with real typing: Terminal focused with a command half typed, drag the widget by the cat, keep typing. Keystrokes stay in Terminal and Terminal stays frontmost.
7. #117 item 15, network: the usage numbers load, and Settings ▸ General ▸ Check now reaches GitHub. A dry-run build compares by its package version, 0.6.2, so expect "Tokitty v0.6.2 is the latest version."; a certificate error is the failure #145 fixed and must not come back.

Afterwards, launch the installed Tokitty once so the hook command points back at it.

## Results

Run on Nick's Mac (Apple silicon, macOS 26) on 2026-10-14. I used the CI zip from run 38512074951 (38c6dca, the branch's last code commit) rather than 38506230465, since only docs commits come after it. The zip's SHA-256 matched. It is 35,047,833 bytes and unpacks to 90 MB. Checks 1 and 4 used `TOKITTY_DEBUG_STATE=content`; checks 2, 3 and 5 to 7 used real launches (see the first correction below).

1. Tk mode: pass, with an old defect. Dock icon and app menu as in v0.6.2. About shows "Tokitty" and both copyright lines, but the version line reads "Version 0.6.2 (0.6.2)". The installed v0.6.2 shows the same, so it predates #115: on this macOS, Tk's `orderFrontStandardAboutPanelWithOptions_({"Version": ""})` through pyobjc keeps the build number, while Qt's ctypes call with the same option drops it (check 2). It goes with the Tk About panel in #142.
2. Qt mode: pass. The widget and the menu-bar cat come up. About opens the native panel with "Tokitty", "Version 0.6.2" once, and both copyright lines. Exit from the menu-bar menu removes the cat, and the process ends within 0.5 s. Settings… from the menu-bar menu opens Settings.
3. Notifications: run 1 printed `tokitty: debug notify: submitted`, and macOS showed its permission prompt ("“Tokitty” Notifications: Notifications may include alerts, sounds, and icon badges.") instead of the notification. Run 2, with that prompt still unanswered, printed `submitted` again, and nothing was shown. Nick then allowed notifications for Tokitty and reports that the notification looks good. Under decision 9, the first notification on a Mac is only seen after this permission prompt. The prompt belongs to `com.nickwolf.tokitty`, which the installed app shares, so the choice also applies there.
4. Licences: pass. `licenses/` holds GPL-3.0.txt, LGPL-2.1.txt, LGPL-3.0.txt, LICENSE, pystray-NOTICE.txt, python-xlib-NOTICE.txt and Qt-NOTICE.txt. `pystray/` has plain `.py` files, and `PySide6/` has `__init__.py` next to its binaries.
5. #117 item 3, full screen: fail. With TextEdit in full screen, no Tokitty window is on screen in that Space, and the widget is back once full screen ends. The build is still a regular app with a Dock icon, and `LSUIElement` (item 13) moved to #142, which is probably why. This item likely goes to #142 with it.
6. #117 item 14, focus: pass. I typed into a TextEdit document rather than Terminal, so no keystroke could reach a live shell. I typed, dragged the widget 100 pt by the cat, typed more, dragged it back and typed again. Every keystroke reached the document, and TextEdit stayed frontmost throughout.
7. #117 item 15, network: pass. Live usage loaded, and Check now said "Tokitty v0.6.2 is the latest version." with no certificate error.

Corrections to this plan:

- `TOKITTY_DEBUG_STATE=content` covers checks 1 and 4 only. A debug-state Qt launch returns before the tray, the app menu's About and Settings items, and the `TOKITTY_DEBUG_NOTIFY` timer are set up, so checks 2 and 3 need a real launch.
- Besides repointing `current`, a real launch that drags the card saves its position in Qt's form. Here that was `{"x": 47, "y": 894, "unit": "logical"}`: the saved y=951 left the card partly behind the Dock, so #148's clamp lifted it. Back up `position.json` before the checks and restore it afterwards.
