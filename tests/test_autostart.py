from pathlib import Path

from tokitty.autostart import LAUNCHER_FILENAME, _windows_pythonw_path, resolve_launch_command


def test_frozen_returns_executable_alone(tmp_path):
    result = resolve_launch_command(tmp_path, frozen=True, executable=r"C:\Program Files\Tokitty\tokitty.exe")
    assert result == [r"C:\Program Files\Tokitty\tokitty.exe"]


def test_repo_clone_windows_uses_pythonw_and_launcher_path(tmp_path):
    result = resolve_launch_command(
        tmp_path, frozen=False, executable=r"C:\Program Files\Python313\python.exe",
        platform="win32", repo_root=Path(r"C:\Tools\tokitty"),
    )
    assert result == [r"C:\Program Files\Python313\pythonw.exe", str(tmp_path / LAUNCHER_FILENAME)]


def test_repo_clone_linux_uses_executable_directly(tmp_path):
    result = resolve_launch_command(
        tmp_path, frozen=False, executable="/usr/bin/python3", platform="linux",
        repo_root=Path("/home/nick/tokitty"),
    )
    assert result == ["/usr/bin/python3", str(tmp_path / LAUNCHER_FILENAME)]


def test_repo_clone_macos_uses_executable_directly(tmp_path):
    result = resolve_launch_command(
        tmp_path, frozen=False, executable="/usr/bin/python3", platform="darwin",
        repo_root=Path("/Users/nick/tokitty"),
    )
    assert result == ["/usr/bin/python3", str(tmp_path / LAUNCHER_FILENAME)]


def test_windows_pythonw_path_swaps_python_exe_for_pythonw_exe():
    assert _windows_pythonw_path(r"C:\Program Files\Python313\python.exe") == r"C:\Program Files\Python313\pythonw.exe"


def test_windows_pythonw_path_leaves_pythonw_exe_unchanged():
    result = _windows_pythonw_path(r"C:\Program Files\Python313\pythonw.exe")
    assert result == r"C:\Program Files\Python313\pythonw.exe"


def test_windows_pythonw_path_passes_through_unrecognized_shape():
    assert _windows_pythonw_path(r"C:\Tools\venv\Scripts\tokitty.exe") == r"C:\Tools\venv\Scripts\tokitty.exe"


def test_windows_pythonw_path_handles_space_containing_directory():
    # Required acceptance criterion: real interpreter installs commonly
    # live under "C:\Program Files (x86)\...". PureWindowsPath makes this
    # testable on Linux/macOS CI, not just on real Windows.
    result = _windows_pythonw_path(r"C:\Program Files (x86)\Python313\python.exe")
    assert result == r"C:\Program Files (x86)\Python313\pythonw.exe"


def test_default_repo_root_is_the_real_repository_root():
    from tokitty.autostart import _default_repo_root

    assert (_default_repo_root() / "tokitty" / "__init__.py").is_file()
