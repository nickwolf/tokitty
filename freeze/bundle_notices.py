"""Writes licenses/THIRD-PARTY-NOTICES.txt for one build, from what PyInstaller
collected (#152). freeze/tokitty.spec calls write_notices() after the Qt trim,
so the list follows the bundle: every collected file is put down to a source,
in this order, and a binary or extension nobody accounts for fails the build.

  - A file an installed distribution's RECORD lists: that distribution, with
    the licence files from its metadata. PySide6 and shiboken6 are left to
    Qt-NOTICE.txt.
  - Under the repo or the build folder: Tokitty's own, or made by PyInstaller.
  - On Linux, a file a dpkg package owns: that package, with its
    /usr/share/doc/<package>/copyright and the common licences it points to.
    These are the build host's libraries.
  - Under the build interpreter's prefix: CPython, with the interpreter's
    LICENSE.txt and the incorporated-software section of Doc/license.rst for
    its minor version. The LICENSE.txt of a macOS or Linux Python carries only
    the PSF licence, so the section is kept in freeze/notices/, fetched with:

    gh api -H "Accept: application/vnd.github.raw" \
        "repos/python/cpython/contents/Doc/license.rst?ref=v3.13.16" \
        > freeze/notices/cpython-v3.13.16-license.rst
"""

import glob
import hashlib
import os
import platform
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

NOTICES_DIR = Path(__file__).resolve().parent / "notices"

# Distributions whose notice is Qt-NOTICE.txt, by normalised name.
QT_DISTRIBUTIONS = frozenset(("pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"))

# PyInstaller is always in the bundle: both executables are its bootloader,
# whatever the Analysis lists.
ALWAYS = ("pyinstaller",)

USED = {
    "pyinstaller": (
        "The bootloader both executables start from (GPL-2.0-or-later with the bootloader exception, "
        "which asks for no notice) and the run-time hooks (Apache-2.0). Both texts are in COPYING.txt."
    ),
}

# Kinds that name a file the bundle carries. SYMLINK and DEPENDENCY entries
# point at files listed under their own name.
_FILE_KINDS = ("BINARY", "EXTENSION", "DATA", "PYMODULE", "PYSOURCE")
_BINARY_KINDS = ("BINARY", "EXTENSION")
_LICENCE_NAME = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)([.-].*)?", re.I)


