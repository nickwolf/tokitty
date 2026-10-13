"""Tests for tokitty/frozen.py: the translocation guard and the
windowed crash log / --self-check (#48)."""
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

import tokitty.frozen as frozen
from tokitty.frozen import MOVE_TO_APPLICATIONS, AppTranslocatedError, is_translocated

GUI_ENTRY_PATH = Path(__file__).resolve().parent.parent / "freeze" / "gui_entry.py"


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
    version that runs everywhere. A box missing that backend raises
    ValueError (not ImportError) out of pystray's own backend selection, so
    skip cleanly on either rather than leaving the gui suite red; self_check()
    itself is not weakened to work around this."""
    try:
        import pystray  # noqa: F401
    except (ImportError, ValueError) as exc:
        pytest.skip(f"pystray unavailable: {exc}")
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


def test_self_check_omits_tk_root_without_the_env_var(monkeypatch, capsys):
    """tkinter.Tcl() in _check_tkinter never opens a window, so without
    TOKITTY_SELF_CHECK_TK the tk_root check must be absent, not just
    unchecked, keeping headless unit tests headless."""
    monkeypatch.delenv("TOKITTY_SELF_CHECK_TK", raising=False)
    monkeypatch.setitem(sys.modules, "pystray", types.ModuleType("pystray"))
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert "tk_root" not in report["checks"]


@pytest.mark.gui
def test_self_check_tk_root_when_env_var_set(monkeypatch, capsys):
    """TOKITTY_SELF_CHECK_TK=1 opens a real tkinter.Tk() window and reports
    its patchlevel as check tk_root, catching a bundle whose window can't
    actually open even though tkinter.Tcl() above it passed."""
    monkeypatch.setenv("TOKITTY_SELF_CHECK_TK", "1")
    monkeypatch.setitem(sys.modules, "pystray", types.ModuleType("pystray"))
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["tk_root"]["ok"] is True
    assert report["checks"]["tk_root"]["detail"]


def test_self_check_reports_env_names_not_values(monkeypatch, capsys):
    for name in list(os.environ):
        if name.startswith(frozen.ENV_DUMP_PREFIXES):
            monkeypatch.delenv(name)
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "secret-value-1")
    monkeypatch.setenv("TCL_LIBRARY", "secret-value-2")
    monkeypatch.setenv("LD_LIBRARY_PATH", "secret-value-3")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "secret-value-4")
    monkeypatch.setenv("TK_LIBRARY", "secret-value-5")
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "1")
    monkeypatch.setenv("HOME_UNRELATED", "x")
    monkeypatch.setitem(sys.modules, "pystray", types.ModuleType("pystray"))
    # The made-up TCL_LIBRARY would break a real Tk check on Windows.
    monkeypatch.setattr(frozen, "_check_tkinter", lambda: "8.6")
    assert frozen.self_check() == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["env"] == [
        "DYLD_LIBRARY_PATH",
        "LD_LIBRARY_PATH",
        "PYINSTALLER_RESET_ENVIRONMENT",
        "TCL_LIBRARY",
        "TK_LIBRARY",
        "_PYI_APPLICATION_HOME_DIR",
    ]
    assert "secret-value" not in out


def test_self_check_fails_without_prices(monkeypatch, tmp_path, capsys):
    from tokitty import pricing

    monkeypatch.setattr(pricing, "PACKAGED_PRICES", tmp_path / "missing.json")
    assert frozen.self_check() == 1
    assert json.loads(capsys.readouterr().out)["checks"]["prices"]["ok"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="Windows loads its own store regardless")
def test_self_check_fails_with_no_trusted_roots(monkeypatch, tmp_path, capsys):
    """What a frozen Mac build saw before tokitty.tls: OpenSSL's compiled-in
    cert file and directory both missing."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing"))
    assert frozen.self_check() == 1
    tls = json.loads(capsys.readouterr().out)["checks"]["tls"]
    assert tls["ok"] is False
    assert "no trusted CA certificates" in tls["detail"]


