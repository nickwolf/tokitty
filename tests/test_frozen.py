"""Tests for tokitty/frozen.py: the translocation guard (#48 Task 3)."""
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
