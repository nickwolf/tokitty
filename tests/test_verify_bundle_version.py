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
