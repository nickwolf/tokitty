import json
import sys

from tokitty.accounts import canonicalize_locator
from tokitty.manual_path import looks_like_codex_home, validate_codex_path, validate_manual_path


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


def test_transcripts_without_credentials_are_accepted(tmp_path):
    """The case the old validator rejected outright, and the whole reason
    an API-key user could not add their account by hand."""
    (tmp_path / "projects").mkdir()
    result = validate_manual_path(str(tmp_path), [])
    assert result.ok
    assert result.capabilities == frozenset({"models"})
    assert not result.supports("limits")


def test_credentials_and_transcripts_grant_both(tmp_path):
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t"}}), encoding="utf-8"
    )
    (tmp_path / "projects").mkdir()
    result = validate_manual_path(str(tmp_path), [])
    assert result.capabilities == frozenset({"limits", "models"})


def test_credentials_without_transcripts_grant_limits_only(tmp_path):
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t"}}), encoding="utf-8"
    )
    result = validate_manual_path(str(tmp_path), [])
    assert result.capabilities == frozenset({"limits"})


def test_neither_is_still_rejected(tmp_path):
    result = validate_manual_path(str(tmp_path), [])
    assert result.ok is False
    assert result.capabilities == frozenset()


def test_invalid_credentials_still_accepted_when_transcripts_exist(tmp_path):
    """A broken credentials file must not veto a directory that can still
    drive the per-model view."""
    (tmp_path / ".credentials.json").write_text("{}", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    result = validate_manual_path(str(tmp_path), [])
    assert result.ok
    assert result.capabilities == frozenset({"models"})


# ---------------------------------------------------------------------------
# Codex homes
# ---------------------------------------------------------------------------


def _codex_home(tmp_path, subdir="sessions"):
    home = tmp_path / ".codex"
    (home / subdir).mkdir(parents=True)
    return home


def test_codex_home_with_sessions_is_accepted(tmp_path):
    home = _codex_home(tmp_path)
    result = validate_codex_path(str(home), active_config_dirs=[])
    assert result.ok
    assert result.config_dir == str(home)


def test_codex_home_with_only_archived_sessions_is_accepted(tmp_path):
    home = _codex_home(tmp_path, "archived_sessions")
    assert validate_codex_path(str(home), active_config_dirs=[]).ok


def test_codex_sessions_dir_itself_is_normalized_to_the_home(tmp_path):
    home = _codex_home(tmp_path)
    result = validate_codex_path(str(home / "sessions"), active_config_dirs=[])
    assert result.ok
    assert result.config_dir == str(home)


def test_codex_dir_without_sessions_is_rejected(tmp_path):
    (tmp_path / ".codex").mkdir()
    result = validate_codex_path(str(tmp_path / ".codex"), active_config_dirs=[])
    assert not result.ok
    assert "sessions" in result.error


def test_codex_missing_dir_is_rejected(tmp_path):
    result = validate_codex_path(str(tmp_path / "nope"), active_config_dirs=[])
    assert not result.ok
    assert "does not exist" in result.error


def test_claude_dir_picked_as_codex_names_the_mistake(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    # Claude Code has a sessions/ folder too, so that alone must not pass.
    (claude / "sessions").mkdir()
    result = validate_codex_path(str(claude), active_config_dirs=[])
    assert not result.ok
    assert "Claude Code" in result.error


def test_codex_home_picked_as_claude_names_the_mistake(tmp_path):
    home = _codex_home(tmp_path)
    result = validate_manual_path(str(home), active_config_dirs=[])
    assert not result.ok
    assert "Codex" in result.error


def test_codex_duplicate_is_detected_across_providers(tmp_path):
    home = _codex_home(tmp_path)
    result = validate_codex_path(str(home / "sessions"), active_config_dirs=[str(home)])
    assert not result.ok
    assert "already added" in result.error


def test_codex_relative_path_is_rejected():
    result = validate_codex_path("relative/.codex", active_config_dirs=[])
    assert not result.ok
    assert "absolute" in result.error.lower()


def _fake_wsl_run(existing_dirs):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        test_expr = cmd[-1]
        path = test_expr.split('"')[1]

        class Result:
            returncode = 0 if path in existing_dirs else 1

        return Result()

    return run, calls


def test_codex_wsl_path_is_checked_through_wsl_exe():
    run, calls = _fake_wsl_run({"/home/nick/.codex/sessions"})
    result = validate_codex_path(
        r"\\wsl.localhost\Ubuntu\home\nick\.codex", active_config_dirs=[], run=run
    )
    assert result.ok
    assert result.config_dir == r"\\wsl.localhost\Ubuntu\home\nick\.codex"
    assert all(cmd[0] == "wsl.exe" for cmd in calls)


def test_codex_wsl_claude_dir_is_rejected():
    run, _ = _fake_wsl_run({"/home/nick/.claude/projects", "/home/nick/.claude/sessions"})
    result = validate_codex_path(
        r"\\wsl.localhost\Ubuntu\home\nick\.claude", active_config_dirs=[], run=run
    )
    assert not result.ok
    assert "Claude Code" in result.error


def test_looks_like_codex_home(tmp_path):
    home = _codex_home(tmp_path)
    assert looks_like_codex_home(str(home))
    claude = tmp_path / ".claude"
    (claude / "sessions").mkdir(parents=True)
    (claude / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")
    assert not looks_like_codex_home(str(claude))
    assert not looks_like_codex_home(str(tmp_path / "missing"))


def test_codex_wsl_dir_with_claude_credentials_is_rejected():
    run, _ = _fake_wsl_run({"/home/nick/.claude/sessions", "/home/nick/.claude/.credentials.json"})
    result = validate_codex_path(
        r"\\wsl.localhost\Ubuntu\home\nick\.claude", active_config_dirs=[], run=run
    )
    assert not result.ok
    assert "Claude Code" in result.error
