"""Tests for tokitty/uninstall.py: --uninstall and its shared plan.

SAFETY: every test passes tmp_path state, config and appdata dirs and a fake
or None autostart backend. Nothing here may reach the real ~/.claude, the real
state directory, the real autostart entry or the real Stream Dock plugin.
"""
import json
import os
import sys

import pytest

from tokitty import hooks_install as hi
from tokitty import runner_link
from tokitty import uninstall
from tokitty.autostart import LAUNCHER_FILENAME, LinuxDesktopEntryBackend
from tokitty.lock import SingleInstanceLock
from tokitty.settings import load_settings, update_settings
from tokitty.streamdock import install as sd
from tokitty.streamdock.pending import ENABLED_MARKER


class FakeBackend:
    def __init__(self, registered=True, fail=False):
        self.registered = registered
        self.fail = fail

    def is_registered(self):
        return self.registered

    def location(self):
        return "FAKE-AUTOSTART"

    def deregister(self):
        if self.fail:
            raise OSError("registry locked")
        self.registered = False


@pytest.fixture
def world(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    appdata = tmp_path / "appdata"
    plugin = sd._plugins_dir(appdata) / sd.PLUGIN_FOLDER
    plugin.mkdir(parents=True)
    (plugin / "manifest.json").write_text("{}")
    # Windows-only in real life; the plugin folder is the tmp one either way.
    monkeypatch.setattr(sd, "install_supported", lambda: True)
    accounts = []
    for name in ("a", "b"):
        config = tmp_path / f".claude-{name}"
        config.mkdir()
        (config / "settings.json").write_text(json.dumps({"theme": "dark", "hooks": {}}))
        hi.install_hooks_for_dir(str(config))
        (config / "tokitty" / "sessions").mkdir(exist_ok=True)
        (config / "tokitty" / "sessions" / "s1.json").write_text("{}")
        (config / "tokitty" / ENABLED_MARKER).write_text("")
        accounts.append({"name": name, "config_dir": str(config)})
    (state / "accounts.json").write_text(json.dumps({"accounts": accounts}))
    (state / "settings.json").write_text("{}")
    update_settings(state, streamdock_port=40123, streamdock_token="T" * 43)
    (state / LAUNCHER_FILENAME).write_text("# launcher")
    release = tmp_path / "releases" / "0.9.0"
    release.mkdir(parents=True)
    (release / "tokitty-hook").write_text("hook")
    (release / "tokitty").write_text("gui")
    # A junction on Windows, a symlink elsewhere: the real link code makes it.
    runner_link.ensure_runner_link(state, executable=str(release / "tokitty"))
    assert runner_link._is_link(state / "current")
    backend = FakeBackend()
    return type("World", (), dict(
        tmp=tmp_path, state=state, appdata=appdata, plugin=plugin, backend=backend,
        release=release, configs=[a["config_dir"] for a in accounts]))


def plan_for(world, purge=False, **kw):
    kw.setdefault("backend", world.backend)
    return uninstall.build_plan(world.state, purge=purge, appdata=world.appdata, **kw)



# ---- plan ---------------------------------------------------------------

def test_plan_lists_every_concrete_item_and_changes_nothing(world):
    before = sorted(str(p) for p in world.tmp.rglob("*"))
    plan = plan_for(world)
    assert sorted(str(p) for p in world.tmp.rglob("*")) == before
    assert [(i.kind, i.path) for i in plan.items] == [
        (uninstall.HOOKS, os.path.join(world.configs[0], "settings.json")),
        (uninstall.HOOK_FILES, os.path.join(world.configs[0], "tokitty")),
        (uninstall.HOOKS, os.path.join(world.configs[1], "settings.json")),
        (uninstall.HOOK_FILES, os.path.join(world.configs[1], "tokitty")),
        (uninstall.STREAMDOCK, str(world.plugin)),
        (uninstall.AUTOSTART, "FAKE-AUTOSTART"),
        (uninstall.RUNNER_LINK, str(world.state / "current")),
        (uninstall.LAUNCHER, str(world.state / LAUNCHER_FILENAME)),
    ]
    assert not plan.purge


def test_plan_format_is_one_line_per_item(world):
    lines = uninstall.format_plan(plan_for(world, purge=True))
    assert lines[0] == "Tokitty will remove:"
    assert f"  Hook files: {os.path.join(world.configs[0], 'tokitty')} ({uninstall.STUB_NOTE})" in lines
    assert "  Start at login: FAKE-AUTOSTART" in lines
    assert f"  Settings folder (accounts, looks, theme): {world.state}" in lines
    assert lines[-2] == uninstall.RESTART_NOTE
    assert lines[-1] == "Release folders are not touched. Delete them yourself afterwards."


def test_plan_without_purge_omits_the_state_directory(world):
    assert uninstall.STATE not in [i.kind for i in plan_for(world).items]
    assert uninstall.STATE in [i.kind for i in plan_for(world, purge=True).items]


def test_plan_skips_what_is_not_there(world):
    world.backend.registered = False
    (world.state / LAUNCHER_FILENAME).unlink()
    uninstall._remove_link(world.state / "current")
    plan = plan_for(world)
    kinds = [i.kind for i in plan.items]
    assert uninstall.AUTOSTART not in kinds
    assert uninstall.LAUNCHER not in kinds
    assert uninstall.RUNNER_LINK not in kinds
    no_backend = plan_for(world, backend=None)
    assert uninstall.AUTOSTART not in [i.kind for i in no_backend.items]


def test_plan_skips_the_plugin_off_windows(world, monkeypatch):
    monkeypatch.setattr(sd, "install_supported", lambda: False)
    assert uninstall.STREAMDOCK not in [i.kind for i in plan_for(world).items]


def test_plan_covers_every_account_in_accounts_json(world):
    plan = plan_for(world)
    assert [i.account[0] for i in plan.items if i.kind == uninstall.HOOKS] == world.configs


def test_with_purge_keeps_the_items_and_adds_the_state_directory(world):
    plan = plan_for(world)
    purged = uninstall.with_purge(plan)
    assert purged.purge and purged.items[:-1] == plan.items
    assert purged.items[-1].kind == uninstall.STATE
    assert uninstall.with_purge(purged) is purged


# ---- execute --------------------------------------------------------------

def test_execute_removes_everything_but_the_state_directory(world):
    outcomes = uninstall.execute(plan_for(world))
    assert all(o.ok for o in outcomes), uninstall.format_outcomes(outcomes)
    for config in world.configs:
        data = json.loads(open(os.path.join(config, "settings.json")).read())
        assert data["theme"] == "dark" and data["hooks"] == {}
        assert os.listdir(os.path.join(config, "tokitty")) == ["hook_writer.py"]
    assert not world.plugin.exists()
    assert world.backend.registered is False
    assert not runner_link._is_link(world.state / "current")
    assert not (world.state / LAUNCHER_FILENAME).exists()
    assert (world.state / "accounts.json").exists()
    assert load_settings(world.state).streamdock_port == 0
    # the release behind the link is untouched
    assert (world.release / "tokitty-hook").read_text() == "hook"


def test_purge_also_deletes_the_state_directory_but_not_the_release(world):
    outcomes = uninstall.execute(plan_for(world, purge=True))
    assert all(o.ok for o in outcomes), uninstall.format_outcomes(outcomes)
    assert not world.state.exists()
    assert (world.release / "tokitty-hook").read_text() == "hook"


def test_defer_purge_leaves_the_state_directory_for_after_exit(world):
    outcomes = uninstall.execute(plan_for(world, purge=True), defer_purge=True)
    assert uninstall.STATE not in [o.item.kind for o in outcomes]
    assert (world.state / "accounts.json").exists()
    ok, _ = uninstall.purge_state_dir(world.state)
    assert ok and not world.state.exists()
    assert (world.release / "tokitty-hook").exists()


def test_the_stub_replaces_the_script_and_exits_zero_with_the_real_hook_args(world):
    import subprocess

    config = world.configs[0]
    script = os.path.join(config, "tokitty", "hook_writer.py")
    uninstall.execute(plan_for(world))
    assert open(script).read() == uninstall.STUB_SOURCE
    assert os.listdir(os.path.join(config, "tokitty")) == ["hook_writer.py"]
    done = subprocess.run(
        [sys.executable, script, "--sessions-dir", os.path.join(config, "tokitty", "sessions")],
        input=b'{"hook_event_name": "PreToolUse"}', capture_output=True, timeout=30)
    assert done.returncode == 0 and done.stdout == b""


def test_a_hook_folder_without_the_script_is_removed_whole(world):
    config = world.configs[0]
    os.remove(os.path.join(config, "tokitty", "hook_writer.py"))
    plan = plan_for(world)
    item = [i for i in plan.items if i.kind == uninstall.HOOK_FILES and i.account[0] == config][0]
    assert item.note == ""
    uninstall.execute(plan)
    assert not os.path.exists(os.path.join(config, "tokitty"))


def test_a_failed_account_does_not_stop_the_rest_and_keeps_its_hook_files(world):
    (open(os.path.join(world.configs[0], "settings.json"), "w")).write("{not json")
    outcomes = uninstall.execute(plan_for(world))
    bad = [o for o in outcomes if not o.ok]
    assert [(o.item.kind, o.item.account[0]) for o in bad] == [
        (uninstall.HOOKS, world.configs[0]),
        (uninstall.HOOK_FILES, world.configs[0]),
    ]
    assert os.path.exists(os.path.join(world.configs[0], "tokitty", "hook_writer.py"))
    assert os.listdir(os.path.join(world.configs[1], "tokitty")) == ["hook_writer.py"]
    assert not world.plugin.exists()
    assert world.backend.registered is False


def test_hook_files_stay_while_a_local_settings_entry_still_registers_them(world):
    config = world.configs[0]
    shared = json.loads(open(os.path.join(config, "settings.json")).read())
    # the same tokitty entries, but in settings.local.json, which is never edited
    open(os.path.join(config, "settings.json"), "w").write(json.dumps({"hooks": {}}))
    open(os.path.join(config, "settings.local.json"), "w").write(json.dumps(shared))
    outcomes = uninstall.execute(plan_for(world))
    bad = [o for o in outcomes if not o.ok]
    assert [(o.item.kind, o.item.account[0]) for o in bad] == [
        (uninstall.HOOKS, config), (uninstall.HOOK_FILES, config)]
    assert "settings.local.json" in bad[0].message
    assert os.path.exists(os.path.join(config, "tokitty", "hook_writer.py"))
    assert os.listdir(os.path.join(world.configs[1], "tokitty")) == ["hook_writer.py"]


def test_a_failed_autostart_removal_is_reported_and_the_rest_still_runs(world):
    world.backend.fail = True
    outcomes = uninstall.execute(plan_for(world, purge=True))
    failed = [o for o in outcomes if not o.ok]
    assert [o.item.kind for o in failed] == [uninstall.AUTOSTART]
    assert "registry locked" in failed[0].message
    assert not world.state.exists()
    assert not world.plugin.exists()


def test_uninstall_is_a_noop_on_a_machine_with_nothing_installed(tmp_path):
    state = tmp_path / "state"
    plan = uninstall.build_plan(
        state, purge=True, config_dirs=[], backend=None, appdata=tmp_path / "appdata")
    outcomes = uninstall.execute(plan)
    assert all(o.ok for o in outcomes)
    assert [o.message for o in outcomes] == ["nothing to delete"]


def test_linux_desktop_entry_backend_removal(tmp_path, monkeypatch):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path / "autostart")
    backend.register(["python", "x"])
    plan = uninstall.build_plan(
        tmp_path / "state", config_dirs=[], backend=backend, appdata=tmp_path / "appdata")
    assert [(i.kind, i.path) for i in plan.items] == [
        (uninstall.AUTOSTART, str(tmp_path / "autostart" / "tokitty.desktop"))]
    assert all(o.ok for o in uninstall.execute(plan))
    assert not backend.is_registered()


