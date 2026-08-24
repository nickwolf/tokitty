# Accounts setup UI (design)

**Issue:** [#34](https://github.com/nickwolf/tokitty/issues/34) First-run accounts.json setup UI

**Date:** 2026-08-24

## Goal

Today running tokitty with more than one Claude Code account means hand-writing `accounts.json`. This adds an in-app Accounts manager so nobody has to touch the file by hand: add an account, rename it, remove it, all from the tray/right-click menu.

## Decided (owner, not to be re-opened)

1. **Persistent manager, not a first-run-only wizard.** A new "Accounts..." entry on the right-click menu opens the same dialog at any time, not just on first run. Add, Rename, Remove. No cap on account count.
2. **Auto-opens on first run only when it has to.** If `accounts.json` is absent and discovery finds more than one usable credential source under today's resolution precedence, the manager opens automatically at startup instead of raising `AmbiguousCredentialsError` the way `find_wsl_credentials()` does today. See First-run auto-open below for the precedence check this actually needs and for why "at most once" was the wrong way to describe it: it reopens on every launch for as long as `accounts.json` stays absent, including after the user closes the dialog without saving.
3. **No live reload.** Changes take effect on restart, matching the issue's own note: "restart still required after changes (no live reload, by design)". The dialog says so. No hot-adding of panes, pollers, or watchers.
4. **New accounts get a random look from the existing curated system.** `randomize.random_look(colorways, patterns, rng=None) -> Tuple[str, str]` (`randomize.py:10-13`) already does this; the manager calls it with `list(sprites.COLORWAYS)` and `list(sprites.PATTERNS)`. No new randomization code.
5. **Two separate name fields, and the UI must not blur them.** `accounts.json`'s `"name"` is a stable identity slug, assigned once at setup and never edited afterward by this UI; it is what `customization_key()` binds a pane's saved look to. `customization.json`'s `Customization.label` (`customize.py:36-40`) is the free-text display name, changed any time through the existing Rename... item. Rename therefore never touches the identity key and can never orphan a look.
6. **The identity-key bug gets a root-cause fix**, not a migration layered on top of the current behavior. See below.
7. **Removing an account keeps its `customization.json` entry, orphaned.** Re-adding an account with the same slug restores its exact look. The UI never deletes a customization entry on removal.
8. **Panes lay out in a grid**, replacing the current single vertical column, capped at 4 rows.
9. **`accounts.json` gets a writer**, `save_accounts(state_dir, accounts)` in `accounts.py`, the first one this file has ever had.
10. **Discovery gets a list-returning sibling** that never raises, plus a manual "add by path" row, because no glob can be guaranteed to catch every layout. Discovery is WSL-only: there is no native Linux multi-directory discovery in the source, so Linux gets manual Add only unless a later issue adds one. See Discovery below for the actual fix; it is not the glob.
11. **The WSL running-distros probe becomes shared and cached** across all accounts, folded into this issue because it is exercised by the same multi-account testing this issue already needs.
12. **Hooks stay consistent with `accounts.json`** on every add/remove, using the existing `install_hooks_for_dir`.
13. **macOS shows a virtual read-only account when there's no credentials file on disk.** When a Keychain-only setup has no file to represent, the manager shows a "Default macOS account (Keychain)" row instead: no Add, no Remove, Rename edits the `"default"` customization key directly. An account with a real credentials file is shown normally. Full multi-account support on macOS is a separate prerequisite, not part of this issue.
14. **Removing the last account is disabled.** Tokitty always has at least one pane; there is no zero-account state. An empty `accounts.json` account list is today indistinguishable from "no file" and would silently restore automatic default credential resolution, and a real, deliberate zero-account state would need a non-pane manager shell, tray defaults, empty hit-testing, no pollers or watchers, and an N=0 case in the grid formula, which is a materially larger feature than this issue. N=1 is 1 col x 1 row, 300x128; N=0 cannot occur.

## Scope and non-goals

In scope: the Accounts manager dialog (add/rename/remove), first-run auto-open, the grid layout, the identity-key fix and its migration, the `accounts.json` writer, broadened discovery plus manual add, the shared distro probe, and hooks consistency on every mutation.

Out of scope, and why:

- **Sprite raster caching.** `Pane._draw_frame` (`ui.py:228-253`) does `canvas.delete("cat")` then one `create_rectangle` per non-transparent sprite pixel, about 340 per frame, on an 800ms timer (`FRAME_INTERVAL_MS = 800`, `ui.py:36`, driven by `TokittyWindow._animate` at `ui.py:487-490`). `get_frames` (`sprites.py:455-483`) has no cache either. That is roughly 343 canvas operations per pane per 800ms, about 1,715 at N=5. This is pre-existing and orthogonal to accounts, and issue #28's PySide6 rewrite would subsume it. It gets its own issue.
- **Poll staggering.** Every `Poller.start()` fires its first fetch immediately (`poller.py:68-77`), so all N accounts hit the usage endpoint at once. `POLL_INTERVAL = 120.0`, `WAKING_POLL_INTERVAL = 20.0` (`poller.py:13-14`), per-account exponential backoff `BACKOFF_INITIAL = 30.0` to `BACKOFF_MAX = 600.0` (`poller.py:15-17`), with no shared throttle across accounts. Known, and out of scope for this issue.
- **Multi-account support on macOS.** See point 13 above and the macOS section below.
- **Code signing, autostart, packaging.**

There is no hard cap of 2 anywhere in the current code. `dual` (`__main__.py:404`) is just `len(accounts) > 1`, and the "two-account mode" phrasing in the `credentials.py:106` comment is stale naming, not an enforced limit. `pane_count = len(accounts)` already (`__main__.py:383`); the literal `2` there only fires for the `TOKITTY_DEBUG_ACCOUNTS == "2"` dev toggle. Per-account cost today is two daemon threads (`Poller` at `poller.py:52`, `ActivityWatcher` at `activity_watcher.py:86`), plus, for WSL accounts, one `wsl.exe cat` subprocess per poll, since `CredentialLoader` caches Keychain sources only and reads file/WSL sources through on every call (`credentials.py:229-251`).

## Data model and file formats

### `accounts.json`

Schema, unchanged from what `load_accounts` already reads: `{"accounts": [{"name": str, "config_dir": str}, ...]}`. The writer does not write the legacy `"coat"` key; it stays read-only legacy, translated via `sprites.LEGACY_COAT_MAP`.

`load_accounts` (`accounts.py:26-49`) defaults a missing `"name"` to `f"account {index}"`, positionally, by list order. This is the reason `"name"` cannot be a user-facing, freely-renamed field: if it were, removing a middle account would shift every later account's index and silently make it inherit the wrong saved look. An explicit slug, written once at setup time by this new UI, closes that class of bug at the source rather than working around it.

`load_accounts` today collapses four different situations into the same `None` return: file absent (`accounts.py:29`), parse failure (`accounts.py:33`), `"accounts"` present but not a list (`accounts.py:36`), and a valid, present file whose account list is empty or entirely invalid, via `return accounts or None` (`accounts.py:49`). The new rule for `customization_key` (see The identity-key fix and migration below) is "`default` only when there is no `accounts.json` file at all", and that rule can't be made real until these four cases are told apart. The loader is changed to report four distinct states: absent, valid-and-non-empty, valid-but-empty, and malformed.

`load_accounts` also accepts a missing or duplicate `"name"` with no validation (`accounts.py:39-49`), and `customization.json` is a plain dict keyed by that same string (`customize.py:86`), so two accounts with the same name would silently share one look and one label. See Identity slug scheme below for how the writer prevents this.

### `accounts.py` writer (new)

`save_accounts(state_dir, accounts)` is the first writer this file gets; `customization.json`, `settings.json`, `position.json`, and Claude Code's own `settings.json` all already have one, `accounts.json` does not. It copies `save_customization`'s tmp-file-plus-`os.replace` pattern exactly (`customize.py:103-108`):

```python
tmp_path = path.with_suffix(path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
os.replace(tmp_path, path)
```

### Identity slug scheme

Uniqueness alone is not enough for the identity key: a numeric suffix or a basename-derived slug changes after a remove and re-add, and `customization.json` records no path-to-slug relationship, so an account has no way to recover its old look once its slug changes.

- Canonicalize a logical locator for the config dir: a WSL UNC path becomes `wsl:<casefolded distro>:<normalized POSIX config dir>`; a Windows-local path becomes its normalized absolute form via `normcase`; a POSIX path becomes its absolute, normalized real path; a relative path is rejected outright.
- The slug is `acct-v1-<full lowercase SHA-256 of the canonical locator>`.
- An additive `identity_history` mapping from locator digest to selected slug is persisted, so an existing account can register its current slug before removal, and a collision choice stays stable across re-adds.
- On collision with a different locator, hash `locator + "\0" + counter`, persist the chosen result in `identity_history`, and reuse it forever afterward. Never pick a `-2` suffix by looking only at the currently active account list, since a removed account's old slug is invisible there.
- If the same canonical locator is already an active account, Add reports "already added" rather than creating a second pane for it.

The slug is opaque and must never be shown to the user as fallback text. See The identity-key fix and migration below for the related `initial_label` fix.

### `customization.json`

Unchanged shape. `Customization.label` (`customize.py:36-40`) remains the free-text display name, edited only through Rename..., never through the Accounts manager.

## Discovery

`_credentials_paths_in_distro(distro, run=subprocess.run) -> List[str]` (`wsl_probe.py:66-85`) already returns a list and is reusable as-is.

`find_wsl_credentials(run) -> Tuple[str, str]` (`wsl_probe.py:88-114`) collapses discovery across all distros to exactly one match, or raises `AmbiguousCredentialsError` on more than one and `CredentialsError` on zero. There is no all-distros sibling that returns the full list without collapsing or raising. One is added for the manager to use.

**The real defect is not the glob, it's the path helpers, and the two are one change.** The discovery script at `wsl_probe.py:15` globs `/home/*/.claude/.credentials.json` only, so it misses a layout like `~/.claude-work` and would present an incomplete account list. Broadening the glob looks like the fix, but it is not, and shipping it alone is actively worse than doing nothing.

`_wsl_home_windows_style` (`wsl_probe.py:117-119`) builds the config directory with `wsl_credentials_path.rsplit("/.claude/", 1)[0]`. Python's `rsplit` returns the whole input string unchanged when the separator is not present. For `/home/nick/.claude-work/.credentials.json` there is no `/.claude/` substring (it's `/.claude-work/`), so the rsplit falls through and returns the full path unsplit. `wsl_config_dir_from_credentials` (`wsl_probe.py:131-138`) then appends `\.claude` to that unsplit result, producing `\\wsl.localhost\<distro>\home\nick\.claude-work\.credentials.json\.claude`, a path that does not exist. `wsl_sessions_dir_from_credentials` (`wsl_probe.py:122-128`) has the identical defect.

