# Auto-update implementation plan (#77)

**Goal:** Tokitty checks GitHub for a newer release once a day, offers it from the menu and a tray notification, and on a click downloads, verifies, installs and hands over to the new copy, keeping the previous copy for rollback.

**Design:** `docs/superpowers/specs/2026-10-02-auto-update-design.md`, revised after a Codex review and confirmed by Nick on 2026-10-02. Read it in full before any task. Where this plan and the spec disagree, stop and ask; don't pick one.

**Branch:** `auto-update` from `origin/main` at c1a816d, worktree `.worktrees/auto-update`. Merge back with `git merge --no-ff`, never squash.

## Global constraints

- Baseline on the branch: `/usr/bin/python3 -m pytest -q` gives 1724 passed, 6 skipped, 61 deselected, and `xvfb-run -a /usr/bin/python3 -m pytest -q -m gui` gives 60 passed, 1 skipped. Every task ends with both of those and `ruff check .` all clean. Use `/usr/bin/python3`, not bare `python3` (see `CLAUDE.local.md`). The GUI suite has a known intermittent flake from 2 s `_pump_until` timeouts: re-run a failing GUI test alone before treating it as real.
- One commit per task, staged by explicit path (never `git add -A`: the sandbox shows phantom files). Commit messages follow the repo's style (imperative, sentence case, no prefix). No em-dashes anywhere, no AI attribution lines, no hard-wrapped prose in docs.
- Tests use `tmp_path` only and never touch the network. HTTP is faked by monkeypatching the module's own `urlopen` seam. Never touch a real state dir, `~/.claude`, `~/.codex`, `%LOCALAPPDATA%\Tokitty`, or anything under `releases/`.
- Code that only runs on one OS (`renamex_np`, `DETACHED_PROCESS`, `ditto`, `Zone.Identifier`) is written behind a small injectable seam so its callers are tested on Linux with fakes. The real call is exercised on CI by Task 6 and nowhere else. A unit test may use `pytest.mark.skipif(sys.platform != "darwin")` for a real `renamex_np` call, which then only runs in the CI test matrix on macOS.
- Background threads never call into Tk, `root.after` included. They publish into a lock-guarded field that `tick()` in `__main__.run_gui` reads, the pattern of `Poller.get_latest` (`poller.py:64`, `:78`).
- Comment density matches the surrounding file. Module docstrings are a few lines; the long reasoning lives in the spec.
- New JSON state is written with the `codex_trust._write_record` shape (`mkstemp` in the same dir, `os.replace`, unlink on failure). Extract it into a shared helper only if a second caller needs exactly that; don't refactor the existing copy-pasted writers.

## Seams in the merged code

From a read of the branch on 2026-10-02. Line numbers drift; search by name.