# ---- purge guards -----------------------------------------------------------

def test_purge_refuses_while_tokitty_is_running(world):
    lock = SingleInstanceLock(world.state)
    lock.acquire()
    try:
        ok, message = uninstall.purge_state_dir(world.state)
    finally:
        lock.release()
    assert not ok and "still running" in message
    assert (world.state / "accounts.json").exists()


def test_purge_refuses_when_the_running_program_is_inside(world, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(world.state / "bin" / "tokitty"))
    ok, message = uninstall.purge_state_dir(world.state)
    assert not ok and "running program" in message
    assert (world.state / "accounts.json").exists()


def test_purge_never_follows_the_current_link(world):
    ok, _ = uninstall.purge_state_dir(world.state)
    assert ok and not world.state.exists()
    assert (world.release / "tokitty-hook").exists()


def test_purge_keeps_a_real_directory_standing_in_for_current(world):
    uninstall._remove_link(world.state / "current")
    (world.state / "current").mkdir()
    (world.state / "current" / "tokitty-hook").write_text("x")
    ok, message = uninstall.purge_state_dir(world.state)
    assert ok and "current" in message
    assert (world.state / "current" / "tokitty-hook").exists()
    assert not (world.state / "accounts.json").exists()


# ---- CLI --------------------------------------------------------------------

