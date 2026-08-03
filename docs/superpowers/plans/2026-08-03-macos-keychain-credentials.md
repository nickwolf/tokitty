# macOS Keychain Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let tokitty resolve Claude Code's OAuth credentials from the macOS login Keychain, so the app works on macOS where no `.credentials.json` file exists.

**Architecture:** A new `KeychainCredentialsSource` variant joins the existing `CredentialsSource` union, read via the `security` CLI in a new subprocess-only `tokitty/keychain.py` that mirrors `wsl_probe.py`. A `CredentialLoader` caches Keychain reads until token expiry and makes access failures sticky so the poller's backoff cannot produce a prompt storm.

**Tech Stack:** Python 3.10+, stdlib only (`subprocess`), pytest, ruff 0.15.22.

**Spec:** `docs/superpowers/specs/2026-08-03-macos-keychain-credentials-design.md`

## Global Constraints

- **Branch:** `macos-keychain` (already created; the spec is committed there as 288d2d8).
- **Python floor:** `requires-python = ">=3.10"`. CI matrix is ubuntu/macos/windows × py3.10/3.14.
- **No new dependencies.** Runtime deps stay exactly `["pystray", "Pillow"]`.
- **Every darwin-specific test monkeypatches `sys.platform`; never `skipif`.** A `skipif(sys.platform != "darwin")` would silently drop this code from five of six matrix cells.
- **No test may invoke the real `security` binary or touch the real Keychain.** Inject `run`.
- **Import cycle rule:** `keychain.py` imports from `tokitty.credentials` at module scope; `credentials.py` imports from `tokitty.keychain` **only lazily, inside functions**. This is the existing `wsl_probe.py` pattern (`wsl_probe.py:13` vs `credentials.py:97`) and reversing it creates a circular import.
- **Never pass `creationflags` to `subprocess.run` in `keychain.py`.** It is Windows-only and raises `ValueError` on POSIX. (`wsl_probe.py` passes it because it only ever runs on Windows.)
- **`ruff` target-version is `py310`;** all modules use `from __future__ import annotations` and `Optional[...]` rather than `X | Y` in runtime positions.
- **Exit code 44** = `errSecItemNotFound` (verified 2026-08-03).
- **Keychain service string:** `"Claude Code-credentials"` exactly.
- Run `pytest` (headless, `-m 'not gui'` via `addopts`) after every task; `pytest -m gui` and `ruff check .` before the final commit.

---

### Task 1: `tokitty/keychain.py` and the `KeychainAccessError` exception

**Files:**
- Modify: `tokitty/credentials.py:15-21` (add exception class alongside the existing two)
- Create: `tokitty/keychain.py`
- Test: `tests/test_keychain.py`

**Interfaces:**
- Consumes: `CredentialsError` from `tokitty.credentials`.
- Produces:
  - `tokitty.credentials.KeychainAccessError(CredentialsError)`
  - `tokitty.keychain.KEYCHAIN_SERVICE: str = "Claude Code-credentials"`
  - `tokitty.keychain.EXIT_ITEM_NOT_FOUND: int = 44`
  - `tokitty.keychain.keychain_item_exists(service: str, account: Optional[str] = None, run: Callable = subprocess.run) -> bool`
  - `tokitty.keychain.read_keychain_secret(service: str, account: Optional[str] = None, run: Callable = subprocess.run) -> str`

- [ ] **Step 1: Add the exception class**

In `tokitty/credentials.py`, directly after the existing `AmbiguousCredentialsError` class (around line 21):

```python
class KeychainAccessError(CredentialsError):
    """Raised when a macOS Keychain item exists but its secret could not be
    read -- user denied the prompt, keychain locked, or any other non-
    "item not found" failure. Distinct from CredentialsError because it is
    the only failure mode that re-prompts, so callers make it sticky."""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_keychain.py`:

```python
import subprocess

import pytest

from tokitty.credentials import CredentialsError, KeychainAccessError
from tokitty.keychain import (
    KEYCHAIN_SERVICE,
    keychain_item_exists,
    read_keychain_secret,
)


class FakeCompletedProcess:
    def __init__(self, stdout: bytes = b"", returncode: int = 0, stderr: bytes = b""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_read_keychain_secret_returns_stdout():
    def fake_run(cmd, **kwargs):
        assert cmd == [
            "security", "find-generic-password", "-s", "Claude Code-credentials", "-w",
        ]
        return FakeCompletedProcess(stdout=b'{"claudeAiOauth": {}}\n')

    assert read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run) == '{"claudeAiOauth": {}}'


def test_read_keychain_secret_includes_account_when_given():
    def fake_run(cmd, **kwargs):
        assert cmd == [
            "security", "find-generic-password",
            "-s", "Claude Code-credentials", "-a", "someuser", "-w",
        ]
        return FakeCompletedProcess(stdout=b"{}")

    read_keychain_secret(KEYCHAIN_SERVICE, account="someuser", run=fake_run)


def test_read_keychain_secret_raises_credentials_error_on_not_found():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=44, stderr=b"could not be found")

    with pytest.raises(CredentialsError) as excinfo:
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)
    # Exit 44 is transient-if-it-happens-during-a-load, so it must NOT be the
    # sticky subclass -- see CredentialLoader in Task 4.
    assert not isinstance(excinfo.value, KeychainAccessError)


def test_read_keychain_secret_raises_keychain_access_error_on_denial():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=128, stderr=b"User canceled")

    with pytest.raises(KeychainAccessError):
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)


def test_read_keychain_secret_wraps_os_error():
    def fake_run(cmd, **kwargs):
        raise OSError("no such binary")

    with pytest.raises(CredentialsError):
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)


def test_read_keychain_secret_wraps_timeout():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="security", timeout=10)

    with pytest.raises(CredentialsError):
        read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)


# The -w flag is what asks for the *secret*, which is what raises a macOS
# dialog. Resolution runs on every poll (~120s), so the existence probe must
# never include it. This is a correctness assertion, not a style one.
def test_keychain_item_exists_never_requests_the_secret():
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeCompletedProcess(stdout=b'svce="Claude Code-credentials"')

    keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run)

    assert "-w" not in seen["cmd"]
    assert seen["cmd"] == [
        "security", "find-generic-password", "-s", "Claude Code-credentials",
    ]


def test_keychain_item_exists_true_on_success():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=0)

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is True


def test_keychain_item_exists_false_when_not_found():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=44)

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is False


# Existence is not in question on an unexpected exit code -- only access to the
# secret is. Returning False here would route the user to the "can't find
# credentials" hint, which is the wrong remedy.
def test_keychain_item_exists_true_on_unexpected_exit_code():
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(returncode=128)

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is True


def test_keychain_item_exists_false_when_security_is_missing():
    def fake_run(cmd, **kwargs):
        raise OSError("no such binary")

    assert keychain_item_exists(KEYCHAIN_SERVICE, run=fake_run) is False


def test_keychain_calls_pass_a_timeout():
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return FakeCompletedProcess(stdout=b"{}")

    read_keychain_secret(KEYCHAIN_SERVICE, run=fake_run)

    assert seen["timeout"] == 10
    assert seen["capture_output"] is True
    assert seen["check"] is False
    # creationflags is Windows-only and raises ValueError on POSIX.
    assert "creationflags" not in seen
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_keychain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tokitty.keychain'`

- [ ] **Step 4: Write the implementation**

Create `tokitty/keychain.py`:

```python
"""macOS Keychain access for Claude Code's OAuth credentials.

Deliberately subprocess-only (mirrors wsl_probe.py): both entry points take an
injectable `run` so tests never invoke a real `security` binary.

The two functions are split on purpose. Asking for a secret (`-w`) can raise a
macOS authorization dialog; asking only for an item's attributes cannot.
resolve_credentials_source() runs on every poll, so it may only ever call
keychain_item_exists(); read_keychain_secret() is reached solely from
load_credentials(). That keeps the prompt confined to one call site.

Imports from tokitty.credentials at module scope, and credentials.py imports
this module lazily inside functions -- same direction as wsl_probe.py, and
reversing it would be a circular import.
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Optional

from tokitty.credentials import CredentialsError, KeychainAccessError

KEYCHAIN_SERVICE = "Claude Code-credentials"

# `security` exits 44 (errSecItemNotFound) when no matching item exists.
EXIT_ITEM_NOT_FOUND = 44


def _base_command(service: str, account: Optional[str]) -> List[str]:
    cmd = ["security", "find-generic-password", "-s", service]
    if account:
        cmd += ["-a", account]
    return cmd


def keychain_item_exists(service: str, account: Optional[str] = None, run: Callable = subprocess.run) -> bool:
    """True if a matching generic-password item exists.

    Attribute-only query -- no `-w`, so it never prompts. Any exit code other
    than 44 counts as "exists": on an unexpected failure the item's existence
    is not what is in doubt, only access to its secret, and reporting False
    would send the caller down the misleading "can't find credentials" path.
    """
    try:
        result = run(_base_command(service, account), capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        # No `security` binary, or it hung: we cannot claim the item exists.
        return False
    return result.returncode != EXIT_ITEM_NOT_FOUND


def read_keychain_secret(service: str, account: Optional[str] = None, run: Callable = subprocess.run) -> str:
    """Return the item's secret. May raise a macOS authorization dialog."""
    try:
        result = run(_base_command(service, account) + ["-w"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not run `security` to read the Keychain: {exc}") from exc

    if result.returncode == EXIT_ITEM_NOT_FOUND:
        raise CredentialsError(f"No '{service}' item in the login Keychain")

    if result.returncode != 0:
        detail = _decode(result.stderr).strip() or f"exit {result.returncode}"
        raise KeychainAccessError(f"Could not read '{service}' from the login Keychain: {detail}")

    return _decode(result.stdout).strip()


def _decode(raw) -> str:
    return raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else (raw or "")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_keychain.py -v`
Expected: PASS (13 tests)

- [ ] **Step 6: Run the full suite and linter**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check .`
Expected: PASS, no new failures (baseline was 333 passed / 5 deselected)

- [ ] **Step 7: Commit**

```bash
git add tokitty/keychain.py tokitty/credentials.py tests/test_keychain.py
git commit -m "feat(keychain): subprocess-only macOS Keychain reader

Attribute-only existence probe (never passes -w, so it cannot prompt) and a
secret read that classifies exit 44 as CredentialsError and every other
non-zero exit as the sticky KeychainAccessError."
```

---

### Task 2: `KeychainCredentialsSource` variant

**Files:**
- Modify: `tokitty/credentials.py:23-40` (dataclasses, union, `describe_source`), `:108-129` (`load_credentials`)
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: `read_keychain_secret`, `KEYCHAIN_SERVICE` from Task 1.
- Produces:
  - `tokitty.credentials.KeychainCredentialsSource(service: str, account: Optional[str] = None)` — frozen dataclass
  - `describe_source(KeychainCredentialsSource(...)) -> "Keychain:<service>"`
  - `load_credentials` accepts the new variant

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_credentials.py`:

```python
def test_describe_source_for_keychain():
    source = credentials.KeychainCredentialsSource(service="Claude Code-credentials")
    assert describe_source(source) == "Keychain:Claude Code-credentials"


def test_load_credentials_reads_from_keychain(monkeypatch):
    monkeypatch.setattr(
        "tokitty.keychain.read_keychain_secret",
        lambda service, account=None: json.dumps({"claudeAiOauth": {"accessToken": "kc"}}),
    )
    source = credentials.KeychainCredentialsSource(service="Claude Code-credentials")

    assert load_credentials(source) == {"accessToken": "kc"}


def test_load_credentials_from_keychain_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr("tokitty.keychain.read_keychain_secret", lambda service, account=None: "not json")
    source = credentials.KeychainCredentialsSource(service="Claude Code-credentials")

    with pytest.raises(CredentialsError):
        load_credentials(source)


def test_load_credentials_from_keychain_passes_account(monkeypatch):
    seen = {}

    def fake_read(service, account=None):
        seen["service"], seen["account"] = service, account
        return json.dumps({"claudeAiOauth": {}})

    monkeypatch.setattr("tokitty.keychain.read_keychain_secret", fake_read)
    load_credentials(credentials.KeychainCredentialsSource(service="svc", account="acct"))

    assert seen == {"service": "svc", "account": "acct"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_credentials.py -k keychain -v`
Expected: FAIL — `AttributeError: module 'tokitty.credentials' has no attribute 'KeychainCredentialsSource'`

- [ ] **Step 3: Add the dataclass and extend the union**

In `tokitty/credentials.py`, after `WslDistroCredentialsSource` (around line 32):

```python
@dataclass(frozen=True)
class KeychainCredentialsSource:
    """macOS login Keychain. `account` is optional: Claude Code stores one item
    per macOS user, so the service name alone is unambiguous in practice."""

    service: str
    account: Optional[str] = None
```

Change the union to include it:

```python
CredentialsSource = Union[LocalCredentialsSource, WslDistroCredentialsSource, KeychainCredentialsSource]
```

- [ ] **Step 4: Extend `describe_source`**

Replace the body of `describe_source` so the Keychain branch comes before the WSL fallthrough:

```python
def describe_source(source: CredentialsSource) -> str:
    if isinstance(source, LocalCredentialsSource):
        return str(source.path)
    if isinstance(source, KeychainCredentialsSource):
        return f"Keychain:{source.service}"
    return f"WSL:{source.distro}:{source.wsl_path}"
```

- [ ] **Step 5: Extend `load_credentials`**

In `load_credentials`, add a branch before the WSL `else`. The result feeds the existing `json.loads` → `claudeAiOauth` code below it unchanged:

```python
    if isinstance(source, LocalCredentialsSource):
        try:
            raw = source.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CredentialsError(f"Could not read credentials file at {source.path}: {exc}") from exc
    elif isinstance(source, KeychainCredentialsSource):
        from tokitty.keychain import read_keychain_secret

        raw = read_keychain_secret(source.service, account=source.account)
    else:
        from tokitty.wsl_probe import read_wsl_credentials

        raw = read_wsl_credentials(source.distro, source.wsl_path)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_credentials.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and linter**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check .`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add tokitty/credentials.py tests/test_credentials.py
git commit -m "feat(credentials): KeychainCredentialsSource variant

The Keychain secret is the same JSON payload the file form holds, so it feeds
the existing json.loads -> claudeAiOauth path with no parsing changes."
```

---

### Task 3: darwin resolution step and platform-aware error messages

**Files:**
- Modify: `tokitty/credentials.py:66-105` (`resolve_credentials_source`)
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: `keychain_item_exists`, `KEYCHAIN_SERVICE` (Task 1); `KeychainCredentialsSource` (Task 2).
- Produces: resolution order `env → file → Keychain (darwin) → WSL (win32) → raise`.

**Note on monkeypatching:** `credentials.py` imports `tokitty.keychain` lazily *inside* the function, so the import resolves at call time. Patch the target as `"tokitty.keychain.keychain_item_exists"`, not as an attribute of `tokitty.credentials`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_credentials.py`:

