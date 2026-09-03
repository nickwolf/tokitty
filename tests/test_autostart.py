import ast
import os
import plistlib
import subprocess
from pathlib import Path, PureWindowsPath

from tokitty.autostart import (
    LAUNCHER_FILENAME,
    LinuxDesktopEntryBackend,
    MacLaunchAgentBackend,
    WindowsRegistryBackend,
    _windows_pythonw_path,
    ensure_current,
    get_backend,
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


class _FakeRegistry:
    def __init__(self):
        self.value = None

    def read_value(self):
        return self.value

    def write_value(self, value):
        self.value = value

    def delete_value(self):
        self.value = None


def test_windows_backend_register_writes_quoted_value():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    command = [
        r"C:\Program Files\Python313\pythonw.exe",
        r"C:\Users\nick\AppData\Local\Tokitty\autostart_launcher.pyw",
    ]
    backend.register(command)
    # subprocess.list2cmdline only quotes the arguments that need it (the
    # ones containing spaces), not every argument -- so the launcher path
    # here, which has no spaces, stays unquoted. Asserted against the real
    # stdlib function's actual output rather than a hand-typed guess, since
    # list2cmdline's quoting rules (only-when-needed, doubled backslashes
    # before an embedded quote) are exactly the part a hand-typed string
    # tends to get wrong.
    assert backend._registry.value == subprocess.list2cmdline(command)
    assert backend._registry.value == (
        '"C:\\Program Files\\Python313\\pythonw.exe" '
        "C:\\Users\\nick\\AppData\\Local\\Tokitty\\autostart_launcher.pyw"
    )


def test_windows_backend_register_quotes_every_space_containing_arg():
    # Required acceptance criterion (Global Constraints): interpreter AND
    # launcher paths can both contain spaces, e.g. a launcher written under
    # a per-user profile directory like "C:\Users\Nick Wolf\...". Confirms
    # quoting is applied per-argument, not just to the first one.
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    command = [
        r"C:\Program Files\Python313\pythonw.exe",
        r"C:\Users\Nick Wolf\AppData\Local\Tokitty\autostart_launcher.pyw",
    ]
    backend.register(command)
    assert backend._registry.value == (
        '"C:\\Program Files\\Python313\\pythonw.exe" '
        '"C:\\Users\\Nick Wolf\\AppData\\Local\\Tokitty\\autostart_launcher.pyw"'
    )


def test_windows_backend_is_registered_false_initially():
    assert WindowsRegistryBackend(registry=_FakeRegistry()).is_registered() is False


def test_windows_backend_register_then_is_registered_true():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register(["a", "b"])
    assert backend.is_registered() is True


def test_windows_backend_deregister_clears_value():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register(["a", "b"])
    backend.deregister()
    assert backend.is_registered() is False


def test_windows_backend_is_current_true_after_register():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    command = ["a", "b"]
    backend.register(command)
    assert backend.is_current(command) is True


def test_windows_backend_is_current_false_after_interpreter_path_changes():
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register([r"C:\Old\pythonw.exe", "launcher.pyw"])
    assert backend.is_current([r"C:\New\pythonw.exe", "launcher.pyw"]) is False


def test_windows_backend_is_current_false_when_not_registered():
    assert WindowsRegistryBackend(registry=_FakeRegistry()).is_current(["a", "b"]) is False


def test_windows_backend_never_imports_real_winreg_when_registry_injected():
    # This whole test class already runs on every CI platform (Linux/macOS
    # have no winreg module at all), so simply running it green is most of
    # this proof. This test makes the point explicit: winreg must not even
    # be present in sys.modules as a side effect of using a fake registry.
    import sys as _sys

    _sys.modules.pop("winreg", None)
    backend = WindowsRegistryBackend(registry=_FakeRegistry())
    backend.register(["a", "b"])
    backend.is_registered()
    backend.is_current(["a", "b"])
    backend.deregister()
    assert "winreg" not in _sys.modules


def test_real_windows_registry_import_is_lazy_per_method():
    # Confirms _RealWindowsRegistry itself (not just the backend wrapping
    # it) never imports winreg at construction time -- only inside each
    # method body, which is what makes `import tokitty.autostart` safe on
    # every non-Windows CI runner.
    import sys as _sys

    from tokitty.autostart import _RealWindowsRegistry

    _sys.modules.pop("winreg", None)
    _RealWindowsRegistry()
    assert "winreg" not in _sys.modules


def test_mac_backend_register_writes_plist(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    command = ["/usr/bin/python3", "/Users/nick/Library/Application Support/Tokitty/autostart_launcher.pyw"]
    backend.register(command)
    data = plistlib.loads((tmp_path / "com.nickwolf.tokitty.plist").read_bytes())
    assert data["Label"] == "com.nickwolf.tokitty"
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"] == command


def test_mac_backend_is_registered_roundtrip(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    assert backend.is_registered() is False
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    assert backend.is_registered() is True


def test_mac_backend_deregister_removes_plist(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    backend.deregister()
    assert backend.is_registered() is False


def test_mac_backend_deregister_when_absent_does_not_raise(tmp_path):
    MacLaunchAgentBackend(launch_agents_dir=tmp_path).deregister()


def test_mac_backend_is_current(tmp_path):
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    command = ["/usr/bin/python3", "launcher.pyw"]
    backend.register(command)
    assert backend.is_current(command) is True
    assert backend.is_current(["/usr/bin/python3", "different.pyw"]) is False


def test_mac_backend_register_writes_via_tmp_file_and_replace(tmp_path, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("tokitty.autostart.os.replace", spy_replace)
    backend = MacLaunchAgentBackend(launch_agents_dir=tmp_path)
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    assert len(calls) == 1
    assert calls[0][0].endswith("com.nickwolf.tokitty.plist.tmp")
    assert calls[0][1].endswith("com.nickwolf.tokitty.plist")


def test_linux_backend_register_writes_desktop_file(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "/home/nick/.config/tokitty/autostart_launcher.pyw"])
    content = (tmp_path / "tokitty.desktop").read_text(encoding="utf-8")
    assert "Exec=/usr/bin/python3 /home/nick/.config/tokitty/autostart_launcher.pyw" in content
    assert "X-GNOME-Autostart-enabled=true" in content


def test_linux_backend_register_quotes_space_containing_path(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "/home/nick/My Documents/tokitty/autostart_launcher.pyw"])
    content = (tmp_path / "tokitty.desktop").read_text(encoding="utf-8")
    assert 'Exec=/usr/bin/python3 "/home/nick/My Documents/tokitty/autostart_launcher.pyw"' in content


def test_linux_backend_is_registered_roundtrip(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    assert backend.is_registered() is False
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    assert backend.is_registered() is True


def test_linux_backend_deregister_removes_file(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    backend.deregister()
    assert backend.is_registered() is False


def test_linux_backend_deregister_when_absent_does_not_raise(tmp_path):
    LinuxDesktopEntryBackend(autostart_dir=tmp_path).deregister()


def test_linux_backend_is_current_detects_drift(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    backend.register(["/usr/bin/python3", "launcher.pyw"])
    assert backend.is_current(["/usr/bin/python3", "launcher.pyw"]) is True
    assert backend.is_current(["/usr/bin/python3.11", "launcher.pyw"]) is False


def test_linux_backend_is_current_false_when_not_registered(tmp_path):
    backend = LinuxDesktopEntryBackend(autostart_dir=tmp_path)
    assert backend.is_current(["/usr/bin/python3", "launcher.pyw"]) is False


def test_get_backend_selects_windows():
    assert isinstance(get_backend(platform="win32"), WindowsRegistryBackend)


def test_get_backend_selects_mac():
    assert isinstance(get_backend(platform="darwin"), MacLaunchAgentBackend)


def test_get_backend_selects_linux():
    assert isinstance(get_backend(platform="linux"), LinuxDesktopEntryBackend)


def test_get_backend_none_for_unknown_platform():
    assert get_backend(platform="freebsd13") is None


class _RecordingBackend:
    def __init__(self, registered=False, current_command=None, raise_on_register=False):
        self._registered = registered
        self._command = current_command
        self._raise_on_register = raise_on_register
        self.registered_calls = []

    def is_registered(self):
        return self._registered

    def is_current(self, command):
        return self._command == command

    def register(self, command):
        if self._raise_on_register:
            raise OSError("permission denied")
        self.registered_calls.append(command)
        self._command = command
        self._registered = True


def test_ensure_current_noop_when_not_registered(tmp_path):
    backend = _RecordingBackend(registered=False)
    changed = ensure_current(tmp_path, backend, repo_root=tmp_path / "repo")
    assert changed is False
    assert backend.registered_calls == []


def test_ensure_current_rewrites_registration_on_interpreter_drift(tmp_path):
    stale = ["/usr/bin/python3.10", str(tmp_path / LAUNCHER_FILENAME)]
    backend = _RecordingBackend(registered=True, current_command=stale)
    changed = ensure_current(
        tmp_path, backend, repo_root=tmp_path / "repo", executable="/usr/bin/python3.12", platform="linux",
    )
    assert changed is True
    assert backend.registered_calls[-1][0] == "/usr/bin/python3.12"


def test_ensure_current_noop_when_already_current(tmp_path):
    fresh = resolve_launch_command(tmp_path, executable="/usr/bin/python3", platform="linux", repo_root=tmp_path / "repo")
    backend = _RecordingBackend(registered=True, current_command=fresh)
    changed = ensure_current(
        tmp_path, backend, repo_root=tmp_path / "repo", executable="/usr/bin/python3", platform="linux",
    )
    assert changed is False
    assert backend.registered_calls == []


def test_ensure_current_always_rewrites_the_launcher_file(tmp_path):
    """Repo-moved case: the registered command's argv never encodes the
    repo path (only the launcher file's content does), so the only way
    to repair a moved repo is to unconditionally regenerate the launcher
    file -- covered even when the registered command doesn't change at
    all. Both halves of the asymmetry are asserted here: the launcher
    file now points at the new root, while the registered command (same
    state_dir, same interpreter) is byte-identical and therefore never
    rewritten."""
    fresh = resolve_launch_command(
        tmp_path, executable="/usr/bin/python3", platform="linux", repo_root=tmp_path / "old_repo"
    )
    backend = _RecordingBackend(registered=True, current_command=fresh)
    ensure_current(tmp_path, backend, repo_root=tmp_path / "new_repo", executable="/usr/bin/python3", platform="linux")
    content = (tmp_path / LAUNCHER_FILENAME).read_text(encoding="utf-8")
    # repr(), not the raw path string: launcher_content() embeds repo_root
    # via repr() (see its docstring), which doubles backslashes on a
    # Windows-shaped path. A bare `str(path) in content` check is exactly
    # the kind of assertion this repo's own accounts-setup-ui branch got
    # burned by -- it passes on POSIX (no backslashes to escape) and fails
    # on real Windows, confirmed by hand against
    # C:\Users\nickw\AppData\Local\Programs\Python\Python313\python.exe.
    assert repr(str(tmp_path / "old_repo")) not in content
    assert repr(str(tmp_path / "new_repo")) in content
    assert backend.registered_calls == []


def test_ensure_current_swallows_oserror_and_returns_false(tmp_path):
    stale = ["/usr/bin/python3.10", str(tmp_path / LAUNCHER_FILENAME)]
    backend = _RecordingBackend(registered=True, current_command=stale, raise_on_register=True)
    changed = ensure_current(
        tmp_path, backend, repo_root=tmp_path / "repo", executable="/usr/bin/python3.12", platform="linux",
    )
    assert changed is False


def test_write_launcher_and_register_writes_launcher_then_registers(tmp_path):
    """The shared helper behind both install_autostart and the menu
    toggle: write_launcher_file, then backend.register with the resolved
    command, in that order and nothing else."""
    from tokitty.autostart import write_launcher_and_register

    backend = _RecordingBackend(registered=False)
    write_launcher_and_register(tmp_path, backend)
    assert (tmp_path / LAUNCHER_FILENAME).is_file()
    assert backend.registered_calls == [resolve_launch_command(tmp_path)]


def test_install_autostart_registers_via_backend(tmp_path, monkeypatch):
    from tokitty import autostart

    fake_backend = _FakeToggleBackendForCli()
    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: fake_backend)

    assert autostart.install_autostart() == 0
    assert fake_backend.registered is True
    assert (tmp_path / autostart.LAUNCHER_FILENAME).is_file()


def test_uninstall_autostart_deregisters_via_backend(tmp_path, monkeypatch):
    from tokitty import autostart

    fake_backend = _FakeToggleBackendForCli(registered=True)
    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: fake_backend)

    assert autostart.uninstall_autostart() == 0
    assert fake_backend.registered is False


def test_install_autostart_unsupported_platform_returns_1(tmp_path, monkeypatch, capsys):
    from tokitty import autostart

    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: None)

    assert autostart.install_autostart() == 1
    assert "not supported" in capsys.readouterr().err


def test_uninstall_autostart_unsupported_platform_returns_1(tmp_path, monkeypatch, capsys):
    from tokitty import autostart

    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: None)

    assert autostart.uninstall_autostart() == 1
    assert "not supported" in capsys.readouterr().err


def test_install_autostart_oserror_returns_1(tmp_path, monkeypatch, capsys):
    from tokitty import autostart

    class _RaisingBackend:
        def is_registered(self):
            return False

        def register(self, command):
            raise OSError("permission denied")

        def deregister(self):
            pass

        def is_current(self, command):
            return False

    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: _RaisingBackend())

    assert autostart.install_autostart() == 1
    assert "permission denied" in capsys.readouterr().err


def test_uninstall_autostart_oserror_returns_1(tmp_path, monkeypatch, capsys):
    from tokitty import autostart

    class _RaisingBackend:
        def is_registered(self):
            return True

        def register(self, command):
            pass

        def deregister(self):
            raise OSError("permission denied")

        def is_current(self, command):
            return False

    monkeypatch.setattr(autostart, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(autostart, "get_backend", lambda: _RaisingBackend())

    assert autostart.uninstall_autostart() == 1
    assert "permission denied" in capsys.readouterr().err


class _FakeToggleBackendForCli:
    def __init__(self, registered=False):
        self.registered = registered

    def is_registered(self):
        return self.registered

    def is_current(self, command):
        return False

    def register(self, command):
        self.registered = True

    def deregister(self):
        self.registered = False
