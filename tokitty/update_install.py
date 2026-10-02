"""Download, unpack and verify a release into a staging folder (#77).

Everything here stops at a validated, self-checked staging folder: nothing is
renamed into place or launched. The design is in
docs/superpowers/specs/2026-10-02-auto-update-design.md ("Installing", steps
1 to 4, and "The clean child environment"). Platform branches take
`sys_platform` so Linux tests can drive the Windows and macOS logic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from tokitty.updater import Release, add_owned, drop_owned, parse_version, running_version

CHUNK = 64 * 1024
IO_TIMEOUT = 30.0
SELF_CHECK_TIMEOUT = 60.0
DITTO_TIMEOUT = 120.0
MAX_SUMS_BYTES = 64 * 1024
MAC_TOP = "Tokitty.app"

_SUMS_LINE = re.compile(r"([0-9a-fA-F]{64}) [ *](.+)")


class UpdateInstallError(Exception):
    """A download, unpack or check failed; the message is shown to the user."""


class UpdateCancelled(Exception):
    """The user cancelled while the update was downloading."""


def _kind(sys_platform: str) -> Optional[str]:
    if sys_platform == "win32":
        return "windows"
    if sys_platform == "darwin":
        return "macos"
    return "linux" if sys_platform.startswith("linux") else None


@dataclass(frozen=True)
class Target:
    """Where an update goes. `parent` is the versions dir (Windows, Linux) or
    the folder holding the .app (macOS): staging folders are made there, and
    it is the folder that must be writable."""

    parent: Path
    top: str
    sys_platform: str
    refusal: Optional[str] = None

    def final_path(self, tag: str) -> Path:
        if _kind(self.sys_platform) == "macos":
            return self.parent / self.top
        return self.parent / tag / self.top


def top_name(sys_platform: str) -> str:
    kind = _kind(sys_platform)
    return MAC_TOP if kind == "macos" else "Tokitty" if kind == "windows" else "tokitty"


def binary_paths(top: Path, sys_platform: str) -> Tuple[Path, Path]:
    """The GUI executable and tokitty-hook inside an unpacked top entry; the
    same relative paths as freeze/verify_artifact.py `_locate_binaries`."""
    kind = _kind(sys_platform)
    if kind == "windows":
        return top / "Tokitty.exe", top / "tokitty-hook.exe"
    if kind == "macos":
        macos_dir = top / "Contents" / "MacOS"
        return macos_dir / "Tokitty", macos_dir / "tokitty-hook"
    return top / "tokitty", top / "tokitty-hook"


def install_target(executable, sys_platform: Optional[str] = None) -> Target:
    sys_platform = sys.platform if sys_platform is None else sys_platform
    kind = _kind(sys_platform)
    exe = Path(os.path.realpath(executable))
    top = top_name(sys_platform)
    release = exe.parent
    if kind is None:
        return Target(release.parent, top, sys_platform, "Updates aren't supported on this platform.")
    if kind == "macos":
        app = next((p for p in exe.parents if p.suffix == ".app"), None)
        if app is None:
            return Target(release.parent, top, sys_platform, "Tokitty isn't running from an app bundle.")
        refusal = None if app.name == MAC_TOP else f"This copy is named {app.name}, not {MAC_TOP}."
        return Target(app.parent, top, sys_platform, refusal)
    versions = release.parent.parent if parse_version(release.parent.name) else release.parent
    return Target(versions, top, sys_platform)


def probe_writable(directory) -> bool:
    """Create and delete a uniquely named file: group membership and mode bits
    don't say whether a write will really work."""
    probe = Path(directory) / f".tokitty-probe-{uuid.uuid4().hex}"
    try:
        with open(probe, "x"):
            pass
        probe.unlink()
    except OSError:
        return False
    return True


def child_env(base_env, sys_platform: str, *, self_check: bool = False) -> dict:
    """The environment for any child the updater starts. A frozen child must
    treat itself as a fresh top-level process, and on Linux must not load the
    old bundle's libraries."""
    env = dict(base_env)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if _kind(sys_platform) == "linux":
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    if self_check:
        env["TOKITTY_SELF_CHECK_TK"] = "1"
    return env


def parse_sums(text: str) -> Dict[str, str]:
    """`sha256sum` output: `<hex>  name`, or `<hex> *name` for binary mode."""
    sums: Dict[str, str] = {}
    for line in text.splitlines():
        match = _SUMS_LINE.fullmatch(line.strip())
        if match:
            sums.setdefault(match.group(2), match.group(1).lower())
    return sums