- `__main__.run_gui()`: lock at `SingleInstanceLock(state_dir)` / `lock.acquire()`, `settings = load_settings(state_dir)`, `root = tk.Tk()`, one `TokittyWindow(root, state_dir, ...)` holding every pane, `run_discovery` on a daemon thread, `tray = TrayManager(root, lambda: window.build_menu_model(0), state_dir, ...)`, `window.on_quit = lambda: (tray.stop(), root.destroy())`, `window.on_menu_action_done = tray.refresh`, then pollers, watchers, `tray.start()`, `root.after(UI_REFRESH_MS, tick)`, `root.mainloop()`, and a `finally:` that stops everything and calls `lock.release()`. `tick()` is the Tk-thread consumer and re-schedules itself.
- `__main__.main(argv)`: a linear chain of `if "--flag" in argv:` blocks with lazy imports, ending in `return run_gui()`. No flag takes a value yet. `run_gui` takes no arguments. Test model: `tests/test_main.py` `test_main_dispatches_self_check`.
- `menu.MenuItem(label, action, submenu, separator, checkbox, radio_selected, enabled)` and `menu.build_menu(*, ...)`, keyword-only, optional items gated on `None` (`if on_set_budget is not None:`, getter plus toggle for checkboxes as with `autostart_enabled` / `on_toggle_autostart`). Ends with a separator and `Exit`.
- `ui.TokittyWindow`: callbacks are plain attributes set in `__init__` (`on_set_budget`, `on_toggle_autostart`, `autostart_enabled`, ...), mapped into `build_menu` by `build_menu_model(pane_index)`. Tk-only dynamic items are inserted in `_rebuild_context_menu` (the Stream Dock item is the model).
- `tray.TrayManager(root, menu_provider, state_dir, ..., icon_factory=None, image_factory=None)`: `start`, `refresh` (`icon.update_menu()`), `stop`, `set_enabled`, `available`, the icon in `self._icon`. Tray menu getters run on pystray's thread, so they read plain Python state. No `notify` today. Tests: `tests/test_tray.py` `FakeRoot`, `_managers`.
- Dialogs: lazy `from tkinter import messagebox, simpledialog` inside the handler (so `--debug-print` works without Tk), `parent=root`, and `root.after(0, ...)` when raised from `tick()`. `_open_customize_dialog` (ui.py) and `accounts_ui.AccountsManager.open` are the `tk.Toplevel` examples.
- `settings.Settings` (frozen dataclass), `load_settings` validating each field independently (`if not isinstance(x, bool): x = default`), `save_settings`, `update_settings(state_dir, **changes)`.
- `runner_link.ensure_runner_link(state_dir)`, `autostart.ensure_current(state_dir, backend)` and `autostart.get_backend()`.
- `frozen.self_check()` and its JSON report (`checks`, `frozen`, `executable`, `build_id`, `state_dir`); `TOKITTY_SELF_CHECK_TK=1` adds `tk_root`.
- `freeze/verify_artifact.py`: `STEPS` list of `(name, fn)`, each `step_x(ctx) -> {"ok", "detail"}`, `_locate_binaries(app_dir)` per OS, `run_child(argv, env, ...)`, `_scratch_env(work)`, atomic `write_report`. `freeze/tokitty.spec` writes `rthook_build_id.py` that sets `os.environ['TOKITTY_BUILD_ID']` for both executables.
- Tests: flat `tests/test_<module>.py`, function tests, `conftest.py` autouse fixtures (`_no_real_autostart_registration`, `_no_real_state_dir_for_hooks`, `_clean_up_gui_root` for gui tests). GUI tests: `@pytest.mark.gui`, `monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)`, `main_module.get_state_dir` patched to `tmp_path`. `_pump_until` lives in `tests/test_accounts_ui.py`; copy it rather than move it.

## Changes from the spec

Record any here, with the reason, as tasks find them.

- The ack wait aborts as soon as the new copy exits without acking, instead of always waiting 120 s (orchestrator, after Task 4). A crashed new copy then rolls back at once, and the CI no-ack step stays fast.
- `--apply-update` exits after a rollback, so the CI no-ack step checks `current` and the restored bundle rather than a lock held by a process that has gone.
- The CI verifier's local server speaks HTTPS with a throwaway certificate (Task 6), because the updater refuses plain HTTP and a test-only HTTP exception would be untested shipped code.

## Task 1: Version, release selection and update state

**Files:** `tokitty/updater.py` (new), `tokitty/settings.py`, `tokitty/__main__.py` (one flag), `tests/test_updater.py` (new), `tests/test_settings.py`, `tests/test_main.py`.

**Acceptance criteria:**
- `parse_version(s) -> Optional[tuple[int, int, int]]` for `^v(\d+)\.(\d+)\.(\d+)$` only.
- `running_version() -> RunningVersion(version, installable)`: frozen with a parsing `TOKITTY_BUILD_ID` is installable; frozen with any other ID, or a source run (version from `importlib.metadata.version("tokitty")`, prefixed `v`), is not. Both inputs injectable for tests.
- `platform_target(sys_platform, machine) -> Optional[str]` giving `windows-x64`, `macos-arm64`, `macos-x86_64`, `linux-x86_64`, else `None`; `asset_name(tag, target)` with `.tar.gz` for Linux and `.zip` elsewhere.
- `fetch_releases(*, urlopen=..., base_url=None)`: `GET {base}/repos/nickwolf/tokitty/releases?per_page=100`, base from `TOKITTY_UPDATE_API_URL` or `https://api.github.com`, headers `Accept: application/vnd.github+json` and `User-Agent: tokitty/<version>`, 10 s timeout. Raises a single `UpdateCheckError` with a readable message for HTTP, network, and JSON failures.
- `select_latest(releases, target) -> Optional[Release]`: ignores drafts, prereleases and unparseable tags, takes the highest version, and reports `asset_url`, `asset_size`, `sums_url` (asset named exactly `SHA256SUMS`) and `html_url`, each `None` when absent. `Release.installable_from_app` is true only when both the platform asset and `SHA256SUMS` exist.
- `update.json` (`UPDATE_FILENAME`) with `load_update_state` / `save_update_state`: fields `last_checked` (ISO UTC string or null), `latest_tag`, `notified_tag`, `owned` (list of `{"path", "version", "kind"}`, kind `"copy"`, `"staging"` or `"backup"`), `pending` (dict or null). Robust-loaded: a missing, unparseable or wrong-shape file or field degrades to defaults field by field. Atomic write per Global constraints.
- `Settings.update_check: bool = True`, validated like `surprise_me`.
- Hidden `--check-for-update` in `main`: fetches, selects, prints one JSON object (`running`, `installable`, `latest`, `newer`, `installable_from_app`, `error`) and returns 0 on a successful fetch, 1 otherwise. No Tk import.

