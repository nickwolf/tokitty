"""Tests for the Qt steps of freeze/verify_artifact.py (#115): the Qt-mode
self-check, bundle_contents and tk_without_qt. The frozen app is faked with a
small Python script that prints a canned self-check report; nothing here needs
a display or a real build."""
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO / "freeze" / "verify_artifact.py"
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the fake app is an executable script")


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_artifact_qt_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


va = _load_module()

REQUIRED_SOURCE = ("pystray/__init__.py", "PySide6/__init__.py", "shiboken6/__init__.py")


def _touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_linux_bundle(root: Path, qt_version: str = "6.12.0") -> Path:
    """A minimal Linux bundle that passes bundle_contents."""
    internal = root / "_internal"
    for rel in REQUIRED_SOURCE:
        _touch(internal / rel)
    _touch(internal / "PySide6" / "QtDBus.abi3.so")
    _touch(internal / "PySide6" / "Qt" / "plugins" / "platforms" / "libqxcb.so")
    _touch(internal / "PySide6" / "Qt" / "lib" / "libQt6Core.so.6")
    os.symlink("PySide6/Qt/lib/libQt6Core.so.6", internal / "libQt6Core.so.6")
    licenses = root / "licenses"
    for name in ("LICENSE", "LGPL-3.0.txt", "GPL-3.0.txt", "pystray-NOTICE.txt"):
        _touch(licenses / name)
    _touch(licenses / "Qt-NOTICE.txt", f"Qt {qt_version} notices")
    return root


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "linux")


def run_contents(app_dir, qt_version="6.12.0"):
    return va.step_bundle_contents({"app_dir": app_dir, "repo_root": REPO, "qt_version": qt_version})


def test_a_trimmed_linux_bundle_passes(tmp_path, linux):
    result = run_contents(make_linux_bundle(tmp_path))
    assert result["ok"], result
    assert result["detail"]["lgpl_source_in"]["PySide6/__init__.py"] == "_internal"
    assert result["detail"]["qt_dbus"] is True


@pytest.mark.parametrize("name", ["LICENSE", "LGPL-3.0.txt", "GPL-3.0.txt", "Qt-NOTICE.txt", "pystray-NOTICE.txt"])
def test_a_missing_licence_file_fails(tmp_path, linux, name):
    app = make_linux_bundle(tmp_path)
    (app / "licenses" / name).unlink()
    result = run_contents(app)
    assert not result["ok"]
    assert name in " ".join(result["detail"]["problems"])


def test_the_notice_must_name_the_reported_qt_version(tmp_path, linux):
    app = make_linux_bundle(tmp_path, qt_version="6.11.0")
    assert not run_contents(app, qt_version="6.12.0")["ok"]
    assert run_contents(app, qt_version=None)["ok"]


@pytest.mark.parametrize("rel", REQUIRED_SOURCE)
def test_an_lgpl_package_missing_as_a_source_file_fails(tmp_path, linux, rel):
    app = make_linux_bundle(tmp_path)
    (app / "_internal" / rel).unlink()
    result = run_contents(app)
    assert not result["ok"]
    assert rel in " ".join(result["detail"]["problems"])


@pytest.mark.parametrize(
    "rel",
    [
        "PySide6/Qt/plugins/imageformats/libqjpeg.so",
        "PySide6/Qt/lib/libQt6Network.so.6",
        "PySide6/QtNetwork.abi3.so",
        "PySide6/Qt/plugins/platforms/libqoffscreen.so",
        "PySide6/Qt/plugins/platforms/libqwayland-generic.so",
        "PySide6/Qt/plugins/platformthemes/libqgtk3.so",
        "PySide6/Qt/plugins/tls/libqcertonlybackend.so",
    ],
)
def test_a_file_the_trim_drops_fails(tmp_path, linux, rel):
    app = make_linux_bundle(tmp_path)
    _touch(app / "_internal" / rel)
    result = run_contents(app)
    assert not result["ok"]
    assert rel in " ".join(result["detail"]["problems"])


def test_a_dropped_name_outside_pyside6_is_not_the_trims_business(tmp_path, linux):
    app = make_linux_bundle(tmp_path)
    _touch(app / "_internal" / "tokitty" / "tls" / "generic.py")
    _touch(app / "_internal" / "libQt6Network.so.6")
    assert run_contents(app)["ok"]


