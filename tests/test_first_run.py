import json
from pathlib import Path

import pytest

from tokitty import first_run, hooks_install, sprites
from tokitty.accounts import (
    ACCOUNTS_FILENAME,
    Account,
    assign_identity_slug,
    canonicalize_locator,
    load_accounts,
    load_identity_history,
    save_accounts,
)
from tokitty.credentials import (
    CredentialsError,
    KeychainCredentialsSource,
    LocalCredentialsSource,
)
from tokitty.customize import SINGLE_KEY, Customization, load_customization, save_customization
from tokitty.first_run import (
    Candidate,
    classify,
    discover_candidates,
    is_fresh_state_dir,
    macos_source_label,
    mark_done,
    save_picked_accounts,
)
from tokitty.lock import LOCK_FILENAME
from tokitty.settings import SETTINGS_FILENAME, Settings, load_settings, save_settings


class FakeCache:
    def __init__(self, matches=(), claude_dirs=()):
        self._matches = list(matches)
        self._claude_dirs = list(claude_dirs)
        self.all_matches_calls = 0
        self.claude_dirs_calls = 0

    def all_matches(self):
        self.all_matches_calls += 1
        return list(self._matches)

    def claude_dirs(self):
        self.claude_dirs_calls += 1
        return list(self._claude_dirs)


def _unc(distro, rest):
    return "\\\\wsl.localhost\\" + distro + "\\" + rest


# is_fresh_state_dir

def test_fresh_when_empty_or_missing(tmp_path):
    assert is_fresh_state_dir(tmp_path)
    assert is_fresh_state_dir(tmp_path / "nope")


def test_fresh_with_only_the_lock(tmp_path):
    (tmp_path / LOCK_FILENAME).write_text("")
    assert is_fresh_state_dir(tmp_path)


@pytest.mark.parametrize("name", ["customization.json", "settings.json", "anything"])
def test_not_fresh_with_any_other_file(tmp_path, name):
    (tmp_path / LOCK_FILENAME).write_text("")
    (tmp_path / name).write_text("{}")
    assert not is_fresh_state_dir(tmp_path)


def test_not_fresh_with_a_subdir(tmp_path):
    (tmp_path / "sub").mkdir()
    assert not is_fresh_state_dir(tmp_path)


# classify

def test_classify_fresh_writes_pending(tmp_path):
    assert classify(tmp_path, Settings(), excluded=False, environ={}) is True
    assert load_settings(tmp_path).first_run == "pending"


def test_classify_pending_from_a_killed_run_shows_again(tmp_path):
    (tmp_path / "customization.json").write_text("{}")
    assert classify(tmp_path, Settings(first_run="pending"), excluded=False, environ={}) is True


def test_classify_done_does_not_show(tmp_path):
    (tmp_path / "customization.json").write_text("{}")
    assert classify(tmp_path, Settings(first_run="done"), excluded=False, environ={}) is False


def test_classify_existing_user_writes_nothing(tmp_path):
    (tmp_path / "customization.json").write_text("{}")
    assert classify(tmp_path, Settings(), excluded=False, environ={}) is False
    assert not (tmp_path / SETTINGS_FILENAME).exists()


def test_classify_existing_user_settings_file_is_not_rewritten(tmp_path):
    (tmp_path / "customization.json").write_text("{}")
    save_settings(tmp_path, Settings(surprise_me=True))
    before = (tmp_path / SETTINGS_FILENAME).read_bytes()
    assert classify(tmp_path, load_settings(tmp_path), excluded=False, environ={}) is False
    assert (tmp_path / SETTINGS_FILENAME).read_bytes() == before


@pytest.mark.parametrize("pending", [False, True])
def test_classify_excluded_writes_nothing_even_when_fresh(tmp_path, pending):
    settings = Settings(first_run="pending" if pending else "")
    assert classify(tmp_path, settings, excluded=True, environ={}) is False
    assert not (tmp_path / SETTINGS_FILENAME).exists()


def test_classify_env_force_does_not_change_what_is_persisted(tmp_path):
    (tmp_path / "customization.json").write_text("{}")
    assert classify(tmp_path, Settings(first_run="done"), excluded=False,
                    environ={"TOKITTY_FIRST_RUN": "1"}) is True
    assert not (tmp_path / SETTINGS_FILENAME).exists()


def test_classify_env_force_on_a_fresh_dir_writes_nothing(tmp_path):
    assert classify(tmp_path, Settings(), excluded=False, environ={"TOKITTY_FIRST_RUN": "1"}) is True
    assert not (tmp_path / SETTINGS_FILENAME).exists()


