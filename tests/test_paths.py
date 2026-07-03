import sys
from pathlib import Path

from tokitty.paths import get_state_dir


def test_get_state_dir_creates_directory_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = get_state_dir()

    assert result == tmp_path / ".config" / "tokitty"
    assert result.is_dir()


def test_get_state_dir_respects_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "customcfg"))

    result = get_state_dir()

    assert result == tmp_path / "customcfg" / "tokitty"
    assert result.is_dir()


def test_get_state_dir_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    result = get_state_dir()

    assert result == tmp_path / "AppData" / "Local" / "Tokitty"
    assert result.is_dir()


def test_get_state_dir_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = get_state_dir()

    assert result == tmp_path / "Library" / "Application Support" / "Tokitty"
    assert result.is_dir()