def test_the_platform_plugin_and_qtdbus_are_required(tmp_path, linux):
    app = make_linux_bundle(tmp_path)
    (app / "_internal" / "PySide6" / "Qt" / "plugins" / "platforms" / "libqxcb.so").unlink()
    (app / "_internal" / "PySide6" / "QtDBus.abi3.so").unlink()
    problems = " ".join(run_contents(app)["detail"]["problems"])
    assert "libqxcb" in problems and "QtDBus" in problems


def test_gtk_theme_libraries_at_the_top_of_internal_fail_on_linux(tmp_path, linux):
    app = make_linux_bundle(tmp_path)
    _touch(app / "_internal" / "libgtk-3.so.0")
    result = run_contents(app)
    assert not result["ok"]
    assert "libgtk-3.so.0" in " ".join(result["detail"]["problems"])


@posix_only
def test_a_dangling_symlink_anywhere_fails(tmp_path, linux):
    app = make_linux_bundle(tmp_path)
    os.symlink("PySide6/Qt/lib/libQt6Gone.so.6", app / "_internal" / "libQt6Gone.so.6")
    result = run_contents(app)
    assert not result["ok"]
    assert "libQt6Gone.so.6" in " ".join(result["detail"]["problems"])


def test_windows_needs_its_plugin_but_not_qtdbus(tmp_path, monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "win32")
    internal = tmp_path / "_internal"
    for rel in REQUIRED_SOURCE:
        _touch(internal / rel)
    _touch(internal / "PySide6" / "Qt" / "plugins" / "platforms" / "qwindows.dll")
    licenses = tmp_path / "licenses"
    for name in ("LICENSE", "LGPL-3.0.txt", "GPL-3.0.txt", "Qt-NOTICE.txt", "pystray-NOTICE.txt"):
        _touch(licenses / name, "Qt 6.12.0")
    result = run_contents(tmp_path)
    assert result["ok"], result
    assert result["detail"]["qt_dbus"] is False
    _touch(internal / "PySide6" / "Qt" / "plugins" / "imageformats" / "qjpeg.dll")
    assert not run_contents(tmp_path)["ok"]


def make_mac_bundle(root: Path, source_root: str) -> None:
    contents = root / "Tokitty.app" / "Contents"
    base = contents / source_root
    for rel in REQUIRED_SOURCE:
        _touch(base / rel)
    _touch(base / "PySide6" / "Qt" / "lib" / "QtDBus.framework" / "Versions" / "A" / "QtDBus")
    _touch(base / "PySide6" / "Qt" / "plugins" / "platforms" / "libqcocoa.dylib")
    for name in ("LICENSE", "LGPL-3.0.txt", "GPL-3.0.txt", "Qt-NOTICE.txt", "pystray-NOTICE.txt"):
        _touch(contents / "Resources" / "licenses" / name, "Qt 6.12.0")


@pytest.mark.parametrize("source_root", ["Frameworks", "Resources"])
def test_macos_accepts_either_folder_and_says_which(tmp_path, monkeypatch, source_root):
    monkeypatch.setattr(va.sys, "platform", "darwin")
    make_mac_bundle(tmp_path, source_root)
    result = run_contents(tmp_path)
    assert result["ok"], result
    assert result["detail"]["lgpl_source_in"]["pystray/__init__.py"] == f"Contents/{source_root}"
    assert result["detail"]["pyside6_in"] == [f"Contents/{source_root}"]


def test_macos_judges_the_pyside6_tree_in_both_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "darwin")
    make_mac_bundle(tmp_path, "Frameworks")
    _touch(tmp_path / "Tokitty.app" / "Contents" / "Resources" / "PySide6" / "Qt" / "lib" / "QtNetwork.framework" / "Versions" / "A" / "QtNetwork")
    result = run_contents(tmp_path)
    assert not result["ok"]
    assert "QtNetwork.framework" in " ".join(result["detail"]["problems"])


# ---- the self-check steps, against a fake executable ----

