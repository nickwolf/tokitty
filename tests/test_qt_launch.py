"""GUI routing (always Qt) and the missing-PySide6 exit. These need no PySide6, so
they run in the headless matrix; the launches themselves are in
tests/qt/test_app.py."""
import sys

import pytest


@pytest.fixture
def routed(monkeypatch):
    """main() with run_qt replaced by a recorder."""
    from tokitty import __main__ as main_module
    from tokitty.qt import app

    calls = []
    monkeypatch.setattr(app, "run_qt", lambda **kw: calls.append(kw) or 0)
    monkeypatch.setattr("tokitty.update_swap.valid_token", lambda token: True)
    return main_module, calls


@pytest.mark.parametrize("value", [None, "", "0", "1", "true"])
def test_a_plain_launch_runs_qt_whatever_the_old_flag_says(routed, monkeypatch, value):
    main_module, calls = routed
    if value is None:
        monkeypatch.delenv("TOKITTY_QT", raising=False)
    else:
        monkeypatch.setenv("TOKITTY_QT", value)

    assert main_module.main([]) == 0
    assert calls == [{"after_update_token": None, "apply_update": False}]


@pytest.mark.parametrize("argv, kwargs", [
    (["--apply-update"], {"after_update_token": None, "apply_update": True}),
    (["--after-update", "tok"], {"after_update_token": "tok", "apply_update": False}),
    (["--after-update", "tok", "--apply-update"], {"after_update_token": "tok", "apply_update": True}),
])
def test_update_arguments_reach_run_qt(routed, argv, kwargs):
    main_module, calls = routed

    main_module.main(argv)
    assert calls == [kwargs]


def test_an_invalid_token_is_refused_before_routing(routed, monkeypatch, capsys):
    main_module, calls = routed
    monkeypatch.setattr("tokitty.update_swap.valid_token", lambda token: False)

    assert main_module.main(["--after-update", "bad"]) == 2
    assert calls == []


def test_missing_pyside6_prints_the_message_and_returns_1(tmp_path, monkeypatch, capsys):
    from tokitty.qt.app import run_qt

    monkeypatch.setitem(sys.modules, "PySide6", None)
    monkeypatch.setattr("tokitty.paths.get_state_dir", lambda: tmp_path)

    assert run_qt() == 1
    assert capsys.readouterr().err.strip() == (
        'tokitty: TOKITTY_QT is set but PySide6 is not installed (pip install -e ".[qt]")'
    )
    # Nothing was started: no lock, no settings.
    assert list(tmp_path.iterdir()) == []


def test_importing_the_entry_point_needs_no_pyside6():
    import subprocess

    code = "import sys\nsys.modules['PySide6'] = None\nimport tokitty.qt.app\n"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
