"""Tests for hook_guard, hook_status_for_dir and apply_hook_operation.

SAFETY: tmp_path only. Never touch a real ~/.claude or ~/.codex.
"""
import json
import os
import threading
from pathlib import Path

import pytest

from tokitty import hook_guard
from tokitty import hooks_install as hi


def _snapshot(root: Path):
    out = {}
    for path in sorted(root.rglob("*")):
        st = path.stat()
        out[str(path.relative_to(root))] = (
            None if path.is_dir() else path.read_bytes(),
            st.st_mtime_ns,
        )
    return out


@pytest.fixture
def claude(tmp_path):
    d = tmp_path / ".claude"
    d.mkdir()
    return d


@pytest.fixture
def codex(tmp_path):
    d = tmp_path / ".codex"
    d.mkdir()
    return d


def _status(d, provider, **kw):
    """Status of d, asserting the tree (and state dir) is untouched."""
    before = _snapshot(d.parent)
    result = hi.hook_status_for_dir(str(d), provider, **kw)
    assert _snapshot(d.parent) == before
    return result


# ---- Claude ---------------------------------------------------------------

def test_claude_not_installed_missing_dir(tmp_path):
    result = hi.hook_status_for_dir(str(tmp_path / "nope"), "claude")
    assert result.state == "not_installed"
    assert not (tmp_path / "nope").exists()


def test_claude_not_installed_empty_dir(claude):
    assert _status(claude, "claude").state == "not_installed"


def test_claude_installed_then_outdated(claude):
    assert hi.install_hooks_for_dir(str(claude), "claude").ok
    assert _status(claude, "claude").state == "installed"

    settings = json.loads((claude / "settings.json").read_text())
    del settings["hooks"]["Stop"]
    (claude / "settings.json").write_text(json.dumps(settings))
    status = _status(claude, "claude")
    assert status.state == "outdated"
    assert "Stop" in status.detail


def test_claude_outdated_when_runner_differs(claude):
    hi.install_hooks_for_dir(str(claude), "claude")
    (claude / "tokitty" / "hook_writer.py").write_text("# old")
    assert _status(claude, "claude").state == "outdated"


def test_claude_local_only(claude):
    hi.install_hooks_for_dir(str(claude), "claude")
    settings = json.loads((claude / "settings.json").read_text())
    (claude / "settings.local.json").write_text(json.dumps(settings))
    (claude / "settings.json").write_text("{}")
    assert _status(claude, "claude").state == "local_only"


def test_claude_error_invalid_json(claude):
    (claude / "settings.json").write_text("{not json")
    status = _status(claude, "claude")
    assert status.state == "error"
    assert "settings.json" in status.detail


@pytest.mark.parametrize("body", ["[]", '{"hooks": []}', '{"hooks": {"Stop": 3}}'])
def test_claude_error_wrong_shape(claude, body):
    (claude / "settings.json").write_text(body)
    status = _status(claude, "claude")
    assert status.state == "error"
    assert status.detail


def test_unsupported_provider(claude):
    assert _status(claude, "no-such-harness").state == "unsupported"


def test_oserror_reading_dir_is_unreachable(claude, monkeypatch):
    real_stat = Path.stat

    def boom(self, *a, **k):
        if self == claude:
            raise PermissionError("denied")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", boom)
    assert hi.hook_status_for_dir(str(claude), "claude").state == "unreachable"


# ---- WSL gate -------------------------------------------------------------

UNC = r"\\wsl.localhost\Ubuntu\home\u\.claude"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_stopped_distro_is_unreachable_without_touching_path(provider, monkeypatch):
    seen = []

    def native(_dir):
        raise AssertionError("path must not be resolved for a stopped distro")

    monkeypatch.setattr(hi, "_local_config_path", native)
    status = hi.hook_status_for_dir(
        UNC, provider, distro_running=lambda name: seen.append(name) or False
    )
    assert status.state == "unreachable"
    assert seen == ["Ubuntu"]


def test_running_distro_proceeds(tmp_path, monkeypatch):
    target = tmp_path / "home"
    target.mkdir()
    monkeypatch.setattr(hi, "_local_config_path", lambda _d: str(target))
    status = hi.hook_status_for_dir(UNC, "claude", distro_running=lambda name: True)
    assert status.state == "not_installed"