Today this bug is dormant, because the glob at `wsl_probe.py:15` never surfaces a `.claude-work` path in the first place, so the broken helpers never run on one. Broadening the glob without fixing the helpers does not merely fail to add the second account, it activates a latent bug: discovery would now return the `.claude-work` credentials path, and the path helpers would turn it into the garbled UNC path above, silently pointed at nothing. That is why the glob change and the path-helper fix are one change, not two, and must land together.

**Fix:** derive the config directory as the credentials file's parent, independent of its basename, instead of splitting on a hard-coded `/.claude/`:

```python
config_posix = str(PurePosixPath(credentials_path).parent)
windows_style = config_posix.lstrip("/").replace("/", "\\")
config_unc = rf"\\wsl.localhost\{distro}\{windows_style}"
sessions_unc = config_unc + r"\tokitty\sessions"
```

The backslash is built outside the f-string expression on purpose: backslashes inside f-string expressions are a `SyntaxError` before Python 3.12, and the CI matrix runs 3.10. Both `wsl_config_dir_from_credentials` and `wsl_sessions_dir_from_credentials` need explicit tests against a `.claude-work`-shaped path, not just the existing `.claude` case.

Because no glob can be guaranteed to catch every layout, the manager always offers a manual "add by path" row next to whatever discovery finds; see Manual path validation below.