```python
def _no_files(monkeypatch, tmp_path):
    """Neither credential file exists, so resolution reaches the platform steps."""
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


def test_resolve_uses_keychain_on_darwin_when_no_files(monkeypatch, tmp_path):
    _no_files(monkeypatch, tmp_path)
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr("tokitty.keychain.keychain_item_exists", lambda service, account=None: True)

    source = resolve_credentials_source()

    assert isinstance(source, credentials.KeychainCredentialsSource)
    assert source.service == "Claude Code-credentials"


def test_resolve_prefers_credentials_file_over_keychain_on_darwin(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text('{"claudeAiOauth": {}}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    def boom(service, account=None):
        raise AssertionError("Keychain must not be consulted when the file exists")

    monkeypatch.setattr("tokitty.keychain.keychain_item_exists", boom)

    source = resolve_credentials_source()

    assert isinstance(source, LocalCredentialsSource)


def test_resolve_prefers_env_override_over_keychain_on_darwin(monkeypatch, tmp_path):
    creds = tmp_path / "c.json"
    creds.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(ENV_OVERRIDE, str(creds))
    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    def boom(service, account=None):
        raise AssertionError("Keychain must not be consulted when the env override is set")

    monkeypatch.setattr("tokitty.keychain.keychain_item_exists", boom)

    assert isinstance(resolve_credentials_source(), LocalCredentialsSource)


def test_resolve_raises_on_darwin_when_keychain_item_absent(monkeypatch, tmp_path):
    _no_files(monkeypatch, tmp_path)
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr("tokitty.keychain.keychain_item_exists", lambda service, account=None: False)

    with pytest.raises(CredentialsError) as excinfo:
        resolve_credentials_source()

    message = str(excinfo.value)
    # The message must name both misses; the pre-existing text cited only a
    # file path that can never exist on macOS.
    assert "Keychain" in message
    assert ".credentials.json" in message


def test_resolve_does_not_consult_keychain_on_linux(monkeypatch, tmp_path):
    _no_files(monkeypatch, tmp_path)
    monkeypatch.setattr(credentials.sys, "platform", "linux")

    def boom(service, account=None):
        raise AssertionError("Keychain is macOS-only")

    monkeypatch.setattr("tokitty.keychain.keychain_item_exists", boom)

    with pytest.raises(CredentialsError):
        resolve_credentials_source()


def test_config_dir_message_mentions_files_on_darwin(monkeypatch, tmp_path):
    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    with pytest.raises(CredentialsError) as excinfo:
        resolve_credentials_source(config_dir=str(tmp_path / "nope"))

    assert "single-account" in str(excinfo.value)


def test_config_dir_never_resolves_to_keychain_on_darwin(monkeypatch, tmp_path):
    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    def boom(service, account=None):
        raise AssertionError("accounts.json entries are file-only")

    monkeypatch.setattr("tokitty.keychain.keychain_item_exists", boom)

    with pytest.raises(CredentialsError):
        resolve_credentials_source(config_dir=str(tmp_path / "nope"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_credentials.py -k "darwin or keychain" -v`
Expected: FAIL — resolution raises instead of returning a `KeychainCredentialsSource`

- [ ] **Step 3: Add the darwin source helper**

In `tokitty/credentials.py`, above `resolve_credentials_source`:

```python
def _keychain_source() -> Optional[KeychainCredentialsSource]:
    """The macOS Keychain source, or None off-darwin / when no item exists.

    Uses the attribute-only existence probe, never the secret read: this runs
    on every poll and must not raise a macOS authorization dialog.
    """
    if sys.platform != "darwin":
        return None

    from tokitty.keychain import KEYCHAIN_SERVICE, keychain_item_exists

    if not keychain_item_exists(KEYCHAIN_SERVICE):
        return None
    return KeychainCredentialsSource(service=KEYCHAIN_SERVICE)
```

- [ ] **Step 4: Insert the resolution step and platform-aware final message**

In `resolve_credentials_source`, replace everything from the `home_relative` check to the end of the function:

```python
    home_relative = _home_relative_source()
    if home_relative is not None:
        return home_relative

    keychain = _keychain_source()
    if keychain is not None:
        return keychain

    if sys.platform == "win32":
        from tokitty.wsl_probe import find_wsl_credentials

        distro, wsl_path = find_wsl_credentials()
        return WslDistroCredentialsSource(distro=distro, wsl_path=wsl_path)

    if sys.platform == "darwin":
        from tokitty.keychain import KEYCHAIN_SERVICE

        raise CredentialsError(
            "No Claude Code credentials found: no ~/.claude/.credentials.json and no "
            f"'{KEYCHAIN_SERVICE}' item in your login Keychain. Open Claude Code to sign "
            f"in, or set {ENV_OVERRIDE} to a credentials file."
        )

    raise CredentialsError(
        "No Claude Code credentials found at ~/.claude/.credentials.json. "
        f"Set {ENV_OVERRIDE} to the correct path."
    )
```

- [ ] **Step 5: Add the macOS sentence to the `config_dir` branch**

In the `if config_dir:` block, replace the missing-file raise (around line 82):

```python
        if not candidate.is_file():
            message = f"No credentials file at {candidate} (from accounts.json)"
            if sys.platform == "darwin":
                message += (
                    ". Two-account mode requires credential files; macOS Keychain "
                    "resolution is single-account only."
                )
            raise CredentialsError(message)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_credentials.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and linter**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check .`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add tokitty/credentials.py tests/test_credentials.py
git commit -m "feat(credentials): resolve macOS Keychain after the file sources

Order is env -> file -> Keychain (darwin) -> WSL (win32). The file still wins
for back-compat. accounts.json entries stay file-only and now say so on macOS."
```

---

### Task 4: `CredentialLoader` — expiry cache and sticky access failures

**Files:**
- Modify: `tokitty/credentials.py` (append the class after `is_token_expired`)
- Test: `tests/test_credential_loader.py`

**Interfaces:**
- Consumes: `KeychainCredentialsSource`, `KeychainAccessError`, `describe_source`, `is_token_expired`, `load_credentials`.
- Produces:
  - `tokitty.credentials.CredentialLoader()`
  - `.load(source, load_fn=load_credentials, now_ms=None) -> dict`
  - `.clear_block() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_credential_loader.py`:

```python
import time
from pathlib import Path

import pytest

