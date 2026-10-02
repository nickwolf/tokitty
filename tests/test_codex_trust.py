"""Tests for tokitty/codex_trust.py and the Codex trust record that
hooks_install writes: reading config.toml, recording on install, the
approval status, the activity hint and the stopped-distro gate.

SAFETY: tmp_path only. conftest points hooks_install.state_dir_path at a
temporary directory, so the trust record never lands in the real state dir.
Never touch a real ~/.codex.
"""
import json
import os
import sys

import pytest

from tokitty import codex_trust as ct
from tokitty import hooks_install as hi

UNC = r"\\wsl.localhost\Ubuntu\home\u\.codex"
REWARN = "Codex will ask you to approve Tokitty's hooks again."

SPIKE_CONFIG = """\
model = "gpt-5"

[hooks.state]

[projects.'C:\\Users\\nick']
trust_level = "trusted"

[hooks.state.'C:\\Users\\nick\\.codex\\hooks.json:pre_tool_use:0:0']
trusted_hash = "sha256:aaa"

[hooks.state."C:\\\\Users\\\\nick\\\\.codex\\\\hooks.json:stop:1:0"]
enabled = true
trusted_hash = "sha256:bbb"

[hooks.state.'/home/u/.codex/hooks.json:session_end:0:0']
trusted_hash = 'sha256:ccc'

[hooks.state.'/home/u/.codex/hooks.json:stop:0:0']
enabled = true

[profiles.work]
trusted_hash = "sha256:not-a-hook"
"""

EXPECTED_SPIKE = {
    "C:\\Users\\nick\\.codex\\hooks.json:pre_tool_use:0:0": "sha256:aaa",
    "C:\\Users\\nick\\.codex\\hooks.json:stop:1:0": "sha256:bbb",
    "/home/u/.codex/hooks.json:session_end:0:0": "sha256:ccc",
}