def _open_https(url: str, urlopen):
    """Open `url`, refusing plain HTTP before and after any redirect."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise UpdateInstallError(f"Refusing a non-HTTPS download: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": f"tokitty/{running_version().version}"})
    try:
        response = (urlopen or urllib.request.urlopen)(request, timeout=IO_TIMEOUT)
    except OSError as exc:
        raise UpdateInstallError(f"Could not download {url}: {exc}") from exc
    final = response.geturl()
    if urllib.parse.urlsplit(final).scheme != "https":
        response.close()
        raise UpdateInstallError(f"Refusing a redirect to a non-HTTPS URL: {final}")
    return response


def download(url, dest, *, expected_size, expected_sha256, progress, cancelled, urlopen=None) -> None:
    """Stream `url` to `dest`, hashing as it goes. Removes `dest` on failure."""
    digest, done = hashlib.sha256(), 0
    try:
        with _open_https(url, urlopen) as response, open(dest, "wb") as out:
            while True:
                if cancelled():
                    raise UpdateCancelled()
                try:
                    chunk = response.read(CHUNK)
                except OSError as exc:
                    raise UpdateInstallError(f"The download was interrupted: {exc}") from exc
                if not chunk:
                    break
                done += len(chunk)
                if done > expected_size:
                    raise UpdateInstallError("The download is larger than the release says.")
                digest.update(chunk)
                out.write(chunk)
                progress(done, expected_size)
        if done != expected_size:
            raise UpdateInstallError(f"The download is {done} bytes, the release says {expected_size}.")
        if digest.hexdigest() != expected_sha256.lower():
            raise UpdateInstallError("The download doesn't match its SHA256SUMS line.")
    except BaseException:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise


def _fetch_sums(url: str, urlopen) -> Dict[str, str]:
    with _open_https(url, urlopen) as response:
        body = response.read(MAX_SUMS_BYTES + 1)
    if len(body) > MAX_SUMS_BYTES:
        raise UpdateInstallError("SHA256SUMS is unexpectedly large.")
    return parse_sums(body.decode("utf-8", "replace"))


def _is_link(path) -> bool:
    """A symlink, or on Windows a junction or any other reparse point."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def validate_layout(root, sys_platform: str) -> Path:
    """Check an unpacked tree and return its single top entry. Raises
    UpdateInstallError for anything but exactly the expected layout."""
    root = Path(root)
    expected = top_name(sys_platform)
    names = sorted(os.listdir(root))
    if names != [expected]:
        raise UpdateInstallError(f"The archive should hold only {expected}, not {names or 'nothing'}.")
    top = root / expected
    if _is_link(top) or not top.is_dir():
        raise UpdateInstallError(f"{expected} in the archive is not a plain folder.")
    for binary in binary_paths(top, sys_platform):
        if os.path.islink(binary) or not binary.is_file():
            raise UpdateInstallError(f"The archive is missing {binary.relative_to(top)}.")
    real_root = Path(os.path.realpath(root))
    windows = _kind(sys_platform) == "windows"
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            if windows and _is_link(path):
                raise UpdateInstallError(f"The archive contains a link: {name}")
            if not windows and os.path.islink(path) and not Path(os.path.realpath(path)).is_relative_to(real_root):
                raise UpdateInstallError(f"The archive contains a link that leaves the app: {name}")
    return top


