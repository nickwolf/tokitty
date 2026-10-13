"""Generates freeze/licenses/Qt-NOTICE.txt from the qt_attribution.json files
of the qtbase source release the bundled Qt is built from (#115).

    python -m freeze.qt_notices --qtbase <dir> --out freeze/licenses/Qt-NOTICE.txt

<dir> mirrors the qtbase tree at the tag matching the pinned PySide6 version:
the qt_attribution.json files in their folders, the licence files they name
next to them, and the LICENSES/ folder. The script reads only that folder, so
it runs offline; see the plan for fetching it with `gh api`. Qt's web page
tracks the current release, which is why the notice comes from the tag.
"""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_data import qt_version  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The modules the trimmed bundle ships: QtCore, QtGui, QtWidgets and QtDBus.
# QtWidgets has no entries of its own.
KEPT_MODULES = ("qtcore", "qtgui", "qtwidgets", "qtdbus")

# Entries in a kept module that the bundle still does not contain, with why.
DROPPED_IDS = {
    "extra-cmake-modules": "build system only",
    "kwin": "build system only",
    "android-native-style": "Android only",
    "android-gradle-wrapper": "Android only",
    "dejayvu": "WebAssembly only",
    "libjpeg": "used by the qjpeg plugin, which the trim drops",
}

# Platform plugin entries are kept whichever module they name.
_PLATFORM_PLUGIN = re.compile(r"\b(xcb|cocoa|windows)\b.*platform plugin|platform plugin.*\b(xcb|cocoa|windows)\b", re.I | re.S)


def load_entries(qtbase: Path) -> List[dict]:
    entries = []
    for path in sorted(glob.glob(str(qtbase / "**" / "qt_attribution.json"), recursive=True)):
        # Some files hold raw line breaks inside strings, which strict JSON refuses.
        data = json.loads(Path(path).read_text(encoding="utf-8"), strict=False)
        for entry in data if isinstance(data, list) else [data]:
            entry["_dir"] = Path(path).parent
            entries.append(entry)
    return entries


def classify(entries: List[dict]) -> Tuple[List[dict], List[Tuple[dict, str]]]:
    """Split into kept entries and (dropped entry, reason)."""
    kept, dropped = [], []
    for entry in entries:
        usage = " ".join(str(entry.get("QtUsage") or "").split())
        if entry.get("Id") in DROPPED_IDS:
            dropped.append((entry, DROPPED_IDS[entry["Id"]]))
        elif entry.get("QDocModule") in KEPT_MODULES or _PLATFORM_PLUGIN.search(usage) and "wayland" not in usage.lower():
            kept.append(entry)
        else:
            dropped.append((entry, f"module {entry.get('QDocModule')} is not in the bundle"))
    return kept, dropped


def _spdx_ids(expression: str) -> List[str]:
    return [part for part in re.split(r"\s+(?:AND|OR)\s+", expression.strip()) if part]


def licence_texts(entry: dict, qtbase: Path) -> List[Tuple[str, str]]:
    """The (key, text) pairs for an entry: its licence file when it names one,
    else the SPDX texts under LICENSES/."""
    name = entry.get("LicenseFile")
    if name:
        path = Path(os.path.normpath(entry["_dir"] / name))
        return [(os.path.relpath(path, qtbase).replace("\\", "/"), path.read_text(encoding="utf-8", errors="replace"))]
    out = []
    for spdx in _spdx_ids(entry.get("LicenseId") or ""):
        path = qtbase / "LICENSES" / f"{spdx}.txt"
        out.append((f"LICENSES/{spdx}.txt", path.read_text(encoding="utf-8", errors="replace")))
    return out


def icu_licence(copyright_glob: str = "/usr/share/doc/libicu*/copyright") -> Optional[Tuple[str, str]]:
    """ICU's licence text from the Debian copyright file (its first stanza, the
    one for ICU itself) and the path it came from."""
    for path in sorted(glob.glob(copyright_glob)):
        text = Path(path).read_text(encoding="utf-8")
        stanza = text.split("\nFiles: debian/", 1)[0].split("\nLicense: MIT\n", 1)
        if len(stanza) != 2:
            continue
        lines = [line[1:] if line.startswith(" ") else line for line in stanza[1].splitlines()]
        body = "\n".join("" if line == "." else line for line in lines).strip()
        return path, body
    return None


def _wrap(items) -> str:
    return "\n".join(f"  {item}" for item in items)


