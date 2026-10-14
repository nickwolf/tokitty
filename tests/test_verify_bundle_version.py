"""Pure-logic tests for freeze/verify_artifact.py's macOS bundle version
check (#98), loaded by path the way tests/test_keychain_check.py loads its
freeze/ script."""
import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "freeze" / "verify_artifact.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_artifact_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


va = _load_module()


def plist(version):
    return {"CFBundleShortVersionString": version, "CFBundleVersion": version}


def test_a_release_bundle_matching_its_tag_and_pyproject_passes():
    assert va.bundle_version_problem(plist("0.6.0"), "v0.6.0", "0.6.0") is None


def test_the_unset_pyinstaller_default_fails():
    assert va.bundle_version_problem(plist("0.0.0"), "v0.6.0", "0.6.0")


def test_a_tag_that_pyproject_was_not_bumped_for_fails():
    assert va.bundle_version_problem(plist("0.5.0"), "v0.6.0", "0.5.0")


def test_a_dry_run_only_has_to_match_pyproject():
    assert va.bundle_version_problem(plist("0.6.0"), "dryrun-abc1234", "0.6.0") is None
    assert va.bundle_version_problem(plist("0.0.0"), "dryrun-abc1234", "0.6.0")


def test_a_build_number_that_disagrees_fails():
    data = plist("0.6.0")
    data["CFBundleVersion"] = "0.0.0"
    assert va.bundle_version_problem(data, "v0.6.0", "0.6.0")


def _mac_ctx(tmp_path, plist_data):
    import plistlib

    gui = tmp_path / "Tokitty.app" / "Contents" / "MacOS" / "Tokitty"
    gui.parent.mkdir(parents=True)
    (gui.parent.parent / "Info.plist").write_bytes(plistlib.dumps(plist_data))
    repo = Path(__file__).resolve().parent.parent
    return {"gui": gui, "repo_root": repo, "self_check_report": {"build_id": "dryrun-abc1234"}}


def _pyproject_version():
    return va._pyproject_version(Path(__file__).resolve().parent.parent)


def test_macos_requires_lsuielement_true(tmp_path, monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "darwin")
    good = {**plist(_pyproject_version()), "LSUIElement": True}
    assert va.step_bundle_version(_mac_ctx(tmp_path / "a", good))["ok"]
    for bad in (plist(_pyproject_version()), {**good, "LSUIElement": False}, {**good, "LSUIElement": "1"}):
        result = va.step_bundle_version(_mac_ctx(tmp_path / f"b{len(str(bad))}{bad.get('LSUIElement')}", bad))
        assert not result["ok"] and "LSUIElement" in result["detail"]


def test_other_platforms_skip_the_bundle_version_step(monkeypatch):
    monkeypatch.setattr(va.sys, "platform", "linux")
    assert va.step_bundle_version({})["ok"]