from tokitty.credentials import (
    CredentialLoader,
    CredentialsError,
    KeychainAccessError,
    KeychainCredentialsSource,
    LocalCredentialsSource,
)

KEYCHAIN = KeychainCredentialsSource(service="Claude Code-credentials")
FUTURE = int(time.time() * 1000) + 3_600_000
PAST = int(time.time() * 1000) - 1_000


class CountingLoader:
    """Counts reads, because the read count IS the feature -- a test asserting
    only the returned dict would pass even if caching did nothing."""

    def __init__(self, creds=None, raises=None):
        self.calls = 0
        self._creds = creds if creds is not None else {"expiresAt": FUTURE}
        self._raises = raises

    def __call__(self, source):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._creds


def test_keychain_read_is_cached_while_token_is_valid():
    loader, fake = CredentialLoader(), CountingLoader()

    first = loader.load(KEYCHAIN, load_fn=fake)
    second = loader.load(KEYCHAIN, load_fn=fake)

    assert first == second
    assert fake.calls == 1


def test_expired_token_forces_a_reread():
    loader, fake = CredentialLoader(), CountingLoader(creds={"expiresAt": PAST})

    loader.load(KEYCHAIN, load_fn=fake)
    loader.load(KEYCHAIN, load_fn=fake)

    assert fake.calls == 2


def test_non_keychain_sources_are_never_cached(tmp_path):
    loader, fake = CredentialLoader(), CountingLoader()
    source = LocalCredentialsSource(path=tmp_path / "c.json")

    loader.load(source, load_fn=fake)
    loader.load(source, load_fn=fake)

    assert fake.calls == 2


def test_a_different_source_invalidates_the_cache():
    loader, fake = CredentialLoader(), CountingLoader()

    loader.load(KEYCHAIN, load_fn=fake)
    loader.load(KeychainCredentialsSource(service="Other"), load_fn=fake)

    assert fake.calls == 2


def test_access_failure_becomes_sticky_and_stops_calling_the_keychain():
    loader = CredentialLoader()
    fake = CountingLoader(raises=KeychainAccessError("denied"))

    with pytest.raises(KeychainAccessError):
        loader.load(KEYCHAIN, load_fn=fake)
    with pytest.raises(KeychainAccessError):
        loader.load(KEYCHAIN, load_fn=fake)

    # The second load must not re-run `security` -- that is what would put a
    # macOS dialog on screen every backoff interval.
    assert fake.calls == 1


def test_clear_block_re_enables_reads():
    loader = CredentialLoader()
    denied = CountingLoader(raises=KeychainAccessError("denied"))

    with pytest.raises(KeychainAccessError):
        loader.load(KEYCHAIN, load_fn=denied)

    loader.clear_block()
    granted = CountingLoader()
    assert loader.load(KEYCHAIN, load_fn=granted) == {"expiresAt": FUTURE}
    assert granted.calls == 1


# Exit 44 during a load means the item vanished between the existence probe and
# the read (a sign-out, say). That is transient and must keep retrying under the
# poller's normal backoff -- only access failures go sticky.
def test_plain_credentials_error_does_not_become_sticky():
    loader = CredentialLoader()
    fake = CountingLoader(raises=CredentialsError("item not found"))

    with pytest.raises(CredentialsError):
        loader.load(KEYCHAIN, load_fn=fake)
    with pytest.raises(CredentialsError):
        loader.load(KEYCHAIN, load_fn=fake)

    assert fake.calls == 2


def test_now_ms_is_honored_for_expiry():
    loader, fake = CredentialLoader(), CountingLoader(creds={"expiresAt": FUTURE})

    loader.load(KEYCHAIN, load_fn=fake, now_ms=1)
    loader.load(KEYCHAIN, load_fn=fake, now_ms=FUTURE + 1)

    assert fake.calls == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_credential_loader.py -v`
Expected: FAIL — `ImportError: cannot import name 'CredentialLoader'`

- [ ] **Step 3: Write the implementation**

Append to `tokitty/credentials.py`:

```python
class CredentialLoader:
    """Loads credentials, caching Keychain reads until the access token expires.

    File and WSL sources are read through on every call, exactly as before.
    The cache exists because a Keychain read may raise a macOS authorization
    dialog, which a file read never does -- and because the Windows+WSL2 path
    is the one verified end-to-end by hand, so this change should be provably
    inert there rather than uniformly applied.

    Expiry doubles as cache invalidation and as the stale_token trigger: an
    expired cached token forces a re-read *before* the caller evaluates
    is_token_expired(), so a token still expired afterward genuinely means
    Claude Code has not refreshed it.

    Not thread-safe, and does not need to be: fetch() is only ever called from
    Poller._poll_once() on a single daemon thread, and request_refresh() wakes
    that same thread rather than calling fetch itself.
    """

    def __init__(self) -> None:
        self._key: Optional[str] = None
        self._creds: Optional[dict] = None
        self._blocked: Optional[KeychainAccessError] = None

    def clear_block(self) -> None:
        """Drop a sticky access failure so the next load retries the Keychain.
        Wired to the "Refresh now" menu item, which is what keeps the sticky
        block from ever trapping the user."""
        self._blocked = None

    def load(
        self,
        source: CredentialsSource,
        load_fn: Callable[[CredentialsSource], dict] = load_credentials,
        now_ms: Optional[int] = None,
    ) -> dict:
        if not isinstance(source, KeychainCredentialsSource):
            return load_fn(source)

        if self._blocked is not None:
            # Re-raise without touching the Keychain: the poller retries every
            # 30s-600s on failure, and each real read would re-prompt.
            raise self._blocked

        key = describe_source(source)
        if self._key == key and self._creds is not None and not is_token_expired(self._creds, now_ms):
            return self._creds

        try:
            creds = load_fn(source)
        except KeychainAccessError as exc:
            self._blocked = exc
            raise

        self._key, self._creds = key, creds
        return creds
