# Installable app (#48)

Status: design only, 2026-09-23. Nothing here is built. The numbers come from a throwaway Windows spike (PyInstaller 6.22.3 with pyinstaller-hooks-contrib 2026.7, Nuitka 4.2.2 with its auto-downloaded zig 0.16.0 backend, Briefcase 0.4.5, pystray 0.19.5, Pillow 12.3.0, Windows Python 3.13.2 with Tk 8.6.15, WSL Ubuntu Python 3.12.3) run on Cucumber with Defender real-time protection on. Claude Code facts come from the 2.1.280 binary and its public CHANGELOG, Codex facts from codex-cli 0.156.1.

Settled before this spec and not reopened here: ship unsigned, and build the artifacts on the existing three-OS CI matrix, published to GitHub Releases on a tag.

## Recommendation

- **PyInstaller, onedir, windowed**, one build per OS on its own runner. Windows ships a zip of the folder, macOS a zipped `.app`, Linux a tarball. Onefile is rejected on measured evidence (below), for both PyInstaller and Nuitka.
- **Bundle `hook_writer.py` and `prices.json` as data files at `tokitty/` inside the bundle.** Both are found through `Path(__file__)`, and PyInstaller puts package data at the same relative path, so `hooks_install.py` and `pricing.py` work unchanged. Verified: a real `--install-hooks` from the packaged exe wrote a byte-identical `hook_writer.py` and all seven hook entries, and running the registered command wrote the session state file.
- **Pick the hook runner by where Claude Code runs, not by how Tokitty was installed.** A Claude home inside WSL keeps today's `python3 <copied hook_writer.py>`: WSL ships `python3`, and that path measured 45.5 ms against 82.4 to 97.4 ms for the Windows exe through interop. A Claude home on the same OS as a frozen Tokitty gets a second bundled executable, `tokitty-hook`, registered in Claude Code's exec form (`command` plus `args`, no shell). A source install keeps today's command everywhere.
- **Codex needs nothing for #48.** Its provider declares `activity=False`, so no hooks are ever installed into a Codex home.
- **Autostart already handles a frozen build** (`resolve_launch_command` returns `[sys.executable]` when `sys.frozen` is set). It needs two small fixes, not a redesign.
- **macOS Keychain should not re-prompt per release**, because the binary the ACL is granted to is `/usr/bin/security`, not Tokitty. That is a prediction. The CI job in Task 7 confirms or rules it out without anyone owning a Mac.

## Measurements

All Windows numbers are from `subprocess.run` with a real hook payload on stdin, timed with `perf_counter`, and each run asserted exit 0, empty stdout, and a state file whose `seq` incremented. Cold is the first run after the build, then 20 warm runs. Sizes are bytes on disk and a deflate zip of the same folder.

| Artifact | Size | Zipped | Hook cold | Hook median (min to max) | GUI start, 5 runs (ms) |
|---|---|---|---|---|---|
| `python.exe hook_writer.py` (today, native Windows) | | | 56.6 ms | 50.3 ms (49.0 to 63.4) | |
| `pythonw.exe hook_writer.py` | | | 230.7 ms | 44.9 ms (42.8 to 49.4) | |
| `pythonw.exe -m tokitty` (today) | | | | | 218, 191, 207, 220, 209 |
| PyInstaller onedir | 38.01 MB | 17.78 MB | 82.4 ms | 77.9 ms (73.8 to 90.6) | 310, 199, 237, 223, 198 |
| PyInstaller onedir, hook-only exe | 16.02 MB | 7.13 MB | 267.7 ms | 77.5 ms (69.6 to 100.9) | |
| PyInstaller onefile | 17.80 MB | | 1297 ms | 2583 ms (1192 to 5076) | first launch hung, see below |
| Nuitka standalone | 50.80 MB | 20.37 MB | 51.7 ms | 45.0 ms (42.0 to 56.0) | 238, 176, 206, 216, 208 |
| Nuitka onefile | 50.94 MB | | 1057 ms | 1078 ms (992 to 3900) | not measured |
| Briefcase (MSI / zip) | | 14.72 / 18.28 MB | hangs | hangs | no tkinter |