def _norm(path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class DpkgIndex:
    """Which dpkg package owns a file, and that package's version and
    copyright file, read from the dpkg database without running dpkg."""

    def __init__(self, info_dir="/var/lib/dpkg/info", status="/var/lib/dpkg/status", doc_root="/usr/share/doc",
                 common_licenses="/usr/share/common-licenses"):
        self.doc_root = Path(doc_root)
        self.common_licenses = Path(common_licenses)
        self.owners: Dict[str, str] = {}
        for path in sorted(glob.glob(os.path.join(str(info_dir), "*.list"))):
            package = Path(path).stem.split(":", 1)[0]
            for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
                if line and line != "/." and line not in self.owners:
                    self.owners[line] = package
        self.packages: Dict[str, Dict[str, str]] = {}
        text = Path(status).read_text(encoding="utf-8", errors="replace") if Path(status).is_file() else ""
        for block in text.split("\n\n"):
            fields = dict(re.findall(r"^([A-Za-z-]+): (.*)$", block, re.M))
            if "Package" in fields and fields.get("Status", "install ok installed").endswith(" installed"):
                self.packages.setdefault(fields["Package"], fields)

    def owner(self, path: str) -> Optional[str]:
        # Ubuntu merges /lib into /usr/lib, and a package's file list can use
        # either spelling, so try both for the path and for its target.
        for candidate in dict.fromkeys((path, os.path.realpath(path))):
            for spelling in (candidate, *_usrmerge_spellings(candidate)):
                if spelling in self.owners:
                    return self.owners[spelling]
        return None

    def version(self, package: str) -> str:
        return self.packages.get(package, {}).get("Version", "not recorded")

    def source(self, package: str) -> Tuple[str, str]:
        """The source package and its version. dpkg leaves the version out
        of Source when it matches the binary package's."""
        field = self.packages.get(package, {}).get("Source", package)
        match = re.fullmatch(r"(\S+)(?: \((.+)\))?", field.strip())
        return match.group(1), match.group(2) or self.version(package)

    def copyright(self, package: str) -> Tuple[str, str]:
        path = self.doc_root / package / "copyright"
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing, so the bundled files from {package} have no notice")
        return f"{path} ({package} {self.version(package)})", path.read_text(encoding="utf-8", errors="replace")

    def common_licences(self, copyright_text: str) -> List[Tuple[str, str]]:
        """The full texts a copyright file points to in /usr/share/common-licenses
        rather than quoting, such as the GPL and LGPL."""
        out = []
        for name in dict.fromkeys(re.findall(r"/usr/share/common-licenses/([A-Za-z0-9.+_-]*[A-Za-z0-9])", copyright_text)):
            # GPL and LGPL are links to the current versions' files.
            path = (self.common_licenses / name).resolve()
            if path.is_file():
                out.append((str(self.common_licenses / path.name), path.read_text(encoding="utf-8", errors="replace")))
        return out


def _usrmerge_spellings(path: str) -> List[str]:
    for short in ("/lib", "/lib64", "/bin", "/sbin"):
        if path.startswith(short + "/"):
            return ["/usr" + path]
        if path.startswith("/usr" + short + "/"):
            return [path[len("/usr"):]]
    return []


def distribution_files(distributions) -> Dict[str, object]:
    """Every file the given importlib.metadata distributions record, by
    normalised absolute path."""
    found = {}
    for dist in distributions:
        for rel in dist.files or ():
            found.setdefault(_norm(dist.locate_file(rel)), dist)
    return found


def distribution_licence(dist) -> Tuple[str, List[Tuple[str, str]]]:
    """(licence label, [(key, text)]) for a distribution: the files its
    License-File fields name (PEP 639 puts them under licenses/), else any
    LICENSE, COPYING or NOTICE file it records."""
    meta = dist.metadata
    name, version = meta["Name"], meta["Version"]
    label = meta.get("License-Expression") or meta.get("License") or ""
    if not label or len(label) > 200:
        classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        label = ", ".join(classifiers) or "see the licence text"
    recorded = [rel for rel in dist.files or () if _LICENCE_NAME.fullmatch(Path(str(rel)).name)]
    wanted = meta.get_all("License-File") or []
    picked = [rel for rel in recorded if Path(str(rel)).name in wanted] or recorded
    texts = []
    for rel in picked:
        path = Path(dist.locate_file(rel))
        if path.is_file():
            texts.append((f"{name} {version}: {Path(str(rel)).name}", path.read_text(encoding="utf-8", errors="replace")))
    if not texts:
        raise ValueError(f"{name} {version} is in the bundle but ships no licence file; write its notice by hand")
    return label, texts


def cpython_licences(stdlib: Path, base_prefix: Path, version: Tuple[int, int], notices_dir: Path = NOTICES_DIR):
    """The build interpreter's own LICENSE.txt and the incorporated-software
    section of Doc/license.rst for its minor version, as [(key, text)]."""
    own = next((p for p in (Path(stdlib) / "LICENSE.txt", Path(base_prefix) / "LICENSE.txt") if p.is_file()), None)
    if own is None:
        raise FileNotFoundError(f"no LICENSE.txt in {stdlib} or {base_prefix}")
    major, minor = version
    copies = sorted(Path(notices_dir).glob(f"cpython-v{major}.{minor}.*-license.rst"))
    if len(copies) != 1:
        raise FileNotFoundError(
            f"need exactly one freeze/notices/cpython-v{major}.{minor}.*-license.rst, found {len(copies)}; "
            "see the fetch command in freeze/bundle_notices.py"
        )
    rst = copies[0].read_text(encoding="utf-8")
    start = rst.find("Licenses and Acknowledgements for Incorporated Software")
    if start < 0:
        raise ValueError(f"{copies[0]} has no incorporated-software section")
    tag = copies[0].name[len("cpython-"):-len("-license.rst")]
    return [
        ("CPython: LICENSE.txt of the build interpreter", own.read_text(encoding="utf-8", errors="replace")),
        (f"CPython: Doc/license.rst at python/cpython {tag}, from its incorporated-software section", rst[start:]),
    ]


class Component:
    def __init__(self, name, version, licence, home="", used=""):
        self.name, self.version, self.licence, self.home, self.used = name, version, licence, home, used
        self.binaries: List[str] = []
        self.packages: List[str] = []
        self.others = 0
        self.texts: List[Tuple[str, str]] = []

    def add(self, dest: str, kind: str) -> None:
        if kind in _BINARY_KINDS:
            if dest not in self.binaries:
                self.binaries.append(dest)
        else:
            self.others += 1


def attribute(
    entries: Iterable[tuple],
    *,
    own_dirs: Iterable,
    base_prefix,
    dist_of: Callable[[str], Optional[object]],
    dpkg: Optional[DpkgIndex],
):
    """Sort TOC entries (dest, src, kind) by where src came from. Returns
    (distributions {canonical name: (dist, [(dest, kind)])}, dpkg packages
    {name: [(dest, kind)]}, cpython [(dest, kind)], unattributed [(dest, src)])."""
    own = [_norm(d) for d in own_dirs]
    prefix = _norm(base_prefix)
    dists: Dict[str, tuple] = {}
    packages: Dict[str, list] = {}
    cpython, unattributed = [], []
    for entry in entries:
        dest, src, kind = entry[0], entry[1], entry[2]
        if kind not in _FILE_KINDS or not src or not os.path.isfile(src):
            continue
        path = _norm(src)
        # Distributions first: a venv inside the repo is still third-party.
        dist = dist_of(path)
        if dist is not None:
            dists.setdefault(canonical(dist.metadata["Name"]), (dist, []))[1].append((dest, kind))
            continue
        if any(_under(path, d) for d in own):
            continue
        package = dpkg.owner(src) if dpkg is not None else None
        if package is not None:
            packages.setdefault(package, []).append((dest, kind))
        elif _under(path, prefix):
            cpython.append((dest, kind))
        elif kind in _BINARY_KINDS:
            unattributed.append((dest, src))
    return dists, packages, cpython, unattributed


def build_components(dists, packages, cpython, *, cpython_texts, dpkg, always=ALWAYS, find_dist=None):
    components = []
    if cpython or cpython_texts:
        comp = Component("CPython", platform.python_version(), "PSF-2.0, and the licences of the code it incorporates",
                         "https://www.python.org/", "The Python interpreter and standard library.")
        for dest, kind in cpython:
            comp.add(dest, kind)
        comp.texts = list(cpython_texts)
        components.append(comp)
    for name in always:
        if name not in dists and find_dist is not None:
            dist = find_dist(name)
            if dist is not None:
                dists[name] = (dist, [])
    for key in sorted(dists):
        if key in QT_DISTRIBUTIONS:
            continue
        dist, files = dists[key]
        meta = dist.metadata
        label, texts = distribution_licence(dist)
        home = meta.get("Home-page") or next(
            (u.split(", ", 1)[-1] for u in meta.get_all("Project-URL") or [] if u.lower().startswith(("homepage", "source"))), "")
        comp = Component(meta["Name"], meta["Version"], label, home, USED.get(key, ""))
        for dest, kind in files:
            comp.add(dest, kind)
        comp.texts = texts
        components.append(comp)
    # Packages built from one source usually share one copyright file, so
    # they share a block.
    by_text: Dict[str, Component] = {}
    for package in sorted(packages, key=lambda p: (dpkg.source(p), p)):
        key, text = dpkg.copyright(package)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        comp = by_text.get(digest)
        if comp is None:
            source, version = dpkg.source(package)
            licences = sorted(set(re.findall(r"^License: *(\S.*)$", text, re.M)))
            comp = Component(source, version, ", ".join(licences) or "see the copyright file", "",
                             "System libraries from the build host's Ubuntu packages")
            comp.texts = [(key, text), *dpkg.common_licences(text)]
            by_text[digest] = comp
            components.append(comp)
        comp.packages.append(f"{package} {dpkg.version(package)}")
        for dest, kind in packages[package]:
            comp.add(dest, kind)
    return components


def render(components: List[Component], system: str) -> str:
    header = f"""Third-party notices

This file lists the software in this {system} build of Tokitty that is not Tokitty's own, other than Qt, PySide6 and shiboken6, which Qt-NOTICE.txt beside this file covers. It is generated at build time from the files PyInstaller collected into the build, so each build lists what it actually contains. Tokitty's own code is MIT licensed, see LICENSE in this folder.
"""
    if any(comp.packages for comp in components):
        header += (
            "\nThe system libraries come from the Ubuntu packages of the machine that built this release. Each is a separate file "
            "in _internal/ that you can replace with your own build, and its source is the Ubuntu source package named in its "
            "block, at the version given (apt-get source <package>=<version>).\n"
        )
    blocks, texts = [], {}
    for comp in components:
        lines = [comp.name + (f" {comp.version}" if comp.version else ""), f"  Licence: {comp.licence}"]
        if comp.home:
            lines.append(f"  Home: {comp.home}")
        if comp.used:
            lines.append(f"  Used: {comp.used}")
        if comp.packages:
            lines.append(f"  Packages: {', '.join(comp.packages)}")
        if comp.binaries:
            lines.append("  Libraries and extension modules:")
            lines += [f"    {dest}" for dest in sorted(comp.binaries)]
        if comp.others:
            lines.append(f"  Other files (Python modules and data): {comp.others}")
        lines.append(f"  Licence text: {'; '.join(key for key, _ in comp.texts)}")
        blocks.append("\n".join(lines))
        texts.update(comp.texts)
    parts = [header, "\n\n".join(blocks), "\nLicence texts\n"]
    for key, text in texts.items():
        parts.append(f"----- {key} -----\n{text.strip()}\n")
    return "\n".join(parts).rstrip("\n") + "\n"


def write_notices(out: Path, tocs: Iterable[Iterable[tuple]], *, own_dirs: Iterable) -> Path:
    """Generate the notice for the running build into `out`. Raises when a
    collected binary or extension cannot be put down to any source."""
    import importlib.metadata
    import sysconfig

    distributions = list(importlib.metadata.distributions())
    files = distribution_files(distributions)
    by_name = {canonical(d.metadata["Name"]): d for d in distributions}
    dpkg = DpkgIndex() if sys.platform.startswith("linux") and Path("/var/lib/dpkg/info").is_dir() else None
    entries = [entry for toc in tocs for entry in toc]
    dists, packages, cpython, unattributed = attribute(
        entries, own_dirs=own_dirs, base_prefix=sys.base_prefix, dist_of=files.get, dpkg=dpkg,
    )
    if unattributed:
        listing = "\n".join(f"  {dest} from {src}" for dest, src in unattributed)
        raise RuntimeError(f"no notice source for these collected files; extend freeze/bundle_notices.py:\n{listing}")
    texts = cpython_licences(Path(sysconfig.get_paths()["stdlib"]), Path(sys.base_prefix), sys.version_info[:2])
    components = build_components(dists, packages, cpython, cpython_texts=texts, dpkg=dpkg, find_dist=by_name.get)
    system = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(components, f"{system} {platform.machine()}"), encoding="utf-8", newline="\n")
    return out