```

Add `Callable` to the `typing` import at the top of `credentials.py`:

```python
from typing import Callable, Optional, Union
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_credential_loader.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full suite and linter**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check .`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tokitty/credentials.py tests/test_credential_loader.py
git commit -m "feat(credentials): CredentialLoader caches Keychain reads

Caches until token expiry (~1h instead of every 120s poll) and makes access
failures sticky, so the poller's 30s-600s backoff cannot turn one denied
prompt into a dialog every half minute. Exit 44 stays non-sticky."
```

---

### Task 5: wire the loader into `__main__.py` and add the `keychain_denied` status

**Files:**
- Modify: `tokitty/poller.py:22` (status docstring)
- Modify: `tokitty/__main__.py:42-77` (`build_fetch_fn`), `:167-172` (`_STALE_HINTS`), `:261-266` (`hints`), `:375-391` (units loop), `:399-401` (`refresh_all`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `CredentialLoader`, `KeychainAccessError` (Tasks 1, 4).
- Produces:
  - `build_fetch_fn(config_dir: Optional[str] = None, loader: Optional[CredentialLoader] = None)` — the new `loader` kwarg is optional, so the existing call in `tests/test_main.py:203` keeps working
  - `PollResult.status == "keychain_denied"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`:

```python
def test_build_fetch_fn_reports_keychain_denied(monkeypatch):
    from tokitty.credentials import KeychainAccessError, KeychainCredentialsSource

    source = KeychainCredentialsSource(service="Claude Code-credentials")
    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", lambda config_dir=None: source)
    monkeypatch.setattr(
        "tokitty.__main__.load_credentials",
        lambda src: (_ for _ in ()).throw(KeychainAccessError("denied")),
    )

    result = build_fetch_fn()()

    # Not credentials_unreachable: the credentials were found, access was
    # refused. Its hint ("can't find credentials") would be the wrong remedy.
    assert result.status == "keychain_denied"


def test_build_fetch_fn_uses_the_injected_loader(monkeypatch):
    from tokitty.credentials import CredentialLoader, KeychainCredentialsSource

    source = KeychainCredentialsSource(service="Claude Code-credentials")
    monkeypatch.setattr("tokitty.__main__.resolve_credentials_source", lambda config_dir=None: source)
    monkeypatch.setattr(
        "tokitty.__main__.load_credentials",
        lambda src: {"expiresAt": 0},  # expired -> stops before any API call
    )

    loader = CredentialLoader()
    result = build_fetch_fn(loader=loader)()

    assert result.status == "stale_token"


def test_keychain_denied_has_hint_text_in_both_dicts():
    from tokitty.__main__ import _STALE_HINTS

    assert "keychain_denied" in _STALE_HINTS
    # The user-facing hint must name the recovery action, since PollResult.message
    # is never rendered anywhere in the UI.
    display = _display_state_for(_error("keychain_denied"), previous=None, now=NOW)
    assert "Refresh" in display["hint_text"]


def test_keychain_denied_falls_back_to_cached_countdown(monkeypatch):
    # A denied Keychain is a transient fetch failure like a stale token: the
    # cached countdown should keep showing rather than blanking out.
    good = _ok(_snapshot(session_pct=42.0, weekly_pct=20.0))
    display = _display_state_for(_error("keychain_denied"), previous=good, now=NOW)

    assert display["session_pct"] == 42.0
```

Both helpers already exist in `tests/test_main.py`: `_error(status)` at line 41 and `_ok(snapshot)` at line 37. The real signature is `_display_state_for(result, previous, now=None)` (`__main__.py:215`) — use the `previous=` / `now=` keyword style every other test in the file uses.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_main.py -k keychain -v`
Expected: FAIL — status is `credentials_unreachable`, and `keychain_denied` is missing from `_STALE_HINTS`

- [ ] **Step 3: Update the imports and `build_fetch_fn`**

In `tokitty/__main__.py`, extend the `tokitty.credentials` import block (lines 15-22):

```python
from tokitty.credentials import (
    AmbiguousCredentialsError,
    CredentialLoader,
    CredentialsError,
    KeychainAccessError,
    describe_source,
    is_token_expired,
    load_credentials,
    resolve_credentials_source,
)
```

Replace the `build_fetch_fn` signature and the load block:

```python
def build_fetch_fn(config_dir: Optional[str] = None, loader: Optional[CredentialLoader] = None):
    # One loader per closure, i.e. per account: it caches that account's
    # Keychain reads and holds its sticky-denial state.
    loader = loader if loader is not None else CredentialLoader()

    def fetch() -> PollResult:
        now = datetime.now(timezone.utc)
        try:
            source = resolve_credentials_source(config_dir=config_dir)
        except AmbiguousCredentialsError as exc:
            return PollResult(status="ambiguous_credentials", snapshot=None, message=str(exc), fetched_at=now)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)

        try:
            creds = loader.load(source, load_fn=load_credentials)
        except KeychainAccessError as exc:
            # Must precede the CredentialsError branch -- it is a subclass.
            return PollResult(status="keychain_denied", snapshot=None, message=str(exc), fetched_at=now)
        except CredentialsError as exc:
            return PollResult(status="credentials_unreachable", snapshot=None, message=str(exc), fetched_at=now)