FAKE_APP = '''#!{python}
import json, os, sys
internal = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "_internal")
qt_mode = os.environ.get("TOKITTY_QT") == "1"
checks = {{"tkinter": {{"ok": True, "detail": "8.6"}}, "tk_root": {{"ok": True, "detail": "8.6"}}}}
if qt_mode:
    checks["qt"] = {{"ok": True, "detail": "{qt_version}"}}
    if {qt_root}:
        checks["qt_root"] = {{"ok": True, "detail": "xcb"}}
if qt_mode and os.environ.get("QT_QPA_PLATFORM"):
    checks["qpa_leaked"] = {{"ok": False, "detail": os.environ["QT_QPA_PLATFORM"]}}
if not qt_mode and os.path.exists(os.path.join(internal, "libQt6Core.so.6")) and "{break_without_core}" == "1":
    checks["qtcore"] = {{"ok": False, "detail": "present"}}
report = {{"checks": checks, "state_dir": "{state_dir}", "toolkits": {toolkits}}}
print(json.dumps(report))
sys.exit(0 if all(c["ok"] for c in checks.values()) else 1)
'''


def make_fake_app(root: Path, state_dir: Path, *, toolkits=("qt", "tk"), qt_version="6.12.0", qt_root=True, break_without_core=False) -> None:
    make_linux_bundle(root)
    script = root / "tokitty"
    script.write_text(
        FAKE_APP.format(
            python=sys.executable,
            qt_version=qt_version,
            qt_root=qt_root,
            break_without_core="1" if break_without_core else "0",
            state_dir=state_dir,
            toolkits=json.dumps(list(toolkits)),
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    hook = root / "tokitty-hook"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)


def make_ctx(tmp_path, app, **kwargs):
    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    ctx = {"app_dir": app, "work": work, "env": env, "repo_root": REPO}
    ctx["gui"] = app / "tokitty"
    ctx.update(kwargs)
    return ctx


@posix_only
def test_self_check_runs_again_in_qt_mode_without_qpa_platform(tmp_path, linux):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state")
    ctx = make_ctx(tmp_path, app)
    result = va.step_self_check(ctx)
    assert result["ok"], result
    assert ctx["qt_version"] == "6.12.0"
    assert result["detail"]["qt_mode"]["checks"]["qt_root"]["ok"]
    assert "qt" not in result["detail"]["checks"]


@posix_only
def test_self_check_fails_when_the_qt_mode_report_lacks_qt_root(tmp_path, linux):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state", qt_root=False)
    result = va.step_self_check(make_ctx(tmp_path, app))
    assert not result["ok"]
    assert "qt_root" in result["detail"]


@posix_only
def test_a_tk_only_build_is_checked_once_and_keeps_its_detail_shape(tmp_path, linux):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state", toolkits=("tk",))
    ctx = make_ctx(tmp_path, app)
    result = va.step_self_check(ctx)
    assert result["ok"] and "qt_mode" not in result["detail"]
    assert "qt_version" not in ctx


@posix_only
def test_tk_without_qt_deletes_qtcore_from_a_copy_and_still_passes(tmp_path, linux):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state", break_without_core=True)
    ctx = make_ctx(tmp_path, app, toolkits=["qt", "tk"])
    result = va.step_tk_without_qt(ctx)
    assert result["ok"], result
    assert sorted(result["detail"]["deleted"]) == ["_internal/PySide6/Qt/lib/libQt6Core.so.6", "_internal/libQt6Core.so.6"]
    assert result["detail"]["tk_root"]["ok"]
    assert (app / "_internal" / "PySide6" / "Qt" / "lib" / "libQt6Core.so.6").is_file()
    assert not (ctx["work"] / "tk-without-qt").exists()


@posix_only
def test_tk_without_qt_fails_when_the_tk_report_breaks_without_qtcore(tmp_path, linux):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state")
    (app / "tokitty").write_text(
        "#!/bin/sh\necho '{\"checks\": {\"tk_root\": {\"ok\": false, \"detail\": \"ImportError\"}}, \"state_dir\": \"/x\"}'\nexit 1\n",
        encoding="utf-8",
    )
    result = va.step_tk_without_qt(make_ctx(tmp_path, app, toolkits=["qt", "tk"]))
    assert not result["ok"]


def test_tk_without_qt_is_skipped_for_a_build_without_qt(tmp_path, linux):
    result = va.step_tk_without_qt({"toolkits": ["tk"], "app_dir": tmp_path, "work": tmp_path})
    assert result["ok"] and "skipped" in result["detail"]


def test_the_new_steps_run_after_self_check():
    names = [name for name, _ in va.STEPS]
    assert names[names.index("self_check") + 1 : names.index("self_check") + 3] == ["bundle_contents", "tk_without_qt"]