WSL interop, timed from WSL bash:

| Hook as Claude Code in WSL would call it | Sessions dir | Cold | Median (min to max) |
|---|---|---|---|
| `python3 hook_writer.py` (today) | ext4 `/tmp` | 44.4 ms | 45.5 ms (43.2 to 49.1) |
| PyInstaller onedir `.exe` via interop | Windows dir | 98.1 ms | 82.4 ms (80.3 to 104.7) |
| PyInstaller onedir `.exe` via interop | `\\wsl.localhost\Ubuntu\tmp\...` | 95.9 ms | 97.4 ms (88.9 to 111.0) |
| PyInstaller onefile `.exe` via interop | Windows dir | 1445 ms | 1391 ms (1221 to 3922) |

Linux, PyInstaller onedir built in WSL: 31.03 MB, 12.76 MB zipped. Run from `/mnt/c` its hook median was 544 ms, which is the 9P bridge opening a few hundred `.so` files per launch, not Linux. Copied to ext4 and re-timed: cold 57.6 ms, median 53.0 ms (50.1 to 60.6).

The budget the hook has to meet is the `<100 ms` gate from `docs/hook-preflight-2026-07-16.md`, where plain `python3` on ext4 measured a 53.5 ms median. PyInstaller onedir on native Windows lands at 77.9 ms: inside the gate, 27.6 ms slower than `python.exe`. That is an estimate from the spike's dispatcher build, not the final artifact, and Task 6 re-measures the real one. The hook runs synchronously on `PreToolUse` and `PostToolUse`, so that is roughly 55 ms more per tool call, and only for someone running Claude Code natively on Windows. The hook-only exe saves disk space but no time (77.5 ms against 77.9 ms). Launching the bootloader is the cost, not what it carries.

### Why onefile is out

Both onefile builds got slower with every run. The unsorted log climbs from about 1.2 s to 5.1 s (PyInstaller) and from 1.0 s to 3.9 s (Nuitka) over 20 runs. Each launch unpacks into a fresh `%TEMP%` dir that Defender has never seen. The first GUI launch of the PyInstaller onefile build never showed a window within 15 s, survived the harness's `Stop-Process -Force`, and was still alive 16 min 39 s later when it was killed by hand. None of the onedir or standalone builds did anything like that. Every hook call would pay the unpack, so onefile is out even before the hang.

### Failure behaviour of a windowed build

With `hook_writer.py` not bundled, both tools fail the hook install at the same line (`hooks_install.py:245`, `shutil.copy2`, `FileNotFoundError: [WinError 3]`). PyInstaller's windowed bootloader turns an uncaught exception into a modal "Unhandled exception in script" dialog that blocks until someone dismisses it. It showed up on the desktop during the spike. Nuitka exits 1 silently. This matters for the hook runner in particular: one escaped exception per tool call would be one dialog per tool call. `hook_writer.main` is already wrapped in a bare `except`, and the spike fed the runner garbage stdin and an unwritable sessions dir without ever reaching the dialog. The runner's entry must keep that guarantee around its imports too.

In a windowed PyInstaller build `sys.stdin/stdout/stderr` are `None` when there is no parent console, but a pipe handed over by the parent works: every timed hook run above read its payload from stdin. When launched from a terminal, the child inherited that console and `print(..., file=sys.stderr)` appeared there. So `--install-hooks` and `--debug-print` still work from a terminal but print nothing from a double-click. That is acceptable, because the Accounts dialog is the GUI path for hooks.

## Question by question

### 1. Hook installation when frozen

