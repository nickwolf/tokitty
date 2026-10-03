#!/usr/bin/env python3
"""Verifier for the in-app updater, run against real frozen builds (#77).

Drives an old frozen copy (built with TOKITTY_BUILD_ID=v0.0.1) through its
hidden --apply-update entry against a fake GitHub on 127.0.0.1, with the new
release being the exact archive CI is about to upload (or, on a
workflow_dispatch rehearsal, a v0.0.2 rebuild). Every child runs in a scratch
environment, so the state dir is under --work; it is read from the child's own
--self-check report rather than recomputed.

The updater refuses plain HTTP, so the fake server speaks HTTPS with a
throwaway CA and leaf made by the `cryptography` package, which must be
installed in the Python that runs this script (not in the frozen bundle). The
CA is handed to the frozen child through SSL_CERT_FILE. The updater has no HTTP
escape hatch: if the frozen child can't verify the certificate, the step fails
with the child's stderr.

Usage:
  verify_update.py --old-archive <old.zip|.tar.gz> --new-archive <new archive> \\
      --new-sums <new archive>.sha256 --tag <tag> --work <empty scratch dir> \\
      --report <report.json> [--rehearsal] [--repo-root <repo>] [--only a,b]

--old-archive is the v0.0.1 fixture, archived the way the release job archives
(zip on Windows, ditto on macOS, tar.gz on Linux) and unpacked here with the
same tools. --new-archive must be named tokitty-<tag>-<target>.<ext>, and
--new-sums holds its sha256sum line. --repo-root (default: the parent of this
script's directory) is put on sys.path to import the updater's own
`child_env`, `asset_name` and the single-instance lock, none of which touch Tk.

Steps, in order (only `prepare` stops the run; the rest each get a fresh
scratch environment and always run):
  prepare           inputs agree, throwaway CA and leaf written
  env_dump          the new copy's --self-check `env` field, with and without
                    the updater's child_env (informational values)
  tls_github        old copy --check-for-update against the real api.github.com
  apply_update      old copy --apply-update: exit 0, ack consumed, one new
                    process holding the lock, `current` in the new copy, the
                    new self-check reports the tag, no Zone.Identifier or
                    quarantine mark, macOS bundle and backup layout
  no_ack_rollback   TOKITTY_UPDATE_TEST_NO_ACK=1: exit 2 well inside the ack
                    limit, nothing left running, `current` and macOS bundle
                    back on the old copy
  bad_checksum      wrong SHA256SUMS line: exit 1, versions dir unchanged
  bad_layout        archive with an extra top-level entry: same
  cleanup           the spec's seed set, a launch of the new copy, survivors
  rename_swap       macOS only: the real renamex_np on two temp dirs
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_artifact import (  # noqa: E402
    _locate_binaries,
    _scratch_env,
    _work_dir_unusable_reason,
    run_child,
    write_report,
)

OLD_TAG = "v0.0.1"
APPLY_TIMEOUT = 300
# The old copy waits up to 120 s for the ack; a no-ack run must end well
# inside that because the wait aborts as soon as the new copy has exited.
NO_ACK_LIMIT_S = 100
CLEANUP_TIMEOUT = 60
REPO_API_PATH = "/repos/nickwolf/tokitty/releases"
ENV_PREFIXES = ("TOKITTY_UPDATE", "SSL_CERT_FILE", "PYINSTALLER_RESET_ENVIRONMENT")


# ---------------------------------------------------------------------------
# pure helpers (unit tested in tests/test_verify_update.py)
# ---------------------------------------------------------------------------


def top_name(plat: str) -> str:
    """The archive's single top-level entry."""
    return "Tokitty.app" if plat == "darwin" else "Tokitty" if plat == "win32" else "tokitty"


def asset_file_name(tag: str, target: str) -> str:
    ext = "tar.gz" if target.startswith("linux") else "zip"
    return f"tokitty-{tag}-{target}.{ext}"


def sums_text(name: str, digest: str) -> str:
    """One `sha256sum` line: hex digest, two spaces, file name only."""
    return f"{digest}  {name}\n"


def parse_sums_text(text: str) -> Dict[str, str]:
    sums: Dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if match:
            sums.setdefault(match.group(2), match.group(1).lower())
    return sums


def release_list(tag: str, archive_name: str, archive_size: int, base_url: str, sums_size: int) -> list:
    """What GET /repos/nickwolf/tokitty/releases?per_page=100 returns: one
    published release holding the archive and SHA256SUMS."""
    download = f"{base_url}/download/{tag}"
    return [
        {
            "tag_name": tag,
            "draft": False,
            "prerelease": False,
            "html_url": f"https://github.com/nickwolf/tokitty/releases/tag/{tag}",
            "assets": [
                {"name": archive_name, "size": archive_size, "browser_download_url": f"{download}/{archive_name}"},
                {"name": "SHA256SUMS", "size": sums_size, "browser_download_url": f"{download}/SHA256SUMS"},
            ],
        }
    ]


