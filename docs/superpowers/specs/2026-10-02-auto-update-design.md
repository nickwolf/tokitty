# Auto-update from GitHub releases (#77)

Status: design only, 2026-10-02. Nothing here is built. Decided with Nick the same day: the daily check is on by default and only ever tells you an update exists, installing always takes a click; an available update shows as a menu item plus one tray notification per version; after an update the previous version is kept for rollback and anything older that the updater installed is deleted (narrowed after review and confirmed by Nick, see The new copy); the first PR covers Windows, macOS and Linux together, verified on the release CI runners, and adds a `SHA256SUMS` asset to every release.

Revised the same day after an adversarial review (Codex, gpt-6-sol, read-only). The review's main points were that launching the new copy is not the same as the new copy running, that the macOS two-rename swap has a window with no app at all, that cleanup must not infer ownership from a folder name, that `releases/latest` sorts by commit date rather than version, and that a frozen parent leaks its library path into the child it spawns. All five are folded in below.

## What the code already gives us

- Frozen builds know their version. `freeze/tokitty.spec` bakes `TOKITTY_BUILD_ID` into both executables at runtime (it overwrites any inherited value), and the release job sets it to the tag (`v0.2.1`). A source run has `dev`, a CI dry run has `dryrun-<sha>`.
- The hook path doesn't move. `runner_link.ensure_runner_link` repoints `<state dir>/current` at whichever release is running, on every launch, so a new copy that starts once takes over all registered hooks. Open Claude Code sessions keep working and Codex sees a byte-identical command.
- A moved exe doesn't break Start at login. `autostart.ensure_current` runs at startup, and when autostart is registered it re-registers whenever the freshly resolved command differs from the registered one (read 2026-10-02).
- Copied hook writers are refreshed. The startup `hooks_install.ensure_current` reconciles every hook-enabled account in `accounts.json`, and `_reconcile_hooks` copies the bundled `hook_writer.py` over each home's copy, WSL homes included. With no `accounts.json` it does nothing, so a single-account setup that ran `--install-hooks` against the default home keeps its old copy across any update, manual or automatic. That predates this work and is left to a separate issue.
- There is a single-instance lock (`lock.SingleInstanceLock`, taken first thing in `run_gui`). A new copy started while the old one holds it prints "Tokitty is already running." and exits, so the swap needs a handover.
- The tray has no notification support yet. pystray's `notify` works on the win32 backend; the Linux build uses the xorg backend and macOS uses darwin, and neither reports `HAS_NOTIFICATION`.
- HTTP is plain `urllib` (`api.py`), with no `certifi` or `truststore` in the bundle.
- `--self-check` only opens a real Tk window when `TOKITTY_SELF_CHECK_TK=1`, which the release verifier sets.

## Recommendation

- **List the releases once a day on a worker thread and take the highest stable version**, and do the same on demand from a "Check for updates" menu item.
- **Offer, never install, without a click.** A newer version adds "Update to vX.Y.Z…" as the first item of both the pane menu and the tray menu, and fires one tray notification for that version where the tray supports it. The item opens a confirm dialog with Install, Release notes, and Later.
- **Install beside the old copy on Windows and Linux, in place on macOS.** Windows and Linux unpack to `<versions dir>/vX.Y.Z/`, the layout releases are kept in by hand now. macOS can't sensibly keep two `Tokitty.app` in Applications, so it exchanges the new bundle with the old one in a single atomic `renamex_np(RENAME_SWAP)` call.
- **Hand over with an acknowledgement.** The old copy releases its lock, hides itself, launches the new copy, and waits. It only exits once the new copy reports that it holds the lock and has a window on screen. If that report doesn't arrive, the old copy kills the child, undoes the swap on macOS, takes its lock back and says what happened.
- **Verify before swapping.** The download must match the release's `SHA256SUMS`, the unpacked tree must have exactly the expected layout, and the new copy must pass `--self-check`, including the real Tk window check, with a `build_id` equal to the tag.
- **Delete only what the updater installed.** `update.json` records every copy the updater created. Cleanup deletes from that record and nothing else.
- **A source run only says an update exists.** Its menu item opens the release page in a browser.

## Versions

The running version is `TOKITTY_BUILD_ID` when it matches `^v(\d+)\.(\d+)\.(\d+)$`, compared as an integer triple. A frozen build whose own ID doesn't parse (a CI dry run) is treated like a source run: it can see that a release exists but never installs one. A source run reads its version from package metadata (`importlib.metadata.version("tokitty")`) for the comparison only.

## The check

