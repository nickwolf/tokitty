# macOS Keychain credentials — design

**Issue:** none yet — surfaced while setting tokitty up on a macOS box on
2026-08-03. Tokitty installs and tests clean there but cannot resolve
credentials at all, so the app never renders live data.

**Date:** 2026-08-03
**Branch:** `macos-keychain`

## Goal

Let tokitty find Claude Code's OAuth credentials on macOS, where they live in the
**login Keychain** rather than in a file. Today `resolve_credentials_source()`
knows three sources — the `TOKITTY_CREDENTIALS` env var (a file),
`~/.claude/.credentials.json` (a file), and a WSL probe gated behind
`sys.platform == "win32"`. On macOS none of them can ever succeed, because there
is no file for any of them to find. `TOKITTY_CREDENTIALS` is not a workaround:
it also expects a path to an existing file.

macOS becomes a **first-class supported platform** for credential resolution,
alongside Windows/WSL2 — new source variant, tests that run on every cell of the
CI matrix, and README coverage — not a local hack to unblock one machine.

## Decided (owner, not to be re-opened)

1. **First-class macOS support**, not a minimal unblock and not a
   file-export sidestep. Exporting the Keychain secret to a file and pointing
   `TOKITTY_CREDENTIALS` at it was rejected: it puts an OAuth token in plaintext
   on disk and goes stale on every token refresh.
2. **Single-account only.** Keychain resolution applies solely to the v1
   no-`config_dir` path. `accounts.json` continues to require credential
   *files*, and its macOS error message says so.
3. **`security` CLI over pyobjc.** Subprocess-only, mirroring `wsl_probe.py`,
   with an injectable `run` so tests never touch the real Keychain.
4. **Cache Keychain reads until token expiry.** File and WSL sources keep
   re-reading every poll, unchanged.

## Owner choices (brainstorm, 2026-08-03)

| Question | Choice |
|---|---|
| Scope | **First-class macOS support** — new `CredentialsSource` variant, tests on all matrix cells, README updated. |
| Two-account mode | **Single-account only, documented.** The Keychain has one item per macOS user (`svce="Claude Code-credentials"`, `acct=<macOS username>`), with no per-account identity to key on. A per-account `keychain` field in `accounts.json` was rejected as designing against an unobserved storage model; Keychain-as-fallback-per-account was rejected because two panes would silently show identical numbers. |
| Read mechanism | **`security` CLI subprocess.** pyobjc was rejected: it narrows the ACL grant from `/usr/bin/security` to the Python framework binary, which is not a meaningful boundary (both mean "any code running as you"), and it costs the cheap injected-`run` testing story. |
| Read frequency | **Cache until token expiry**, Keychain sources only. |

## Context / grounding (verified 2026-08-03)

Verified on a real macOS 15 (Darwin 25.5.0) box with Python 3.14.6 from
python.org, tkinter against Tk 9.0:

- **The whole test suite passes on macOS**, including the 5 `gui`-marked tests
  against a real Tk display: `338 passed`, `ruff check` clean. The gap is
  credential resolution alone, not rendering or platform support generally.
- **`~/.claude/.credentials.json` does not exist**; `resolve_credentials_source()`
  raises `CredentialsError: No Claude Code credentials found at
  ~/.claude/.credentials.json.`
- **The Keychain item exists**: a generic password with `svce="Claude
  Code-credentials"` and `acct=<macOS username>`.
- **An attribute-only query does not prompt.** `security find-generic-password -s
  <svce>` (no `-w`) returns `svce`/`acct` silently. This is load-bearing — see
  the landmine below.
- **`security` exits 44** when no matching item exists (confirmed against a
  deliberately absent service name).
- **The secret's payload is the same JSON** the file form holds, so it feeds the
  existing `json.loads` → `claudeAiOauth` path with no parsing changes.
- **`wsl_probe.py` is the template**: module docstring declares it "deliberately
  subprocess-only", every entry point takes `run: Callable = subprocess.run`,
  `timeout=10`, and `OSError`/`subprocess.TimeoutExpired` are re-raised as
  `CredentialsError`. `tests/test_wsl_probe.py` injects fakes and never shells
  out.
- **Error plumbing already exists**: `PollResult.status` carries
  `credentials_unreachable` / `ambiguous_credentials`, and `Poller` backs off
  30s → 600s on any non-`ok` result.
- **A "Refresh now" menu item already exists** (`menu.py:66` → `__main__.py`
  `refresh_all()` → `poller.request_refresh()`). This is the manual recovery
  hook the sticky-block design depends on.

## The landmines and how they're contained

### 1. Resolution runs on every poll and must never prompt

`build_fetch_fn()`'s `fetch()` calls `resolve_credentials_source()` on **every
poll** (~120s). If resolution asked the Keychain for a *secret*, every poll
would risk a macOS dialog.

Contained by splitting the two operations:

- `keychain_item_exists()` uses the **attribute-only** query (no `-w`) —
  verified prompt-free — and is all resolution ever calls.
- `read_keychain_secret()` uses `-w` and is called **only** from
  `load_credentials()`.

