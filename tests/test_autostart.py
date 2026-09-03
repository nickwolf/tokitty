import ast
import os
from pathlib import Path, PureWindowsPath

from tokitty.autostart import (
    LAUNCHER_FILENAME,
    _windows_pythonw_path,
    launcher_content,
    resolve_launch_command,
    write_launcher_file,
)


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


def test_launcher_content_is_valid_python():
    ast.parse(launcher_content(Path("/home/nick/tokitty")))


def test_launcher_content_imports_main_from_dunder_main():
    content = launcher_content(Path("/home/nick/tokitty"))
    assert "from tokitty.__main__ import main" in content
    assert content.strip().endswith("main()")


def test_launcher_content_embeds_repo_root_with_a_space(tmp_path):
    repo_root = tmp_path / "My Tools" / "tokitty"
    content = launcher_content(repo_root)
    ast.parse(content)
    assert repr(str(repo_root)) in content


def test_launcher_content_handles_windows_style_backslashes():
    repo_root = PureWindowsPath(r"C:\Program Files\tokitty")
    content = launcher_content(repo_root)
    ast.parse(content)
    assert repr(str(repo_root)) in content


def test_write_launcher_file_creates_file_at_state_dir(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = write_launcher_file(state_dir, tmp_path / "repo")
    assert path == state_dir / "autostart_launcher.pyw"
    assert path.is_file()


def test_write_launcher_file_uses_tmp_file_and_replace(tmp_path, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("tokitty.autostart.os.replace", spy_replace)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    write_launcher_file(state_dir, tmp_path / "repo")
    assert len(calls) == 1
    assert calls[0][0].endswith("autostart_launcher.pyw.tmp")
    assert calls[0][1].endswith("autostart_launcher.pyw")


def test_write_launcher_file_is_idempotent(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = write_launcher_file(state_dir, tmp_path / "repo")
    first = path.read_text(encoding="utf-8")
    write_launcher_file(state_dir, tmp_path / "repo")
    assert path.read_text(encoding="utf-8") == first


def test_write_launcher_file_defaults_repo_root(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = write_launcher_file(state_dir)
    assert "tokitty" in path.read_text(encoding="utf-8")


def test_generated_launcher_imports_from_a_foreign_cwd(tmp_path):
    """The whole reason the launcher exists: `-m tokitty` resolves only
    from the repo root, so the launcher must make `import tokitty` work
    from a cwd that is not the repo. Every other test here asserts on the
    launcher's text; this one actually runs it, in a subprocess whose cwd
    is elsewhere. The final main() line is stripped so the GUI never
    starts."""
    import subprocess
    import sys as _sys

    from tokitty.autostart import _default_repo_root

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()

    launcher = write_launcher_file(state_dir, _default_repo_root())
    body = launcher.read_text(encoding="utf-8").replace("main()\n", "")

    probe = body + "print('RESOLVED', main.__name__)\n"
    result = subprocess.run(
        [_sys.executable, "-c", probe],
        cwd=str(foreign_cwd),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "RESOLVED main" in result.stdout