It still holds after the multi-harness seam. `hooks_install.py:41` sets `_HOOK_WRITER_SOURCE = Path(__file__).resolve().parent / "hook_writer.py"` and line 245 copies it to `<config_dir>/tokitty/hook_writer.py`, on every install call, even when the entries already exist. What PRs #61 and #62 changed is who gets hooks: `get_config_dirs` and `apply_account_mutation` now skip any account whose provider has `capabilities.activity == False`.

The issue says PyInstaller "compiles `.py` files into its archive", and that is true, but the fix is simpler than it suggested. Declaring the file under `datas` at destination `tokitty` puts it at `_internal/tokitty/hook_writer.py`, and `Path(__file__)` for a frozen `tokitty.hooks_install` resolves to `_internal/tokitty/`. No code change is needed for the copy itself. The same applies to `prices.json`, which #48 missed: `pricing.PACKAGED_PRICES = Path(__file__).with_name("prices.json")`, and the unbundled spike build failed to load prices. Both were confirmed by a `--spike-probe` run inside the frozen exe, before and after adding `datas`.

The acceptance test is the one the spike ran and CI should repeat on all three OSes: point a scratch state dir (`LOCALAPPDATA` on Windows, `HOME` elsewhere) at an `accounts.json` naming a scratch Claude home, run the artifact's `--install-hooks`, check that the copied `hook_writer.py` is byte-identical to the repo's and that seven entries are present, then execute each registered command with a sample payload and check the state file.

### 2. Where the hook's interpreter comes from

Today `_build_command` writes `python3 "<dir>/tokitty/hook_writer.py" --sessions-dir "<dir>/tokitty/sessions"`, or `python ...` when the config dir looks like `C:\...`. A frozen app changes nothing about that string, so the interpreter requirement moves to wherever Claude Code runs.

**(a) Keep Python and document it.** Correct for WSL and for most Linux and macOS machines, and it is what ships today. It leaves the one user #48 is for, someone on native Windows who downloaded the exe to avoid installing Python, with hooks that never fire. On that machine `python` usually resolves to the Microsoft Store App Execution Alias, which prints an install hint and exits instead of running anything. On macOS without the Command Line Tools, `/usr/bin/python3` is a shim that opens the developer-tools install dialog, and a hook could trigger that on every tool call.

**(b) The frozen app as its own hook runner.** Measured above: 77.9 ms median on native Windows, inside the gate. Two details decide how it is registered.

- Claude Code has supported an exec form for command hooks since 2.1.139 (CHANGELOG: "Added hook `args: string[]` field (exec form) that spawns the command directly without a shell"). The 2.1.280 schema text says that without `args` the command "runs through a shell (bash on POSIX, PowerShell on Windows without Git Bash)". A quoted exe path at the start of a PowerShell command is a string expression rather than a call (it needs `&`), so shell form is fragile on exactly the machines this path is for. Exec form removes quoting from the problem on every OS.
- The runner should be a **separate executable** (`tokitty-hook`) rather than `tokitty --run-hook`. In the spike the two measured the same (77.5 ms against 77.9 ms), but neither was the exact artifact proposed here. One was the throwaway dispatcher, the other a separately collected hook-only build. The reason for separating them is containment, not speed. The hook executable can never start the GUI, whatever arguments it gets, so no hook-registration mistake can open a window from inside a Claude Code tool call. Built as a second `EXE` in the same PyInstaller `COLLECT`, it should share `_internal` rather than add the 16 MB of the standalone hook-only build. The spike tested neither the sharing nor the final layout, so Task 1 checks the sharing first and Task 6 gates the timing of the final archived artifact on every OS.
- **Claude Code older than 2.1.139 is unsupported for exe hooks.** How an older version treats an unknown `args` field (rejects the entry, ignores it and shell-runs `command`, or something else) was not tested, and no older binary was available to test it. Don't promise a silent no-op. The README states the minimum version.

**(c) Other options considered.**