def _unpack_zip(archive: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as zf:
            real_dest = os.path.realpath(dest)
            for name in zf.namelist():
                parts = re.split(r"[\\/]", name)
                target = os.path.realpath(os.path.join(dest, name))
                if ".." in parts or re.match(r"[A-Za-z]:|[\\/]", name) or not Path(target).is_relative_to(real_dest):
                    raise UpdateInstallError(f"The archive has an unsafe path: {name}")
            zf.extractall(dest)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateInstallError(f"Could not unpack the archive: {exc}") from exc


def _unpack_tar(archive: Path, dest: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise UpdateInstallError(f"Could not unpack the archive: {exc}") from exc


def _unpack_ditto(archive: Path, dest: Path, run) -> None:
    """ditto, not zipfile, which drops the symlinks and modes inside
    Python.framework."""
    argv = ["ditto", "-x", "-k", str(archive), str(dest)]
    try:
        proc = (run or subprocess.run)(
            argv, env=child_env(os.environ, "darwin"), capture_output=True, timeout=DITTO_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateInstallError(f"Could not run ditto: {exc}") from exc
    if proc.returncode != 0:
        raise UpdateInstallError(f"ditto failed with exit {proc.returncode}.")


@dataclass(frozen=True)
class Staged:
    staging: Path
    top: Path
    gui: Path


def discard_staging(state_dir, staging) -> None:
    """Remove a staging folder and its `owned` entry. A folder that can't be
    removed keeps its entry, so a later cleanup pass may retry."""
    shutil.rmtree(staging, ignore_errors=True)
    if not os.path.lexists(staging):
        drop_owned(state_dir, staging)


def stage(
    release: Release,
    target: Target,
    state_dir,
    *,
    sys_platform: Optional[str] = None,
    progress: Callable[[int, int], None] = lambda done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
    urlopen=None,
    run=None,
    verify: Optional[Callable[[Path], None]] = None,
    pid: Optional[int] = None,
) -> Staged:
    """Download, unpack and validate `release` beside the target. `verify` is
    called with the staged GUI executable (the self-check). Any failure or
    cancel removes the staging folder and leaves everything else untouched."""
    sys_platform = sys.platform if sys_platform is None else sys_platform
    if target.refusal:
        raise UpdateInstallError(target.refusal)
    if not release.installable_from_app or release.asset_size is None:
        raise UpdateInstallError(f"{release.tag} has no installable download for this platform.")
    staging = target.parent / f".tokitty-update-{release.tag}-{os.getpid() if pid is None else pid}"
    add_owned(state_dir, staging, release.tag, "staging")
    try:
        staging.mkdir()
        sums = _fetch_sums(release.sums_url, urlopen)
        name = urllib.parse.unquote(urllib.parse.urlsplit(release.asset_url).path.rsplit("/", 1)[-1])
        if name not in sums:
            raise UpdateInstallError(f"SHA256SUMS has no line for {name}.")
        archive, unpacked = staging / name, staging / "unpacked"
        download(
            release.asset_url,
            archive,
            expected_size=release.asset_size,
            expected_sha256=sums[name],
            progress=progress,
            cancelled=cancelled,
            urlopen=urlopen,
        )
        if cancelled():
            raise UpdateCancelled()
        unpacked.mkdir()
        kind = _kind(sys_platform)
        if kind == "windows":
            _unpack_zip(archive, unpacked)
        elif kind == "macos":
            _unpack_ditto(archive, unpacked, run)
        else:
            _unpack_tar(archive, unpacked)
        top = validate_layout(unpacked, sys_platform)
        gui = binary_paths(top, sys_platform)[0]
        if verify is not None:
            verify(gui)
        return Staged(staging, top, gui)
    except BaseException as exc:
        discard_staging(state_dir, staging)
        if isinstance(exc, OSError):
            raise UpdateInstallError(f"Could not stage the update: {exc}") from exc
        raise


def run_self_check(exe, tag: str, *, runner=None, sys_platform: Optional[str] = None) -> None:
    """Run the staged copy's `--self-check` with the real Tk window check on.
    Raises UpdateInstallError unless every check passes and the build ID is
    the release tag."""
    sys_platform = sys.platform if sys_platform is None else sys_platform
    try:
        proc = (runner or subprocess.run)(
            [str(exe), "--self-check"],
            env=child_env(os.environ, sys_platform, self_check=True),
            capture_output=True,
            timeout=SELF_CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateInstallError(f"The new version's self-check took longer than {int(SELF_CHECK_TIMEOUT)} s.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateInstallError(f"The new version's self-check could not run: {exc}") from exc
    out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else proc.stdout or ""
    try:
        report = json.loads(out.strip())
    except ValueError as exc:
        raise UpdateInstallError("The new version's self-check printed no report.") from exc
    checks = report.get("checks") if isinstance(report, dict) else None
    if not isinstance(checks, dict) or "tk_root" not in checks:
        raise UpdateInstallError("The new version's self-check report is incomplete.")
    failed = sorted(n for n, c in checks.items() if not (isinstance(c, dict) and c.get("ok") is True))
    if failed:
        raise UpdateInstallError(f"The new version failed its self-check: {', '.join(failed)}.")
    if report.get("build_id") != tag:
        raise UpdateInstallError(f"The new version reports build {report.get('build_id')}, not {tag}.")
