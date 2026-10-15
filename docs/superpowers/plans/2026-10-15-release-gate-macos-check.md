# Mac checks for the v0.7.0 release gate (#117)

Only the #117 items no frozen Qt-only build has proven on a Mac yet. Already proven on the #142 build (run 38642089300 at 841e8f7, `2026-10-14-qt-only-macos-check.md`), with no change since to the code they depend on: item 3 (full screen), 13 (agent app), 14 (focus while dragging, TextEdit) and 15 (network). Item 10 (Command-comma) went away with the app menu in #142 (decision 5). Item 9 (mixed DPI) can't be tested: no external monitor.

## Setup

The candidate is the `workflow_dispatch` run of `release.yml` on `release-gate-117` (run 38764477381 at 3097d7d). Its app code is main's (aa53ae6); the branch only adds an upload of the v99.0.0 rehearsal archive, which check 10 needs. Quit the installed Tokitty first: both copies use `~/Library/Application Support/Tokitty` and its lock.

```
gh run download 38764477381 -R nickwolf/tokitty -n tokitty-macos-arm64 -D ~/Downloads/tokitty-117
gh run download 38764477381 -R nickwolf/tokitty -n tokitty-rehearsal-macos-arm64 -D ~/Downloads/tokitty-117/rehearsal
cd ~/Downloads/tokitty-117
ditto -x -k tokitty/tokitty/tokitty-dryrun-3097d7d-macos-arm64.zip .
xattr -dr com.apple.quarantine Tokitty.app
S=~/Library/Application\ Support/Tokitty
cp "$S/position.json" position.json.bak; cp "$S/customization.json" customization.json.bak
```

Every check uses a real launch, `./Tokitty.app/Contents/MacOS/Tokitty` from `~/Downloads/tokitty-117`, because a `TOKITTY_DEBUG_STATE` launch skips the tray, the menus and the saved position. A real launch repoints the Claude Code hook command at this copy; launching the installed app at the end puts it back.

## Checks

