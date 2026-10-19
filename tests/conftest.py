
import pytest


@pytest.fixture(autouse=True)
def _packaged_prices_only(monkeypatch, tmp_path_factory):
    """Keep a developer's own prices.json override out of every test.

    Tests pin costs to the packaged table; an override in the real state
    directory would change them depending on whose machine ran the suite.
    """
    from tokitty import pricing

    missing = tmp_path_factory.mktemp("no-price-override") / "prices.json"
    monkeypatch.setattr(pricing, "override_path", lambda: missing)
    pricing._TABLE = None
    yield
    # Cleared, not reloaded: a test may still have table() patched here.
    pricing._TABLE = None


@pytest.fixture(autouse=True)
def _no_real_codex_home(monkeypatch, tmp_path_factory):
    """Keep the Accounts dialog's Codex discovery off the developer's own
    ~/.codex. Tests that exercise discovery patch it back themselves."""
    from tokitty import accounts_logic

    missing = tmp_path_factory.mktemp("no-codex-home") / ".codex"
    monkeypatch.setattr(accounts_logic, "default_codex_home", lambda: str(missing))
    from tokitty import first_run

    monkeypatch.setattr(first_run, "default_codex_home", lambda: str(missing))
    yield


@pytest.fixture(autouse=True)
def _no_real_autostart_registration(monkeypatch):
    """Keep the test suite out of the real OS autostart registration.

    the launch resolves its backend with a bare get_backend(), so any test
    that drives a launch on a developer machine writes to whatever the
    live OS surface is: HKCU\...\Run on Windows, a LaunchAgent plist on
    macOS, ~/.config/autostart on Linux. ensure_current only acts when
    autostart is already registered, so this stays invisible on a machine
    that has never enabled it, and quietly repoints the real entry at a
    pytest tmp_path on a machine that has. That happened: the Tokitty Run
    value ended up at C:\tmp\ptbase\test_live_customization_change0\
    autostart_launcher.pyw, a directory pytest had already deleted, which
    silently broke launch-at-login until tokitty was started by hand.

    Returning None is the same seam the launch already handles for an
    unsupported platform. Tests that actually exercise autostart either
    import get_backend directly (an already-bound name this does not
    touch) or monkeypatch it themselves in the test body, which wins over
    this fixture.
    """
    monkeypatch.setattr("tokitty.autostart.get_backend", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_real_update_check(monkeypatch):
    """The daily update check must never reach api.github.com from a test.

    the launch builds an UpdateChecker whose default fetch is the module's own
    fetch_releases, looked up at call time, so this one patch covers it.
    Tests that exercise the checker pass their own fetch.
    """
    from tokitty import update_check
    from tokitty.updater import UpdateCheckError

    def refuse():
        raise UpdateCheckError("tests never touch the network")

    monkeypatch.setattr(update_check, "fetch_releases", refuse)


@pytest.fixture(autouse=True)
def _no_real_state_dir_for_hooks(monkeypatch, tmp_path_factory):
    """Keep hook installs off the developer's real Tokitty state dir.

    A Codex install writes its trust record (codex_hook_trust.json) under
    hooks_install.state_dir_path(), and a frozen install makes the stable
    runner link there. Tests that care patch it again themselves, which wins.
    """
    from tokitty import hooks_install

    state = tmp_path_factory.mktemp("hooks-state")
    monkeypatch.setattr(hooks_install, "state_dir_path", lambda: state)


@pytest.fixture(autouse=True)
def _keep_stderr_untee(monkeypatch):
    """run_qt puts a StderrTee on sys.stderr; undo it so it never wraps a
    stream pytest closes after the test."""
    import sys

    monkeypatch.setattr(sys, "stderr", sys.stderr)
    yield


@pytest.fixture(autouse=True)
def _no_custom_colorways_leak():
    """The custom-colorway table is module state; start and end every test empty."""
    from tokitty import sprites

    sprites.set_custom_colorways({})
    yield
    sprites.set_custom_colorways({})