**Scope: discovery is WSL-only.** There is no native Linux multi-directory discovery in the source today. On Linux, the manager offers manual Add only, unless a separate discovery design is added in a later issue. This is a real scope limit, not an oversight to gloss over.

## Manual path validation

This can't stay an open question: `install_hooks_for_dir` does `mkdir(parents=True, exist_ok=True)` on whatever string is entered (`hooks_install.py:213`), so a typo in the manual "add by path" row creates a real directory tree and installs settings into it, then fails every credentials poll afterward with no clear signal of why. Native resolution only rejects a missing credentials file much later, at `credentials.py:117`.

Bad inputs the validation has to handle: `~/.claude-work` left unexpanded; a relative path; a POSIX path like `/home/nick/.claude-work` typed into the Windows process, which gets read as Windows-local because only UNC forms are recognized as WSL; the credentials file itself entered instead of its containing directory; and the same real directory written once as `\\wsl$\...` and once as `\\wsl.localhost\...`, which should resolve to the same account, not two.

**Before any persistence or hook call:** normalize the input and require an absolute config directory; canonicalize WSL path aliases (`\\wsl$` and `\\wsl.localhost` to the same canonical form, see Identity slug scheme above); require `.credentials.json` to exist in that directory and parse as a `claudeAiOauth` object; accept an expired token, since token validity is not the same thing as account identity; reject a canonical duplicate of an already-active account; and label the field "Claude config directory" in the UI, not "path", so the user knows what's expected. For a WSL path, validation goes through the distro-aware subprocess reader, not by touching the UNC path directly from the Windows process.