def run(world, argv, answer=None, **kw):
    import io

    out, err = io.StringIO(), io.StringIO()
    asked = []

    def input_fn(prompt):
        asked.append(prompt)
        if isinstance(answer, Exception):
            raise answer
        return answer

    code = uninstall.run_cli(
        argv, state_dir=world.state, input_fn=input_fn, out=out, err=err,
        backend=world.backend, appdata=world.appdata, **kw)
    return code, out.getvalue(), err.getvalue(), asked


def test_cli_prints_the_plan_and_removes_after_y(world):
    code, out, err, asked = run(world, ["--uninstall"], answer="y")
    assert code == 0 and err == ""
    assert "Tokitty will remove:" in out and "FAKE-AUTOSTART" in out
    assert asked == ["Remove Tokitty from this computer? [y/N] "]
    assert not world.plugin.exists()
    assert (world.state / "accounts.json").exists()


@pytest.mark.parametrize("answer", ["", "n", "no", "yes please", EOFError(), RuntimeError("no stdin")])
def test_cli_removes_nothing_unless_the_answer_is_yes(world, answer):
    code, out, _err, _ = run(world, ["--uninstall"], answer=answer)
    assert code == 0 and "nothing was removed" in out
    assert world.plugin.exists() and world.backend.registered


def test_cli_yes_skips_the_prompt(world):
    code, _out, _err, asked = run(world, ["--uninstall", "--yes"])
    assert code == 0 and asked == []
    assert not world.plugin.exists()