def build_notice(qtbase: Path, qt: str, icu: Optional[Tuple[str, str]]) -> str:
    kept, _ = classify(load_entries(qtbase))
    texts: Dict[str, str] = {}
    blocks = []
    for entry in sorted(kept, key=lambda e: (e.get("Name") or e["Id"]).lower()):
        pairs = licence_texts(entry, qtbase)
        texts.update(pairs)
        copyrights = entry.get("Copyright") or []
        if isinstance(copyrights, str):
            copyrights = [copyrights]
        lines = [
            f"{entry.get('Name') or entry['Id']}",
            f"  Version: {entry.get('Version') or 'not stated'}",
            f"  Licence: {entry.get('LicenseId')}",
            f"  Home: {entry.get('Homepage') or 'not stated'}",
            f"  Used: {' '.join(str(entry.get('QtUsage') or '').split())}",
        ]
        if copyrights:
            lines.append("  Copyright:")
            lines.append(_wrap(f"  {c}" for c in copyrights))
        lines.append(f"  Licence text: {', '.join(key for key, _ in pairs)}")
        blocks.append("\n".join(lines))
    if icu is not None:
        blocks.append(
            "ICU (International Components for Unicode)\n"
            "  Licence: ICU / Unicode licence, text below\n"
            "  Home: https://icu.unicode.org/\n"
            "  Used: Linux only. The Qt libraries in the Linux wheel link the ICU libraries it ships\n"
            "        (libicudata, libicui18n, libicuuc, in the same folder as the Qt libraries).\n"
            f"  Licence text: ICU, from {icu[0]}"
        )
    header = f"""Qt and PySide6 notice

This application contains Qt {qt}, PySide6 {qt} and shiboken6 {qt}, which are licensed under the GNU Lesser General Public License, version 3 (LGPL-3.0). The full LGPL-3.0 text is in LGPL-3.0.txt and the GPL-3.0 it incorporates is in GPL-3.0.txt, both beside this file.

Source code
  Qt: qtbase-everywhere-src-{qt}, https://download.qt.io/archive/qt/{qt.rsplit('.', 1)[0]}/{qt}/submodules/qtbase-everywhere-src-{qt}.tar.xz
  PySide6 and shiboken6: the {qt} tag of https://code.qt.io/cgit/pyside/pyside-setup.git (mirror: https://github.com/qt/pyside-setup)

Where the Qt libraries are, and how to replace them
  Qt is not compiled into the tokitty program. It sits as separate files in the application folder, so you can swap in your own build of the same Qt version (the relinking the LGPL asks for).
  Windows: the Qt6*.dll files and the plugin folders in _internal\\PySide6\\, next to the QtCore.pyd, QtGui.pyd and QtWidgets.pyd bindings.
  Linux: the libQt6*.so.6 files under _internal/PySide6/Qt/lib/ and the plugins under _internal/PySide6/Qt/plugins/.
  macOS: the QtCore, QtGui, QtWidgets and QtDBus frameworks under Tokitty.app/Contents/Frameworks/PySide6/Qt/lib/.
  Replace a file with the same file from a compatible build of Qt {qt}, then start the application again.
  PySide6 and shiboken6 are replaceable the same way: their binding modules sit beside the Qt files, and their Python code ships as plain .py files in the PySide6 and shiboken6 folders (on macOS under Tokitty.app/Contents/Resources/), not inside the compiled application archive.
  The licence files for the whole application are in the licenses folder: beside the executable on Windows and Linux, and in Tokitty.app/Contents/Resources/licenses on macOS.

Tokitty's own code is MIT licensed, see LICENSE in this folder. It uses Qt through PySide6 and does not modify it.

Third-party components in the Qt modules this application ships
  The list below comes from the qt_attribution.json files of the qtbase {qt} source release, for QtCore, QtGui, QtWidgets, QtDBus and the platform plugins. Components used only by Qt modules, plugins or platforms that this application leaves out are not listed. Some entries cover code that only a given operating system's build uses, and the Used line says which.
"""
    parts = [header, "\n\n".join(blocks), "\nLicence texts\n"]
    for key in sorted(texts):
        parts.append(f"----- {key} -----\n{texts[key].strip()}\n")
    if icu is not None:
        parts.append(f"----- ICU, from {icu[0]} -----\n{icu[1]}\n")
    return "\n".join(parts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--qtbase", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--no-icu", action="store_true", help="leave out the Linux ICU licence")
    args = parser.parse_args(argv)
    icu = None if args.no_icu else icu_licence()
    if icu is None and not args.no_icu:
        print("warning: no /usr/share/doc/libicu*/copyright found, ICU left out", file=sys.stderr)
    text = build_notice(args.qtbase, qt_version(ROOT), icu)
    args.out.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    kept, dropped = classify(load_entries(args.qtbase))
    print(f"kept {len(kept)} entries, dropped {len(dropped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