## The dialog and its flows

Modeled on `TokittyWindow._open_customize_dialog` (`ui.py:404-438`): a `tk.Toplevel(self.root)` with title, `transient`, matching background, `resizable(False, False)`, and a grid of rows. `_open_rename_dialog` (`ui.py:440-446`), which uses `simpledialog.askstring`, is the template for the simpler prompts. New module: `accounts_ui.py`. The manager Toplevel is a singleton: opening "Accounts..." while one is already open raises the existing window rather than creating a second one, and the dialog reloads `accounts.json` from disk immediately before every save, since two independently opened dialogs could otherwise overwrite each other's changes with a stale account list.

**Menu entry.** `MenuItem` (`menu.py:17-24`) dataclasses are built in `menu.build_menu` (`menu.py:26-76`); the single menu model feeds both the Tk right-click menu and the pystray tray menu, so one new "Accounts..." entry appears in both. Window callback seams are assigned post-construction in `run_gui`, the same way `window.on_refresh_requested` (`__main__.py:451`), `window.on_randomize` (`__main__.py:501`), `window.on_quit` (`__main__.py:521`), and `window.on_toggle_tray` (`__main__.py:530`) already are. Tray actions already marshal onto the Tk thread via `root.after(0, ...)`, so opening a Toplevel from the tray menu is safe. Separately, the ambiguous-credentials status text (`__main__.py:289`) currently tells the user to set an environment variable; it needs to point at Accounts... instead, now that the manager is the supported fix.

**Add.** User supplies a config dir, either picked from discovery or typed into the manual "add by path" row and validated per Manual path validation above. The manager canonicalizes the path, assigns a unique identity slug per the Identity slug scheme, rolls a random colorway/pattern via `random_look`, then follows Write ordering and crash consistency below rather than calling `install_hooks_for_dir` first.

**Rename.** The manager's Rename operates on the stable slug, `rename_account(slug, label)`, loading and saving `customization.json` by slug directly, never through `accounts.json`'s `"name"`. This has to be a slug-keyed operation and not a reuse of the existing pane-index Rename, because manager rows do not map to live panes: adding account B and renaming it before restart would index past the live panes, and removing account A and renaming the new first row would edit A's customization instead of B's, since `self.panes[pane_index]` (`ui.py:440`) and `units[pane_index]` (`__main__.py:453`) both dereference by position. Updating a matching live pane, if one exists, is optional and goes through a slug-to-unit lookup, never through row position. The existing pane-index callback is unchanged and stays as the right-click Rename on a live pane.

**Remove.** Deletes the account's entry from `accounts.json` via `save_accounts`, then uninstalls hooks from the captured config dir immediately; see Write ordering and crash consistency below for why this can't be deferred to a later CLI uninstall. Leaves its `customization.json` entry in place, orphaned, so re-adding the same slug later restores the exact same look. Remove is disabled when the account being removed is the last one; see Decided.

