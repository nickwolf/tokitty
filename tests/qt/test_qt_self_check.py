"""The real Qt self-check functions (#113 slice 4, #142); the report's key set
is tested headless in tests/test_frozen.py."""
import json
import types

import pytest
from PySide6.QtCore import qVersion
from PySide6.QtGui import QGuiApplication

from tokitty import frozen

pytestmark = pytest.mark.qt


def test_check_qt_reports_the_qt_version():
    assert frozen._check_qt() == qVersion()


def test_check_qt_root_shows_a_window_and_names_the_platform(qapp):
    platform = frozen._check_qt_root()
    assert platform == QGuiApplication.platformName()
    assert platform


def test_check_qt_root_maps_the_platform_first(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(frozen, "qt_platform_env", lambda *a, **k: calls.append(1))
    frozen._check_qt_root()
    assert calls == [1]


def test_self_check_passes_with_the_real_qt_checks(qapp, monkeypatch, capsys):
    monkeypatch.setenv("TOKITTY_SELF_CHECK_TK", "1")
    # What an old tk,qt copy hands the new one must change nothing.
    monkeypatch.setenv("TOKITTY_BUILD_TOOLKITS", "tk,qt")
    monkeypatch.setenv("TOKITTY_QT", "1")
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    checks = report["checks"]
    assert checks["qt"] == {"ok": True, "detail": qVersion()}
    assert checks["qt_root"]["ok"] is True
    assert checks["tk_root"] == checks["qt_root"]
    assert "tkinter" not in checks and "pystray" not in checks and "pil" not in checks
    assert report["toolkits"] == ["qt"]
    from tokitty import update_install

    report["build_id"] = "v1.2.3"
    runner = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(report).encode(), returncode=0)  # noqa: E731
    update_install.run_self_check("tokitty", "v1.2.3", runner=runner, sys_platform="linux")