The prompt is therefore confined to exactly one call site. A test asserts that
`keychain_item_exists` does not pass `-w`; that is a correctness assertion, not
a style one.

### 2. Backoff turns one denial into a prompt storm

On any non-`ok` result the poller retries after 30s, doubling to 600s. If the
user denies the Keychain prompt, **each retry re-prompts** — a dialog every 30
seconds, which is worse than a cat that does not work.

Contained by a **sticky block**: after a non-44 failure, `CredentialLoader`
records it and short-circuits later loads without touching the Keychain.
Recovery does not require a restart, because **"Refresh now" clears the block** —
so `refresh_all()` gains a `clear_block()` call. The flow is *grant access →
right-click → Refresh now → working*.

Because stickiness is cheaply reversible, it is safe to be aggressive about it,
and the exit-code classification does not have to be perfect for the UX to hold.

### 3. The exception message never reaches the user

`PollResult.message` is **never rendered**. Statuses map to short canned strings
in two dicts — `_STALE_HINTS` (`__main__.py:167`) and `hints`
(`__main__.py:261`).

So reusing `credentials_unreachable` for a denied Keychain would display *"can't
find credentials"* — actively wrong. The credentials were found; access was
refused. Different problem, opposite remedy. Hence one new status
(`keychain_denied`) rather than a more descriptive `message`.

## Architecture

### `tokitty/keychain.py` (new)

Subprocess-only, mirroring `wsl_probe.py`.

```python
KEYCHAIN_SERVICE = "Claude Code-credentials"

def keychain_item_exists(service, account=None, run=subprocess.run) -> bool
def read_keychain_secret(service, account=None, run=subprocess.run) -> str
```

Both build `security find-generic-password -s <service> [-a <account>]`;
`read_keychain_secret` appends `-w`. `timeout=10`;
`OSError`/`TimeoutExpired` → `CredentialsError`.

Exit-code classification in `read_keychain_secret`:

| Exit | Meaning | Raised |
|---|---|---|
| 0 | secret on stdout | — |
| 44 | no such item | `CredentialsError` |
| other | denied, locked, or unexpected | `KeychainAccessError` |

`KeychainAccessError` subclasses `CredentialsError`, so handlers that do not
care still catch it. Denial-specific exit codes are deliberately **not**
enumerated — handling is identical for every non-44 failure.

`keychain_item_exists` returns `False` on exit 44 and `True` on 0. Other exit
codes also return `True`: the item's existence is not in question, only access
to its secret, and reporting `False` would produce the misleading "can't find
credentials" hint that landmine 3 exists to avoid.

### `tokitty/credentials.py` (changed)

New variant, added to the `CredentialsSource` union:

```python
@dataclass(frozen=True)
class KeychainCredentialsSource:
    service: str
    account: Optional[str] = None
```

- `describe_source()` → `f"Keychain:{service}"`.
- `load_credentials()` → new branch calling `read_keychain_secret()`, feeding
  the existing `json.loads` → `claudeAiOauth` path unchanged.
- `resolve_credentials_source()` — one new step in the **no-`config_dir`**
  branch only:

  1. `TOKITTY_CREDENTIALS` env override (file) — unchanged
  2. `~/.claude/.credentials.json` (file) — unchanged, still wins for back-compat
  3. **new:** `sys.platform == "darwin"` and `keychain_item_exists()` →
     `KeychainCredentialsSource`
  4. `sys.platform == "win32"` → WSL probe — unchanged
  5. raise `CredentialsError` — message now platform-aware

  The final message on darwin names both misses (no file, no Keychain item) and
  says to open Claude Code to sign in, or set `TOKITTY_CREDENTIALS`. The current
  text cites only a file path that will never exist on macOS.

- The `config_dir` branch stays **file-only**, with a macOS-specific sentence
  explaining that two-account mode requires credential files.

### `CredentialLoader` (new, in `credentials.py`)

Caching policy lives behind a loader so the call site stays one line and
`fetch()` knows nothing about caching.

```python
class CredentialLoader:
    def load(self, source, load_fn=load_credentials, now_ms=None) -> dict:
        if not isinstance(source, KeychainCredentialsSource):
            return load_fn(source)         # unchanged behavior
        if self._blocked is not None:
            raise self._blocked            # no subprocess, no prompt
        key = describe_source(source)
        if self._key == key and self._creds and not is_token_expired(self._creds, now_ms):
            return self._creds
        try:
            creds = load_fn(source)
        except KeychainAccessError as exc:
            self._blocked = exc
            raise
        self._key, self._creds = key, creds
        return creds

    def clear_block(self) -> None: ...
```

**The block is set on `KeychainAccessError` only** — never on a bare
`CredentialsError`. Exit 44 during a load means the item vanished between
`keychain_item_exists()` and the read (Claude Code signed out, say), which is
transient and should keep retrying under normal backoff. Only access failures,
which are what re-prompt, become sticky. Re-raising the stored exception keeps
`fetch()`'s existing `except` ladder as the single place that maps failures to a
`PollResult`.

**Only Keychain sources are cached.** Not consistency for its own sake: the
Windows+WSL2 path is the one verified end-to-end by hand against a real account,
and this change should be provably inert there. The cache exists because a
Keychain read may prompt — which a file read never does.