def tree_snapshot(root, depth: int = 2) -> List[str]:
    """Sorted paths below `root` down to `depth` levels, links marked, for
    'the versions dir is exactly as it was' comparisons."""
    root = Path(root)
    found: List[str] = []

    def walk(directory: Path, level: int) -> None:
        for entry in sorted(directory.iterdir()):
            rel = entry.relative_to(root).as_posix()
            is_link = entry.is_symlink()
            found.append(rel + (" -> link" if is_link else ""))
            if level < depth and entry.is_dir() and not is_link:
                walk(entry, level + 1)

    if root.is_dir():
        walk(root, 1)
    return found


def snapshot_diff(before: Sequence[str], after: Sequence[str]) -> List[str]:
    return [f"added: {p}" for p in sorted(set(after) - set(before))] + [
        f"removed: {p}" for p in sorted(set(before) - set(after))
    ]


def survivor_problems(kept: Sequence[Path], gone: Sequence[Path], exists: Callable[[Path], bool] = os.path.lexists) -> List[str]:
    """The cleanup step's verdict: everything in `kept` is still there and
    everything in `gone` is not."""
    problems = [f"deleted but should have survived: {p}" for p in kept if not exists(p)]
    problems += [f"survived but should have been deleted: {p}" for p in gone if exists(p)]
    return problems


def cleanup_seed(plat: str, base: Path, tag: str) -> dict:
    """The spec's cleanup seed set, laid out relative to `base` (the versions
    dir on Windows and Linux, the folder holding the app on macOS). Needs a tag
    above v0.0.1, which every tag build and the v0.0.2 rehearsal are.

    `dirs` are plain folders to create, `links` are (link, target) pairs,
    `owned` is what update.json records, `kept` and `gone` are the expected
    outcome. The owned staging entry that is really a link would be deleted if
    it were a folder, and the unowned staging-looking folder would be deleted if
    it were recorded, so each proves its own rule."""
    top = top_name(plat)
    stale_staging = base / ".tokitty-update-v0.0.9-4242"
    link = base / ".tokitty-update-v0.0.8-1"
    unowned_staging = base / ".tokitty-update-v0.0.7-9"
    if plat == "darwin":
        replaced = base / ".Tokitty-v0.0.1.app"
        older = base / ".Tokitty-v0.0.0.app"
        unowned = base / ".Tokitty-v0.0.0-unowned.app"
        owned = [
            {"path": str(replaced), "version": "v0.0.1", "kind": "backup"},
            {"path": str(older), "version": "v0.0.0", "kind": "backup"},
        ]
        dirs = [replaced, older, unowned]
    else:
        replaced = base / "v0.0.1" / top
        older = base / "v0.0.0" / top
        unowned = base / "v0.0.0-unowned"
        owned = [
            {"path": str(replaced), "version": "v0.0.1", "kind": "copy"},
            {"path": str(older), "version": "v0.0.0", "kind": "copy"},
        ]
        dirs = [replaced, older, unowned]
    owned += [
        {"path": str(stale_staging), "version": "v0.0.9", "kind": "staging"},
        {"path": str(link), "version": "v0.0.8", "kind": "staging"},
    ]
    dirs += [stale_staging, unowned_staging]
    return {
        "dirs": dirs,
        "links": [(link, base.parent / "link-target")],
        "owned": owned,
        "kept": [replaced, unowned, unowned_staging, link, base.parent / "link-target" / "sentinel.txt"],
        "gone": [older, stale_staging],
    }


def make_bad_layout_archive(path, plat: str) -> None:
    """A small archive with the right top folder plus an extra top-level
    entry, which the updater's layout check must reject. Windows and macOS
    take a zip (ditto reads it), Linux a tar.gz."""
    top = top_name(plat)
    if plat == "linux" or plat.startswith("linux"):
        with tarfile.open(path, "w:gz") as tf:
            for name, data in ((f"{top}/placeholder.txt", b"x"), ("extra.txt", b"extra")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    else:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"{top}/placeholder.txt", b"x")
            zf.writestr("extra.txt", b"extra")


def parse_tasklist_csv(text: str) -> List[Tuple[str, int]]:
    """`tasklist /FO CSV /NH` rows as (image name, pid). The 'INFO: No tasks'
    line, which isn't CSV with a numeric second column, is skipped."""
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[1].strip().isdigit():
            rows.append((row[0], int(row[1])))
    return rows


def parse_ps(text: str) -> List[Tuple[int, str]]:
    """`ps -axo pid=,args=` lines as (pid, args)."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if parts and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1] if len(parts) > 1 else ""))
    return rows


def gui_processes_from_ps(rows: Sequence[Tuple[int, str]], roots: Sequence[str], gui_name: str) -> List[dict]:
    """Rows whose executable (the first word of args) is the GUI binary under
    one of `roots`. The hook runner has another basename and never matches."""
    found = []
    for pid, args in rows:
        words = args.split(None, 1)
        exe = words[0] if words else ""
        if os.path.basename(exe) != gui_name:
            continue
        if any(exe.startswith(root.rstrip("/\\") + "/") for root in roots):
            found.append({"pid": pid, "path": exe})
    return found


def single_process_problems(procs: Sequence[dict], new_exe, old_pid: Optional[int]) -> List[str]:
    """Exactly one live Tokitty GUI process, not the old one, and (where its
    path is known) the new copy's executable."""
    problems = []
    if len(procs) != 1:
        problems.append(f"expected exactly 1 Tokitty process, found {len(procs)}: {list(procs)}")
    if old_pid is not None and any(p["pid"] == old_pid for p in procs):
        problems.append(f"the old process {old_pid} is still running")
    for proc in procs:
        path = proc.get("path")
        if path and os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.realpath(str(new_exe))):
            problems.append(f"process {proc['pid']} runs {path}, not {new_exe}")
    return problems