def test_classify_other_env_values_do_not_force(tmp_path):
    (tmp_path / "customization.json").write_text("{}")
    assert classify(tmp_path, Settings(), excluded=False, environ={"TOKITTY_FIRST_RUN": "0"}) is False


def test_classify_reads_the_process_environment_by_default(tmp_path, monkeypatch):
    (tmp_path / "customization.json").write_text("{}")
    monkeypatch.setenv("TOKITTY_FIRST_RUN", "1")
    assert classify(tmp_path, Settings(), excluded=False) is True


def test_mark_done(tmp_path):
    save_settings(tmp_path, Settings(first_run="pending", surprise_me=True))
    mark_done(tmp_path)
    loaded = load_settings(tmp_path)
    assert loaded.first_run == "done"
    assert loaded.surprise_me is True


# discover_candidates

def _claude_home(home, credentials=True, projects=False):
    config = home / ".claude"
    config.mkdir(parents=True, exist_ok=True)
    if credentials:
        (config / ".credentials.json").write_text("{}")
    if projects:
        (config / "projects").mkdir()
    return config


def _discover(cache=None, home=None, **kwargs):
    kwargs.setdefault("platform", "linux")
    kwargs.setdefault("environ", {})
    kwargs.setdefault("codex_home", str(home / "no-codex") if home else "/nonexistent/.codex")
    return discover_candidates(cache or FakeCache(), home=home, **kwargs)


def test_native_claude_with_credentials(tmp_path):
    config = _claude_home(tmp_path)
    assert _discover(home=tmp_path) == [Candidate("claude", str(config), "This computer", True)]


def test_native_claude_transcripts_only_starts_unchecked(tmp_path):
    config = _claude_home(tmp_path, credentials=False, projects=True)
    [found] = _discover(home=tmp_path)
    assert found == Candidate("claude", str(config), "This computer", False)
    assert found.default_checked is False


def test_signed_in_row_starts_checked(tmp_path):
    _claude_home(tmp_path)
    assert _discover(home=tmp_path)[0].default_checked is True


def test_empty_claude_dir_is_not_a_candidate(tmp_path):
    _claude_home(tmp_path, credentials=False)
    assert _discover(home=tmp_path) == []


def test_nothing_found(tmp_path):
    cache = FakeCache()
    assert _discover(cache, home=tmp_path, platform="win32") == []


def test_env_override_local(tmp_path):
    creds = tmp_path / "elsewhere" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    home = tmp_path / "home"
    _claude_home(home)
    found = _discover(home=home, environ={"TOKITTY_CREDENTIALS": str(creds)})
    # The override replaces ~/.claude rather than adding to it.
    assert found == [Candidate("claude", str(creds.parent), "This computer", True)]


def test_env_override_missing_file_with_no_transcripts_is_dropped(tmp_path):
    missing = tmp_path / "x" / ".credentials.json"
    assert _discover(home=tmp_path, environ={"TOKITTY_CREDENTIALS": str(missing)}) == []


def test_env_override_unc(tmp_path):
    override = _unc("Ubuntu", r"home\nick\.claude-work\.credentials.json")
    found = _discover(home=tmp_path, environ={"TOKITTY_CREDENTIALS": override})
    assert found == [
        Candidate("claude", _unc("Ubuntu", r"home\nick\.claude-work"), "WSL: Ubuntu", True)
    ]


def test_wsl_credentials_match_the_accounts_dialog_form(tmp_path):
    from tokitty.accounts_ui import build_discovered_path_specs

    matches = [("Ubuntu", "/home/nick/.claude/.credentials.json")]
    cache = FakeCache(matches=matches)
    [found] = _discover(cache, home=tmp_path, platform="win32")
    assert found.config_dir == build_discovered_path_specs(matches)[0].config_dir
    assert found == Candidate("claude", _unc("Ubuntu", r"home\nick\.claude"), "WSL: Ubuntu", True)


def test_wsl_transcripts_only(tmp_path):
    cache = FakeCache(claude_dirs=[("Debian", "/home/nick/.claude-work")])
    [found] = _discover(cache, home=tmp_path, platform="win32")
    assert found == Candidate("claude", _unc("Debian", r"home\nick\.claude-work"), "WSL: Debian", False)


def test_wsl_dir_with_both_is_merged_and_signed_in(tmp_path):
    cache = FakeCache(
        matches=[("Ubuntu", "/home/nick/.claude/.credentials.json")],
        claude_dirs=[("Ubuntu", "/home/nick/.claude"), ("Ubuntu", "/home/nick/.claude-old")],
    )
    found = _discover(cache, home=tmp_path, platform="win32")
    assert [(c.config_dir, c.signed_in) for c in found] == [
        (_unc("Ubuntu", r"home\nick\.claude"), True),
        (_unc("Ubuntu", r"home\nick\.claude-old"), False),
    ]


