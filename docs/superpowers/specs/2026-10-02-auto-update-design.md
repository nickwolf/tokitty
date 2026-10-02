# Auto-update from GitHub releases (#77)

Status: design only, 2026-10-02. Nothing here is built. Decided with Nick the same day: the daily check is on by default and only ever tells you an update exists, installing always takes a click; an available update shows as a menu item plus one tray notification per version; after an update the previous version is kept for rollback and anything older is deleted; the first PR covers Windows, macOS and Linux together, verified on the release CI runners, and adds a `SHA256SUMS` asset to every release.

## What the code already gives us

- Frozen builds know their version. `freeze/tokitty.spec` bakes `TOKITTY_BUILD_ID` into both executables, and the release job sets it to the tag (`v0.2.1`). A source run has `dev`, a CI dry run has `dryrun-<sha>`.
- The hook path doesn't move. `runner_link.ensure_runner_link` repoints `<state dir>/current` at whichever release is running, on every launch, so a new copy that starts once takes over all registered hooks. Open Claude Code sessions keep working and Codex sees a byte-identical command.
- A moved exe doesn't break Start at login. Startup calls `autostart.ensure_current`, which re-registers a stale entry with the running executable (to be confirmed in the plan, see Open questions).
- There is a single-instance lock (`lock.SingleInstanceLock`, taken first thing in `run_gui`). A new copy started while the old one runs prints "Tokitty is already running." and exits, so the swap needs a handover.
- The tray has no notification support yet. pystray's `notify` works on the win32 backend; the Linux build uses the xorg backend and macOS uses darwin, and neither reports `HAS_NOTIFICATION`.
- HTTP is plain `urllib` (`api.py`), with no `certifi` or `truststore` in the bundle.

## Recommendation

- **Check `GET https://api.github.com/repos/nickwolf/tokitty/releases/latest` once a day on a worker thread**, and on demand from a "Check for updates" menu item. That endpoint never returns drafts or prereleases, which matches how releases are published by hand from a draft today.
- **Offer, never install, without a click.** A newer tag adds "Update to vX.Y.Z…" as the first item of both the pane menu and the tray menu, and fires one tray notification for that tag where the tray supports it. The item opens a confirm dialog with Install, Release notes, and Later.
- **Install beside the old copy on Windows and Linux, in place on macOS.** Windows and Linux unpack to `<versions dir>/vX.Y.Z/`, which is the layout releases are kept in by hand now. macOS can't sensibly keep two `Tokitty.app` in Applications, so it renames the running app aside and puts the new one at the same path.
- **Hand over through `--after-update`.** The old copy launches the new one with that flag and quits. The new copy waits up to 30 s for the lock instead of exiting, then starts normally, which repoints `current` and fixes autostart.
- **Verify before swapping anything.** The download must match the release's `SHA256SUMS`, and the unpacked copy must pass `--self-check` with a `build_id` equal to the tag. A failure at any point before the swap leaves nothing changed.
- **A source run only says an update exists.** Its menu item opens the release page in a browser.

## Versions

The running version is `TOKITTY_BUILD_ID` when it matches `^v(\d+)\.(\d+)\.(\d+)$`, compared as an integer triple. The latest version is `tag_name` under the same pattern. A tag that doesn't parse is ignored. A frozen build whose own ID doesn't parse (a CI dry run) is treated like a source run: it can see that a release exists but never installs one. A source run reads its version from package metadata (`importlib.metadata.version("tokitty")`) for the comparison only.

## The check

