import sys

import pytest

from tokitty import dpi


def test_scale_for_is_dpi_over_96():
    assert dpi.scale_for(96) == 1.0
    assert dpi.scale_for(120) == 1.25
    assert dpi.scale_for(144) == 1.5
    assert dpi.scale_for(192) == 2.0


@pytest.mark.skipif(sys.platform == "win32", reason="the no-op path is for everything else")
def test_awareness_and_scale_are_a_no_op_off_windows():
    assert dpi.set_awareness() == "not-windows"
    assert dpi.system_dpi() == dpi.LOGICAL_DPI
    assert dpi.init() == 1.0


def test_system_dpi_falls_back_to_the_logical_baseline(monkeypatch):
    monkeypatch.setattr(dpi.sys, "platform", "linux")
    assert dpi.system_dpi() == 96