@pytest.fixture
def home(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    return codex_home


@pytest.fixture
def state():
    return hi.state_dir_path()


def _install(home):
    return hi.install_hooks_for_dir(str(home), "codex")


def _refresh(home):
    return hi.refresh_hooks_for_dir(str(home), "codex")


def _hooks(home):
    return json.loads((home / "hooks.json").read_text(encoding="utf-8"))


def _toml_key(config_dir, event, group=0, handler=0):
    return f"{hi.codex_paths(str(config_dir))[1]}:{ct.EVENT_SNAKE[event]}:{group}:{handler}"


def _approve(home, hashes):
    """Write config.toml the way Codex does after a review: one table per
    (event, group, handler) -> hash."""
    lines = []
    for (event, group, handler), value in hashes.items():
        lines.append(f"[hooks.state.'{_toml_key(home, event, group, handler)}']")
        lines.append(f'trusted_hash = "{value}"')
        lines.append("")
    (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")


def _approve_all(home, suffix=""):
    _approve(home, {(e, 0, 0): f"sha256:{e}{suffix}" for e, _m in hi.CODEX_EVENTS})


def _record(state, home):
    data = json.loads((state / ct.RECORD_FILENAME).read_text(encoding="utf-8"))
    return data[ct.home_key(str(home))]


# ---------------------------------------------------------------------------
# read_trusted_hashes
# ---------------------------------------------------------------------------

def _use_tomllib(monkeypatch, enabled):
    if enabled:
        if ct._tomllib() is None:
            pytest.skip("tomllib needs Python 3.11")
    else:
        monkeypatch.setattr(ct, "_tomllib", lambda: None)


@pytest.mark.parametrize("with_tomllib", [True, False])
def test_read_trusted_hashes_from_the_spike_layout(tmp_path, monkeypatch, with_tomllib):
    _use_tomllib(monkeypatch, with_tomllib)
    path = tmp_path / "config.toml"
    path.write_text(SPIKE_CONFIG, encoding="utf-8")

    assert ct.read_trusted_hashes(path) == EXPECTED_SPIKE


@pytest.mark.parametrize("with_tomllib", [True, False])
def test_read_trusted_hashes_missing_file_is_empty(tmp_path, monkeypatch, with_tomllib):
    _use_tomllib(monkeypatch, with_tomllib)
    assert ct.read_trusted_hashes(tmp_path / "config.toml") == {}


@pytest.mark.parametrize("with_tomllib", [True, False])
def test_read_trusted_hashes_unreadable_file_is_none(tmp_path, monkeypatch, with_tomllib):
    _use_tomllib(monkeypatch, with_tomllib)
    path = tmp_path / "config.toml"
    path.mkdir()
    assert ct.read_trusted_hashes(path) is None
    bad = tmp_path / "bad.toml"
    bad.write_bytes(b"\xff\xfe not utf-8")
    assert ct.read_trusted_hashes(bad) is None


def test_read_trusted_hashes_unparseable_toml_is_none_with_tomllib(tmp_path, monkeypatch):
    _use_tomllib(monkeypatch, True)
    path = tmp_path / "config.toml"
    path.write_text("[hooks.state\nbroken = ", encoding="utf-8")
    assert ct.read_trusted_hashes(path) is None


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def test_install_records_null_for_an_unapproved_home(home, state):
    _install(home)

    record = _record(state, home)
    assert isinstance(record["written_at"], float)
    assert set(record["keys"]) == {ct.trust_key(str(home), e, 0, 0) for e, _m in hi.CODEX_EVENTS}
    assert all(value is None for value in record["keys"].values())
    assert ct.home_key(str(home)) == hi._normalize_home_path(hi.codex_paths(str(home))[1])


def test_rewrite_over_an_approved_key_records_the_old_hash_and_leaves_the_rest(home, state):
    _install(home)
    _approve_all(home)
    data = _hooks(home)
    del data["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"]
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    result = _refresh(home)

    assert result.warning == REWARN
    keys = _record(state, home)["keys"]
    assert keys[ct.trust_key(str(home), "SessionEnd", 0, 0)] == "sha256:SessionEnd"
    assert keys[ct.trust_key(str(home), "Stop", 0, 0)] is None
    assert ct.codex_hook_status(str(home), state) == ct.NEEDS_APPROVAL
    # Re-approving writes a new hash, which differs from the recorded one.
    _approve_all(home, suffix="-new")
    assert ct.codex_hook_status(str(home), state) == ct.APPROVED


def test_a_refresh_that_changes_nothing_does_not_touch_the_record(home, state):
    _install(home)
    before = (state / ct.RECORD_FILENAME).read_bytes()

    _refresh(home)

    assert (state / ct.RECORD_FILENAME).read_bytes() == before


def test_record_failure_aborts_before_hooks_json_is_touched(home, monkeypatch):
    user = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]}}
    (home / "hooks.json").write_text(json.dumps(user), encoding="utf-8")
    before = (home / "hooks.json").read_bytes()
    backups = []
    writes = []
    monkeypatch.setattr(hi, "_backup", lambda path: backups.append(path))
    monkeypatch.setattr(hi, "_write_settings", lambda path, data: writes.append(path))

    def boom(state_dir, data):
        raise OSError("disk full")

    monkeypatch.setattr(ct, "_write_record", boom)

    result = _install(home)

    assert result.ok is False
    assert "trust record" in result.message
    assert "disk full" in result.message
    assert backups == [] and writes == []
    assert (home / "hooks.json").read_bytes() == before


def test_record_failure_from_os_replace_aborts_and_never_creates_hooks_json(home, monkeypatch):
    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(ct.os, "replace", boom)

    result = _install(home)

    assert result.ok is False
    assert "trust record" in result.message
    assert not (home / "hooks.json").exists()