- Request headers: `Accept: application/vnd.github+json`, `User-Agent: tokitty/<version>`. Timeout 10 s. Unauthenticated, so it is subject to GitHub's 60 requests an hour per IP, which a daily check and occasional manual checks never approach.
- Schedule: 60 s after startup if the last successful check is more than 20 h old or there isn't one, then a Tk `after` tick every hour that checks again once 24 h have passed. The worker thread never touches Tk; it hands its result back through `root.after`, the same way the pollers do.
- The platform asset is `tokitty-<tag>-<target>.<ext>` with target `windows-x64`, `macos-arm64`, `macos-x86_64` (from `platform.machine()`, so an Intel build under Rosetta keeps getting the Intel build) or `linux-x86_64`, `.tar.gz` on Linux and `.zip` elsewhere. A release with no asset for this target is shown as available but can't be installed from the app.
- Setting: `update_check: bool = True` in `settings.json`, shown as a "Check for updates automatically" checkbox. Turning it off stops the scheduled check only; the manual item always works.
- Check state goes in its own `update.json` in the state dir, not in `settings.json`, since it isn't a preference: `last_checked`, `latest_tag`, `notified_tag`, and `previous_release` (written by an install, used for cleanup, below). Robust-loaded like `settings.py`: missing or malformed means empty.
- A failed scheduled check is silent and retried on the next tick. A failed manual check says why in a dialog. A manual check that finds nothing newer says "Tokitty vX.Y.Z is the latest version."

## The notice

- "Update to vX.Y.Z…" is the first menu item, in both menus, whenever `latest_tag` is newer than the running version.
- A tray notification ("Tokitty vX.Y.Z is available", "Right-click Tokitty to update") fires once per tag, recorded in `notified_tag`, and only when the tray is enabled and `HAS_NOTIFICATION` is true. Elsewhere the menu item is the whole notice.
- The confirm dialog shows the current and new version. Release notes opens `html_url` with `webbrowser`. Install shows a progress line while downloading and a Cancel button; Later closes it.

## Installing

All of this runs on a worker thread, reporting progress to the dialog through `root.after`.

1. **Where it goes.** Let `R` be the release folder, `dirname(realpath(sys.executable))`. On Windows and Linux, if `R`'s parent is named `v<semver>`, the versions dir is `R`'s grandparent; otherwise it is `R`'s parent. The new copy's home is `<versions dir>/vX.Y.Z/`, and the archive's own top folder (`Tokitty/` or `tokitty/`) lands inside it, so `releases\v0.2.1\Tokitty\Tokitty.exe` becomes `releases\v0.3.0\Tokitty\Tokitty.exe`. On macOS the target is the `.app` containing the executable, at the same path. The target's parent must be writable; if not, the dialog says so and offers the release page.
2. **Download.** Fetch `SHA256SUMS` first. A release without one can't be installed from the app (the dialog offers the release page), which covers every release before this change. Then stream the archive into a staging folder, `.tokitty-update-<tag>-<pid>` in the versions dir (macOS: next to the `.app`), so the final move is a same-volume rename. Hash while streaming, and require both the asset's `size` and the `SHA256SUMS` line to match. HTTPS only, including after redirects.
3. **Unpack.** Windows: `zipfile`, rejecting any member whose resolved path escapes the staging folder. Linux: `tarfile.extractall(filter="data")`, which keeps the executable bits and the bundle's internal symlinks and refuses anything outside the target. macOS: `ditto -x -k`, the same tool the release job uses, because `zipfile` drops the symlinks and permissions inside `Python.framework` and the result won't launch.
4. **Self-check.** Run the new executable with `--self-check` (60 s timeout) and require `ok` for every check and `build_id` equal to the tag. This is the same gate the release CI applies to every artifact.
5. **Swap.**
   - Windows and Linux: rename the staged top folder to `<versions dir>/vX.Y.Z/<top folder>`. If that path already exists and passes the same self-check (an earlier install that never launched), use it as is; if it exists and fails, stop and say so rather than delete something the updater didn't make. The old copy is untouched.
   - macOS: rename the running `Tokitty.app` to `.Tokitty-<old tag>.app` beside it (hidden in Finder), then rename the staged app to `Tokitty.app`. If the second rename fails, rename the first back. A running app's bundle can be renamed under it; the process keeps its open files. `current` points into `Tokitty.app/Contents/MacOS`, so hooks pick up the new bundle at the moment of the second rename.
6. **Record and hand over.** Write `previous_release` (old tag and old path) to `update.json`, launch the new executable with `--after-update <old tag>` fully detached (Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; Linux: `start_new_session=True`; macOS: `open -n <app> --args --after-update <old tag>`), then quit through the normal quit path, which releases the lock.
7. **Clean up the staging folder** on any failure, and on success once the swap is done.