- *Claude Code HTTP hooks* (since 2.1.63): POST the payload to a loopback listener in the widget, with no process spawn at all. Rejected for now. Events are lost whenever the widget isn't running, including `SessionEnd`, which leaves stale session files. The state files exist precisely so activity survives a widget restart. The binary also refuses private and link-local targets ("Loopback (127.0.0.1, ::1) is allowed for local dev"), and under WSL's default NAT networking a WSL-side Claude Code can't reach the Windows loopback, so the WSL setup couldn't use it. Worth a separate issue if the per-call cost ever matters.
- *`tokitty.exe` through interop for WSL Claude homes*: works, at 82.4 ms (Windows dir) or 97.4 ms (writing to the `\\wsl.localhost` sessions dir, which is where the state really lives). That is twice the 45.5 ms `python3` path, depends on WSL interop being enabled, and breaks whenever the Windows app folder moves. No benefit over `python3`, which every mainstream WSL distro ships.
- *Nuitka for a faster runner*: 45.0 ms, as fast as `python.exe`. See question 5 for why it still loses overall.

**Rule for `_build_command`:**

| Tokitty | Claude home | Registered hook |
|---|---|---|
| source install | any | unchanged: `python3`/`python` + copied `hook_writer.py` |
| frozen | WSL (UNC path, from Windows) | unchanged: `python3` + copied `hook_writer.py` |
| frozen | same OS as Tokitty | exec form: `command` = absolute path of bundled `tokitty-hook`, `args` = `["--sessions-dir", "<dir>/tokitty/sessions"]` |

The copied `hook_writer.py` is still written in every case, so switching between rows never strands a command pointing at a missing file.

**What this makes `hooks_install` do that it doesn't today.** `install_hooks_for_dir` treats any event that already has a tokitty-marked entry as installed and skips it. So a user moving from a source install to the frozen app, or unzipping a new release into a new folder, keeps the old command forever. Three constraints shape the fix, all read from the current code:

- **Refresh rewrites, it never adds.** `--uninstall-hooks` removes the entries but leaves the account in `accounts.json`, and nothing records that hooks were turned off on purpose. A startup pass that installed hooks for every hook-enabled account would silently undo an uninstall. So the startup `ensure_current` only rewrites Tokitty entries that are already present and out of date. Adding entries stays an explicit install (the CLI flag or the Accounts dialog).
- **Ownership has to be exact.** `_is_tokitty_entry` (`hooks_install.py:196`) matches any command containing the substring `tokitty`, so a user's own hook in a path containing that word would count as ours. Before anything rewrites entries, ownership must recognise only the shapes Tokitty has actually written: the historical `python`/`python3 "<dir>/tokitty/hook_writer.py" --sessions-dir "<dir>/tokitty/sessions"` string, and the new exec form whose `command` ends in `tokitty-hook[.exe]` with `args` `["--sessions-dir", <dir>/tokitty/sessions]`. Rewrite only the owned hook inside an entry, and keep its matcher and any other hooks in it.
- **`settings.local.json` is read, never written.** Install already counts events found there as installed, and uninstall already leaves them alone with a note. Refresh keeps that ownership rule. An owned entry in the local file that is out of date gets reported in the same way uninstall reports one today, and no replacement goes into `settings.json`, which would make the hook fire twice.

Running Claude Code sessions keep the old command until restarted (preflight Q2), so if an update deletes the old folder, open sessions log hook errors until they restart. The README should say so.

### 3. Codex

`CodexProvider.capabilities = ProviderCapabilities(rate_limits=True, token_ledger=True, activity=False)` (`providers/codex.py:288`), and `provider_has_hooks` keys on that flag. So Codex homes never get hooks and #48 has no Codex hook path to fix. Codex activity comes only from rollout files, and those carry rate limits and tokens, not live activity.

