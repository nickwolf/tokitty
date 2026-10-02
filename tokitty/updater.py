"""Update check against GitHub releases, and the update.json state (#77).

Pure logic and one network call: nothing here touches Tk or installs anything.
The design is in docs/superpowers/specs/2026-10-02-auto-update-design.md.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

REPO = "nickwolf/tokitty"
DEFAULT_API_URL = "https://api.github.com"
API_URL_ENV = "TOKITTY_UPDATE_API_URL"
CHECK_TIMEOUT = 10.0
SUMS_NAME = "SHA256SUMS"
UPDATE_FILENAME = "update.json"
OWNED_KINDS = ("copy", "staging", "backup")

# Leading zeros are rejected so two spellings of one version can't both exist.
_VERSION_RE = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)

Version = Tuple[int, int, int]


class UpdateCheckError(Exception):
    """Any failure to list releases: network, HTTP status, or a bad body."""


def parse_version(text) -> Optional[Version]:
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.fullmatch(text)
    return tuple(int(part) for part in match.groups()) if match else None


@dataclass(frozen=True)
class RunningVersion:
    version: str
    installable: bool


def _metadata_version() -> Optional[str]:
    from importlib import metadata

    try:
        return "v" + metadata.version("tokitty")
    except metadata.PackageNotFoundError:
        return None


def running_version(
    *,
    frozen: Optional[bool] = None,
    build_id: Optional[str] = None,
    read_metadata: Callable[[], Optional[str]] = _metadata_version,
) -> RunningVersion:
    """Only a frozen build whose own build ID is a release tag can install. A
    source run or a CI dry run (`dryrun-<sha>`) compares by package metadata
    and can only report that a release exists."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if build_id is None:
        build_id = os.environ.get("TOKITTY_BUILD_ID")
    if frozen and parse_version(build_id) is not None:
        return RunningVersion(build_id, True)
    return RunningVersion(read_metadata() or build_id or "dev", False)


def platform_target(sys_platform: Optional[str] = None, machine: Optional[str] = None) -> Optional[str]:
    sys_platform = sys.platform if sys_platform is None else sys_platform
    machine = (platform.machine() if machine is None else machine).lower()
    intel = machine in ("x86_64", "amd64")
    if sys_platform == "win32" and intel:
        return "windows-x64"
    if sys_platform == "darwin" and machine in ("arm64", "aarch64"):
        return "macos-arm64"
    if sys_platform == "darwin" and intel:
        return "macos-x86_64"
    if sys_platform.startswith("linux") and intel:
        return "linux-x86_64"
    return None


def asset_name(tag: str, target: str) -> str:
    ext = "tar.gz" if target.startswith("linux") else "zip"
    return f"tokitty-{tag}-{target}.{ext}"


@dataclass(frozen=True)
class Release:
    tag: str
    html_url: Optional[str]
    asset_url: Optional[str]
    asset_size: Optional[int]
    sums_url: Optional[str]

    @property
    def installable_from_app(self) -> bool:
        return self.asset_url is not None and self.sums_url is not None


