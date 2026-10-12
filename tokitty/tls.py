"""Point OpenSSL at the system's CA bundle when its own is missing.

A frozen build carries the OpenSSL of the Python it was built with, and that
OpenSSL looks for trusted roots at a path fixed at compile time. On macOS
that is the python.org framework's etc/openssl, which exists on the release
runners and on almost no user's Mac, so every HTTPS request failed with
CERTIFICATE_VERIFY_FAILED: usage, update checks, downloads and ntfy alike.
The fix sets SSL_CERT_FILE, which OpenSSL reads each time a default context
loads its roots, so every urlopen in the process picks it up.
"""
from __future__ import annotations

import os
import ssl
import sys
from typing import Callable, MutableMapping, Optional

SYSTEM_CA_BUNDLES = {
    # Apple ships and updates this one with the OS.
    "darwin": ["/etc/ssl/cert.pem"],
    # Debian and Ubuntu, Fedora and RHEL, openSUSE, Alpine.
    "linux": [
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/ca-bundle.pem",
        "/etc/ssl/cert.pem",
    ],
}


def use_system_ca_bundle(
    environ: Optional[MutableMapping[str, str]] = None,
    *,
    paths: Optional[ssl.DefaultVerifyPaths] = None,
    exists: Callable[[str], bool] = os.path.isfile,
    platform: Optional[str] = None,
) -> Optional[str]:
    """Set SSL_CERT_FILE to the system bundle if OpenSSL's compiled-in file
    and directory are both missing. Returns the bundle chosen, or None when
    nothing needed changing or nothing was found. Never overrides a
    SSL_CERT_FILE or SSL_CERT_DIR the user set."""
    environ = os.environ if environ is None else environ
    paths = ssl.get_default_verify_paths() if paths is None else paths
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        platform = "linux"
    if platform not in SYSTEM_CA_BUNDLES:
        return None
    if environ.get(paths.openssl_cafile_env) or environ.get(paths.openssl_capath_env):
        return None
    # DefaultVerifyPaths.cafile and .capath are None when the path is missing.
    if paths.cafile or paths.capath:
        return None
    for bundle in SYSTEM_CA_BUNDLES[platform]:
        if exists(bundle):
            environ[paths.openssl_cafile_env] = bundle
            return bundle
    return None
