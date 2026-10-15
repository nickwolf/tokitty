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

    Check now ▸ Install again. Pass: a box says "Tokitty v99.0.0 didn't start. Still running v0.6.2.", the v0.6.2 widget is still there, and `/tmp/tk117/a/Tokitty.app` is still v0.6.2 (`defaults read /tmp/tk117/a/Tokitty.app/Contents/Info CFBundleShortVersionString`). Exit, stop the feed with Ctrl+C, `rm -rf /tmp/tk117 /tmp/tk117-home /tmp/tk117-certs /tmp/tk117-feed`.

Afterwards: put back `position.json` and `customization.json` from the `.bak` copies, and launch the installed Tokitty once so the hook command points back at it.

Report: pass or fail for each check, with what you saw for any fail, `retina.png` from check 7, and the `position.json` contents from check 6.