def fetch_releases(*, urlopen=None, base_url: Optional[str] = None) -> list:
    """The newest 100 releases as parsed JSON. Raises UpdateCheckError."""
    urlopen = urlopen or urllib.request.urlopen
    base = (base_url or os.environ.get(API_URL_ENV) or DEFAULT_API_URL).rstrip("/")
    request = urllib.request.Request(
        f"{base}/repos/{REPO}/releases?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"tokitty/{running_version().version}",
        },
    )
    try:
        with urlopen(request, timeout=CHECK_TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateCheckError(f"HTTP {exc.code} from the releases list") from exc
    except urllib.error.URLError as exc:
        raise UpdateCheckError(f"Network error reaching the releases list: {exc.reason}") from exc
    except OSError as exc:
        raise UpdateCheckError(f"Network error reaching the releases list: {exc}") from exc
    try:
        releases = json.loads(body)
    except ValueError as exc:
        raise UpdateCheckError(f"The releases list was not valid JSON: {exc}") from exc
    if not isinstance(releases, list):
        raise UpdateCheckError("The releases list was not a JSON array")
    return releases


def select_latest(releases, target: Optional[str]) -> Optional[Release]:
    """The highest stable version by number. `releases/latest` isn't used: it
    sorts by the tagged commit's date, not by version."""
    best: Optional[Tuple[Version, dict]] = None
    for entry in releases if isinstance(releases, list) else []:
        if not isinstance(entry, dict) or entry.get("draft") or entry.get("prerelease"):
            continue
        version = parse_version(entry.get("tag_name"))
        if version is not None and (best is None or version > best[0]):
            best = (version, entry)
    if best is None:
        return None
    entry = best[1]
    tag = entry["tag_name"]
    assets = {}
    for asset in entry.get("assets") if isinstance(entry.get("assets"), list) else []:
        if isinstance(asset, dict) and isinstance(asset.get("name"), str):
            assets.setdefault(asset["name"], asset)
    wanted = assets.get(asset_name(tag, target)) if target else None
    sums = assets.get(SUMS_NAME)
    size = wanted.get("size") if wanted else None
    html_url = entry.get("html_url")
    return Release(
        tag=tag,
        html_url=html_url if isinstance(html_url, str) else None,
        asset_url=_url(wanted),
        asset_size=size if isinstance(size, int) and not isinstance(size, bool) else None,
        sums_url=_url(sums),
    )


def _url(asset: Optional[dict]) -> Optional[str]:
    url = asset.get("browser_download_url") if asset else None
    return url if isinstance(url, str) and url else None


def is_newer(tag: Optional[str], running: str) -> bool:
    latest, current = parse_version(tag), parse_version(running)
    return latest is not None and current is not None and latest > current


@dataclass
class UpdateState:
    last_checked: Optional[str] = None
    latest_tag: Optional[str] = None
    notified_tag: Optional[str] = None
    owned: List[dict] = field(default_factory=list)
    pending: Optional[dict] = None


def load_update_state(state_dir) -> UpdateState:
    """Robust-loaded: each field degrades to its default on its own."""
    path = Path(state_dir) / UPDATE_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UpdateState()
    if not isinstance(data, dict):
        return UpdateState()
    pending = data.get("pending")
    return UpdateState(
        last_checked=_iso_or_none(data.get("last_checked")),
        latest_tag=_tag_or_none(data.get("latest_tag")),
        notified_tag=_tag_or_none(data.get("notified_tag")),
        owned=_owned(data.get("owned")),
        pending=pending if isinstance(pending, dict) else None,
    )


def _iso_or_none(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _tag_or_none(value) -> Optional[str]:
    return value if parse_version(value) is not None else None


def _owned(value) -> List[dict]:
    """Keep well-formed entries one by one. A malformed entry is dropped, which
    only ever makes cleanup more conservative."""
    if not isinstance(value, list):
        return []
    kept = []
    for entry in value:
        if not isinstance(entry, dict) or entry.get("kind") not in OWNED_KINDS:
            continue
        path, version = entry.get("path"), entry.get("version")
        if isinstance(path, str) and path and isinstance(version, str):
            kept.append({"path": path, "version": version, "kind": entry["kind"]})
    return kept


def save_update_state(state_dir, state: UpdateState) -> None:
    """Atomic: a temp file in the same directory, then os.replace."""
    path = Path(state_dir) / UPDATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(state), indent=2) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def add_owned(state_dir, path, version: str, kind: str) -> None:
    """Record a path the updater created, replacing any entry for that path."""
    state = load_update_state(state_dir)
    state.owned = [e for e in state.owned if e["path"] != str(path)]
    state.owned.append({"path": str(path), "version": version, "kind": kind})
    save_update_state(state_dir, state)


def drop_owned(state_dir, path) -> None:
    state = load_update_state(state_dir)
    state.owned = [e for e in state.owned if e["path"] != str(path)]
    save_update_state(state_dir, state)


def check_for_update_cli() -> int:
    """Hidden --check-for-update: print one JSON object, 0 on a good fetch."""
    running = running_version()
    report = {
        "running": running.version,
        "installable": running.installable,
        "latest": None,
        "newer": False,
        "installable_from_app": False,
        "error": None,
    }
    code = 0
    try:
        release = select_latest(fetch_releases(), platform_target())
    except UpdateCheckError as exc:
        report["error"] = str(exc)
        code = 1
    else:
        if release is not None:
            report["latest"] = release.tag
            report["newer"] = is_newer(release.tag, running.version)
            report["installable_from_app"] = release.installable_from_app
    print(json.dumps(report))
    return code