def test_wsl_is_only_reached_through_the_cache_and_once_each(tmp_path, monkeypatch):
    from tokitty import wsl_probe

    def boom(*args, **kwargs):
        raise AssertionError("direct wsl.exe call")

    monkeypatch.setattr(wsl_probe.subprocess, "run", boom)
    cache = FakeCache()
    _discover(cache, home=tmp_path, platform="win32")
    assert (cache.all_matches_calls, cache.claude_dirs_calls) == (1, 1)


def test_wsl_is_not_swept_off_windows(tmp_path):
    cache = FakeCache(matches=[("Ubuntu", "/home/nick/.claude/.credentials.json")])
    assert _discover(cache, home=tmp_path, platform="linux") == []
    assert (cache.all_matches_calls, cache.claude_dirs_calls) == (0, 0)


def test_codex_present_and_absent(tmp_path):
    codex = tmp_path / ".codex"
    assert _discover(home=tmp_path, codex_home=str(codex)) == []
    (codex / "sessions").mkdir(parents=True)
    assert _discover(home=tmp_path, codex_home=str(codex)) == [
        Candidate("codex", str(codex), "This computer", True)
    ]
    assert _discover(home=tmp_path) == []  # default is home/.codex, tested below


def test_codex_home_defaults_under_the_given_home(tmp_path):
    (tmp_path / ".codex" / "archived_sessions").mkdir(parents=True)
    found = discover_candidates(FakeCache(), platform="linux", environ={}, home=tmp_path)
    assert found == [Candidate("codex", str(tmp_path / ".codex"), "This computer", True)]


def test_order_is_native_then_wsl_then_codex(tmp_path):
    _claude_home(tmp_path)
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    cache = FakeCache(matches=[("Ubuntu", "/home/nick/.claude/.credentials.json")])
    found = discover_candidates(cache, platform="win32", environ={}, home=tmp_path)
    assert [(c.provider, c.where) for c in found] == [
        ("claude", "This PC"), ("claude", "WSL: Ubuntu"), ("codex", "This PC"),
    ]


def test_darwin_is_one_read_only_candidate(tmp_path):
    cache = FakeCache()
    found = discover_candidates(
        cache, platform="darwin", environ={}, home=tmp_path,
        macos_label=lambda: "Signed in (Keychain)",
    )
    assert found == [Candidate(
        "claude", str(tmp_path / ".claude"), "This Mac", True,
        read_only=True, detail="Signed in (Keychain)",
    )]
    assert (cache.all_matches_calls, cache.claude_dirs_calls) == (0, 0)


def test_machine_label_names_each_platform():
    assert first_run.machine_label("darwin") == "This Mac"
    assert first_run.machine_label("win32") == "This PC"
    assert first_run.machine_label("linux") == "This computer"


def test_linux_native_claude_is_this_computer(tmp_path):
    _claude_home(tmp_path)
    [found] = _discover(home=tmp_path, platform="linux")
    assert found.where == "This computer"


def test_darwin_not_signed_in(tmp_path):
    [found] = discover_candidates(
        FakeCache(), platform="darwin", environ={}, home=tmp_path,
        macos_label=lambda: "Not signed in yet",
    )
    assert found.signed_in is False and found.read_only is True


# macos_source_label

def test_macos_label_keychain():
    assert macos_source_label(lambda: KeychainCredentialsSource(service="s")) == "Signed in (Keychain)"


def test_macos_label_local_file_wins():
    assert macos_source_label(lambda: LocalCredentialsSource(path=Path("/x"))) == "Signed in (~/.claude)"


def test_macos_label_not_signed_in():
    def raises():
        raise CredentialsError("nope")

    assert macos_source_label(raises) == "Not signed in yet"