The provider's docstring says "Codex has no hook equivalent", and that is now out of date. codex-cli 0.156.1 reports `hooks  stable  true` in `codex features list`. It reads a `hooks.json` with the same `type: "command"` shape, and its binary carries the same event names Claude Code uses (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `SubagentStop`, `SessionEnd`, plus `PermissionRequest` and `PreCompact`). It also pins every hook by hash (`[hooks.state.'<file>:<event>:..'] trusted_hash = "sha256:..."` in `config.toml`) and asks the user to review it again when it changes. If Codex activity is ever built, it will hit the same interpreter question, and the trust pin makes a stable runner path matter more, because every path change forces a re-review. That belongs in its own issue. For #48, keep the runner harness-neutral (it takes a sessions dir and a payload, nothing Claude-specific), which `hook_writer.py` already is.

### 4. Autostart and every other `python -m tokitty`

`autostart.resolve_launch_command` already returns `[sys.executable]` when `sys.frozen` is true, which PyInstaller sets. So the registered command becomes:

- Windows: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Tokitty` = `"<folder>\Tokitty.exe"` (via `subprocess.list2cmdline`)
- macOS: a LaunchAgent plist with `ProgramArguments` = `[".../Tokitty.app/Contents/MacOS/Tokitty"]`
- Linux: `Exec=<folder>/tokitty` in `~/.config/autostart/*.desktop`

Two changes are needed:

1. `write_launcher_file` runs unconditionally in `ensure_current` and `write_launcher_and_register`, and when frozen it would write an `autostart_launcher.pyw` pinning `_default_repo_root()`, which is the bundle's `_internal` parent. Harmless but junk. Skip it when frozen.
2. **macOS App Translocation.** A quarantined app run from where it was unzipped (usually `~/Downloads`) runs from a randomized read-only path under `/private/var/folders/.../AppTranslocation/`. `sys.executable` then points somewhere that vanishes, and both the LaunchAgent and the `tokitty-hook` path would be registered against it. Refuse to register either while `sys.executable` contains `/AppTranslocation/`, and tell the user to move Tokitty to Applications first.

The only other place that builds an interpreter command line is `hooks_install._build_command`, covered in question 2. `pythonw.exe -m tokitty` appears only in docs and the README.

### 5. Packaging tool

**PyInstaller** is the pick.

- Tk: collected with no flags, and the window came up at the same 198 to 310 ms as the unfrozen app.
- pystray: hooks-contrib 2026.7 ships `hook-pystray.py` (`collect_submodules`), so no `--hidden-import` was needed. The probe loaded the win32 backend on Windows and the xorg backend (via Xlib) on Linux. `gi` (AppIndicator) was reported missing on Linux because PyGObject isn't installed, so tray support on Linux is xorg only. The tray is already optional and macOS already excludes it (#45).
- Pillow: nothing extra needed.
- Size: 17.78 MB zipped on Windows, 12.76 MB on Linux.
- Hook: 77.9 ms.
- CI: `pip install pyinstaller`, no compiler, builds in seconds, runs natively on all three hosted runners. It sets `sys.frozen`, which the autostart code already relies on. It ad-hoc signs macOS arm64 binaries by default, which arm64 requires just to run them.

**Nuitka** is the fallback, not the pick. The one real advantage is a hook at 45.0 ms, and it fails silently instead of with a dialog. Against it:
- `sys.frozen` is `False` in every Nuitka build, so the frozen branch in `resolve_launch_command` never runs.
- `sys.executable` is a `python.exe` path that doesn't exist, inside the dist dir and in 8.3 short names (`...\ENTRY~1.DIS\python.exe`), so autostart would register a path that isn't there.
- Bundles are 50.80 MB (20.37 MB zipped) because the `tk-inter` plugin takes all 926 Tcl/Tk data files.
- The first build took 248.0 s and needs a C compiler per OS. Windows had no MSVC, and Nuitka's MinGW64 download doesn't support Python 3.13, so it fell back to zig.
- It warned `Cannot find Windows Runtime DLLs to include, requiring them to be installed on target systems`.

None of that is fatal, but it is a bigger bundle, more code to adapt, and slower CI, all to save 32.9 ms per hook for native-Windows Claude Code users only.

**Briefcase** is ruled out. Its default Windows template uses the embeddable CPython distribution, which has no `_tkinter.pyd` and no Tcl/Tk: the probe got `ModuleNotFoundError: No module named 'tkinter'`. Separately, its stub exe hung with a JSON payload on stdin (a 30 s timeout, then a 6 s manual retry, no state file written) and hung on a bare launch too, so it can't serve as a hook runner either. That second failure was not root-caused. Briefcase did have one nice property: data files come along as plain copies with no configuration.

### 6. macOS Keychain across releases

`keychain.py` never calls the Security framework in-process. Both entry points shell out to `security find-generic-password`, and the #46 spec rejected pyobjc for exactly that reason. The process that asks the Keychain is always `/usr/bin/security`, and "Always Allow" grants that binary (the README already says so). A new Tokitty build is a different binary but not the requester. **Prediction: no new prompt per release.** Because neither of us can run macOS, this goes to CI as a test that can fail.

A headless runner can't click a dialog. When a read would need one, `security` fails with "User interaction is not allowed" instead of prompting. So "would prompt" becomes an observable failure, and `read_keychain_secret` maps it to `KeychainAccessError`, i.e. PollResult status `keychain_denied`.

Test (Task 7), on `macos-latest`:

1. Build the artifact twice from the same commit with a different version string, giving two binaries with different ad-hoc signatures (A and B).
2. Create and unlock a throwaway keychain, and put it first in the user search list:
   ```
   security create-keychain -p ci tk.keychain-db
   security set-keychain-settings tk.keychain-db
   security unlock-keychain -p ci tk.keychain-db
   security list-keychains -d user -s tk.keychain-db $(security list-keychains -d user | tr -d '"')
   ```
3. Three items under service `Claude Code-credentials`, one at a time, each holding a fake credentials JSON:
   - `trusted`: `-T /usr/bin/security`, the state "Always Allow" leaves behind.
   - `negative`: `-T ''`, no trusted app. A read must fail. This is the control that proves the instrument can see a prompt at all.
   - `binary-only`: `-T <path of build A>`. If the ACL keyed on Tokitty's binary, A would pass here and B would fail.
4. For each item, run A and B with `--debug-print` under a scratch `HOME` that has **no `accounts.json` at all**, no `~/.claude/.credentials.json`, and no credentials-path override in the environment. Any account with a `config_dir` goes file-only and never touches the Keychain (`credentials.resolve_credentials_source`). Put an external timeout of about 30 s on each run: a hosted runner might leave `security -w` waiting on a dialog nobody can see instead of failing at once, and Tokitty's own `SECRET_TIMEOUT` is 120 s. A timeout counts as "would prompt".
5. Make the fake credentials carry an `expiresAt` in the past. A successful read then ends in `status: stale_token` with a `credentials source:` line naming the Keychain, before any network call. `keychain_denied` or a timeout means a dialog would have appeared. Anything else (`credentials_unreachable` for example) means the harness is wrong, not that the prediction held.
6. Before each run, dump the item's ACL and partition list (`security dump-keychain -a tk.keychain-db`) into the log. Items created with `security add-generic-password` get an `apple-tool:` partition, which is also the partition `/usr/bin/security` reads under, so partition rules should allow every read here and only the ACL varies. The dump confirms that instead of assuming it.

Expected if the prediction holds: `trusted` gives `stale_token` for both A and B, and `negative` and `binary-only` are denied or time out for both. The run disproves the prediction if B is denied where A passed, or if `binary-only` passes for A. If `negative` passes, the harness is broken and the run proves nothing.

This is a check against a synthetic keychain. A pass shows that Tokitty's binary identity plays no part in the decision. It can't show how the real item Claude Code writes behaves, because that item's ACL and partition list come from Claude Code, not from this test. So the manual recipe below stays the release gate for this question, not the CI job.

Manual recipe for a later run on a real Mac, against the login keychain Claude Code writes to. Install release N, launch it, answer "Always Allow". Quit, replace it with N+1 in the same Applications path, and launch again. No dialog should appear. Then `security dump-keychain -a ~/Library/Keychains/login.keychain-db` should list `/usr/bin/security`, and not Tokitty, among the item's trusted applications. The same Mac session should cover App Translocation (question 4): launch the quarantined app from `~/Downloads` first and confirm that autostart and hook install both refuse with the move-to-Applications message, then move it and confirm both register the `/Applications` path.

### 7. Lock, state and config paths

- `paths.state_dir_path` resolves from `LOCALAPPDATA`, `~/Library/Application Support`, or `XDG_CONFIG_HOME`, never from the package. The frozen build is unaffected.
- `lock.SingleInstanceLock` lives in the state dir. The spike showed it working across frozen instances: a second launch printed "Tokitty is already running." and exited.
- `__file__` users in the package: `hooks_install._HOOK_WRITER_SOURCE` and `pricing.PACKAGED_PRICES` (both fixed by `datas`), and `autostart._default_repo_root` (only feeds the launcher file, skipped when frozen per question 4). `scripts/` isn't shipped.
- No `os.getcwd`, `chdir`, or relative-path file access anywhere in `tokitty/`.
- One thing that isn't a path but will bite: a second windowed launch reports "already running" on stderr, which a double-click never shows, so it looks like nothing happened. That is already true under `pythonw.exe`, and it stays out of scope here.

## Risks

- **Antivirus false positives.** Unsigned PyInstaller bootloaders are a common Defender and SmartScreen target. The venv's own copy of `python.exe` also stalled at the OS loader for 20 s or more (zero CPU, `python313.dll` never loaded) while the system copy of the same binary started in 100 ms. That wasn't root-caused, but it looks like the same kind of scrutiny for binaries in unfamiliar paths.
- **The macOS first-launch note in #48 is outdated.** On macOS 15, Control-click ▸ Open no longer bypasses Gatekeeper. The user has to open the app once, then go to System Settings ▸ Privacy & Security ▸ Open Anyway (or run `xattr -dr com.apple.quarantine Tokitty.app`). This doesn't reopen "ship unsigned", but the README note has to describe the current flow.
- **Path churn per release.** Each unzip into a new folder changes the `tokitty-hook` path and the autostart target. `ensure_current` repairs both on the next launch, but open Claude Code sessions keep the old hook command until they restart.
- **Claude Code older than 2.1.139** has an untested reaction to exec-form entries. Declared unsupported for exe hooks, not tested.
- **The numbers are Windows estimates from adjacent builds.** The spike's dispatcher and hook-only build are not the two-EXE bundle proposed here, and macOS was never measured. Task 6 gates the real artifact.
- **Hook cost on native Windows** rises from 50.3 ms to 77.9 ms per event. Inside the gate, but noticeable over a long session. Nuitka is the known way out if it ever matters.
- **Linux glibc floor** is the build runner's glibc. Build on the oldest supported Ubuntu runner, not `ubuntu-latest`.
- **macOS architecture.** `macos-latest` builds arm64 only. Intel Macs need either a separate x86_64 runner or a universal2 build.
- **Tk from `actions/setup-python` on macOS** has not been checked inside a PyInstaller bundle. Task 6 verifies it on the first run.

## Tasks

Sized for one subagent each, in order. Tasks 2 to 5 have no packaging dependency and can run before Task 1 lands.

1. **PyInstaller spec and entry points.** Add `tokitty.spec` with two `Analysis`/`EXE` pairs (`Tokitty`, windowed; `tokitty-hook`, windowed, entry = `hook_writer.py` inside the same never-raise wrapper) and one `COLLECT` (plus `BUNDLE` on macOS), with `datas` for `hook_writer.py` and `prices.json`. First confirm the two EXEs really do share `_internal`, and if they don't, note the size cost. Add a test that fails if any file under `[tool.setuptools.package-data]` is missing from the spec's `datas`. Pin PyInstaller and hooks-contrib in a `packaging` extra.
2. **Hook command builder.** `_build_command` returns either a shell string or an exec-form `{command, args}`, following the table in question 2. Unit tests for every row, including a frozen Windows app targeting a `\\wsl.localhost` home and a `C:\` home. `_is_tokitty_entry` must match exec-form entries (the marker is in the `tokitty-hook` path).
3. **Hook ownership and refresh.** First narrow `_is_tokitty_entry` to the exact shapes listed in question 2. Then `install_hooks_for_dir` rewrites owned hooks whose command or args differ, keeping matchers and neighbouring hooks and backing up settings first as it does now. Add `hooks_install.ensure_current(state_dir)` at startup, off the Tk thread, next to `retry_pending_hook_op`. It only rewrites owned entries already present in `settings.json` and never adds one. Tests: python to exe, exe to moved exe, no-op when identical, a user hook whose path contains "tokitty" left alone, a mixed entry with a user hook in the same event, `--uninstall-hooks` followed by a restart staying uninstalled, and a stale owned entry in `settings.local.json` reported and not duplicated.
4. **Frozen guards.** Skip the launcher file when frozen, and refuse autostart and exe-hook registration from an `/AppTranslocation/` path, with a message the Accounts dialog and the tray can show. Tests with `frozen=True` and fake executables, following `autostart.py`'s injectable style.
5. **Windowed error handling.** In the frozen GUI entry, catch anything that escapes `main`, write it to `<state_dir>/crash.log`, and exit nonzero instead of letting the bootloader show its dialog. Add a hidden `--self-check` flag that imports tkinter, pystray and PIL, loads the packaged prices, and checks that `hook_writer.py` exists beside `hooks_install`, then exits 0 or 1. CI needs it, and it's a cheaper bug-report tool than a screenshot.
6. **Release workflow.** New `.github/workflows/release.yml`: on a `v*` tag, plus `workflow_dispatch` for dry runs without publishing. Matrix: `windows-latest`, `macos-latest`, and the oldest supported `ubuntu-*`, Python 3.13. Each job builds and archives, then **extracts the archive to a fresh path and tests only the extracted copy**. It runs `--self-check` and the hook-install acceptance test from question 1 against scratch dirs, and asserts the registered `tokitty-hook` path exists (inside `Tokitty.app/Contents/MacOS/` on macOS). It times the extracted `tokitty-hook` like the spike did (1 cold, 20 warm, real payload, state file checked), logs every value, and fails over a 100 ms median. It registers autostart into a scratch location and asserts the registered path is the extracted executable. Then it uploads. A final job creates the release and attaches the three archives. `ci.yml` stays as it is.
7. **macOS Keychain job.** The test in question 6, as a `workflow_dispatch` job (or part of the release dry run), with its output recorded in the PR.
8. **README.** Install from Releases. First-launch notes per OS with the current macOS 15 flow. "Hooks from the downloaded app need Claude Code 2.1.139 or newer", "restart open Claude Code sessions after updating or moving Tokitty", and "move Tokitty to Applications before turning on autostart". Update the `python -m tokitty` instructions to present the source install as the developer path.
9. **Manual gate on Windows (Nick).** Download the dry-run artifact, run it from a new folder, add an account in the dialog, confirm the WSL home still gets the `python3` hook, and confirm the cat reacts in a restarted session. Toggle autostart and reboot.

Out of #48 and suggested as separate issues: Codex activity hooks (question 3), HTTP hooks as a no-spawn runner, and the `CodexProvider` docstring correction.