def tail(data, limit: int = 2000) -> str:
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data or "")
    return text[-limit:]


# ---------------------------------------------------------------------------
# the fake GitHub, over HTTPS
# ---------------------------------------------------------------------------


def make_certificates(directory: Path) -> dict:
    """A CA and a 127.0.0.1 leaf. Python 3.13 (the frozen runtime) verifies
    with VERIFY_X509_STRICT, which rejects a bare self-signed leaf, so the CA
    carries basicConstraints CA:TRUE (critical), keyUsage keyCertSign and a
    subject key id, and the leaf an authority key id, SAN, digitalSignature
    and serverAuth."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    start, end = now - datetime.timedelta(hours=1), now + datetime.timedelta(days=2)

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tokitty verifier CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    paths = {"ca": directory / "ca.pem", "leaf": directory / "leaf.pem", "key": directory / "leaf.key"}
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["leaf"].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return paths


class FakeGitHub:
    """Serves the releases list, the archive and SHA256SUMS over HTTPS on an
    ephemeral 127.0.0.1 port. `requests` records every path asked for and
    `errors` every handshake or handler failure, for the report."""

    def __init__(self, certs: dict, tag: str, asset: str, archive: Path, sums: str):
        self.tag, self.asset, self.archive, self.sums = tag, asset, Path(archive), sums.encode("utf-8")
        self.requests: List[str] = []
        self.errors: List[str] = []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certs["leaf"]), str(certs["key"]))
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 60

            def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
                pass

            def _send(self, body: bytes, ctype: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 - stdlib name
                outer.requests.append(self.path)
                path = self.path.split("?", 1)[0]
                download = f"/download/{outer.tag}/"
                if path == REPO_API_PATH:
                    body = json.dumps(
                        release_list(
                            outer.tag,
                            outer.asset,
                            outer.archive.stat().st_size,
                            outer.base_url,
                            len(outer.sums),
                        )
                    ).encode("utf-8")
                    self._send(body, "application/json")
                elif path == download + "SHA256SUMS":
                    self._send(outer.sums, "text/plain")
                elif path == download + outer.asset:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(outer.archive.stat().st_size))
                    self.end_headers()
                    with open(outer.archive, "rb") as f:
                        shutil.copyfileobj(f, self.wfile, 64 * 1024)
                else:
                    self.send_error(404)

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                sock, addr = super().get_request()
                # The handshake happens lazily in the handler thread, so one
                # client that never finishes it can't block accept().
                return context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

            def handle_error(self, request, client_address):
                outer.errors.append(f"{client_address}: {sys.exc_info()[1]!r}")

        self._server = Server(("127.0.0.1", 0), Handler)
        self.base_url = f"https://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "FakeGitHub":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------


def _gui_name(plat: str) -> str:
    return "Tokitty.exe" if plat == "win32" else "Tokitty" if plat == "darwin" else "tokitty"


def _windows_process_path(pid: int) -> Optional[str]:
    proc, err = run_child(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"(Get-Process -Id {pid}).Path"],
        dict(os.environ),
        timeout=30,
    )
    if err or proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip() or None


def list_gui_processes(roots: Sequence[str]) -> List[dict]:
    """Live Tokitty GUI processes: on Windows every Tokitty.exe from
    `tasklist /FO CSV` (which has no path, so one is looked up per pid), else
    those from `ps` whose executable lies under one of `roots`."""
    if sys.platform == "win32":
        proc, err = run_child(["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq Tokitty.exe"], dict(os.environ), timeout=60)
        if err or proc.returncode != 0:
            raise RuntimeError(err or f"tasklist exit {proc.returncode}: {tail(proc.stderr)}")
        rows = parse_tasklist_csv(proc.stdout.decode("utf-8", "replace"))
        return [{"pid": pid, "path": _windows_process_path(pid)} for name, pid in rows if name.lower() == "tokitty.exe"]
    proc, err = run_child(["ps", "-axo", "pid=,args="], dict(os.environ), timeout=60)
    if err or proc.returncode != 0:
        raise RuntimeError(err or f"ps exit {proc.returncode}: {tail(proc.stderr)}")
    return gui_processes_from_ps(parse_ps(proc.stdout.decode("utf-8", "replace")), roots, _gui_name(sys.platform))


def wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _ere_escape(text: str) -> str:
    """Escape only the characters special in an extended regex. re.escape also
    escapes `-` and `#`, which BSD pkill may not read as a
    literal."""
    return re.sub(r"([.\[\]*+?(){}|^$\\])", r"\\\1", text)


def stop_tokitty(scn_root: Path) -> None:
    """Kill every Tokitty this scenario started, whatever happened. Windows
    kills by image name, the only Tokitty.exe on a fresh runner being ours;
    elsewhere `pkill -f` on the scenario's own paths, so the verifier itself
    (whose command line names only --work) is never matched."""
    roots = [str(scn_root / "releases"), str(scn_root / "apps")]
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/IM", "Tokitty.exe"], capture_output=True, timeout=60)
    else:
        for root in roots:
            subprocess.run(["pkill", "-KILL", "-f", _ere_escape(root)], capture_output=True, timeout=60)

    def gone() -> bool:
        try:
            return not list_gui_processes(roots)
        except RuntimeError:
            return False

    wait_until(gone, 15)


# ---------------------------------------------------------------------------
# scenario plumbing
# ---------------------------------------------------------------------------


class Scenario:
    """A scratch install of the old copy: its own HOME and state dir, and the
    layout the updater expects (`releases/v0.0.1/<top>` or `apps/Tokitty.app`)."""

    def __init__(self, ctx: dict, name: str):
        self.ctx, self.name = ctx, name
        self.dir = ctx["work"] / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.env = scratch_env(self.dir)
        self.plat = sys.platform
        if self.plat == "darwin":
            self.versions = self.dir / "apps"
            self.old_app_dir = self.versions
            self.new_app_dir = self.versions
        else:
            self.versions = self.dir / "releases"
            self.old_app_dir = self.versions / OLD_TAG / top_name(self.plat)
            self.new_app_dir = self.versions / ctx["tag"] / top_name(self.plat)
        self.state_dir: Optional[Path] = None

    @property
    def old_gui(self) -> Path:
        return _locate_binaries(self.old_app_dir)[0]

    @property
    def new_gui(self) -> Path:
        return _locate_binaries(self.new_app_dir)[0]

    @property
    def new_hook(self) -> Path:
        return _locate_binaries(self.new_app_dir)[1]

    def unpack_old(self) -> None:
        dest = self.versions if self.plat == "darwin" else self.versions / OLD_TAG
        unpack_archive(self.ctx["old_archive"], dest)
        if not self.old_gui.is_file():
            raise RuntimeError(f"the old fixture has no executable at {self.old_gui}")

    def read_state_dir(self, gui: Path) -> Path:
        """The state dir as the frozen child itself reports it, which must lie
        under this scenario: nothing here may ever touch a real one."""
        proc, err = run_child([str(gui), "--self-check"], self.env)
        if err:
            raise RuntimeError(err)
        try:
            report = json.loads(proc.stdout.decode("utf-8").strip())
            state_dir = Path(report["state_dir"]).resolve()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"could not read state_dir from --self-check ({exc}): {tail(proc.stdout)} {tail(proc.stderr)}")
        if not _inside(state_dir, self.dir):
            raise RuntimeError(f"state dir {state_dir} is not under {self.dir}; refusing to continue")
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = state_dir
        return state_dir

    def prepare_old(self) -> "Scenario":
        self.unpack_old()
        self.read_state_dir(self.old_gui)
        return self

    def update_json(self) -> dict:
        try:
            return json.loads((self.state_dir / "update.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def self_check_build(self, gui: Path) -> Optional[str]:
        proc, err = run_child([str(gui), "--self-check"], self.env)
        if err or proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout.decode("utf-8").strip()).get("build_id")
        except ValueError:
            return None

    def apply_env(self, ctx: dict, extra: Optional[dict] = None) -> dict:
        env = dict(self.env)
        env["TOKITTY_UPDATE_API_URL"] = ctx["server_url"]
        env["SSL_CERT_FILE"] = str(ctx["certs"]["ca"])
        env.update(extra or {})
        return env


def _inside(path: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(parent)))
        return True
    except ValueError:
        return False


def scratch_env(scn_dir: Path) -> dict:
    """verify_artifact's scratch environment plus every other per-user
    location, minus any TOKITTY_UPDATE_* or SSL_CERT_FILE the runner had."""
    env = _scratch_env(scn_dir)
    for key in [k for k in env if k.startswith(ENV_PREFIXES)]:
        del env[key]
    if sys.platform == "win32":
        env["APPDATA"] = str(scn_dir / "appdata")
    else:
        env["XDG_DATA_HOME"] = str(scn_dir / "xdg-data")
        env["XDG_CACHE_HOME"] = str(scn_dir / "xdg-cache")
        env["XDG_STATE_HOME"] = str(scn_dir / "xdg-state")
    for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        if key in env and str(env[key]).startswith(str(scn_dir)):
            Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def unpack_archive(archive, dest) -> None:
    """The release job's own extraction tools: zipfile on Windows, ditto on
    macOS (zipfile drops the symlinks and modes in Python.framework), tar on
    Linux."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        return
    argv = ["ditto", "-x", "-k", str(archive), str(dest)] if sys.platform == "darwin" else ["tar", "-xzf", str(archive), "-C", str(dest)]
    proc, err = run_child(argv, dict(os.environ), timeout=300)
    if err or proc.returncode != 0:
        raise RuntimeError(err or f"{argv[0]} exit {proc.returncode}: {tail(proc.stderr)}")


