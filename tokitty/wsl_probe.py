"""Windows-only fallback: locate Claude Code's credentials inside WSL.

Deliberately subprocess-only (no pathlib/UNC filesystem access) -- see
the module docstring in the design spec's Cross-platform architecture
section. All three functions accept an injectable `run` callable so
tests never invoke a real wsl.exe.
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Tuple

from tokitty.credentials import ENV_OVERRIDE, AmbiguousCredentialsError, CredentialsError

_CHECK_SCRIPT = 'for u in /home/*; do f="$u/.claude/.credentials.json"; [ -f "$f" ] && echo "$f"; done'


def list_wsl_distros(run: Callable = subprocess.run) -> List[str]:
    """Return the names of installed WSL distros, via `wsl.exe -l -q`."""
    try:
        result = run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not list WSL distros: {exc}") from exc

    raw = result.stdout
    text = raw.decode("utf-16-le", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def _credentials_paths_in_distro(distro: str, run: Callable = subprocess.run) -> List[str]:
    """Return WSL-side absolute paths to credentials files found under /home in a distro."""
    try:
        # --exec (not --) is required here: wsl.exe's default invocation
        # re-joins the argv after `--` before handing it to the distro's
        # shell, which mangles a multi-word `sh -c "<script>"` and leaves
        # variables like $u empty. --exec preserves the argv array as-is.
        result = run(
            ["wsl.exe", "-d", distro, "--exec", "sh", "-c", _CHECK_SCRIPT],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    raw = result.stdout
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_wsl_credentials(run: Callable = subprocess.run) -> Tuple[str, str]:
    """Probe every installed WSL distro for a Claude Code credentials file.

    Returns (distro_name, wsl_side_path) for the single match. Raises
    AmbiguousCredentialsError if more than one match exists across all
    distros, or CredentialsError if none do.
    """
    distros = list_wsl_distros(run=run)
    matches: List[Tuple[str, str]] = []

    for distro in distros:
        for path in _credentials_paths_in_distro(distro, run=run):
            matches.append((distro, path))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        joined = ", ".join(f"{d}:{p}" for d, p in matches)
        raise AmbiguousCredentialsError(
            f"Multiple Claude Code installs found across WSL distros: {joined}. "
            f"Set {ENV_OVERRIDE} to the correct path."
        )

    raise CredentialsError(
        "No Claude Code credentials found in any WSL distro. "
        f"Set {ENV_OVERRIDE} to the correct path."
    )


def read_wsl_credentials(distro: str, wsl_path: str, run: Callable = subprocess.run) -> str:
    """Return the raw file contents of a credentials file inside WSL, via `wsl.exe cat`."""
    try:
        result = run(["wsl.exe", "-d", distro, "--", "cat", wsl_path], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialsError(f"Could not read credentials from WSL distro {distro}: {exc}") from exc

    if result.returncode != 0:
        raise CredentialsError(f"wsl.exe cat failed for {distro}:{wsl_path}")

    raw = result.stdout
    return raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
