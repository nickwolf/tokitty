import json
import pathlib
import sys

from tokitty.accounts import canonicalize_locator
from tokitty.manual_path import validate_manual_path


def _oauth_json():
    return json.dumps({"claudeAiOauth": {"accessToken": "x", "expiresAt": 0}})


def test_relative_path_rejected():
    result = validate_manual_path("relative/.claude", active_config_dirs=[])
    assert not result.ok
    assert "absolute" in result.error.lower()


def test_unexpanded_tilde_is_expanded(tmp_path, monkeypatch):
    # os.path.expanduser checks USERPROFILE before HOME on Windows, so
    # only setting HOME there leaves it silently expanding against the
    # real user profile instead of this fixture.
    home_var = "USERPROFILE" if sys.platform == "win32" else "HOME"
    monkeypatch.setenv(home_var, str(tmp_path))
    (tmp_path / ".claude-work").mkdir()
    (tmp_path / ".claude-work" / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")
    result = validate_manual_path("~/.claude-work", active_config_dirs=[])
    assert result.ok
    assert result.config_dir == str(tmp_path / ".claude-work")


def test_missing_credentials_file_rejected(tmp_path):
    (tmp_path / ".claude").mkdir()
    result = validate_manual_path(str(tmp_path / ".claude"), active_config_dirs=[])
    assert not result.ok
    assert ".credentials.json" in result.error


def test_credentials_file_path_itself_is_accepted_via_parent(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    creds = config_dir / ".credentials.json"
    creds.write_text(_oauth_json(), encoding="utf-8")
    result = validate_manual_path(str(creds), active_config_dirs=[])
    assert result.ok
    assert result.config_dir == str(config_dir)


def test_credentials_file_without_oauth_shape_rejected(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text("{}", encoding="utf-8")
    result = validate_manual_path(str(config_dir), active_config_dirs=[])
    assert not result.ok


def _fake_run(cmd, **kwargs):
    class R:
        stdout = _oauth_json().encode("utf-8")
        returncode = 0
    return R()


def test_wsl_dollar_and_localhost_aliases_are_equivalent():
    a = validate_manual_path("\\\\wsl$\\Ubuntu\\home\\nick\\.claude", active_config_dirs=[], run=_fake_run)
    b = validate_manual_path("\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude", active_config_dirs=[], run=_fake_run)
    assert a.ok and b.ok
    # Both alias forms of the same real directory must canonicalize to
    # the same locator -- validate_manual_path returns the raw candidate
    # (not a canonicalized form), so this is the assertion that would
    # actually catch a regression in alias-folding.
    assert canonicalize_locator(a.config_dir) == canonicalize_locator(b.config_dir)


def test_wsl_localhost_rejected_as_duplicate_of_active_wsl_dollar_same_dir():
    # The global constraint requires both alias forms of the same real
    # directory to be treated as duplicates of each other. This exercises
    # the actual duplicate-rejection path across aliases, not just
    # same-string-twice.
    active = ["\\\\wsl$\\Ubuntu\\home\\nick\\.claude"]
    result = validate_manual_path(
        "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude", active_config_dirs=active, run=_fake_run
    )
    assert not result.ok
    assert "already added" in result.error.lower()


def test_wsl_unc_with_credentials_filename_routes_through_wsl_branch():
    # Regression guard for the hazard this task exists to prevent: a WSL
    # UNC path with a trailing "\.credentials.json" must still be routed
    # through the distro-aware subprocess reader, not fall through to the
    # local branch and touch the UNC path directly. _strip_credentials_filename
    # normalizes to forward slashes before checking the suffix, and
    # parse_wsl_unc is separator-direction-agnostic (it re-normalizes
    # "/" -> "\\" internally), so this must still resolve as WSL.
    calls = []

    def tracking_run(cmd, **kwargs):
        calls.append(cmd)
        return _fake_run(cmd, **kwargs)

    result = validate_manual_path(
        "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude\\.credentials.json",
        active_config_dirs=[],
        run=tracking_run,
    )
    assert result.ok
    assert calls, "expected the injected run() to be invoked via the WSL branch"


class _WindowsStyleAbsolutePath(pathlib.PosixPath):
    """Test double: real POSIX I/O (so it can touch tmp_path on this Linux
    test runner), but `is_absolute()` uses real Windows pathlib semantics
    (PureWindowsPath), which is False for a leading-slash path with no
    drive letter -- reproducing the bug's platform-specific trigger, which
    cannot occur naturally on Linux since PosixPath already treats a
    leading "/" as absolute."""

    def is_absolute(self):
        return pathlib.PureWindowsPath(str(self)).is_absolute()


def test_posix_shaped_path_without_drive_letter_is_treated_as_windows_local_absolute(tmp_path, monkeypatch):
    monkeypatch.setattr("tokitty.manual_path.Path", _WindowsStyleAbsolutePath)
    config_dir = tmp_path / ".claude-work"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")

    if sys.platform != "win32":
        # Sanity-check the premise: this exact path shape is NOT absolute
        # under real Windows pathlib semantics, which is why a bare
        # `path.is_absolute()` check alone rejected it. On real Windows
        # tmp_path is always drive-lettered, so this premise doesn't
        # apply the same way there; the monkeypatched Path below is what
        # actually exercises the fixed code path on every platform.
        assert not pathlib.PureWindowsPath(str(config_dir)).is_absolute()

    result = validate_manual_path(str(config_dir), active_config_dirs=[])
    assert result.ok
    assert result.config_dir == str(config_dir)


def test_duplicate_of_active_account_rejected(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")
    result = validate_manual_path(str(config_dir), active_config_dirs=[str(config_dir)])
    assert not result.ok
    assert "already added" in result.error.lower()