def run_logged(argv, env, timeout: float, log_dir: Path, label: str) -> dict:
    """Run to completion with stdout and stderr in files, not pipes: the update
    leaves the new copy running detached, and a pipe it somehow inherited would
    hold a capture open until the timeout."""
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = log_dir / f"{label}.out", log_dir / f"{label}.err"
    start = time.monotonic()
    timed_out = False
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
            rc = None
    stderr = err_path.read_bytes().decode("utf-8", "replace")
    return {
        "pid": proc.pid,
        "returncode": rc,
        "timed_out": timed_out,
        "elapsed_s": round(time.monotonic() - start, 1),
        "stdout": tail(out_path.read_bytes()),
        # The updater's own messages, kept whole: a runner's toolkit noise
        # (a tray that can't dock under xvfb) must not push them out of the tail.
        "update_messages": [line for line in stderr.splitlines() if line.startswith("tokitty: update")],
        "stderr": tail(stderr, 1500),
    }


def _current_target(state_dir: Path) -> Optional[str]:
    link = state_dir / "current"
    if not os.path.lexists(link):
        return None
    return os.path.normcase(os.path.realpath(link))


def _points_at(state_dir: Path, release_dir: Path, timeout: float = 15) -> bool:
    want = os.path.normcase(os.path.realpath(release_dir))
    return wait_until(lambda: _current_target(state_dir) == want, timeout)


