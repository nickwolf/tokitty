# Accounts Setup UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent Accounts manager dialog so nobody has to hand-edit `accounts.json`, fixing the identity-key bug, the WSL discovery bug, and the unbounded single-column layout that block it along the way.
**Architecture:** A new `accounts_ui.py` Toplevel dialog drives add/rename/remove through a stable per-account identity slug (`accounts.py`), a crash-consistent write-then-hook ordering (`hooks_install.py`), a grid layout (`ui.py`), and a shared WSL running-distros cache (`distro_probe.py`), wired into the existing menu model and `run_gui` startup sequence.
**Tech Stack:** Python 3.10+, tkinter, pytest, hashlib, threading.
**Spec:** docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md

## Global Constraints

Binding on every task below:

- Python 3.10 compatible. Backslashes inside f-string expressions are a SyntaxError before 3.12; build such strings outside the f-string. CI matrix runs 3.10 and 3.14 on ubuntu, macos and windows.
- The suite is headless by default. `pyproject.toml:27` sets `addopts = "-m 'not gui'"`. ANY new test that constructs a real `tk.Tk()` MUST be marked `@pytest.mark.gui` or it reddens the headless matrix. gui tests run via `xvfb-run -a pytest -m gui`.
- Baseline is 413 collected tests plus 5 gui-marked. Every task must leave the suite green.
- Never write the legacy `"coat"` key to accounts.json.
- `activity_watcher.py` is hook-adjacent; changes there need their own tests.
- Follow the existing tmp-file plus `os.replace` write pattern (`customize.py:103-108`) for every state-file write.

## User decisions (already made)

- Arbitrary N accounts, no cap. Owner: "if they want to add 10 accounts that's their problem".
- Grid layout after 4 accounts, balanced rows: `cols = ceil(N/4)`, `rows = ceil(N/cols)`, row-major.
- Removing the last remaining account is disabled; there is no zero-account state.
- accounts.json `name` is a stable identity slug; customization.json `label` is the display name.
- Removing an account KEEPS its customization entry, orphaned.
- New accounts get a random colorway/pattern.
- First-run auto-open when accounts.json is absent and discovery finds more than one usable credential source.
- The shared distro probe is folded into this issue.

---

### Task 1: Loader state distinction in accounts.py

**Goal:** `load_accounts` currently collapses absent, malformed, not-a-list, and valid-but-empty files to the same `None` return; split those into a typed `AccountsLoadResult` with a state field, while keeping `load_accounts` itself working exactly as before for every existing caller.

**Files:**
- Modify: `tokitty/accounts.py:26-49` (`load_accounts`)
- Test: `tests/test_accounts.py`

**Acceptance Criteria:**
- [ ] `load_accounts_result(state_dir)` returns an `AccountsLoadResult(state, accounts)` where `state` is one of `"absent"`, `"valid_non_empty"`, `"valid_empty"`, `"malformed"`.
- [ ] File missing entirely -> `state="absent"`, `accounts=[]`.
- [ ] File present but unparseable JSON -> `state="malformed"`, `accounts=[]`.
- [ ] File present, valid JSON, but `"accounts"` is not a list -> `state="malformed"`, `accounts=[]`.
- [ ] File present, valid JSON, `"accounts"` is a list, but every entry is invalid (missing `config_dir`) -> `state="valid_empty"`, `accounts=[]`.
- [ ] File present, valid JSON, at least one valid entry -> `state="valid_non_empty"`, `accounts` populated in list order, tested at N=3.
- [ ] `load_accounts(state_dir)` is unchanged in behavior: returns the account list on `"valid_non_empty"`, `None` on every other state. All 4 existing `test_accounts.py` callers of `load_accounts` keep passing unmodified.

**Verify:**
```
python3 -m pytest tests/test_accounts.py -v
```
Expected: all tests pass, including the new `AccountsLoadResult` tests, with no changes required to pre-existing test functions.

**Steps:**
- [ ] Step 1: Write the failing tests. Add to `tests/test_accounts.py`:
```python
from tokitty.accounts import AccountsLoadResult, load_accounts_result


def test_load_accounts_result_absent(tmp_path):
    result = load_accounts_result(tmp_path)
    assert result.state == "absent"
    assert result.accounts == []


def test_load_accounts_result_malformed_json(tmp_path):
    (tmp_path / "accounts.json").write_text("{not json", encoding="utf-8")
    result = load_accounts_result(tmp_path)
    assert result.state == "malformed"
    assert result.accounts == []


def test_load_accounts_result_accounts_not_a_list(tmp_path):
    write_accounts(tmp_path, {"accounts": "nope"})
    result = load_accounts_result(tmp_path)
    assert result.state == "malformed"


def test_load_accounts_result_valid_but_empty(tmp_path):
    write_accounts(tmp_path, {"accounts": [{"name": "x"}]})  # no config_dir
    result = load_accounts_result(tmp_path)
    assert result.state == "valid_empty"
    assert result.accounts == []


def test_load_accounts_result_valid_non_empty_three_accounts(tmp_path):
    write_accounts(tmp_path, {"accounts": [
        {"name": "a", "config_dir": "/home/u/.claude-a"},
        {"name": "b", "config_dir": "/home/u/.claude-b"},
        {"name": "c", "config_dir": "/home/u/.claude-c"},
    ]})
    result = load_accounts_result(tmp_path)
    assert result.state == "valid_non_empty"
    assert [a.name for a in result.accounts] == ["a", "b", "c"]
```
- [ ] Step 2: Run it and see it fail. `python3 -m pytest tests/test_accounts.py -k load_accounts_result -v` fails with `ImportError: cannot import name 'AccountsLoadResult'`.
- [ ] Step 3: Write the implementation. Replace `load_accounts` in `tokitty/accounts.py`:
```python
@dataclass(frozen=True)
class AccountsLoadResult:
    state: str  # "absent" | "valid_non_empty" | "valid_empty" | "malformed"
    accounts: List[Account]


def load_accounts_result(state_dir: Path) -> AccountsLoadResult:
    path = Path(state_dir) / ACCOUNTS_FILENAME
    if not path.is_file():
        return AccountsLoadResult(state="absent", accounts=[])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AccountsLoadResult(state="malformed", accounts=[])
    entries = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return AccountsLoadResult(state="malformed", accounts=[])

    accounts: List[Account] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or not entry.get("config_dir"):
            continue
        accounts.append(
            Account(
                name=str(entry.get("name") or f"account {index}"),
                config_dir=str(entry["config_dir"]),
                coat=entry.get("coat"),
            )
        )
    if not accounts:
        return AccountsLoadResult(state="valid_empty", accounts=[])
    return AccountsLoadResult(state="valid_non_empty", accounts=accounts)


def load_accounts(state_dir: Path) -> Optional[List[Account]]:
    """Backward-compatible accessor: every existing caller keeps seeing
    None for absent, malformed, and valid-but-empty files, and the
    account list only for valid_non_empty. New code that needs to tell
    those apart (customization_key's migration, first-run auto-open)
    calls load_accounts_result directly."""
    result = load_accounts_result(state_dir)
    return result.accounts if result.state == "valid_non_empty" else None
```
- [ ] Step 4: Run it and see it pass. `python3 -m pytest tests/test_accounts.py -v` all green.
- [ ] Step 5: Commit.
```
git add tokitty/accounts.py tests/test_accounts.py
git commit -m "accounts: distinguish absent/malformed/empty/valid accounts.json states"
```

---

### Task 2: `save_accounts` writer in accounts.py

**Goal:** Give `accounts.json` its first writer, using the project's standard tmp-file-plus-`os.replace` pattern, so the manager (Task 13) never hand-serializes the file.

**Files:**
- Modify: `tokitty/accounts.py` (add `save_accounts`, imports `os`)
- Test: `tests/test_accounts.py`

**Acceptance Criteria:**
- [ ] `save_accounts(state_dir, accounts: List[Account])` writes `{"accounts": [{"name": ..., "config_dir": ...}, ...]}`.
- [ ] The legacy `"coat"` key is never written, even if an `Account.coat` is set (it stays read-only legacy, per Global Constraints).
- [ ] Uses `path.with_suffix(".json.tmp")` then `os.replace`, matching `customize.py:103-108`.
- [ ] Round-trips through `load_accounts` at N=1, N=3, and N=5: `load_accounts(save_accounts(...))` reproduces the same names and config dirs in the same order.

**Verify:**
```
python3 -m pytest tests/test_accounts.py -k save_accounts -v
```
Expected: 3 round-trip tests (N=1, N=3, N=5) pass.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
from tokitty.accounts import save_accounts


def test_save_accounts_round_trip_n1(tmp_path):
    accounts = [Account(name="solo", config_dir="/home/u/.claude")]
    save_accounts(tmp_path, accounts)
    assert load_accounts(tmp_path) == accounts


def test_save_accounts_round_trip_n3(tmp_path):
    accounts = [
        Account(name="a", config_dir="/home/u/.claude-a"),
        Account(name="b", config_dir="/home/u/.claude-b"),
        Account(name="c", config_dir="/home/u/.claude-c"),
    ]
    save_accounts(tmp_path, accounts)
    assert load_accounts(tmp_path) == accounts


def test_save_accounts_round_trip_n5(tmp_path):
    accounts = [Account(name=f"acct{i}", config_dir=f"/home/u/.claude-{i}") for i in range(5)]
    save_accounts(tmp_path, accounts)
    assert load_accounts(tmp_path) == accounts


def test_save_accounts_never_writes_coat_key(tmp_path):
    save_accounts(tmp_path, [Account(name="a", config_dir="/home/u/.claude", coat="orange_tabby")])
    raw = (tmp_path / "accounts.json").read_text(encoding="utf-8")
    assert "coat" not in raw


