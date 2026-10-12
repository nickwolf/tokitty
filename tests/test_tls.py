"""Tests for tokitty/tls.py: the system CA bundle fallback for a bundled
OpenSSL whose compiled-in certificate path is missing."""
import os
import ssl
import sys

import pytest

from tokitty import tls

MISSING = ssl.DefaultVerifyPaths(
    cafile=None,
    capath=None,
    openssl_cafile_env="SSL_CERT_FILE",
    openssl_cafile="/Library/Frameworks/Python.framework/Versions/3.13/etc/openssl/cert.pem",
    openssl_capath_env="SSL_CERT_DIR",
    openssl_capath="/Library/Frameworks/Python.framework/Versions/3.13/etc/openssl/certs",
)
PRESENT = MISSING._replace(cafile=MISSING.openssl_cafile)


def _exists(*paths):
    return lambda path: path in paths


def test_points_openssl_at_the_macos_bundle_when_its_own_is_missing():
    environ = {}
    chosen = tls.use_system_ca_bundle(environ, paths=MISSING, exists=_exists("/etc/ssl/cert.pem"), platform="darwin")
    assert chosen == "/etc/ssl/cert.pem"
    assert environ == {"SSL_CERT_FILE": "/etc/ssl/cert.pem"}


def test_takes_the_first_linux_bundle_that_exists():
    environ = {}
    chosen = tls.use_system_ca_bundle(
        environ, paths=MISSING, exists=_exists("/etc/pki/tls/certs/ca-bundle.crt"), platform="linux"
    )
    assert chosen == "/etc/pki/tls/certs/ca-bundle.crt"
    assert environ["SSL_CERT_FILE"] == chosen


def test_leaves_a_working_openssl_alone():
    environ = {}
    assert tls.use_system_ca_bundle(environ, paths=PRESENT, exists=_exists("/etc/ssl/cert.pem"), platform="darwin") is None
    assert environ == {}


def test_a_present_cert_dir_counts_as_working():
    environ = {}
    paths = MISSING._replace(capath=MISSING.openssl_capath)
    assert tls.use_system_ca_bundle(environ, paths=paths, exists=_exists("/etc/ssl/cert.pem"), platform="darwin") is None
    assert environ == {}


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_never_overrides_the_users_own_setting(name):
    environ = {name: "/somewhere/else"}
    assert tls.use_system_ca_bundle(environ, paths=MISSING, exists=_exists("/etc/ssl/cert.pem"), platform="darwin") is None
    assert environ == {name: "/somewhere/else"}


def test_windows_is_left_alone():
    """Python loads the Windows certificate store itself, whatever OpenSSL's
    compiled-in path says."""
    environ = {}
    assert tls.use_system_ca_bundle(environ, paths=MISSING, exists=lambda path: True, platform="win32") is None
    assert environ == {}


def test_nothing_to_fall_back_to():
    environ = {}
    assert tls.use_system_ca_bundle(environ, paths=MISSING, exists=lambda path: False, platform="linux") is None
    assert environ == {}


@pytest.mark.skipif(sys.platform == "win32", reason="Windows loads its own store regardless")
def test_the_fallback_reaches_a_real_default_context(monkeypatch, tmp_path):
    """End to end through the ssl module: with OpenSSL's compiled-in paths
    hidden, a default context loads no roots until the fallback runs, then
    loads them from the system bundle."""
    platform = "darwin" if sys.platform == "darwin" else "linux"
    bundle = next((path for path in tls.SYSTEM_CA_BUNDLES[platform] if os.path.isfile(path)), None)
    if bundle is None:
        pytest.skip("no system CA bundle on this machine")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing"))
    assert ssl.create_default_context().cert_store_stats()["x509_ca"] == 0

    monkeypatch.delenv("SSL_CERT_FILE")
    monkeypatch.delenv("SSL_CERT_DIR")
    tls.use_system_ca_bundle(os.environ, paths=MISSING, exists=os.path.isfile, platform=platform)
    assert os.environ["SSL_CERT_FILE"] == bundle
    assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0