def _use_repo(ctx: dict) -> None:
    root = str(ctx["repo_root"])
    if root not in sys.path:
        sys.path.insert(0, root)


def _fake(ctx: dict, scn: Scenario, *, sums: Optional[str] = None, archive: Optional[Path] = None) -> FakeGitHub:
    server = FakeGitHub(
        ctx["certs"],
        ctx["tag"],
        ctx["asset"],
        archive or ctx["new_archive"],
        ctx["sums"] if sums is None else sums,
    ).start()
    ctx["server_url"] = server.base_url
    return server


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


def step_prepare(ctx):
    _use_repo(ctx)
    from tokitty.updater import parse_version, platform_target

    problems = []
    for key in ("old_archive", "new_archive", "new_sums"):
        if not ctx[key].is_file():
            problems.append(f"--{key.replace('_', '-')} {ctx[key]} is not a file")
    if parse_version(ctx["tag"]) is None:
        problems.append(f"--tag {ctx['tag']!r} is not vMAJOR.MINOR.PATCH")
    elif parse_version(ctx["tag"]) <= parse_version(OLD_TAG):
        problems.append(f"--tag {ctx['tag']} is not newer than the old fixture {OLD_TAG}")
    target = platform_target()
    if target is None:
        problems.append("this platform has no release target")
    if problems:
        return {"ok": False, "detail": "; ".join(problems)}
    asset = asset_file_name(ctx["tag"], target)
    if ctx["new_archive"].name != asset:
        return {"ok": False, "detail": f"--new-archive is named {ctx['new_archive'].name}, the updater would look for {asset}"}
    digest = hashlib.sha256(ctx["new_archive"].read_bytes()).hexdigest()
    sums = ctx["new_sums"].read_text(encoding="utf-8")
    listed = parse_sums_text(sums).get(asset)
    if listed != digest:
        return {"ok": False, "detail": f"--new-sums lists {listed} for {asset}, the archive hashes to {digest}"}
    try:
        ctx["certs"] = make_certificates(ctx["work"] / "tls")
    except ImportError as exc:
        return {"ok": False, "detail": f"the cryptography package is needed to make the throwaway CA: {exc}"}
    ctx.update(target=target, asset=asset, sums=sums, sha256=digest)
    return {"ok": True, "detail": {"target": target, "asset": asset, "sha256": digest, "tag": ctx["tag"], "rehearsal": ctx["rehearsal"]}}


def step_env_dump(ctx):
    scn = Scenario(ctx, "env")
    unpack_archive(ctx["new_archive"], scn.versions if sys.platform == "darwin" else scn.versions / ctx["tag"])
    _use_repo(ctx)
    from tokitty.update_install import child_env

    reports = {}
    for label, env in (("bootloader_only", scn.env), ("with_child_env", child_env(scn.env, sys.platform))):
        proc, err = run_child([str(scn.new_gui), "--self-check"], env)
        if err:
            return {"ok": False, "detail": f"{label}: {err}"}
        try:
            data = json.loads(proc.stdout.decode("utf-8").strip())
        except ValueError:
            return {"ok": False, "detail": f"{label}: no JSON (exit {proc.returncode}): {tail(proc.stdout)} {tail(proc.stderr)}"}
        reports[label] = data
    detail = {label: {"env": r.get("env"), "build_id": r.get("build_id"), "frozen": r.get("frozen")} for label, r in reports.items()}
    bad = [label for label, r in reports.items() if r.get("env") is None or r.get("build_id") != ctx["tag"] or r.get("frozen") is not True]
    if bad:
        return {"ok": False, "detail": {"problem": f"missing env field, wrong build_id or not frozen in: {bad}", **detail}}
    return {"ok": True, "detail": detail}


