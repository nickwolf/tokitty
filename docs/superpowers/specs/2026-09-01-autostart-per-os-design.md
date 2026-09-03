# Autostart per OS (design)

Issue: [#20](https://github.com/nickwolf/tokitty/issues/20) ("shell:startup / login item / autostart desktop entry + boot-race tolerance").

Status: design, not yet planned into tasks.

## Goal

Tokitty comes back on its own after a reboot, with no installer and no admin rights, controlled by a checkbox in the existing right-click and tray menu.

## Decided (owner, not to be re-opened)

1. Menu toggle is the primary surface, not CLI flags only. It mirrors "Show tray icon".
2. No installer, no elevation, no new dependencies. Every mechanism below is a user-scope file or registry write using the stdlib.
3. Unsigned, repo-clone deployment is the target. Frozen binaries ([#48](https://github.com/nickwolf/tokitty/issues/48)) are a future consumer of the same seam, not a prerequisite.

## Scope and non-goals

In scope: Windows, macOS and Linux registration and deregistration; the menu toggle; boot-race tolerance; the launch-command seam.

Not in scope: code signing, package managers (winget, Homebrew), the frozen artifact itself, and any change to how credentials resolve.

## Mechanisms

| OS | Mechanism | Stdlib module |
|---|---|---|
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value name `Tokitty` | `winreg` |
| macOS | `~/Library/LaunchAgents/com.nickwolf.tokitty.plist`, `RunAtLoad` true | `plistlib` |
| Linux | `~/.config/autostart/tokitty.desktop` | plain text |

Windows uses the `Run` key rather than a `.lnk` in `shell:startup` because a shortcut needs COM (pywin32), and the dependency list is `pystray` and `Pillow` only (`pyproject.toml:8`). A `.cmd` in the Startup folder avoids COM but flashes a console window on every login, which is worse than either.

macOS note: a `LaunchAgent` starts the process but does not give it `LSUIElement` behaviour. The menu bar caveat already documented in the README (a real `.app` bundle is required, Tk owns the menu) is unchanged by this work.

## The launch command seam

This is the part most likely to rot, so it gets one function and one decision point:

```
resolve_launch_command() -> list[str]
```

For a frozen build it returns the executable path alone. Detection is `getattr(sys, "frozen", False)`, the same check #48 will need.

For a repo clone it is not that simple, and the obvious answer is wrong.

**`-m tokitty` cannot be registered directly.** The package is not pip-installed in the target deployment, so it is importable only because the process starts with the repo root as its working directory. Verified on this machine with real Windows Python 3.13:

```
cd C:\Users && python.exe -c "import tokitty"
ModuleNotFoundError: No module named 'tokitty'

cd C:\Tools\tokitty && python.exe -c "import tokitty"
import OK
```

An `HKCU\...\Run` value has no working-directory field. It is a bare command string, and the process inherits a working directory that is not the repo. So registering `pythonw.exe -m tokitty` produces an entry that fails at every login, silently, with nothing on screen. That is precisely the failure this design is otherwise trying to avoid, and it would likely have shipped: it cannot reproduce when launched by hand from the repo root, which is how every manual test would run it.

This also removes the working-directory advantage a `.lnk` would have had, so it is not an argument for shortcuts.

**Resolution: generate a launcher.** Write `autostart_launcher.pyw` into the per-user state dir, pinning the path explicitly:

```python
import sys
sys.path.insert(0, r"<repo root>")
from tokitty.__main__ import main
main()
```

and register `"<abs path>\pythonw.exe" "<state dir>\autostart_launcher.pyw"`. This mirrors what `hooks_install.py` already does when it copies `hook_writer.py` into the user's config dir, so it is an established shape in this codebase rather than a new mechanism. It also makes the recorded repo path a single line in one generated file, which is easier to validate and rewrite than a registry string.

Note the launcher must import `tokitty.__main__` rather than run `tokitty/__main__.py` as a script: running the file directly puts `<repo>\tokitty` on `sys.path` instead of `<repo>`, so `import tokitty` still fails.

**Quoting.** The interpreter path routinely contains spaces (`C:\Program Files\...`). Both the interpreter and the launcher path must be quoted in the registry value. A test with a space-containing fake interpreter path is required, not optional.

Consequence to accept explicitly: with a repo clone, the registered entry depends on both the interpreter path and the clone location. Moving, renaming or deleting the clone leaves a stale entry that fails silently at login with nothing on screen to explain it. Mitigation is a validity check at startup, not self-healing. **Correction, 2026-09-01, found while planning:** an earlier draft of this section said to compare the registered command against `resolve_launch_command()` and rewrite on mismatch. That is not sufficient, because the registered command contains only the interpreter path and a fixed launcher path inside the state dir. The repo root never appears in it at all; it lives only inside the launcher file's contents. So a moved repo produces an identical registered command and the comparison would never fire.

The check therefore has two halves, and they are not symmetric:

- The launcher file is regenerated unconditionally on every startup. It is a few lines of text at a known path, so rewriting it is cheap and idempotent, and it is the only thing that actually tracks the repo root.
- The OS registration is rewritten only on mismatch, which is what catches an interpreter that moved or was upgraded in place.

Both halves are needed for the coverage this section claims (Python upgraded in place, and repo moved while tokitty still runs from the new location).

## Menu wiring and the source-of-truth problem

`menu.py` gets `autostart_enabled: Callable[[], bool]` and `on_toggle_autostart`, following the optional-pair convention already used for tray and surprise-me, so platforms or builds that cannot support it simply pass neither and the item does not render.

The important constraint is where the checkbox reads from. The OS entry is the source of truth, not `settings.json`. A bool persisted in settings would drift the moment anything outside tokitty removes the registry value or the plist, and the checkbox would then confidently show "on" for a feature that no longer runs.

But the getter cannot query the registry or filesystem directly either. `menu.py`'s module docstring is explicit: pystray evaluates these getters on its own thread when it draws the tray menu, so they must read plain-Python shadow state only. Doing I/O there would put a registry read on pystray's thread on every menu draw.

So: read the real OS state once at startup and again after every toggle, cache it in a plain bool, and have the getter return the cached value. `settings.json` gains no autostart key at all.

## Boot-race tolerance

At login the network may be down, WSL may not be up, and credentials may not be readable yet. The failure to avoid is a cat that comes up dimmed with an error and stays that way after conditions recover.

Requirement: a failed first poll at startup must be indistinguishable from any other transient poll failure, and must recover on the normal poll cadence with no user action. This needs a test that boots the app with credential resolution failing, then succeeding, and asserts the card recovers without a manual **Refresh now**.

## WSL interaction, which needs an owner decision

On the Windows plus WSL2 setup tokitty runs as a Windows process while Claude Code lives in WSL, so credential resolution probes WSL. Autostart makes that probe run at every single login.

Two known facts make this sharper than it sounds. PR #51 deliberately left the shared distro-probe cache at a 1.0s TTL as best-effort rather than an absolute "never restart a stopped distro" invariant. And `resolve_activity_sessions` still runs its own separate synchronous WSL scan instead of reusing the Accounts-manager discovery result, so a launch with no `accounts.json` can trigger two `wsl.exe` scans rather than one.

Neither is a bug today, because launches are manual and infrequent. Autostart converts them into something that happens on every boot, and on a machine where WSL is not already running that means every login wakes it.

Owner decision, 2026-09-01: accept for this work, and file the `resolve_activity_sessions` reuse fix as its own issue ([#52](https://github.com/nickwolf/tokitty/issues/52)). Autostart therefore ships with a per-boot double scan on a no-`accounts.json` Windows plus WSL2 install, knowingly.

## Testing strategy

Registration and deregistration are pure enough to test with an injected root: a fake registry accessor on Windows, a `tmp_path` `LaunchAgents` or `autostart` dir elsewhere. `resolve_launch_command` is pure and testable directly on all three platforms.

Two things the CI matrix will not catch, both to be verified by hand on Cucumber before this is called done:

- That the registered command actually launches at a real Windows login, not merely that the registry value is well-formed.
- That `pythonw.exe` produces no console window at login.

Explicit lesson carried forward from PR #51: every test on that branch was validated only under WSL and the first real Windows CI run failed four tests, all genuine platform bugs. This feature is platform-branching logic touching paths and separators, which is exactly that category. Verify against real Windows Python at `/mnt/c/Users/nickw/AppData/Local/Programs/Python/Python313/python.exe` before pushing, using `--basetemp="C:\tmp\<name>"` to route around the locked default pytest temp root.

## Open questions

1. ~~The WSL per-boot scan above: accept, or fix `resolve_activity_sessions` as part of this?~~ **DECIDED 2026-09-01 (owner): accept for #20, file separately.** The double scan is wasted work rather than a correctness bug, and on the primary machine WSL is normally already running, so the per-boot wake is mostly theoretical there. Filed as [#52](https://github.com/nickwolf/tokitty/issues/52).
2. Should `--install-autostart` / `--uninstall-autostart` CLI flags ship alongside the toggle, mirroring `--install-hooks`? Useful for headless setup and for scripting, but the menu covers the stated need.
3. On Linux, should the `.desktop` entry carry a delay or `X-GNOME-Autostart-Delay` to reduce the boot race, or is poll-retry tolerance sufficient on its own?
