import hashlib
import io
import json
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokitty.update_install import (
    MAC_TOP,
    UpdateCancelled,
    UpdateInstallError,
    binary_paths,
    child_env,
    download,
    install_target,
    parse_sums,
    probe_writable,
    run_self_check,
    stage,
    top_name,
    validate_layout,
)
from tokitty.updater import Release, load_update_state

TAG = "v0.3.0"
BASE = "https://example.invalid/v0.3.0"


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _build_tree(parent, sys_platform, *, hook=True):
    """A fake unpacked release: parent/<top>/... with both executables."""
    top = parent / top_name(sys_platform)
    gui, hook_path = binary_paths(top, sys_platform)
    _touch(gui)
    if hook:
        _touch(hook_path)
    return top


class FakeResponse(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _urlopen(files, redirects=None, on_open=None):
    """files maps URL to bytes; redirects maps URL to the URL it ends up at."""

    def urlopen(request, timeout=None):
        url = request.full_url
        if on_open:
            on_open(url)
        return FakeResponse(files[url], (redirects or {}).get(url, url))

    return urlopen


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _release(name, body, *, size=None):
    return Release(
        tag=TAG,
        html_url=None,
        asset_url=f"{BASE}/{name}",
        asset_size=len(body) if size is None else size,
        sums_url=f"{BASE}/SHA256SUMS",
    )


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _tar_bytes(entries, symlinks=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
        for name, link in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = link
            tf.addfile(info)
    return buf.getvalue()


def _setup(tmp_path, sys_platform, body, *, name="asset", sums_line=None):
    versions = tmp_path / "versions"
    versions.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    if sys_platform == "darwin":
        exe = versions / MAC_TOP / "Contents" / "MacOS" / "Tokitty"
    else:
        exe = versions / "v0.2.0" / top_name(sys_platform) / "x"
    target = install_target(exe, sys_platform)
    sums = (sums_line or f"{_sha(body)}  {name}") + "\n"
    files = {f"{BASE}/{name}": body, f"{BASE}/SHA256SUMS": sums.encode()}
    return versions, state_dir, target, files, _release(name, body)


def _snapshot(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


# --- install_target -------------------------------------------------------


def test_target_beside_a_versioned_release_folder(tmp_path):
    exe = _touch(tmp_path / "releases" / "v0.2.1" / "Tokitty" / "Tokitty.exe")
    target = install_target(exe, "win32")
    assert target.refusal is None
    assert target.parent == tmp_path / "releases"
    assert target.final_path("v0.3.0") == tmp_path / "releases" / "v0.3.0" / "Tokitty"


def test_target_for_a_hand_unpacked_folder_uses_its_parent(tmp_path):
    exe = _touch(tmp_path / "Downloads" / "Tokitty" / "Tokitty.exe")
    target = install_target(exe, "win32")
    assert target.parent == tmp_path / "Downloads"
    assert target.final_path("v0.3.0") == tmp_path / "Downloads" / "v0.3.0" / "Tokitty"


def test_target_resolves_a_launch_through_current(tmp_path):
    exe = _touch(tmp_path / "releases" / "v0.2.1" / "tokitty" / "tokitty")
    state = tmp_path / "state"
    state.mkdir()
    (state / "current").symlink_to(exe.parent)
    target = install_target(state / "current" / "tokitty", "linux")
    assert target.parent == tmp_path / "releases"
    assert target.top == "tokitty"
    assert target.final_path("v0.3.0") == tmp_path / "releases" / "v0.3.0" / "tokitty"


def test_target_on_macos_is_the_containing_app(tmp_path):
    exe = _touch(tmp_path / "Apps" / "Tokitty.app" / "Contents" / "MacOS" / "Tokitty")
    target = install_target(exe, "darwin")
    assert target.refusal is None
    assert target.final_path("v0.3.0") == tmp_path / "Apps" / "Tokitty.app"
    assert target.parent == tmp_path / "Apps"


def test_target_refuses_a_rollback_named_bundle_and_a_bare_executable(tmp_path):
    renamed = _touch(tmp_path / ".Tokitty-v0.2.0.app" / "Contents" / "MacOS" / "Tokitty")
    assert ".Tokitty-v0.2.0.app" in install_target(renamed, "darwin").refusal
    bare = _touch(tmp_path / "bin" / "Tokitty")
    assert install_target(bare, "darwin").refusal
    assert install_target(bare, "freebsd14").refusal


# --- probe_writable, child_env, parse_sums --------------------------------


def test_probe_writable_leaves_no_file_and_rejects_a_missing_dir(tmp_path):
    assert probe_writable(tmp_path) is True
    assert list(tmp_path.iterdir()) == []
    assert probe_writable(tmp_path / "missing") is False


def test_child_env_restores_the_original_library_path_on_linux():
    base = {"LD_LIBRARY_PATH": "/bundle/lib", "LD_LIBRARY_PATH_ORIG": "/usr/lib/x", "KEEP": "1"}
    env = child_env(base, "linux")
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/x"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["KEEP"] == "1"
    assert "TOKITTY_SELF_CHECK_TK" not in env
    assert base["LD_LIBRARY_PATH"] == "/bundle/lib"


def test_child_env_drops_the_library_path_when_there_was_no_original():
    assert "LD_LIBRARY_PATH" not in child_env({"LD_LIBRARY_PATH": "/bundle/lib"}, "linux")


def test_child_env_leaves_other_platforms_alone_and_flags_the_self_check():
    base = {"LD_LIBRARY_PATH": "/x", "DYLD_LIBRARY_PATH": "/y"}
    env = child_env(base, "darwin", self_check=True)
    assert env == {**base, "PYINSTALLER_RESET_ENVIRONMENT": "1", "TOKITTY_SELF_CHECK_TK": "1"}


def test_parse_sums_accepts_the_binary_marker_and_skips_junk():
    a, b = "a" * 64, "B" * 64
    text = f"{a}  one.zip\n{b} *two.tar.gz\nnot a line\n\n{a[:10]}  short\n"
    assert parse_sums(text) == {"one.zip": a, "two.tar.gz": "b" * 64}


# --- download -------------------------------------------------------------


def _download(tmp_path, body, *, url=f"{BASE}/a.zip", size=None, sha=None, cancelled=lambda: False, **kw):
    seen = []
    dest = tmp_path / "a.zip"
    download(
        url,
        dest,
        expected_size=len(body) if size is None else size,
        expected_sha256=_sha(body) if sha is None else sha,
        progress=lambda done, total: seen.append((done, total)),
        cancelled=cancelled,
        urlopen=_urlopen({url: body}, **kw),
    )
    return dest, seen


def test_download_streams_hashes_and_reports_progress(tmp_path):
    body = os.urandom(150_000)
    dest, seen = _download(tmp_path, body)
    assert dest.read_bytes() == body
    assert seen[-1] == (len(body), len(body)) and len(seen) == 3


def test_download_rejects_a_wrong_size_and_removes_the_file(tmp_path):
    with pytest.raises(UpdateInstallError, match="bytes"):
        _download(tmp_path, b"abcdef", size=7)
    with pytest.raises(UpdateInstallError, match="larger"):
        _download(tmp_path, b"abcdef", size=3)
    assert not (tmp_path / "a.zip").exists()


def test_download_rejects_a_wrong_hash(tmp_path):
    with pytest.raises(UpdateInstallError, match="SHA256SUMS"):
        _download(tmp_path, b"abcdef", sha=_sha(b"other"))
    assert not (tmp_path / "a.zip").exists()


def test_download_cancel_stops_midway(tmp_path):
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(UpdateCancelled):
        _download(tmp_path, os.urandom(200_000), cancelled=cancelled)
    assert not (tmp_path / "a.zip").exists()


def test_download_refuses_plain_http_and_a_redirect_to_it(tmp_path):
    with pytest.raises(UpdateInstallError, match="non-HTTPS"):
        _download(tmp_path, b"abc", url="http://example.invalid/a.zip")
    with pytest.raises(UpdateInstallError, match="redirect"):
        _download(tmp_path, b"abc", redirects={f"{BASE}/a.zip": "http://example.invalid/a.zip"})


# --- validate_layout ------------------------------------------------------


@pytest.mark.parametrize("sys_platform", ["win32", "darwin", "linux"])
def test_validate_layout_accepts_a_good_tree(tmp_path, sys_platform):
    top = _build_tree(tmp_path, sys_platform)
    assert validate_layout(tmp_path, sys_platform) == top


@pytest.mark.parametrize("sys_platform", ["win32", "darwin", "linux"])
def test_validate_layout_rejects_a_missing_hook(tmp_path, sys_platform):
    _build_tree(tmp_path, sys_platform, hook=False)
    with pytest.raises(UpdateInstallError, match="missing"):
        validate_layout(tmp_path, sys_platform)


def test_validate_layout_rejects_extra_and_misnamed_top_entries(tmp_path):
    _build_tree(tmp_path, "linux")
    (tmp_path / "extra").mkdir()
    with pytest.raises(UpdateInstallError, match="only tokitty"):
        validate_layout(tmp_path, "linux")
    other = tmp_path / "other"
    _build_tree(other, "linux")
    (other / "tokitty").rename(other / "Tokitty")
    with pytest.raises(UpdateInstallError, match="only tokitty"):
        validate_layout(other, "linux")


def test_validate_layout_rejects_any_link_on_windows(tmp_path):
    top = _build_tree(tmp_path, "win32")
    (top / "alias").symlink_to(top / "Tokitty.exe")
    with pytest.raises(UpdateInstallError, match="link"):
        validate_layout(tmp_path, "win32")


def test_validate_layout_posix_allows_inner_links_but_not_outside_ones(tmp_path):
    top = _build_tree(tmp_path, "linux")
    (top / "inner").symlink_to("tokitty")
    assert validate_layout(tmp_path, "linux") == top
    (top / "escape").symlink_to("/etc")
    with pytest.raises(UpdateInstallError, match="leaves the app"):
        validate_layout(tmp_path, "linux")


# --- stage ----------------------------------------------------------------


def _good_zip():
    return _zip_bytes({"Tokitty/Tokitty.exe": b"gui", "Tokitty/tokitty-hook.exe": b"hook", "Tokitty/_internal/a": b"a"})


def _good_tar():
    return _tar_bytes({"tokitty/tokitty": b"gui", "tokitty/tokitty-hook": b"hook"}, {"tokitty/alias": "tokitty"})


def _fake_ditto(tree_files):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        dest = Path(argv[-1])
        for rel in tree_files:
            _touch(dest / rel)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    run.calls = calls
    return run


def test_stage_unpacks_a_good_zip_on_windows(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "win32", _good_zip(), name="a.zip")
    staged = stage(release, target, state_dir, sys_platform="win32", urlopen=_urlopen(files), pid=7)
    assert staged.staging == versions / ".tokitty-update-v0.3.0-7"
    assert staged.gui == staged.staging / "unpacked" / "Tokitty" / "Tokitty.exe"
    assert staged.gui.read_bytes() == b"gui"
    assert load_update_state(state_dir).owned == [{"path": str(staged.staging), "version": TAG, "kind": "staging"}]


def test_stage_unpacks_a_good_tar_on_linux_and_keeps_modes_and_links(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "linux", _good_tar(), name="a.tar.gz")
    staged = stage(release, target, state_dir, sys_platform="linux", urlopen=_urlopen(files))
    assert os.access(staged.gui, os.X_OK)
    assert os.path.islink(staged.top / "alias")


def test_stage_unpacks_through_the_injected_ditto_on_macos(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "darwin", b"zipbytes", name="a.zip")
    run = _fake_ditto(
        ["Tokitty.app/Contents/MacOS/Tokitty", "Tokitty.app/Contents/MacOS/tokitty-hook"],
    )
    staged = stage(release, target, state_dir, sys_platform="darwin", urlopen=_urlopen(files), run=run)
    argv, kwargs = run.calls[0]
    assert argv[:3] == ["ditto", "-x", "-k"] and argv[-1] == str(staged.staging / "unpacked")
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert staged.gui == staged.top / "Contents" / "MacOS" / "Tokitty"


def test_stage_records_the_staging_folder_before_creating_it(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "linux", _good_tar(), name="a.tar.gz")
    seen = []

    def on_open(url):
        state = load_update_state(state_dir)
        seen.append(([e["kind"] for e in state.owned], sorted(p.name for p in versions.iterdir())))

    stage(release, target, state_dir, sys_platform="linux", urlopen=_urlopen(files, on_open=on_open))
    assert seen[0] == (["staging"], [".tokitty-update-v0.3.0-" + str(os.getpid())])


@pytest.mark.parametrize(
    ("sys_platform", "body", "message"),
    [
        ("win32", _zip_bytes({"../escape": b"x", "Tokitty/Tokitty.exe": b"g"}), "unsafe"),
        ("win32", _zip_bytes({"Tokitty/Tokitty.exe": b"g", "Tokitty/tokitty-hook.exe": b"h", "Other/a": b"a"}), "only"),
        ("win32", _zip_bytes({"Tokitty/Tokitty.exe": b"g"}), "missing"),
        ("linux", _tar_bytes({"tokitty/tokitty": b"g", "tokitty/tokitty-hook": b"h"}, {"tokitty/x": "/etc/passwd"}), "unpack"),
        ("linux", _tar_bytes({"tokitty/tokitty": b"g"}), "missing"),
        ("linux", _tar_bytes({"tokitty/tokitty": b"g", "tokitty/tokitty-hook": b"h"}, {"tokitty/x": "../../out"}), "unpack"),
    ],
)
def test_stage_failures_leave_the_install_untouched(tmp_path, sys_platform, body, message):
    versions, state_dir, target, files, release = _setup(tmp_path, sys_platform, body, name="asset")
    (versions / "v0.2.0").mkdir()
    before = _snapshot(versions)
    with pytest.raises(UpdateInstallError, match=message):
        stage(release, target, state_dir, sys_platform=sys_platform, urlopen=_urlopen(files))
    assert _snapshot(versions) == before
    assert load_update_state(state_dir).owned == []
    assert not (tmp_path / "escape").exists() and not (versions / "escape").exists()


def test_stage_rejects_a_wrong_checksum_line(tmp_path):
    body = _good_tar()
    versions, state_dir, target, files, release = _setup(tmp_path, "linux", body, sums_line=f"{'0' * 64}  asset")
    with pytest.raises(UpdateInstallError, match="SHA256SUMS"):
        stage(release, target, state_dir, sys_platform="linux", urlopen=_urlopen(files))
    assert _snapshot(versions) == []
    assert load_update_state(state_dir).owned == []


def test_stage_needs_a_sums_line_for_the_asset(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "linux", _good_tar(), sums_line=f"{'0' * 64}  other")
    with pytest.raises(UpdateInstallError, match="no line"):
        stage(release, target, state_dir, sys_platform="linux", urlopen=_urlopen(files))
    assert _snapshot(versions) == []


def test_stage_cancel_removes_the_staging_folder(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "linux", _good_tar())
    with pytest.raises(UpdateCancelled):
        stage(release, target, state_dir, sys_platform="linux", urlopen=_urlopen(files), cancelled=lambda: True)
    assert _snapshot(versions) == []
    assert load_update_state(state_dir).owned == []


def test_stage_refuses_a_target_refusal_and_an_uninstallable_release(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "linux", _good_tar())
    refused = type(target)(target.parent, target.top, "darwin", "no")
    with pytest.raises(UpdateInstallError, match="no"):
        stage(release, refused, state_dir, sys_platform="linux", urlopen=_urlopen(files))
    bare = Release(tag=TAG, html_url=None, asset_url=None, asset_size=None, sums_url=None)
    with pytest.raises(UpdateInstallError, match="no installable"):
        stage(bare, target, state_dir, sys_platform="linux", urlopen=_urlopen(files))
    assert _snapshot(versions) == [] and not (state_dir / "update.json").exists()


def test_stage_runs_verify_and_cleans_up_when_it_fails(tmp_path):
    versions, state_dir, target, files, release = _setup(tmp_path, "linux", _good_tar())
    seen = []

    def verify(gui):
        seen.append(gui)
        raise UpdateInstallError("no good")

    with pytest.raises(UpdateInstallError, match="no good"):
        stage(release, target, state_dir, sys_platform="linux", urlopen=_urlopen(files), verify=verify)
    assert seen and seen[0].name == "tokitty"
    assert _snapshot(versions) == []
    assert load_update_state(state_dir).owned == []


# --- run_self_check -------------------------------------------------------


def _report(**overrides):
    report = {
        "checks": {"tkinter": {"ok": True, "detail": "9"}, "tk_root": {"ok": True, "detail": ""}},
        "frozen": True,
        "build_id": TAG,
    }
    report.update(overrides)
    return report


def _runner(report=None, *, stdout=None, raises=None):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if raises:
            raise raises
        out = stdout if stdout is not None else json.dumps(report).encode()
        return SimpleNamespace(returncode=0, stdout=out, stderr=b"")

    run.calls = calls
    return run


def test_self_check_passes_and_uses_a_clean_env_and_timeout(tmp_path):
    run = _runner(_report())
    run_self_check(tmp_path / "tokitty", TAG, runner=run, sys_platform="linux")
    argv, kwargs = run.calls[0]
    assert argv == [str(tmp_path / "tokitty"), "--self-check"]
    assert kwargs["timeout"] == 60
    assert kwargs["env"]["TOKITTY_SELF_CHECK_TK"] == "1"
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (_runner(_report(build_id="v0.0.1")), "not v0.3.0"),
        (_runner(_report(build_id=None)), "not v0.3.0"),
        (_runner(_report(checks={"tk_root": {"ok": False, "detail": "no display"}})), "tk_root"),
        (_runner(_report(checks={"tkinter": {"ok": True}})), "incomplete"),
        (_runner(_report(checks={})), "incomplete"),
        (_runner(stdout=b"garbage"), "no report"),
        (_runner(raises=subprocess.TimeoutExpired("x", 60)), "longer than 60"),
        (_runner(raises=PermissionError("denied")), "could not run"),
    ],
)
def test_self_check_failures(tmp_path, runner, message):
    with pytest.raises(UpdateInstallError, match=message):
        run_self_check(tmp_path / "tokitty", TAG, runner=runner, sys_platform="linux")
