"""Tests for freeze/bundle_notices.py (#152), against fake dpkg databases,
fake distributions and fake TOCs. The real build runs it from the spec."""
import importlib.metadata
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "freeze"))
try:
    import bundle_notices as bn
finally:
    sys.path.pop(0)


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_dpkg(tmp_path: Path, owned: dict, copyrights: dict, status: str = "") -> bn.DpkgIndex:
    """owned maps "package:arch" to the paths its .list file names."""
    info = tmp_path / "dpkg" / "info"
    for package, paths in owned.items():
        _touch(info / f"{package}.list", "/.\n" + "\n".join(paths) + "\n")
    for package, text in copyrights.items():
        _touch(tmp_path / "doc" / package / "copyright", text)
    _touch(tmp_path / "dpkg" / "status", status)
    return bn.DpkgIndex(info, tmp_path / "dpkg" / "status", tmp_path / "doc", tmp_path / "common-licenses")


def make_dist(site: Path, name: str, version: str = "1.0", licence_files=("LICENSE",), expression="MIT", modules=("__init__.py",)):
    """A dist-info folder with a RECORD, as pip leaves one."""
    info = site / f"{name}-{version}.dist-info"
    meta = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    if expression:
        meta.append(f"License-Expression: {expression}")
    meta += [f"License-File: {f}" for f in licence_files]
    _touch(info / "METADATA", "\n".join(meta) + "\n")
    record = []
    for module in modules:
        _touch(site / name / module, "")
        record.append(f"{name}/{module},,")
    for f in licence_files:
        _touch(info / "licenses" / f, f"{name} licence text")
        record.append(f"{info.name}/licenses/{f},,")
    record += [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
    _touch(info / "RECORD", "\n".join(record) + "\n")


def test_dpkg_owner_strips_the_arch_and_tries_both_usrmerge_spellings(tmp_path):
    dpkg = make_dpkg(tmp_path, {"libssl3:amd64": ["/usr/lib/x86_64-linux-gnu/libssl.so.3"], "zlib1g": ["/lib/x86_64-linux-gnu/libz.so.1"]}, {})
    assert dpkg.owner("/lib/x86_64-linux-gnu/libssl.so.3") == "libssl3"
    assert dpkg.owner("/usr/lib/x86_64-linux-gnu/libssl.so.3") == "libssl3"
    assert dpkg.owner("/usr/lib/x86_64-linux-gnu/libz.so.1") == "zlib1g"
    assert dpkg.owner("/opt/hostedtoolcache/Python/3.13.9/x64/lib/libpython3.13.so.1.0") is None


def test_dpkg_reads_versions_and_source_packages_from_status(tmp_path):
    status = (
        "Package: libglib2.0-0\nStatus: install ok installed\nArchitecture: amd64\nSource: glib2.0\nVersion: 2.72.4-0ubuntu2.6\n\n"
        "Package: libssl3\nStatus: install ok installed\nSource: openssl (3.0.2-0ubuntu1.20)\nVersion: 3.0.2-0ubuntu1.20+b1\n\n"
        "Package: gone\nStatus: deinstall ok config-files\nVersion: 1\n"
    )
    dpkg = make_dpkg(tmp_path, {}, {}, status)
    assert dpkg.source("libglib2.0-0") == ("glib2.0", "2.72.4-0ubuntu2.6")
    assert dpkg.source("libssl3") == ("openssl", "3.0.2-0ubuntu1.20")
    assert dpkg.version("libssl3") == "3.0.2-0ubuntu1.20+b1"
    assert "gone" not in dpkg.packages


def test_a_package_without_a_copyright_file_fails(tmp_path):
    dpkg = make_dpkg(tmp_path, {}, {})
    with pytest.raises(FileNotFoundError, match="libfoo1"):
        dpkg.copyright("libfoo1")


def test_common_licences_are_shipped_once_under_their_real_name(tmp_path):
    dpkg = make_dpkg(tmp_path, {}, {})
    _touch(tmp_path / "common-licenses" / "LGPL-2.1", "GNU LESSER GENERAL PUBLIC LICENSE 2.1")
    text = "see /usr/share/common-licenses/LGPL-2.1.\nAlso /usr/share/common-licenses/LGPL-2.1 and /usr/share/common-licenses/Nope."
    assert dpkg.common_licences(text) == [(str(tmp_path / "common-licenses" / "LGPL-2.1"), "GNU LESSER GENERAL PUBLIC LICENSE 2.1")]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_common_licence_links_resolve_to_the_versioned_file(tmp_path):
    dpkg = make_dpkg(tmp_path, {}, {})
    _touch(tmp_path / "common-licenses" / "GPL-3", "GPL 3 text")
    os.symlink("GPL-3", tmp_path / "common-licenses" / "GPL")
    keys = [key for key, _ in dpkg.common_licences("/usr/share/common-licenses/GPL and /usr/share/common-licenses/GPL-3")]
    assert len(keys) == 2 and all(key.endswith("GPL-3") for key in keys)


def _ci_layout(tmp_path):
    """A setup-python style install, a repo, a build folder and a system
    library, as on the Ubuntu 22.04 release runner."""
    prefix = tmp_path / "toolcache" / "Python" / "3.13.9" / "x64"
    site = prefix / "lib" / "python3.13" / "site-packages"
    make_dist(site, "comtypes")
    make_dist(site, "shiboken6", licence_files=())
    make_dist(site, "pyinstaller", expression="", modules=("__init__.py", "hooks/rthooks/pyi_rth_inspect.py"))
    repo, work = tmp_path / "repo", tmp_path / "build"
    system = tmp_path / "lib" / "x86_64-linux-gnu"
    files = {
        "stdlib": _touch(prefix / "lib" / "python3.13" / "json" / "__init__.py"),
        "dynload": _touch(prefix / "lib" / "python3.13" / "lib-dynload" / "_ssl.cpython-313-x86_64-linux-gnu.so"),
        "libpython": _touch(prefix / "lib" / "libpython3.13.so.1.0"),
        "own": _touch(repo / "tokitty" / "app.py"),
        "rthook": _touch(work / "rthook_build_id.py"),
        "libssl": _touch(system / "libssl.so.3"),
        "stray": _touch(tmp_path / "elsewhere" / "libmystery.so.1"),
    }
    dists = list(importlib.metadata.distributions(path=[str(site)]))
    dpkg = make_dpkg(tmp_path, {"libssl3:amd64": [str(system / "libssl.so.3")]}, {"libssl3": "License: Apache-2.0\n"})
    return prefix, site, repo, work, files, dists, dpkg


def test_entries_are_put_down_to_where_they_came_from(tmp_path):
    prefix, site, repo, work, files, dists, dpkg = _ci_layout(tmp_path)
    entries = [
        ("json", str(files["stdlib"]), "PYMODULE"),
        ("python3.13/lib-dynload/_ssl.cpython-313-x86_64-linux-gnu.so", str(files["dynload"]), "EXTENSION"),
        ("libpython3.13.so.1.0", str(files["libpython"]), "BINARY"),
        ("tokitty.app", str(files["own"]), "PYMODULE"),
        ("rthook_build_id", str(files["rthook"]), "PYSOURCE"),
        ("libssl.so.3", str(files["libssl"]), "BINARY"),
        ("comtypes", str(site / "comtypes" / "__init__.py"), "PYMODULE"),
        ("shiboken6", str(site / "shiboken6" / "__init__.py"), "PYMODULE"),
        ("pyi_rth_inspect", str(site / "pyinstaller" / "hooks" / "rthooks" / "pyi_rth_inspect.py"), "PYSOURCE"),
        ("libmystery.so.1", str(files["stray"]), "BINARY"),
        ("libQt6Core.so.6", "PySide6/Qt/lib/libQt6Core.so.6", "SYMLINK"),
    ]
    found, packages, cpython, unattributed = bn.attribute(
        entries, own_dirs=[repo, work], base_prefix=prefix, dist_of=bn.distribution_files(dists).get, dpkg=dpkg,
    )
    assert sorted(found) == ["comtypes", "pyinstaller", "shiboken6"]
    assert packages == {"libssl3": [("libssl.so.3", "BINARY")]}
    assert [dest for dest, _ in cpython] == ["json", "python3.13/lib-dynload/_ssl.cpython-313-x86_64-linux-gnu.so", "libpython3.13.so.1.0"]
    assert unattributed == [("libmystery.so.1", str(files["stray"]))]


def test_a_python_that_dpkg_owns_is_the_packages(tmp_path):
    # A local build on the distribution's own Python: dpkg owns libpython,
    # which also sits under sys.base_prefix (/usr), and its copyright file
    # is the one that applies.
    lib = _touch(tmp_path / "usr" / "lib" / "x86_64-linux-gnu" / "libpython3.12.so.1.0")
    dpkg = make_dpkg(tmp_path, {"libpython3.12t64:amd64": [str(lib)]}, {})
    _, packages, cpython, _ = bn.attribute(
        [("libpython3.12.so.1.0", str(lib), "BINARY")], own_dirs=[], base_prefix=tmp_path / "usr", dist_of={}.get, dpkg=dpkg,
    )
    assert packages == {"libpython3.12t64": [("libpython3.12.so.1.0", "BINARY")]} and cpython == []


def test_a_venv_inside_the_repo_is_still_third_party(tmp_path):
    repo = tmp_path / "repo"
    site = repo / ".venv" / "lib" / "site-packages"
    make_dist(site, "comtypes")
    dists = list(importlib.metadata.distributions(path=[str(site)]))
    found, _, _, _ = bn.attribute(
        [("comtypes", str(site / "comtypes" / "__init__.py"), "PYMODULE")],
        own_dirs=[repo], base_prefix=tmp_path / "py", dist_of=bn.distribution_files(dists).get, dpkg=None,
    )
    assert list(found) == ["comtypes"]


def test_distribution_licence_reads_the_licence_files_it_names(tmp_path):
    make_dist(tmp_path, "comtypes", licence_files=("LICENSE.txt",))
    (dist,) = importlib.metadata.distributions(path=[str(tmp_path)])
    label, texts = bn.distribution_licence(dist)
    assert label == "MIT"
    assert texts == [("comtypes 1.0: LICENSE.txt", "comtypes licence text")]


def test_a_bundled_distribution_without_a_licence_file_fails(tmp_path):
    make_dist(tmp_path, "mystery", licence_files=())
    (dist,) = importlib.metadata.distributions(path=[str(tmp_path)])
    with pytest.raises(ValueError, match="mystery 1.0"):
        bn.distribution_licence(dist)


def _cpython(tmp_path, rst_names=("cpython-v3.13.9-license.rst",)):
    stdlib = _touch(tmp_path / "lib" / "python3.13" / "LICENSE.txt", "PSF LICENSE AGREEMENT").parent
    notices = tmp_path / "notices"
    for name in rst_names:
        _touch(notices / name, "History\n\nLicenses and Acknowledgements for Incorporated Software\n====\n\nexpat\n-----\n")
    return stdlib, notices


def test_cpython_texts_are_the_interpreters_licence_and_the_incorporated_section(tmp_path):
    stdlib, notices = _cpython(tmp_path)
    (own_key, own), (rst_key, rst) = bn.cpython_licences(stdlib, tmp_path / "nowhere", (3, 13), notices)
    assert own == "PSF LICENSE AGREEMENT"
    assert "v3.13.9" in rst_key
    assert rst.startswith("Licenses and Acknowledgements for Incorporated Software") and "expat" in rst


def test_the_windows_licence_beside_the_interpreter_is_found(tmp_path):
    _, notices = _cpython(tmp_path)
    _touch(tmp_path / "win" / "LICENSE.txt", "PSF plus bzip2")
    texts = bn.cpython_licences(tmp_path / "win" / "Lib", tmp_path / "win", (3, 13), notices)
    assert texts[0][1] == "PSF plus bzip2"


@pytest.mark.parametrize("names", [(), ("cpython-v3.13.1-license.rst", "cpython-v3.13.9-license.rst"), ("cpython-v3.12.3-license.rst",)])
def test_cpython_needs_exactly_one_license_rst_for_its_minor_version(tmp_path, names):
    stdlib, notices = _cpython(tmp_path, names)
    with pytest.raises(FileNotFoundError, match="cpython-v3.13"):
        bn.cpython_licences(stdlib, tmp_path, (3, 13), notices)


def test_the_repo_carries_license_rst_for_the_python_the_release_builds_with():
    import re

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    major, minor = re.search(r"python-version: '(\d+)\.(\d+)'", workflow).groups()
    (rst,) = (ROOT / "freeze" / "notices").glob(f"cpython-v{major}.{minor}.*-license.rst")
    text = rst.read_text(encoding="utf-8")
    for component in ("OpenSSL", "expat", "libffi", "zlib", "libmpdec", "mimalloc"):
        assert f"\n{component}\n" in text, component


def test_the_notice_lists_each_library_and_skips_what_qt_notice_covers(tmp_path):
    prefix, site, repo, work, files, dists, dpkg = _ci_layout(tmp_path)
    entries = [
        ("libpython3.13.so.1.0", str(files["libpython"]), "BINARY"),
        ("libssl.so.3", str(files["libssl"]), "BINARY"),
        ("comtypes", str(site / "comtypes" / "__init__.py"), "PYMODULE"),
        ("shiboken6", str(site / "shiboken6" / "__init__.py"), "PYMODULE"),
    ]
    found, packages, cpython, _ = bn.attribute(
        entries, own_dirs=[repo, work], base_prefix=prefix, dist_of=bn.distribution_files(dists).get, dpkg=dpkg,
    )
    by_name = {bn.canonical(d.metadata["Name"]): d for d in dists}
    components = bn.build_components(
        found, packages, cpython, cpython_texts=[("CPython: LICENSE.txt", "PSF")], dpkg=dpkg, find_dist=by_name.get,
    )
    assert [c.name for c in components] == ["CPython", "comtypes", "pyinstaller", "libssl3"]
    text = bn.render(components, "Linux x86_64")
    # verify_artifact.py reads the library names off these four-space lines.
    assert "\n    libpython3.13.so.1.0\n" in text and "\n    libssl.so.3\n" in text
    assert "shiboken6" not in text.split("Licence texts")[0].replace("Qt, PySide6 and shiboken6", "")
    assert "Licence: Apache-2.0" in text
    assert "----- comtypes 1.0: LICENSE -----\ncomtypes licence text\n" in text
    assert "apt-get source" in text


def test_libraries_from_one_copyright_file_share_a_block(tmp_path):
    a = _touch(tmp_path / "lib" / "libxcb-shape.so.0")
    b = _touch(tmp_path / "lib" / "libxcb-xkb.so.1")
    status = "".join(f"Package: {p}\nStatus: install ok installed\nSource: libxcb\nVersion: 1.14-3ubuntu3\n\n" for p in ("libxcb-shape0", "libxcb-xkb1"))
    dpkg = make_dpkg(tmp_path, {"libxcb-shape0:amd64": [str(a)], "libxcb-xkb1:amd64": [str(b)]},
                     {"libxcb-shape0": "License: X11\n", "libxcb-xkb1": "License: X11\n"}, status)
    _, packages, _, _ = bn.attribute(
        [("libxcb-shape.so.0", str(a), "BINARY"), ("libxcb-xkb.so.1", str(b), "BINARY")],
        own_dirs=[], base_prefix=tmp_path / "py", dist_of={}.get, dpkg=dpkg,
    )
    (comp,) = bn.build_components({}, packages, [], cpython_texts=[], dpkg=dpkg, always=())
    assert (comp.name, comp.version) == ("libxcb", "1.14-3ubuntu3")
    assert comp.binaries == ["libxcb-shape.so.0", "libxcb-xkb.so.1"]
    assert len(comp.texts) == 1


def test_the_build_venvs_pyside6_is_a_distribution_qt_notice_covers():
    pyside6 = pytest.importorskip("PySide6")
    files = bn.distribution_files(importlib.metadata.distributions())
    dist = files.get(bn._norm(pyside6.__file__))
    assert dist is not None and bn.canonical(dist.metadata["Name"]) in bn.QT_DISTRIBUTIONS