1. Spaces (item 2). Launch. Switch Space with Ctrl+Right and back. The widget is on every Space.
2. Always in front (item 4). Right-click the widget, turn Always in front off. Another window (TextEdit) can now cover the widget, straight away, without switching apps first (the source run in comment 3 stayed floating until the next app switch). Turn it back on: the widget is above TextEdit again. Then, since the toggle recreates the native window, repeat check 1, full screen (put TextEdit in full screen: the widget shows over it) and check 3.
3. Focus in Terminal (items 1 and 14). Terminal frontmost, type `echo tokitty` without pressing Return, drag the widget by the cat, type ` ok`. The line reads `echo tokitty ok`, Terminal stays frontmost and nothing runs. Do this once before check 2's toggle and once after it.
4. Click-through on the empty margin (item 5). Exit from the menu. In `customization.json` (in the state dir), set `"cat_home": "on_top"` on your account's entry. Relaunch. With Finder behind the widget, click the empty strip above the card beside the cat: Finder takes the click. Clicks on the cat and on the card stay with the widget.
5. The 0% card catches clicks (item 6). Exit. Create `theme.json` in the state dir with `{"card": {"opacity": 0}}`. Relaunch. The card is invisible; a left-drag on a blank spot of it moves the widget, a right-click there opens the menu, and Finder behind it gets nothing. Exit, delete `theme.json`, and put `customization.json` back from `customization.json.bak`.
6. Position (item 7). Relaunch, drag the widget somewhere new, Exit from the menu, relaunch: it comes back in the same place, not 20 pt higher (the #147 fix, never checked on a Mac since). Note the `position.json` contents.
7. Retina (item 8). With the widget showing, run `screencapture -x ~/Downloads/tokitty-117/retina.png` and send me the PNG. I check it against the cat's own raster, the same way I checked Windows at 100% and 150%.
8. Windows in front (item 11). Turn Always in front off. With TextEdit frontmost, open Settings… from the menu-bar cat: Settings comes up in front and takes typing. From Settings open the colour picker (Look ▸ any Change…) and the accounts manager (Accounts ▸ Manage accounts…): each comes up in front. Then with Settings open, toggle Always in front from the menu-bar cat, off and on: Settings stays visible and in front both times. Repeat the three windows once with Always in front on.
9. First run (item 12). Exit. Move the state dir aside: `mv ~/Library/Application\ Support/Tokitty ~/Library/Application\ Support/Tokitty.117bak`. Launch. The walkthrough's Accounts page shows one row for this Mac's Claude Code, labelled "Signed in (Keychain)" or "Signed in (~/.claude)". Go through to Finish: the widget comes up. Exit, then restore: `rm -rf ~/Library/Application\ Support/Tokitty && mv ~/Library/Application\ Support/Tokitty.117bak ~/Library/Application\ Support/Tokitty`.
10. Update from v0.6.2 by hand, then rollback. This runs a separate v0.6.2 copy with a scratch `HOME`, so nothing touches the real state dir, the Keychain entry or the installed app. Usage won't load in it, which is fine. In one Terminal, start a fake GitHub feed for the v99.0.0 rehearsal (a rebuild of this same commit, since the dry-run build ID is not a version), from a clone with this branch checked out:

    ```
    cd ~/path/to/tokitty && git fetch && git checkout release-gate-117
    python3 -m venv /tmp/tk117-feed && /tmp/tk117-feed/bin/pip install -q cryptography
    /tmp/tk117-feed/bin/python - <<'PY'
    import sys, time, pathlib
    sys.path.insert(0, "freeze")
    import verify_update as vu
    d = pathlib.Path.home() / "Downloads/tokitty-117/rehearsal"
    a = d / "tokitty-v99.0.0-macos-arm64.zip"
    certs = vu.make_certificates(pathlib.Path("/tmp/tk117-certs"))
    s = vu.FakeGitHub(certs, "v99.0.0", a.name, a, (d / (a.name + ".sha256")).read_text()).start()
    print("URL", s.base_url, "CA", certs["ca"], flush=True)
    while True:
        time.sleep(5)
        if s.requests: print(s.requests, s.errors, flush=True); s.requests.clear()
    PY
    ```

    In a second Terminal, set up the old copy and launch it against the feed (put the printed port in place of PORT):

    ```
    mkdir -p /tmp/tk117/a /tmp/tk117-home/Library/Application\ Support/Tokitty
    gh release download v0.6.2 -R nickwolf/tokitty -p 'tokitty-v0.6.2-macos-arm64.zip' -D /tmp/tk117
    ditto -x -k /tmp/tk117/tokitty-v0.6.2-macos-arm64.zip /tmp/tk117/a && xattr -dr com.apple.quarantine /tmp/tk117/a/Tokitty.app
    echo '{"onboarding_version": 1, "update_check": false}' > /tmp/tk117-home/Library/Application\ Support/Tokitty/settings.json
    HOME=/tmp/tk117-home TOKITTY_UPDATE_API_URL=https://127.0.0.1:PORT SSL_CERT_FILE=/tmp/tk117-certs/ca.pem /tmp/tk117/a/Tokitty.app/Contents/MacOS/Tokitty
    ```

    In the v0.6.2 widget: Settings ▸ Check now. The dialog says v99.0.0 is available. Install. Pass: the v0.6.2 window goes, the Qt widget and the menu-bar cat come up, About Tokitty says v99.0.0, the feed printed the releases list, `SHA256SUMS` and the zip with no errors, and `/tmp/tk117-home/Library/Application Support/Tokitty/after-update-qt.json` says `"exposed_before_ack": true`. Exit it.

    Rollback: reset and run once more with the test switch that makes the new copy quit before it acknowledges:

    ```
    rm -rf /tmp/tk117/a /tmp/tk117-home && mkdir -p /tmp/tk117/a /tmp/tk117-home/Library/Application\ Support/Tokitty
    ditto -x -k /tmp/tk117/tokitty-v0.6.2-macos-arm64.zip /tmp/tk117/a && xattr -dr com.apple.quarantine /tmp/tk117/a/Tokitty.app
    echo '{"onboarding_version": 1, "update_check": false}' > /tmp/tk117-home/Library/Application\ Support/Tokitty/settings.json
    HOME=/tmp/tk117-home TOKITTY_UPDATE_TEST_NO_ACK=1 TOKITTY_UPDATE_API_URL=https://127.0.0.1:PORT SSL_CERT_FILE=/tmp/tk117-certs/ca.pem /tmp/tk117/a/Tokitty.app/Contents/MacOS/Tokitty
    ```

    Check now ▸ Install again. Pass: a box says "Tokitty v99.0.0 didn't start. Still running v0.6.2.", the v0.6.2 widget is still there, and `/tmp/tk117/a/Tokitty.app` is still v0.6.2: `HOME=/tmp/tk117-home /tmp/tk117/a/Tokitty.app/Contents/MacOS/Tokitty --self-check` reports Tk, not build v99.0.0 with Qt (`Info.plist` can't tell them apart: the rehearsal's also says 0.6.2). Exit, stop the feed with Ctrl+C, `rm -rf /tmp/tk117 /tmp/tk117-home /tmp/tk117-certs /tmp/tk117-feed`.

Afterwards: put back `position.json` and `customization.json` from the `.bak` copies, and launch the installed Tokitty once so the hook command points back at it.

Report: pass or fail for each check, with what you saw for any fail, `retina.png` from check 7, and the `position.json` contents from check 6.

## Results

Run 2026-10-15 on an arm64 Mac (Darwin 25.6.0, built-in display only, 1710×1112 pt at 2x), build from run 38764477381 (3097d7d, build ID `dryrun-3097d7d`). Driven through System Events, posted mouse and key events, and screenshots, with window levels read from `CGWindowListCopyWindowInfo`. The installed v0.6.2 was stopped first and relaunched at the end.

1. Pass. After Ctrl+Right the other Space had no Finder, Chrome, Terminal or TextEdit windows on screen, and the widget was there at level 8. Ctrl+Left brought them back.
2. **Fail on the "off" half.** Right-click ▸ Always in front off (the menu then shows it unchecked) moves the widget from level 8 to level 3 (`NSFloatingWindowLevel`), not 0. With TextEdit frontmost and its window moved over the widget, the widget stays on top. It still did after an app switch (Finder, then TextEdit) and with Finder frontmost, so it never drops to normal level in this build; the source run in comment 3 reached 0 after a switch. Probable cause: Qt gives a `Qt.Tool` window `NSFloatingWindowLevel` and only `WindowStaysOnTopHint` raises it to 8, so clearing the hint leaves 3. A source run is a regular app and deactivation lowered it there; the `LSUIElement` build never goes through that. Setting the level to 0 in `macos.apply` when the hint is off would probably fix it. Turning it back on: level 8 straight away, above TextEdit. After the toggle, check 1 passed again, the widget showed over full-screen TextEdit, and check 3 passed again.
   Side effect of level 3: a click on the wallpaper ("click wallpaper to reveal desktop") sweeps the widget off to (−288, 1081) with the other windows, and the next click brings it back. At level 8 it stays put. Nothing was saved to `position.json`.
3. Pass, before and after check 2's toggle. In a new Terminal window: typed `echo tokitty`, dragged the widget by the cat (it moved 80, −60 pt), typed ` ok`. The line read `% echo tokitty ok` with no output under it, and Terminal stayed frontmost across the drag both times.
4. Pass. With `cat_home: on_top` the window is 300×232 with the cat above the card. A Finder window (Downloads, column view) behind it: clicks at (280, 830) and (330, 800) in the strip beside the cat made Finder frontmost and selected the folder under the click. Clicks on the cat, a blank part of the card and the card's text left TextEdit frontmost.
5. Pass. The card was invisible with only the bars and text drawn. Left-clicks on three blank spots of it left TextEdit frontmost and Finder's selection unchanged. A left-drag from a blank spot moved the widget 100, −50 pt, and a right-click there opened the menu. `theme.json` was deleted and `customization.json` restored afterwards.
6. Pass. Dragged to (700, 495), Exit, relaunch: back at (700, 495), not 20 pt higher. `position.json` was `{"x": 700, "y": 495, "unit": "logical"}` before and after the relaunch.
7. Captured as `~/Downloads/tokitty-117/retina.png` on the Mac (widget at 700, 495 pt, so 1400, 990 px). I also measured it: the cat's box holds 9 flat colours (the card plus 8 cat colours), every count a multiple of 4, and all 2352 colour edges fall on even device pixels.
8. **Pass with Always in front on, fail with it off, same cause as check 2.** Off, TextEdit frontmost: Settings… from the menu-bar cat opened Settings with Tokitty active, and a typed `z` did not reach TextEdit. Settings is level 0 though, so the level-3 widget draws over it. The colour picker (Look ▸ Bar fill ▸ Change…) is the native Colors panel at level 8 and came up above both. Manage accounts… opened Accounts at level 0 with focus in the Budget field, again under the widget. Toggling Always in front from the menu-bar cat with Settings open: on raised Settings to level 8 in front of the widget, off put it back to level 0, still on screen and the frontmost window but under the widget. With Always in front on, all three windows came up at level 8 in front of the widget and above TextEdit, and a typed `z` did not reach TextEdit.
9. Pass. With the state dir moved aside, "Tokitty setup" came up on Welcome. Accounts showed one row, "Claude Code · This Mac · Signed in (Keychain) · /Users/nickwolf_alma/.claude". Live activity showed it as Installed (I didn't press Install). Finish brought up the widget at (1386, 870). The state dir was restored.
   Stale copy on the Done page: "Choose Tokitty ▸ Settings… from the menu bar, or right-click any cat", but there is no Tokitty app menu since #142.
10. Pass, update and rollback. v0.6.2 (Tk, opened with Tokitty ▸ Settings… from its app menu because the right-click menu didn't open from a posted event) ▸ Check now said "Tokitty v0.6.2 is installed. v99.0.0 is available." Install: the Tk window went, one Qt process came up (`--after-update`) with the widget and the menu-bar cat, and About said v99.0.0 with Qt 6.12.0 and PySide6 6.12.0. The feed logged the releases list, then `SHA256SUMS` and the zip, with no errors. `after-update-qt.json` was `{"exposed_before_ack": true, "waited_s": 0.065}`. Rollback with `TOKITTY_UPDATE_TEST_NO_ACK=1`: a box said "Tokitty v99.0.0 didn't start. Still running v0.6.2." and the v0.6.2 widget stayed.
    The rollback step's `defaults read … CFBundleShortVersionString` can't tell the two builds apart: the v99.0.0 rehearsal's `Info.plist` also says 0.6.2 for both version keys. I checked the bundle in other ways. After the update, `--self-check` reported build `v99.0.0` with Qt. After the rollback it reported tkinter and pystray, and `Info.plist` was the original 860-byte file from Oct 12 (the v99.0.0 one is 984 bytes).

Afterwards: `position.json` and `customization.json` restored from the backups (`settings.json` unchanged), and the installed Tokitty relaunched. `current` points at `~/Applications/Tokitty.app` again, and the Claude Code hook goes through `current`.
