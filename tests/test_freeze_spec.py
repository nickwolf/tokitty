import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _bundle_files():
    sys.path.insert(0, str(ROOT / "freeze"))
    try:
        import bundle_data
    finally:
        sys.path.pop(0)
    return set(bundle_data.DATA_FILES)


def test_package_data_is_bundled():
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["tokitty"]
    missing = set(package_data) - _bundle_files()
    assert not missing, f"add {sorted(missing)} to freeze/bundle_data.py"


def test_hook_writer_is_bundled():
    assert "hook_writer.py" in _bundle_files()


def test_bundled_files_exist():
    for name in _bundle_files():
        assert (ROOT / "tokitty" / name).is_file(), name


def test_release_build_installs_the_streamdock_extra():
    # Without comtypes in the build environment, the frozen Windows app has no
    # Stream Dock tab switching or Interrupt, and nothing fails to say so.
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'pip install -e ".[packaging,streamdock,qt]"' in workflow


def _release_workflow():
    return (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def test_release_build_declares_both_toolkits_and_installs_the_xcb_libraries():
    workflow = _release_workflow()
    assert "TOKITTY_BUILD_TOOLKITS: tk,qt" in workflow
    apt = next(line for line in workflow.splitlines() if "apt-get install" in line)
    for package in ("xvfb", "libegl1", "libxcb-shape0", "libxcb-cursor0", "libxcb-icccm4", "libxcb-keysyms1", "libxcb-xkb1", "libxkbcommon-x11-0"):
        assert package in apt


def test_release_verifies_the_declared_toolkits_on_every_platform():
    assert _release_workflow().count("--expect-toolkits tk,qt") == 3


def test_release_runs_the_qt_and_shipped_release_update_passes():
    workflow = _release_workflow()
    assert "name: Verify update (Qt)" in workflow
    assert "--qt --only apply_update,no_ack_rollback" in workflow
    assert "update-report-qt.json" in workflow
    assert "name: Verify update from a shipped release" in workflow
    assert "LEGACY_TAG: v0.6.2" in workflow
    assert 'gh release download "$LEGACY_TAG" --repo "$GITHUB_REPOSITORY"' in workflow
    assert '--old-tag "$LEGACY_TAG"' in workflow
    assert "update-report-legacy.json" in workflow


def test_release_rehearsal_tag_sits_above_every_real_release():
    workflow = _release_workflow()
    assert 'echo "UPDATE_TAG=v99.0.0"' in workflow
    assert "v0.0.2" not in workflow


def test_release_trims_qt_unless_the_dispatch_input_says_otherwise():
    workflow = _release_workflow()
    assert workflow.count("TOKITTY_QT_TRIM") == 1
    line = next(line for line in workflow.splitlines() if "TOKITTY_QT_TRIM" in line)
    assert "inputs.trim" in line
    assert "type: boolean" in workflow


def test_release_publish_names_the_repo():
    # The publish job has no checkout, so gh cannot infer the repo from a .git.
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'gh release create "$GITHUB_REF_NAME" --repo "$GITHUB_REPOSITORY"' in workflow


def _bundle_data():
    sys.path.insert(0, str(ROOT / "freeze"))
    try:
        import bundle_data
    finally:
        sys.path.pop(0)
    return bundle_data


def test_project_version_is_read_from_pyproject():
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert _bundle_data().project_version(ROOT) == pyproject["project"]["version"]


def test_info_plist_carries_the_version_and_copyright():
    plist = _bundle_data().info_plist("0.6.0")
    assert plist["CFBundleShortVersionString"] == "0.6.0"
    assert plist["CFBundleVersion"] == "0.6.0"
    assert "Nick Wolf" in plist["NSHumanReadableCopyright"]


def test_spec_passes_the_info_plist_to_the_bundle():
    # Without it the .app reports CFBundleShortVersionString 0.0.0 (#98).
    spec = (ROOT / "freeze" / "tokitty.spec").read_text(encoding="utf-8")
    assert "info_plist=info_plist(project_version(ROOT))" in spec


def test_release_refuses_a_tag_that_pyproject_was_not_bumped_for():
    # The bundle version comes from pyproject.toml, so a tag pushed before the
    # bump commit would ship a .app that reports the previous version.
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    check = workflow.index("name: Check the tag matches pyproject.toml")
    assert check < workflow.index("name: Build with PyInstaller")
    assert "project_version" in workflow[check:workflow.index("name: Build with PyInstaller")]


def test_verifier_checks_the_bundle_version():
    script = (ROOT / "freeze" / "verify_artifact.py").read_text(encoding="utf-8")
    assert '("bundle_version", step_bundle_version)' in script


def test_spec_gives_the_mac_bundle_and_windows_exe_an_icon():
    spec = (ROOT / "freeze" / "tokitty.spec").read_text(encoding="utf-8")
    assert "icon=str(icns_path)" in spec
    assert "icon=gui_icon" in spec


QT_PIN = "6.12.0"


@pytest.mark.parametrize(
    "dest",
    [
        # Linux, from the local trim=off build's Analysis TOC (#115).
        "PySide6/Qt/lib/libQt6Network.so.6",
        "PySide6/Qt/lib/libQt6OpenGL.so.6",
        "PySide6/Qt/lib/libQt6Svg.so.6",
        "PySide6/Qt/lib/libQt6WaylandClient.so.6",
        "PySide6/Qt/lib/libQt6WlShellIntegration.so.6",
        "PySide6/QtNetwork.abi3.so",
        "PySide6/Qt/plugins/imageformats/libqjpeg.so",
        "PySide6/Qt/plugins/iconengines/libqsvgicon.so",
        "PySide6/Qt/plugins/generic/libqevdevmouseplugin.so",
        "PySide6/Qt/plugins/networkinformation/libqnetworkmanager.so",
        "PySide6/Qt/plugins/tls/libqopensslbackend.so",
        "PySide6/Qt/plugins/platforms/libqoffscreen.so",
        "PySide6/Qt/plugins/platforms/libqminimal.so",
        "PySide6/Qt/plugins/platforms/libqminimalegl.so",
        "PySide6/Qt/plugins/platforms/libqwayland.so",
        "PySide6/Qt/plugins/wayland-shell-integration/libxdg-shell.so",
        "PySide6/Qt/plugins/wayland-decoration-client/libadwaita.so",
        "PySide6/Qt/translations/qtbase_de.qm",
        # Windows, from the spike's file list (Qt6*.dll beside the plugins in PySide6\).
        "PySide6\\Qt6Network.dll",
        "PySide6\\Qt6Pdf.dll",
        "PySide6\\Qt6Qml.dll",
        "PySide6\\Qt6QmlModels.dll",
        "PySide6\\Qt6Quick.dll",
        "PySide6\\Qt6OpenGL.dll",
        "PySide6\\Qt6Svg.dll",
        "PySide6\\Qt6VirtualKeyboard.dll",
        # Linux: xcb is the only platform kept, and the GTK 3 theme goes.
        "PySide6/Qt/plugins/platforms/libqlinuxfb.so",
        "PySide6/Qt/plugins/platforms/libqeglfs.so",
        "PySide6/Qt/plugins/platforms/libqvnc.so",
        "PySide6/Qt/plugins/platforms/libqvkkhrdisplay.so",
        "PySide6/Qt/plugins/egldeviceintegrations/libqeglfs-kms-integration.so",
        "PySide6/Qt/lib/libQt6EglFSDeviceIntegration.so.6",
        "PySide6/Qt/lib/libQt6EglFsKmsSupport.so.6",
        "PySide6/Qt/plugins/platformthemes/libqgtk3.so",
        "PySide6\\opengl32sw.dll",
        "PySide6\\d3dcompiler_47.dll",
        "PySide6\\QtNetwork.pyd",
        "PySide6\\plugins\\platforms\\qoffscreen.dll",
        "PySide6\\plugins\\platforms\\qminimal.dll",
        "PySide6\\plugins\\platforms\\qdirect2d.dll",
        "PySide6\\plugins\\imageformats\\qjpeg.dll",
        "PySide6\\plugins\\tls\\qschannelbackend.dll",
        "PySide6\\translations\\qt_de.qm",
        # macOS, from the spike's file list (frameworks under PySide6/Qt/lib at Analysis time).
        "PySide6/Qt/lib/QtNetwork.framework/Versions/A/QtNetwork",
        "PySide6/Qt/lib/QtSvg.framework/Resources/Info.plist",
        "PySide6/Qt/lib/QtQml.framework/Versions/A/QtQml",
        "PySide6/QtSvg.abi3.so",
        "PySide6/Qt/plugins/platforms/libqoffscreen.dylib",
        "PySide6/Qt/plugins/platforms/libqminimal.dylib",
        "PySide6/Qt/plugins/imageformats/libqjpeg.dylib",
    ],
)
def test_qt_entry_dropped_drops_what_the_trim_names(dest):
    assert _bundle_data().qt_entry_dropped(dest)


@pytest.mark.parametrize(
    "dest",
    [
        # Linux, from the local build's TOC.
        "PySide6/Qt/lib/libQt6Core.so.6",
        "PySide6/Qt/lib/libQt6DBus.so.6",
        "PySide6/Qt/lib/libQt6Gui.so.6",
        "PySide6/Qt/lib/libQt6Widgets.so.6",
        "PySide6/Qt/lib/libQt6XcbQpa.so.6",
        "PySide6/Qt/lib/libicuuc.so.73",
        "PySide6/QtCore.abi3.so",
        "PySide6/QtDBus.abi3.so",
        "PySide6/QtGui.abi3.so",
        "PySide6/QtWidgets.abi3.so",
        "PySide6/Qt/plugins/platforms/libqxcb.so",
        "PySide6/Qt/plugins/platformthemes/libqxdgdesktopportal.so",
        "PySide6/Qt/plugins/platforminputcontexts/libibusplatforminputcontextplugin.so",
        "PySide6/Qt/plugins/xcbglintegrations/libqxcb-glx-integration.so",
        "shiboken6/libshiboken6.abi3.so.6.12",
        # Windows and macOS, from the spike's file lists.
        "PySide6\\Qt6Core.dll",
        "PySide6\\Qt6DBus.dll",
        "PySide6\\plugins\\platforms\\qwindows.dll",
        "PySide6\\plugins\\styles\\qmodernwindowsstyle.dll",
        "PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore",
        "PySide6/Qt/lib/QtDBus.framework/Versions/A/QtDBus",
        "PySide6/Qt/plugins/platforms/libqcocoa.dylib",
        "PySide6/Qt/plugins/styles/libqmacstyle.dylib",
        # Outside PySide6/, even with a dropped name in the path: never touched.
        "tokitty/tls/cert.py",
        "tokitty/generic/helper.py",
        "tokitty/translations/en.json",
        "_internal/tls/bundle.pem",
        "certifi/cacert.pem",
        "base_library.zip",
        "libQt6Network.so.6",
        "PySide6qt/tls/x",
        "MyPySide6/imageformats/x",
    ],
)
def test_qt_entry_dropped_keeps_everything_else(dest):
    assert not _bundle_data().qt_entry_dropped(dest)


def test_qt_toc_entry_dropped_follows_a_symlink_to_its_target():
    # Linux lists a top-level libQt6*.so.6 link per library, named without the
    # PySide6/ prefix; a dropped library must not leave its link dangling.
    drop = _bundle_data().qt_toc_entry_dropped
    assert drop(("libQt6Network.so.6", "PySide6/Qt/lib/libQt6Network.so.6", "SYMLINK"))
    assert not drop(("libQt6Core.so.6", "PySide6/Qt/lib/libQt6Core.so.6", "SYMLINK"))
    assert not drop(("libssl.so.3", "/usr/lib/libssl.so.3", "BINARY"))


def test_qt_toc_entry_dropped_takes_the_gtk_theme_libraries_and_nothing_near_them():
    drop = _bundle_data().qt_toc_entry_dropped
    assert drop(("libgtk-3.so.0", "/usr/lib/x86_64-linux-gnu/libgtk-3.so.0", "BINARY"))
    assert drop(("libcairo.so.2", "/usr/lib/x86_64-linux-gnu/libcairo.so.2", "BINARY"))
    # Libraries libX11 or Qt may dlopen, and anything not exactly named, stay.
    for name in ("libXcursor.so.1", "libgio-2.0.so.0", "libgobject-2.0.so.0", "libX11.so.6", "libgtk-3.so.0.2"):
        assert not drop((name, f"/usr/lib/x86_64-linux-gnu/{name}", "BINARY"))
    # Only a top-level binary entry is matched.
    assert not drop(("PIL/libgtk-3.so.0", "/x/libgtk-3.so.0", "BINARY"))


def test_qt_excluded_modules_leave_the_kept_bindings_alone():
    data = _bundle_data()
    excluded = set(data.QT_EXCLUDED_MODULES)
    assert all(name.startswith("PySide6.Qt") for name in excluded)
    for kept in (*data.QT_KEPT_BINDINGS, "QtDBus"):
        assert f"PySide6.{kept}" not in excluded
    assert "PySide6.QtNetwork" in excluded


def test_qt_kept_bindings_are_the_only_ones_tokitty_imports():
    import re

    imported = set()
    for path in (ROOT / "tokitty").rglob("*.py"):
        imported |= set(re.findall(r"PySide6\.(Qt\w+)", path.read_text(encoding="utf-8")))
    assert imported <= set(_bundle_data().QT_KEPT_BINDINGS) | {"QtDBus"}


def test_licence_files_exist_and_license_matches_the_repo():
    data = _bundle_data()
    licenses = ROOT / "freeze" / "licenses"
    assert set(data.LICENSE_FILES) == {p.name for p in licenses.iterdir()}
    assert (licenses / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (licenses / "LGPL-3.0.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in (licenses / "GPL-3.0.txt").read_text(encoding="utf-8")
    assert "LGPL" in (licenses / "pystray-NOTICE.txt").read_text(encoding="utf-8")


def test_qt_notice_names_the_pinned_version():
    data = _bundle_data()
    pinned = data.qt_version(ROOT)
    assert pinned == QT_PIN
    notice = (ROOT / "freeze" / "licenses" / "Qt-NOTICE.txt").read_text(encoding="utf-8")
    assert f"Qt {pinned}" in notice
    assert f"PySide6 {pinned}" in notice


def test_qt_version_is_read_from_the_qt_extra(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project.optional-dependencies]\nother = ["PySide6==1.0"]\nqt = ["PySide6-Essentials==6.99.1"]\n',
        encoding="utf-8",
    )
    assert _bundle_data().qt_version(tmp_path) == "6.99.1"


def test_info_plist_carries_the_qt_line():
    plist = _bundle_data().info_plist("0.6.0")
    lines = plist["NSHumanReadableCopyright"].split("\n")
    assert lines[0] == "Copyright © 2026 Nick Wolf. MIT License."
    assert lines[1] == (
        f"Includes Qt {QT_PIN} and PySide6 {QT_PIN} under the LGPL-3.0, see Contents/Resources/licenses."
    )
    assert _bundle_data().info_plist("0.6.0", "7.1.2")["NSHumanReadableCopyright"].count("7.1.2") == 2


def test_spec_applies_the_trim_and_ships_the_licences():
    spec = (ROOT / "freeze" / "tokitty.spec").read_text(encoding="utf-8")
    assert "qt_entry_dropped" in spec
    assert "LICENSE_FILES" in spec
    assert "TOKITTY_QT_TRIM" in spec
    assert "excludes=list(QT_EXCLUDED_MODULES)" in spec
    assert '"PySide6", "shiboken6"' in spec
    assert 'Path(DISTPATH) / APP_NAME / "licenses"' in spec


def test_qt_notices_script_keeps_the_bundled_modules(tmp_path):
    sys.path.insert(0, str(ROOT))
    try:
        from freeze import qt_notices
    finally:
        sys.path.pop(0)

    def entry(id_, module, usage="Used in Qt.", licence_id="MIT"):
        return {"Id": id_, "QDocModule": module, "QtUsage": usage, "LicenseId": licence_id, "_dir": tmp_path}

    entries = [
        entry("kept-core", "qtcore"),
        entry("kept-dbus", "qtdbus"),
        entry("net", "qtnetwork"),
        entry("kwin", "qtcore", "Used as part of the build system.", "BSD-3-Clause"),
        entry("wayland-protocol", "qtwaylandcompositor", "Used in the Qt Wayland platform plugin"),
        entry("xcb-thing", "qtother", "Used in XCB platform plugin if absent."),
    ]
    kept, dropped = qt_notices.classify(entries)
    assert [e["Id"] for e in kept] == ["kept-core", "kept-dbus", "xcb-thing"]
    assert {e["Id"] for e, _ in dropped} == {"net", "kwin", "wayland-protocol"}


def test_lgpl_packages_ship_as_replaceable_source():
    # pystray, PySide6 and shiboken6 are LGPL-3.0: their Python code must sit
    # in the bundle as .py files a user can replace, not inside the PYZ (#115).
    data = _bundle_data()
    assert data.LGPL_SOURCE_PACKAGES == {"pystray": "py", "PySide6": "py", "shiboken6": "py", "Xlib": "py"}
    spec = (Path(__file__).resolve().parent.parent / "freeze" / "tokitty.spec").read_text(encoding="utf-8")
    assert "module_collection_mode=LGPL_SOURCE_PACKAGES" in spec


def test_qt_notice_carries_the_icu_licence_of_the_bundled_version():
    # The Linux wheel ships ICU 73.2; its notice comes from that release's
    # icu4c/LICENSE, not from whatever ICU the build host has (#115).
    notice = (Path(__file__).resolve().parent.parent / "freeze" / "licenses" / "Qt-NOTICE.txt").read_text(encoding="utf-8")
    assert "ICU 73.2, icu4c/LICENSE at unicode-org/icu release-73-2" in notice
    assert "UNICODE LICENSE V3" in notice.upper() or "UNICODE, INC. LICENSE AGREEMENT" in notice.upper()
    assert "/usr/share/doc/" not in notice


def test_notice_versions_follow_the_installed_packages(tmp_path):
    # pystray and python-xlib are unpinned, so the spec refuses to build when a
    # notice names a different version than the build venv has (#115).
    data = _bundle_data()
    (tmp_path / "pystray-NOTICE.txt").write_text("pystray\n\nVersion: 0.19.5\n", encoding="utf-8")
    (tmp_path / "python-xlib-NOTICE.txt").write_text("python-xlib\n\nVersion: 0.33\n", encoding="utf-8")
    versions = {"pystray": "0.19.5", "python-xlib": "0.33"}
    assert data.notice_version_problems(tmp_path, versions.get) == []
    versions["pystray"] = "0.19.6"
    assert data.notice_version_problems(tmp_path, versions.get) == ["pystray-NOTICE.txt says pystray 0.19.5, the build has 0.19.6"]
    # A package this platform does not install (python-xlib off Linux) is skipped.
    assert data.notice_version_problems(tmp_path, {"pystray": "0.19.5"}.get) == []


def test_the_shipped_notices_name_versions_the_spec_can_check():
    data = _bundle_data()
    licenses = Path(__file__).resolve().parent.parent / "freeze" / "licenses"
    assert data.notice_version_problems(licenses, {"pystray": "0.19.5", "python-xlib": "0.33"}.get) == []
    spec = (Path(__file__).resolve().parent.parent / "freeze" / "tokitty.spec").read_text(encoding="utf-8")
    assert "notice_version_problems(" in spec
