# Mac checks for the Qt-only build (#142)

The frozen checks the spec leaves to a real Mac: the agent app (`LSUIElement`, #117 item 13), About in the menus, windows coming to the front, full screen (#117 item 3), focus while dragging (#117 item 14), the notification trial rerun (decision 5), the network (#117 item 15), the licences, and quitting the first-run walkthrough. Spec: `docs/superpowers/specs/2026-10-14-qt-only-design.md`.

## Setup

The build is the `workflow_dispatch` run of `release.yml` on `qt-only-142` (run 38642089300 at 841e8f7). That build predates b28dbbc, so it still has the Release notes link in About and the old debug-menu Always in front; those two changes need a later run. Quit the installed Tokitty first: both copies use `~/Library/Application Support/Tokitty` and its lock.

```
gh run download 38642089300 -R nickwolf/tokitty -n tokitty-macos-arm64 -D ~/Downloads/tokitty-142
cd ~/Downloads/tokitty-142
ditto -x -k tokitty/tokitty/tokitty-dryrun-*-macos-arm64.zip .
xattr -dr com.apple.quarantine Tokitty.app
cp ~/Library/Application\ Support/Tokitty/position.json ~/Downloads/tokitty-142/position.json.bak
```

Back up `position.json` (last line above) before any real launch: a real launch that moves the card saves its position, and #115's check found it rewritten. Restore it at the end.

A real launch also repoints the Claude Code hook command in `~/.claude/settings.json` at this copy; launching the installed app afterwards puts it back.

`TOKITTY_DEBUG_STATE=content` gives a fake pane that touches nothing, but a debug-state launch returns before the tray, the menus' About and Settings items and the notification timer are set up. None of the checks below use it: check 4 needs no launch, and every other check needs a real launch (`./Tokitty.app/Contents/MacOS/Tokitty`, or `open Tokitty.app` where noted).

## Checks

1. Agent app (#117 item 13): `open Tokitty.app`. The widget and the menu-bar cat come up, with no Dock icon and no Tokitty app menu (the menu bar keeps showing the frontmost app's menus). Command-Tab does not list Tokitty.
2. About: menu-bar cat ▸ About Tokitty opens the Tokitty About window in front of other apps: the cat, "Tokitty", the version, the GitHub, Release notes and Licences links, each of which opens (this build predates b28dbbc; a dry-run build after it shows no Release notes link, since its release page would describe a different build), "Copyright © 2026 Nick Wolf. MIT License." and the Qt line. Close it, then right-click the widget ▸ About Tokitty: the same window, in front.
3. Windows come to the front: with another app frontmost (TextEdit), open Settings… from the menu-bar cat, then from the right-click menu. Each time Settings is in front and takes typing. Same for Accounts (from Settings) and Check now's result box.
4. Licences: `ls Tokitty.app/Contents/Resources/licenses` lists four files: GPL-3.0.txt, LGPL-3.0.txt, LICENSE, Qt-NOTICE.txt. No pystray or python-xlib notices.
5. #117 item 3, full screen: put TextEdit in full screen. Is the widget visible over it, in that Space?
6. #117 item 14, focus: type into a TextEdit document, drag the widget by the cat, keep typing. Every keystroke reaches the document and TextEdit stays frontmost.
7. Notifications (decision 5): quit Tokitty, then `TOKITTY_DEBUG_NOTIFY=1 ./Tokitty.app/Contents/MacOS/Tokitty`. About 5 s after start, Terminal prints `tokitty: debug notify: submitted` or `not submitted (...)`. Does a notification appear? Notifications for Tokitty were allowed during #115, so to repeat that trial's first-run case, first set Tokitty's notifications to off in System Settings ▸ Notifications, or note that it was left allowed. Note what you see. The gate stays unless the first toast arrives without a permission prompt eating it.
8. #117 item 15, network: the usage numbers load, and Settings ▸ General ▸ Check now reaches GitHub ("Tokitty v0.6.2 is the latest version." from a dry-run build). No certificate error.
9. Walkthrough Quit: quit Tokitty, move the state dir aside (`mv ~/Library/Application\ Support/Tokitty ~/Library/Application\ Support/Tokitty.142bak`), and launch. The first-run walkthrough comes up in front. Press Command-Q: Tokitty exits. Launch again: the walkthrough shows again. Quit it with Command-Q, then restore the state dir (`[ -d ~/Library/Application\ Support/Tokitty.142bak ] && rm -rf ~/Library/Application\ Support/Tokitty && mv ~/Library/Application\ Support/Tokitty.142bak ~/Library/Application\ Support/Tokitty`).
10. Exit: menu-bar cat ▸ Exit removes the cat and ends the process within a second.

Afterwards: restore `position.json` from the backup, and launch the installed Tokitty once so the hook command points back at it.

## Results

(Filled in after the run.)