def _point_state_dir_env_at(monkeypatch, tmp_path):
    """Point every OS's state-dir env var at tmp_path, so whichever one the
    host platform actually reads still resolves under the sandbox."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def _load_gui_entry_with_broken_frozen_import(monkeypatch):
    """Load freeze/gui_entry.py as the last-line-of-defense case: even
    ``tokitty.frozen`` fails to import. Poisoning sys.modules makes any
    import of it raise, exactly like a bundle missing that module would.

    freeze/gui_entry.py has no ``if __name__`` guard -- its last line is a
    bare ``sys.exit(_run())`` -- so loading it always raises SystemExit.
    exec_module still mutates the module object's __dict__ top to bottom as
    it runs, so everything defined before that final line (_fallback_state_dir,
    _run) survives on the returned module even though the exec call itself
    raises. This never reaches the real GUI: the poisoned import fails before
    _run() can construct main() or call run_gui_entry.
    """
    monkeypatch.setitem(sys.modules, "tokitty.frozen", None)
    spec = importlib.util.spec_from_file_location("gui_entry_under_test", GUI_ENTRY_PATH)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(SystemExit) as exc_info:
        spec.loader.exec_module(module)
    assert exc_info.value.code == 1
    return module


def test_gui_entry_logs_crash_when_tokitty_frozen_fails_to_import(monkeypatch, tmp_path):
    _point_state_dir_env_at(monkeypatch, tmp_path)
    module = _load_gui_entry_with_broken_frozen_import(monkeypatch)

    log = (Path(module._fallback_state_dir()) / "crash.log").read_text(encoding="utf-8")
    assert "Traceback" in log
    assert "tokitty.frozen" in log


def test_fallback_state_dir_matches_paths_state_dir_path(monkeypatch, tmp_path):
    """_fallback_state_dir() is a from-scratch reimplementation of
    tokitty.paths.state_dir_path() for the case where tokitty itself is
    unimportable. Pin them together so the two rules can't drift apart."""
    _point_state_dir_env_at(monkeypatch, tmp_path)
    module = _load_gui_entry_with_broken_frozen_import(monkeypatch)

    from tokitty import paths

    assert Path(module._fallback_state_dir()) == paths.state_dir_path()


# --- declared toolkits (#113) ---------------------------------------------

DEFAULT_KEYS = {"tkinter", "pil", "pystray", "prices", "hook_writer", "tls"}


def _report(monkeypatch, capsys, toolkits=None, window=False, qt_blocked=False):
    """Run self_check() under a declared toolkit list and return (exit code,
    report). The Tk and Qt window checks are stubbed so this stays headless;
    the real ones are covered by the gui and qt suites."""
    if toolkits is None:
        monkeypatch.delenv("TOKITTY_BUILD_TOOLKITS", raising=False)
    else:
        monkeypatch.setenv("TOKITTY_BUILD_TOOLKITS", toolkits)
    if window:
        monkeypatch.setenv("TOKITTY_SELF_CHECK_TK", "1")
    else:
        monkeypatch.delenv("TOKITTY_SELF_CHECK_TK", raising=False)
    monkeypatch.setitem(sys.modules, "pystray", types.ModuleType("pystray"))
    monkeypatch.setattr(frozen, "_check_tk_root", lambda: "8.6.0")
    if qt_blocked:
        for name in ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
            monkeypatch.setitem(sys.modules, name, None)
    else:
        monkeypatch.setattr(frozen, "_check_qt", lambda: "6.12.0")
        monkeypatch.setattr(frozen, "_check_qt_root", lambda: "xcb")
    code = frozen.self_check()
    return code, json.loads(capsys.readouterr().out)


def _judge(report, tag="v1.2.3"):
    """Feed a report through the check every shipped build applies to a new
    copy's --self-check output."""
    from tokitty import update_install

    report = dict(report, build_id=tag)
    runner = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(report).encode(), returncode=0)  # noqa: E731
    update_install.run_self_check("tokitty", tag, runner=runner, sys_platform="linux")


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, {"tk"}),
        ("", {"tk"}),
        (" , ,", {"tk"}),
        ("tk", {"tk"}),
        ("qt", {"qt"}),
        ("tk,qt", {"tk", "qt"}),
        (" Qt , TK ,", {"tk", "qt"}),
        ("qt,qt", {"qt"}),
    ],
)
def test_build_toolkits_parsing(value, expected):
    environ = {} if value is None else {"TOKITTY_BUILD_TOOLKITS": value}
    assert frozen.build_toolkits(environ) == expected


