"""Tests for tokitty/frozen.py: the translocation guard (#48 Task 3) and the
windowed crash log / --self-check (#48 Task 6)."""
import json
import sys
import types

import pytest

import tokitty.frozen as frozen
from tokitty.frozen import MOVE_TO_APPLICATIONS, AppTranslocatedError, is_translocated


def test_is_translocated_true_for_apptranslocation_path():
    assert is_translocated(
        "/private/var/folders/x/AppTranslocation/1234/d/Tokitty.app/Contents/MacOS/Tokitty"
    )


def test_is_translocated_false_for_applications_path():
    assert not is_translocated("/Applications/Tokitty.app/Contents/MacOS/Tokitty")


def test_is_translocated_normalises_backslashes():
    # tmp_path on Windows uses backslashes; the check must still match.
    assert is_translocated(r"C:\Users\n\AppData\Local\Temp\AppTranslocation\x\rel\tokitty.exe")


def test_is_translocated_defaults_to_sys_executable(monkeypatch):
    import tokitty.frozen as frozen_module

    monkeypatch.setattr(frozen_module.sys, "executable", "/tmp/AppTranslocation/x/rel/tokitty")
    assert is_translocated()


def test_apptranslocated_error_message():
    exc = AppTranslocatedError()
    assert str(exc) == MOVE_TO_APPLICATIONS
    assert isinstance(exc, OSError)


def test_run_gui_entry_logs_crash(tmp_path):
    def boom():
        raise RuntimeError("kaboom")

    assert frozen.run_gui_entry(boom, state_dir_fn=lambda: tmp_path) == 1
    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "RuntimeError: kaboom" in log and "Traceback" in log


def test_run_gui_entry_passes_system_exit(tmp_path):
    def leave():
        raise SystemExit(3)

    with pytest.raises(SystemExit):
        frozen.run_gui_entry(leave, state_dir_fn=lambda: tmp_path)


def test_run_gui_entry_returns_result(tmp_path):
    assert frozen.run_gui_entry(lambda: 0, state_dir_fn=lambda: tmp_path) == 0
    assert not (tmp_path / "crash.log").exists()


def test_run_gui_entry_swallows_a_failure_to_write_the_log(tmp_path):
    def boom():
        raise RuntimeError("kaboom")

    # state_dir_fn points at a directory that was never created, so the
    # open() call inside the except block raises FileNotFoundError. That
    # must not escape run_gui_entry as a second, unhandled exception.
    missing = tmp_path / "does" / "not" / "exist"
    assert frozen.run_gui_entry(boom, state_dir_fn=lambda: missing) == 1


@pytest.mark.gui
def test_self_check_passes_in_dev(capsys):
    """The real pystray import needs a working GTK/AppIndicator (or xorg)
    backend, which a bare headless Linux box may not have even under xvfb --
    see test_self_check_passes_in_dev_headless for the check-logic-only
    version that runs everywhere."""
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert all(c["ok"] for c in report["checks"].values())


def test_self_check_passes_in_dev_headless(monkeypatch, capsys):
    """Same assertion as test_self_check_passes_in_dev, but stubs only the
    pystray check: pystray's import can fail on a headless Linux host with
    no GTK/AppIndicator install at all, which is an environment gap, not a
    defect in --self-check. Every other check runs for real."""
    monkeypatch.setitem(sys.modules, "pystray", types.ModuleType("pystray"))
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert all(c["ok"] for c in report["checks"].values())


def test_self_check_fails_without_prices(monkeypatch, tmp_path, capsys):
    from tokitty import pricing

    monkeypatch.setattr(pricing, "PACKAGED_PRICES", tmp_path / "missing.json")
    assert frozen.self_check() == 1
    assert json.loads(capsys.readouterr().out)["checks"]["prices"]["ok"] is False