def step_tls_github(ctx):
    scn = Scenario(ctx, "tls").prepare_old()
    proc, err = run_child([str(scn.old_gui), "--check-for-update"], scn.env, timeout=90)
    if err:
        return {"ok": False, "detail": err}
    try:
        report = json.loads(proc.stdout.decode("utf-8").strip())
    except ValueError:
        return {"ok": False, "detail": f"no JSON (exit {proc.returncode}): {tail(proc.stdout)} {tail(proc.stderr)}"}
    from_github = proc.returncode == 0 and report.get("error") is None and report.get("latest")
    return {"ok": bool(from_github), "detail": {"exit": proc.returncode, "report": report, "stderr": tail(proc.stderr)}}


def _quarantine_problems(scn: Scenario) -> Tuple[List[str], dict]:
    """No Zone.Identifier (Windows) or com.apple.quarantine (macOS) on what the
    updater unpacked. Each probe is first proven able to see a mark by planting
    one on a scratch file."""
    problems, info = [], {}
    paths = [scn.new_gui, scn.new_hook]
    probe = scn.dir / "mark-probe.txt"
    probe.write_text("probe", encoding="utf-8")
    if sys.platform == "win32":
        try:
            with open(str(probe) + ":Zone.Identifier", "w", encoding="utf-8") as f:
                f.write("[ZoneTransfer]\nZoneId=3\n")
            info["instrument_sees_a_mark"] = os.path.exists(str(probe) + ":Zone.Identifier")
        except OSError as exc:
            info["instrument_sees_a_mark"] = f"could not plant a mark: {exc}"
        for path in paths:
            if os.path.exists(str(path) + ":Zone.Identifier"):
                problems.append(f"{path} carries a Zone.Identifier stream")
    elif sys.platform == "darwin":
        planted = subprocess.run(["xattr", "-w", "com.apple.quarantine", "0081;00000000;Test;", str(probe)], capture_output=True)
        seen = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(probe)], capture_output=True)
        info["instrument_sees_a_mark"] = planted.returncode == 0 and seen.returncode == 0
        for path in paths + [scn.new_app_dir / "Tokitty.app"]:
            proc = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(path)], capture_output=True)
            if proc.returncode == 0:
                problems.append(f"{path} carries com.apple.quarantine: {tail(proc.stdout, 200)}")
    else:
        info["instrument_sees_a_mark"] = "not applicable on Linux"
    return problems, info


def step_apply_update(ctx):
    scn = Scenario(ctx, "apply").prepare_old()
    server = _fake(ctx, scn)
    _use_repo(ctx)
    from tokitty.lock import LockAcquisitionError, SingleInstanceLock

    problems: List[str] = []
    detail: dict = {}
    try:
        before = tree_snapshot(scn.versions)
        run = run_logged([str(scn.old_gui), "--apply-update"], scn.apply_env(ctx), APPLY_TIMEOUT, scn.dir / "logs", "apply")
        detail["apply"] = run
        detail["requests"] = list(server.requests)
        detail["server_errors"] = list(server.errors)
        if run["returncode"] != 0:
            return {
                "ok": False,
                "detail": {**detail, "problem": f"--apply-update exited {run['returncode']}, expected 0: {run['update_messages']}"},
            }
        roots = [str(scn.dir)]
        state = scn.update_json()
        leftovers = sorted(p.name for p in scn.state_dir.glob("update-ack-*"))
        # The old copy only reports "installed" after reading and removing the
        # ack, so exit 0 plus no ack file and no pending record is the ack.
        if leftovers:
            problems.append(f"an ack file was left behind: {leftovers}")
        if state.get("pending"):
            problems.append(f"update.json still has a pending handover: {state['pending']}")
        if not scn.new_gui.is_file():
            problems.append(f"the new copy is missing: {scn.new_gui}")
        procs = list_gui_processes(roots)
        detail["processes"] = procs
        problems += single_process_problems(procs, scn.new_gui, run["pid"])
        if not _points_at(scn.state_dir, scn.new_gui.parent):
            problems.append(f"current is {_current_target(scn.state_dir)}, expected {scn.new_gui.parent}")
        build = scn.self_check_build(scn.new_gui)
        detail["new_build_id"] = build
        if build != ctx["tag"]:
            problems.append(f"the new copy's --self-check reports {build}, expected {ctx['tag']}")
        quarantine, info = _quarantine_problems(scn)
        detail["quarantine"] = info
        problems += quarantine
        owned = state.get("owned") or []
        detail["owned"] = owned
        if sys.platform == "darwin":
            backup = scn.versions / f".Tokitty-{OLD_TAG}.app"
            if not backup.is_dir():
                problems.append(f"the backup {backup} is missing")
            elif not any(e.get("path") == str(backup) and e.get("kind") == "backup" for e in owned):
                problems.append(f"{backup} is not recorded as an owned backup")
            else:
                old_build = scn.self_check_build(backup / "Contents" / "MacOS" / "Tokitty")
                if old_build != OLD_TAG:
                    problems.append(f"the backup reports build {old_build}, expected {OLD_TAG}")
            if not (scn.versions / "Tokitty.app").is_dir():
                problems.append("Tokitty.app is missing")
        else:
            if not any(e.get("path") == str(scn.new_app_dir) and e.get("kind") == "copy" for e in owned):
                problems.append(f"{scn.new_app_dir} is not recorded as an owned copy")
            if not scn.old_gui.is_file():
                problems.append("the old copy was removed")
        # The lock: the new copy must hold it right now, and must be what
        # held it (the same probe succeeds once nothing is running).
        probe = SingleInstanceLock(scn.state_dir)
        try:
            probe.acquire()
        except LockAcquisitionError:
            detail["lock_held_by_new_copy"] = True
        else:
            probe.release()
            detail["lock_held_by_new_copy"] = False
            problems.append("tokitty.lock could be taken while the new copy should be running")
        detail["tree_changes"] = snapshot_diff(before, tree_snapshot(scn.versions))
    finally:
        stop_tokitty(scn.dir)
        server.stop()
    # The instrument check: with the new copy stopped the same probe succeeds.
    probe = SingleInstanceLock(scn.state_dir)
    try:
        probe.acquire()
        probe.release()
    except LockAcquisitionError:
        problems.append("tokitty.lock is still held after the new copy was stopped, so the lock probe proves nothing")
    if problems:
        detail["problems"] = problems
    return {"ok": not problems, "detail": detail}


