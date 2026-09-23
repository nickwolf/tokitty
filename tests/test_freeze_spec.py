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