def test_build_toolkits_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("TOKITTY_BUILD_TOOLKITS", "qt")
    assert frozen.build_toolkits() == {"qt"}


@pytest.mark.parametrize("qt_blocked", [False, True])
@pytest.mark.parametrize("window", [False, True])
def test_default_declaration_keeps_todays_keys(monkeypatch, capsys, window, qt_blocked):
    code, report = _report(monkeypatch, capsys, window=window, qt_blocked=qt_blocked)
    assert code == 0
    expected = DEFAULT_KEYS | ({"tk_root"} if window else set())
    assert set(report["checks"]) == expected
    assert "toolkits" not in report


def test_explicit_tk_declaration_matches_the_default(monkeypatch, capsys):
    _, report = _report(monkeypatch, capsys, toolkits="tk")
    assert set(report["checks"]) == DEFAULT_KEYS
    assert "toolkits" not in report


def test_tk_and_qt_adds_qt_and_qt_root_only_with_the_window_switch(monkeypatch, capsys):
    code, report = _report(monkeypatch, capsys, toolkits="tk,qt")
    assert code == 0
    assert set(report["checks"]) == DEFAULT_KEYS | {"qt"}
    assert report["checks"]["qt"] == {"ok": True, "detail": "6.12.0"}
    assert report["toolkits"] == ["qt", "tk"]
    _, report = _report(monkeypatch, capsys, toolkits="tk,qt", window=True)
    assert set(report["checks"]) == DEFAULT_KEYS | {"qt", "qt_root", "tk_root"}
    assert report["checks"]["tk_root"]["detail"] == "8.6.0"
    assert report["checks"]["qt_root"]["detail"] == "xcb"


def test_qt_alone_drops_tkinter_and_tk_root_carries_the_qt_result(monkeypatch, capsys):
    _, report = _report(monkeypatch, capsys, toolkits="qt")
    assert set(report["checks"]) == (DEFAULT_KEYS - {"tkinter"}) | {"qt"}
    assert report["toolkits"] == ["qt"]
    _, report = _report(monkeypatch, capsys, toolkits="qt", window=True)
    assert set(report["checks"]) == (DEFAULT_KEYS - {"tkinter"}) | {"qt", "qt_root", "tk_root"}
    assert report["checks"]["tk_root"] == report["checks"]["qt_root"]
    assert report["checks"]["tk_root"] == {"ok": True, "detail": "xcb"}


def test_declared_qt_with_pyside6_blocked_fails_the_qt_check(monkeypatch, capsys):
    code, report = _report(monkeypatch, capsys, toolkits="tk,qt", qt_blocked=True)
    assert code == 1
    assert report["checks"]["qt"]["ok"] is False
    assert report["checks"]["tkinter"]["ok"] is True


def test_declared_qt_window_check_fails_with_pyside6_blocked(monkeypatch, capsys):
    code, report = _report(monkeypatch, capsys, toolkits="qt", window=True, qt_blocked=True)
    assert code == 1
    assert report["checks"]["qt_root"]["ok"] is False
    assert report["checks"]["tk_root"] == report["checks"]["qt_root"]


@pytest.mark.parametrize("toolkits", [None, "tk,qt", "qt"])
def test_shipped_judge_accepts_each_passing_report(monkeypatch, capsys, toolkits):
    code, report = _report(monkeypatch, capsys, toolkits=toolkits, window=True)
    assert code == 0
    _judge(report)


@pytest.mark.parametrize("toolkits", ["tk,qt", "qt"])
def test_shipped_judge_rejects_a_failed_qt_report(monkeypatch, capsys, toolkits):
    from tokitty.update_install import UpdateInstallError

    code, report = _report(monkeypatch, capsys, toolkits=toolkits, window=True, qt_blocked=True)
    assert code == 1
    with pytest.raises(UpdateInstallError, match="failed its self-check"):
        _judge(report)