**First-run auto-open.** See First-run auto-open below; the intent here is unchanged, the mechanics are corrected there.

**Restart notice.** The dialog distinguishes two different restart needs, because they are not the same thing: restarting Tokitty is what makes new panes appear and pollers and watchers start, while restarting any already-open Claude Code session is what makes a hook change actually apply, since hooks are not hot-reloaded (`hooks_install.py:314`). Exact wording for both notices is still open; see Open questions.

## Write ordering and crash consistency

The obvious order, install hooks then save `accounts.json`, is not crash-consistent, and none of the failure modes below are hypothetical: `install_hooks_for_dir` (`hooks_install.py:195-248`) does not convert filesystem exceptions to a `ConfigDirResult`. An `OSError` or `PermissionError` raised from `mkdir`, `copy2`, or the settings write propagates raw to the caller. Separately, `hooks_install.py:213-214` runs `mkdir(parents=True, exist_ok=True)` and then `shutil.copy2` of `hook_writer.py` before the hook-shape validation at `hooks_install.py:221-233` runs, so an install that later fails validation has already created the directory and overwritten `hook_writer.py`. The manager has to treat `result.ok == False` and a raised exception as two separate cases, and has to tolerate the directory and `hook_writer.py` existing even after an aborted install.

**Order:** write `accounts.json` first, as the durable desired state, then perform the hooks side effect.

1. Save `accounts.json` via `save_accounts` with the new or removed entry.
2. Atomically persist a pending hook operation (add or remove, containing the captured config dir path) so a crash or failure between steps leaves a record of what still needs to happen. Clear this record only once the hook operation succeeds.
3. Run `install_hooks_for_dir` / `uninstall_hooks_for_dir` off the Tk thread, so a slow filesystem or a stuck `wsl.exe` call doesn't freeze the UI.
4. At next startup, or the next time the manager is opened, retry any pending hook operation still on disk.

This also fixes a pre-existing, accounts-unrelated bug that the manager would otherwise hit repeatedly: `_write_settings` (`hooks_install.py:159-162`) is `open(path, "w")` followed by `json.dump`, with no tmp file and no `os.replace`, so a crash or disk error mid-write truncates the user's live Claude Code `settings.json`. It is made atomic with the same tmp-file-plus-`os.replace` pattern `save_accounts` already uses.

Remove uninstalls hooks immediately from the captured path rather than leaving cleanup for a later CLI uninstall. "Leave it until the user runs the CLI uninstall" does not work: `get_config_dirs()` (`hooks_install.py:96-118`) reads `accounts.json` to decide what to uninstall, and a config dir that Remove has already deleted from `accounts.json` is invisible to it, so a later global uninstall can't find it to clean up. This resolves the spec's former open question about remove-side hook behavior.

## First-run auto-open

Auto-open can misfire in a case the original design didn't cover: resolution without an explicit `config_dir` checks `TOKITTY_CREDENTIALS`, then `~/.claude`, then Keychain, and only then WSL (`credentials.py:128-142`). So finding two WSL credential files is not the same thing as today's ambiguous case whenever an override or a native credential already wins resolution before WSL is even consulted; auto-open has to run its own check against that same precedence order, not just count WSL matches.

Auto-open can also stall startup. `find_wsl_credentials` loops every installed distro serially (`wsl_probe.py:88-114`) with a 10-second timeout per call (`wsl_probe.py:28` and `wsl_probe.py:76`), and can start a stopped distro to check it, including entries like `docker-desktop` that have nothing to do with Claude Code. `resolve_activity_sessions(None)` already does this same work synchronously on the Tk startup thread (`__main__.py:130`, called at `__main__.py:422`), so a separate first-run scan would duplicate it rather than reuse it.

Other constraints on the mechanics: the `TOKITTY_DEBUG_ACCOUNTS` branch (`__main__.py:388`) must bypass auto-open and discovery entirely, not just skip the dialog. The 5 `gui`-marked tests construct `TokittyWindow` directly and never call `run_gui` (verified: `test_smoke_gui.py:44` and `test_ui_layout.py:95,120,138,158`), so startup logic has to live in `run_gui`, not in the constructor, or those tests start touching WSL. Discovery must run only after `tk.Tk()` succeeds, so a headless launch fails for lack of a display before it ever probes WSL. There is no tray-only mode today: the root window is always created and the tray is added afterward, so the spec should not be written as if a tray-only path needs preserving.

