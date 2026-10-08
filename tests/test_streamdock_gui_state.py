"""The Stream Dock state holder behind the Settings tab, and the install
wrappers it calls. Fakes stand in for the real install and settings."""
from pathlib import Path
from types import SimpleNamespace

from tokitty.streamdock import install
from tokitty.streamdock.gui_state import StreamdockHolder

CONFIGURED = SimpleNamespace(streamdock_port=40000, streamdock_token="t" * 43)
EMPTY = SimpleNamespace(streamdock_port=0, streamdock_token="")


def holder(settings, runtime=None, *, install_fn=None, uninstall_fn=None, reload=None):
    return StreamdockHolder(
        settings, "state", lambda: runtime,
        load_fn=lambda _sd: reload, install_fn=install_fn or (lambda _sd: (True, [])),
        uninstall_fn=uninstall_fn or (lambda _sd: (True, [])))


def test_base_states_follow_settings_and_runtime():
    assert holder(EMPTY).state() == "not_installed"
    assert holder(CONFIGURED).state() == "not_connected"
    assert holder(CONFIGURED, SimpleNamespace(connected=True)).state() == "connected"
    assert holder(CONFIGURED, SimpleNamespace(connected=False)).state() == "not_connected"


def test_install_without_a_runtime_needs_a_restart():
    h = holder(EMPTY, reload=CONFIGURED)
    assert h.install() == (True, [])
    assert h.state() == "restart_to_connect"


def test_reinstall_keeping_the_port_and_token_reads_live_state():
    h = holder(CONFIGURED, SimpleNamespace(connected=True), reload=CONFIGURED)
    h.install()
    assert h.state() == "connected"


def test_reinstall_after_uninstall_with_a_live_runtime_needs_a_restart():
    # Uninstall clears the port and token, so the reinstall saves new ones the
    # runtime started at launch does not serve.
    fresh = SimpleNamespace(streamdock_port=40001, streamdock_token="u" * 43)
    h = holder(CONFIGURED, SimpleNamespace(connected=True), reload=EMPTY)
    h.uninstall()
    h._load = lambda _sd: fresh
    h.install()
    assert h.state() == "restart_to_connect"


def test_failed_install_changes_nothing():
    h = holder(EMPTY, reload=CONFIGURED, install_fn=lambda _sd: (False, ["nope"]))
    assert h.install() == (False, ["nope"])
    assert h.state() == "not_installed"


def test_uninstall_with_a_live_runtime_waits_for_restart():
    h = holder(CONFIGURED, SimpleNamespace(connected=True), reload=EMPTY)
    h.uninstall()
    assert h.state() == "restart_to_finish_removal"


def test_uninstall_after_an_install_this_session_returns_to_not_installed():
    h = holder(EMPTY, reload=CONFIGURED)
    h.install()
    h._load = lambda _sd: EMPTY
    h.uninstall()
    assert h.state() == "not_installed"


def test_gui_install_checks_the_plugin_folder_exists(tmp_path):
    appdata = tmp_path / "appdata"

    def fake(*, state_dir, appdata, print_fn):
        print_fn("Installed.")
        return 0

    ok, lines = install.gui_install(tmp_path, appdata, install_fn=fake)
    assert not ok and lines == ["Installed.", "The plugin folder was not created."]
    (appdata / "HotSpot" / "StreamDock" / "plugins" / install.PLUGIN_FOLDER).mkdir(parents=True)
    ok, lines = install.gui_install(tmp_path, appdata, install_fn=fake)
    assert ok and lines == ["Installed."]


def test_gui_install_reports_a_nonzero_code_and_exceptions(tmp_path):
    assert install.gui_install(tmp_path, tmp_path, install_fn=lambda **kw: 1) == (False, [])

    def boom(**kw):
        raise OSError("disk full")

    ok, lines = install.gui_install(tmp_path, tmp_path, install_fn=boom)
    assert not ok and "disk full" in lines[0]


def test_gui_uninstall_passes_the_tokitty_dirs(tmp_path):
    seen = {}

    def fake(*, state_dir, appdata, tokitty_dirs, print_fn):
        seen["dirs"] = list(tokitty_dirs)
        print_fn("Removed.")
        return 0

    ok, lines = install.gui_uninstall(
        tmp_path, tmp_path, uninstall_fn=fake, config_dirs_fn=lambda sd: [("/c/a", "claude")])
    assert ok and lines == ["Removed."]
    assert seen["dirs"] == [Path("/c/a") / "tokitty"]
