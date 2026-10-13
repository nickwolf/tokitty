"""The real Qt self-check functions (#113 slice 4); the declaration logic
around them is tested headless in tests/test_frozen.py."""
import json
import sys
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


def test_self_check_passes_a_real_qt_declaration(qapp, monkeypatch, capsys):
    monkeypatch.setenv("TOKITTY_BUILD_TOOLKITS", "qt")
    monkeypatch.setenv("TOKITTY_SELF_CHECK_TK", "1")
    monkeypatch.setitem(sys.modules, "pystray", types.ModuleType("pystray"))
    assert frozen.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    checks = report["checks"]
    assert checks["qt"] == {"ok": True, "detail": qVersion()}
    assert checks["qt_root"]["ok"] is True
    assert checks["tk_root"] == checks["qt_root"]
    assert "tkinter" not in checks
    from tokitty import update_install

    report["build_id"] = "v1.2.3"
    runner = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(report).encode(), returncode=0)  # noqa: E731
    update_install.run_self_check("tokitty", "v1.2.3", runner=runner, sys_platform="linux")