- `GET https://api.github.com/repos/nickwolf/tokitty/releases?per_page=100`, then keep releases that are not drafts or prereleases and whose `tag_name` parses, and take the highest. `releases/latest` isn't used: GitHub picks it by `created_at`, which is the date of the tagged commit rather than of publication, so a fix release tagged on an older commit could hide a newer version. One page is enough for the foreseeable future; if there are ever more than 100 releases, only the newest 100 are considered.
- Request headers: `Accept: application/vnd.github+json`, `User-Agent: tokitty/<version>`. Timeout 10 s. Unauthenticated, so subject to GitHub's 60 requests an hour per IP, which a daily check never approaches.
- The base URL can be overridden with `TOKITTY_UPDATE_API_URL`, for the CI verifier only. It is documented in the README's Configuration section next to the other `TOKITTY_*` variables.
- Schedule: 60 s after startup if the last successful check is more than 20 h old or there isn't one, then a Tk `after` tick every hour that checks again once 24 h have passed. The worker thread never touches Tk, `root.after` included (it is silently dropped from a thread before `mainloop` starts): it publishes into a lock-guarded field that the Tk-thread `tick()` reads, the same way `Poller.get_latest` works.
- The platform asset is `tokitty-<tag>-<target>.<ext>` with target `windows-x64`, `macos-arm64`, `macos-x86_64` (from `platform.machine()`, so an Intel build under Rosetta keeps getting the Intel build) or `linux-x86_64`, `.tar.gz` on Linux and `.zip` elsewhere. A release with no asset for this target, or with no `SHA256SUMS` asset (every release before this change), is shown as available but can't be installed from the app; the dialog offers the release page instead.
- Setting: `update_check: bool = True` in `settings.json`, shown as a "Check for updates automatically" checkbox. Turning it off stops the scheduled check only; the manual item always works.
- Check state goes in its own `update.json` in the state dir, not in `settings.json`, since it isn't a preference: `last_checked`, `latest_tag`, `notified_tag`, `owned` (below) and `pending` (below). Robust-loaded like `settings.py`, written atomically (temp file then `os.replace`).
- A failed scheduled check is silent and retried on the next tick. A failed manual check says why in a dialog. A manual check that finds nothing newer says "Tokitty vX.Y.Z is the latest version."

## The notice

- "Update to vX.Y.Z…" is the first menu item, in both menus, whenever `latest_tag` is newer than the running version.
- A tray notification ("Tokitty vX.Y.Z is available", "Right-click Tokitty to update") fires once per tag, recorded in `notified_tag`, and only when the tray is enabled and `HAS_NOTIFICATION` is true. Elsewhere the menu item is the whole notice.
- The confirm dialog shows the current and new version. Release notes opens `html_url` with `webbrowser`. Install shows a progress line and a Cancel button while downloading; Later closes it.

## Installing

Steps 1 to 4 run on a worker thread, publishing progress into the same kind of lock-guarded field for `tick()` to show, and change nothing outside a staging folder. Cancel or any failure in them removes the staging folder and leaves the install exactly as it was.

1. **Where it goes.** Let `R` be the release folder, `dirname(realpath(sys.executable))`.
   - Windows and Linux: if `R`'s parent is named `v<semver>`, the versions dir is `R`'s grandparent; otherwise it is `R`'s parent. The new copy's home is `<versions dir>/vX.Y.Z/`, with the archive's top folder (`Tokitty/` or `tokitty/`) inside it, so `releases\v0.2.1\Tokitty\Tokitty.exe` becomes `releases\v0.3.0\Tokitty\Tokitty.exe`.
   - macOS: the target is the `.app` containing the executable. If that bundle isn't named `Tokitty.app` (a rollback copy launched by hand, or a renamed app), the dialog offers the release page instead of installing.
   - The target's parent must actually be writable, tested by creating and removing a probe file rather than trusted from group membership. If it isn't, the dialog says so and offers the release page. This is the expected path for a standard macOS user whose app lives in `/Applications`.
