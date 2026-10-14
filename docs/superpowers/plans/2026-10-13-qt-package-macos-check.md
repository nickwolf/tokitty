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

(filled in from Nick's run)
