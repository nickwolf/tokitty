import json

from tokitty.manual_path import validate_manual_path


def _oauth_json():
    return json.dumps({"claudeAiOauth": {"accessToken": "x", "expiresAt": 0}})


def test_relative_path_rejected():
    result = validate_manual_path("relative/.claude", active_config_dirs=[])
    assert not result.ok
    assert "absolute" in result.error.lower()


def test_unexpanded_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
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


def test_wsl_dollar_and_localhost_aliases_are_equivalent():
    def fake_run(cmd, **kwargs):
        class R:
            stdout = _oauth_json().encode("utf-8")
            returncode = 0
        return R()

    a = validate_manual_path("\\\\wsl$\\Ubuntu\\home\\nick\\.claude", active_config_dirs=[], run=fake_run)
    b = validate_manual_path("\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude", active_config_dirs=[], run=fake_run)
    assert a.ok and b.ok


def test_duplicate_of_active_account_rejected(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")
    result = validate_manual_path(str(config_dir), active_config_dirs=[str(config_dir)])
    assert not result.ok
    assert "already added" in result.error.lower()