**"At most once per install" is false as originally written.** If the user closes the manager without saving, `accounts.json` stays absent, and auto-open reopens on the next launch, and every launch after that, for as long as the file remains absent. That's a direct consequence of the "auto-opens whenever `accounts.json` is absent" rule in Decided, not a bug to fix, so dismissing the dialog does not persist as a choice: it reopens again next time, the same as if the user had never seen it.

**Design:** a pure, injectable startup-decision function that respects credential precedence and debug mode, so it can be unit-tested without touching WSL or Tk. One asynchronous discovery operation is launched after the root window exists, and its result is reused for activity resolution rather than scanned twice. An incomplete scan (timeout, distro error) is reported distinctly from a scan that completed and found zero or one match.

## Grid layout

Current layout is a single column: `card_height(pane_count) = PANE_HEIGHT * pane_count` (`ui.py:39-40`), `PANE_HEIGHT = 128` (`ui.py:20`), `CARD_WIDTH = 300`. Panes are placed with `frame.place(x=0, y=i * PANE_HEIGHT)` (`ui.py:284-286`). Height is unbounded and there is no scrolling: N=5 is already 640px tall, N=8 is 1024px.

New layout: `cols = ceil(N / 4)`, `rows = ceil(N / cols)`, filled row-major. Pane `i` sits at `x = (i % cols) * CARD_WIDTH`, `y = (i // cols) * PANE_HEIGHT`.

Worked examples:

- N=0: cannot occur, see Decided; Remove is disabled on the last account, so the grid formula never has to define a zero case
- N=1: 1 col x 1 row = 300x128
- N=4: 1 col x 4 rows = 300x512
- N=5: 2 cols x 3 rows = 600x384
- N=8: 2 cols x 4 rows = 600x512
- N=9: 3 cols x 3 rows = 900x384
- N=12: 3 cols x 4 rows = 900x512

Height never exceeds 512px; width grows instead.

`card_height()` becomes a size/grid calculation that returns both dimensions. `root.geometry()` must take the computed width in place of the constant `CARD_WIDTH` everywhere it's used at the window level: `ui.py:296`, `ui.py:464`, `ui.py:469`, and `ui.py:473`. Per-pane uses of `CARD_WIDTH`, such as status text wrapping and label placement inside a single pane, are unrelated and stay unchanged.

**This change breaks hit-testing, and the fix has to land with it.** `pane_index_at(y, pane_count)` (`ui.py:43-46`) is `min(y // PANE_HEIGHT, pane_count - 1)`, a function of `y` only, and its call site (`ui.py:390`) passes only `y_relative`. That was correct for a single column, where x never mattered, but the grid adds a second axis it doesn't know about. At N=5, right-clicking pane 1 at x=350, y=50 selects pane 0 instead. Fix: store `self._width`, `self._height`, and `self._cols` on the window; hit-test as `row * cols + col`, computed from root-relative x and y together; return `None` for a blank cell in a ragged final row and show only global menu actions there.

`clamp_position` (`geometry.py:8-26`) already takes width and height and clamps the whole rectangle, and `ui.py:469` already passes `self._height`. It needs the new computed width passed through and nothing else changed. Verified: `screen_h=1080`, `height=1024`, saved `y=400` returns `y=32`, bottom edge 1056, on-screen. No algorithm change is needed here.

**Accepted, not a defect:** with rows capped at 4 and columns growing to fit, width is unbounded. At N=17 the window is 1500px wide, and `clamp_position` (`geometry.py:16`) only anchors an oversized window at (0,0), it doesn't shrink or wrap it. The owner has explicitly accepted this ceiling. Paging or scrolling is not required work for this issue; it's recorded here as a known limit, not a TODO.

## The identity-key fix and migration

Today `customization_key(account)` (`__main__.py:406-407`) is:

```python
return account.name if (dual and account is not None) else SINGLE_KEY
```

where `dual = bool(accounts) and len(accounts) > 1` (`__main__.py:404`) and `SINGLE_KEY = "default"` (`customize.py:29`).