# ---- Codex ----------------------------------------------------------------

def test_codex_not_installed(codex):
    assert _status(codex, "codex").state == "not_installed"


def test_codex_installed_awaiting_approval_then_installed(codex):
    from tokitty import codex_trust as ct

    assert hi.install_hooks_for_dir(str(codex), "codex").ok
    assert _status(codex, "codex").state == "awaiting_approval"

    lines = []
    for event, _m in hi.CODEX_EVENTS:
        lines.append(f"[hooks.state.'{ct.trust_key(str(codex), event, 0, 0)}']")
        lines.append(f'trusted_hash = "sha256:{event}"')
    (codex / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _status(codex, "codex").state == "installed"


def test_codex_outdated_when_event_missing(codex):
    hi.install_hooks_for_dir(str(codex), "codex")
    data = json.loads((codex / "hooks.json").read_text())
    del data["hooks"]["Stop"]
    (codex / "hooks.json").write_text(json.dumps(data))
    status = _status(codex, "codex")
    assert status.state == "outdated"
    assert "Stop" in status.detail


def test_codex_automatic_reviewer_home(codex):
    (codex / "config.toml").write_text('approvals_reviewer = "auto_review"\n', encoding="utf-8")
    hi.install_hooks_for_dir(str(codex), "codex")
    # Seven handlers installed: the reviewer's home is complete, not outdated.
    assert _status(codex, "codex").state in ("awaiting_approval", "installed")

    # The same home carrying the PermissionRequest handler is outdated.
    other = codex.parent / ".codex2"
    other.mkdir()
    hi.install_hooks_for_dir(str(other), "codex")
    (other / "config.toml").write_text('approvals_reviewer = "auto_review"\n', encoding="utf-8")
    status = _status(other, "codex")
    assert status.state == "outdated"
    assert "PermissionRequest" in status.detail


def test_codex_error_invalid_json_and_wrong_shape(codex):
    (codex / "hooks.json").write_text("{nope")
    assert _status(codex, "codex").state == "error"
    (codex / "hooks.json").write_text('{"hooks": []}')
    assert _status(codex, "codex").state == "error"
    (codex / "hooks.json").write_text('{"stray": 1, "hooks": {}}')
    status = _status(codex, "codex")
    assert status.state in ("not_installed", "error")


def test_codex_stray_top_level_key_with_owned_entry_is_error(codex):
    hi.install_hooks_for_dir(str(codex), "codex")
    data = json.loads((codex / "hooks.json").read_text())
    data["stray"] = 1
    (codex / "hooks.json").write_text(json.dumps(data))
    status = _status(codex, "codex")
    assert status.state == "error"
    assert "stray" in status.detail


# ---- apply_hook_operation -------------------------------------------------

@pytest.fixture
def state(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


def _ok(config_dir, provider):
    return hi.ConfigDirResult(config_dir, True, "done")


def test_refuses_when_another_dir_is_pending(state, tmp_path):
    hi.save_pending_hook_op(state, "install", str(tmp_path / "other"), "claude")
    calls = []
    result = hi.apply_hook_operation(
        state, str(tmp_path / "mine"), "claude", "uninstall",
        install_fn=lambda *a: calls.append(a), uninstall_fn=lambda *a: calls.append(a),
    )
    assert not result.ok
    assert result.blocked_by == str(tmp_path / "other")
    assert calls == []
    assert hi.load_pending_hook_op(state)["config_dir"] == str(tmp_path / "other")


def test_supersedes_same_dir_pending_op(state, tmp_path):
    d = str(tmp_path / "mine")
    hi.save_pending_hook_op(state, "install", d, "claude")
    seen = []

    def uninstall(cd, provider):
        seen.append(hi.load_pending_hook_op(state))
        return _ok(cd, provider)

    result = hi.apply_hook_operation(state, d, "claude", "uninstall", uninstall_fn=uninstall)
    assert result.ok
    assert seen == [{"op": "remove", "config_dir": d, "provider": "claude"}]
    assert hi.load_pending_hook_op(state) is None


@pytest.mark.parametrize("failure", ["not_ok", "oserror", "other"])
def test_journal_left_on_failure(state, tmp_path, failure):
    d = str(tmp_path / "mine")

    def install(cd, provider):
        if failure == "oserror":
            raise OSError("disk gone")
        if failure == "other":
            raise RuntimeError("boom")
        return hi.ConfigDirResult(cd, False, "nope")

    result = hi.apply_hook_operation(state, d, "claude", "install", install_fn=install)
    assert not result.ok
    assert result.message
    assert hi.load_pending_hook_op(state) == {"op": "install", "config_dir": d, "provider": "claude"}


def test_journal_cleared_on_success(state, tmp_path):
    d = str(tmp_path / "mine")
    result = hi.apply_hook_operation(state, d, "claude", "install", install_fn=_ok)
    assert result.ok and result.blocked_by is None
    assert hi.load_pending_hook_op(state) is None


def test_unknown_op_raises(state, tmp_path):
    with pytest.raises(ValueError):
        hi.apply_hook_operation(state, str(tmp_path), "claude", "frobnicate")


def test_remove_then_startup_retry_does_not_reinstall(state, claude):
    d = str(claude)
    # An older install left pending (e.g. crashed), then the tab removes.
    hi.save_pending_hook_op(state, "install", d, "claude")
    assert hi.apply_hook_operation(state, d, "claude", "install").ok
    assert hi.hook_status_for_dir(d, "claude").state == "installed"

    hi.save_pending_hook_op(state, "install", d, "claude")
    assert hi.apply_hook_operation(state, d, "claude", "uninstall").ok
    assert hi.retry_pending_hook_op(state) is None
    assert hi.hook_status_for_dir(d, "claude").state == "not_installed"


def test_failed_tab_op_is_finished_by_startup_retry(state, claude):
    d = str(claude)
    def flaky(cd, provider):
        raise OSError("transient")

    result = hi.apply_hook_operation(state, d, "claude", "install", install_fn=flaky)
    assert not result.ok
    assert hi.hook_status_for_dir(d, "claude").state == "not_installed"
    retry = hi.retry_pending_hook_op(state)
    assert retry is not None and retry.ok
    assert hi.hook_status_for_dir(d, "claude").state == "installed"


# ---- hook_guard -----------------------------------------------------------

def test_guard_acquire_release(tmp_path):
    assert not hook_guard.held(tmp_path)
    token = hook_guard.try_acquire(tmp_path, "tab")
    assert token is not None
    assert hook_guard.held(tmp_path)
    assert hook_guard.held_kind(tmp_path) == "tab"
    assert hook_guard.try_acquire(tmp_path, "startup") is None
    hook_guard.release(token)
    assert not hook_guard.held(tmp_path)
    hook_guard.release(token)  # idempotent


def test_guard_is_keyed_by_resolved_state_dir(tmp_path):
    token = hook_guard.try_acquire(tmp_path, "a")
    try:
        assert hook_guard.held(tmp_path / ".." / tmp_path.name)
    finally:
        hook_guard.release(token)


def test_guard_single_winner_across_threads(tmp_path):
    results = []
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        results.append(hook_guard.try_acquire(tmp_path, "t"))

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    hook_guard.release(winners[0])


def test_frozen_status_makes_no_runner_link(tmp_path, monkeypatch):
    state_dir = tmp_path / "appstate"
    release = tmp_path / "release"
    release.mkdir()
    exe_name = "tokitty.exe" if os.name == "nt" else "tokitty"
    hook_name = "tokitty-hook.exe" if os.name == "nt" else "tokitty-hook"
    (release / exe_name).write_text("gui")
    (release / hook_name).write_text("hook")
    monkeypatch.setattr(hi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hi.sys, "executable", str(release / exe_name))
    monkeypatch.setattr(hi, "state_dir_path", lambda: state_dir)
    home = tmp_path / ".claude"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [hi._build_command(str(home), runner=str(state_dir / "current" / hook_name))]}
    ]}}))
    before = _snapshot(tmp_path)
    status = hi.hook_status_for_dir(str(home), "claude")
    assert status.state == "outdated"
    assert _snapshot(tmp_path) == before
    assert not state_dir.exists()
