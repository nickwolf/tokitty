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


def test_self_check_passes_in_dev_headless(monkeypatch, capsys):
    """The Qt window check (qt_root) is off without TOKITTY_SELF_CHECK_TK, so
    every check that runs is a plain import or file check."""
    monkeypatch.delenv("TOKITTY_SELF_CHECK_TK", raising=False)
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert all(c["ok"] for c in report["checks"].values())


def test_self_check_omits_tk_root_without_the_env_var(monkeypatch, capsys):
    """The window checks open a real window, so without
    TOKITTY_SELF_CHECK_TK both tk_root and qt_root must be absent, not just
    unchecked, keeping headless unit tests headless."""
    monkeypatch.delenv("TOKITTY_SELF_CHECK_TK", raising=False)
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert "tk_root" not in report["checks"] and "qt_root" not in report["checks"]


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
    monkeypatch.delenv("TOKITTY_SELF_CHECK_TK", raising=False)
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


# --- the Qt-only report (#142) ---------------------------------------------

QT_ONLY_KEYS = {"qt", "prices", "hook_writer", "tls"}


def _report(monkeypatch, capsys, window=False, qt_blocked=False, inherited=False):
    """Run self_check() and return (exit code, report). The Qt window check is
    stubbed so this stays headless; the real one is covered by the qt suite.
    `inherited` puts what an old copy's environment hands the new one in
    os.environ: a tk,qt build's declaration and a Qt-mode launch's switch."""
    if inherited:
        monkeypatch.setenv("TOKITTY_BUILD_TOOLKITS", "tk,qt")
        monkeypatch.setenv("TOKITTY_QT", "1")
    else:
        monkeypatch.delenv("TOKITTY_BUILD_TOOLKITS", raising=False)
        monkeypatch.delenv("TOKITTY_QT", raising=False)
    if window:
        monkeypatch.setenv("TOKITTY_SELF_CHECK_TK", "1")
    else:
        monkeypatch.delenv("TOKITTY_SELF_CHECK_TK", raising=False)
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


@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("window", [False, True])
def test_the_report_has_the_qt_only_keys_whatever_the_environment_inherited(monkeypatch, capsys, window, inherited):
    code, report = _report(monkeypatch, capsys, window=window, inherited=inherited)
    assert code == 0
    assert set(report["checks"]) == QT_ONLY_KEYS | ({"qt_root", "tk_root"} if window else set())
    assert report["toolkits"] == ["qt"]
    for gone in ("tkinter", "pystray", "pil"):
        assert gone not in report["checks"]


def test_the_key_set_does_not_depend_on_the_inherited_variables(monkeypatch, capsys):
    keys = []
    for inherited in (False, True):
        _, report = _report(monkeypatch, capsys, window=True, inherited=inherited)
        keys.append(list(report["checks"]))
    assert keys[0] == keys[1]


def test_frozen_adds_the_hook_runner_entry(monkeypatch, capsys):
    monkeypatch.setattr(frozen, "_check_hook_runner", lambda: "runner")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    _, report = _report(monkeypatch, capsys)
    assert set(report["checks"]) == QT_ONLY_KEYS | {"hook_runner"}


def test_tk_root_carries_the_qt_root_result(monkeypatch, capsys):
    _, report = _report(monkeypatch, capsys, window=True)
    assert report["checks"]["tk_root"] == report["checks"]["qt_root"] == {"ok": True, "detail": "xcb"}


def test_blocked_pyside6_fails_qt_qt_root_and_tk_root_only(monkeypatch, capsys):
    code, report = _report(monkeypatch, capsys, window=True, qt_blocked=True)
    assert code == 1
    failed = sorted(name for name, check in report["checks"].items() if not check["ok"])
    assert failed == ["qt", "qt_root", "tk_root"]


@pytest.mark.parametrize("inherited", [False, True])
def test_shipped_judge_accepts_the_qt_only_report(monkeypatch, capsys, inherited):
    code, report = _report(monkeypatch, capsys, window=True, inherited=inherited)
    assert code == 0
    _judge(report)


def test_shipped_judge_rejects_a_failed_qt_report_and_names_the_detail(monkeypatch, capsys):
    from tokitty.update_install import UpdateInstallError

    code, report = _report(monkeypatch, capsys, window=True, qt_blocked=True)
    assert code == 1
    with pytest.raises(UpdateInstallError, match=r"failed its self-check: qt, qt_root, tk_root \(qt: .+\)\.$"):
        _judge(report)


# --- qt_platform_env (#115) -------------------------------------------------


def _platform_env(environ, **kwargs):
    kwargs.setdefault("frozen", True)
    kwargs.setdefault("platform", "linux")
    return frozen.qt_platform_env(environ, **kwargs)


@pytest.mark.parametrize("value", ["wayland", "wayland-egl", "wayland;wayland-egl", "Wayland:foo=1", " wayland ; wayland-egl"])
def test_a_wayland_only_value_becomes_xcb_in_a_frozen_linux_build(capsys, value):
    environ = {"QT_QPA_PLATFORM": value, "DISPLAY": ":0"}
    assert _platform_env(environ) == "xcb"
    assert environ["QT_QPA_PLATFORM"] == "xcb"
    err = capsys.readouterr().err
    assert err == (
        f"tokitty: QT_QPA_PLATFORM={value} needs the Wayland plugin, which this build leaves out; using xcb\n"
    )


@pytest.mark.parametrize(
    "environ, kwargs",
    [
        ({"QT_QPA_PLATFORM": "wayland"}, {}),
        ({"QT_QPA_PLATFORM": "wayland", "DISPLAY": ""}, {}),
        ({"QT_QPA_PLATFORM": "wayland;xcb", "DISPLAY": ":0"}, {}),
        ({"QT_QPA_PLATFORM": "xcb", "DISPLAY": ":0"}, {}),
        ({"QT_QPA_PLATFORM": "offscreen", "DISPLAY": ":0"}, {}),
        ({"QT_QPA_PLATFORM": "", "DISPLAY": ":0"}, {}),
        ({"DISPLAY": ":0"}, {}),
        ({"QT_QPA_PLATFORM": "wayland", "DISPLAY": ":0"}, {"frozen": False}),
        ({"QT_QPA_PLATFORM": "wayland", "DISPLAY": ":0"}, {"platform": "win32"}),
        ({"QT_QPA_PLATFORM": "wayland", "DISPLAY": ":0"}, {"platform": "darwin"}),
    ],
)
def test_qt_platform_env_changes_nothing_otherwise(capsys, environ, kwargs):
    before = dict(environ)
    assert _platform_env(environ, **kwargs) is None
    assert environ == before
    assert capsys.readouterr().err == ""


def test_qt_platform_env_defaults_to_the_process(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert frozen.qt_platform_env() is None
    assert os.environ["QT_QPA_PLATFORM"] == "wayland"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert frozen.qt_platform_env() == "xcb"
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"