```

The rest of `fetch()` is unchanged.

- [ ] **Step 4: Add hint text to both dicts**

In `_STALE_HINTS` (around line 167) add:

```python
    "keychain_denied": "can't confirm, Keychain denied",
```

In the `hints` dict inside `_display_state_for` (around line 261) add:

```python
        "keychain_denied": "Keychain denied, Refresh to retry",
```

- [ ] **Step 5: Update the `PollResult` status docstring**

In `tokitty/poller.py:22`:

```python
    status: str  # "ok" | "stale_token" | "credentials_unreachable" | "ambiguous_credentials" | "keychain_denied" | "api_error"
```

No logic change: backoff already treats every non-`ok` status alike, and the sticky block is what keeps that from re-prompting.

- [ ] **Step 6: Wire the loader into the units loop and `refresh_all`**

In the units loop (around line 377), replace the poller construction:

```python
        config_dir = account.config_dir if account else None
        cred_loader = CredentialLoader()
        poller = Poller(fetch_fn=build_fetch_fn(config_dir, loader=cred_loader))
```

Add the loader to the unit dict (around line 390):

```python
        units.append({"pane": pane, "poller": poller, "watcher": watcher,
                      "last_good": None, "key": key, "account": account,
                      "cred_loader": cred_loader})
```

Replace `refresh_all` (around line 399):

```python
    def refresh_all():
        for unit in units:
            # Clearing first means "Refresh now" is the recovery path after a
            # denied Keychain prompt: grant access, click Refresh, done. No
            # restart needed, which is what makes the sticky block safe.
            unit["cred_loader"].clear_block()
            unit["poller"].request_refresh()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: PASS, including the pre-existing `test_build_fetch_fn_passes_config_dir`

- [ ] **Step 8: Run the full suite, the GUI tests, and the linter**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m pytest -m gui && .venv/bin/ruff check .`
Expected: PASS on all three

- [ ] **Step 9: Commit**

```bash
git add tokitty/__main__.py tokitty/poller.py tests/test_main.py
git commit -m "feat(main): keychain_denied status and per-account CredentialLoader

Reuses credentials_unreachable would have shown 'can't find credentials' for a
denied prompt -- found but refused is the opposite remedy, and PollResult.message
is never rendered, so the distinction has to live in the status. Refresh now
clears the sticky block."
```

---

### Task 6: README

**Files:**
- Modify: `README.md` — Setup ▸ macOS (~line 113), Two accounts (~line 38), Security & privacy (~line 77)

**Interfaces:** none (docs only).

- [ ] **Step 1: Rewrite the macOS Setup section**

Replace the `### macOS` block (currently two lines, around 113-116):

```markdown
### macOS

1. Install Python from [python.org](https://www.python.org/) (recommended over
   Apple's system Python — which is 3.9 and below this project's 3.10 floor —
   or some Homebrew builds, which can have flaky Tcl/Tk).
2. `python3 -m tokitty`

Claude Code on macOS keeps its OAuth credentials in your **login Keychain**, not
in `~/.claude/.credentials.json`, so tokitty reads them from there. The first
read raises a macOS authorization prompt; choose **Always Allow** unless you
want to re-authorize roughly once an hour. If you deny it, the cat shows
"Keychain denied, Refresh to retry" and stops asking — grant access, then
right-click ▸ **Refresh now** to recover. No restart needed.
```

- [ ] **Step 2: Add the macOS note to Two accounts**

Append to the `## Two accounts` section, after the paragraph describing `config_dir`:

```markdown
**Two-account mode requires credential *files*.** Each `config_dir` entry is
read as `<config_dir>/.credentials.json`, so on macOS — where Claude Code stores
credentials in the login Keychain — `accounts.json` cannot resolve them. The
Keychain holds one item per macOS user with no per-account identity to key on,
so Keychain resolution is single-account only, and tokitty says so explicitly
rather than silently showing the same numbers in both panes.
```

- [ ] **Step 3: Add the Keychain paragraph to Security & privacy**

Append to the `## Security & privacy` section, after the live-activity bullets:

```markdown
**macOS Keychain.** On macOS the credentials are read from the login Keychain
instead of a file. Tokitty's access stays read-only — it never writes to the
item and never touches the refresh token. One thing worth knowing before you
click **Always Allow**: macOS Keychain ACLs are per-*binary*, and the binary
being authorized is `/usr/bin/security`. So granting it persistent access means
any process running as you can afterwards read that token by shelling out to
`security`, without a prompt. That is a property of how Keychain authorization
works, not something tokitty can tighten — a narrower grant would require
tokitty to be a signed app bundle with a stable identity rather than a Python
script. Choosing **Allow** instead of **Always Allow** keeps the grant to a
single read, at the cost of a prompt roughly once per token lifetime.
```

- [ ] **Step 4: Verify nothing else in the README contradicts this**

Run: `grep -n -i "credentials.json\|keychain\|macos" README.md`
Expected: no remaining claim that macOS resolves credentials from a file. **Leave "Platforms tested" alone** — it says macOS interactive use is "not yet hands-on", which stays true until Task 7 passes.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): macOS Keychain setup, ACL scope, and two-account limits