Consequence today: going from 1 account to 2 orphans the existing cat's look, because the key switches from `"default"` to `account.name`. Going from 2 accounts back to 1 orphans it again in the other direction.

**New rule:** the key is `account.name` whenever an account exists at all. `"default"` is used only when there is no `accounts.json` file whatsoever.

**Migration is not one case.** A single "if there's exactly one account and no slug entry yet, copy `default` across" migration is sound for a user who has always run a single account, and silently wrong for anyone who has ever had two. The table below covers the upgrade states that actually occur:

| Upgrade history | On-disk state | Outcome under a single-case migration |
|---|---|---|
| Always one configured account | `default` set, no slug entry | Sound, copy succeeds |
| Previously 2 accounts, now 1 | `default` holds the current singleton's look, an older slug entry from the second account also still exists | Migration skips because a slug entry already exists; the stale older look silently replaces the current one. Broken. |
| No file, then manager adds one account | `default` exists from before, Add creates a new random slug entry | Migration skips because the slug already exists. Broken. |
| No file, then manager adds two accounts | `default` exists from before, two random slug entries, no singleton left | No singleton migration runs at all. Broken. |
| Historical 1 to 2 accounts | `default` is stale, both slug entries are current | Ownership of `default` is not recoverable from disk either way |

So the migration has to do more than the one case:

- Run a versioned migration before the manager is allowed to mutate account count, so partial or repeated runs can be told apart from a first run.
- For an existing singleton, migrate `default` even when a slug entry already exists, archiving the old slug's value rather than discarding it, since the 2-to-1 row above shows discarding is actively wrong.
- Record the migration version (or move/remove `default` once consumed) so a later run can't overwrite a slug edit the user made in between.
- When the manager converts an implicit default account into an explicit one, transfer `default` to the credential source that account currently resolves to, and randomize a look only for genuinely new accounts, not for the one that already existed.
- Do not guess ownership of `default` in an existing multi-account file; the historical-1-to-2 row above shows there's no way to recover that from disk, so the migration should not try.

`initial_label` (`__main__.py:352-359`) falls back to `account.name` when `Customization.label` is blank and `dual` is true. Once `account.name` is the opaque slug from the Identity slug scheme, that fallback would show the raw hash to the user. `initial_label` stops using the slug as visible fallback text, and the migration seeds `Customization.label` from whatever visible name the account had before, so legacy accounts keep a readable name instead of falling back to a hash.

## Hooks consistency

`hooks_install.get_config_dirs()` (`hooks_install.py:96-118`) reads `accounts.json` directly, so any write from the manager that isn't matched by a hooks update leaves hooks installed for the wrong set of config dirs. `install_hooks_for_dir(config_dir) -> ConfigDirResult` (`hooks_install.py:195`) is importable, takes a plain string, returns a result object, and does not print, so the manager calls it directly for each added account. `install_hooks()`/`uninstall_hooks()` (`hooks_install.py:301, 319`) are the CLI-only printing wrappers and stay CLI-only. `hooks_install.py:213-214` does a `mkdir` and a `shutil.copy2` of `hook_writer.py` into the config dir on every install, so each Add runs that copy; see Write ordering and crash consistency above for what has to change about the order this runs in and how failures are handled.

## Shared distro probe

Today each `ActivityWatcher` calls `list_running_distros` (`wsl_probe.py:38-63`) independently, which shells out `wsl.exe --list --running --quiet` once per tick, at `FAST_INTERVAL_S = 1.0` while its account is active (`activity_watcher.py:119-123`, `SLOW_INTERVAL_S = 20.0` otherwise). The answer is identical for every account, since which distros are running is global state, not per-account state. At N=5 active accounts that is 5 `wsl.exe` process spawns every second, forever.

`list_running_distros` returns `[]` both when nothing is running and on `OSError` or `TimeoutExpired`, and its own docstring warns callers not to conflate the two. A cache built naively on top of it can't preserve that distinction, and a naive cache has real hazards beyond that: without a lock, every cold watcher thread misses at once and each spawns its own `wsl.exe`; a failed refresh can overwrite a good result; every watcher whose refresh failed then publishes idle and drops to the 20-second interval (`activity_watcher.py:98`); a stale positive can let a watcher touch a UNC path after its distro has actually stopped, silently booting it back up, which is exactly what the guard at `activity_watcher.py:123` exists to prevent; and the subprocess call can block up to 10 seconds while watcher shutdown only waits 5 (`activity_watcher.py:89`).