def test_cli_purge_deletes_the_state_directory(world):
    code, out, _err, _ = run(world, ["--uninstall", "--purge", "--yes"])
    assert code == 0
    assert "Settings folder" in out
    assert not world.state.exists()


def test_cli_exits_non_zero_when_anything_failed(world):
    world.backend.fail = True
    code, out, err, _ = run(world, ["--uninstall", "--yes"])
    assert code == 1
    assert "FAILED: Start at login: FAKE-AUTOSTART" in err
    assert "ok: Stream Dock plugin" in out
    assert not world.plugin.exists()


def test_main_routes_uninstall_to_run_cli(monkeypatch):
    from tokitty import __main__ as entry

    seen = []
    monkeypatch.setattr(uninstall, "run_cli", lambda argv: seen.append(argv) or 7)
    assert entry.main(["--uninstall", "--yes"]) == 7
    assert seen == [["--uninstall", "--yes"]]


def test_main_does_not_treat_the_older_flags_as_uninstall(monkeypatch):
    from tokitty import __main__ as entry

    monkeypatch.setattr(uninstall, "run_cli", lambda argv: pytest.fail("wrong flag"))
    monkeypatch.setattr(hi, "uninstall_hooks", lambda: 0)
    assert entry.main(["--uninstall-hooks"]) == 0