def test_macos_label_uses_the_real_resolver_precedence(tmp_path, monkeypatch):
    from tokitty import credentials

    local = tmp_path / ".claude" / ".credentials.json"
    local.parent.mkdir()
    local.write_text("{}")
    monkeypatch.delenv("TOKITTY_CREDENTIALS", raising=False)
    monkeypatch.setattr(credentials.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(credentials, "_keychain_source", lambda: KeychainCredentialsSource(service="s"))
    assert macos_source_label() == "Signed in (~/.claude)"


# save_picked_accounts

@pytest.fixture
def no_hooks(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("hooks must not be touched")

    for name in ("apply_account_mutation", "install_hooks_for_dir", "save_pending_hook_op"):
        monkeypatch.setattr(hooks_install, name, fail)


def _picked(tmp_path):
    claude_a = tmp_path / "a" / ".claude"
    claude_b = tmp_path / "b" / ".claude"
    codex = tmp_path / ".codex"
    for d in (claude_a, claude_b, codex):
        d.mkdir(parents=True)
    return [
        Candidate("claude", str(claude_a), "This PC", True),
        Candidate("codex", str(codex), "This PC", True),
        Candidate("claude", str(claude_b), "This PC", True),
    ]


def test_save_writes_accounts_json_like_the_dialog(tmp_path, no_hooks):
    state = tmp_path / "state"
    state.mkdir()
    picked = _picked(tmp_path)
    saved = save_picked_accounts(state, picked)

    on_disk = json.loads((state / ACCOUNTS_FILENAME).read_text(encoding="utf-8"))
    assert on_disk == {"accounts": [
        {"name": a.name, "config_dir": c.config_dir, "provider": c.provider}
        for a, c in zip(saved, picked)
    ]}
    assert load_accounts(state) == saved
    # Same slug the dialog's Add derives from the same directory.
    for account, candidate in zip(saved, picked):
        expected, _ = assign_identity_slug(canonicalize_locator(candidate.config_dir), set(), {})
        assert account.name == expected
    history = load_identity_history(state)
    assert history == {
        canonicalize_locator(c.config_dir): a.name for a, c in zip(saved, picked)
    }
    assert len({a.name for a in saved}) == 3


def test_first_claude_absorbs_the_default_look_and_others_get_random(tmp_path, no_hooks, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    default = Customization(colorway="orange", pattern="tabby", label="Mine")
    save_customization(state, {SINGLE_KEY: default})
    looks = iter([("black", "tuxedo"), ("gray", "tabby")])
    monkeypatch.setattr(first_run, "random_look", lambda *a, **k: next(looks))

    saved = save_picked_accounts(state, _picked(tmp_path))
    store = load_customization(state)
    assert store[saved[0].name] == default
    assert (store[saved[1].name].colorway, store[saved[1].name].pattern) == ("black", "tuxedo")
    assert (store[saved[2].name].colorway, store[saved[2].name].pattern) == ("gray", "tabby")


def test_codex_first_does_not_absorb_the_default_look(tmp_path, no_hooks):
    state = tmp_path / "state"
    state.mkdir()
    save_customization(state, {SINGLE_KEY: Customization(colorway="orange", pattern="tabby")})
    picked = _picked(tmp_path)
    saved = save_picked_accounts(state, [picked[1], picked[0]])
    store = load_customization(state)
    assert store[saved[1].name].colorway == "orange"
    assert store[saved[0].name].colorway in sprites.COLORWAYS


def test_no_default_look_leaves_the_first_claude_unset_like_the_dialog(tmp_path, no_hooks):
    state = tmp_path / "state"
    state.mkdir()
    [account] = save_picked_accounts(state, _picked(tmp_path)[:1])
    assert account.name not in load_customization(state)


def test_empty_pick_writes_nothing(tmp_path, no_hooks):
    state = tmp_path / "state"
    state.mkdir()
    assert save_picked_accounts(state, []) == []
    assert list(state.iterdir()) == []


def test_existing_accounts_json_raises_and_is_untouched(tmp_path, no_hooks):
    state = tmp_path / "state"
    state.mkdir()
    save_accounts(state, [Account(name="x", config_dir=str(tmp_path / "x"))])
    before = (state / ACCOUNTS_FILENAME).read_bytes()
    with pytest.raises(FileExistsError):
        save_picked_accounts(state, _picked(tmp_path))
    assert (state / ACCOUNTS_FILENAME).read_bytes() == before
    assert not (state / "identity_history.json").exists()


def test_read_only_candidate_is_refused(tmp_path, no_hooks):
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(ValueError):
        save_picked_accounts(state, [Candidate("claude", str(tmp_path), "This PC", True, read_only=True)])
    assert list(state.iterdir()) == []


def test_wsl_unc_candidate_slug_matches_either_alias(tmp_path, no_hooks):
    state = tmp_path / "state"
    state.mkdir()
    unc = _unc("Ubuntu", r"home\nick\.claude")
    [account] = save_picked_accounts(state, [Candidate("claude", unc, "WSL: Ubuntu", True)])
    expected, _ = assign_identity_slug(canonicalize_locator(unc.replace("wsl.localhost", "wsl$")), set(), {})
    assert account.name == expected
    assert account.config_dir == unc