2. **Download.** Fetch `SHA256SUMS`, then stream the archive into `.tokitty-update-<tag>-<pid>/` beside the target (so every later move is a same-volume rename). Hash while streaming and require both the asset's `size` and its `SHA256SUMS` line to match. HTTPS only, including after redirects. Record the staging folder in `update.json` `owned` before writing to it, so a crash mid-download leaves a folder cleanup is allowed to remove.
3. **Unpack and check the layout.** Windows: `zipfile`, rejecting any member whose resolved path escapes the staging folder. Linux: `tarfile.extractall(filter="data")`, which keeps executable bits and the bundle's internal symlinks. macOS: `ditto -x -k`, the tool the release job uses, because `zipfile` drops the symlinks and permissions inside `Python.framework`. Then validate the extracted tree before anything else touches it: exactly one top-level entry with the expected name, the main executable and `tokitty-hook` at their expected relative paths, no symlink or junction at all on Windows, and on Linux and macOS no symlink that resolves outside the tree.
4. **Self-check.** Run the new executable with `--self-check` and `TOKITTY_SELF_CHECK_TK=1` (60 s timeout) in a clean child environment (below), and require `ok` for every check and `build_id` equal to the tag.
5. **Hand over.** Back on the Tk thread:
   1. Write `pending` to `update.json`: old and new version, old and new path, a random token, and on macOS the staging path the swap will use.
   2. Windows and Linux: rename the staged top folder to `<versions dir>/vX.Y.Z/<top folder>` and add that path to `owned`. If the path already exists and is itself in `owned` and passes the same layout check and self-check (an earlier install that never took over), use it; otherwise stop and say so rather than delete something the updater didn't make.
   3. Stop the tray icon, withdraw the windows, and release the single-instance lock.
   4. macOS: `renamex_np(staged, app, RENAME_SWAP)` through `ctypes`. After it returns, `Tokitty.app` is the new bundle and the staging path holds the old one, which is still running. A crash at any instant leaves a complete bundle at `Tokitty.app`. Then rename the staging path to `.Tokitty-<old tag>.app` (hidden in Finder) and add it to `owned`. `current` points into `Tokitty.app/Contents/MacOS`, so hooks follow the new bundle from the moment of the swap.
   5. Launch the new executable with `--after-update <token>`, detached (Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; Linux and macOS: `start_new_session=True`), in the clean child environment. macOS launches the inner executable directly, as the release verifier does, rather than through `open`.
   6. Wait up to 120 s for `<state dir>/update-ack-<token>`. The limit is generous so a SmartScreen prompt on the new exe can be answered in time.
   7. Ack received: clear `pending` and exit.
   8. No ack: kill the child if it is still alive, swap back on macOS (the same `RENAME_SWAP` between `Tokitty.app` and the hidden copy), take the lock back, call `ensure_runner_link` and `autostart.ensure_current` again so `current` and the login item point at the old copy, restore the windows and tray, clear `pending`, and say "Tokitty vX.Y.Z didn't start. Still running vA.B.C."

Between steps 5.4 and 5.8 the old process is running from a renamed bundle on macOS, and on every OS has no windows. It must not import anything or open any new Tk widget in that stretch, since modules and Tcl scripts not loaded yet would be read from a path that has moved. Everything step 5 needs is imported before step 5.1.

### The clean child environment

Every child the updater starts (the self-check, the new copy, `ditto`) gets a copy of `os.environ` with `PYINSTALLER_RESET_ENVIRONMENT=1` set, so a frozen child treats itself as a fresh top-level process instead of a subprocess of the old bundle, and on Linux with `LD_LIBRARY_PATH` restored from `LD_LIBRARY_PATH_ORIG` (or removed when that is unset), so the new copy doesn't load the old bundle's libraries. `TOKITTY_SELF_CHECK_TK` is passed to the self-check only. The plan confirms which other variables the built bootloader sets (`_PYI_*`, Tcl/Tk library paths) by dumping the environment from inside a frozen build on each runner.

## The new copy

- `--after-update <token>` changes two things in `run_gui`. The lock is retried for up to 30 s instead of failing at once. Once the lock is held and the first window is mapped (an `after(0)` callback inside the mainloop), it writes `<state dir>/update-ack-<token>`. Everything else is the normal startup, so `current` is repointed and autostart re-registered by code that already does both. If it can't get the lock in 30 s it exits quietly.
- Any launch that finds a `pending` in `update.json` older than 5 minutes (the old process died mid-handover) clears it. On macOS `Tokitty.app` is complete either way thanks to the atomic swap, so there is nothing to restore; whichever bundle is there is the one that runs.
- Cleanup runs on a worker thread after startup, on every launch. It considers only paths in `owned`, and for each one: skips it if it is the running release, `current`'s target, or the copy this one replaced (the newest owned version below the running one); deletes it if it is a staging folder not named in a live `pending`, or a copy whose version is lower than the replaced one. Before deleting it re-validates the path (still a real directory, not a link or junction, still inside the versions dir or beside the app), renames it to `.tokitty-trash-<name>` and then removes the renamed folder. On Windows a folder with a file still in use fails the rename and is left whole for the next launch, rather than half-deleted by `rmtree`. Successful deletes are removed from `owned`.
- So a copy you unpacked by hand is never deleted, even if it sits in the same versions dir with a `v` name. On Nick's machine `releases\v0.1.0`, `v0.2.0` and `v0.2.1` stay until deleted by hand; only copies the updater fetched get rotated.
- Rollback on Windows and Linux is launching the previous copy by hand. It repoints `current` at itself and its own check offers the newer version again. On macOS the README gives the two `mv` commands that put the hidden `.Tokitty-<old tag>.app` back at `Tokitty.app`, since a hidden bundle can't be opened from Finder and the updater refuses to run from a bundle with any other name.

