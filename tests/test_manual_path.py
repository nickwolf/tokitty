import json
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


def test_posix_shaped_path_without_drive_letter_is_treated_as_windows_local_absolute():
    """A POSIX-style path with no drive letter must not be rejected by
    the absoluteness check, even though real Windows pathlib.is_absolute()
    returns False for it (no drive letter means Windows treats it as
    drive-relative, not absolute) -- only \\wsl$\\ / \\wsl.localhost\\ UNC
    forms are recognized as WSL, so this exact shape has to fall through
    to local validation instead of being rejected outright.

    The directory doesn't need to actually exist to prove this: what
    matters is which error comes back. "Not an absolute path" would mean
    the absoluteness gate rejected the input; "No .credentials.json
    found" means it passed the gate and failed at the next, unrelated
    check instead, which is what should happen here on every platform.
    A real directory would also work on Linux/macOS (where this shape is
    already absolute) but not reliably on Windows, where resolving a
    driveless path for real file I/O depends on which drive the current
    working directory happens to be on."""
    result = validate_manual_path("/home/nick/.claude-work-does-not-exist", active_config_dirs=[])
    assert not result.ok
    assert "not an absolute path" not in result.error.lower()
    assert "No .credentials.json found" in result.error


def test_duplicate_of_active_account_rejected(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")
    result = validate_manual_path(str(config_dir), active_config_dirs=[str(config_dir)])
    assert not result.ok
    assert "already added" in result.error.lower()