def test_record_write_is_atomic_through_a_temp_file(home, state, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((os.path.dirname(str(src)), os.path.dirname(str(dst)), os.path.exists(src)))
        real_replace(src, dst)

    monkeypatch.setattr(ct.os, "replace", spy)

    _install(home)

    # os.replace is process-wide, so hooks.json's own write shows up too.
    assert (str(state), str(state), True) in seen
    assert sorted(p.name for p in state.iterdir() if p.name.startswith(ct.RECORD_FILENAME)) == [ct.RECORD_FILENAME]


def test_unreadable_config_toml_never_blocks_install_and_records_nothing(home, state):
    (home / "config.toml").mkdir()

    result = _install(home)

    assert result.ok
    assert (home / "hooks.json").exists()
    assert not (state / ct.RECORD_FILENAME).exists()
    assert ct.codex_hook_status(str(home), state) == ct.UNREADABLE
    assert result.note == hi.CODEX_UNREADABLE_NOTE


def test_uninstall_removes_the_homes_record_and_only_that_home(home, tmp_path, state):
    other = tmp_path / "other" / ".codex"
    other.mkdir(parents=True)
    _install(home)
    _install(other)

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.ok
    data = json.loads((state / ct.RECORD_FILENAME).read_text(encoding="utf-8"))
    assert list(data) == [ct.home_key(str(other))]


def test_uninstall_succeeds_when_the_record_cannot_be_cleaned_up(home, monkeypatch):
    _install(home)

    def boom(state_dir, data):
        raise OSError("read-only")

    monkeypatch.setattr(ct, "_write_record", boom)

    result = hi.uninstall_hooks_for_dir(str(home), "codex")

    assert result.ok
    assert not any(_hooks(home)["hooks"].values())


def test_tokitty_never_writes_config_toml(home):
    original = SPIKE_CONFIG.encode("utf-8")
    (home / "config.toml").write_bytes(original)

    _install(home)
    assert (home / "config.toml").read_bytes() == original
    data = _hooks(home)
    del data["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"]
    (home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    assert _refresh(home).warning == REWARN
    assert (home / "config.toml").read_bytes() == original
    hi.uninstall_hooks_for_dir(str(home), "codex")
    assert (home / "config.toml").read_bytes() == original


def test_tokitty_never_creates_config_toml(home):
    _install(home)
    _refresh(home)
    hi.uninstall_hooks_for_dir(str(home), "codex")
    assert not (home / "config.toml").exists()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def test_status_is_none_when_nothing_is_installed(home, state):
    assert ct.codex_hook_status(str(home), state) is None
    _install(home)
    hi.uninstall_hooks_for_dir(str(home), "codex")
    assert ct.codex_hook_status(str(home), state) is None


def test_status_with_no_config_toml_needs_approval(home, state):
    _install(home)
    assert ct.codex_hook_status(str(home), state) == ct.NEEDS_APPROVAL


def test_status_with_one_key_missing_needs_approval(home, state):
    _install(home)
    _approve(home, {(e, 0, 0): "sha256:x" for e, _m in hi.CODEX_EVENTS if e != "Interrupt"})
    assert ct.codex_hook_status(str(home), state) == ct.NEEDS_APPROVAL


def test_status_after_a_full_approval_is_approved(home, state):
    result = _install(home)
    assert result.note == hi.CODEX_APPROVAL_NOTE
    _approve_all(home)
    assert ct.codex_hook_status(str(home), state) == ct.APPROVED


def test_status_key_equal_to_the_recorded_value_needs_approval(home, state):
    _approve_all(home)  # a stale approval that predates this install
    result = _install(home)

    assert result.note == hi.CODEX_APPROVAL_NOTE
    assert ct.codex_hook_status(str(home), state) == ct.NEEDS_APPROVAL
    _approve_all(home, suffix="-fresh")
    assert ct.codex_hook_status(str(home), state) == ct.APPROVED


def test_status_with_no_record_reads_a_present_key_as_approved(home, state):
    _install(home)
    _approve_all(home)
    (state / ct.RECORD_FILENAME).unlink()
    assert ct.codex_hook_status(str(home), state) == ct.APPROVED


def test_a_second_install_after_approval_has_no_note(home, state):
    _install(home)
    _approve_all(home)
    assert _install(home).note is None


def test_refresh_never_sets_a_note(home):
    _install(home)
    assert _refresh(home).note is None


def _own_hooks_json(home, config_dir, events=("Stop",)):
    """A hooks.json with Tokitty's source handler for config_dir, written by
    hand for a home spelled differently from where the file lives."""
    native = hi._wsl_native_path(config_dir)
    command = f'python3 "{native}/tokitty/hook_writer.py" --sessions-dir "{native}/tokitty/sessions"'
    hooks = {event: [{"hooks": [{"type": "command", "command": command}]}] for event in events}
    (home / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


def test_status_matches_a_wsl_posix_key_from_a_unc_config_dir(home, state, monkeypatch):
    monkeypatch.setattr(hi, "_local_config_path", lambda config_dir: str(home))
    _own_hooks_json(home, UNC)
    (home / "config.toml").write_text(
        "[hooks.state.'/home/u/.codex/hooks.json:stop:0:0']\ntrusted_hash = \"sha256:z\"\n",
        encoding="utf-8",
    )

    assert ct.codex_hook_status(UNC, state, lambda: ["Ubuntu"]) == ct.APPROVED


def test_status_matches_a_drive_letter_key_in_a_different_case(home, state, monkeypatch):
    config_dir = r"C:\Users\Nick\.codex"
    monkeypatch.setattr(hi, "_local_config_path", lambda cd: str(home))
    _own_hooks_json(home, config_dir)
    (home / "config.toml").write_text(
        "[hooks.state.'c:\\users\\NICK\\.codex\\hooks.json:stop:0:0']\ntrusted_hash = \"sha256:z\"\n",
        encoding="utf-8",
    )

    assert ct.codex_hook_status(config_dir, state) == ct.APPROVED


def test_status_ignores_a_key_for_another_event_group_or_home(home, state, monkeypatch):
    monkeypatch.setattr(hi, "_local_config_path", lambda config_dir: str(home))
    _own_hooks_json(home, UNC)
    (home / "config.toml").write_text(
        "[hooks.state.'/home/u/.codex/hooks.json:stop:1:0']\ntrusted_hash = \"sha256:a\"\n"
        "[hooks.state.'/home/u/.codex/hooks.json:subagent_stop:0:0']\ntrusted_hash = \"sha256:b\"\n"
        "[hooks.state.'/home/x/.codex/hooks.json:stop:0:0']\ntrusted_hash = \"sha256:c\"\n",
        encoding="utf-8",
    )

    assert ct.codex_hook_status(UNC, state, lambda: ["Ubuntu"]) == ct.NEEDS_APPROVAL


def test_a_stopped_distro_is_not_read(home, state, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hi, "_local_config_path", lambda config_dir: str(home))
    _own_hooks_json(home, UNC)
    asked = []

    def stopped():
        asked.append(True)
        return []

    def forbidden(*args, **kwargs):
        raise AssertionError("opened a file in a stopped distro")

    monkeypatch.setattr(ct.hi, "_load_settings", forbidden)
    monkeypatch.setattr(ct, "read_trusted_hashes", forbidden)

    assert ct.codex_hook_status(UNC, state, stopped) is None
    assert ct.codex_activity_hint(UNC, state, stopped) is False
    assert asked == [True, True]


def test_a_running_distro_is_read(home, state, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hi, "_local_config_path", lambda config_dir: str(home))
    _own_hooks_json(home, UNC)

    assert ct.codex_hook_status(UNC, state, lambda: ["ubuntu"]) == ct.NEEDS_APPROVAL


# ---------------------------------------------------------------------------
# Hint
# ---------------------------------------------------------------------------

def test_hint_needs_an_approved_status(home, state):
    _install(home)
    assert ct.codex_activity_hint(str(home), state) is False


def test_hint_is_true_when_approved_and_there_is_no_sessions_dir(home, state):
    _install(home)
    _approve_all(home)
    assert ct.codex_activity_hint(str(home), state) is True


def test_hint_is_false_once_the_sessions_dir_changes_after_the_write(home, state):
    _install(home)
    _approve_all(home)
    sessions = home / "tokitty" / "sessions"
    sessions.mkdir()
    written_at = _record(state, home)["written_at"]

    os.utime(sessions, (written_at + 30, written_at + 30))
    assert ct.codex_activity_hint(str(home), state) is False

    os.utime(sessions, (written_at - 30, written_at - 30))
    assert ct.codex_activity_hint(str(home), state) is True


# ---------------------------------------------------------------------------
# Startup gate
# ---------------------------------------------------------------------------

def _accounts_file(state_dir, config_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [{"config_dir": config_dir, "provider": "codex"}]}), encoding="utf-8"
    )


def test_ensure_current_skips_a_stopped_distro_codex_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hi, "provider_has_hooks", lambda kind: True)
    _accounts_file(tmp_path / "s", UNC)
    called = []

    def refresh(config_dir, provider):
        called.append(config_dir)
        return hi.ConfigDirResult(config_dir, True, "ok")

    assert hi.ensure_current(tmp_path / "s", refresh, lambda: []) == []
    assert called == []
    assert len(hi.ensure_current(tmp_path / "s", refresh, lambda: ["Ubuntu"])) == 1
    assert called == [UNC]