Names the /usr/bin/security ACL blast radius explicitly: Always Allow lets any
process running as you read the token without a prompt, which follows from
per-binary Keychain ACLs and is not something tokitty can tighten."
```

---

### Task 7: live verification on macOS

**Files:** none — this is hand verification of the spec's gates. Nothing here can be automated; the prompt is a system dialog and denial cannot be simulated.

**Interfaces:** none.

- [ ] **Step 1: Confirm the automated gates**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m pytest -m gui && .venv/bin/ruff check .`
Expected: all green. Baseline was 333 passed / 5 deselected; expect roughly 355-365 passed with the new tests.

- [ ] **Step 2: Confirm resolution now finds the Keychain**

Run:
```bash
.venv/bin/python -c "
from tokitty.credentials import resolve_credentials_source
print(resolve_credentials_source())
"
```
Expected: `KeychainCredentialsSource(service='Claude Code-credentials', account=None)` — and **no** authorization prompt, because resolution only probes attributes.

- [ ] **Step 3: First live run — grant access**

Run: `.venv/bin/python -m tokitty`
Expected: the card renders; macOS raises one authorization prompt for `Claude Code-credentials`; after **Always Allow**, real usage percentages and reset countdowns appear.

- [ ] **Step 4: Confirm the prompt does not recur**

Leave it running at least 4 minutes (two 120s poll intervals).
Expected: no second dialog. If one appears per poll, the cache is not holding — check that `build_fetch_fn` reuses one `CredentialLoader` rather than constructing one per `fetch()` call.

- [ ] **Step 5: Exercise the denial path**

**No command in this step. Do not attempt a CLI route to revoke Keychain access** — the closest one, `security delete-generic-password`, *deletes the credential item itself* and signs Claude Code out. There is no supported CLI to revoke only an ACL entry.

Revoke the stored authorization through the GUI: **Keychain Access.app** ▸ search `Claude Code-credentials` ▸ double-click ▸ **Access Control** ▸ select `security` in the allowed list ▸ **Remove** ▸ Save Changes. Then restart tokitty and click **Deny** on the prompt.

Expected: the cat shows "Keychain denied, Refresh to retry", and **no further dialog appears** while blocked — this is the prompt-storm gate, so watch for at least 2 minutes.

- [ ] **Step 6: Confirm Refresh recovers**

Right-click ▸ **Refresh now**, and grant the prompt this time.
Expected: live numbers return without restarting tokitty.

- [ ] **Step 7: Update Platforms tested, now that it is earned**

In `README.md`, move macOS out of "Not yet hands-on" and record what was actually verified: interactive desktop use with live Keychain-sourced polling on macOS (Darwin 25.5.0, Python 3.14.6, Tk 9.0). Leave native Linux in the not-yet-hands-on list.

- [ ] **Step 8: Commit and push the branch**

```bash
git add README.md
git commit -m "docs(readme): record macOS as hands-on verified

Interactive run with live Keychain-sourced polling, prompt-frequency and
denial/recovery paths all confirmed by hand."
git push -u origin macos-keychain
```

- [ ] **Step 9: Confirm CI is green on all six matrix cells**

Run: `gh pr create --fill && gh pr checks --watch`
Expected: `test (ubuntu/macos/windows, py3.10/3.14)`, `smoke`, and `lint` all pass. The Linux and Windows cells passing is what proves the `sys.platform` monkeypatching worked — if the new tests only pass on macOS, they were written as skips.

---

## Self-Review

**Spec coverage:** every spec section maps to a task — `keychain.py` and exit-code classification → Task 1; the source variant, `describe_source`, `load_credentials` → Task 2; resolution order and both platform-aware messages → Task 3; `CredentialLoader`, caching, sticky block → Task 4; `__main__`/`poller` wiring, `keychain_denied`, `refresh_all` → Task 5; all three README edits → Task 6; all eight verification gates → Task 7. The spec's "Out of scope" list adds no tasks by definition.

**Type consistency:** `CredentialLoader.load(source, load_fn=..., now_ms=...)` and `.clear_block()` are named identically in Tasks 4, 5, and 7. `KEYCHAIN_SERVICE`, `EXIT_ITEM_NOT_FOUND`, `keychain_item_exists`, `read_keychain_secret`, `KeychainAccessError`, and `KeychainCredentialsSource(service=, account=)` are consistent across Tasks 1-5. `build_fetch_fn(config_dir=None, loader=None)` keeps the existing positional first argument, so `tests/test_main.py:203` needs no edit.

**Signatures verified against source:** `_display_state_for(result, previous, now=None)` (`__main__.py:215`), the `_ok`/`_error` test helpers (`tests/test_main.py:37,41`), `build_fetch_fn(config_dir=None)` (`__main__.py:42`), `refresh_all` (`__main__.py:399`), the units loop (`__main__.py:375-391`), both hint dicts (`__main__.py:167,261`), and the `wsl_probe.py` conventions the new module mirrors (injectable `run`, `timeout=10`, `FakeCompletedProcess`). Exit code 44 and the prompt-free behavior of the attribute-only query were confirmed against the real machine on 2026-08-03.

**One deliberate gap:** Task 7 Step 5 cannot be scripted. Revoking a Keychain ACL entry has no supported CLI equivalent (`security delete-generic-password` deletes the credential itself and signs Claude Code out — the plan says explicitly not to run it), so the denial gate is a Keychain Access.app walkthrough. That is why Task 7 is hand verification rather than a test.