def test_save_accounts_uses_tmp_file_and_replace(tmp_path, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("tokitty.accounts.os.replace", spy_replace)
    save_accounts(tmp_path, [Account(name="a", config_dir="/home/u/.claude")])
    assert len(calls) == 1
    assert calls[0][0].endswith("accounts.json.tmp")
    assert calls[0][1].endswith("accounts.json")
```
(`os` is already imported at the top of `tests/test_accounts.py`'s target module; add `import os` to the test file if it is not already present there.)
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'save_accounts'`.
- [ ] Step 3: Implement, in `tokitty/accounts.py`:
```python
def save_accounts(state_dir: Path, accounts: List[Account]) -> None:
    """First writer accounts.json has ever had. Never writes the legacy
    "coat" key -- see Account.coat's docstring: it is parsed for
    backward compatibility only, translated via sprites.LEGACY_COAT_MAP
    on read, and this writer's job is to persist the new identity-slug
    scheme's accounts, not to round-trip legacy coats."""
    path = Path(state_dir) / ACCOUNTS_FILENAME
    payload = {
        "accounts": [
            {"name": account.name, "config_dir": account.config_dir}
            for account in accounts
        ]
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
```
(`os` is already imported at the top of `tokitty/accounts.py`.)
- [ ] Step 4: Run and see it pass.
- [ ] Step 5: Commit.
```
git add tokitty/accounts.py tests/test_accounts.py
git commit -m "accounts: add save_accounts writer with tmp+replace"
```

---

### Task 3: Fix the WSL path helpers

**Goal:** Replace the hard-coded `rsplit("/.claude/", 1)` in the WSL config/sessions path helpers, which silently returns the unsplit input on any non-`.claude` basename (e.g. `.claude-work`), with basename-independent parent derivation.

**Files:**
- Modify: `tokitty/wsl_probe.py:117-119` (`_wsl_home_windows_style`), `:122-128` (`wsl_sessions_dir_from_credentials`), `:131-138` (`wsl_config_dir_from_credentials`)
- Test: `tests/test_wsl_probe.py`

**Acceptance Criteria:**
- [ ] `wsl_config_dir_from_credentials("Ubuntu", "/home/nick/.claude/.credentials.json")` returns `\\wsl.localhost\Ubuntu\home\nick\.claude` (unchanged behavior for the existing shape).
- [ ] `wsl_config_dir_from_credentials("Ubuntu", "/home/nick/.claude-work/.credentials.json")` returns `\\wsl.localhost\Ubuntu\home\nick\.claude-work` (currently returns the garbled `...\.claude-work\.credentials.json\.claude`).
- [ ] `wsl_sessions_dir_from_credentials` returns the matching `...\tokitty\sessions` suffix for both shapes.
- [ ] No f-string expression contains a backslash (Python 3.10 SyntaxError guard).
- [ ] `_wsl_home_windows_style` is removed; nothing else in the codebase references it (`grep -rn _wsl_home_windows_style tokitty/` returns only its own removal diff, i.e. nothing).

**Verify:**
```
python3 -m pytest tests/test_wsl_probe.py -v && grep -rn _wsl_home_windows_style tokitty/
```
Expected: all tests pass; grep prints nothing.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
def test_wsl_config_dir_from_credentials_dot_claude():
    result = wsl_config_dir_from_credentials("Ubuntu", "/home/nick/.claude/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude"


def test_wsl_config_dir_from_credentials_dot_claude_work():
    result = wsl_config_dir_from_credentials("Ubuntu", "/home/nick/.claude-work/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude-work"


def test_wsl_sessions_dir_from_credentials_dot_claude():
    result = wsl_sessions_dir_from_credentials("Ubuntu", "/home/nick/.claude/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude\\tokitty\\sessions"


def test_wsl_sessions_dir_from_credentials_dot_claude_work():
    result = wsl_sessions_dir_from_credentials("Ubuntu", "/home/nick/.claude-work/.credentials.json")
    assert result == "\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude-work\\tokitty\\sessions"
```
- [ ] Step 2: Run and see it fail. `test_wsl_config_dir_from_credentials_dot_claude_work` fails: actual is `\\wsl.localhost\Ubuntu\home\nick\.claude-work\.credentials.json\.claude`.
- [ ] Step 3: Implement. Replace the three functions in `tokitty/wsl_probe.py`:
```python
def _wsl_config_dir_windows_style(wsl_credentials_path: str) -> str:
    """Windows-style (backslash) relative path to the credentials file's
    parent directory, independent of that directory's basename. Fixes
    the old rsplit("/.claude/", 1) approach, which silently returned the
    whole input unsplit whenever the basename wasn't literally ".claude"
    (e.g. ".claude-work"), because str.rsplit returns the input
    unchanged when the separator isn't found."""
    config_posix = str(PurePosixPath(wsl_credentials_path).parent)
    return config_posix.lstrip("/").replace("/", "\\")


def wsl_config_dir_from_credentials(distro: str, wsl_credentials_path: str) -> str:
    """Derive the \\\\wsl.localhost UNC path to the WSL-side Claude Code
    config dir (the one containing settings.json) from a (distro,
    wsl-side credentials path) pair returned by find_wsl_credentials --
    never hardcode a username, always derive it from the actual
    credentials path found."""
    windows_style = _wsl_config_dir_windows_style(wsl_credentials_path)
    # Backslash built outside the f-string expression on purpose:
    # backslashes inside an f-string expression are a SyntaxError before
    # Python 3.12, and the CI matrix runs 3.10.
    unc_prefix = "\\\\wsl.localhost\\" + distro + "\\"
    return unc_prefix + windows_style


def wsl_sessions_dir_from_credentials(distro: str, wsl_credentials_path: str) -> str:
    """Derive the \\\\wsl.localhost UNC path to tokitty's sessions dir from
    a (distro, wsl-side credentials path) pair returned by
    find_wsl_credentials."""
    config_dir = wsl_config_dir_from_credentials(distro, wsl_credentials_path)
    return config_dir + "\\tokitty\\sessions"
```
Add `from pathlib import PurePosixPath` to the imports at the top of `tokitty/wsl_probe.py` (alongside the existing `import subprocess`).
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_wsl_probe.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/wsl_probe.py tests/test_wsl_probe.py
git commit -m "wsl_probe: derive config dir from credentials path's parent, not a hardcoded split"
```

---

### Task 4: Broaden discovery glob and add the all-distros sibling

**Goal:** Broaden `_CHECK_SCRIPT`'s glob to catch `.claude*`-shaped config dirs, and add a list-returning sibling of `find_wsl_credentials` that never collapses to one match or raises. Must land after Task 3: broadening the glob first would surface a `.claude-work` path while the path helpers were still garbled, silently pointing the manager's discovery entries at a directory that does not exist.

**Files:**
- Modify: `tokitty/wsl_probe.py:15` (`_CHECK_SCRIPT`), add `find_all_wsl_credentials`
- Test: `tests/test_wsl_probe.py`

**Acceptance Criteria:**
- [ ] `_CHECK_SCRIPT` globs `/home/*/.claude*/.credentials.json` instead of `/home/*/.claude/.credentials.json`.
- [ ] `find_all_wsl_credentials(run)` returns `List[Tuple[distro, path]]` for every match across every installed distro.
- [ ] Zero matches -> `[]`, no exception.
- [ ] Multiple matches (including matches within the same distro, e.g. both `.claude` and `.claude-work`) -> all returned, none dropped.
- [ ] `find_wsl_credentials`'s existing single-match/raise contract is unchanged (it stays for callers, like `_default_config_dir`, that still want exactly one match).

**Verify:**
```
python3 -m pytest tests/test_wsl_probe.py -v
```
Expected: all tests pass, including new `find_all_wsl_credentials` coverage for 0, 1, and 3 matches across 2 distros.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
def test_find_all_wsl_credentials_empty():
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))
        return FakeCompletedProcess(stdout=b"")

    assert find_all_wsl_credentials(run=fake_run) == []


def test_find_all_wsl_credentials_returns_every_match_across_distros():
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["wsl.exe", "-l", "-q"]:
            return FakeCompletedProcess(stdout="Ubuntu\nDebian\n".encode("utf-16-le"))
        distro = cmd[2]
        if distro == "Ubuntu":
            out = "/home/nick/.claude/.credentials.json\n/home/nick/.claude-work/.credentials.json\n"
        else:
            out = "/home/dana/.claude/.credentials.json\n"
        return FakeCompletedProcess(stdout=out.encode("utf-8"))

    matches = find_all_wsl_credentials(run=fake_run)
    assert set(matches) == {
        ("Ubuntu", "/home/nick/.claude/.credentials.json"),
        ("Ubuntu", "/home/nick/.claude-work/.credentials.json"),
        ("Debian", "/home/dana/.claude/.credentials.json"),
    }


def test_check_script_globs_dot_claude_star():
    assert "/home/*/.claude*/.credentials.json" in _CHECK_SCRIPT
```
(`FakeCompletedProcess` mirrors the fixture already used in `tests/test_wsl_probe.py`'s existing tests; reuse it, don't redefine it.)
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'find_all_wsl_credentials'`, and the glob test fails on the old literal-`.claude` script.
- [ ] Step 3: Implement.
```python
_CHECK_SCRIPT = (
    'for f in /home/*/.claude*/.credentials.json; do [ -f "$f" ] && echo "$f"; done'
)
```
```python
def find_all_wsl_credentials(run: Callable = subprocess.run) -> List[Tuple[str, str]]:
    """Return every (distro, wsl_side_path) credentials match across all
    installed WSL distros, without collapsing to one and without raising
    on zero or many matches. Used by the Accounts manager's discovery,
    which needs the full set; find_wsl_credentials keeps its
    single-match/raise contract for the existing single-account
    resolution callers."""
    distros = list_wsl_distros(run=run)
    matches: List[Tuple[str, str]] = []
    for distro in distros:
        for path in _credentials_paths_in_distro(distro, run=run):
            matches.append((distro, path))
    return matches
```
- [ ] Step 4: Run and see it pass.
- [ ] Step 5: Commit.
```
git add tokitty/wsl_probe.py tests/test_wsl_probe.py
git commit -m "wsl_probe: broaden discovery glob and add find_all_wsl_credentials"
```

---

### Task 5: Identity slug scheme

**Goal:** Give every account a stable, opaque identity slug derived from a canonicalized config-dir locator, with an additive history so a collision resolution or a re-add after removal is stable forever.

**Files:**
- Modify: `tokitty/accounts.py` (add canonicalization, slug assignment, identity history persistence)
- Test: `tests/test_accounts.py`

**Acceptance Criteria:**
- [ ] `canonicalize_locator(config_dir)` for a WSL UNC path (either `\\wsl$\` or `\\wsl.localhost\` alias) returns `wsl:<casefolded distro>:<normalized posix config dir>`, and both aliases of the same real directory produce the identical locator string.
- [ ] `canonicalize_locator` for a Windows-local absolute path (`C:\Users\...`) returns its `ntpath.normcase(ntpath.normpath(...))` form.
- [ ] `canonicalize_locator` for a POSIX absolute path returns `os.path.realpath(...)`.
- [ ] `canonicalize_locator` raises `ValueError` for any relative path, in all three shapes.
- [ ] `assign_identity_slug(locator, taken_slugs, history)` returns `acct-v1-<64 lowercase hex chars>` (full SHA-256), where the digest input is the locator on a first assignment.
- [ ] Calling `assign_identity_slug` twice with the same locator and the history returned by the first call returns the identical slug both times (history hit).
- [ ] On a hash collision (same digest space occupied by a different locator, simulated via a pre-populated `taken_slugs`), the resolver hashes `locator + "\0" + counter` starting at `counter=2` and persists the chosen result in the returned history.
- [ ] `load_identity_history` / `save_identity_history` round-trip via the `customize.py:103-108` tmp+replace pattern, in a new `identity_history.json` file in `state_dir`.

**Verify:**
```
python3 -m pytest tests/test_accounts.py -k "canonicalize or identity_slug or identity_history" -v
```
Expected: all pass, including the wsl$/wsl.localhost locator-equality test and the collision test.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
import hashlib

from tokitty.accounts import (
    assign_identity_slug,
    canonicalize_locator,
    load_identity_history,
    save_identity_history,
)


def test_canonicalize_locator_wsl_aliases_match():
    a = canonicalize_locator("\\\\wsl.localhost\\Ubuntu\\home\\nick\\.claude")
    b = canonicalize_locator("\\\\wsl$\\Ubuntu\\home\\nick\\.claude")
    assert a == b == "wsl:ubuntu:/home/nick/.claude"


def test_canonicalize_locator_windows_local():
    assert canonicalize_locator("C:\\Users\\nick\\.claude") == "c:\\users\\nick\\.claude"


def test_canonicalize_locator_posix(tmp_path):
    real = tmp_path / ".claude"
    real.mkdir()
    assert canonicalize_locator(str(real)) == str(real.resolve())


def test_canonicalize_locator_rejects_relative_path():
    for bad in ("relative/.claude", ".claude", "..\\relative"):
        try:
            canonicalize_locator(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_assign_identity_slug_deterministic_full_sha256():
    locator = "wsl:ubuntu:/home/nick/.claude"
    slug, history = assign_identity_slug(locator, taken_slugs=set(), history={})
    expected_digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()
    assert slug == f"acct-v1-{expected_digest}"
    assert history[locator] == slug


def test_assign_identity_slug_reuses_history_on_second_call():
    locator = "wsl:ubuntu:/home/nick/.claude"
    slug1, history = assign_identity_slug(locator, taken_slugs=set(), history={})
    slug2, history2 = assign_identity_slug(locator, taken_slugs={slug1}, history=history)
    assert slug1 == slug2
    assert history2 == history


def test_assign_identity_slug_resolves_collision_with_counter():
    locator_a = "wsl:ubuntu:/home/nick/.claude"
    slug_a, _ = assign_identity_slug(locator_a, taken_slugs=set(), history={})

    locator_b = "wsl:ubuntu:/home/nick/.claude-work"
    slug_b, history_b = assign_identity_slug(locator_b, taken_slugs={slug_a}, history={})
    assert slug_b != slug_a
    expected_digest = hashlib.sha256(f"{locator_b}\x002".encode("utf-8")).hexdigest()
    # Only true if slug_a happens to equal the un-collided hash of locator_b,
    # which it won't in practice -- this test instead asserts the mechanism:
    # forcing taken_slugs to already contain the un-collided digest forces
    # the counter path.
    forced_taken = {hashlib.sha256(locator_b.encode("utf-8")).hexdigest()}
    forced_taken = {f"acct-v1-{d}" for d in forced_taken}
    slug_c, history_c = assign_identity_slug(locator_b, taken_slugs=forced_taken, history={})
    assert slug_c == f"acct-v1-{expected_digest}"
    assert history_c[locator_b] == slug_c


def test_identity_history_round_trip(tmp_path):
    save_identity_history(tmp_path, {"wsl:ubuntu:/home/nick/.claude": "acct-v1-abc"})
    assert load_identity_history(tmp_path) == {"wsl:ubuntu:/home/nick/.claude": "acct-v1-abc"}


def test_identity_history_absent_file_returns_empty(tmp_path):
    assert load_identity_history(tmp_path) == {}
```
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'canonicalize_locator'`.
- [ ] Step 3: Implement, in `tokitty/accounts.py`. Add imports `hashlib`, `ntpath`, `posixpath`, `from pathlib import PurePosixPath`:
```python
IDENTITY_PREFIX = "acct-v1-"
IDENTITY_HISTORY_FILENAME = "identity_history.json"


def _is_windows_local(config_dir: str) -> bool:
    return len(config_dir) >= 2 and config_dir[1] == ":" and config_dir[0].isalpha()


def canonicalize_locator(config_dir: str) -> str:
    """Canonical locator string for a config dir, used as the identity
    slug's hash input. A WSL UNC path (either \\\\wsl$ or \\\\wsl.localhost
    alias) becomes wsl:<casefolded distro>:<normalized posix dir>, so both
    aliases of the same real directory hash to the same slug. A
    Windows-local path becomes its normcase'd, normpath'd absolute form. A
    POSIX path becomes its absolute, symlink-resolved real path. A
    relative path in any of the three shapes raises ValueError -- there is
    no safe canonical form for "relative to what" outside a live process.
    """
    unc = parse_wsl_unc(config_dir)
    if unc is not None:
        distro, posix_dir = unc
        normalized = str(PurePosixPath(posix_dir))
        return f"wsl:{distro.casefold()}:{normalized}"
    if _is_windows_local(config_dir):
        if not ntpath.isabs(config_dir):
            raise ValueError(f"relative path not allowed: {config_dir}")
        return ntpath.normcase(ntpath.normpath(config_dir))
    if not posixpath.isabs(config_dir):
        raise ValueError(f"relative path not allowed: {config_dir}")
    return os.path.realpath(config_dir)


def assign_identity_slug(locator: str, taken_slugs: "set", history: dict) -> "Tuple[str, dict]":
    """Return (slug, updated_history) for a canonical locator. A history
    hit always wins, so a re-add after removal recovers its exact old
    slug. On a fresh locator, the slug is acct-v1-<full lowercase
    SHA-256 of the locator>; on a collision with a slug already in
    taken_slugs (which the caller populates from every slug in history,
    not just the currently active accounts -- a removed account's old
    slug is invisible to an active-only scan), the digest input becomes
    "<locator>\\0<counter>" starting at counter=2, tried until free."""
    if locator in history:
        return history[locator], history

    digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()
    slug = f"{IDENTITY_PREFIX}{digest}"
    counter = 2
    while slug in taken_slugs:
        digest = hashlib.sha256(f"{locator}\x00{counter}".encode("utf-8")).hexdigest()
        slug = f"{IDENTITY_PREFIX}{digest}"
        counter += 1

    new_history = dict(history)
    new_history[locator] = slug
    return slug, new_history


def load_identity_history(state_dir: Path) -> dict:
    path = Path(state_dir) / IDENTITY_HISTORY_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def save_identity_history(state_dir: Path, history: dict) -> None:
    path = Path(state_dir) / IDENTITY_HISTORY_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
```
- [ ] Step 4: Run and see it pass.
- [ ] Step 5: Commit.
```
git add tokitty/accounts.py tests/test_accounts.py
git commit -m "accounts: add identity slug canonicalization, collision resolution, and history"
```

---

### Task 6: Atomic `_write_settings` in hooks_install.py

**Goal:** `_write_settings` is a bare `open("w")` + `json.dump`, so a crash or disk error mid-write truncates the user's live Claude Code `settings.json`. Make it tmp+`os.replace`, matching every other state-file writer in the project. Small, standalone, sequenced early so Task 8 builds on a safe writer.

**Files:**
- Modify: `tokitty/hooks_install.py:159-162` (`_write_settings`), add `import os`
- Test: `tests/test_hooks_install.py`

**Acceptance Criteria:**
- [ ] `_write_settings` writes via a `.tmp` sibling file then `os.replace`, never `open(path, "w")` directly on the target.
- [ ] Output content (including the trailing newline) is byte-identical to before the change.
- [ ] If the write step raises before `os.replace` runs, the original file (if any) is left untouched, not truncated.
- [ ] `install_hooks_for_dir` and `uninstall_hooks_for_dir` end-to-end tests still pass unmodified.

**Verify:**
```
python3 -m pytest tests/test_hooks_install.py -v
```
Expected: all existing tests pass, plus the two new atomicity tests.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
def test_write_settings_uses_tmp_file_and_replace(tmp_path, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("tokitty.hooks_install.os.replace", spy_replace)
    path = tmp_path / "settings.json"
    _write_settings(path, {"a": 1})
    assert len(calls) == 1
    assert calls[0][0].endswith("settings.json.tmp")
    assert calls[0][1].endswith("settings.json")
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_write_settings_failed_write_does_not_truncate_original(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"original": true}\n', encoding="utf-8")

    def raising_write_text(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", raising_write_text)
    try:
        _write_settings(path, {"new": True})
    except OSError:
        pass
    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == {"original": True}
```
- [ ] Step 2: Run and see it fail. The first test fails: `os.replace` spy records zero calls (the current implementation never calls it).
- [ ] Step 3: Implement.
```python
def _write_settings(path: Path, data) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
```
Add `import os` to the top of `tokitty/hooks_install.py`, alongside the existing `import json`, `import shutil`.
- [ ] Step 4: Run and see it pass.
- [ ] Step 5: Commit.
```
git add tokitty/hooks_install.py tests/test_hooks_install.py
git commit -m "hooks_install: make _write_settings atomic via tmp+replace"
```

---

### Task 7: Identity-key fix and versioned migration

**Goal:** Fix `customization_key` to key on `account.name` whenever an account exists at all (never `"default"` unless there is no `accounts.json` file whatsoever), fix `initial_label` to stop showing the opaque slug as a fallback display name, and add a versioned migration that carries a pre-existing `"default"` customization entry into its slug-keyed home across all five upgrade-history rows the spec identifies. Depends on Tasks 1 and 5 (uses `AccountsLoadResult`/`load_accounts_result` and the identity slug scheme).

**Files:**
- Modify: `tokitty/__main__.py:352-359` (`initial_label`), `:404-407` (`dual`/`customization_key`)
- Create: `tokitty/migration.py`
- Test: `tests/test_migration.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `customization_key(account)` returns `account.name` whenever `account is not None`, `SINGLE_KEY` only when `account is None`. The `dual` variable is removed from `customization_key`'s signature and call sites.
- [ ] `initial_label(account, custom)` returns `custom.label` and nothing else -- no fallback to `account.name`. The `dual` parameter is removed.
- [ ] `migrate_default_customization(state_dir, accounts, customization_store)` is idempotent across repeated calls, tracked via a `migration_state.json` marker (never via "does a slug entry already exist", which the table below shows is unsound).
- [ ] Row 1 (always one account, no slug entry): `"default"`'s look is copied to the singleton's slug key, `"default"` removed.
- [ ] Row 2 (previously 2, now 1; `"default"` holds the current singleton's look, a stale slug entry from the removed second account also exists): the current singleton's slug key gets `"default"`'s look; the stale second-account entry is left untouched, orphaned (never overwritten, never deleted).
- [ ] Row 3 (no file, then Add creates one account): handled by `absorb_implicit_default`, not the startup migration -- see below.
- [ ] Row 4 (no file, then Add creates two accounts): startup migration does nothing (`len(accounts) != 1`), matching "does not guess ownership of `default`".
- [ ] Row 5 (historical 1-to-2, `"default"` stale, both slugs current): startup migration does nothing; `"default"` is left in place, unconsumed.
- [ ] `absorb_implicit_default(customization_store, new_slug)` copies `"default"`'s look into `new_slug` only if `new_slug` has no entry yet, and leaves `"default"` in place afterward (so a second Add in the same session is a no-op, not a second absorption).
- [ ] One test per row (5 tests total across the two functions).

**Verify:**
```
python3 -m pytest tests/test_migration.py tests/test_main.py -v
```
Expected: all pass, including the 5 row tests and updated `test_main.py` assertions for the new `customization_key`/`initial_label` signatures.

**Steps:**
- [ ] Step 1: Write the failing tests. Create `tests/test_migration.py`:
```python
from dataclasses import replace

from tokitty.accounts import Account
from tokitty.customize import Customization, SINGLE_KEY
from tokitty.migration import (
    CUSTOMIZATION_MIGRATION_KEY,
    absorb_implicit_default,
    migrate_default_customization,
)


def test_row1_always_one_account_migrates_default_to_slug(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo")}
    accounts = [Account(name="acct-v1-abc", config_dir="/home/u/.claude")]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result["acct-v1-abc"] == Customization(colorway="black", pattern="tuxedo")
    assert SINGLE_KEY not in result


def test_row2_two_to_one_keeps_stale_second_entry_orphaned(tmp_path):
    current_look = Customization(colorway="black", pattern="tuxedo")
    stale_look = Customization(colorway="orange", pattern="tabby")
    store = {SINGLE_KEY: current_look, "acct-v1-removed": stale_look}
    accounts = [Account(name="acct-v1-remaining", config_dir="/home/u/.claude")]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result["acct-v1-remaining"] == current_look
    assert result["acct-v1-removed"] == stale_look
    assert SINGLE_KEY not in result


def test_row3_absorb_implicit_default_on_first_explicit_add():
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo")}
    result = absorb_implicit_default(store, "acct-v1-new")
    assert result["acct-v1-new"] == Customization(colorway="black", pattern="tuxedo")
    assert SINGLE_KEY in result  # left in place, not deleted


def test_row3_absorb_is_a_noop_if_slug_already_has_an_entry():
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo"),
             "acct-v1-new": Customization(colorway="orange", pattern="tabby")}
    result = absorb_implicit_default(store, "acct-v1-new")
    assert result["acct-v1-new"] == Customization(colorway="orange", pattern="tabby")


def test_row4_two_new_accounts_startup_migration_is_noop(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo"),
             "acct-v1-one": Customization(colorway="grey", pattern="calico"),
             "acct-v1-two": Customization(colorway="orange", pattern="tabby")}
    accounts = [
        Account(name="acct-v1-one", config_dir="/home/u/.claude-1"),
        Account(name="acct-v1-two", config_dir="/home/u/.claude-2"),
    ]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result == store


def test_row5_historical_1_to_2_leaves_default_unconsumed(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo"),
             "acct-v1-one": Customization(colorway="grey", pattern="calico"),
             "acct-v1-two": Customization(colorway="orange", pattern="tabby")}
    accounts = [
        Account(name="acct-v1-one", config_dir="/home/u/.claude-1"),
        Account(name="acct-v1-two", config_dir="/home/u/.claude-2"),
    ]
    result = migrate_default_customization(tmp_path, accounts, store)
    assert result[SINGLE_KEY] == Customization(colorway="black", pattern="tuxedo")


def test_migration_is_idempotent_across_repeated_calls(tmp_path):
    store = {SINGLE_KEY: Customization(colorway="black", pattern="tuxedo")}
    accounts = [Account(name="acct-v1-abc", config_dir="/home/u/.claude")]
    first = migrate_default_customization(tmp_path, accounts, store)
    # Simulate a manual edit to the slug entry between launches.
    edited = dict(first)
    edited["acct-v1-abc"] = replace(edited["acct-v1-abc"], label="Personal")
    second = migrate_default_customization(tmp_path, accounts, edited)
    assert second["acct-v1-abc"].label == "Personal"
```
- [ ] Step 2: Run and see it fail. `ModuleNotFoundError: No module named 'tokitty.migration'`.
- [ ] Step 3: Implement. Create `tokitty/migration.py`:
```python
"""One-time, versioned migration of the pre-slug-key "default"
customization entry into its slug-keyed home. See
docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md, The
identity-key fix and migration, for the five upgrade-history rows this
covers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from tokitty.accounts import Account
from tokitty.customize import Customization, SINGLE_KEY

MIGRATION_STATE_FILENAME = "migration_state.json"
CUSTOMIZATION_MIGRATION_KEY = "customization_default_key_v1"


def load_migration_state(state_dir: Path) -> Dict[str, bool]:
    path = Path(state_dir) / MIGRATION_STATE_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_migration_state(state_dir: Path, state: Dict[str, bool]) -> None:
    path = Path(state_dir) / MIGRATION_STATE_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def migrate_default_customization(
    state_dir: Path,
    accounts: Optional[List[Account]],
    customization_store: Dict[str, Customization],
) -> Dict[str, Customization]:
    """Run once (tracked by CUSTOMIZATION_MIGRATION_KEY in
    migration_state.json, never by "does a slug entry already exist" --
    the 2-to-1 row shows that check is unsound, since a stale entry from
    a REMOVED account can already occupy a slug key that has nothing to
    do with the current singleton).

    Only acts when accounts is a single-element list: that is the one
    case where "default"'s ownership is unambiguous. Two or more
    accounts (rows 4 and 5) leaves "default" alone rather than guess.
    """
    state = load_migration_state(state_dir)
    if state.get(CUSTOMIZATION_MIGRATION_KEY):
        return customization_store

    store = dict(customization_store)
    default_entry = store.get(SINGLE_KEY)
    if default_entry is not None and accounts and len(accounts) == 1:
        slug = accounts[0].name
        store[slug] = default_entry
        del store[SINGLE_KEY]

    state[CUSTOMIZATION_MIGRATION_KEY] = True
    save_migration_state(state_dir, state)
    return store


def absorb_implicit_default(
    customization_store: Dict[str, Customization], new_slug: str
) -> Dict[str, Customization]:
    """Called by accounts_ui.py's Add flow, exactly once, only when
    accounts.json did not exist before this Add: the brand-new first
    explicit account inherits the running "default" look instead of a
    random one. "default" is left in place afterward (harmless, unused
    once the pane's key changes) rather than deleted, so a second Add in
    the same session does not re-trigger absorption into the wrong
    account."""
    store = dict(customization_store)
    default_entry = store.get(SINGLE_KEY)
    if default_entry is not None and new_slug not in store:
        store[new_slug] = default_entry
    return store
```
Now update `tokitty/__main__.py`. Replace `initial_label` (`:352-359`):
```python
def initial_label(account: Optional[Account], custom: Customization) -> str:
    """Default label: an explicit stored label always wins; otherwise
    blank. Never falls back to account.name -- since the identity slug
    scheme, account.name is an opaque SHA-256-derived string and must
    never be shown to the user."""
    return custom.label
```
Replace the `dual`/`customization_key` block (`:404-407`):
```python
    def customization_key(account: Optional[Account]) -> str:
        return account.name if account is not None else SINGLE_KEY
```
Update the two call sites that passed `dual` to `initial_label` (the pane-setup loop and `handle_customization_changed`'s label branch) to drop the third argument:
```python
        label = initial_label(account, custom)
```
```python
        if field == "label":
            label = initial_label(unit["account"], custom)
            unit["pane"].set_appearance(label=label)
```
Wire the migration in before the pane-setup loop, right after `customization_store = load_customization(state_dir)`:
```python
    from tokitty.migration import migrate_default_customization

    customization_store = migrate_default_customization(state_dir, accounts, customization_store)
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_migration.py tests/test_main.py -v`. Fix any `test_main.py` assertions that called `initial_label(..., dual=...)` with the old 3-arg signature.
- [ ] Step 5: Commit.
```
git add tokitty/migration.py tokitty/__main__.py tests/test_migration.py tests/test_main.py
git commit -m "migration: versioned default->slug customization migration, drop dual from key/label"
```

---

### Task 8: Write ordering and the pending hook operation journal

**Goal:** Make account add/remove crash-consistent: write `accounts.json` first as durable desired state, persist a pending hook operation record before touching hooks, run the hook side effect, and clear the record only on success so a crash or failure leaves something to retry. Depends on Tasks 2 and 6 (`save_accounts`, atomic `_write_settings`).

**Files:**
- Modify: `tokitty/hooks_install.py` (add pending-op persistence and `apply_account_mutation`/`retry_pending_hook_op`)
- Test: `tests/test_hooks_install.py`

**Acceptance Criteria:**
- [ ] `save_pending_hook_op(state_dir, op, config_dir)` / `load_pending_hook_op(state_dir)` / `clear_pending_hook_op(state_dir)` round-trip via tmp+replace; `load_pending_hook_op` returns `None` for a missing, malformed, or shape-invalid file.
- [ ] `apply_account_mutation(state_dir, accounts, op, config_dir, install_fn, uninstall_fn)` calls `save_accounts` before the pending-op record, and the pending-op record before the hook call (verified by call-order spy).
- [ ] On `result.ok is True`, the pending-op record is cleared.
- [ ] On `result.ok is False`, the pending-op record is left on disk.
- [ ] On the hook function raising, the pending-op record is also left on disk (a raised exception and `ok=False` are both "retry later", per the spec).
- [ ] `retry_pending_hook_op(state_dir, install_fn, uninstall_fn)` re-runs a leftover pending op and clears it on success; returns `None` when nothing was pending.

**Verify:**
```
python3 -m pytest tests/test_hooks_install.py -k "pending or apply_account_mutation or retry_pending" -v
```
Expected: all pass, including the raised-exception case.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
from tokitty.accounts import Account
from tokitty.hooks_install import (
    ConfigDirResult,
    apply_account_mutation,
    clear_pending_hook_op,
    load_pending_hook_op,
    retry_pending_hook_op,
    save_pending_hook_op,
)


def test_pending_hook_op_round_trip(tmp_path):
    save_pending_hook_op(tmp_path, "install", "/home/u/.claude")
    assert load_pending_hook_op(tmp_path) == {"op": "install", "config_dir": "/home/u/.claude"}
    clear_pending_hook_op(tmp_path)
    assert load_pending_hook_op(tmp_path) is None


def test_load_pending_hook_op_missing_file_returns_none(tmp_path):
    assert load_pending_hook_op(tmp_path) is None


def test_apply_account_mutation_writes_accounts_before_pending_op_before_hook(tmp_path):
    order = []
    accounts = [Account(name="a", config_dir="/home/u/.claude")]

    def fake_save_accounts(state_dir, accts):
        order.append("save_accounts")

    def fake_install(config_dir):
        order.append("hook_call")
        return ConfigDirResult(config_dir, True, "installed")

    import tokitty.hooks_install as hi
    real_save = hi.save_accounts if hasattr(hi, "save_accounts") else None

    class _Spy:
        def __call__(self, state_dir, accts):
            fake_save_accounts(state_dir, accts)

    original_save_pending = hi.save_pending_hook_op

    def spy_save_pending(state_dir, op, config_dir):
        order.append("save_pending")
        return original_save_pending(state_dir, op, config_dir)

    import tokitty.accounts as accounts_mod
    monkeypatched_save_accounts = accounts_mod.save_accounts

    def spy_save_accounts(state_dir, accts):
        order.append("save_accounts")
        return monkeypatched_save_accounts(state_dir, accts)

    hi.save_accounts = spy_save_accounts
    hi.save_pending_hook_op = spy_save_pending
    try:
        apply_account_mutation(tmp_path, accounts, "install", "/home/u/.claude", install_fn=fake_install)
    finally:
        hi.save_accounts = monkeypatched_save_accounts
        hi.save_pending_hook_op = original_save_pending

    assert order == ["save_accounts", "save_pending", "hook_call"]


def test_apply_account_mutation_clears_pending_op_on_success(tmp_path):
    accounts = [Account(name="a", config_dir="/home/u/.claude")]
    apply_account_mutation(
        tmp_path, accounts, "install", "/home/u/.claude",
        install_fn=lambda cd: ConfigDirResult(cd, True, "installed"),
    )
    assert load_pending_hook_op(tmp_path) is None


def test_apply_account_mutation_leaves_pending_op_on_ok_false(tmp_path):
    accounts = [Account(name="a", config_dir="/home/u/.claude")]
    apply_account_mutation(
        tmp_path, accounts, "install", "/home/u/.claude",
        install_fn=lambda cd: ConfigDirResult(cd, False, "aborted"),
    )
    assert load_pending_hook_op(tmp_path) == {"op": "install", "config_dir": "/home/u/.claude"}


def test_apply_account_mutation_leaves_pending_op_on_raised_exception(tmp_path):
    accounts = [Account(name="a", config_dir="/home/u/.claude")]

    def raising_install(config_dir):
        raise OSError("disk full")

    try:
        apply_account_mutation(tmp_path, accounts, "install", "/home/u/.claude", install_fn=raising_install)
    except OSError:
        pass
    assert load_pending_hook_op(tmp_path) == {"op": "install", "config_dir": "/home/u/.claude"}


def test_retry_pending_hook_op_clears_on_success(tmp_path):
    save_pending_hook_op(tmp_path, "remove", "/home/u/.claude")
    result = retry_pending_hook_op(
        tmp_path, uninstall_fn=lambda cd: ConfigDirResult(cd, True, "uninstalled")
    )
    assert result.ok
    assert load_pending_hook_op(tmp_path) is None


def test_retry_pending_hook_op_returns_none_when_nothing_pending(tmp_path):
    assert retry_pending_hook_op(tmp_path) is None
```
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'apply_account_mutation'`.
- [ ] Step 3: Implement, in `tokitty/hooks_install.py`. Add `from tokitty.accounts import Account, save_accounts` to the imports:
```python
PENDING_HOOK_OP_FILENAME = "pending_hook_op.json"


def save_pending_hook_op(state_dir: Path, op: str, config_dir: str) -> None:
    path = Path(state_dir) / PENDING_HOOK_OP_FILENAME
    payload = {"op": op, "config_dir": config_dir}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def load_pending_hook_op(state_dir: Path) -> Optional[dict]:
    path = Path(state_dir) / PENDING_HOOK_OP_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("op") not in ("install", "remove") or not data.get("config_dir"):
        return None
    return {"op": data["op"], "config_dir": data["config_dir"]}


def clear_pending_hook_op(state_dir: Path) -> None:
    path = Path(state_dir) / PENDING_HOOK_OP_FILENAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def apply_account_mutation(
    state_dir: Path,
    accounts: List[Account],
    op: str,
    config_dir: str,
    install_fn=install_hooks_for_dir,
    uninstall_fn=uninstall_hooks_for_dir,
) -> ConfigDirResult:
    """accounts.json first (durable desired state), then a pending hook
    op record, then the hook side effect, clearing the record only on
    success. Call this off the Tk thread (see accounts_ui.py, Task 13) --
    a slow filesystem or a stuck wsl.exe call must not freeze the UI.
    result.ok is False and a raised exception are both treated as "did
    not complete": both leave the pending-op record in place for
    retry_pending_hook_op to pick up."""
    save_accounts(state_dir, accounts)
    save_pending_hook_op(state_dir, op, config_dir)
    fn = install_fn if op == "install" else uninstall_fn
    result = fn(config_dir)
    if result.ok:
        clear_pending_hook_op(state_dir)
    return result


def retry_pending_hook_op(
    state_dir: Path, install_fn=install_hooks_for_dir, uninstall_fn=uninstall_hooks_for_dir
) -> Optional[ConfigDirResult]:
    """Called at next startup, or the next time the manager is opened.
    Returns None if there was nothing pending."""
    pending = load_pending_hook_op(state_dir)
    if pending is None:
        return None
    fn = install_fn if pending["op"] == "install" else uninstall_fn
    result = fn(pending["config_dir"])
    if result.ok:
        clear_pending_hook_op(state_dir)
    return result
```
- [ ] Step 4: Run and see it pass.
- [ ] Step 5: Commit.
```
git add tokitty/hooks_install.py tests/test_hooks_install.py
git commit -m "hooks_install: crash-consistent write ordering via a pending hook op journal"
```

---

### Task 9: Grid geometry

**Goal:** Replace the single-column `card_height` with a grid size calculation, and switch pane placement and every window-level width use over to the computed width.

**Files:**
- Modify: `tokitty/ui.py:39-40` (`card_height` -> `grid_size`), `:284-286` (pane placement), `:296` (`_configure_window`), `:464` (`_restore_position`), `:469`, `:473` (`_restore_position`/`clamp_position` call)
- Test: `tests/test_ui_layout.py`

**Acceptance Criteria:**
- [ ] `grid_size(1) == (300, 128, 1)`.
- [ ] `grid_size(4) == (300, 512, 1)`.
- [ ] `grid_size(5) == (600, 384, 2)`.
- [ ] `grid_size(8) == (600, 512, 2)`.
- [ ] `grid_size(9) == (900, 384, 3)`.
- [ ] `grid_size(12) == (900, 512, 3)`.
- [ ] `TokittyWindow.__init__` stores `self._width`, `self._height`, `self._cols` from `grid_size(pane_count)`.
- [ ] Every window-level use of the literal `CARD_WIDTH` for geometry (`ui.py:296`, `:464`, `:469`, `:473` in the pre-change file) uses `self._width` instead; per-pane uses (label placement at `:165`, wraplength at `:156`) are unchanged.
- [ ] Panes are placed row-major: `frame.place(x=(i % cols) * CARD_WIDTH, y=(i // cols) * PANE_HEIGHT)`.
- [ ] Old `card_height` tests in `test_ui_layout.py` (`card_height(1) == 128`, `card_height(2) == 256`) are replaced by `grid_size` assertions, not left dangling as calls to a removed function.

**Verify:**
```
python3 -m pytest tests/test_ui_layout.py -v
```
Expected: all pass, including the 6 worked-example assertions.

**Steps:**
- [ ] Step 1: Write the failing tests. In `tests/test_ui_layout.py`, replace the `card_height` tests with:
```python
from tokitty.ui import grid_size


def test_grid_size_n1():
    assert grid_size(1) == (300, 128, 1)


def test_grid_size_n4():
    assert grid_size(4) == (300, 512, 1)


def test_grid_size_n5():
    assert grid_size(5) == (600, 384, 2)


def test_grid_size_n8():
    assert grid_size(8) == (600, 512, 2)


def test_grid_size_n9():
    assert grid_size(9) == (900, 384, 3)


def test_grid_size_n12():
    assert grid_size(12) == (900, 512, 3)
```
- [ ] Step 2: Run and see it fail. `ImportError: cannot import name 'grid_size'`.
- [ ] Step 3: Implement. In `tokitty/ui.py`, add `import math` to the top imports, then replace `card_height` (`:39-40`):
```python
def grid_size(pane_count: int) -> "Tuple[int, int, int]":
    """(width, height, cols) for pane_count panes filled row-major,
    capped at 4 rows: cols = ceil(N/4), rows = ceil(N/cols). Height
    never exceeds 512px (4 * PANE_HEIGHT); width grows instead."""
    cols = math.ceil(pane_count / 4)
    rows = math.ceil(pane_count / cols)
    return CARD_WIDTH * cols, PANE_HEIGHT * rows, cols
```
Add `Tuple` to the `typing` import at the top of `ui.py` (`from typing import Callable, List, Optional, Tuple`).

Update `TokittyWindow.__init__` (around `:261`):
```python
        self._width, self._height, self._cols = grid_size(pane_count)
```
Update pane placement (`:283-286`):
```python
        self.panes = []
        for i in range(pane_count):
            row, col = divmod(i, self._cols)
            frame = tk.Frame(root, width=CARD_WIDTH, height=PANE_HEIGHT, bg=BG_COLOR)
            frame.place(x=col * CARD_WIDTH, y=row * PANE_HEIGHT)
            self.panes.append(Pane(frame))
```
Update `_configure_window` (`:296`):
```python
        self.root.geometry(f"{self._width}x{self._height}")
```
Update `_restore_position` (`:464`, `:469`, `:473`):
```python
    def _restore_position(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x, y = screen_w - self._width - 24, screen_h - self._height - 24

        if self._position_path.is_file():
            try:
                saved = json.loads(self._position_path.read_text(encoding="utf-8"))
                x, y = clamp_position(int(saved["x"]), int(saved["y"]), self._width, self._height, screen_w, screen_h)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

        self.root.geometry(f"{self._width}x{self._height}+{x}+{y}")
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_ui_layout.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/ui.py tests/test_ui_layout.py
git commit -m "ui: replace single-column card_height with a balanced grid layout"
```

---

### Task 10: Grid-aware hit-testing

**Goal:** `pane_index_at` is a function of `y` only, correct for the old single column but wrong once panes sit in a grid. Fix it to use both axes, and make the right-click menu show only global actions over a blank cell in a ragged final row. Depends on Task 9 (`grid_size`, `self._cols`).

**Files:**
- Modify: `tokitty/ui.py:43-46` (`pane_index_at`), `:390` (`_show_context_menu` call site), `_rebuild_context_menu`
- Test: `tests/test_ui_layout.py`

**Acceptance Criteria:**
- [ ] `pane_index_at(x, y, pane_count, cols)` takes both `x` and `y`, root-relative.
- [ ] At N=5 (`cols=2`), a click at `x=350, y=50` returns `1`, not `0`.
- [ ] At N=5 (`cols=2`), a click at `x=50, y=50` returns `0`.
- [ ] At N=5 (`cols=2`, 3 rows, ragged last row has only 1 pane in it), a click in the blank second cell of the last row (`row=2, col=1`, i.e. index `5`) returns `None`.
- [ ] A negative `x` or `y` returns `None`, never a clamped 0.
- [ ] `_show_context_menu` passes both coordinates and handles a `None` result by showing only the global (non-pane-specific) menu items: Refresh now, Always in front, Show tray icon, Surprise me, Exit. Colorway, Pattern, Randomize, Customize…, Rename… are omitted.

**Verify:**
```
python3 -m pytest tests/test_ui_layout.py -k pane_index_at -v
```
Expected: all pass, including the N=5 x=350/y=50 -> 1 assertion and the ragged-row-returns-None assertion.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
from tokitty.ui import pane_index_at


def test_pane_index_at_n5_x350_y50_selects_pane_1_not_0():
    assert pane_index_at(350, 50, pane_count=5, cols=2) == 1


def test_pane_index_at_n5_x50_y50_selects_pane_0():
    assert pane_index_at(50, 50, pane_count=5, cols=2) == 0


def test_pane_index_at_n5_ragged_last_row_blank_cell_is_none():
    # N=5, cols=2 -> 3 rows, row 2 has only column 0 filled (index 4);
    # row 2 column 1 would be index 5, which does not exist.
    assert pane_index_at(350, 300, pane_count=5, cols=2) is None


def test_pane_index_at_negative_coordinates_return_none():
    assert pane_index_at(-1, 50, pane_count=5, cols=2) is None
    assert pane_index_at(50, -1, pane_count=5, cols=2) is None
```
- [ ] Step 2: Run and see it fail. `TypeError: pane_index_at() takes 2 positional arguments but 4 were given`.
- [ ] Step 3: Implement. Replace `pane_index_at` (`:43-46`):
```python
def pane_index_at(x: int, y: int, pane_count: int, cols: int) -> "Optional[int]":
    """Map root-relative (x, y) to a pane index in row-major grid order,
    or None for a blank cell in a ragged final row (e.g. N=5, cols=2:
    index 4 exists but index 5 does not -- that cell shows only global
    menu actions, never falls back to the nearest real pane)."""
    if x < 0 or y < 0:
        return None
    col = x // CARD_WIDTH
    row = y // PANE_HEIGHT
    if col >= cols:
        return None
    index = row * cols + col
    if index >= pane_count:
        return None
    return index
```
Add a module-level constant just above the `TokittyWindow` class:
```python
_PANE_SPECIFIC_LABELS = frozenset({"Colorway", "Pattern", "Randomize", "Customize…", "Rename…"})
```
Update `_show_context_menu` and `_rebuild_context_menu`:
```python
    def _rebuild_context_menu(self) -> None:
        if getattr(self, "menu", None) is not None:
            self.menu.destroy()
        self._menu_vars = []
        self.menu = tk.Menu(self.root, tearoff=0)
        if self._menu_pane_index is None:
            model = [item for item in self.build_menu_model(0) if item.label not in _PANE_SPECIFIC_LABELS]
        else:
            model = self.build_menu_model(self._menu_pane_index)
        self._render_tk_menu(self.menu, model)

    def _show_context_menu(self, event: tk.Event) -> None:
        x_relative = event.x_root - self.root.winfo_rootx()
        y_relative = event.y_root - self.root.winfo_rooty()
        self._menu_pane_index = pane_index_at(x_relative, y_relative, len(self.panes), self._cols)
        self._rebuild_context_menu()
        self.menu.tk_popup(event.x_root, event.y_root)
```
`self._menu_pane_index` is initialized to `0` in `__init__` (`:279`); its type annotation, if added, is `Optional[int]`.
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_ui_layout.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/ui.py tests/test_ui_layout.py
git commit -m "ui: grid-aware hit-testing, global-only menu over a blank ragged-row cell"
```

---

### Task 11: Shared `RunningDistroProbe`

**Goal:** Replace N independent `wsl.exe --list --running --quiet` spawns (one per `ActivityWatcher`, once per tick) with one process-scoped, single-flight, cached probe injected into every watcher.

**Files:**
- Create: `tokitty/distro_probe.py`
- Modify: `tokitty/__main__.py` (construct one `RunningDistroProbe`, pass `probe.get_running` as `list_running_distros_fn` to every `ActivityWatcher`)
- Test: `tests/test_distro_probe.py`

**Acceptance Criteria:**
- [ ] `RunningDistroProbe.get_running() -> List[str]` matches `ActivityWatcher`'s existing `list_running_distros_fn: Callable[[], List[str]]` seam exactly, so no `ActivityWatcher` code changes.
- [ ] `ProbeResult` distinguishes `CONFIRMED` (non-empty), `EMPTY` (confirmed nothing running), and `UNKNOWN` (subprocess error or timeout) -- collapsing the raw `list_running_distros`'s `[]`-for-both-empty-and-error into 3 distinct states.
- [ ] Concurrent callers within the success TTL do not trigger a second subprocess call: single-flight via `threading.Condition`, verified with N=5 threads racing a gated fake `run`, asserting exactly one call.
- [ ] Success TTL is `1.0` second, defined as a single named constant `SUCCESS_TTL_S`, measured from completion of the refresh via `time.monotonic`.
- [ ] Failure backoff is `20.0` seconds (`FAILURE_BACKOFF_S`).
- [ ] Subprocess timeout is `2.0` seconds (`SUBPROCESS_TIMEOUT_S`), not the 10s used elsewhere in `wsl_probe.py`.
- [ ] A refresh that raises `OSError` or `TimeoutExpired` sets the published result to `UNKNOWN`, discarding any previously cached `CONFIRMED` snapshot -- a stale positive is never reused after a failed refresh.
- [ ] One process-scoped `RunningDistroProbe` is constructed in `run_gui` and injected into every `ActivityWatcher` via `list_running_distros_fn=probe.get_running`.

**Verify:**
```
python3 -m pytest tests/test_distro_probe.py -v
```
Expected: all pass, including the single-flight concurrency test and the failure-invalidates-a-good-result test.

**Steps:**
- [ ] Step 1: Write the failing tests. Create `tests/test_distro_probe.py`:
```python
import subprocess
import threading
import time

from tokitty.distro_probe import ProbeStatus, RunningDistroProbe


class FakeCompletedProcess:
    def __init__(self, stdout: bytes = b"", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_get_running_returns_confirmed_distros():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_running() == ["Ubuntu"]
    assert probe.get_result().status == ProbeStatus.CONFIRMED


def test_get_result_empty_status_on_zero_distros():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout=b"")

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_result().status == ProbeStatus.EMPTY
    assert probe.get_result().distros == frozenset()


def test_get_result_unknown_status_on_subprocess_error():
    def fake_run(cmd, **kwargs):
        raise OSError("wsl.exe not found")

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_result().status == ProbeStatus.UNKNOWN


def test_get_result_unknown_status_on_timeout():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="wsl.exe", timeout=2)

    probe = RunningDistroProbe(run=fake_run)
    assert probe.get_result().status == ProbeStatus.UNKNOWN


def test_success_ttl_avoids_a_second_call_within_window():
    calls = []
    fake_time = {"now": 0.0}

    def fake_run(cmd, **kwargs):
        calls.append(1)
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run, time_fn=lambda: fake_time["now"], success_ttl=1.0)
    probe.get_result()
    fake_time["now"] += 0.5
    probe.get_result()
    assert len(calls) == 1


def test_ttl_expiry_triggers_a_fresh_call():
    calls = []
    fake_time = {"now": 0.0}

    def fake_run(cmd, **kwargs):
        calls.append(1)
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run, time_fn=lambda: fake_time["now"], success_ttl=1.0)
    probe.get_result()
    fake_time["now"] += 1.5
    probe.get_result()
    assert len(calls) == 2


def test_failed_refresh_invalidates_a_previously_confirmed_result():
    fake_time = {"now": 0.0}
    responses = [
        FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le")),
    ]

    def fake_run(cmd, **kwargs):
        if responses:
            return responses.pop()
        raise OSError("wsl.exe not found")

    probe = RunningDistroProbe(run=fake_run, time_fn=lambda: fake_time["now"], success_ttl=1.0)
    first = probe.get_result()
    assert first.status == ProbeStatus.CONFIRMED
    fake_time["now"] += 1.5
    second = probe.get_result()
    assert second.status == ProbeStatus.UNKNOWN
    assert second.distros == frozenset()


def test_single_flight_coalesces_concurrent_callers():
    call_count = {"n": 0}
    release = threading.Event()
    entered = threading.Barrier(5, timeout=5)

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        entered.wait()
        release.wait(timeout=5)
        return FakeCompletedProcess(stdout="Ubuntu\n".encode("utf-16-le"))

    probe = RunningDistroProbe(run=fake_run)
    threads = [threading.Thread(target=probe.get_result) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    release.set()
    for t in threads:
        t.join(timeout=5)
    assert call_count["n"] == 1
```
- [ ] Step 2: Run and see it fail. `ModuleNotFoundError: No module named 'tokitty.distro_probe'`.
- [ ] Step 3: Implement. Create `tokitty/distro_probe.py`:
```python
"""Process-scoped cache of which WSL distros are currently running,
shared across every ActivityWatcher instead of each spawning its own
wsl.exe --list --running --quiet. See docs/superpowers/specs/
2026-08-24-accounts-setup-ui-design.md, Shared distro probe.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, FrozenSet, List

SUCCESS_TTL_S = 1.0  # Matches ActivityWatcher.FAST_INTERVAL_S. The spec
                      # leaves open whether this amount of positive
                      # staleness is acceptable, or whether the "never
                      # restart a stopped distro" invariant demands the
                      # shorter ~0.25s coalescing-only window instead --
                      # that tradeoff is deliberately encoded as this one
                      # constant so adopting the shorter window is a
                      # one-line change, not a redesign.
FAILURE_BACKOFF_S = 20.0
SUBPROCESS_TIMEOUT_S = 2.0

_NO_CONSOLE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ProbeStatus(Enum):
    CONFIRMED = "confirmed"
    EMPTY = "empty"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    distros: FrozenSet[str]


_UNKNOWN_RESULT = ProbeResult(status=ProbeStatus.UNKNOWN, distros=frozenset())


class RunningDistroProbe:
    """One instance, constructed once per process and injected into every
    ActivityWatcher via list_running_distros_fn=probe.get_running.
    threading.Condition gives single-flight refresh: concurrent callers
    within a stale window coalesce into one wsl.exe call instead of a
    thundering herd."""

    def __init__(
        self,
        run: Callable = subprocess.run,
        time_fn: Callable[[], float] = time.monotonic,
        success_ttl: float = SUCCESS_TTL_S,
        failure_backoff: float = FAILURE_BACKOFF_S,
        subprocess_timeout: float = SUBPROCESS_TIMEOUT_S,
    ):
        self._run = run
        self._time_fn = time_fn
        self._success_ttl = success_ttl
        self._failure_backoff = failure_backoff
        self._subprocess_timeout = subprocess_timeout

        self._condition = threading.Condition()
        self._result: ProbeResult = _UNKNOWN_RESULT
        self._result_at: float = float("-inf")
        self._last_failure_at: float = float("-inf")
        self._refreshing = False

    def get_running(self) -> List[str]:
        return list(self.get_result().distros)

    def get_result(self) -> ProbeResult:
        with self._condition:
            now = self._time_fn()
            if self._is_fresh(now):
                return self._result
            while self._refreshing:
                self._condition.wait()
                now = self._time_fn()
                if self._is_fresh(now):
                    return self._result
            now = self._time_fn()
            if self._is_fresh(now):
                return self._result
            self._refreshing = True
        try:
            return self._do_refresh()
        finally:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()

    def _is_fresh(self, now: float) -> bool:
        return self._result.status is not ProbeStatus.UNKNOWN and (now - self._result_at) < self._success_ttl

    def _do_refresh(self) -> ProbeResult:
        try:
            result = self._run(
                ["wsl.exe", "--list", "--running", "--quiet"],
                capture_output=True,
                timeout=self._subprocess_timeout,
                check=False,
                creationflags=_NO_CONSOLE_FLAGS,
            )
        except (OSError, subprocess.TimeoutExpired):
            now = self._time_fn()
            with self._condition:
                self._result = _UNKNOWN_RESULT
                self._result_at = now
                self._last_failure_at = now
                return self._result

        raw = result.stdout
        text = raw.decode("utf-16-le", errors="ignore") if isinstance(raw, bytes) else raw
        names = frozenset(line.strip() for line in text.splitlines() if line.strip())
        now = self._time_fn()
        with self._condition:
            status = ProbeStatus.CONFIRMED if names else ProbeStatus.EMPTY
            self._result = ProbeResult(status=status, distros=names)
            self._result_at = now
            return self._result
```
Now wire it into `tokitty/__main__.py`. Import at the top: `from tokitty.distro_probe import RunningDistroProbe`. In `run_gui`, before the `for index, account in enumerate(accounts or [None]):` loop:
```python
    distro_probe = RunningDistroProbe()
```
Inside the loop, update the `ActivityWatcher` construction:
```python
        watcher = ActivityWatcher(
            sessions_dir, ActivityTracker(), distro_name=distro_name,
            list_running_distros_fn=distro_probe.get_running,
        )
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_distro_probe.py tests/test_main.py -v`.
- [ ] Step 5: Commit.
```
git add tokitty/distro_probe.py tokitty/__main__.py tests/test_distro_probe.py
git commit -m "distro_probe: shared single-flight cache for running WSL distros"
```

---

### Task 12: Manual path validation

**Goal:** Validate a manually entered "Claude config directory" before any persistence or hook call, so a typo never creates a real directory tree via `install_hooks_for_dir`'s bare `mkdir(parents=True, exist_ok=True)`. Depends on Task 5 for canonicalization.

**Files:**
- Create: `tokitty/manual_path.py`
- Test: `tests/test_manual_path.py`

**Acceptance Criteria:**
- [ ] An unexpanded `~/.claude-work` is expanded via `os.path.expanduser` before any other check.
- [ ] A relative path is rejected with a message naming the problem, not silently accepted.
- [ ] A POSIX-shaped path (`/home/nick/.claude-work`) that is not a recognized WSL UNC form is validated as Windows-local (matching the existing resolution rule: only `\\wsl$\` / `\\wsl.localhost\` are recognized as WSL).
- [ ] Entering the credentials file itself (`...\.credentials.json`) instead of its containing directory is detected and the parent directory used.
- [ ] `\\wsl$\Ubuntu\home\nick\.claude` and `\\wsl.localhost\Ubuntu\home\nick\.claude` validate to the same canonical result (via `canonicalize_locator`, Task 5).
- [ ] A missing `.credentials.json` produces a rejection naming the expected file, not a generic error.
- [ ] A `.credentials.json` that parses but has no `claudeAiOauth` object is rejected; one that has an expired-looking token (any shape of `claudeAiOauth`) is accepted -- token validity is not checked.
- [ ] A WSL path's credentials file is read through `wsl_probe.read_wsl_credentials` (the distro-aware subprocess reader), never by touching the UNC path directly.
- [ ] A path that canonicalizes to an already-active account's locator is rejected with an "already added" message.

**Verify:**
```
python3 -m pytest tests/test_manual_path.py -v
```
Expected: all pass, including the wsl$/wsl.localhost equivalence case and the already-added duplicate rejection.

**Steps:**
- [ ] Step 1: Write the failing tests.
```python
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
```
- [ ] Step 2: Run and see it fail. `ModuleNotFoundError: No module named 'tokitty.manual_path'`.
- [ ] Step 3: Implement. Create `tokitty/manual_path.py`:
```python
"""Validation for the Accounts manager's manual "add by path" row. See
docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md, Manual
path validation.
"""
from __future__ import annotations

import json
import os
import posixpath
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from tokitty.accounts import canonicalize_locator, parse_wsl_unc


@dataclass(frozen=True)
class PathValidationResult:
    ok: bool
    config_dir: Optional[str] = None
    error: Optional[str] = None


def _strip_credentials_filename(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.endswith("/.credentials.json"):
        return normalized.rsplit("/", 1)[0]
    return path


def _parses_as_oauth(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("claudeAiOauth"), dict)


def _check_wsl_credentials(distro: str, posix_dir: str, run: Callable) -> PathValidationResult:
    from tokitty.wsl_probe import read_wsl_credentials

    creds_path = posix_dir.rstrip("/") + "/.credentials.json"
    try:
        text = read_wsl_credentials(distro, creds_path, run=run)
    except Exception:
        return PathValidationResult(ok=False, error=f"No .credentials.json found at {distro}:{posix_dir}.")
    if not _parses_as_oauth(text):
        return PathValidationResult(
            ok=False, error=f"{distro}:{creds_path} is not a valid Claude Code credentials file."
        )
    return PathValidationResult(ok=True)


def validate_manual_path(
    raw: str,
    active_config_dirs: List[str],
    run: Callable = subprocess.run,
) -> PathValidationResult:
    """Normalize, canonicalize, and check a manually entered "Claude
    config directory" before any persistence or hook call."""
    expanded = os.path.expanduser(raw.strip())
    if not expanded:
        return PathValidationResult(ok=False, error="Enter a Claude config directory.")

    candidate = _strip_credentials_filename(expanded)

    unc = parse_wsl_unc(candidate)
    if unc is not None:
        distro, posix_dir = unc
        if not posixpath.isabs(posix_dir):
            return PathValidationResult(ok=False, error="Path must be absolute.")
        wsl_result = _check_wsl_credentials(distro, posix_dir, run=run)
        if not wsl_result.ok:
            return wsl_result
    else:
        path = Path(candidate)
        if not path.is_absolute():
            return PathValidationResult(
                ok=False,
                error=f"'{raw}' is not an absolute path. Enter a full Claude config directory.",
            )
        creds = path / ".credentials.json"
        if not creds.is_file():
            return PathValidationResult(ok=False, error=f"No .credentials.json found in {candidate}.")
        if not _parses_as_oauth(creds.read_text(encoding="utf-8")):
            return PathValidationResult(
                ok=False, error=f"{creds} is not a valid Claude Code credentials file."
            )

    try:
        locator = canonicalize_locator(candidate)
    except ValueError as exc:
        return PathValidationResult(ok=False, error=str(exc))

    for existing in active_config_dirs:
        try:
            if canonicalize_locator(existing) == locator:
                return PathValidationResult(ok=False, error="This account is already added.")
        except ValueError:
            continue

    return PathValidationResult(ok=True, config_dir=candidate)
```
- [ ] Step 4: Run and see it pass.
- [ ] Step 5: Commit.
```
git add tokitty/manual_path.py tests/test_manual_path.py
git commit -m "manual_path: validate the Accounts manager's manual add-by-path row"
```

---

### Task 13: `accounts_ui.py`, the manager dialog

**Goal:** The persistent Accounts manager Toplevel: list rows with Add / Rename / Remove, singleton window behavior, disabled Remove at one account, slug-based rename, random look for genuinely new accounts, both restart notices. Depends on Tasks 2, 4, 5, 12.

**Files:**
- Create: `tokitty/accounts_ui.py`
- Modify: `tokitty/customize.py` (add `rename_account`)
- Test: `tests/test_accounts_ui.py`, `tests/test_customize.py`

**Acceptance Criteria:**
- [ ] `rename_account(state_dir, slug, label)` writes only `customization.json`'s `label` field for `slug`, never touching `accounts.json`.
- [ ] `build_row_specs(accounts, customization_store)` (pure, no Tk) returns one display row per account: label (falls back to a short, non-slug placeholder like `"Cat N"` when `Customization.label` is blank, never the raw slug), and whether Remove is enabled for that row (`False` iff `len(accounts) == 1`).
- [ ] `AccountsManager.open(root, state_dir)` is a singleton per root: calling it twice while the dialog is open raises the existing Toplevel instead of creating a second one (`@pytest.mark.gui`).
- [ ] Opening the manager reloads `accounts.json` from disk immediately before every save (so two independently opened dialogs cannot clobber each other) -- verified by a pure function `reconcile_before_save(state_dir, in_memory_accounts)` that reloads and re-diffs rather than trusting stale in-memory state.
- [ ] Add: validates via `validate_manual_path`, assigns a slug via `assign_identity_slug`, rolls a random look via `random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))` for a genuinely new account, or absorbs the implicit default via `absorb_implicit_default` when this is the first explicit account (`accounts.json` did not exist before this Add), then calls `apply_account_mutation(..., op="install")`.
- [ ] Add on a canonical duplicate of an already-active account reports "already added" (via `validate_manual_path`) and does not create a second row.
- [ ] Remove is disabled (button/row state) when it is the last account; calling the remove handler on the last account is a no-op, not an exception.
- [ ] Remove calls `apply_account_mutation(..., op="remove")` and leaves the removed account's `customization.json` entry in place (never deletes it).
- [ ] `rename_account` is called by slug, not by row position (`build_row_specs`' row order is irrelevant to which entry gets updated).
- [ ] The dialog shows two distinct restart notices: one for Tokitty itself (new panes, pollers, watchers) and one for any already-open Claude Code session (hook changes need a session restart).

**Verify:**
```
python3 -m pytest tests/test_accounts_ui.py tests/test_customize.py -k "rename_account or build_row_specs or reconcile_before_save" -v
```
Expected: all pass. (Full Toplevel interaction tests are `@pytest.mark.gui`; run those separately per Global Constraints: `xvfb-run -a pytest -m gui`.)

**Steps:**
- [ ] Step 1: Write the failing tests. Add to `tests/test_customize.py`:
```python
from tokitty.customize import rename_account


def test_rename_account_sets_label_only(tmp_path):
    save_customization(tmp_path, {"acct-v1-abc": Customization(colorway="black", pattern="tuxedo")})
    rename_account(tmp_path, "acct-v1-abc", "Personal")
    result = load_customization(tmp_path)
    assert result["acct-v1-abc"].label == "Personal"
    assert result["acct-v1-abc"].colorway == "black"
```
Create `tests/test_accounts_ui.py`:
```python
from tokitty.accounts import Account
from tokitty.accounts_ui import build_row_specs, reconcile_before_save
from tokitty.customize import Customization


def test_build_row_specs_remove_disabled_at_one_account():
    accounts = [Account(name="acct-v1-a", config_dir="/home/u/.claude")]
    rows = build_row_specs(accounts, {})
    assert len(rows) == 1
    assert rows[0].remove_enabled is False


def test_build_row_specs_remove_enabled_above_one_account():
    accounts = [
        Account(name="acct-v1-a", config_dir="/home/u/.claude-a"),
        Account(name="acct-v1-b", config_dir="/home/u/.claude-b"),
    ]
    rows = build_row_specs(accounts, {})
    assert all(row.remove_enabled for row in rows)


def test_build_row_specs_label_falls_back_without_showing_the_slug():
    accounts = [Account(name="acct-v1-deadbeef", config_dir="/home/u/.claude")]
    rows = build_row_specs(accounts, {})
    assert "acct-v1-deadbeef" not in rows[0].display_label


def test_build_row_specs_uses_stored_label_when_present():
    accounts = [Account(name="acct-v1-a", config_dir="/home/u/.claude")]
    store = {"acct-v1-a": Customization(colorway="black", pattern="tuxedo", label="Personal")}
    rows = build_row_specs(accounts, store)
    assert rows[0].display_label == "Personal"


def test_reconcile_before_save_reloads_from_disk(tmp_path):
    from tokitty.accounts import save_accounts

    save_accounts(tmp_path, [Account(name="acct-v1-a", config_dir="/home/u/.claude-a")])
    stale_in_memory = []  # a second dialog that never saw the first dialog's write
    reconciled = reconcile_before_save(tmp_path, stale_in_memory)
    assert [a.name for a in reconciled] == ["acct-v1-a"]
```
- [ ] Step 2: Run and see it fail. `ModuleNotFoundError: No module named 'tokitty.accounts_ui'`.
- [ ] Step 3: Implement. Add to `tokitty/customize.py`:
```python
def rename_account(state_dir: Path, slug: str, label: str) -> None:
    """Rename operates on the stable identity slug, never on row
    position or accounts.json's "name" field -- see the Accounts
    manager's Rename flow, which must not confuse a live pane's index
    with a manager row's index."""
    store = load_customization(state_dir)
    current = store.get(slug, Customization())
    store[slug] = replace(current, label=label)
    save_customization(state_dir, store)
```
Create `tokitty/accounts_ui.py`:
```python
"""The persistent Accounts manager dialog: add/rename/remove accounts
without hand-editing accounts.json. Modeled on
TokittyWindow._open_customize_dialog (ui.py:404-438) for the Toplevel
shape, and _open_rename_dialog (ui.py:440-446) for simpledialog use.
See docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md.
"""
from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import simpledialog
from typing import Dict, List, Optional

from tokitty.accounts import (
    Account,
    canonicalize_locator,
    load_accounts,
    load_identity_history,
    save_identity_history,
    assign_identity_slug,
)
from tokitty.customize import Customization, rename_account, load_customization, save_customization
from tokitty.hooks_install import apply_account_mutation
from tokitty.manual_path import validate_manual_path
from tokitty.migration import absorb_implicit_default
from tokitty.randomize import random_look
from tokitty import sprites

_manager_instances: Dict[int, "AccountsManager"] = {}


@dataclass(frozen=True)
class RowSpec:
    slug: str
    config_dir: str
    display_label: str
    remove_enabled: bool


def _fallback_label(index: int) -> str:
    return f"Cat {index + 1}"


def build_row_specs(accounts: List[Account], customization_store: Dict[str, Customization]) -> List[RowSpec]:
    """Pure, Tk-free: one display row per account. Never shows the raw
    slug as a fallback label -- it's an opaque SHA-256-derived string."""
    remove_enabled = len(accounts) > 1
    rows = []
    for index, account in enumerate(accounts):
        custom = customization_store.get(account.name)
        label = custom.label if custom and custom.label else _fallback_label(index)
        rows.append(RowSpec(
            slug=account.name, config_dir=account.config_dir,
            display_label=label, remove_enabled=remove_enabled,
        ))
    return rows


def reconcile_before_save(state_dir: Path, in_memory_accounts: List[Account]) -> List[Account]:
    """Reload accounts.json immediately before every save, so two
    independently opened dialogs cannot silently clobber each other's
    changes with a stale in-memory list."""
    on_disk = load_accounts(state_dir)
    return on_disk if on_disk is not None else in_memory_accounts


def _run_mutation_off_thread(state_dir: Path, accounts: List[Account], op: str,
                              config_dir: str, on_done) -> None:
    def worker():
        result = apply_account_mutation(state_dir, accounts, op, config_dir)
        on_done(result)

    threading.Thread(target=worker, daemon=True).start()


class AccountsManager:
    """Singleton Toplevel per root: AccountsManager.open() raises the
    existing dialog instead of creating a second one."""

    def __init__(self, root: tk.Tk, state_dir: Path):
        self.root = root
        self.state_dir = state_dir
        self.toplevel = tk.Toplevel(root)
        self.toplevel.title("Accounts")
        self.toplevel.transient(root)
        self.toplevel.resizable(False, False)
        self.toplevel.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()

    @classmethod
    def open(cls, root: tk.Tk, state_dir: Path) -> "AccountsManager":
        key = id(root)
        existing = _manager_instances.get(key)
        if existing is not None and existing.toplevel.winfo_exists():
            existing.toplevel.lift()
            existing.toplevel.focus_force()
            return existing
        manager = cls(root, state_dir)
        _manager_instances[key] = manager
        return manager

    def _on_close(self) -> None:
        _manager_instances.pop(id(self.root), None)
        self.toplevel.destroy()

    def _build(self) -> None:
        self._refresh_rows()
        tk.Label(
            self.toplevel,
            text="Tokitty restart needed for new panes. Claude Code session restart needed for hooks.",
            wraplength=360, justify="left",
        ).pack(padx=8, pady=(4, 10))
        tk.Button(self.toplevel, text="Add…", command=self._on_add).pack(padx=8, pady=(0, 10))

    def _refresh_rows(self) -> None:
        for child in self.toplevel.winfo_children():
            if getattr(child, "_accounts_row", False):
                child.destroy()
        accounts = load_accounts(self.state_dir) or []
        store = load_customization(self.state_dir)
        for row in build_row_specs(accounts, store):
            frame = tk.Frame(self.toplevel)
            frame._accounts_row = True
            tk.Label(frame, text=row.display_label).pack(side="left", padx=4)
            tk.Button(frame, text="Rename…", command=lambda s=row.slug: self._on_rename(s)).pack(side="left")
            remove_state = "normal" if row.remove_enabled else "disabled"
            tk.Button(frame, text="Remove", state=remove_state,
                      command=lambda s=row.slug, c=row.config_dir: self._on_remove(s, c)).pack(side="left")
            frame.pack(fill="x", padx=8, pady=2)

    def _on_rename(self, slug: str) -> None:
        result = simpledialog.askstring("Rename", "Cat name:", parent=self.toplevel)
        if result is not None:
            rename_account(self.state_dir, slug, result)
            self._refresh_rows()

    def _on_remove(self, slug: str, config_dir: str) -> None:
        accounts = load_accounts(self.state_dir) or []
        if len(accounts) <= 1:
            return
        remaining = [a for a in accounts if a.name != slug]
        remaining = reconcile_before_save(self.state_dir, remaining)
        remaining = [a for a in remaining if a.name != slug]

        def on_done(result):
            self.toplevel.after(0, self._refresh_rows)

        _run_mutation_off_thread(self.state_dir, remaining, "remove", config_dir, on_done)

    def _on_add(self) -> None:
        raw = simpledialog.askstring("Add account", "Claude config directory:", parent=self.toplevel)
        if not raw:
            return
        accounts = load_accounts(self.state_dir) or []
        active_dirs = [a.config_dir for a in accounts]
        validation = validate_manual_path(raw, active_config_dirs=active_dirs)
        if not validation.ok:
            tk.messagebox.showerror("Add account", validation.error, parent=self.toplevel)
            return

        history = load_identity_history(self.state_dir)
        locator = canonicalize_locator(validation.config_dir)
        taken = set(history.values())
        slug, history = assign_identity_slug(locator, taken, history)
        save_identity_history(self.state_dir, history)

        was_implicit_only = not accounts
        new_account = Account(name=slug, config_dir=validation.config_dir)
        new_accounts = reconcile_before_save(self.state_dir, accounts) + [new_account]

        store = load_customization(self.state_dir)
        if was_implicit_only:
            store = absorb_implicit_default(store, slug)
        else:
            colorway, pattern = random_look(list(sprites.COLORWAYS), list(sprites.PATTERNS))
            store[slug] = Customization(colorway=colorway, pattern=pattern)
        save_customization(self.state_dir, store)

        def on_done(result):
            self.toplevel.after(0, self._refresh_rows)

        _run_mutation_off_thread(self.state_dir, new_accounts, "install", validation.config_dir, on_done)
```
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_accounts_ui.py tests/test_customize.py -v` (pure-function tests). Then `xvfb-run -a pytest -m gui` for any `@pytest.mark.gui` Toplevel tests added alongside.
- [ ] Step 5: Commit.
```
git add tokitty/accounts_ui.py tokitty/customize.py tests/test_accounts_ui.py tests/test_customize.py
git commit -m "accounts_ui: manager dialog with add/rename/remove"
```

---

### Task 14: Menu entry and wiring

**Goal:** Add an "Accounts…" entry to the single menu model so it appears in both the Tk right-click menu and the pystray tray menu, wire its callback through `run_gui` following the existing seam pattern, and repoint the ambiguous-credentials status text at it. Depends on Task 13.

**Files:**
- Modify: `tokitty/menu.py:26-76` (`build_menu`), `tokitty/ui.py` (`build_menu_model`, new `on_open_accounts` seam), `tokitty/__main__.py:289` (status text), `:451`/`:501`/`:521`/`:530`-adjacent (new callback assignment)
- Test: `tests/test_menu.py`, `tests/test_main.py`

**Acceptance Criteria:**
- [ ] `build_menu` gains an `on_open_accounts: Optional[Callable[[], None]] = None` parameter; when provided, an `"Accounts…"` `MenuItem` is appended (positioned after "Rename…" and before the separator that precedes "Refresh now").
- [ ] Omitting `on_open_accounts` (the default) omits the item entirely, so every existing `build_menu` caller and test is unaffected.
- [ ] `TokittyWindow` exposes `self.on_open_accounts: Optional[Callable[[], None]] = None`, assigned externally by `run_gui`, matching the `on_refresh_requested`/`on_randomize`/`on_quit`/`on_toggle_tray` pattern.
- [ ] `build_menu_model` passes `on_open_accounts=self.on_open_accounts` through to `build_menu`, so "Accounts…" appears in both the Tk menu and (via `TrayManager`'s reuse of `build_menu_model`) the tray menu.
- [ ] `run_gui` assigns `window.on_open_accounts = lambda: AccountsManager.open(root, state_dir)`.
- [ ] The ambiguous-credentials status hint at `__main__.py:289` no longer tells the user to set an environment variable; it names the Accounts entry instead.

**Verify:**
```
python3 -m pytest tests/test_menu.py tests/test_main.py -v
```
Expected: all pass, including a new test asserting "Accounts…" appears exactly once when `on_open_accounts` is supplied and not at all when it is omitted.

**Steps:**
- [ ] Step 1: Write the failing tests. Add to `tests/test_menu.py`:
```python
def test_accounts_item_appears_when_callback_given():
    items = build_menu(
        colorways=["orange"], patterns=["tabby"],
        current_colorway=lambda: "orange", current_pattern=lambda: "tabby",
        on_colorway=lambda n: None, on_pattern=lambda n: None,
        on_customize=lambda: None, on_rename=lambda: None, on_refresh=lambda: None,
        always_on_top=lambda: False, on_toggle_always_on_top=lambda: None,
        on_quit=lambda: None, on_open_accounts=lambda: None,
    )
    labels = [item.label for item in items]
    assert labels.count("Accounts…") == 1


def test_accounts_item_omitted_when_callback_not_given():
    items = build_menu(
        colorways=["orange"], patterns=["tabby"],
        current_colorway=lambda: "orange", current_pattern=lambda: "tabby",
        on_colorway=lambda n: None, on_pattern=lambda n: None,
        on_customize=lambda: None, on_rename=lambda: None, on_refresh=lambda: None,
        always_on_top=lambda: False, on_toggle_always_on_top=lambda: None,
        on_quit=lambda: None,
    )
    labels = [item.label for item in items]
    assert "Accounts…" not in labels
```
Add to `tests/test_main.py`:
```python
def test_ambiguous_credentials_hint_points_at_accounts_not_env_var():
    from tokitty.__main__ import hints  # or the module-level dict holding these hints
    assert "TOKITTY_CREDENTIALS" not in hints["ambiguous_credentials"]
    assert "Accounts" in hints["ambiguous_credentials"]
```
(Adjust the import path to wherever the `hints` dict actually lives after re-reading `__main__.py:287-290` at implementation time -- it is a local dict inside `_display_state_for`, so this test may need to call the enclosing function with a crafted `PollResult(status="ambiguous_credentials")` and assert on the returned `hint_text` instead of importing `hints` directly.)
- [ ] Step 2: Run and see it fail. `TypeError: build_menu() got an unexpected keyword argument 'on_open_accounts'`.
- [ ] Step 3: Implement. In `tokitty/menu.py`, add the parameter and item:
```python
def build_menu(
    *,
    colorways: List[str],
    patterns: List[str],
    current_colorway: Callable[[], str],
    current_pattern: Callable[[], str],
    on_colorway: Callable[[str], None],
    on_pattern: Callable[[str], None],
    on_customize: Callable[[], None],
    on_rename: Callable[[], None],
    on_refresh: Callable[[], None],
    always_on_top: Callable[[], bool],
    on_toggle_always_on_top: Callable[[], None],
    on_quit: Callable[[], None],
    tray_enabled: Optional[Callable[[], bool]] = None,
    on_toggle_tray: Optional[Callable[[], None]] = None,
    on_randomize: Optional[Callable[[], None]] = None,
    surprise_me: Optional[Callable[[], bool]] = None,
    on_toggle_surprise: Optional[Callable[[], None]] = None,
    on_open_accounts: Optional[Callable[[], None]] = None,
) -> List[MenuItem]:
    ...
    items += [
        MenuItem(label="Customize…", action=on_customize),
        MenuItem(label="Rename…", action=on_rename),
    ]
    if on_open_accounts is not None:
        items.append(MenuItem(label="Accounts…", action=on_open_accounts))
    items += [
        MenuItem(separator=True),
        MenuItem(label="Refresh now", action=on_refresh),
        MenuItem(label="Always in front", action=on_toggle_always_on_top, checkbox=always_on_top),
    ]
```
In `tokitty/ui.py`, add the seam in `TokittyWindow.__init__` (alongside `self.on_refresh_requested`):
```python
        self.on_open_accounts: Optional[Callable[[], None]] = None
```
Update `build_menu_model` to pass it through:
```python
            on_open_accounts=self.on_open_accounts,
```
In `tokitty/__main__.py`, add the wiring in `run_gui` near the other post-construction callback assignments:
```python
    from tokitty.accounts_ui import AccountsManager

    def open_accounts() -> None:
        AccountsManager.open(root, state_dir)

    window.on_open_accounts = open_accounts
```
Update the `hints` dict entry at `__main__.py:289` (or wherever it lives after reading the current source):
```python
        "ambiguous_credentials": "multiple installs, use Accounts...",
```
- [ ] Step 4: Run and see it pass.
- [ ] Step 5: Commit.
```
git add tokitty/menu.py tokitty/ui.py tokitty/__main__.py tests/test_menu.py tests/test_main.py
git commit -m "menu: add Accounts... entry to both the right-click and tray menus"
```

---

### Task 15: First-run auto-open and the macOS virtual Keychain row

**Goal:** Auto-open the Accounts manager on startup only when `accounts.json` is absent and credential-resolution precedence would otherwise hit an ambiguous WSL case; run discovery asynchronously after `tk.Tk()` succeeds; bypass all of this under `TOKITTY_DEBUG_ACCOUNTS`; and show a read-only virtual account row on macOS when there is no credentials file on disk. Depends on Task 14.

**Files:**
- Create: `tokitty/startup.py`
- Modify: `tokitty/__main__.py` (`run_gui`), `tokitty/accounts_ui.py` (macOS virtual row)
- Test: `tests/test_startup.py`

**Acceptance Criteria:**
- [ ] `should_auto_open(accounts_state, env_override_set, home_relative_exists, keychain_available, platform)` is a pure, injectable decision function: returns `True` only when `accounts_state == "absent"` and none of `env_override_set` / `home_relative_exists` / `keychain_available` would win resolution first (mirroring the precedence in `credentials.py:128-142`: `TOKITTY_CREDENTIALS`, then `~/.claude`, then Keychain, then WSL).
- [ ] `should_auto_open` returns `False` whenever `accounts_state != "absent"`.
- [ ] `should_auto_open` returns `False` when `env_override_set` or `home_relative_exists` or (on darwin) `keychain_available` is `True`, even if WSL discovery would otherwise find more than one match -- those sources win resolution before WSL is ever consulted.
- [ ] The function takes an already-computed WSL match count (from the async discovery result) as an argument, not global state, so it is unit-testable without touching WSL or Tk.
- [ ] `run_gui` launches discovery asynchronously (a background thread) only after `tk.Tk()` has succeeded, and reuses that result for `resolve_activity_sessions` rather than scanning twice.
- [ ] The `TOKITTY_DEBUG_ACCOUNTS` branch bypasses `should_auto_open` and discovery entirely (verified: no call to the discovery function under that branch).
- [ ] Startup auto-open logic lives in `run_gui`, not in `TokittyWindow.__init__` -- the 5 `gui`-marked tests (`test_smoke_gui.py:44`, `test_ui_layout.py:95,120,138,158`) construct `TokittyWindow` directly and must keep passing unmodified with zero WSL calls.
- [ ] On macOS, when `load_accounts` returns `None`/absent and no credentials file exists on disk, `AccountsManager` shows one virtual row: label `"Default macOS account (Keychain)"`, Add and Remove disabled, Rename edits the `"default"` customization key directly (not via `rename_account`'s slug path). It never calls `save_accounts`.

**Verify:**
```
python3 -m pytest tests/test_startup.py -v && python3 -m pytest -m gui
```
Expected: `test_startup.py` passes headless; the 5 pre-existing gui tests still pass under `xvfb-run -a pytest -m gui` with no WSL subprocess calls (verify by running with `WSL_CREDENTIALS_TEST_GUARD` unset and confirming no `wsl.exe` invocation is attempted, e.g. by temporarily renaming `wsl.exe` off PATH in the CI sandbox has no effect on gui test results).

**Steps:**
- [ ] Step 1: Write the failing tests. Create `tests/test_startup.py`:
```python
from tokitty.startup import should_auto_open


def test_no_auto_open_when_accounts_file_exists():
    assert should_auto_open(
        accounts_state="valid_non_empty", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is False


def test_no_auto_open_when_accounts_file_malformed():
    assert should_auto_open(
        accounts_state="malformed", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is False


def test_auto_open_when_absent_and_wsl_finds_two():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=2,
    ) is True


def test_no_auto_open_when_absent_but_wsl_finds_only_one():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=1,
    ) is False


def test_no_auto_open_when_env_override_wins_first():
    assert should_auto_open(
        accounts_state="absent", env_override_set=True,
        home_relative_exists=False, keychain_available=False,
        platform="win32", wsl_match_count=3,
    ) is False


def test_no_auto_open_when_home_relative_wins_first():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=True, keychain_available=False,
        platform="win32", wsl_match_count=3,
    ) is False


def test_no_auto_open_when_keychain_wins_on_darwin():
    assert should_auto_open(
        accounts_state="absent", env_override_set=False,
        home_relative_exists=False, keychain_available=True,
        platform="darwin", wsl_match_count=0,
    ) is False
```
- [ ] Step 2: Run and see it fail. `ModuleNotFoundError: No module named 'tokitty.startup'`.
- [ ] Step 3: Implement. Create `tokitty/startup.py`:
```python
"""Pure, injectable startup decisions for run_gui: whether to auto-open
the Accounts manager, kept separate from TokittyWindow so the 5
gui-marked tests that construct TokittyWindow directly never touch WSL.
See docs/superpowers/specs/2026-08-24-accounts-setup-ui-design.md,
First-run auto-open.
"""
from __future__ import annotations


def should_auto_open(
    accounts_state: str,
    env_override_set: bool,
    home_relative_exists: bool,
    keychain_available: bool,
    platform: str,
    wsl_match_count: int,
) -> bool:
    """True only when accounts.json is absent AND nothing earlier in
    credentials.py's resolution precedence (TOKITTY_CREDENTIALS, then
    ~/.claude, then Keychain on darwin, then WSL) would already resolve
    unambiguously before WSL is even consulted, AND the async WSL
    discovery found more than one usable credential source."""
    if accounts_state != "absent":
        return False
    if env_override_set or home_relative_exists:
        return False
    if platform == "darwin" and keychain_available:
        return False
    return wsl_match_count > 1
```
Now wire it into `tokitty/__main__.py`'s `run_gui`. After `root = tk.Tk()` and `window = TokittyWindow(...)`, and only outside the `TOKITTY_DEBUG_ACCOUNTS` branch:
```python
    from tokitty.startup import should_auto_open

    discovery_result = {"wsl_matches": [], "done": False}

    def run_discovery():
        if sys.platform == "win32":
            from tokitty.wsl_probe import find_all_wsl_credentials
            discovery_result["wsl_matches"] = find_all_wsl_credentials()
        discovery_result["done"] = True
        root.after(0, maybe_auto_open)

    def maybe_auto_open():
        accounts_result = load_accounts_result(state_dir)
        env_override_set = bool(os.environ.get("TOKITTY_CREDENTIALS"))
        home_relative_exists = (Path.home() / ".claude" / ".credentials.json").is_file()
        keychain_available = False
        if sys.platform == "darwin":
            from tokitty.keychain import KEYCHAIN_SERVICE, keychain_item_exists
            keychain_available = keychain_item_exists(KEYCHAIN_SERVICE)
        if should_auto_open(
            accounts_state=accounts_result.state,
            env_override_set=env_override_set,
            home_relative_exists=home_relative_exists,
            keychain_available=keychain_available,
            platform=sys.platform,
            wsl_match_count=len(discovery_result["wsl_matches"]),
        ):
            from tokitty.accounts_ui import AccountsManager
            AccountsManager.open(root, state_dir)

    if not (debug_state or debug_accounts == "2"):
        threading.Thread(target=run_discovery, daemon=True).start()
```
Add `import threading` and `from tokitty.accounts import load_accounts_result` to the top of `__main__.py` if not already present. Place this block after `debug_accounts` is computed but before the `debug_state or debug_accounts == "2"` early-return branch reads it, so the debug branch's early `return 0` skips it entirely (satisfying "bypasses auto-open and discovery entirely").

For the macOS virtual row, add to `tokitty/accounts_ui.py`'s `_refresh_rows`:
```python
    def _refresh_rows(self) -> None:
        for child in self.toplevel.winfo_children():
            if getattr(child, "_accounts_row", False):
                child.destroy()
        accounts = load_accounts(self.state_dir)
        if accounts is None and sys.platform == "darwin" and not self._has_local_credentials_file():
            self._render_virtual_macos_row()
            return
        accounts = accounts or []
        store = load_customization(self.state_dir)
        for row in build_row_specs(accounts, store):
            ...  # unchanged from Task 13

    def _has_local_credentials_file(self) -> bool:
        return (Path.home() / ".claude" / ".credentials.json").is_file()

    def _render_virtual_macos_row(self) -> None:
        frame = tk.Frame(self.toplevel)
        frame._accounts_row = True
        tk.Label(frame, text="Default macOS account (Keychain)").pack(side="left", padx=4)
        tk.Button(frame, text="Rename…", command=self._on_rename_default_key).pack(side="left")
        tk.Button(frame, text="Remove", state="disabled").pack(side="left")
        frame.pack(fill="x", padx=8, pady=2)

    def _on_rename_default_key(self) -> None:
        from tokitty.customize import SINGLE_KEY, load_customization, save_customization, Customization
        from dataclasses import replace

        result = simpledialog.askstring("Rename", "Cat name:", parent=self.toplevel)
        if result is None:
            return
        store = load_customization(self.state_dir)
        current = store.get(SINGLE_KEY, Customization())
        store[SINGLE_KEY] = replace(current, label=result)
        save_customization(self.state_dir, store)
        self._refresh_rows()
```
Add `import sys` to the top of `accounts_ui.py`, and disable the "Add…" button in `_build` when the virtual row is showing (skip building it, or pass a flag through; the `AccountsManager._build` method should check the same condition `_refresh_rows` does and omit the `"Add…"` button entirely on the virtual-row path, since Add is disabled there per the spec).
- [ ] Step 4: Run and see it pass. `python3 -m pytest tests/test_startup.py -v`, then `python3 -m pytest -m gui` (headed, or `xvfb-run -a pytest -m gui`) to confirm the 5 existing gui tests are untouched.
- [ ] Step 5: Commit.
```
git add tokitty/startup.py tokitty/__main__.py tokitty/accounts_ui.py tests/test_startup.py
git commit -m "startup: async first-run auto-open decision, macOS virtual Keychain row"
```

---

### Task 16: Manual Windows verification

**Goal:** This is the repo's established final gate for UI work: launch the real widget on Windows and confirm the whole feature end to end, since no automated test drives an actual grid render, an actual tray click, or an actual hook install against a real Claude Code config dir.

**Files:**
- None (verification only; no code changes).

**Acceptance Criteria:**
- [ ] The "Accounts…" entry appears in both the right-click menu and the pystray tray menu.
- [ ] Adding a second account writes `accounts.json` and installs hooks into the new account's config dir (confirm `settings.json` there gained the tokitty hook entries).
- [ ] The grid renders correctly at N=2 (1 col x 2 rows, 300x256) and N=5 (2 cols x 3 rows, 600x384).
- [ ] Right-clicking a pane in the second column selects the correct pane (not the nearest pane in column 1).
- [ ] The existing cat keeps its look (colorway/pattern) across the 1-to-2 account transition -- this is the identity-key bug's regression check.

**Verify:**
```
TOKITTY_DEBUG_STATE=content pythonw.exe -m tokitty
```
Launch from an elevated or non-elevated PowerShell as appropriate (this is a normal user-session app, not one that needs elevation), from `C:\Tools\tokitty`. Before trusting what appears on screen, confirm the process actually landed in the interactive session rather than an invisible Session 0: `Get-Process pythonw,explorer | Select ProcessName,Id,SessionId` and check `pythonw`'s `SessionId` matches `explorer.exe`'s. If it doesn't match, kill the orphan process and relaunch. Position across restarts is read from and written to `%LOCALAPPDATA%\Tokitty\position.json` -- delete that file between runs if a stale off-screen position from a prior N makes the window hard to find. Expected: the window appears in the correct session, all 5 acceptance criteria above are visually confirmed, and no exception appears in the console.

**Steps:**
- [ ] Step 1: On Windows, from `C:\Tools\tokitty`, run `pythonw.exe -m tokitty` (or `python.exe -m tokitty` to keep a console visible for errors) with a single account already configured, and confirm the baseline single-pane window still opens normally.
- [ ] Step 2: Right-click the pane and open "Accounts…"; confirm the dialog opens and shows the one existing account with Remove disabled.
- [ ] Step 3: Use Add… to add a second account, pointing at a real second Claude Code config directory (or a WSL one via its UNC path). Confirm the dialog reports success and shows both restart notices.
- [ ] Step 4: Inspect `%LOCALAPPDATA%\Tokitty\accounts.json` and confirm both accounts are present with slug-shaped names. Inspect the new account's `settings.json` and confirm the tokitty hook entries were installed.
- [ ] Step 5: Restart Tokitty. Confirm the window is now a 1x2 or 2x1 grid at 300x256 (per the layout formula), both panes render, and the originally-existing cat's colorway and pattern are unchanged from before the Add (the identity-key regression check).
- [ ] Step 6: Add three more accounts (N=5 total) through the manager, restart, and confirm the window is a 2-col x 3-row grid at 600x384.
- [ ] Step 7: Right-click a pane visually in the second column (e.g. account 2 or account 4) and confirm the context menu's Rename…/Customize… operate on that pane, not on column 1's pane at the same row.
- [ ] Step 8: Remove an account down to N=4, confirm the removed account's row disappears from the manager, the live window drops to a 1x4 grid on restart, and the removed account's look survives in `customization.json` (inspect the file directly; it should still contain the removed slug's entry).
- [ ] Step 9: Report the outcome of all 5 acceptance criteria back in the plan-execution log. This task carries no `userGate` metadata; it is a plain task, not a gated one.