def step_no_ack_rollback(ctx):
    scn = Scenario(ctx, "noack").prepare_old()
    server = _fake(ctx, scn)
    problems: List[str] = []
    detail: dict = {}
    try:
        run = run_logged(
            [str(scn.old_gui), "--apply-update"],
            scn.apply_env(ctx, {"TOKITTY_UPDATE_TEST_NO_ACK": "1"}),
            APPLY_TIMEOUT,
            scn.dir / "logs",
            "apply",
        )
        detail["apply"] = run
        detail["requests"] = list(server.requests)
        if run["returncode"] != 2:
            problems.append(f"--apply-update exited {run['returncode']}, expected 2 (rolled back)")
        if run["elapsed_s"] >= NO_ACK_LIMIT_S:
            problems.append(f"it took {run['elapsed_s']} s; the wait should abort once the new copy exits, well inside 120 s")
        if not any("didn't start" in line for line in run["update_messages"]):
            problems.append("stderr does not say the new copy didn't start")
        roots = [str(scn.dir)]
        if not wait_until(lambda: not list_gui_processes(roots), 15):
            problems.append(f"Tokitty processes are still running: {list_gui_processes(roots)}")
        if not _points_at(scn.state_dir, scn.old_gui.parent, 5):
            problems.append(f"current is {_current_target(scn.state_dir)}, expected the old copy {scn.old_gui.parent}")
        if scn.update_json().get("pending"):
            problems.append("update.json still has a pending handover")
        if sys.platform == "darwin":
            build = scn.self_check_build(scn.versions / "Tokitty.app" / "Contents" / "MacOS" / "Tokitty")
            detail["restored_build_id"] = build
            if build != OLD_TAG:
                problems.append(f"Tokitty.app reports build {build} after the rollback, expected {OLD_TAG}")
            detail["apps"] = sorted(p.name for p in scn.versions.iterdir())
    finally:
        stop_tokitty(scn.dir)
        server.stop()
    if problems:
        detail["problems"] = problems
    return {"ok": not problems, "detail": detail}


def _refused_update(ctx, name: str, *, sums: Optional[str], archive: Optional[Path], expect: str):
    scn = Scenario(ctx, name).prepare_old()
    server = _fake(ctx, scn, sums=sums, archive=archive)
    problems: List[str] = []
    detail: dict = {}
    try:
        before = tree_snapshot(scn.versions)
        run = run_logged([str(scn.old_gui), "--apply-update"], scn.apply_env(ctx), APPLY_TIMEOUT, scn.dir / "logs", "apply")
        detail["apply"] = run
        detail["requests"] = list(server.requests)
        if run["returncode"] != 1:
            problems.append(f"--apply-update exited {run['returncode']}, expected 1")
        if not any(expect in line for line in run["update_messages"]):
            problems.append(f"the updater's messages do not mention {expect!r}: {run['update_messages']}")
        changes = snapshot_diff(before, tree_snapshot(scn.versions))
        if changes:
            problems.append(f"the versions dir changed: {changes}")
        if scn.update_json().get("owned"):
            problems.append(f"update.json still records owned paths: {scn.update_json().get('owned')}")
    finally:
        stop_tokitty(scn.dir)
        server.stop()
    if problems:
        detail["problems"] = problems
    return {"ok": not problems, "detail": detail}


def step_bad_checksum(ctx):
    wrong = sums_text(ctx["asset"], "0" * 64)
    return _refused_update(ctx, "badsum", sums=wrong, archive=None, expect="SHA256SUMS")


def step_bad_layout(ctx):
    archive = ctx["work"] / "bad-layout" / ctx["asset"]
    archive.parent.mkdir(parents=True, exist_ok=True)
    make_bad_layout_archive(archive, sys.platform)
    sums = sums_text(ctx["asset"], hashlib.sha256(archive.read_bytes()).hexdigest())
    return _refused_update(ctx, "badlay", sums=sums, archive=archive, expect="should hold only")


def _make_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"mklink /J failed: {tail(proc.stdout)} {tail(proc.stderr)}")
    else:
        os.symlink(target, link)