Cancel during download stops the stream and removes the staging folder. Nothing can be cancelled once the swap starts.

## The new copy

- `--after-update` changes one thing in `run_gui`: the lock is retried for up to 30 s instead of failing at once. Everything after that is the normal startup, so `current` is repointed and autostart is re-registered by the code that already does both.
- Cleanup runs on every launch, on a worker thread, and only when `update.json` has a `previous_release`. It deletes versions strictly older than that one: on Windows and Linux, sibling `v<semver>` folders in the versions dir that contain the expected executable at the expected relative path; on macOS, hidden `.Tokitty-v<semver>.app` siblings. It never deletes the running release, `current`'s target, or the previous release, and it ignores errors (a folder still in use on Windows is retried next launch). It also removes stale `.tokitty-update-*` staging folders. Because it keys on `previous_release`, nothing is deleted on a machine that has never installed an update from the app.
- Rollback is launching the previous copy by hand. It repoints `current` at itself and its own check offers the newer version again. The README says so.

## Release workflow

The `release` job writes `SHA256SUMS` (`sha256sum` over the four archives, file names without paths) and uploads it with them, so the job now expects five assets. The integrity this buys is against a truncated or corrupted download and a tampered asset in transit. It doesn't protect against someone who can publish a release on the repo; with unsigned builds that is the same trust as downloading by hand today, and the README should say so.

## Verifying on CI

A new `freeze/verify_update.py` runs in each `build` matrix job after `verify_artifact.py`, against the extracted artifact, with `TOKITTY_UPDATE_API_URL` pointing the updater at a local HTTP server that serves a fake `releases/latest`, the just-built archive, and its `SHA256SUMS`. It asserts:

- A real HTTPS request to `api.github.com` succeeds from the frozen bundle (hidden `--check-for-update`, printing the parsed result as JSON). This is the TLS question below, answered on every OS.
- The full install path from the local server: checksum match, unpack, self-check, swap, `--after-update` launch, the new copy taking the lock after the old one exits, and `current` repointed.
- The downloaded archive and the unpacked executable carry no `Zone.Identifier` stream (Windows) and no `com.apple.quarantine` attribute (macOS).
- On macOS, the in-place swap of a running app in a writable folder, and that the renamed old bundle keeps running until it quits.
- Cleanup deletes seeded older `v<semver>` folders and keeps the previous one, the running one, and an unrelated folder.
- A wrong `SHA256SUMS` line and a failing self-check each leave the install folder exactly as it was.

The fake release needs a tag newer than the running build, while the self-check needs `build_id` to equal the tag. The plan resolves this, most likely by having the verifier build the "new" side from the same artifact with a different baked ID, or by letting `--self-check` accept an expected ID from the caller in test mode only.

## Open questions to settle in the plan

- **TLS in the frozen macOS build.** python.org's macOS Python finds root certificates through a file installed by "Install Certificates.command", which a PyInstaller bundle doesn't carry. If the usage API already works from the macOS build this is moot; if the CI request above fails, the fix (`truststore` or a bundled `certifi`) also fixes the usage API.
- **SmartScreen and Gatekeeper.** Both only examine files tagged as downloaded, and `urllib` adds no tag, so the issue's worry about them asking again shouldn't apply to an update the app fetched itself. The CI assertion above checks the tag's absence. Smart App Control (Windows 11) checks every executable regardless of tags, but a machine with it on blocks the first manual launch too, so updating changes nothing there.
- **`autostart.ensure_current`** must re-register when the registered command names an executable that differs from the running one. Read it and add a test before relying on it.
- **WSL hook copies.** A Claude home inside WSL runs a copied `hook_writer.py`. Confirm the startup reconcile refreshes that copy when the bundled one changes, otherwise an update leaves WSL hooks on old code.
- **Writable Applications.** An admin user can write to `/Applications` without a prompt; a standard user can't. The plan should confirm what the CI runner's user is and test the not-writable message with a read-only temp folder.

## Out of scope

- Skipping a version, release channels, and prereleases.
- Downgrades. Rollback is by hand, as above.
- Code signing. The checksum is the only integrity check until the builds are signed.
- Updating a source install.
