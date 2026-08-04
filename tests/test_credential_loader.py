import time

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
