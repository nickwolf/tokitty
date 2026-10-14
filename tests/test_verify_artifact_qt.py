"""Tests for the Qt steps of freeze/verify_artifact.py (#115, #142): the
self-check and bundle_contents. The frozen app is faked with a
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

REQUIRED_SOURCE = ("PySide6/__init__.py", "shiboken6/__init__.py")
LICENCES = ("LICENSE", "LGPL-3.0.txt", "GPL-3.0.txt", "Qt-NOTICE.txt")
FONT_FILES = ("tokitty/streamdock/fonts/Aileron-Regular.ttf", "tokitty/streamdock/fonts/NOTICE.txt")


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
    os.symlink(os.path.join("PySide6", "Qt", "lib", "libQt6Core.so.6"), internal / "libQt6Core.so.6")
    licenses = root / "licenses"
    for name in LICENCES:
        _touch(licenses / name)
    _touch(licenses / "Qt-NOTICE.txt", f"Qt {qt_version} notices")
    for rel in FONT_FILES:
        _touch(internal / rel)
    for name in va.LINUX_XCB_LIBS:
        _touch(internal / name)
    return root


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "linux")


def run_contents(app_dir, qt_version="6.12.0"):
    return va.step_bundle_contents({"app_dir": app_dir, "repo_root": REPO, "qt_version": qt_version, "toolkits": ["qt"]})


def test_a_trimmed_linux_bundle_passes(tmp_path, linux):
    result = run_contents(make_linux_bundle(tmp_path))
    assert result["ok"], result
    assert result["detail"]["lgpl_source_in"]["PySide6/__init__.py"] == "_internal"
    assert result["detail"]["qt_dbus"] is True


@pytest.mark.parametrize("name", LICENCES)
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
    os.symlink(os.path.join("PySide6", "Qt", "lib", "libQt6Gone.so.6"), app / "_internal" / "libQt6Gone.so.6")
    result = run_contents(app)
    assert not result["ok"]
    assert "libQt6Gone.so.6" in " ".join(result["detail"]["problems"])


@pytest.mark.parametrize("rel", FONT_FILES)
def test_the_font_and_its_notice_are_required(tmp_path, linux, rel):
    app = make_linux_bundle(tmp_path)
    (app / "_internal" / rel).unlink()
    result = run_contents(app)
    assert not result["ok"]
    assert rel in " ".join(result["detail"]["problems"])


@pytest.mark.parametrize("name", va.LINUX_XCB_LIBS)
def test_each_xcb_library_is_required_in_internal_on_linux(tmp_path, linux, name):
    app = make_linux_bundle(tmp_path)
    (app / "_internal" / name).unlink()
    result = run_contents(app)
    assert not result["ok"]
    assert name in " ".join(result["detail"]["problems"])


def test_the_xcb_libraries_are_not_required_off_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "win32")
    internal = tmp_path / "_internal"
    for rel in REQUIRED_SOURCE + FONT_FILES:
        _touch(internal / rel)
    _touch(internal / "PySide6" / "Qt" / "plugins" / "platforms" / "qwindows.dll")
    for name in LICENCES:
        _touch(tmp_path / "licenses" / name, "Qt 6.12.0")
    assert run_contents(tmp_path)["ok"]


@pytest.mark.parametrize(
    "rel",
    [
        "tcl/init.tcl", "tk/tk.tcl", "_tcl_data/init.tcl", "_tk_data/tk.tcl", "libtcl8.6.so", "libtk8.6.so",
        "tcl86t.dll", "tk86t.dll", "lib-dynload/_tkinter.cpython-312-x86_64-linux-gnu.so", "_tkinter.pyd",
        "PIL/__init__.py", "pillow.libs/libz.so", "pystray/__init__.py", "objc/__init__.py",
        "pyobjc_framework_Cocoa-10.0.dist-info/METADATA", "Xlib/__init__.py",
    ],
)
def test_a_forbidden_toolkit_file_or_folder_fails(tmp_path, linux, rel):
    app = make_linux_bundle(tmp_path)
    _touch(app / "_internal" / rel)
    result = run_contents(app)
    assert not result["ok"], result
    assert rel.split("/")[0] in " ".join(result["detail"]["problems"])


def test_names_that_merely_contain_a_forbidden_word_pass(tmp_path, linux):
    app = make_linux_bundle(tmp_path)
    _touch(app / "_internal" / "tokitty" / "pilot.py")
    _touch(app / "_internal" / "tkinter_notes.txt")
    _touch(app / "_internal" / "PySide6" / "Qt" / "tkdata.txt")
    assert run_contents(app)["ok"]


def test_windows_needs_its_plugin_but_not_qtdbus(tmp_path, monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "win32")
    internal = tmp_path / "_internal"
    for rel in REQUIRED_SOURCE:
        _touch(internal / rel)
    _touch(internal / "PySide6" / "Qt" / "plugins" / "platforms" / "qwindows.dll")
    licenses = tmp_path / "licenses"
    for name in LICENCES:
        _touch(licenses / name, "Qt 6.12.0")
    for rel in FONT_FILES:
        _touch(internal / rel)
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
    for name in LICENCES:
        _touch(contents / "Resources" / "licenses" / name, "Qt 6.12.0")
    for rel in FONT_FILES:
        _touch(base / rel)


@pytest.mark.parametrize("source_root", ["Frameworks", "Resources"])
def test_macos_accepts_either_folder_and_says_which(tmp_path, monkeypatch, source_root):
    monkeypatch.setattr(va.sys, "platform", "darwin")
    make_mac_bundle(tmp_path, source_root)
    result = run_contents(tmp_path)
    assert result["ok"], result
    assert result["detail"]["lgpl_source_in"]["PySide6/__init__.py"] == f"Contents/{source_root}"
    assert result["detail"]["pyside6_in"] == [f"Contents/{source_root}"]


def test_macos_judges_the_pyside6_tree_in_both_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "darwin")
    make_mac_bundle(tmp_path, "Frameworks")
    _touch(tmp_path / "Tokitty.app" / "Contents" / "Resources" / "PySide6" / "Qt" / "lib" / "QtNetwork.framework" / "Versions" / "A" / "QtNetwork")
    result = run_contents(tmp_path)
    assert not result["ok"]
    assert "QtNetwork.framework" in " ".join(result["detail"]["problems"])


# ---- the self-check step, against a fake executable ----

FAKE_APP = '''#!{python}
import json, os, sys
checks = {{"qt": {{"ok": True, "detail": "{qt_version}"}}, "tk_root": {{"ok": True, "detail": "xcb"}}}}
if {qt_root}:
    checks["qt_root"] = {{"ok": True, "detail": "xcb"}}
if os.environ.get("QT_QPA_PLATFORM"):
    checks["qpa_leaked"] = {{"ok": False, "detail": os.environ["QT_QPA_PLATFORM"]}}
if os.environ.get("WAYLAND_DISPLAY"):
    checks["wayland_leaked"] = {{"ok": False, "detail": os.environ["WAYLAND_DISPLAY"]}}
report = {{"checks": checks, "state_dir": "{state_dir}", "toolkits": {toolkits}}}
print(json.dumps(report))
sys.exit(0 if all(c["ok"] for c in checks.values()) else 1)
'''


def make_fake_app(root: Path, state_dir: Path, *, toolkits=("qt",), qt_version="6.12.0", qt_root=True) -> None:
    make_linux_bundle(root)
    script = root / "tokitty"
    script.write_text(
        FAKE_APP.format(
            python=sys.executable,
            qt_version=qt_version,
            qt_root=qt_root,
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
    env["WAYLAND_DISPLAY"] = "wayland-0"
    ctx = {"app_dir": app, "work": work, "env": env, "repo_root": REPO}
    ctx["gui"] = app / "tokitty"
    ctx.update(kwargs)
    return ctx


@posix_only
def test_self_check_runs_once_without_qpa_platform_and_requires_qt_root(tmp_path, linux):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state")
    ctx = make_ctx(tmp_path, app)
    result = va.step_self_check(ctx)
    assert result["ok"], result
    assert ctx["qt_version"] == "6.12.0"
    assert ctx["toolkits"] == ["qt"]
    assert result["detail"]["checks"]["qt_root"]["ok"]


@posix_only
def test_self_check_fails_when_the_report_lacks_qt_root(tmp_path, linux):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state", qt_root=False)
    result = va.step_self_check(make_ctx(tmp_path, app))
    assert not result["ok"]
    assert "qt_root" in result["detail"]


@posix_only
@pytest.mark.parametrize("toolkits", [("tk", "qt"), ("tk",), ()])
def test_self_check_requires_the_toolkits_field_to_be_exactly_qt(tmp_path, linux, toolkits):
    app = tmp_path / "app"
    app.mkdir()
    make_fake_app(app, tmp_path / "state", toolkits=toolkits)
    result = va.step_self_check(make_ctx(tmp_path, app))
    assert not result["ok"]
    assert "expected ['qt']" in result["detail"]


def test_self_check_refuses_a_report_without_the_toolkits_field(monkeypatch, tmp_path):
    report = {"checks": {"qt": {"ok": True}, "qt_root": {"ok": True}, "tk_root": {"ok": True}}, "state_dir": str(tmp_path)}
    monkeypatch.setattr(va, "_self_check_report", lambda gui, env: (report, None))
    result = va.step_self_check({"env": {}, "gui": tmp_path / "gui"})
    assert not result["ok"] and "None" in result["detail"]


@pytest.mark.parametrize("expected, ok", [(["qt"], True), (["qt", "tk"], False), (["tk"], False)])
def test_expect_toolkits_only_accepts_qt(monkeypatch, tmp_path, expected, ok):
    report = {"checks": {"qt": {"ok": True}, "qt_root": {"ok": True}, "tk_root": {"ok": True}}, "state_dir": str(tmp_path), "toolkits": ["qt"]}
    monkeypatch.setattr(va, "_self_check_report", lambda gui, env: (report, None))
    result = va.step_self_check({"env": {}, "gui": tmp_path / "gui", "expect_toolkits": expected})
    assert result["ok"] is ok


def test_the_tk_without_qt_step_is_gone():
    names = [name for name, _ in va.STEPS]
    assert "tk_without_qt" not in names
    assert not hasattr(va, "step_tk_without_qt")
    assert names[names.index("self_check") + 1] == "bundle_contents"


def test_an_untrimmed_measurement_build_skips_only_the_trim_checks(tmp_path, monkeypatch):
    # The trim=false dispatch keeps the dropped files on purpose (#115 review).
    monkeypatch.setenv("TOKITTY_QT_TRIM", "0")
    app = tmp_path / "tokitty"
    internal = app / "_internal"
    for rel in REQUIRED_SOURCE:
        (internal / rel).parent.mkdir(parents=True, exist_ok=True)
        (internal / rel).write_text("", encoding="utf-8")
    (internal / "PySide6" / "Qt" / "lib").mkdir(parents=True)
    (internal / "PySide6" / "Qt" / "lib" / "libQt6Network.so.6").write_text("", encoding="utf-8")
    licenses = app / "licenses"
    licenses.mkdir()
    for name in LICENCES:
        (licenses / name).write_text("x", encoding="utf-8")
    if sys.platform.startswith("linux"):
        result = va.step_bundle_contents({"app_dir": app, "repo_root": REPO, "toolkits": ["qt"]})
        assert result["detail"]["trim_check"] == "skipped: TOKITTY_QT_TRIM=0"
        assert not any("libQt6Network" in p for p in result["detail"].get("problems", []))