Expiry doubles as cache invalidation *and* the `stale_token` trigger, and one
read serves both: an expired cached token forces a re-read **before**
`is_token_expired()` is evaluated in `fetch()`, so if it is still expired
afterward, Claude Code genuinely has not refreshed it — exactly what
`stale_token` means.

**Threading:** none needed. `fetch()` is only called from `Poller._poll_once()`
on one daemon thread; `request_refresh()` wakes that same thread rather than
calling `fetch` itself. Plain instance attributes; no lock.

### `tokitty/__main__.py` (changed)

- `build_fetch_fn()` instantiates one `CredentialLoader` per closure (i.e. per
  account) and calls `creds = loader.load(source)` in place of
  `load_credentials(source)`.
- `fetch()` catches `KeychainAccessError` **before** the generic
  `CredentialsError` branch, returning `status="keychain_denied"`.
- `refresh_all()` calls `loader.clear_block()` alongside
  `poller.request_refresh()`.
- Both hint dicts gain a `keychain_denied` entry:
  - `hints` → `"Keychain denied, Refresh to retry"`
  - `_STALE_HINTS` → `"can't confirm, Keychain denied"`

### `tokitty/poller.py` (changed)

`PollResult.status` docstring union gains `keychain_denied`. No logic change:
backoff already treats every non-`ok` status alike, and the sticky block is what
keeps that from prompting repeatedly.

## Testing

All new tests are headless and platform-agnostic. **Every darwin-specific test
monkeypatches `sys.platform` rather than skipping** — the matrix is
ubuntu/macos/windows × py3.10/3.14, and a `skipif(sys.platform != "darwin")`
would silently drop this code from five of six cells. No new `gui`-marked
tests; nothing here touches Tk.

**`tests/test_keychain.py`** (new), modeled on `test_wsl_probe.py`:

- exact argv for both functions; **`keychain_item_exists` must not pass `-w`**
- `read_keychain_secret`: exit 0 → stdout; 44 → `CredentialsError`; other →
  `KeychainAccessError`
- `keychain_item_exists`: 44 → `False`; 0 → `True`; other → `True`
- `OSError` / `TimeoutExpired` → `CredentialsError`

**`tests/test_credentials.py`** (extended):

- env beats file; file beats Keychain; Keychain used when both files absent
- darwin with nothing present raises the platform-aware message naming both misses
- `config_dir` branch stays file-only and its macOS message mentions files
- `describe_source` / `load_credentials` cover the new variant

**Cache tests** (new): hit and miss, expiry forces re-read, non-Keychain sources
bypass the cache entirely, sticky block short-circuits without invoking the
loader, `clear_block()` re-enables, and — separately — **a bare
`CredentialsError` (exit 44) does not become sticky** and is retried on the next
load. These assert **call counts** on a counting fake — the number of reads *is*
the feature, so a test asserting only the returned dict would pass even if
caching did nothing.

## Docs

- **Setup ▸ macOS**: credentials come from the login Keychain; macOS prompts on
  first read; "Always Allow" recommended.
- **Security & privacy**: tokitty stays read-only and still never touches the
  refresh token — but "Always Allow" grants **`/usr/bin/security`** persistent
  access to that item, so any process running as you can then read the token
  without a prompt. That follows from macOS Keychain ACLs being per-binary, and
  is not something tokitty can tighten; it belongs in this section rather than
  buried in a commit. A tight grant is not available to a Python script without
  a stable signed-app identity.
- **Two accounts**: `accounts.json` requires credential files; Keychain
  resolution is single-account only.
- **Platforms tested**: left alone by this spec. It says macOS interactive use is
  "not yet hands-on", which stays true until the cat renders and polls live —
  the final verification gate below, not something the spec can claim.

## Out of scope (noted, not built)

- pyobjc / Security-framework access (see Owner choices).
- Per-account Keychain identities in `accounts.json`.
- Anything that **writes** to the Keychain, or touches token refresh.
- `--install-hooks`: it targets `~/.claude`, which exists and works on macOS
  unchanged.
- Keychain storage for tokitty's own state files (`position.json` etc.) — they
  are non-secret by design.
- Linux secret-service / libsecret equivalents.

## Verification gates (before claiming done)

1. `pytest` green on macOS (currently 338; expect ~360 with the new tests).
2. `pytest -m gui` green on macOS against a real display (currently 5 passed).
3. `ruff check .` clean.
4. CI green on all six matrix cells — the new tests must pass on Linux and
   Windows too, which is what the `sys.platform` monkeypatching buys.
5. **Live run on macOS**: `python -m tokitty` renders the card, prompts once for
   Keychain access, and shows real usage numbers after "Always Allow".
6. **Prompt frequency confirmed by observation**: no second dialog across at
   least two poll intervals (~4 min) after granting.
7. **Denial path exercised by hand**: deny the prompt once and confirm the cat
   shows "Keychain denied, Refresh to retry", that **no further dialog appears**
   while blocked, and that "Refresh now" recovers after access is granted.
8. Only then update README **Platforms tested** to record macOS as hands-on.