## Release workflow

Each `build` job writes a one-line `<archive>.sha256` for its own archive, in `sha256sum` format, and uploads it with the archive. The `release` job concatenates the four into `SHA256SUMS`, re-verifies every archive against it with `sha256sum -c`, and uploads it with them, so the job now expects five assets. The integrity this buys is against a truncated or corrupted download and a tampered asset in transit. It doesn't protect against someone who can publish a release on the repo; with unsigned builds that is the same trust as downloading by hand today, and the README says so.

## Verifying on CI

A new `freeze/verify_update.py` runs in each `build` matrix job after `verify_artifact.py`. On a tag build the new side is the exact archive the job is about to upload, with its `.sha256` line. The old side is a second PyInstaller build of the same commit with `TOKITTY_BUILD_ID=v0.0.1`, unpacked into a temp versions dir (macOS: as `Tokitty.app` in a temp folder). A local HTTP server serves a fake releases list naming the real tag, the real archive, and its checksum, and the old copy is pointed at it with `TOKITTY_UPDATE_API_URL`. On a `workflow_dispatch` dry run, whose build ID doesn't parse, the main artifact gets `TOKITTY_BUILD_ID=v0.0.2` for this step only, and the summary marks it as a rehearsal.

The verifier drives the old copy through a hidden `--apply-update` entry that runs the same code as the Install button, with the same Tk handover, and asserts:

- The old copy's real HTTPS request to `api.github.com` succeeds from the frozen bundle (hidden `--check-for-update`, printing the parsed result as JSON). This answers the TLS question below on all four runners.
- The new copy wrote the ack, holds the lock, and is the only Tokitty process left; the old one has exited; `current` resolves into the new copy; the new copy's `--self-check` reports the real tag.
- The downloaded archive and the unpacked executable carry no `Zone.Identifier` stream on Windows and no `com.apple.quarantine` attribute on macOS.
- On macOS, `Tokitty.app` is the new bundle and `.Tokitty-v0.0.1.app` exists and is in `owned`.
- A new copy built to exit without acking (a test-only `TOKITTY_UPDATE_TEST_NO_ACK=1`, honoured only alongside `TOKITTY_UPDATE_API_URL`) leaves the old copy running with the lock, `current` pointing back at it, and on macOS `Tokitty.app` restored to the old bundle.
- A wrong checksum line and an archive with an extra top-level entry each leave the versions dir exactly as it was.
- Cleanup, seeded with an owned older copy, an owned stale staging folder, an unowned `v0.0.0` folder and a junction or symlink named like a version, deletes the first two and touches neither of the others.

## Open questions to settle in the plan

- **TLS in the frozen macOS build.** python.org's macOS Python finds root certificates through a file installed by "Install Certificates.command", which a PyInstaller bundle doesn't carry. If the usage API already works from the macOS build this is moot. If the CI request fails, the fix (`truststore` or a bundled `certifi`) also fixes the usage API.
- **SmartScreen.** An update fetched by `urllib` has no `Zone.Identifier` stream, so the usual "downloaded from the internet" prompt shouldn't appear, but Microsoft doesn't promise that SmartScreen and Smart App Control ignore an untagged unsigned exe, and each release has a new hash with no reputation. The design doesn't depend on it: a prompt delays the ack, a block means no ack and the old copy keeps running. CI can only show the tag is absent.
- **Gatekeeper.** An app that downloads and unpacks files itself doesn't get them quarantined unless it opts in, and Tokitty doesn't, so translocation shouldn't apply to the swapped bundle. The CI assertion covers the runners, not every Mac, and the failed-ack path covers the rest.
- **`renamex_np` with `RENAME_SWAP` on the runner's filesystem.** APFS supports it. The plan confirms it from Python `ctypes` on both macOS runners, and falls back to the release page (no swap) when the call fails with `ENOTSUP`, for example on a non-APFS volume.
- **What the running macOS process still reads from its bundle** after the swap, measured on the runners with the old copy idling through a full 120 s no-ack wait and then restoring its windows.

## Out of scope

- Skipping a version, release channels, and prereleases.
- Downgrades. Rollback is by hand, as above.
- Code signing. The checksum is the only integrity check until the builds are signed.
- Updating a source install.
- Refreshing the copied hook writer when there is no `accounts.json` (see above), which affects manual updates too.