def test_ensure_current_does_not_probe_for_a_local_codex_home_or_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hi, "provider_has_hooks", lambda kind: True)
    state_dir = tmp_path / "s"
    state_dir.mkdir()
    (state_dir / "accounts.json").write_text(
        json.dumps({"accounts": [
            {"config_dir": str(tmp_path / ".codex"), "provider": "codex"},
            {"config_dir": UNC.replace(".codex", ".claude"), "provider": "claude"},
        ]}),
        encoding="utf-8",
    )

    def probe():
        raise AssertionError("probed wsl.exe")

    def refresh(config_dir, provider):
        return hi.ConfigDirResult(config_dir, True, "ok")

    assert len(hi.ensure_current(state_dir, refresh, probe)) == 2


def test_retry_pending_skips_a_stopped_distro_and_keeps_the_op(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hi, "provider_has_hooks", lambda kind: True)
    state_dir = tmp_path / "s"
    state_dir.mkdir()
    hi.save_pending_hook_op(state_dir, "install", UNC, "codex")
    calls = []

    def install(config_dir, provider):
        calls.append((config_dir, provider))
        return hi.ConfigDirResult(config_dir, True, "ok")

    assert hi.retry_pending_hook_op(state_dir, install, install, lambda: []) is None
    assert calls == []
    assert hi.load_pending_hook_op(state_dir) == {"op": "install", "config_dir": UNC, "provider": "codex"}

    result = hi.retry_pending_hook_op(state_dir, install, install, lambda: ["Ubuntu"])

    assert result.ok
    assert calls == [(UNC, "codex")]
    assert hi.load_pending_hook_op(state_dir) is None


# ---------------------------------------------------------------------------
# install_hooks() closing line
# ---------------------------------------------------------------------------

def test_install_hooks_closing_line_names_codex(home, capsys, monkeypatch):
    monkeypatch.setattr(hi, "get_config_dirs", lambda: [(str(home), "codex")])

    assert hi.install_hooks() == 0

    out = capsys.readouterr().out
    assert "codex" in out and "approve the Tokitty hooks" in out and "restart running Codex sessions" in out
    assert "Claude Code" not in out
    assert hi.CODEX_APPROVAL_NOTE in out