**Tests:** version parsing edge cases (`v1.2`, `1.2.3`, `v01.2.3` accepted as 1,2,3 or rejected, pick one and test it, `v1.2.3-rc1` rejected); selection picks `v0.10.0` over `v0.9.9` and ignores a draft and a prerelease with higher versions; `created_at` order is irrelevant; missing `SHA256SUMS` makes it not installable; an Intel-under-Rosetta machine string maps to `macos-x86_64`; `TOKITTY_UPDATE_API_URL` changes the URL; each failure kind raises `UpdateCheckError`; `update.json` round-trips and degrades per field; the settings field defaults and rejects a non-bool; `main(["--check-for-update"])` prints the JSON with a faked fetch.

## Task 2: Download, unpack, layout check and self-check

**Files:** `tokitty/updater.py` (or `tokitty/update_install.py` if `updater.py` passes about 400 lines), tests in the matching test file.

**Acceptance criteria:**
- `install_target(executable, sys_platform) -> Target`: on Windows and Linux the versions dir rule from the spec (grandparent when the release folder's parent is `v<semver>`, else parent), the top folder name (`Tokitty` or `tokitty`) and the final path `<versions dir>/vX.Y.Z/<top>`; on macOS the containing `.app`, with `Target.refusal` set when it isn't named `Tokitty.app`. Works from `realpath`, so a launch through `current` resolves to the real release.
- `probe_writable(dir) -> bool` by creating and deleting a uniquely named file.
- `child_env(base_env, sys_platform, *, self_check=False) -> dict`: sets `PYINSTALLER_RESET_ENVIRONMENT=1`, on Linux restores `LD_LIBRARY_PATH` from `LD_LIBRARY_PATH_ORIG` or removes it, sets `TOKITTY_SELF_CHECK_TK=1` only when `self_check`, and otherwise passes the environment through unchanged.
- `download(url, dest, *, expected_size, expected_sha256, progress, cancelled, urlopen=...)`: streams in chunks, hashes as it goes, calls `progress(done, total)`, stops and raises `UpdateCancelled` when `cancelled()` is true, refuses a non-HTTPS URL and a redirect to one (check `response.geturl()`), and raises `UpdateInstallError` on a size or hash mismatch. `parse_sums(text) -> dict[name, sha256]` for `sha256sum` format, including the `*name` binary marker.
- `stage(...)`: records the staging folder (`.tokitty-update-<tag>-<pid>` beside the target) in `owned` as `"staging"` before creating it, downloads into it, unpacks (Windows `zipfile` with a containment check per member; Linux `tarfile.extractall(filter="data")`; macOS `ditto -x -k` through an injectable runner), then `validate_layout(root, sys_platform)`: exactly one top-level entry with the expected name, the GUI executable and `tokitty-hook` at their `_locate_binaries` paths, no symlink or junction anywhere on Windows, and on POSIX no symlink resolving outside the tree.
- `run_self_check(exe, tag, *, runner=subprocess.run)`: 60 s timeout, `child_env(..., self_check=True)`, parses the JSON, and requires every check ok and `build_id == tag`.
- Any failure or cancel removes the staging folder and its `owned` entry and leaves everything else untouched.

**Tests:** the target rule for `releases/v0.2.1/Tokitty/Tokitty.exe`, `Downloads/Tokitty/Tokitty.exe`, a launch through a `current` symlink, and a macOS rollback-named bundle; `child_env` on Linux with and without `LD_LIBRARY_PATH_ORIG`; download size mismatch, hash mismatch, cancel, `http://` redirect; zip with `../escape`, zip with two top-level folders, tar with an absolute symlink, a missing `tokitty-hook`, and a good archive of each kind built in `tmp_path`; self-check with a wrong `build_id`, a failing check, a timeout, and success; failure cleanup leaves `tmp_path` as it was apart from `update.json`.

## Task 3: Swap, handover primitives and cleanup

**Files:** `tokitty/updater.py` (or a new `tokitty/update_swap.py`), `tokitty/lock.py` only if a retrying acquire belongs there, tests.

**Acceptance criteria:**
- `promote(staged_root, target, ...)` for Windows and Linux: renames into `<versions dir>/vX.Y.Z/<top>`, adds it to `owned` as `"copy"`. An existing final path is reused only when it is in `owned` as a copy and passes `validate_layout` plus `run_self_check`; otherwise `UpdateInstallError` and nothing is removed.
- `swap_bundles(a, b)` for macOS: `renamex_np(a, b, RENAME_SWAP)` through `ctypes` (`RENAME_SWAP = 0x2`), raising `OSError` with errno on failure, `ENOTSUP` mapped to an `UpdateInstallError` that tells the caller to offer the release page. Then `mac_swap_in(staged_app, app, old_tag)` swaps, renames the staging path to `.Tokitty-<old tag>.app`, records it as `"backup"`, and `mac_swap_back(app, backup)` reverses it. Injectable for tests; one darwin-only test calls the real function on two temp directories.
- `write_pending` / `clear_pending` for the spec's `pending` record (old and new version, old and new path, token, staging or backup path, `started` timestamp), and `stale_pending(state, now)` for older than 5 minutes.
- `launch_detached(argv, env, sys_platform, *, popen=subprocess.Popen) -> Popen`: Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` and `close_fds=True`; elsewhere `start_new_session=True`; stdin, stdout and stderr to `DEVNULL`.
- `ack_path(state_dir, token)`, `write_ack`, `wait_for_ack(path, timeout, *, poll, sleep)`.
- `acquire_with_retry(lock, timeout)`.
- `cleanup(state_dir, *, running_release, current_target, now)`: implements the spec's "The new copy" rules exactly. Only `owned` entries are considered; the running release, `current`'s target and the replaced copy (the newest owned copy or backup below the running version) are kept; staging folders not named in a live `pending` and copies or backups older than the replaced one are deleted; each is re-validated (real directory, not a link or junction, still where it was recorded) and renamed to `.tokitty-trash-<name>` before `rmtree`; a failed rename leaves it whole and keeps its entry; successful deletes leave `owned`. Leftover `.tokitty-trash-*` folders beside owned paths are removed too.

**Tests:** promote into a fresh path, into an owned valid path, into an unowned existing path (refused); the macOS swap sequence with a fake `swap_bundles`, including swap-back; `launch_detached` flags per platform with a fake `Popen`; ack timeout and success with a fake clock; cleanup with the spec's CI seed set (owned older copy, owned stale staging, unowned `v0.0.0`, a symlink named like a version, the replaced copy, the running copy, `current`'s target), plus a rename that raises `PermissionError`.

## Task 4: Install flow and new-copy startup in `run_gui`

**Files:** `tokitty/__main__.py`, `tokitty/updater.py` (an `UpdateController` or similar if it keeps `__main__` readable), `tests/test_main.py`, `tests/test_updater.py`.

**Acceptance criteria:**
- `main` passes `--after-update <token>` through to `run_gui(after_update_token=...)`. With a token, the lock uses `acquire_with_retry(lock, 30)` and a failure exits quietly with 1. After the first window is mapped (an `after(0)` callback scheduled just before `mainloop`), it calls `write_ack`. A `pending` older than 5 minutes is cleared at startup on every launch.
- Cleanup runs on every launch on a daemon thread after startup, with `running_release` from `realpath(sys.executable)` and `current_target` from `realpath(<state dir>/current)`. Not in source runs.
- The install flow, driven from the Tk thread: a worker thread runs Task 2's `stage` and `run_self_check` and publishes progress and the result; `tick()` reads them. On success, everything the handover needs is imported first, then on the Tk thread: `write_pending`, promote (Windows and Linux), `tray.stop()`, withdraw the root and content windows, `lock.release()`, `mac_swap_in` (macOS), `launch_detached([new_exe, "--after-update", token], child_env(...))`, then `wait_for_ack` on a worker thread with `tick()` polling it. On ack: `clear_pending` and `root.destroy()`, so the normal `finally` runs (its `lock.release()` must tolerate an already released lock). On timeout: kill the child if alive, `mac_swap_back` on macOS, re-acquire the lock with retry, `ensure_runner_link`, `autostart.ensure_current`, restore windows and tray, `clear_pending`, and show "Tokitty vX.Y.Z didn't start. Still running vA.B.C." Nothing is imported between the swap and the ack.
- A test-only `TOKITTY_UPDATE_TEST_NO_ACK=1`, honoured only when `TOKITTY_UPDATE_API_URL` is also set, makes a new copy started with `--after-update` exit 0 before writing the ack.
- A hidden `--apply-update` in `main` starts the GUI and immediately runs the same install flow against the newest release from the API, with no dialog, for Task 6's verifier. It exits 0 once the old copy has handed over, 2 on a clean no-ack rollback, 1 on any other failure.

**Tests:** GUI tests with `mainloop` patched out, a faked updater seam, and `tmp_path` state: `--after-update` waits for a held lock and writes the ack; the ack is not written when the lock can't be had; stale `pending` is cleared; the handover order (pending, lock release, launch, wait) with fakes recording calls; ack leads to destroy; timeout leads to the rollback sequence and the message; `TOKITTY_UPDATE_TEST_NO_ACK` is ignored without `TOKITTY_UPDATE_API_URL`; `main` dispatches `--apply-update` and `--after-update <token>`.

## Task 5: Check scheduling, menu, tray notification and dialog

**Files:** `tokitty/menu.py`, `tokitty/ui.py`, `tokitty/tray.py`, `tokitty/__main__.py`, a dialog in `tokitty/update_dialog.py` (new) if it is more than a few lines, tests for each.

**Acceptance criteria:**
- `build_menu` gains `update_available_label` plus `on_install_update` (first item, before everything else, only when both are set), `on_check_updates` ("Check for updates"), and `update_check_enabled` plus `on_toggle_update_check` ("Check for updates automatically", a checkbox). Placed above the separator before Exit. `TokittyWindow` gets matching attributes and `build_menu_model` passes them, so the tray menu has the same items.
- Scheduling from `tick()`: 60 s after startup if `last_checked` is missing or older than 20 h, and then whenever 24 h have passed, but only while `settings.update_check` is true. Each check runs on a daemon thread and publishes its result. A successful check writes `last_checked` and `latest_tag`; a failed scheduled check writes nothing and is silent.
- When a newer `latest_tag` is known: the menu item reads "Update to vX.Y.Z…"; `tray.refresh()` is called when it first appears; `TrayManager.notify(title, message)` (new, a no-op when the icon isn't running or `HAS_NOTIFICATION` is false, exceptions swallowed like `refresh`) fires once per tag and records `notified_tag`, only when the tray is enabled.
- Manual "Check for updates" always runs, reporting the result in a messagebox: the error, "Tokitty vX.Y.Z is the latest version.", or the confirm dialog.
- The confirm dialog (a `Toplevel`, one at a time) shows the running and new version and has Install, Release notes (`webbrowser.open(html_url)`), and Later. Install is replaced by a progress line and Cancel while staging runs. For a source run, a non-installable build, a release without the asset or `SHA256SUMS`, a target refusal, or an unwritable target, Install is replaced by "Open release page" plus the one-line reason.
- Toggling the checkbox saves `update_check` through `update_settings`.

**Tests:** menu item presence and order for each combination, including absent seams; tray `notify` no-op and call paths with the `Mock` icon; scheduling with a fake clock (no check before 60 s, none when disabled, one per 24 h, none on a fresh `last_checked`); `notified_tag` prevents a second notification; dialog button sets for installable versus each refusal reason (GUI test).

## Task 6: Release workflow and CI verification

**Files:** `.github/workflows/release.yml`, `freeze/verify_update.py` (new), `freeze/tokitty.spec` only if a build ID needs passing differently, `tests/test_verify_update.py` for its pure helpers.

**Acceptance criteria:**
- Each `build` job writes `<archive>.sha256` in `sha256sum` format (file name only, no path) and uploads it with the archive. The `release` job checks for four archives and four `.sha256` files, concatenates them into `SHA256SUMS`, runs `sha256sum -c SHA256SUMS` from the archives' directory, and uploads five assets.
- Each `build` job, after `verify_artifact.py`, builds an old fixture of the same commit with `TOKITTY_BUILD_ID=v0.0.1` (distpath and workpath of its own), and runs `freeze/verify_update.py --old-app-dir ... --new-archive ... --new-sums ... --tag ... --work ... --report ...`. On a tag build `--tag` is the real tag and `--new-archive` is the exact archive being uploaded. On `workflow_dispatch` the main artifact is built with `TOKITTY_BUILD_ID=v0.0.2` for a second time for this step only, and the summary marks the result as a rehearsal. Linux runs under `xvfb-run -a`.
- `verify_update.py` serves a fake releases list, the archive and `SHA256SUMS` from a local HTTP server, and runs the old copy with `TOKITTY_UPDATE_API_URL` and a scratch state dir. Because the updater refuses non-HTTPS, the verifier serves HTTPS with a throwaway self-signed certificate and passes its CA to the child through `SSL_CERT_FILE`. Confirm on each runner that the frozen bundle's `ssl` honours `SSL_CERT_FILE` (the TLS open question in the spec); if it doesn't, stop and report rather than adding an HTTP escape hatch to the updater.
- Steps, each `{"ok", "detail"}` into the report like `verify_artifact.py`: `env_dump` (a frozen child prints its own `os.environ` keys; record which `_PYI_*`, `TCL_*`, `TK_*`, `LD_*`, `DYLD_*` are set, informational); `tls_github` (old copy `--check-for-update` against the real `api.github.com`, must parse a release); `apply_update` (old copy `--apply-update`, exit 0, then the assertions in the spec's "Verifying on CI" list: ack written, single process, `current` resolves into the new copy, new copy `--self-check` reports the tag, no `Zone.Identifier` on Windows, no `com.apple.quarantine` on macOS, `Tokitty.app` new and `.Tokitty-v0.0.1.app` owned on macOS); `no_ack_rollback` (fresh scratch, `TOKITTY_UPDATE_TEST_NO_ACK=1`, exit 2 well inside the 120 s ack limit since the wait aborts when the child exits, no Tokitty process left running, `current` back on the old copy, macOS `Tokitty.app` restored); `bad_checksum` and `bad_layout` (versions dir unchanged); `cleanup` (the seed set from the spec, then a launch of the new copy, then the expected survivors); `rename_swap` on macOS only (real `renamex_np` on two temp dirs).
- Every process the verifier starts is killed at the end of its step, success or not.

**Tests:** unit tests for the verifier's pure parts (fake release JSON, sums writing, survivor comparison). The real run is the CI job; this task is done when a `workflow_dispatch` run of `release.yml` on the branch is green on all four targets, which needs the branch pushed (see Execution order).

## Task 7: README

**Files:** `README.md`.

**Acceptance criteria:**
- The Install section's manual update paragraph is replaced by the in-app update (what it checks, the menu items, what it installs where, that it keeps the previous copy and deletes only copies it installed, and that hand-unpacked copies are never deleted).
- Rollback per OS, with the two macOS `mv` commands for the hidden `.Tokitty-<old tag>.app`.
- The integrity note: `SHA256SUMS` against corrupted or tampered downloads, not against a compromised publisher; builds are unsigned.
- Configuration lists `TOKITTY_UPDATE_API_URL` as a test hook.
- A source run only reports updates.
- The public writing playbook's mechanical rules hold: no em-dashes, straight quotes, one line per paragraph.

## Task 8: Manual gate (Nick)

After merge and a tagged release that contains the updater, the next release is the first real update. Nick updates his daily driver (`releases\v<ver>\Tokitty\Tokitty.exe`) from the menu and confirms: the new copy appears under `releases\v<new>\`, hooks keep working in an open Claude Code session, Start at login points at the new exe, and the old copy is still there. A third release confirms that the oldest updater-installed copy is deleted and the hand-unpacked ones are not.

## Execution order and review gates

1 → 2 → 3 → 4 → 5 → 7 → 6. Each task goes to its own Sonnet subagent with this plan and the spec; the orchestrating session reviews the diff and runs the three checks before the next task starts. Task 6 is written last because its verifier drives the finished flow. Pushing `auto-update` and dispatching `release.yml` on it are outward-facing, so ask Nick before the first push. Iterate on CI failures in Task 6 until all four targets are green, then run a Codex review of the full branch diff before opening the PR.