def step_cleanup(ctx):
    scn = Scenario(ctx, "clean")
    unpack_archive(ctx["new_archive"], scn.versions if sys.platform == "darwin" else scn.versions / ctx["tag"])
    scn.read_state_dir(scn.new_gui)
    seed = cleanup_seed(sys.platform, scn.versions, ctx["tag"])
    for directory in seed["dirs"]:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "marker.txt").write_text("seed", encoding="utf-8")
    link_target = seed["links"][0][1]
    link_target.mkdir(parents=True, exist_ok=True)
    (link_target / "sentinel.txt").write_text("must survive", encoding="utf-8")
    for link, target in seed["links"]:
        _make_link(link, target)
    state = {"last_checked": None, "latest_tag": None, "notified_tag": None, "owned": seed["owned"], "pending": None}
    (scn.state_dir / "update.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    kept = seed["kept"] + [scn.new_app_dir if sys.platform != "darwin" else scn.versions / "Tokitty.app"]
    detail: dict = {}
    problems: List[str] = []
    out = open(scn.dir / "launch.log", "wb")
    try:
        proc = subprocess.Popen([str(scn.new_gui)], env=scn.env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        detail["pid"] = proc.pid

        def finished() -> bool:
            gone_paths = [str(p) for p in seed["gone"]]
            owned_now = [e.get("path") for e in scn.update_json().get("owned") or []]
            return not any(os.path.lexists(p) for p in seed["gone"]) and not any(p in owned_now for p in gone_paths)

        detail["cleanup_finished"] = wait_until(finished, CLEANUP_TIMEOUT)
        time.sleep(2)  # a wrong deletion would have happened in the same pass
        problems += survivor_problems(kept, seed["gone"])
        detail["owned_after"] = scn.update_json().get("owned")
        if proc.poll() is not None:
            problems.append(f"the new copy exited on its own with {proc.returncode}")
    finally:
        stop_tokitty(scn.dir)
        out.close()
    if not detail.get("cleanup_finished"):
        problems.append(f"cleanup had not finished after {CLEANUP_TIMEOUT} s")
    if problems:
        detail["problems"] = problems
        detail["log"] = tail((scn.dir / "launch.log").read_bytes())
    return {"ok": not problems, "detail": detail}


def step_rename_swap(ctx):
    if sys.platform != "darwin":
        return {"ok": True, "detail": "skipped: macOS only"}
    _use_repo(ctx)
    from tokitty.update_swap import swap_bundles

    base = ctx["work"] / "swap"
    a, b = base / "a", base / "b"
    for directory, name in ((a, "from-a.txt"), (b, "from-b.txt")):
        directory.mkdir(parents=True)
        (directory / name).write_text(name, encoding="utf-8")
    swap_bundles(a, b)
    swapped = (a / "from-b.txt").is_file() and (b / "from-a.txt").is_file() and not (a / "from-a.txt").exists()
    return {"ok": swapped, "detail": {"a": sorted(p.name for p in a.iterdir()), "b": sorted(p.name for p in b.iterdir())}}


STEPS = [
    ("prepare", step_prepare),
    ("env_dump", step_env_dump),
    ("tls_github", step_tls_github),
    ("apply_update", step_apply_update),
    ("no_ack_rollback", step_no_ack_rollback),
    ("bad_checksum", step_bad_checksum),
    ("bad_layout", step_bad_layout),
    ("cleanup", step_cleanup),
    ("rename_swap", step_rename_swap),
]


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-archive", required=True)
    parser.add_argument("--new-archive", required=True)
    parser.add_argument("--new-sums", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--rehearsal", action="store_true", help="the new side is a v0.0.2 rebuild, not the release being published")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--only", default=None, help="comma-separated step names to run after prepare (debugging)")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parent.parent
    work_arg = Path(args.work)
    report = {
        "tag": args.tag,
        "rehearsal": args.rehearsal,
        "platform": sys.platform,
        "work": str(work_arg),
        "repo_root": str(repo_root),
        "steps": {},
    }
    unusable = _work_dir_unusable_reason(work_arg)
    if unusable:
        print(f"[verify_update] refusing to run: {unusable}", file=sys.stderr)
        report.update(ok=False, error=unusable)
        write_report(report_path, report)
        return 1

    work = work_arg.resolve()
    work.mkdir(parents=True, exist_ok=True)
    ctx = {
        "work": work,
        "repo_root": repo_root,
        "old_archive": Path(args.old_archive).resolve(),
        "new_archive": Path(args.new_archive).resolve(),
        "new_sums": Path(args.new_sums).resolve(),
        "tag": args.tag,
        "rehearsal": args.rehearsal,
    }
    only = set(args.only.split(",")) if args.only else None
    for name, fn in STEPS:
        if name != "prepare" and only is not None and name not in only:
            continue
        print(f"[verify_update] running step: {name}", file=sys.stderr)
        try:
            result = fn(ctx)
        except Exception as exc:  # noqa: BLE001 - a step's own bug must still fail closed
            result = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        report["steps"][name] = result
        print(f"[verify_update] step {name}: {'ok' if result.get('ok') else 'FAILED'}", file=sys.stderr)
        if name == "prepare" and not result.get("ok"):
            break

    report["ok"] = bool(report["steps"]) and all(s.get("ok") for s in report["steps"].values())
    write_report(report_path, report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