**Design:** one process-scoped `RunningDistroProbe`, injected into every watcher rather than constructed by each one.

- `threading.Condition` for single-flight refresh, so concurrent callers coalesce into one `wsl.exe` call instead of a thundering herd.
- `time.monotonic` for all timing.
- An immutable `frozenset` snapshot as the published result.
- A typed result distinguishing confirmed, empty, and unknown-or-error, so the cache never collapses "confirmed nothing running" and "the check failed" the way the raw function does.
- Successful TTL of 1.0 second measured from completion of the refresh, matching `FAST_INTERVAL_S`.
- Failure backoff of 20 seconds.
- Subprocess timeout reduced from 10 seconds to about 2 seconds.
- Never reuse a stale positive result after a refresh failure; a failure invalidates the last known-good snapshot rather than extending its life.

**The tension is real and has to be decided, not assumed away.** A 1-second cache means up to 1 second of positive staleness is possible by construction. If "never restart a stopped WSL distro" is meant as an absolute invariant rather than a best effort, the only way to honor it is to cache non-empty snapshots only long enough to coalesce concurrent callers, around 250ms, and accept that watchers can end up calling the probe more than once per second when their phases drift apart. This tradeoff belongs to whoever implements it as an explicit decision, not as something this spec quietly settles by picking the 1-second number.

## macOS behavior

A Keychain-only account can't be represented in `accounts.json` at all: `KeychainCredentialsSource` (`credentials.py:41-48`) has no config dir field, and `accounts.py:40` requires a truthy `config_dir` on every entry. Writing something like `~/.claude` for a Keychain-only account to satisfy that requirement would break a currently working pane, since explicit resolution by config dir is file-only (`credentials.py:100`) and would never fall through to Keychain.

So on macOS, when there is no credentials file on disk, the manager shows a virtual, read-only "Default macOS account (Keychain)" row instead of a normal account entry: it must not create `accounts.json`, Add and Remove are disabled for it, and Rename edits the `"default"` customization key directly rather than going through `rename_account(slug, label)`. An account that does have a real credentials file on disk is represented normally, the same as on Windows or Linux. Multi-account support on macOS, meaning a second Keychain-backed identity, is a separate prerequisite and stays out of scope for this issue.

## Testing strategy

No existing test exercises more than 2 accounts: `test_accounts.py:19` `test_two_accounts_parsed_in_order` uses exactly 2, `test_hooks_install.py:86` uses exactly 2 config dirs, `test_ui_layout.py:16-19` only asserts `card_height(1) == 128` and `card_height(2) == 256`. The max N anywhere in the suite today is 2.

New coverage needs to reach N >= 3 for: accounts parsing, the `save_accounts` round-trip, `get_config_dirs()`, and the new grid sizing.

The suite currently collects 413 tests with 5 deselected; the 5 are `gui`-marked and excluded by `pyproject.toml:27`'s `addopts = "-m 'not gui'"`, run separately via `xvfb-run -a pytest -m gui`. Any new test that constructs a real `tk.Tk()` must be marked `@pytest.mark.gui`, or it reddens the headless CI matrix.

Three more areas need dedicated coverage that the original plan didn't call out: `.claude-work`-shaped input to both `wsl_config_dir_from_credentials` and `wsl_sessions_dir_from_credentials`, since that's the exact case the discovery fix exists for; concurrency tests for the shared `RunningDistroProbe`, covering the single-flight refresh and the failure-doesn't-clobber-a-good-result case; and one test per row of the migration table in The identity-key fix and migration above, since each row is a distinct on-disk state with a distinct correct outcome.

## Open questions

- Exact wording of the two restart notices (Tokitty restart for panes, Claude Code session restart for hooks) and of the macOS read-only account message.
- The shared distro probe's TTL-versus-staleness tradeoff from Shared distro probe above: whether a 1-second cache is an acceptable amount of positive staleness, or whether the "never restart a stopped distro" invariant is absolute enough to require the shorter coalescing-only window instead.
