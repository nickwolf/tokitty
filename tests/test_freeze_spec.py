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
    assert 'pip install -e ".[packaging,streamdock]"' in workflow


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
