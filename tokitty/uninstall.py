"""Remove Tokitty from this computer (--uninstall, and Settings > General).

build_plan lists what would go, without touching anything, so the GUI dialog
and the CLI prompt show the same concrete paths. execute then does each item
on its own: a failure is recorded and the rest still run. Hooks reuse
hooks_install.uninstall_hooks_for_dir, the plugin gui_uninstall and autostart
the backend's deregister, the same code the three --uninstall-* flags run.

Release folders are never touched, and neither is anything outside the
per-user state directory and each account's <config-dir>/tokitty/.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from tokitty.paths import state_dir_path

RESTART_NOTE = ("Close or restart open Claude Code and Codex sessions: until they restart "
                "they keep calling the removed hook script.")

HOOKS = "hooks"
HOOK_FILES = "hook_files"
STREAMDOCK = "streamdock"
AUTOSTART = "autostart"
RUNNER_LINK = "runner_link"
LAUNCHER = "launcher"
STATE = "state"

# Set by the GUI action; run_qt purges once the lock is released and the
# window's own shutdown writes are done (see purge_requested).
_purge_on_exit = False


@dataclass(frozen=True)
class Item:
    kind: str
    label: str
    path: str
    # (config_dir, provider) for the hooks and hook_files kinds
    account: Optional[Tuple[str, str]] = None


@dataclass
class Plan:
    items: List[Item]
    state_dir: Path
    purge: bool
    appdata: Optional[Path] = None
    backend: object = None
    config_dirs: List[Tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Outcome:
    item: Item
    ok: bool
    message: str


def request_purge_on_exit() -> None:
    global _purge_on_exit
    _purge_on_exit = True


def purge_requested() -> bool:
    return _purge_on_exit


def _is_link(path) -> bool:
    from tokitty.runner_link import _is_link as is_link

    return is_link(path)


def _remove_link(path) -> None:
    # A Windows junction is removed like an empty directory, a symlink like a file.
    if sys.platform == "win32":
        os.rmdir(str(path))
    else:
        os.unlink(str(path))


def _hook_dir(config_dir: str) -> Path:
    from tokitty import hooks_install

    return Path(hooks_install._local_config_path(config_dir)) / "tokitty"


def _autostart_present(backend) -> bool:
    if backend is None:
        return False
    try:
        return bool(backend.is_registered())
    except (OSError, ImportError):
        return False


def build_plan(
    state_dir,
    *,
    purge: bool = False,
    config_dirs: Optional[List[Tuple[str, str]]] = None,
    backend="default",
    appdata=None,
) -> Plan:
    """What --uninstall would remove, reading the machine but changing nothing.
    config_dirs, backend and appdata default to the real ones; tests pass
    their own. Only things that exist are listed, apart from each account's
    hook entries, which are listed for every account that can have them."""
    from tokitty import autostart, hooks_install
    from tokitty.streamdock import install as sd

    state_dir = Path(state_dir)
    if config_dirs is None:
        config_dirs = hooks_install.get_config_dirs(state_dir)
    if backend == "default":
        backend = autostart.get_backend()
    if appdata is None:
        appdata = sd._real_appdata()
    items: List[Item] = []
    for config_dir, provider in config_dirs:
        settings_file = hooks_install._hook_target(provider).settings_file
        shown = Path(hooks_install._local_config_path(config_dir)) / settings_file
        items.append(Item(HOOKS, "Hooks", str(shown), (config_dir, provider)))
        hook_dir = _hook_dir(config_dir)
        if hook_dir.is_dir():
            items.append(Item(HOOK_FILES, "Hook files", str(hook_dir), (config_dir, provider)))
    plugin = sd._plugins_dir(appdata) / sd.PLUGIN_FOLDER
    if sd.install_supported() and plugin.is_dir():
        items.append(Item(STREAMDOCK, "Stream Dock plugin", str(plugin)))
    if _autostart_present(backend):
        items.append(Item(AUTOSTART, "Start at login", backend.location()))
    link = state_dir / "current"
    if _is_link(link):
        items.append(Item(RUNNER_LINK, "Hook runner link", str(link)))
    launcher = state_dir / autostart.LAUNCHER_FILENAME
    if launcher.is_file():
        items.append(Item(LAUNCHER, "Autostart launcher", str(launcher)))
    plan = Plan(items, state_dir, False, appdata, backend, list(config_dirs))
    return with_purge(plan) if purge else plan


def with_purge(plan: Plan) -> Plan:
    """The same plan, also deleting the state directory (accounts, looks, theme)."""
    if plan.purge:
        return plan
    item = Item(STATE, "Settings folder (accounts, looks, theme)", str(plan.state_dir))
    return Plan([*plan.items, item], plan.state_dir, True, plan.appdata, plan.backend, plan.config_dirs)


def format_plan(plan: Plan) -> List[str]:
    lines = ["Tokitty will remove:"]
    for item in plan.items:
        lines.append(f"  {item.label}: {item.path}")
    lines.append(RESTART_NOTE)
    lines.append("Release folders are not touched. Delete them yourself afterwards.")
    return lines


def format_outcomes(outcomes: List[Outcome]) -> List[str]:
    return [f"{'ok' if o.ok else 'FAILED'}: {o.item.label}: {o.item.path}"
            + (f" ({o.message})" if o.message else "") for o in outcomes]


def _protected_inside(path: Path) -> Optional[str]:
    """The running program, if it lives inside path: never delete that."""
    import tokitty

    root = os.path.normcase(os.path.realpath(str(path)))
    for candidate in (sys.executable, str(Path(tokitty.__file__).parent)):
        real = os.path.normcase(os.path.realpath(candidate))
        if real == root or real.startswith(root + os.sep):
            return candidate
    return None


def purge_state_dir(state_dir) -> Tuple[bool, str]:
    """Delete the state directory. Refuses while another Tokitty holds its
    lock, and never follows the `current` link into a release folder, nor
    removes a real directory standing in for it."""
    from tokitty.lock import LockAcquisitionError, SingleInstanceLock

    state_dir = Path(state_dir)
    if not state_dir.exists():
        return True, "nothing to delete"
    if state_dir.is_symlink():
        return False, "the settings folder is a link, left alone"
    held = _protected_inside(state_dir)
    if held is not None:
        return False, f"the running program is inside it ({held}), left alone"
    lock = SingleInstanceLock(state_dir)
    try:
        lock.acquire()
    except LockAcquisitionError:
        return False, "Tokitty is still running, quit it first"
    except OSError as exc:
        return False, str(exc)
    lock.release()
    leftover = []
    try:
        for child in sorted(state_dir.iterdir()):
            if _is_link(child):
                _remove_link(child)
            elif child.name == "current" and child.is_dir():
                leftover.append(child.name)
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        if not leftover:
            state_dir.rmdir()
    except OSError as exc:
        return False, str(exc)
    if leftover:
        return True, "kept 'current', a real folder Tokitty did not make"
    return True, ""


def _run_item(plan: Plan, item: Item) -> Tuple[bool, str]:
    from tokitty import hooks_install
    from tokitty.streamdock import install as sd

    if item.kind == HOOKS:
        config_dir, provider = item.account
        result = hooks_install.uninstall_hooks_for_dir(config_dir, provider)
        message = result.message
        if result.warning:
            message += f" ({result.warning})"
        target = hooks_install._hook_target(provider)
        if result.ok and target.local_settings_file:
            # The uninstall never edits the local file, so its entries would
            # keep registering a hook script we are about to delete.
            local, error = hooks_install._load_settings(
                Path(hooks_install._local_config_path(config_dir)) / target.local_settings_file)
            if error is None and isinstance(local, dict) and hooks_install._events_with_tokitty_entries(
                    local, config_dir, provider):
                return False, f"tokitty hooks remain in {target.local_settings_file}, remove them by hand"
        return result.ok, message
    if item.kind == HOOK_FILES:
        shutil.rmtree(item.path)
        return True, ""
    if item.kind == STREAMDOCK:
        dirs = plan.config_dirs
        ok, lines = sd.gui_uninstall(plan.state_dir, plan.appdata, config_dirs_fn=lambda _sd: dirs)
        return ok, "" if ok else "; ".join(lines)
    if item.kind == AUTOSTART:
        plan.backend.deregister()
        return True, ""
    if item.kind == RUNNER_LINK:
        _remove_link(item.path)
        return True, ""
    if item.kind == LAUNCHER:
        Path(item.path).unlink()
        return True, ""
    if item.kind == STATE:
        return purge_state_dir(plan.state_dir)
    raise ValueError(item.kind)


def execute(plan: Plan, *, defer_purge: bool = False) -> List[Outcome]:
    """Run every item, recording failures instead of stopping. An account whose
    hooks could not be removed keeps its hook files, so the registered hook is
    never left pointing at a deleted script. defer_purge skips the state
    directory, for the GUI, which purges after it has exited."""
    outcomes: List[Outcome] = []
    failed_accounts = set()
    for item in plan.items:
        if item.kind == STATE and defer_purge:
            continue
        if item.kind == HOOK_FILES and item.account in failed_accounts:
            outcomes.append(Outcome(item, False, "kept, its hooks could not be removed"))
            continue
        try:
            ok, message = _run_item(plan, item)
        except Exception as exc:
            ok, message = False, str(exc) or type(exc).__name__
        if item.kind == HOOKS and not ok:
            failed_accounts.add(item.account)
        outcomes.append(Outcome(item, ok, message))
    return outcomes


def run_cli(
    argv,
    *,
    state_dir=None,
    input_fn: Callable[[str], str] = input,
    plan_fn=build_plan,
    out=None,
    err=None,
    **plan_kwargs,
) -> int:
    """--uninstall [--purge] [--yes]. Non-zero when anything failed."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    if state_dir is None:
        state_dir = state_dir_path()
    plan = plan_fn(state_dir, purge="--purge" in argv, **plan_kwargs)
    for line in format_plan(plan):
        print(line, file=out)
    if "--yes" not in argv:
        try:
            answer = input_fn("Remove Tokitty from this computer? [y/N] ")
        except (EOFError, RuntimeError):  # no stdin, as in a windowed build
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled, nothing was removed.", file=out)
            return 0
    outcomes = execute(plan)
    failed = [o for o in outcomes if not o.ok]
    for line in format_outcomes(outcomes):
        print(line, file=err if line.startswith("FAILED") else out)
    if failed:
        print(f"{len(failed)} item(s) could not be removed.", file=err)
        return 1
    print("Tokitty was removed. Restart any open Claude Code and Codex sessions.", file=out)
    return 0
