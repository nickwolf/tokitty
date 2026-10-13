# Plan: package and ship the Qt build (#115)

Spec: `docs/superpowers/specs/2026-10-13-qt-package-design.md` (Codex-reviewed, open questions decided by Nick 2026-10-13). Branch `qt-package-115`, worktree `.worktrees/qt-package-115`, cut from main at 879d39c. Read the spec before each slice. This plan orders the work and pins what the spec leaves to implementation. Nick does not review each slice; Codex reviews each finished slice.

## Setup and validation

Run every command from the worktree root, with `python -m`, never a script by path.

- Headless: `/usr/bin/python3 -m pytest -q`.
- Qt: `QT_QPA_PLATFORM=offscreen /tmp/tokitty-qt-venv/bin/python -m pytest -q -m qt` (recreate the venv if `/tmp` was wiped: `/usr/bin/python3 -m venv --system-site-packages /tmp/tokitty-qt-venv && /tmp/tokitty-qt-venv/bin/pip install "PySide6-Essentials==6.12.0"`).
- Tk gui: `xvfb-run -a /usr/bin/python3 -m pytest -q -m gui`, unsandboxed. `tests/test_accounts_ui.py` flakes on its own; rerun it alone before treating a failure there as real.
- Lint: `ruff check tokitty tests freeze`.
- Build venv for frozen builds: `/tmp/tokitty-build-venv` (`/usr/bin/python3 -m venv /tmp/tokitty-build-venv && /tmp/tokitty-build-venv/bin/pip install -e ".[packaging,streamdock,qt]" cryptography`). It runs Python 3.12; the release builds on 3.13, which does not matter for layout and trim checks.
- Local frozen run on WSL: unset `QT_QPA_PLATFORM` and `WAYLAND_DISPLAY` (else Qt uses WSLg and windows land on Nick's screen), run under `xvfb-run -a`. The five xcb packages are installed system-wide here.

Constraints for every slice:

- The Tk path behaves exactly as before. No edit to `ui.py`, `tray.py`, `settings_ui.py`, `update_dialog.py`, `run_gui` or any Tk test. The only Tk-visible change is the `Info.plist` copyright string (spec decision 5).
- Nothing under `tokitty/qt/` imports `tkinter`, `pystray`, `AppKit`, `Foundation`, `objc` or a Tk module (`tests/test_toolkit_isolation.py`). `quitOnLastWindowClosed` stays False.
- The shipped judge (`update_install.run_self_check`) is not edited. Every self-check report a `tk,qt` build can produce must pass it; tests run the real `run_self_check` against fake runners returning those reports.
- No em-dashes in code, comments, docstrings or docs. Comment density matches the surrounding modules.
- Implementers do not commit; the main session reviews and commits each slice.

Order: slices 1 and 2 run in parallel (disjoint files). Slice 3 after both. Slice 4 after 3. Slice 5 is the main session's.

## Slice 1: self-check rule, Wayland mapping, debug notify

Files: `tokitty/frozen.py`, `tokitty/qt/app.py`, `tokitty/qt/tray.py`, `tests/test_frozen.py`, `tests/qt/test_qt_self_check.py`, `tests/qt/test_qt_tray.py` (or a new `tests/qt/test_qt_debug_notify.py`).

1. `frozen.self_check` (spec decision 2): when `"tk" in toolkits`, the `qt` entry and (under `TOKITTY_SELF_CHECK_TK=1`) the `qt_root` entry are added only when `os.environ.get("TOKITTY_QT") == "1"`. When `"tk" not in toolkits`, behaviour is unchanged (always present, `tk_root` aliased). Update the comments that describe the rule, citing #115.
2. `frozen.qt_platform_env(environ=None, *, frozen=None, platform=None) -> Optional[str]` (spec decision 3): toolkit-free. Returns the replacement value or None. In a frozen (`sys.frozen`) Linux build, when `QT_QPA_PLATFORM` is set, every `;`-separated entry's platform name (the part before `:`) starts with `wayland`, and `DISPLAY` is set, it sets `environ["QT_QPA_PLATFORM"] = "xcb"`, prints one line to stderr (`tokitty: QT_QPA_PLATFORM=<old> needs the Wayland plugin, which this build leaves out; using xcb`), and returns `"xcb"`. Otherwise it changes nothing. Called by `_check_qt_root` before it creates the `QApplication`, and by `qt/app.py` before every `QApplication(...)` it creates (the main launch at `_run_locked`, about line 264, and the debug-state launch).
3. `TOKITTY_DEBUG_NOTIFY=1` (spec decision 9), Qt mode only, real launches (not the debug-state launch, which has no tray): after `tray.start()`, `QTimer.singleShot(5000, ...)` calls a new `QtTray.debug_notify(title, message) -> bool`, which does what `notify` does but without the darwin gate, and prints `tokitty: debug notify: submitted` or `tokitty: debug notify: not submitted (<reason>)` to stderr. `notify` itself is unchanged. Title "Tokitty", message "Test notification (TOKITTY_DEBUG_NOTIFY)".
4. Tests, unmarked (`tests/test_frozen.py`): with `TOKITTY_BUILD_TOOLKITS=tk,qt` and no `TOKITTY_QT`, the report's check keys equal the default set (plus `tk_root` with the switch) and pass with `PySide6` imports blocked (the existing blocker in that file); with `TOKITTY_QT=1` the `qt` entry appears and `qt_root` appears only with `TOKITTY_SELF_CHECK_TK=1`; `qt`-only builds are unchanged (existing tests stay green, untouched). The real `update_install.run_self_check` accepts each `tk,qt` report from a fake runner. `qt_platform_env`: the table in the spec's Tests section.
5. Tests, `qt` marker: `_check_qt_root` calls `qt_platform_env` (patch it, assert called); `debug_notify` on a fake icon with `platform="darwin"` calls `showMessage` and returns True, while `notify` on the same tray returns False and does not call it; `run_qt` schedules the debug notify only with the variable set (follow the existing `run_qt` tests in `tests/qt/` for how they build a world).

Validation: headless, qt, ruff.

## Slice 2: trim, licences, Info.plist

Files: `freeze/bundle_data.py`, `freeze/tokitty.spec`, new `freeze/licenses/` (five files), new `freeze/qt_notices.py`, `tests/test_freeze_spec.py`.

1. `bundle_data.py`:
   - `QT_KEPT_BINDINGS = ("QtCore", "QtGui", "QtWidgets")` and `QT_EXCLUDED_MODULES`: every `PySide6.Qt*` binding module present in the installed PySide6-Essentials 6.12.0 wheel except the kept three and `QtDBus` (list them from `/tmp/tokitty-build-venv`; hard-code the list, do not compute it at build time).
   - `qt_entry_dropped(dest: str) -> bool`: normalises `\` to `/`; returns False for anything not under `PySide6/`. Under it, True when a path component equals one of the dropped plugin directory names (`imageformats`, `iconengines`, `generic`, `networkinformation`, `tls`, `qmltooling`, `translations`, and on Linux the `wayland-*` directories: match components starting with `wayland-`), or the file is one of the dropped platform plugins (`qoffscreen`, `qminimal`, `qdirect2d`, `qwayland*`, by base name with any `lib` prefix and any extension), or the file or framework is one of the dropped Qt libraries (Network, Pdf, Qml, QmlModels, QmlMeta, QmlWorkerScript, Quick, OpenGL, OpenGLWidgets, Svg, VirtualKeyboard: match `Qt6<Name>.dll`, `libQt6<Name>.so*`, `Qt<Name>.framework` as a component, and the PySide6 binding `Qt<Name>.pyd/.abi3.so`), or `opengl32sw.dll`, or `d3dcompiler_*.dll`. QtDBus, `platforms/` (other than the dropped plugins), `styles/`, `platformthemes/`, `platforminputcontexts/`, `xcbglintegrations/` are kept. Match on whole components and base names, never substrings of the full path.
   - `QT_TRIM_ENABLED = os.environ.get("TOKITTY_QT_TRIM", "1") != "0"` read in the spec, not at import of `bundle_data` (keep `bundle_data` free of environment reads, as now).
   - `LICENSE_FILES = ("LICENSE", "LGPL-3.0.txt", "GPL-3.0.txt", "Qt-NOTICE.txt", "pystray-NOTICE.txt")`.
   - `info_plist`: `NSHumanReadableCopyright` becomes `"Copyright © 2026 Nick Wolf. MIT License.\nIncludes Qt 6.12.0 and PySide6 6.12.0 under the LGPL-3.0, see Contents/Resources/licenses."` with the version read from the `qt` extra's pin in `pyproject.toml` (a small parser next to `project_version`), so a pin bump moves it.
2. `freeze/licenses/`: `LICENSE` is a copy of the repo's (a test asserts they are byte-identical, so it cannot drift). `LGPL-3.0.txt` and `GPL-3.0.txt` verbatim from `/usr/share/common-licenses/LGPL-3` and `GPL-3`. `pystray-NOTICE.txt`: what pystray is, its licence (LGPL-3.0, from the installed distribution's metadata), its copyright line, its source URL, and a pointer to the LGPL text. `Qt-NOTICE.txt` from `qt_notices.py`.
3. `freeze/qt_notices.py`: `python -m freeze.qt_notices --qtbase <dir> --out freeze/licenses/Qt-NOTICE.txt`. `<dir>` holds the `qt_attribution.json` files of qtbase at tag `v6.12.0`, fetched with `gh api` (the tree at `repos/qt/qtbase/git/trees/v6.12.0?recursive=1`, then each `qt_attribution.json` and the licence files they name; api.github.com is on the sandbox allowlist, download.qt.io is not). It keeps the entries whose `QDocModule` is `qtcore`, `qtgui`, `qtwidgets` or `qtdbus` (and any whose `QtUsage` names the xcb, cocoa or windows platform plugin), and writes: the header from spec decision 5 (versions, LGPL-3.0, source links, where the Qt libraries sit in each archive, how to replace them, the README's licence paragraph in short), then each component's name, version, copyright, licence id, and licence text. For ICU on Linux, append the ICU licence from `/usr/share/doc/libicu*/copyright` or the `icu` licence in qtbase's tree, whichever exists, and say which. Report in the hand-back which entries were kept and dropped.
4. `tokitty.spec`:
   - The GUI Analysis gets `excludes=list(QT_EXCLUDED_MODULES)` when trimming; the hook Analysis adds `"PySide6", "shiboken6"` to its excludes always.
   - After Analysis, when trimming: `gui.binaries = [e for e in gui.binaries if not qt_entry_dropped(e[0])]`, same for `gui.datas`.
   - macOS: the five licence files go in `datas` under `licenses`. Windows and Linux: after `COLLECT`, copy them into `Path(DISTPATH) / APP_NAME / "licenses"` (create the folder; `DISTPATH` is PyInstaller's spec global).
5. Local Linux build in both trim states (main session runs this after review if the implementer cannot run xvfb): `TOKITTY_BUILD_TOOLKITS=tk,qt` with and without `TOKITTY_QT_TRIM=0`; record the extracted size, the `tar -czf` size, the list of PySide6 files dropped, and whether the GTK 3 theme's libraries remain (spec decision 3: drop the theme only if its GTK libraries go with it and Settings still looks right).
6. Tests (`tests/test_freeze_spec.py`): `qt_entry_dropped` against a table of real destination paths copied from the local Linux build's TOCs and written out for Windows and macOS from the spike's file lists (each row commented with its source); paths outside `PySide6/` containing `tls`, `generic` or `translations` are kept; QtDBus kept on every platform; `QT_EXCLUDED_MODULES` excludes neither the kept three nor QtDBus; the licence files exist, `LICENSE` matches the repo's, `Qt-NOTICE.txt` names the pinned version; `info_plist` carries both copyright lines; the spec source mentions `qt_entry_dropped` and `LICENSE_FILES` (the file already tests the spec by reading its text).

Validation: headless, ruff, plus the local build.

## Slice 3: verifiers

Files: `freeze/verify_artifact.py`, `freeze/verify_update.py`, `tests/test_verify_update.py`, new `tests/test_verify_artifact_qt.py`.

1. `verify_artifact.py`:
   - `step_self_check` keeps its run and, when the report's `toolkits` contains `qt`, runs again with `TOKITTY_QT=1` added and `QT_QPA_PLATFORM` removed from the env, requiring `qt` and `qt_root` ok and every other rule it already applies. It stores `qt_version` (the `qt` entry's detail) in `ctx`.
   - New `step_bundle_contents`: the five `LICENSE_FILES` exist (Windows and Linux: `<app dir>/licenses/`; macOS: `Tokitty.app/Contents/Resources/licenses/`); `Qt-NOTICE.txt` contains `ctx["qt_version"]` when set; walking the PySide6 tree of the extracted bundle, no file is one `bundle_data.qt_entry_dropped` drops, and QtDBus and the platform plugin for the OS (`qwindows`, `qcocoa`, `qxcb`) are present. Import `bundle_data` by path the way the file already reaches the repo.
   - New `step_tk_without_qt`: copy the app dir into the work dir, delete the QtCore library from the copy (`Qt6Core.dll`, `libQt6Core.so.6`, `QtCore.framework`'s binary), run its self-check with `TOKITTY_SELF_CHECK_TK=1` and without `TOKITTY_QT`; require exit 0, a report, `tk_root` ok. Skipped (ok, with a detail saying why) when the report's `toolkits` lacks `qt`. On macOS, deleting a file breaks the bundle's signature; run the inner executable directly, as the self-check step does, and say in the detail whether launch needed `codesign --force --deep -s -` on the copy (try without first).
   - Register both in `STEPS` after `self_check`.
2. `verify_update.py`:
   - `--old-tag` (default `v0.0.1`) into `ctx["old_tag"]`; replace every use of `OLD_TAG` (prepare's "newer than" check, `Scenario`'s old folder at about 565 and 582, the macOS backup checks at about 901 and 975, and any other) with it. Keep `OLD_TAG` only as the default.
   - `--qt`: `scratch_env` (or `Scenario`'s env builder) sets `TOKITTY_QT=1` and removes `QT_QPA_PLATFORM` and `WAYLAND_DISPLAY` for every child. In `step_apply_update`, when `--qt`, require `<state dir>/after-update-qt.json` to exist and hold `exposed_before_ack: true`; put its content in the detail.
   - Docstring: the two flags and the Qt rule.
3. Tests: `--old-tag` reaches prepare and `Scenario` paths (construct a `Scenario` with a fake ctx and assert its folders); `--qt` env (set and removed names); the `after-update-qt.json` check as a pure helper (`after_update_qt_problems(state_dir) -> list`) passing, missing and false. For `verify_artifact`: `step_bundle_contents` and the Qt branch of `step_self_check` on a fake app dir with a fake executable script that prints a canned report (follow how existing verifier tests fake children, if any; else a small Python script made executable).

Validation: headless, ruff. Then the main session runs the local rehearsal: v0.0.1 and v99.0.0 Linux builds with `tk,qt`, `verify_artifact` on one, `verify_update --rehearsal` in Tk mode and with `--qt --only apply_update,no_ack_rollback`, all under `xvfb-run -a` with `QT_QPA_PLATFORM` and `WAYLAND_DISPLAY` unset; and the legacy pass with `--old-tag v0.6.2 --only apply_update` against the downloaded v0.6.2 Linux asset.

## Slice 4: workflow and README

Files: `.github/workflows/release.yml`, `README.md`, `tests/test_freeze_spec.py` (its release-workflow assertions).

1. `release.yml` build job:
   - `env: TOKITTY_BUILD_TOOLKITS: tk,qt` at job level; install `.[packaging,streamdock,qt]`.
   - Linux apt line adds `libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 libxkbcommon-x11-0`.
   - Rehearsal tag `v0.0.2` becomes `v99.0.0` everywhere in the file.
   - After "Verify update", a "Verify update (Qt)" step: same inputs plus `--qt --only apply_update,no_ack_rollback`, its own `--work` and `--report` (`update-report-qt.json`).
   - A "Verify update from a shipped release" step: `env: GH_TOKEN: ${{ github.token }}` and `LEGACY_TAG: v0.6.2` (job or workflow env, with a comment that it moves on purpose); `gh release download "$LEGACY_TAG" --pattern "tokitty-$LEGACY_TAG-<target>.<ext>" --dir "$RUNNER_TEMP/legacy"`; then `verify_update.py` with `--old-archive` that file, `--old-tag "$LEGACY_TAG"`, the same new archive, sums and tag, `--only apply_update`, its own work dir and report (`update-report-legacy.json`). Linux under `xvfb-run -a`.
   - The summary step prints the archive size in bytes and a line per update report (Tk, Qt, legacy).
   - Upload the two new reports with the others.
   - The `keychain` job is unchanged.
2. README: Linux install section gains the Qt-mode packages line and the best-effort translucency sentence (spec decision 8); the License section gains the licences paragraph (spec decision 5). One line per paragraph, no em-dashes.
3. Tests in `tests/test_freeze_spec.py`, in the file's existing style of reading the workflow text: the build installs the `qt` extra, declares `tk,qt`, installs the xcb packages, runs the Qt and legacy update steps, and pins `LEGACY_TAG`.

Validation: headless, ruff, `python -c "import yaml"`-free check: `gh workflow view` is not available for a branch, so parse the YAML with the build venv's Python if PyYAML is present, else rely on the dispatch.

## Slice 5: main session

1. Push only after Nick says so. Then `gh workflow run release.yml --ref qt-package-115`, and a second dispatch with `TOKITTY_QT_TRIM=0` for the untrimmed sizes (needs a `workflow_dispatch` input or a temporary env edit; prefer an input `trim` defaulting to true, added in slice 4 if Nick agrees to the extra dispatch).
2. Fill the spec's Sizes section from the reports and `gh release view v0.6.0 --json assets`.
3. Post the #142 comment and the v0.9.0 notices issue (drafts to Nick first).
4. Manual Windows check, then hand Nick the Mac checklist (spec Tests, manual macOS).
5. Codex reviews each slice's diff after the main session's own review; a final whole-branch Codex review before the PR.
